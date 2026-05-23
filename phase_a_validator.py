"""
phase_a_validator.py — v0.12.0+ (Upload Workspace Phase A validator)

對 Upload-driven Phase A 產出的 Pandas filter code 做 semantic + safety 檢查。
對應既有 phase_b_validator.py / phase_c_validator.py 的設計風格。

# 為什麼 Upload-driven 需要獨立 validator

Schema-driven Phase A 是 MongoDB pipeline JSON,有 `sanitize_pipeline` 做策略性
strip(派生 operator / 空白前綴 / 漏 $)。但 Upload-driven Phase A 是 Pandas
**code**,完全不同的攻擊面:
- LLM 可能 import 套件(sandbox 沒裝會炸)
- LLM 可能 read_csv/open/requests(資料外洩風險)
- LLM 可能聚合(那是 Phase B 工作)
- LLM 可能引用不存在的欄位(KeyError)
- LLM 可能漏寫 `raw_df = ...`

# 5 個 check

| Check ID                  | 觸發條件                                      | 嚴重度 |
|---------------------------|---------------------------------------------|--------|
| A_FORBIDDEN_IMPORT        | `import` / `from ... import` / `__import__` | FATAL  |
| A_FORBIDDEN_IO            | `open`/`read_csv`/`os.`/`subprocess`/...    | FATAL  |
| A_HALLUCINATED_COLUMN     | 引用的 col 不在 source_df.columns           | HIGH   |
| A_DERIVED_NEW_COLUMN      | `raw_df['<new>'] = ...` 引入 raw 沒有的欄位 | MED    |
| A_NO_RAW_DF               | exec 後 namespace 沒 `raw_df` 變數          | FATAL  |

# 對外 API

```python
validate_phase_a_output(code, exec_namespace, source_columns) -> list[str]
    Returns: list of issue strings, each 含 [CHECK_NAME] 前綴 + 中文解釋
    Empty list = OK
    Non-empty = 應該 retry,把這個 list 透過 format_issues_as_retry_hint()
                包裝成 previous_error 傳回 generate_pandas_extraction

PANDAS_FILTER_ANTIPATTERN_CHEATSHEET (str constant)
    可選附在 retry hint 後面提示 LLM 常見 anti-pattern
```

# 跟既有 sanitize_pipeline 的關係

兩者**不衝突**:
- sanitize_pipeline 只服務 schema-driven Phase A(MongoDB JSON)
- phase_a_validator 只服務 upload-driven Phase A(Pandas code)
- 兩者完全平行,看 LLMService 是走 generate_pipeline 還是 generate_pandas_extraction
"""

from __future__ import annotations

import ast
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


# ============================================================
# Forbidden tokens
# ============================================================
# Module import 系列(任何形式)
# - `import xxx` / `from xxx import yyy` 必須行首
# - `__import__(...)` 任何位置都不允許(常見繞道手法)
_FORBIDDEN_IMPORT_RE = re.compile(
    r"^\s*(?:import\s+\w+|from\s+\w+\s+import)|__import__\s*\(",
    re.MULTILINE,
)

# I/O / 系統呼叫名單(可 mutate sandbox / 外洩資料)
_FORBIDDEN_IO_TOKENS = (
    "open(",
    "read_csv(",
    "read_excel(",
    "read_parquet(",
    "read_json(",
    "read_sql(",
    "read_html(",
    "read_pickle(",
    "to_csv(",
    "to_excel(",
    "to_pickle(",
    "to_sql(",
    "os.",
    "sys.",
    "subprocess.",
    "subprocess(",
    "requests.",
    "urllib.",
    "socket.",
    "eval(",
    "exec(",
    "compile(",
    "globals(",
    "locals(",
    "__builtins__",
)


# ============================================================
# Individual checks
# ============================================================
def _check_no_forbidden_import(code: str) -> list[str]:
    """檢查 code 內是否有 import 語法。"""
    issues = []
    if _FORBIDDEN_IMPORT_RE.search(code):
        # 找出 offending 行
        bad_lines = [
            f"L{i + 1}: {line.strip()}"
            for i, line in enumerate(code.splitlines())
            if _FORBIDDEN_IMPORT_RE.match(line)
        ]
        issues.append(
            f"[A_FORBIDDEN_IMPORT] Phase A 禁止 import 任何套件 — "
            f"sandbox 已備好 `pd` / `np` / `source_df`,不需要也禁止 import。"
            f"違規行: {', '.join(bad_lines[:3])}"
        )
    return issues


