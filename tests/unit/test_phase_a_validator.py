"""tests/unit/test_phase_a_validator.py — unit tests for phase_a_validator.py (M4a)."""

from __future__ import annotations

import pandas as pd
import pytest

from phase_a_validator import (
    PANDAS_FILTER_ANTIPATTERN_CHEATSHEET,
    format_issues_as_retry_hint,
    validate_phase_a_output,
)


# ============================================================
# Helper
# ============================================================
def _exec_phase_a(code: str, source_df: pd.DataFrame) -> dict:
    """模擬 upload_analysis_service 對 Phase A code 跑 exec,回 namespace。"""
    import numpy as np
    ns = {"pd": pd, "np": np, "source_df": source_df}
    try:
        exec(code, ns, ns)
    except Exception:
        pass
    return ns


# ============================================================
# OK case
# ============================================================
class TestValidPhaseACode:
    def test_simple_filter(self):
        source = pd.DataFrame({
            "region": ["TW", "US", "TW", "JP"],
            "amount": [100, 200, 300, 400],
        })
        code = "raw_df = source_df[source_df['region'] == 'TW']"
        ns = _exec_phase_a(code, source)
        issues = validate_phase_a_output(code, ns, source_columns=list(source.columns))
        assert issues == [], f"unexpected issues: {issues}"

    def test_column_subset(self):
        source = pd.DataFrame({"a": [1, 2], "b": [3, 4], "c": [5, 6]})
        code = "raw_df = source_df[['a', 'b']]"
        ns = _exec_phase_a(code, source)
        issues = validate_phase_a_output(code, ns, source_columns=["a", "b", "c"])
        assert issues == []

    def test_copy(self):
        source = pd.DataFrame({"a": [1, 2]})
        code = "raw_df = source_df.copy()"
        ns = _exec_phase_a(code, source)
        issues = validate_phase_a_output(code, ns, source_columns=["a"])
        assert issues == []


# ============================================================
# Forbidden import
# ============================================================
class TestForbiddenImport:
    def test_import_module(self):
        source = pd.DataFrame({"a": [1]})
        code = "import os\nraw_df = source_df.copy()"
        ns = _exec_phase_a(code, source)
        issues = validate_phase_a_output(code, ns, source_columns=["a"])
        assert any("FORBIDDEN_IMPORT" in i for i in issues)

    def test_from_import(self):
        source = pd.DataFrame({"a": [1]})
        code = "from os import path\nraw_df = source_df.copy()"
        ns = _exec_phase_a(code, source)
        issues = validate_phase_a_output(code, ns, source_columns=["a"])
        assert any("FORBIDDEN_IMPORT" in i for i in issues)

    def test_dunder_import(self):
        source = pd.DataFrame({"a": [1]})
        code = "x = __import__('os')\nraw_df = source_df.copy()"
        ns = _exec_phase_a(code, source)
        issues = validate_phase_a_output(code, ns, source_columns=["a"])
        assert any("FORBIDDEN_IMPORT" in i for i in issues)


# ============================================================
# Forbidden IO
# ============================================================
class TestForbiddenIO:
    @pytest.mark.parametrize("forbidden", [
        "open(", "read_csv(", "read_excel(", "read_parquet(",
        "to_csv(", "os.", "subprocess.", "requests.",
        "eval(", "exec(",
    ])
    def test_forbidden_token(self, forbidden):
        source = pd.DataFrame({"a": [1]})
        code = f"x = pd.{forbidden}'foo')\nraw_df = source_df.copy()"
        ns = _exec_phase_a(code, source)
        issues = validate_phase_a_output(code, ns, source_columns=["a"])
        assert any("FORBIDDEN_IO" in i for i in issues), \
            f"didn't catch `{forbidden}`"


