"""tests/unit/test_multi_table_profiler.py — unit tests for multi_table_profiler.py (M1).

Each test maps to a specific spec requirement so a failure points to a real
broken capability rather than a cosmetic regression.
"""

from __future__ import annotations

import pandas as pd
import pytest

from multi_table_profiler import (
    BRIDGE_FK_COUNT_MIN,
    FACT_MEASURE_RATIO_MIN,
    PK_DISTINCT_PCT_MIN,
    count_duplicate_rows,
    describe_grain,
    detect_total_row,
    find_primary_key_candidates,
    infer_table_role,
    make_table_id,
    normalize_sheet_name,
    profile_multi_table,
)


# ============================================================
# Name normalization (spec §5.2 table_id / table_name shape)
# ============================================================
class TestNormalizeSheetName:
    def test_basic_lowercase(self):
        assert normalize_sheet_name("Employee") == "employee"

    def test_space_to_underscore(self):
        assert normalize_sheet_name("Employee List") == "employee_list"

    def test_special_chars_replaced(self):
        assert normalize_sheet_name("Q1-Sales (2024)") == "q1_sales_2024"

    def test_starts_with_digit_gets_prefix(self):
        # SQL identifiers can't start with a digit; prefix with sheet_.
        assert normalize_sheet_name("2024 Data") == "sheet_2024_data"

    def test_empty_string_fallback(self):
        assert normalize_sheet_name("") == "sheet"
        assert normalize_sheet_name("   ") == "sheet"

    def test_cjk_preserved(self):
        # CJK chars should NOT be stripped — Chinese sheet names are normal in
        # tFlex / Taiwan HR workbooks.
        result = normalize_sheet_name("員工 清單")
        assert "員工" in result
        assert "清單" in result

    def test_dedupe_with_taken_set(self):
        taken = {"employee"}
        assert normalize_sheet_name("Employee", taken=taken) == "employee_2"

    def test_dedupe_chain(self):
        taken = {"employee", "employee_2", "employee_3"}
        assert normalize_sheet_name("Employee", taken=taken) == "employee_4"


class TestMakeTableId:
    def test_prefix(self):
        # Spec §5.2 example: "table_id": "tbl_employee"
        assert make_table_id("employee") == "tbl_employee"


# ============================================================
# Primary key inference (spec §7.1 "possible primary key")
# ============================================================
class TestFindPrimaryKeyCandidates:
    def test_unique_non_null_col_is_pk(self):
        # All 5 rows distinct, no nulls → PK candidate.
        profile = {
            "columns": [
                {"name": "id", "distinct_count": 5, "null_pct": 0.0},
                {"name": "name", "distinct_count": 5, "null_pct": 0.0},
            ],
        }
        pks = find_primary_key_candidates(profile, row_count=5)
        # Both qualify by the rule; reviewer picks.
        assert "id" in pks
        assert "name" in pks

    def test_non_unique_col_not_pk(self):
        profile = {
            "columns": [
                {"name": "status", "distinct_count": 2, "null_pct": 0.0},
            ],
        }
        assert find_primary_key_candidates(profile, row_count=10) == []

    def test_null_col_not_pk(self):
        # distinct == row_count BUT 50% null → not a PK.
        profile = {
            "columns": [
                {"name": "maybe_id", "distinct_count": 5, "null_pct": 50.0},
            ],
        }
        assert find_primary_key_candidates(profile, row_count=5) == []

    def test_empty_table_no_pk(self):
        profile = {"columns": [{"name": "id", "distinct_count": 0, "null_pct": 0}]}
        assert find_primary_key_candidates(profile, row_count=0) == []

    def test_unhashable_col_skipped(self):
        # data_profiler marks unhashable cols with distinct_count=None.
        profile = {
            "columns": [
                {"name": "tags", "distinct_count": None, "null_pct": 0.0},
            ],
        }
        assert find_primary_key_candidates(profile, row_count=5) == []


