"""tests/unit/test_data_profiler.py — unit tests for data_profiler.py (M4a)."""

from __future__ import annotations

import pandas as pd
import pytest

from data_profiler import (
    HIGH_CARDINALITY_RATIO,
    HIGH_NULL_THRESHOLD_PCT,
    LOW_CARDINALITY_MAX,
    SKEW_RATIO_THRESHOLD,
    detect_physical_type,
    profile_column,
    profile_dataset,
    profile_table,
)


# ============================================================
# detect_physical_type
# ============================================================
class TestDetectPhysicalType:
    def test_integer(self):
        assert detect_physical_type(pd.Series([1, 2, 3])) == "integer"

    def test_float(self):
        assert detect_physical_type(pd.Series([1.0, 2.5])) == "number"

    def test_string(self):
        assert detect_physical_type(pd.Series(["a", "b"])) == "string"

    def test_boolean(self):
        assert detect_physical_type(pd.Series([True, False])) == "boolean"

    def test_datetime(self):
        s = pd.to_datetime(pd.Series(["2025-01-01", "2025-01-02"]))
        assert detect_physical_type(s) == "datetime"


# ============================================================
# profile_column — 一般情境
# ============================================================
class TestProfileColumnNumeric:
    def test_basic_stats(self):
        s = pd.Series([10, 20, 30, 40, 50])
        prof = profile_column(s, "x")
        assert prof["physical_type"] == "integer"
        assert prof["null_count"] == 0
        assert prof["distinct_count"] == 5
        assert prof["min"] == 10
        assert prof["max"] == 50
        assert prof["mean"] == 30.0
        assert prof["median"] == 30.0

    def test_right_skewed_warning(self):
        # mean >> median + p95/median > 3 → right_skewed
        s = pd.Series([1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 1000])
        prof = profile_column(s, "x")
        assert "right_skewed" in prof["warnings"]

    def test_high_cardinality(self):
        s = pd.Series(list(range(100)))
        prof = profile_column(s, "x")
        assert "high_cardinality" in prof["warnings"]

    def test_all_same(self):
        s = pd.Series([5] * 10)
        prof = profile_column(s, "x")
        assert "all_same" in prof["warnings"]

    def test_high_null(self):
        s = pd.Series([1, 2, None, None, None, None, 7, None, None, None])
        prof = profile_column(s, "x")
        assert prof["null_pct"] >= HIGH_NULL_THRESHOLD_PCT
        assert "high_null" in prof["warnings"]


class TestProfileColumnString:
    def test_basic(self):
        s = pd.Series(["A", "B", "A", "C", "A"])
        prof = profile_column(s, "category")
        assert prof["physical_type"] == "string"
        assert prof["distinct_count"] == 3
        assert "top_values" in prof
        top_a = next(t for t in prof["top_values"] if t["value"] == "A")
        assert top_a["count"] == 3

    def test_low_cardinality_warning(self):
        s = pd.Series(["Y", "N", "Y", "N", "Y"])
        prof = profile_column(s, "flag")
        assert "low_cardinality" in prof["warnings"]

    def test_suspect_id_warning(self):
        # 高基數 string + 名字含 id
        s = pd.Series([f"PRJ-{i:03d}" for i in range(100)])
        prof = profile_column(s, "project_id")
        assert "high_cardinality" in prof["warnings"]
        assert "suspect_id" in prof["warnings"]

    def test_whitespace_warning(self):
        s = pd.Series(["abc", " def", "ghi"])
        prof = profile_column(s, "x")
        assert "whitespace_in_values" in prof["warnings"]

    def test_mixed_type_warning(self):
        # object dtype 內 mix string + number
        s = pd.Series(["abc", 123, "def"], dtype=object)
        prof = profile_column(s, "x")
        assert "mixed_type" in prof["warnings"]


class TestProfileColumnEdgeCases:
    def test_all_null(self):
        s = pd.Series([None, None, None], dtype=object)
        prof = profile_column(s, "x")
        assert prof["null_pct"] == 100.0
        assert "all_null" in prof["warnings"]

    def test_empty_series(self):
        s = pd.Series([], dtype=object)
        prof = profile_column(s, "x")
        # 0 列也不該炸
        assert prof["null_count"] == 0
        assert prof["distinct_count"] == 0

    def test_sample_values_present(self):
        s = pd.Series([1, 2, 3, 1, 2, 1])
        prof = profile_column(s, "x")
        # top-1 應該是 1(出現 3 次)
        assert 1 in prof["sample_values"]


# ============================================================
# profile_table / profile_dataset
# ============================================================
class TestProfileTable:
    def test_golden_clean(self, golden_data_dir):
        df = pd.read_csv(golden_data_dir / "projects_clean.csv")
        prof = profile_table(df, "sheet1")
        assert prof["table_id"] == "sheet1"
        assert prof["row_count"] == 15
        assert prof["column_count"] == 6
        # project_id 應該被偵測為 suspect_id
        pid_prof = next(c for c in prof["columns"] if c["name"] == "project_id")
        assert "suspect_id" in pid_prof["warnings"]
        # leadtime 是 number / integer
        lt_prof = next(c for c in prof["columns"] if c["name"] == "leadtime")
        assert lt_prof["physical_type"] in ("integer", "number")


class TestProfileDataset:
    def test_multi_table(self):
        df1 = pd.DataFrame({"a": [1, 2, 3]})
        df2 = pd.DataFrame({"b": ["x", "y"]})
        ds = profile_dataset([("t1", df1), ("t2", df2)])
        assert len(ds["tables"]) == 2
        assert ds["tables"][0]["table_id"] == "t1"
        assert ds["tables"][1]["table_id"] == "t2"


# ============================================================
# Threshold constants sanity
# ============================================================
def test_threshold_constants_reasonable():
    assert 0 < HIGH_NULL_THRESHOLD_PCT < 100
    assert 0 < HIGH_CARDINALITY_RATIO <= 1
    assert LOW_CARDINALITY_MAX >= 2
    assert SKEW_RATIO_THRESHOLD > 1