# ============================================================
# Hallucinated columns
# ============================================================
class TestHallucinatedColumns:
    def test_unknown_column(self):
        source = pd.DataFrame({"a": [1], "b": [2]})
        code = "raw_df = source_df[source_df['fake_col'] == 'x']"
        ns = _exec_phase_a(code, source)
        issues = validate_phase_a_output(code, ns, source_columns=["a", "b"])
        assert any("HALLUCINATED_COLUMN" in i for i in issues)

    def test_unicode_column_passes(self):
        # 中文欄名也該認得
        source = pd.DataFrame({"類別": ["a", "b"]})
        code = "raw_df = source_df[source_df['類別'] == 'a']"
        ns = _exec_phase_a(code, source)
        issues = validate_phase_a_output(code, ns, source_columns=["類別"])
        assert not any("HALLUCINATED" in i for i in issues)


# ============================================================
# Derived columns / aggregation 禁止
# ============================================================
class TestForbiddenDerived:
    def test_new_col_assignment(self):
        source = pd.DataFrame({"a": [1, 2]})
        code = (
            "raw_df = source_df.copy()\n"
            "raw_df['new_col'] = raw_df['a'] * 2"
        )
        ns = _exec_phase_a(code, source)
        issues = validate_phase_a_output(code, ns, source_columns=["a"])
        assert any("DERIVED_NEW_COLUMN" in i for i in issues)

    def test_groupby_forbidden(self):
        source = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
        code = "raw_df = source_df.groupby('b').sum()"
        ns = _exec_phase_a(code, source)
        issues = validate_phase_a_output(code, ns, source_columns=["a", "b"])
        assert any("DERIVED_NEW_COLUMN" in i for i in issues)

    def test_assign_forbidden(self):
        source = pd.DataFrame({"a": [1, 2]})
        code = "raw_df = source_df.assign(b=source_df['a'] * 2)"
        ns = _exec_phase_a(code, source)
        issues = validate_phase_a_output(code, ns, source_columns=["a"])
        assert any("DERIVED_NEW_COLUMN" in i for i in issues)


# ============================================================
# raw_df 存在性
# ============================================================
class TestRawDfExists:
    def test_missing_raw_df(self):
        source = pd.DataFrame({"a": [1]})
        code = "filtered = source_df[source_df['a'] > 0]"   # 沒寫 raw_df
        ns = _exec_phase_a(code, source)
        issues = validate_phase_a_output(code, ns, source_columns=["a"])
        assert any("NO_RAW_DF" in i for i in issues)

    def test_raw_df_wrong_type(self):
        source = pd.DataFrame({"a": [1]})
        code = "raw_df = 'this is not a dataframe'"
        ns = _exec_phase_a(code, source)
        issues = validate_phase_a_output(code, ns, source_columns=["a"])
        assert any("NO_RAW_DF" in i for i in issues)

    def test_empty_raw_df(self):
        source = pd.DataFrame({"a": [1, 2]})
        code = "raw_df = source_df[source_df['a'] > 999]"  # filter 全砍
        ns = _exec_phase_a(code, source)
        issues = validate_phase_a_output(code, ns, source_columns=["a"])
        assert any("NO_RAW_DF" in i for i in issues)


# ============================================================
# Retry hint formatter
# ============================================================
class TestRetryHint:
    def test_empty_no_hint(self):
        assert format_issues_as_retry_hint([]) == ""

    def test_hint_includes_issues(self):
        hint = format_issues_as_retry_hint([
            "[A_FORBIDDEN_IMPORT] foo",
            "[A_NO_RAW_DF] bar",
        ])
        assert "A_FORBIDDEN_IMPORT" in hint
        assert "A_NO_RAW_DF" in hint
        assert "Phase A" in hint


# ============================================================
# Cheatsheet 常量
# ============================================================
def test_cheatsheet_contains_key_rules():
    assert "import" in PANDAS_FILTER_ANTIPATTERN_CHEATSHEET
    assert "raw_df" in PANDAS_FILTER_ANTIPATTERN_CHEATSHEET
    assert "groupby" in PANDAS_FILTER_ANTIPATTERN_CHEATSHEET