# ============================================================
# Total row detection (spec §7.1 "possible total row")
# ============================================================
class TestDetectTotalRow:
    def test_english_total(self):
        df = pd.DataFrame({
            "region": ["TW", "JP", "TOTAL"],
            "sales": [100, 200, 300],
        })
        assert detect_total_row(df) is True

    def test_chinese_total(self):
        df = pd.DataFrame({
            "部門": ["A", "B", "合計"],
            "人數": [50, 30, 80],
        })
        assert detect_total_row(df) is True

    def test_subtotal(self):
        df = pd.DataFrame({
            "label": ["x", "y", "Subtotal Q1"],
            "v": [1, 2, 3],
        })
        assert detect_total_row(df) is True

    def test_no_total_row(self):
        df = pd.DataFrame({
            "region": ["TW", "JP", "US"],
            "sales": [100, 200, 300],
        })
        assert detect_total_row(df) is False

    def test_total_keyword_not_in_last_row(self):
        # 'TOTAL' is in row 0, last row is clean → False.
        df = pd.DataFrame({
            "region": ["TOTAL", "TW", "JP"],
            "sales": [300, 100, 200],
        })
        assert detect_total_row(df) is False

    def test_empty_dataframe(self):
        df = pd.DataFrame({"x": []})
        assert detect_total_row(df) is False

    def test_last_row_all_numeric_no_match(self):
        df = pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})
        assert detect_total_row(df) is False


# ============================================================
# Duplicate row counting (spec §7.1 "duplicate row warning")
# ============================================================
class TestCountDuplicateRows:
    def test_no_duplicates(self):
        df = pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})
        assert count_duplicate_rows(df) == 0

    def test_one_duplicate(self):
        df = pd.DataFrame({"a": [1, 2, 1], "b": [4, 5, 4]})
        assert count_duplicate_rows(df) == 1

    def test_multiple_duplicates(self):
        df = pd.DataFrame({"a": [1, 1, 1, 2], "b": [4, 4, 4, 5]})
        # Two later copies of (1,4) → 2 dupes counted, original kept.
        assert count_duplicate_rows(df) == 2

    def test_empty_df(self):
        assert count_duplicate_rows(pd.DataFrame({"x": []})) == 0


# ============================================================
# Table role inference (spec §5.2 table_role)
# ============================================================
class TestInferTableRole:
    def test_dimension_pk_no_measures(self):
        # PK + mostly categorical cols → dimension.
        profile = {
            "columns": [
                {"name": "employee_id", "physical_type": "string"},
                {"name": "name", "physical_type": "string"},
                {"name": "department", "physical_type": "string"},
            ],
        }
        role = infer_table_role(
            profile,
            pk_candidates=["employee_id"],
            all_table_pks={"employee": ["employee_id"]},
            table_name="employee",
        )
        assert role == "dimension"

    def test_fact_pk_plus_measures(self):
        # PK + 3 numeric measures (75% measure ratio) → fact.
        profile = {
            "columns": [
                {"name": "order_id", "physical_type": "string"},
                {"name": "revenue", "physical_type": "number"},
                {"name": "quantity", "physical_type": "integer"},
                {"name": "discount", "physical_type": "number"},
            ],
        }
        role = infer_table_role(
            profile,
            pk_candidates=["order_id"],
            all_table_pks={"orders": ["order_id"]},
            table_name="orders",
        )
        assert role == "fact"

    def test_bridge_two_fks(self):
        # No own PK but has 2 FK-like cols (matching other tables' PKs) → bridge.
        profile = {
            "columns": [
                {"name": "employee_id", "physical_type": "string"},
                {"name": "project_id", "physical_type": "string"},
                {"name": "role", "physical_type": "string"},
            ],
        }
        role = infer_table_role(
            profile,
            pk_candidates=[],
            all_table_pks={
                "employee_project": [],
                "employee": ["employee_id"],
                "project": ["project_id"],
            },
            table_name="employee_project",
        )
        assert role == "bridge"

    def test_unknown_no_pk(self):
        # No PK, no FK candidates → unknown.
        profile = {
            "columns": [
                {"name": "category", "physical_type": "string"},
                {"name": "note", "physical_type": "string"},
            ],
        }
        role = infer_table_role(
            profile,
            pk_candidates=[],
            all_table_pks={"misc": []},
            table_name="misc",
        )
        assert role == "unknown"

    def test_id_columns_not_counted_as_measures(self):
        # 3 of 4 columns look numeric, but 2 of those are *_id → not measures.
        # measure_count = 1 (revenue), len=4 → ratio 0.25 < FACT_MEASURE_RATIO_MIN
        # but has PK → falls to dimension.
        profile = {
            "columns": [
                {"name": "order_id", "physical_type": "integer"},
                {"name": "customer_id", "physical_type": "integer"},
                {"name": "product_id", "physical_type": "integer"},
                {"name": "revenue", "physical_type": "number"},
            ],
        }
        role = infer_table_role(
            profile,
            pk_candidates=["order_id"],
            all_table_pks={"orders": ["order_id"]},
            table_name="orders",
        )
        assert role == "dimension"


