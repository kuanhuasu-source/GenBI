"""tests/unit/test_analysis_step_service.py — v0.18 M5

Two layers of coverage:
  1. Pure action handlers (_handle_*) — no DB / no I/O, fast.
  2. AnalysisStepService end-to-end — requires mongomock fixture.

Each test maps to a specific spec capability so a failure points at
the broken behavior rather than a cosmetic regression.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from analysis_step_service import (
    _DERIVED_TABLE_ROW_LIMIT,
    _VALID_ACTION_TYPES,
    _VALID_AGGREGATIONS,
    AnalysisStepService,
    _handle_add_column,
    _handle_aggregate,
    _handle_create_table,
    _handle_extract_data,
    _handle_visualize,
    generate_session_id,
    generate_step_id,
)


# ============================================================
# Id generators
# ============================================================
class TestIdGenerators:
    def test_step_id_format(self):
        sid = generate_step_id()
        assert sid.startswith("step_")
        parts = sid.split("_")
        assert len(parts) == 3
        assert len(parts[1]) == 14   # 14-char timestamp
        assert len(parts[2]) == 6    # 6 hex chars

    def test_session_id_format(self):
        sid = generate_session_id()
        assert sid.startswith("sess_")

    def test_ids_unique(self):
        # secrets.token_hex(3) → 16M combinations; 50 ids must all differ.
        ids = {generate_step_id() for _ in range(50)}
        assert len(ids) == 50


# ============================================================
# Constant invariants
# ============================================================
class TestConstants:
    def test_action_types_match_spec(self):
        # Spec §10 action types. `inspect_table` is a UI-only read
        # action and doesn't produce a step; the rest are persistent.
        expected = {"extract_data", "add_column", "aggregate",
                    "create_table", "visualize", "insight"}
        assert _VALID_ACTION_TYPES == expected

    def test_aggregations_include_basics(self):
        # Spec §10 doesn't enumerate, but the safe-list must include
        # sum/mean/min/max/count at minimum.
        for required in ("sum", "mean", "min", "max", "count"):
            assert required in _VALID_AGGREGATIONS


# ============================================================
# extract_data handler
# ============================================================
class TestExtractData:
    def test_no_filter_returns_copy(self):
        df = pd.DataFrame({"x": [1, 2, 3]})
        out = _handle_extract_data(df, {})
        assert len(out) == 3
        # Must be a copy — mutating output shouldn't change input.
        out.loc[0, "x"] = 99
        assert df.loc[0, "x"] == 1

    def test_eq_filter(self):
        df = pd.DataFrame({"region": ["TW", "JP", "TW"], "v": [1, 2, 3]})
        out = _handle_extract_data(df, {
            "filters": [{"column": "region", "op": "==", "value": "TW"}],
        })
        assert list(out["v"]) == [1, 3]

    def test_in_filter(self):
        df = pd.DataFrame({"k": ["a", "b", "c", "d"]})
        out = _handle_extract_data(df, {
            "filters": [{"column": "k", "op": "in", "value": ["a", "c"]}],
        })
        assert list(out["k"]) == ["a", "c"]

    def test_multiple_filters_and(self):
        # Filters AND-combine.
        df = pd.DataFrame({
            "r": ["TW", "JP", "TW", "TW"],
            "v": [10, 20, 30, 40],
        })
        out = _handle_extract_data(df, {
            "filters": [
                {"column": "r", "op": "==", "value": "TW"},
                {"column": "v", "op": ">", "value": 15},
            ],
        })
        assert list(out["v"]) == [30, 40]

    def test_notnull_filter(self):
        df = pd.DataFrame({"x": [1, None, 3]})
        out = _handle_extract_data(df, {
            "filters": [{"column": "x", "op": "notnull"}],
        })
        assert list(out["x"]) == [1, 3]

    def test_unknown_op_raises(self):
        df = pd.DataFrame({"x": [1]})
        with pytest.raises(ValueError, match="unknown filter op"):
            _handle_extract_data(df, {
                "filters": [{"column": "x", "op": "regex", "value": "."}],
            })

    def test_missing_column_raises(self):
        df = pd.DataFrame({"x": [1]})
        with pytest.raises(ValueError, match="not in input"):
            _handle_extract_data(df, {
                "filters": [{"column": "y", "op": "==", "value": 1}],
            })


# ============================================================
# add_column handler
# ============================================================
class TestAddColumn:
    def test_simple_arithmetic(self):
        df = pd.DataFrame({"a": [1, 2, 3], "b": [10, 20, 30]})
        out = _handle_add_column(df, {
            "new_column": "c", "formula": "a + b",
        })
        assert list(out["c"]) == [11, 22, 33]

    def test_multiplication(self):
        df = pd.DataFrame({"salary": [1000, 2000]})
        out = _handle_add_column(df, {
            "new_column": "annual", "formula": "salary * 12",
        })
        assert list(out["annual"]) == [12000, 24000]

    def test_ratio(self):
        df = pd.DataFrame({"overtime": [10, 20], "regular": [40, 40]})
        out = _handle_add_column(df, {
            "new_column": "ot_ratio",
            "formula": "overtime / regular",
        })
        assert out["ot_ratio"].iloc[0] == 0.25
        assert out["ot_ratio"].iloc[1] == 0.5

    def test_input_not_mutated(self):
        df = pd.DataFrame({"a": [1, 2]})
        _handle_add_column(df, {"new_column": "b", "formula": "a + 1"})
        assert "b" not in df.columns

    def test_duplicate_column_raises(self):
        df = pd.DataFrame({"a": [1], "b": [2]})
        with pytest.raises(ValueError, match="already exists"):
            _handle_add_column(df, {"new_column": "a", "formula": "b + 1"})

    def test_missing_formula_raises(self):
        df = pd.DataFrame({"a": [1]})
        with pytest.raises(ValueError, match="formula"):
            _handle_add_column(df, {"new_column": "x"})

    def test_invalid_formula_raises(self):
        df = pd.DataFrame({"a": [1]})
        # Reference to non-existent column → df.eval raises → wrapped.
        with pytest.raises(ValueError, match="pandas eval failed"):
            _handle_add_column(df, {"new_column": "x", "formula": "nonexistent + 1"})

    def test_dangerous_formula_blocked(self):
        # df.eval('python' engine) doesn't allow imports / arbitrary
        # function calls — verify that __import__ / open / etc. error
        # out cleanly (don't execute).
        df = pd.DataFrame({"a": [1]})
        # These all reference unknown names → ValueError.
        for bad in ("__import__('os').system('ls')",
                     "open('/etc/passwd')"):
            with pytest.raises(ValueError):
                _handle_add_column(df, {"new_column": "x", "formula": bad})


# ============================================================
# aggregate handler
# ============================================================
class TestAggregate:
    def test_groupby_sum(self):
        df = pd.DataFrame({
            "dept": ["Eng", "Sales", "Eng", "Sales", "Eng"],
            "sales": [100, 200, 150, 300, 50],
        })
        out = _handle_aggregate(df, {
            "group_by": ["dept"],
            "aggregations": [
                {"column": "sales", "function": "sum", "alias": "total_sales"},
            ],
        })
        assert set(out.columns) == {"dept", "total_sales"}
        eng = out[out["dept"] == "Eng"]
        assert eng["total_sales"].iloc[0] == 300

    def test_multi_agg(self):
        df = pd.DataFrame({
            "cat": ["a", "a", "b"],
            "v": [10, 20, 30],
        })
        out = _handle_aggregate(df, {
            "group_by": ["cat"],
            "aggregations": [
                {"column": "v", "function": "sum"},
                {"column": "v", "function": "count", "alias": "n_rows"},
            ],
        })
        # Default alias for first agg is "sum_v"; for second "n_rows"
        # (because alias was provided).
        assert "sum_v" in out.columns
        assert "n_rows" in out.columns

    def test_no_groupby_returns_one_row(self):
        df = pd.DataFrame({"v": [1, 2, 3, 4]})
        out = _handle_aggregate(df, {
            "group_by": [],
            "aggregations": [
                {"column": "v", "function": "sum", "alias": "total"},
            ],
        })
        assert len(out) == 1
        assert out["total"].iloc[0] == 10

    def test_count_distinct(self):
        df = pd.DataFrame({
            "cat": ["a", "a", "b", "b", "b"],
            "user": ["u1", "u2", "u1", "u2", "u3"],
        })
        out = _handle_aggregate(df, {
            "group_by": ["cat"],
            "aggregations": [
                {"column": "user", "function": "count_distinct",
                 "alias": "n_users"},
            ],
        })
        a_row = out[out["cat"] == "a"]
        b_row = out[out["cat"] == "b"]
        assert a_row["n_users"].iloc[0] == 2
        assert b_row["n_users"].iloc[0] == 3

    def test_unknown_function_raises(self):
        df = pd.DataFrame({"x": [1]})
        with pytest.raises(ValueError, match="not allowed"):
            _handle_aggregate(df, {
                "group_by": [],
                "aggregations": [{"column": "x", "function": "rm -rf"}],
            })

    def test_missing_aggregations_raises(self):
        df = pd.DataFrame({"x": [1]})
        with pytest.raises(ValueError, match="at least one aggregation"):
            _handle_aggregate(df, {"group_by": []})

    def test_unknown_groupby_column(self):
        df = pd.DataFrame({"x": [1]})
        with pytest.raises(ValueError, match="group_by"):
            _handle_aggregate(df, {
                "group_by": ["nonexistent"],
                "aggregations": [{"column": "x", "function": "sum"}],
            })


# ============================================================
# create_table + visualize handlers (lightweight)
# ============================================================
class TestCreateTable:
    def test_passthrough(self):
        df = pd.DataFrame({"x": [1, 2]})
        out = _handle_create_table(df, {"new_name": "my_table"})
        assert list(out["x"]) == [1, 2]
        # It's a copy.
        out.loc[0, "x"] = 99
        assert df.loc[0, "x"] == 1

    def test_missing_new_name_raises(self):
        with pytest.raises(ValueError, match="new_name"):
            _handle_create_table(pd.DataFrame({"x": [1]}), {})


class TestVisualize:
    def test_passthrough_with_valid_axes(self):
        df = pd.DataFrame({"month": ["Jan", "Feb"], "sales": [100, 200]})
        out = _handle_visualize(df, {
            "chart_type": "bar", "x": "month", "y": "sales",
        })
        assert len(out) == 2

    def test_y_list(self):
        df = pd.DataFrame({"m": [1, 2], "a": [10, 20], "b": [5, 15]})
        out = _handle_visualize(df, {
            "chart_type": "line", "x": "m", "y": ["a", "b"],
        })
        assert len(out) == 2

    def test_missing_chart_type_raises(self):
        with pytest.raises(ValueError, match="chart_type"):
            _handle_visualize(pd.DataFrame({"x": [1]}), {})

    def test_unknown_x_column(self):
        with pytest.raises(ValueError, match="not in input"):
            _handle_visualize(pd.DataFrame({"x": [1]}), {
                "chart_type": "bar", "x": "nonexistent",
            })


# ============================================================
# Service end-to-end (requires mongomock)
# ============================================================
@pytest.mark.requires_mongo
class TestServiceEndToEnd:
    def _make_service(self, mongo_db, tmp_path):
        from upload_repository import UploadRepository

        repo = UploadRepository(mongo_db)
        repo.ensure_indexes()
        # Seed a dataset + a source table backed by a real parquet
        # file (the service loads parquets via file_parser).
        repo.create_dataset({
            "_id": "ds-1", "dataset_name": "x", "owner": "a",
            "source_type": "file_upload", "file": {},
            "status": "profiled", "active_metadata_version": 1,
        })
        df = pd.DataFrame({
            "dept": ["Eng", "Sales", "Eng"],
            "salary": [1000, 2000, 1500],
            "years": [3, 5, 1],
        })
        parquet_path = tmp_path / "ds-1" / "employee.parquet"
        parquet_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(parquet_path, index=False)
        repo.create_table({
            "dataset_id": "ds-1", "table_id": "employee",
            "table_name": "Employee", "row_count": 3, "column_count": 3,
            "storage": {"format": "parquet", "path": str(parquet_path)},
        })
        return AnalysisStepService(
            upload_repo=repo, uploads_root=tmp_path,
        ), repo

    def test_create_session_records_dataset_id(self, mongo_db, tmp_path):
        svc, repo = self._make_service(mongo_db, tmp_path)
        session_id = svc.create_session("ds-1", owner="alice")
        session = repo.get_session(session_id)
        assert session["dataset_id"] == "ds-1"
        assert session["owner"] == "alice"

    def test_create_session_unknown_dataset_raises(self, mongo_db, tmp_path):
        svc, _ = self._make_service(mongo_db, tmp_path)
        with pytest.raises(ValueError, match="not found"):
            svc.create_session("nonexistent")

    def test_extract_then_add_column_chains(self, mongo_db, tmp_path):
        # Build a 2-step chain: extract Eng dept → add bonus column.
        # Verify step 2's input resolves to step 1's output.
        svc, repo = self._make_service(mongo_db, tmp_path)
        session_id = svc.create_session("ds-1")
        s1 = svc.add_step(
            session_id, action_type="extract_data",
            params={
                "input_table": "employee",
                "filters": [{"column": "dept", "op": "==",
                              "value": "Eng"}],
            },
        )
        assert s1["status"] == "completed"
        assert s1["row_count"] == 2  # 2 Eng rows
        s2 = svc.add_step(
            session_id, action_type="add_column",
            params={
                "input_table": s1["output_table"],
                "new_column": "bonus",
                "formula": "salary * 0.1",
            },
        )
        assert s2["status"] == "completed"
        df = svc.resolve_table(session_id, s2["output_table"])
        assert "bonus" in df.columns
        assert df["bonus"].iloc[0] == 100.0

    def test_aggregate_creates_derived_table_with_row_count(
        self, mongo_db, tmp_path,
    ):
        svc, _ = self._make_service(mongo_db, tmp_path)
        session_id = svc.create_session("ds-1")
        s1 = svc.add_step(
            session_id, action_type="aggregate",
            params={
                "input_table": "employee",
                "group_by": ["dept"],
                "aggregations": [
                    {"column": "salary", "function": "sum",
                     "alias": "total"},
                ],
            },
        )
        assert s1["status"] == "completed"
        # 2 distinct depts (Eng + Sales) → 2 rows
        assert s1["row_count"] == 2
        # Schema captured
        col_names = {c["name"] for c in s1["output_schema"]}
        assert col_names == {"dept", "total"}

    def test_create_table_uses_new_name_as_output(
        self, mongo_db, tmp_path,
    ):
        svc, _ = self._make_service(mongo_db, tmp_path)
        session_id = svc.create_session("ds-1")
        s1 = svc.add_step(
            session_id, action_type="create_table",
            params={"input_table": "employee", "new_name": "emp_renamed"},
        )
        # Output_table uses user-provided new_name, not step_id, so
        # later steps can reference it mnemonically.
        assert s1["output_table"] == "emp_renamed"
        df = svc.resolve_table(session_id, "emp_renamed")
        assert len(df) == 3

    def test_visualize_persists_chart_spec_without_parquet(
        self, mongo_db, tmp_path,
    ):
        svc, _ = self._make_service(mongo_db, tmp_path)
        session_id = svc.create_session("ds-1")
        s1 = svc.add_step(
            session_id, action_type="visualize",
            params={
                "input_table": "employee",
                "chart_type": "bar",
                "x": "dept", "y": "salary",
            },
        )
        assert s1["status"] == "completed"
        assert s1["chart_spec"]["chart_type"] == "bar"
        assert s1["chart_spec"]["x"] == "dept"
        # No parquet written for visualize.
        assert "storage" not in s1

    def test_failed_step_records_error_message(self, mongo_db, tmp_path):
        # add_column with invalid formula → step status='failed',
        # error_message populated, but the chain is not corrupted (we
        # can still add later steps starting from source).
        svc, _ = self._make_service(mongo_db, tmp_path)
        session_id = svc.create_session("ds-1")
        s1 = svc.add_step(
            session_id, action_type="add_column",
            params={
                "input_table": "employee",
                "new_column": "bogus",
                "formula": "this_col_does_not_exist + 1",
            },
        )
        assert s1["status"] == "failed"
        assert "error_message" in s1
        # Subsequent step starting from source still works.
        s2 = svc.add_step(
            session_id, action_type="extract_data",
            params={"input_table": "employee"},
        )
        assert s2["status"] == "completed"

    def test_resolve_table_finds_source_by_table_id(
        self, mongo_db, tmp_path,
    ):
        svc, _ = self._make_service(mongo_db, tmp_path)
        session_id = svc.create_session("ds-1")
        df = svc.resolve_table(session_id, "employee")
        assert len(df) == 3

    def test_resolve_table_finds_source_by_table_name(
        self, mongo_db, tmp_path,
    ):
        # `Employee` (original sheet name) should also resolve.
        svc, _ = self._make_service(mongo_db, tmp_path)
        session_id = svc.create_session("ds-1")
        df = svc.resolve_table(session_id, "Employee")
        assert len(df) == 3

    def test_resolve_table_unknown_raises(self, mongo_db, tmp_path):
        svc, _ = self._make_service(mongo_db, tmp_path)
        session_id = svc.create_session("ds-1")
        with pytest.raises(ValueError, match="not found"):
            svc.resolve_table(session_id, "nonexistent_table")

    def test_step_numbers_increment(self, mongo_db, tmp_path):
        svc, repo = self._make_service(mongo_db, tmp_path)
        session_id = svc.create_session("ds-1")
        s1 = svc.add_step(session_id, "extract_data",
                           {"input_table": "employee"})
        s2 = svc.add_step(session_id, "extract_data",
                           {"input_table": "employee"})
        s3 = svc.add_step(session_id, "extract_data",
                           {"input_table": "employee"})
        assert (s1["step_no"], s2["step_no"], s3["step_no"]) == (1, 2, 3)
        history = repo.list_analysis_steps(session_id)
        assert [s["step_no"] for s in history] == [1, 2, 3]

    def test_invalid_action_type_raises(self, mongo_db, tmp_path):
        svc, _ = self._make_service(mongo_db, tmp_path)
        session_id = svc.create_session("ds-1")
        with pytest.raises(ValueError, match="action_type"):
            svc.add_step(session_id, "delete_table",
                         {"input_table": "employee"})

    def test_missing_input_table_raises(self, mongo_db, tmp_path):
        svc, _ = self._make_service(mongo_db, tmp_path)
        session_id = svc.create_session("ds-1")
        with pytest.raises(ValueError, match="input_table"):
            svc.add_step(session_id, "extract_data", {})
