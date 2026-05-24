"""
file_parser.py — v0.12.0+

CSV / Excel single-sheet 解析模組 — 把上傳檔案解成 DataFrame 並轉存 parquet。

# 為什麼要轉 parquet
原檔留作 audit trail(SHA256 + 30 天 retention),但 staging 用 parquet:
  - 比 CSV 小 ~70%
  - dtype 不會丟(CSV 全 string,parquet 保 numeric / datetime)
  - 之後 DuckDB engine 可直接 register parquet,零 copy

# 為什麼欄名要 normalize
LLM 寫 Pandas code 時對「Column With Spaces」「中文欄位」「`special_chars!`」會出錯。
Normalize 規則:
  - strip 前後空白
  - 連續空白 → 單一 underscore
  - 移除非 `[a-z0-9_中文]` 字元
  - 全小寫(英文)
  - 衝突時加 `_2` / `_3` 後綴
Normalize 結果存進 upload_tables.normalized_columns,並在 profile 中保留原欄名 mapping。

# MVP 限制(spec §14.1)
- file size ≤ 100MB
- 副檔名白名單:.csv, .xlsx, .xls
- Excel 只支援 single sheet — multi-sheet 留 Phase 2
- Excel macro 不執行(openpyxl 預設 read-only 不 trigger macro)
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


# ============================================================
# 規格常量
# ============================================================
MAX_FILE_SIZE_BYTES = 100 * 1024 * 1024  # 100 MB
# v0.15.0+: 直接 parquet upload(spec §4.2 item 8)
ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xls", ".parquet"}


class FileParseError(Exception):
    """File parser 內部錯誤 — caller 應 catch 並寫進 dataset.error_message。"""


# ============================================================
# 欄名 normalize
# ============================================================
def normalize_column_name(name: str, existing: set[str] = None) -> str:
    """把任意欄名轉成 Pandas 友善的 identifier。

    例子:
      "Project ID"       -> "project_id"
      "  leadtime  "     -> "leadtime"
      "Hire Date (UTC)"  -> "hire_date_utc"
      "申請日期"          -> "申請日期"  (中文保留)
      "status!"          -> "status"

    Args:
        name: 原欄名
        existing: 已存在的 normalized 欄名 set,用於避免衝突(加 _2 / _3)

    Returns:
        normalized 字串。空字串會回 "col"。
    """
    if not isinstance(name, str):
        name = str(name)
    s = name.strip().lower()
    # 連續空白 / 特殊符號 → 底線
    s = re.sub(r"[\s\-]+", "_", s)
    # 保留 ASCII alphanum + underscore + 中文(CJK)
    s = re.sub(r"[^a-z0-9_一-鿿]", "", s)
    # 連續底線收成一個,並去頭尾底線
    s = re.sub(r"_+", "_", s).strip("_")
    if not s:
        s = "col"

    # 衝突解決
    if existing is None:
        return s
    base = s
    i = 2
    while s in existing:
        s = f"{base}_{i}"
        i += 1
    return s


def normalize_dataframe_columns(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, str]]:
    """把 DataFrame 欄名全部 normalize,回傳 (new_df, original_to_normalized_map)。

    `df` 不被 mutate;回傳新的 DataFrame。
    """
    mapping: dict[str, str] = {}
    seen: set[str] = set()
    new_names: list[str] = []
    for col in df.columns:
        norm = normalize_column_name(str(col), existing=seen)
        seen.add(norm)
        mapping[str(col)] = norm
        new_names.append(norm)
    new_df = df.copy()
    new_df.columns = new_names
    return new_df, mapping


# ============================================================
# Parser
# ============================================================
def parse_csv(source_path: Path) -> pd.DataFrame:
    """讀 CSV → DataFrame。
    保留 default NaN 偵測但不強轉 dtype(讓 pandas 自動推);
    全 string 欄會以 object dtype 回。"""
    try:
        df = pd.read_csv(source_path, keep_default_na=True)
    except UnicodeDecodeError:
        # 中文 CSV 偶爾用 big5 / gb18030,試常見編碼
        for enc in ("big5", "gb18030", "utf-8-sig"):
            try:
                df = pd.read_csv(source_path, encoding=enc, keep_default_na=True)
                logger.warning(f"CSV 用 {enc} 讀成功(原 utf-8 失敗)")
                return df
            except UnicodeDecodeError:
                continue
        raise FileParseError(
            "CSV 編碼無法辨識,試過 utf-8 / big5 / gb18030 / utf-8-sig"
        )
    except pd.errors.EmptyDataError:
        raise FileParseError("CSV 內容空白,無法解析")
    except pd.errors.ParserError as e:
        raise FileParseError(f"CSV 格式錯誤: {e}")
    return df


def parse_excel(source_path: Path) -> tuple[pd.DataFrame, str, bool]:
    """讀 Excel single sheet(legacy MVP 介面,保留 backward compat)。

    新 caller 用 parse_excel_all_sheets 拿 multi-sheet dict。

    Returns:
        (df, sheet_name, has_multiple_sheets)
    """
    try:
        excel = pd.ExcelFile(source_path, engine="openpyxl")
    except Exception:
        try:
            excel = pd.ExcelFile(source_path)
        except Exception as e2:
            raise FileParseError(f"Excel 無法開啟: {e2}")

    sheet_names = excel.sheet_names
    if not sheet_names:
        raise FileParseError("Excel 內 0 個 sheet")

    first_sheet = sheet_names[0]
    try:
        df = excel.parse(first_sheet)
    except Exception as e:
        raise FileParseError(f"Excel sheet `{first_sheet}` 解析失敗: {e}")

    has_multiple = len(sheet_names) > 1
    return df, first_sheet, has_multiple


def parse_excel_all_sheets(
    source_path: Path,
    max_sheets: int = 20,
) -> dict[str, pd.DataFrame]:
    """v0.15.0+ (M5.1):讀 Excel 全部 sheet,回 {sheet_name: df} dict。

    Args:
        max_sheets:protection,Excel 含 sheet 超過此數 → 只讀前 N 個 + warning。

    Returns:
        {sheet_name: DataFrame}(順序按 Excel 內定義)

    Raises:
        FileParseError:Excel 無法開啟,或全部 sheet 解析都失敗
    """
    try:
        excel = pd.ExcelFile(source_path, engine="openpyxl")
    except Exception:
        try:
            excel = pd.ExcelFile(source_path)
        except Exception as e2:
            raise FileParseError(f"Excel 無法開啟: {e2}")

    sheet_names = excel.sheet_names
    if not sheet_names:
        raise FileParseError("Excel 內 0 個 sheet")

    if len(sheet_names) > max_sheets:
        logger.warning(
            f"Excel 含 {len(sheet_names)} 個 sheet,超過 max_sheets={max_sheets}。"
            f"只讀前 {max_sheets} 個。"
        )
        sheet_names = sheet_names[:max_sheets]

    parsed: dict[str, pd.DataFrame] = {}
    parse_errors: list[str] = []
    for name in sheet_names:
        try:
            df = excel.parse(name)
            # 跳過空 sheet
            if df.shape[0] == 0:
                logger.warning(f"Sheet `{name}` 0 列,跳過")
                continue
            parsed[name] = df
        except Exception as e:
            parse_errors.append(f"{name}: {e}")
            logger.warning(f"Sheet `{name}` 解析失敗:{e}")

    if not parsed:
        raise FileParseError(
            f"Excel 全部 sheet 解析失敗 / 都是空 sheet。錯誤:{parse_errors}"
        )
    return parsed


def parse_parquet_direct(source_path: Path) -> pd.DataFrame:
    """v0.15.0+ (M5.1):直接讀 .parquet 上傳檔(skip CSV/Excel 流程)。

    Parquet 已有 schema/dtype,不需要 normalize 後再轉 parquet。
    但我們仍會 normalize 欄名(避免空白/特殊符號)。
    """
    try:
        df = pd.read_parquet(source_path)
    except Exception as e:
        raise FileParseError(f"Parquet 解析失敗:{e}")
    if df.shape[0] == 0:
        raise FileParseError("Parquet 內 0 列")
    return df


# ============================================================
# Validation
# ============================================================
def validate_file(source_path: Path) -> tuple[str, int]:
    """檔案完整性驗證(在 parse 之前先做)。

    Returns:
        (file_type, file_size_bytes)
        file_type: "csv" | "excel"

    Raises:
        FileParseError: 副檔名不允許、檔案太大、檔案不存在
    """
    if not source_path.exists():
        raise FileParseError(f"檔案不存在: {source_path}")
    size = source_path.stat().st_size
    if size == 0:
        raise FileParseError("檔案大小為 0")
    if size > MAX_FILE_SIZE_BYTES:
        raise FileParseError(
            f"檔案大小 {size:,} bytes 超過限制 "
            f"{MAX_FILE_SIZE_BYTES:,} bytes (100 MB)"
        )
    ext = source_path.suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise FileParseError(
            f"副檔名 `{ext}` 不允許,可接受:{sorted(ALLOWED_EXTENSIONS)}"
        )
    # v0.15.0+ M5.1:加 parquet file_type(.parquet 直接上傳)
    if ext == ".csv":
        file_type = "csv"
    elif ext == ".parquet":
        file_type = "parquet"
    else:
        file_type = "excel"   # .xlsx / .xls
    return file_type, size


# ============================================================
# Orchestrator
# ============================================================
def parse_to_parquet(
    source_path: Path,
    parquet_dir: Path,
    table_id: str = "sheet1",
) -> dict[str, Any]:
    """主入口:讀檔 → normalize 欄名 → 存 parquet,回傳 metadata dict。

    Args:
        source_path: 上傳檔案的本機路徑
        parquet_dir: parquet 輸出資料夾(會 mkdir)
        table_id: 該 table 的 id,默認 "sheet1"

    Returns:
        dict:
        {
          "table_id": "sheet1",
          "table_name": "Sheet1" / "project_leadtime.csv",
          "row_count": int,
          "column_count": int,
          "normalized_columns": [...],         # parquet 內的欄名
          "original_to_normalized": {...},      # 原欄名 → normalized 對照
          "storage": {"format": "parquet", "path": str(parquet_path)},
          "file_type": "csv" | "excel",
          "warnings": [...],                    # 解析時警告(例如多 sheet)
        }

    Raises:
        FileParseError
    """
    file_type, size_bytes = validate_file(source_path)
    warnings: list[str] = []

    if file_type == "csv":
        df = parse_csv(source_path)
        table_name = source_path.name
    elif file_type == "parquet":
        # v0.15.0+ M5.1:直接 parquet upload
        df = parse_parquet_direct(source_path)
        table_name = source_path.name
    else:  # excel
        df, sheet_name, has_multi = parse_excel(source_path)
        table_name = sheet_name
        if has_multi:
            warnings.append(
                f"Excel 含多個 sheet,MVP single-sheet 介面只讀第一個 (`{sheet_name}`)。"
                "請改用 parse_excel_to_parquet_multi() 處理多 sheet(M5.1+)。"
            )

    if df.shape[0] == 0:
        raise FileParseError("解析結果 0 列")

    # Normalize 欄名
    df, mapping = normalize_dataframe_columns(df)

    # 寫 parquet
    parquet_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = parquet_dir / f"{table_id}.parquet"
    try:
        df.to_parquet(parquet_path, index=False)
    except Exception as e:
        raise FileParseError(
            f"寫 parquet 失敗: {e}(可能需要 pyarrow / fastparquet)"
        )

    return {
        "table_id": table_id,
        "table_name": table_name,
        "row_count": int(df.shape[0]),
        "column_count": int(df.shape[1]),
        "normalized_columns": list(df.columns),
        "original_to_normalized": mapping,
        "storage": {"format": "parquet", "path": str(parquet_path)},
        "file_type": file_type,
        "warnings": warnings,
    }


def load_parquet(parquet_path: str | Path) -> pd.DataFrame:
    """讀回 parquet → DataFrame(profiler / analysis 用)。"""
    return pd.read_parquet(parquet_path)


def parse_excel_to_parquet_multi(
    source_path: Path,
    parquet_dir: Path,
    max_sheets: int = 20,
) -> list[dict[str, Any]]:
    """v0.15.0+ (M5.1):Excel multi-sheet → 多個 parquet 檔案。

    對應 spec §4.2 item 1。每個 sheet 對應一個 upload_table。

    Returns:
        list of dicts(每 sheet 一筆),每筆同 parse_to_parquet 結構:
        [
          {table_id, table_name, row_count, column_count,
           normalized_columns, original_to_normalized,
           storage, file_type='excel', warnings},
          ...
        ]
    """
    file_type, _size = validate_file(source_path)
    if file_type != "excel":
        raise FileParseError(
            f"parse_excel_to_parquet_multi 只接受 Excel,實際 file_type={file_type}"
        )

    parquet_dir.mkdir(parents=True, exist_ok=True)
    sheet_dfs = parse_excel_all_sheets(source_path, max_sheets=max_sheets)

    results: list[dict[str, Any]] = []
    used_table_ids: set[str] = set()

    for sheet_name, df in sheet_dfs.items():
        # Normalize sheet_name → safe table_id
        table_id_base = normalize_column_name(sheet_name) or "sheet"
        table_id = table_id_base
        # Resolve table_id 衝突
        suffix = 2
        while table_id in used_table_ids:
            table_id = f"{table_id_base}_{suffix}"
            suffix += 1
        used_table_ids.add(table_id)

        # Normalize 欄名
        df_norm, mapping = normalize_dataframe_columns(df)
        # 寫 parquet
        parquet_path = parquet_dir / f"{table_id}.parquet"
        try:
            df_norm.to_parquet(parquet_path, index=False)
        except Exception as e:
            raise FileParseError(f"sheet `{sheet_name}` 寫 parquet 失敗: {e}")

        results.append({
            "table_id": table_id,
            "table_name": sheet_name,
            "row_count": int(df_norm.shape[0]),
            "column_count": int(df_norm.shape[1]),
            "normalized_columns": list(df_norm.columns),
            "original_to_normalized": mapping,
            "storage": {"format": "parquet", "path": str(parquet_path)},
            "file_type": "excel",
            "warnings": [],
        })

    if not results:
        raise FileParseError("Excel 所有 sheet 都解析失敗或空")
    return results