# ============================================================
# Grain description
# ============================================================
class TestDescribeGrain:
    def test_id_suffix_stripped(self):
        assert describe_grain(["employee_id"], "employee") == "one row per employee"

    def test_no_suffix_kept_as_is(self):
        assert describe_grain(["sku"], "products") == "one row per sku"

    def test_composite_key_uses_parens(self):
        assert (
            describe_grain(["customer_id", "product_id"], "purchases")
            == "one row per (customer_id, product_id)"
        )

    def test_empty_pk_returns_none(self):
        assert describe_grain([], "anything") is None

    def test_no_suffix_with_code(self):
        assert describe_grain(["product_code"], "products") == "one row per product"


# ============================================================
# End-to-end: profile_multi_table
# ============================================================
class TestProfileMultiTable:
    def test_empty_workbook(self):
        result = profile_multi_table({})
        assert result == {"tables": [], "n_sheets": 0, "total_rows": 0}

    def test_single_sheet_clear_pk_dimension(self):
        # Realistic shape: employee_id is unique, name has a duplicate
        # (Alice + Alice K. share a first name → not unique → not PK candidate),
        # department has many dupes.
        df = pd.DataFrame({
            "employee_id": ["E001", "E002", "E003", "E004", "E005"],
            "name": ["Alice", "Bob", "Alice", "Carol", "Bob"],
            "department": ["Eng", "Sales", "Eng", "Eng", "Sales"],
        })
        result = profile_multi_table({"Employee": df})
        assert result["n_sheets"] == 1
        assert result["total_rows"] == 5

        tbl = result["tables"][0]
        assert tbl["sheet_name"] == "Employee"
        assert tbl["table_name"] == "employee"
        assert tbl["table_id"] == "tbl_employee"
        assert tbl["row_count"] == 5
        assert tbl["column_count"] == 3
        assert tbl["possible_primary_key"] == ["employee_id"]
        assert tbl["table_role"] == "dimension"
        assert tbl["grain"] == "one row per employee"
        assert tbl["possible_total_row"] is False
        assert tbl["duplicate_row_count"] == 0
        assert "no_clear_pk" not in tbl["table_warnings"]

    def test_single_sheet_no_pk_unknown(self):
        # No column is unique enough to be PK → unknown role.
        df = pd.DataFrame({
            "status": ["active", "active", "inactive"],
            "category": ["A", "B", "A"],
        })
        result = profile_multi_table({"misc": df})
        tbl = result["tables"][0]
        assert tbl["possible_primary_key"] == []
        assert tbl["table_role"] == "unknown"
        assert tbl["grain"] is None
        assert "no_clear_pk" in tbl["table_warnings"]

    def test_fact_table_detection(self):
        # Realistic Orders shape: order_id is unique; customer_id, revenue,
        # quantity, discount all have duplicates (real fact tables do).
        # Result: order_id is the only PK candidate; the other 3 numeric
        # cols count as measures → ratio 3/5 = 0.6 ≥ 0.3 → fact.
        df = pd.DataFrame({
            "order_id":    ["O1", "O2", "O3", "O4", "O5", "O6", "O7", "O8"],
            "customer_id": ["C1", "C2", "C1", "C3", "C2", "C1", "C2", "C3"],
            "revenue":     [100.0, 250.0, 100.0, 80.0, 250.0, 100.0, 80.0, 250.0],
            "quantity":    [1, 2, 1, 1, 2, 1, 1, 2],
            "discount":    [0.0, 0.1, 0.0, 0.0, 0.1, 0.0, 0.0, 0.1],
        })
        result = profile_multi_table({"Orders": df})
        tbl = result["tables"][0]
        assert tbl["possible_primary_key"] == ["order_id"]
        assert tbl["table_role"] == "fact"

    def test_bridge_detection_across_tables(self):
        # Three sheets: employee (PK=employee_id), project (PK=project_id),
        # employee_project (has both FKs, no own PK) → bridge.
        emp = pd.DataFrame({
            "employee_id": ["E1", "E2", "E3"],
            "name": ["A", "B", "C"],
        })
        proj = pd.DataFrame({
            "project_id": ["P1", "P2"],
            "title": ["X", "Y"],
        })
        link = pd.DataFrame({
            "employee_id": ["E1", "E1", "E2", "E3"],
            "project_id": ["P1", "P2", "P1", "P2"],
            "role": ["lead", "dev", "dev", "qa"],
        })
        result = profile_multi_table({
            "Employee": emp,
            "Project": proj,
            "EmployeeProject": link,
        })
        roles = {t["table_name"]: t["table_role"] for t in result["tables"]}
        assert roles["employee"] == "dimension"
        assert roles["project"] == "dimension"
        assert roles["employeeproject"] == "bridge"

    def test_total_row_warning(self):
        df = pd.DataFrame({
            "region": ["TW", "JP", "TOTAL"],
            "sales": [100, 200, 300],
        })
        tbl = profile_multi_table({"Sales": df})["tables"][0]
        assert tbl["possible_total_row"] is True
        assert "has_total_row" in tbl["table_warnings"]

    def test_duplicate_rows_warning(self):
        df = pd.DataFrame({
            "a": [1, 1, 2, 2],
            "b": [10, 10, 20, 20],
        })
        tbl = profile_multi_table({"Dups": df})["tables"][0]
        assert tbl["duplicate_row_count"] == 2
        assert "has_duplicates" in tbl["table_warnings"]

    def test_empty_table_warning(self):
        # 0 rows but valid columns.
        df = pd.DataFrame({"a": pd.Series(dtype="int64"),
                           "b": pd.Series(dtype="object")})
        tbl = profile_multi_table({"Empty": df})["tables"][0]
        assert tbl["row_count"] == 0
        assert "empty_table" in tbl["table_warnings"]
        assert tbl["possible_primary_key"] == []

    def test_sheet_name_dedupe_in_workbook(self):
        # Two distinct sheet names that normalize to the same string get
        # disambiguated deterministically: "Data 1" and "Data-1" both
        # normalize to "data_1"; the second must become "data_1_2".
        df1 = pd.DataFrame({"id": [1, 2]})
        df2 = pd.DataFrame({"id": [3, 4]})
        result = profile_multi_table({"Data 1": df1, "Data-1": df2})
        names = [t["table_name"] for t in result["tables"]]
        assert names[0] == "data_1"
        assert names[1] == "data_1_2"

    def test_skips_none_sheet_gracefully(self):
        # If a sheet value is None (parser quirk) we log + skip, don't crash.
        df = pd.DataFrame({"x": [1, 2]})
        result = profile_multi_table({"Good": df, "Bad": None})
        assert result["n_sheets"] == 1
        assert result["tables"][0]["sheet_name"] == "Good"

    def test_columns_field_comes_from_data_profiler(self):
        # Verify we delegate column-level profile to data_profiler.profile_table
        # rather than reimplementing it. If data_profiler shape changes, this
        # test catches divergence.
        df = pd.DataFrame({
            "x": [1, 2, 3, 4, 5],
            "y": ["a", "b", "c", "d", "e"],
        })
        tbl = profile_multi_table({"S": df})["tables"][0]
        # data_profiler.profile_column outputs these keys for any column.
        for col in tbl["columns"]:
            assert "name" in col
            assert "physical_type" in col
            assert "null_count" in col
            assert "distinct_count" in col


# ============================================================
# Threshold constants — guard against accidental tuning that
# would change behavior silently across the M1-M7 milestones.
# ============================================================
class TestThresholdConstants:
    def test_pk_distinct_threshold_strict(self):
        # Anything below 0.99 risks calling almost-unique cols PKs.
        assert PK_DISTINCT_PCT_MIN >= 0.95

    def test_bridge_min_two_fks(self):
        # Spec §8 bridge concept needs >=2 FK-like cols by definition.
        assert BRIDGE_FK_COUNT_MIN >= 2

    def test_fact_measure_ratio_reasonable(self):
        # Below 0.2 would flag every dim table as a fact.
        assert 0.2 <= FACT_MEASURE_RATIO_MIN <= 0.5
