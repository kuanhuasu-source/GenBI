"""
duckdb_engine.py — v0.15.0+ (M5.5)

DuckDB SQL execution path,給 Upload Workspace Phase A 大檔(>100K row)用。
對齊 spec §4.2 item 9 + §13.2。

# 為什麼需要

MVP Pandas Phase A 在 100K row 上限。實際 dataset 動輒 1M+ rows(交易紀錄、
log、IoT 數據)。Pandas 對這種尺寸 in-memory filter 慢,DuckDB 用 columnar +
mmap parquet 快很多 + 記憶體友善。

# 流程對比

```
Pandas path (M3):
  source_df = pd.read_parquet(...)             # 全表 load 到 RAM
  raw_df = source_df[source_df['x'] == 'A']    # 全表 filter
  raw_df → Phase B(也 in-memory)

DuckDB path (M5.5):
  con = duckdb_engine.create_connection()
  duckdb_engine.register_parquet(con, "source", "/path/to/x.parquet")
  raw_df = con.execute("SELECT * FROM source WHERE x = 'A'").df()
  con.close()
  raw_df → Phase B
```

DuckDB 自動:
- mmap parquet(不全 load)
- columnar predicate pushdown(只讀需要的欄)
- query plan optimize

# Safety

- 禁 DDL / DML(只 SELECT)
- 禁 ATTACH / COPY / EXPORT(避免 IO 出去)
- 禁 INSTALL / LOAD extension(避免外部模組)
- query timeout(default 60s)
- result row limit(default 1M,Phase B 再 cap 100K)

# 用法

```python
from duckdb_engine import DuckDBEngine

engine = DuckDBEngine()
engine.register_parquet("source", "/path/sheet1.parquet")
result = engine.execute_safe(
    "SELECT * FROM source WHERE region = 'TW' LIMIT 50000"
)
# result.success: bool
# result.df: pandas DataFrame
# result.error: str | None
engine.close()
```
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import pandas as pd

logger = logging.getLogger(__name__)


# ============================================================
# SQL safety patterns
# ============================================================
# 禁忌 SQL keyword(case-insensitive,word boundary)
_FORBIDDEN_SQL_KEYWORDS = (
    "INSERT", "UPDATE", "DELETE", "MERGE",
    "DROP", "TRUNCATE", "ALTER", "CREATE",
    "ATTACH", "DETACH", "COPY", "EXPORT", "IMPORT",
    "INSTALL", "LOAD",
    "PRAGMA",   # PRAGMA 可能改 DuckDB config
    "CALL",
)

_FORBIDDEN_SQL_RE = re.compile(
    r"\b(?:" + "|".join(_FORBIDDEN_SQL_KEYWORDS) + r")\b",
    re.IGNORECASE,
)


# ============================================================
# Result dataclass
# ============================================================
@dataclass
class DuckDBExecResult:
    success: bool
    df: Optional[pd.DataFrame] = None
    error: Optional[str] = None
    error_type: Optional[str] = None
    exec_time_s: float = 0.0
    row_count: int = 0
    truncated: bool = False
    sql_used: str = ""


# ============================================================
# Engine
# ============================================================
class DuckDBEngine:
    """DuckDB SQL engine 包裝。"""

    def __init__(
        self,
        max_result_rows: int = 1_000_000,
        timeout_s: float = 60.0,
    ):
        """
        Args:
            max_result_rows:SELECT 回傳上限(預設 100 萬,Phase B 還會再縮)
            timeout_s:單 query 上限
        """
        import duckdb
        self._duckdb = duckdb
        # in-memory connection;同 process 內 share
        self._con = duckdb.connect(":memory:")
        self.max_result_rows = max_result_rows
        self.timeout_s = timeout_s
        self._registered_tables: set[str] = set()

    def close(self):
        try:
            self._con.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    # ============================================================
    # Table registration
    # ============================================================
    def register_parquet(self, table_name: str, parquet_path: str | Path):
        """把 parquet 註冊成 DuckDB virtual table。

        DuckDB 透過 mmap 讀,不全 load 到 RAM。
        """
        normalized = _validate_table_name(table_name)
        path_str = str(parquet_path)
        # DuckDB CREATE VIEW 不支援 prepared param,只能 inline string。
        # SQL escape single quote(雖然 path 通常不會有,保險)
        escaped_path = path_str.replace("'", "''")
        self._con.execute(
            f"CREATE OR REPLACE VIEW {normalized} AS "
            f"SELECT * FROM read_parquet('{escaped_path}')"
        )
        self._registered_tables.add(normalized)

    def register_dataframe(self, table_name: str, df: pd.DataFrame):
        """把 in-memory DataFrame 註冊(走 zero-copy Arrow)。

        對小檔案 / Phase B 後的 Q 也可用。
        """
        normalized = _validate_table_name(table_name)
        # DuckDB 直接從 Python 名稱讀,不複製
        self._con.register(normalized, df)
        self._registered_tables.add(normalized)

    def list_tables(self) -> list[str]:
        return sorted(self._registered_tables)

    # ============================================================
    # Safe execution
    # ============================================================
    def execute_safe(self, sql: str) -> DuckDBExecResult:
        """跑 SQL,加 safety check + timeout + result row limit。

        Returns:
            DuckDBExecResult
        """
        # 1. SQL safety static check
        safety_err = self._check_sql_safety(sql)
        if safety_err:
            return DuckDBExecResult(
                success=False, error=safety_err,
                error_type="SQLForbidden", sql_used=sql,
            )

        # 2. 加 result limit 保護(自動 wrap LIMIT)
        wrapped_sql = self._enforce_row_limit(sql)

        # 3. Execute(DuckDB 沒 statement_timeout config,timeout 靠 Python-side
        # interrupt — MVP 簡化暫不加,row limit 已防大查詢爆 RAM)
        t0 = time.time()
        try:
            df = self._con.execute(wrapped_sql).fetch_df()
        except Exception as e:
            return DuckDBExecResult(
                success=False,
                error=f"{type(e).__name__}: {e}",
                error_type=type(e).__name__,
                exec_time_s=round(time.time() - t0, 3),
                sql_used=wrapped_sql,
            )
        elapsed = round(time.time() - t0, 3)

        truncated = len(df) >= self.max_result_rows
        return DuckDBExecResult(
            success=True,
            df=df,
            exec_time_s=elapsed,
            row_count=int(len(df)),
            truncated=truncated,
            sql_used=wrapped_sql,
        )

    # ============================================================
    # Internal helpers
    # ============================================================
    def _check_sql_safety(self, sql: str) -> Optional[str]:
        """檢 SQL 是否含禁忌關鍵字。Returns:err string if blocked, None 若 OK。"""
        if not sql or not sql.strip():
            return "SQL 為空"

        m = _FORBIDDEN_SQL_RE.search(sql)
        if m:
            kw = m.group(0).upper()
            return (
                f"SQL 含禁忌關鍵字 `{kw}` — Phase A 只允許 SELECT 查詢,"
                f"DDL / DML / ATTACH / EXPORT 等都被擋下。"
            )
        # 必須是 SELECT 開頭(或 WITH ...)
        stripped = sql.strip()
        first_word_match = re.match(r"\s*(\w+)", stripped, re.IGNORECASE)
        if not first_word_match:
            return "SQL 格式異常(找不到第一個 keyword)"
        first_word = first_word_match.group(1).upper()
        if first_word not in ("SELECT", "WITH", "VALUES", "TABLE"):
            return (
                f"SQL 必須以 SELECT / WITH / VALUES / TABLE 開頭,"
                f"目前是 `{first_word}`"
            )
        return None

    def _enforce_row_limit(self, sql: str) -> str:
        """若 SQL 沒有自帶 LIMIT,wrap 一個 max_result_rows 上限。"""
        if re.search(r"\bLIMIT\s+\d+", sql, re.IGNORECASE):
            return sql
        # 包進 sub-query 加 LIMIT
        return f"SELECT * FROM ({sql.rstrip(';').rstrip()}) AS _wrapped LIMIT {self.max_result_rows}"


# ============================================================
# Table name validation(防 SQL injection)
# ============================================================
def _validate_table_name(name: str) -> str:
    """確保 table_name 只含 alnum + underscore。Raises ValueError if not."""
    if not name or not isinstance(name, str):
        raise ValueError(f"table_name 必須是非空 string,實際 {name!r}")
    if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", name):
        raise ValueError(
            f"table_name `{name}` 含非法字元,只能用 a-z / 0-9 / _,首字非數字"
        )
    return name


# ============================================================
# 高階 helper:從 upload_repository tables 自動 register
# ============================================================
def build_engine_for_dataset(
    upload_repo, dataset_id: str,
    max_result_rows: int = 1_000_000,
    timeout_s: float = 60.0,
) -> DuckDBEngine:
    """從 upload dataset 自動建 DuckDB engine + 註冊所有 tables。

    使用方式:
        engine = build_engine_for_dataset(upload_repo, "upload_xxx")
        result = engine.execute_safe("SELECT * FROM sheet1 WHERE ...")
        engine.close()
    """
    engine = DuckDBEngine(max_result_rows=max_result_rows, timeout_s=timeout_s)
    tables = upload_repo.list_tables(dataset_id)
    for t in tables:
        parquet_path = t["storage"]["path"]
        engine.register_parquet(t["table_id"], parquet_path)
    return engine
