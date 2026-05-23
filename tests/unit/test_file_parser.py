"""tests/unit/test_file_parser.py — unit tests for file_parser.py (M4a)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

import file_parser
from file_parser import (
    ALLOWED_EXTENSIONS,
    FileParseError,
    MAX_FILE_SIZE_BYTES,
    load_parquet,
    normalize_column_name,
    normalize_dataframe_columns,
    parse_csv,
    parse_to_parquet,
    validate_file,
)


# ============================================================
# normalize_column_name
# ============================================================
class TestNormalizeColumnName:
    def test_lowercase(self):
        assert normalize_column_name("Project ID") == "project_id"

    def test_strip_whitespace(self):
        assert normalize_column_name("  leadtime  ") == "leadtime"

    def test_strip_special_chars(self):
        assert normalize_column_name("status!") == "status"
        assert normalize_column_name("hire_date(UTC)") == "hire_dateutc"

    def test_keep_chinese(self):
        assert normalize_column_name("申請日期") == "申請日期"

    def test_empty_returns_default(self):
        assert normalize_column_name("") == "col"
        assert normalize_column_name("   ") == "col"
        assert normalize_column_name("!!!") == "col"

    def test_collapse_consecutive_underscores(self):
        assert normalize_column_name("a___b") == "a_b"

    def test_collision_resolution(self):
        existing = {"project_id"}
        assert normalize_column_name("project id", existing=existing) == "project_id_2"
        existing.add("project_id_2")
        assert normalize_column_name("Project ID", existing=existing) == "project_id_3"

    def test_non_string_input(self):
        # 應該能處理 int / float / 其他 input
        assert normalize_column_name(123) == "123"


class TestNormalizeDataFrameColumns:
    def test_basic(self):
        df = pd.DataFrame({"Project ID": [1, 2], "Lead Time": [10, 20]})
        new_df, mapping = normalize_dataframe_columns(df)
        assert list(new_df.columns) == ["project_id", "lead_time"]
        assert mapping == {"Project ID": "project_id", "Lead Time": "lead_time"}

    def test_collision_handled(self):
        # 兩個欄位 normalize 後相同 → 第二個加 _2
        df = pd.DataFrame({"Project ID": [1], "project_id": [2]})
        new_df, mapping = normalize_dataframe_columns(df)
        assert "project_id" in new_df.columns
        assert "project_id_2" in new_df.columns

    def test_original_df_not_mutated(self):
        df = pd.DataFrame({"Project ID": [1]})
        original_cols = list(df.columns)
        normalize_dataframe_columns(df)
        assert list(df.columns) == original_cols


# ============================================================
# validate_file
# ============================================================
class TestValidateFile:
    def test_csv_passes(self, tmp_path):
        f = tmp_path / "ok.csv"
        f.write_text("a,b\n1,2\n")
        file_type, size = validate_file(f)
        assert file_type == "csv"
        assert size > 0

    def test_xlsx_passes(self, tmp_path):
        f = tmp_path / "ok.xlsx"
        # 寫個空 binary 模擬 xlsx 副檔名(validate 不檢查內容,只看副檔名)
        f.write_bytes(b"fake xlsx")
        file_type, size = validate_file(f)
        assert file_type == "excel"

    def test_missing_file(self, tmp_path):
        with pytest.raises(FileParseError, match="不存在"):
            validate_file(tmp_path / "nope.csv")

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.csv"
        f.write_bytes(b"")
        with pytest.raises(FileParseError, match="0"):
            validate_file(f)

    def test_oversized_rejected(self, tmp_path, monkeypatch):
        # Mock MAX 為 100 bytes 測試上限保護
        monkeypatch.setattr(file_parser, "MAX_FILE_SIZE_BYTES", 100)
        f = tmp_path / "big.csv"
        f.write_bytes(b"x" * 200)
        with pytest.raises(FileParseError, match="超過限制"):
            validate_file(f)

    def test_unknown_ext_rejected(self, tmp_path):
        f = tmp_path / "bad.txt"
        f.write_text("data")
        with pytest.raises(FileParseError, match="不允許"):
            validate_file(f)


# ============================================================
# parse_csv
# ============================================================
class TestParseCSV:
    def test_clean_csv(self, golden_data_dir):
        df = parse_csv(golden_data_dir / "projects_clean.csv")
        assert len(df) == 15
        assert "project_id" in df.columns
        assert "leadtime" in df.columns

    def test_csv_with_total_row(self, golden_data_dir):
        df = parse_csv(golden_data_dir / "projects_with_total_row.csv")
        # 解析時不過濾 TOTAL row,留給 phase_b_validator 偵測
        assert len(df) == 6
        assert df.iloc[-1]["project_id"] == "TOTAL"

    def test_mixed_type_column(self, golden_data_dir):
        df = parse_csv(golden_data_dir / "mixed_type_column.csv")
        assert len(df) == 10
        # score 欄混型別(數字 + "TBD"),pandas 應該推為 string/object,
        # 不該是純 numeric(pandas ≥2.0 可能用 StringDtype 或 object,兩者都可)
        assert not pd.api.types.is_numeric_dtype(df["score"]), \
            f"score 應該是 string/object,但是 {df['score'].dtype}"


# ============================================================
# parse_to_parquet — 端到端
# ============================================================
class TestParseToParquet:
    def test_csv_e2e(self, golden_data_dir, tmp_path):
        result = parse_to_parquet(
            source_path=golden_data_dir / "projects_clean.csv",
            parquet_dir=tmp_path,
            table_id="sheet1",
        )
        # 基本驗證
        assert result["table_id"] == "sheet1"
        assert result["row_count"] == 15
        assert result["column_count"] == 6
        assert result["file_type"] == "csv"
        assert result["warnings"] == []
        # Parquet 真的寫出來
        parquet_path = Path(result["storage"]["path"])
        assert parquet_path.exists()
        # 讀回來應該等價
        df = load_parquet(parquet_path)
        assert len(df) == 15

    def test_oversized_csv_rejected(self, tmp_path, monkeypatch):
        f = tmp_path / "big.csv"
        f.write_bytes(b"a,b\n" + b"1,2\n" * 100_000)
        monkeypatch.setattr(file_parser, "MAX_FILE_SIZE_BYTES", 1000)
        with pytest.raises(FileParseError, match="超過"):
            parse_to_parquet(source_path=f, parquet_dir=tmp_path)

    def test_empty_csv_rejected(self, tmp_path):
        f = tmp_path / "empty.csv"
        f.write_bytes(b"")
        with pytest.raises(FileParseError):
            parse_to_parquet(source_path=f, parquet_dir=tmp_path)

    def test_returns_normalized_columns(self, tmp_path):
        f = tmp_path / "weird.csv"
        f.write_text("Project ID,Lead Time (days)\nPRJ-001,42\n")
        result = parse_to_parquet(source_path=f, parquet_dir=tmp_path)
        # 欄名應該被 normalize
        assert "project_id" in result["normalized_columns"]
        assert result["original_to_normalized"]["Project ID"] == "project_id"


# ============================================================
# Constants
# ============================================================
class TestConstants:
    def test_max_size_is_100mb(self):
        assert MAX_FILE_SIZE_BYTES == 100 * 1024 * 1024

    def test_allowed_extensions(self):
        assert ".csv" in ALLOWED_EXTENSIONS
        assert ".xlsx" in ALLOWED_EXTENSIONS
        assert ".xls" in ALLOWED_EXTENSIONS
        # 危險副檔名不該在內
        assert ".exe" not in ALLOWED_EXTENSIONS
        assert ".sh" not in ALLOWED_EXTENSIONS