def _check_no_forbidden_io(code: str) -> list[str]:
    """檢查 code 是否含 I/O / 系統呼叫關鍵字。"""
    issues = []
    hits = []
    for tok in _FORBIDDEN_IO_TOKENS:
        if tok in code:
            hits.append(tok)
    if hits:
        issues.append(
            f"[A_FORBIDDEN_IO] Phase A 禁止外部 IO / 系統呼叫 — "
            f"偵測到 `{', '.join(hits[:5])}`。Phase A 只能 filter / select "
            f"`source_df`,不可讀檔 / 寫檔 / 跑 subprocess。"
        )
    return issues


def _check_raw_df_exists(exec_namespace: dict) -> list[str]:
    """exec 後 namespace 內是否有 raw_df,且是 DataFrame。"""
    import pandas as pd
    issues = []
    rdf = exec_namespace.get("raw_df")
    if rdf is None:
        issues.append(
            "[A_NO_RAW_DF] Phase A 結尾必須有 `raw_df = ...`。"
            "Phase B 找不到 raw_df 會炸 NameError。"
        )
    elif not isinstance(rdf, pd.DataFrame):
        issues.append(
            f"[A_NO_RAW_DF] `raw_df` 不是 DataFrame(實際是 "
            f"`{type(rdf).__name__}`)。必須 `raw_df = source_df[...]` 之類。"
        )
    elif len(rdf) == 0:
        issues.append(
            "[A_NO_RAW_DF] `raw_df` 是空 DataFrame(0 列)。"
            "請檢查 filter 條件是否過嚴或欄位值名稱錯誤。"
        )
    return issues


def _check_hallucinated_columns(
    code: str,
    source_columns: list[str],
) -> list[str]:
    """檢查 code 內 `source_df['xxx']` 的 xxx 是否在 source_columns。"""
    if not source_columns:
        return []
    valid_cols = set(source_columns)
    # 找所有 source_df['<col>'] / source_df["<col>"] 引用
    refs = re.findall(
        r"source_df\s*\[\s*['\"]([^'\"]+)['\"]\s*\]",
        code,
    )
    hallucinated = [c for c in set(refs) if c not in valid_cols]
    issues = []
    if hallucinated:
        issues.append(
            f"[A_HALLUCINATED_COLUMN] code 引用了不存在的欄位:"
            f"`{', '.join(hallucinated)}`。"
            f"`source_df` 實際只有:{sorted(valid_cols)[:10]}"
            f"{'...' if len(valid_cols) > 10 else ''}"
        )
    return issues


def _check_no_derived_columns(code: str) -> list[str]:
    """檢查 code 是否有 `raw_df['xxx'] = ...` 或 `.assign(...)` 引入新欄位。

    Phase A 不應該派生欄位 — 那是 Phase B 的工作。
    """
    issues = []
    derived_hits = []
    # `raw_df['x'] = ...` (excluding read which is different syntax)
    if re.search(r"raw_df\s*\[\s*['\"][^'\"]+['\"]\s*\]\s*=", code):
        derived_hits.append("raw_df['<col>'] = ...")
    # `.assign(` 任何 DataFrame(raw_df / source_df)都不該派生 — generic
    if re.search(r"\.assign\s*\(", code):
        derived_hits.append(".assign(...)")
    if re.search(r"\.groupby\s*\(", code):
        derived_hits.append(".groupby(...)")
    if re.search(r"\.agg\s*\(", code):
        derived_hits.append(".agg(...)")
    if re.search(r"\.merge\s*\(", code):
        derived_hits.append(".merge(...)")
    if re.search(r"\.pivot\s*\(", code) or re.search(r"\.pivot_table\s*\(", code):
        derived_hits.append(".pivot(...)")
    if derived_hits:
        issues.append(
            f"[A_DERIVED_NEW_COLUMN] Phase A 不可派生新欄位或聚合 — "
            f"偵測到:`{', '.join(derived_hits)}`。"
            f"請只做 filter / column subset,聚合留給 Phase B。"
        )
    return issues


