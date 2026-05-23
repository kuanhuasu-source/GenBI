"""tests/unit/test_semantic_profiler.py — unit tests for semantic_profiler.py (M4a)."""

from __future__ import annotations

import pandas as pd
import pytest

from data_profiler import profile_column, profile_table
from semantic_profiler import (
    ROLE_PROPERTIES,
    SEMANTIC_ROLES,
    infer_role_rule_based,
    profile_columns_semantic,
)


# ============================================================
# Helper:profile_table 一次跑完
# ============================================================
def _profile(df: pd.DataFrame) -> list[dict]:
    """從 df 跑 physical profile,回 list of column dicts。"""
    return profile_table(df, "t")["columns"]


# ============================================================
# Identifier 偵測
# ============================================================
class TestIdentifierInference:
    def test_string_id_with_suspect_id_warning(self):
        df = pd.DataFrame({
            "project_id": [f"PRJ-{i:04d}" for i in range(100)],
        })
        profs = _profile(df)
        result = infer_role_rule_based(profs[0])
        assert result["role"] == "identifier"
        assert result["confidence"] >= 0.85

    def test_id_name_without_suspect_warning(self):
        # Low cardinality + id 名字 → 也該推 identifier 但 confidence 低
        df = pd.DataFrame({"category_code": ["A", "B", "A", "B", "C"] * 20})
        profs = _profile(df)
        result = infer_role_rule_based(profs[0])
        # category_code 帶 _code,可能被推 identifier 或 categorical_status
        # 兩個都合理(low cardinality 的 _code 比較像 status code)
        assert result["role"] in ("identifier", "categorical_status", "dimension")


# ============================================================
# Categorical status 偵測
# ============================================================
class TestCategoricalStatus:
    def test_low_cardinality_string(self):
        df = pd.DataFrame({"status": ["Completed", "InProgress"] * 50})
        profs = _profile(df)
        result = infer_role_rule_based(profs[0])
        assert result["role"] == "categorical_status"


# ============================================================
# Measure 系列偵測
# ============================================================
class TestMeasureInference:
    def test_amount_by_name(self):
        df = pd.DataFrame({"amount": [100.5, 200, 300.75, 50]})
        profs = _profile(df)
        result = infer_role_rule_based(profs[0])
        assert result["role"] == "measure_amount"
        assert result["confidence"] >= 0.85

    def test_count_by_name(self):
        df = pd.DataFrame({"order_count": [1, 2, 3, 5, 8]})
        profs = _profile(df)
        result = infer_role_rule_based(profs[0])
        assert result["role"] == "measure_count"

    def test_duration_by_name_with_days_unit(self):
        df = pd.DataFrame({"leadtime_days": [10, 20, 30, 40]})
        profs = _profile(df)
        result = infer_role_rule_based(profs[0])
        assert result["role"] == "measure_duration"
        assert result["unit"] == "days"

    def test_percentage_ratio_range(self):
        # values 在 0-1 → ratio
        df = pd.DataFrame({"success_rate": [0.5, 0.7, 0.9, 0.6]})
        profs = _profile(df)
        result = infer_role_rule_based(profs[0])
        assert result["role"] == "measure_percentage"
        assert result["unit"] == "ratio"

    def test_percentage_percent_range(self):
        # values 在 0-100 → percent
        df = pd.DataFrame({"completion_pct": [50, 75, 90, 60]})
        profs = _profile(df)
        result = infer_role_rule_based(profs[0])
        assert result["role"] == "measure_percentage"
        assert result["unit"] == "percent"


# ============================================================
# Boolean flag 偵測
# ============================================================
class TestBooleanFlag:
    def test_native_boolean(self):
        df = pd.DataFrame({"is_active": [True, False, True]})
        profs = _profile(df)
        result = infer_role_rule_based(profs[0])
        assert result["role"] == "boolean_flag"

    def test_yn_string(self):
        df = pd.DataFrame({"flag": ["Y", "N", "Y", "N"] * 10})
        profs = _profile(df)
        result = infer_role_rule_based(profs[0])
        # Y/N 應該被偵測為 boolean_flag
        assert result["role"] == "boolean_flag"