# ============================================================
# Public entry
# ============================================================
def validate_phase_a_output(
    code: str,
    exec_namespace: dict,
    source_columns: list[str] | None = None,
) -> list[str]:
    """跑全部 5 個 check,回傳 issue list。

    Args:
        code: Phase A 產出的 Python code 字串
        exec_namespace: exec 後的 namespace dict(用於檢查 raw_df 是否存在)
        source_columns: source_df 實際欄位 list(用於 hallucinated column 偵測)

    Returns:
        list of issue strings(空 list = 通過)
    """
    issues: list[str] = []
    # 靜態檢查(對 code 字串)
    issues.extend(_check_no_forbidden_import(code))
    issues.extend(_check_no_forbidden_io(code))
    issues.extend(_check_no_derived_columns(code))
    if source_columns:
        issues.extend(_check_hallucinated_columns(code, source_columns))
    # 動態檢查(對 exec 結果)
    issues.extend(_check_raw_df_exists(exec_namespace))
    return issues


# ============================================================
# Retry hint formatter
# ============================================================
def format_issues_as_retry_hint(issues: list[str]) -> str:
    """把 issue list 包裝成 LLM retry feedback 字串。"""
    if not issues:
        return ""
    lines = ["\n🔍 Phase A semantic check 失敗,以下問題請修正:\n"]
    for i, issue in enumerate(issues, 1):
        lines.append(f"  {i}. {issue}")
    lines.append(
        "\n請重新產生 Pandas code:**只用 filter / select**,"
        "**禁止 import / IO / 聚合 / 派生欄位**,確保最後有 `raw_df = ...`。"
    )
    return "\n".join(lines)


# ============================================================
# Anti-pattern cheatsheet — 給 generate_pandas_extraction retry 用
# ============================================================
PANDAS_FILTER_ANTIPATTERN_CHEATSHEET = """
### 🛡 Phase A · Pandas Filter Anti-pattern 速查表

❌  `import pandas as pd`(或 import 任何套件)
    為什麼:sandbox 已備好 `pd` / `np` / `source_df`,不需要也禁止 import。
    ✅  直接用,例:`raw_df = source_df[source_df['col']=='X']`

❌  `pd.read_csv(...)` / `open(...)` / `os.path...` / `requests.get(...)`
    為什麼:Phase A 只能讀 `source_df`,不可讀任何其他資料源。
    ✅  全部從 `source_df` 拿:`source_df.loc[...]` / `source_df.query("...")`

❌  `raw_df['new_col'] = source_df['a'] / source_df['b']`(派生新欄位)
    為什麼:派生欄位是 Phase B 的工作。Phase A 只負責「從 source_df 取列子集」。
    ✅  Phase B 會在 raw_df 上跑 `.assign(...)` 等;A 段保持 row filter 純粹。

❌  `raw_df = source_df.groupby(...).agg(...)` / `.merge(...)` / `.pivot(...)`
    為什麼:聚合 / 合表 / pivot 都是 Phase B 的工作。
    ✅  例:`raw_df = source_df[source_df['cat'].isin(['A', 'B'])]`(只 filter)

❌  `raw_df = source_df[source_df['不存在的欄位']=='X']`(hallucinated column)
    為什麼:`source_columns` 已明列實際欄位,LLM 不該憑想像猜。
    ✅  只引用 `source_columns` 中真實存在的欄位。

❌  忘記寫 `raw_df = ...`
    為什麼:Phase B 找不到 raw_df 直接 NameError。
    ✅  最後一行必寫 `raw_df = <filtered DataFrame>`(即使 = source_df.copy())

✅  典型正確範本
    ```python
    # 範例 1:單條件 filter
    raw_df = source_df[source_df['status'] == 'completed']

    # 範例 2:多條件 + 欄位子集
    mask = (source_df['region'] == 'TW') & (source_df['amount'] > 1000)
    raw_df = source_df.loc[mask, ['region', 'category', 'amount']]

    # 範例 3:Plan 沒列任何 filter — 全帶
    raw_df = source_df.copy()
    ```
"""