# ============================================================
# Datetime 偵測
# ============================================================
class TestDateDimension:
    def test_datetime_dtype(self):
        df = pd.DataFrame({"order_date": pd.to_datetime(["2025-01-01", "2025-02-01"])})
        profs = _profile(df)
        result = infer_role_rule_based(profs[0])
        assert result["role"] in ("date_dimension", "datetime_dimension")
        assert result["confidence"] >= 0.85

    def test_iso_string_date(self):
        df = pd.DataFrame({"hire_date": ["2025-01-01", "2025-02-15", "2025-03-20"]})
        profs = _profile(df)
        result = infer_role_rule_based(profs[0])
        assert result["role"] == "date_dimension"


# ============================================================
# Text description / dimension fallback
# ============================================================
class TestDimensionAndText:
    def test_description_by_name(self):
        df = pd.DataFrame({
            "description": [f"Long description text {i}" for i in range(20)],
        })
        profs = _profile(df)
        result = infer_role_rule_based(profs[0])
        # high_cardinality + name 含 description → text_description 或 text fallback
        assert result["role"] == "text_description"

    def test_medium_cardinality_dimension(self):
        df = pd.DataFrame({"category": ["Web", "Mobile", "Infra", "Backend", "Web", "Mobile"] * 10})
        profs = _profile(df)
        result = infer_role_rule_based(profs[0])
        # 中等基數 string 應該推為 dimension
        assert result["role"] == "dimension"


# ============================================================
# ROLE_PROPERTIES sanity
# ============================================================
class TestRoleProperties:
    def test_all_roles_have_properties(self):
        for role in SEMANTIC_ROLES:
            assert role in ROLE_PROPERTIES, f"role {role} missing in ROLE_PROPERTIES"
            props = ROLE_PROPERTIES[role]
            for key in ("default_aggregation", "recommended_use",
                          "not_recommended_use", "is_dimension",
                          "is_measure", "is_identifier"):
                assert key in props, f"role {role} missing key {key}"

    def test_identifier_not_sum(self):
        """spec §10.7 #7:identifier 不得被 sum / avg"""
        props = ROLE_PROPERTIES["identifier"]
        assert "sum" in props["not_recommended_use"]
        assert "average" in props["not_recommended_use"]
        assert props["is_identifier"] is True
        assert props["is_measure"] is False

    def test_percentage_not_sum(self):
        """百分比不可 sum"""
        props = ROLE_PROPERTIES["measure_percentage"]
        assert "sum" in props["not_recommended_use"]


# ============================================================
# profile_columns_semantic 整合
# ============================================================
class TestProfileColumnsSemantic:
    def test_rule_based_only(self, golden_data_dir):
        df = pd.read_csv(golden_data_dir / "projects_clean.csv")
        col_profs = profile_table(df, "t")["columns"]
        results = profile_columns_semantic(col_profs, use_llm=False)
        assert len(results) == len(col_profs)
        # project_id 應被推為 identifier
        pid_idx = next(i for i, c in enumerate(col_profs) if c["name"] == "project_id")
        assert results[pid_idx]["role"] == "identifier"
        # leadtime_days 沒有(只有 leadtime),仍應為 measure_count 或 measure_duration
        lt_idx = next(i for i, c in enumerate(col_profs) if c["name"] == "leadtime")
        assert results[lt_idx]["role"].startswith("measure_")

    def test_sales_amount(self, golden_data_dir):
        df = pd.read_csv(golden_data_dir / "sales_amount.csv")
        col_profs = profile_table(df, "t")["columns"]
        results = profile_columns_semantic(col_profs, use_llm=False)
        # amount 應該被推 measure_amount
        amt_idx = next(i for i, c in enumerate(col_profs) if c["name"] == "amount")
        assert results[amt_idx]["role"] == "measure_amount"
        # order_id 應該 identifier
        oid_idx = next(i for i, c in enumerate(col_profs) if c["name"] == "order_id")
        assert results[oid_idx]["role"] == "identifier"
