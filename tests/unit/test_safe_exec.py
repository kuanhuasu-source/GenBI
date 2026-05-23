"""tests/unit/test_safe_exec.py — unit tests for safe_exec.py (M4b)."""

from __future__ import annotations

import pandas as pd
import pytest

from safe_exec import (
    SafeExecResult,
    check_dataframe_limits,
    safe_exec_pandas,
    _SAFE_BUILTINS,
)


# ============================================================
# Happy path
# ============================================================
class TestSuccessfulExec:
    def test_simple_filter(self):
        source = pd.DataFrame({"x": [1, 2, 3, 4, 5]})
        result = safe_exec_pandas(
            code="raw_df = source_df[source_df['x'] > 2]",
            inputs={"source_df": source},
            expected_output_var="raw_df",
        )
        assert result.success is True
        assert len(result.output) == 3
        assert result.error is None

    def test_column_subset(self):
        source = pd.DataFrame({"a": [1, 2], "b": [3, 4], "c": [5, 6]})
        result = safe_exec_pandas(
            code="raw_df = source_df[['a', 'b']]",
            inputs={"source_df": source},
            expected_output_var="raw_df",
        )
        assert result.success
        assert list(result.output.columns) == ["a", "b"]

    def test_pandas_groupby_works(self):
        source = pd.DataFrame({"cat": ["x", "x", "y"], "n": [1, 2, 3]})
        result = safe_exec_pandas(
            code="Q = source_df.groupby('cat').agg(total=('n', 'sum')).reset_index()",
            inputs={"source_df": source},
            expected_output_var="Q",
        )
        assert result.success
        assert len(result.output) == 2

    def test_numpy_via_np(self):
        result = safe_exec_pandas(
            code="raw_df = pd.DataFrame({'x': np.arange(5)})",
            inputs={},
            expected_output_var="raw_df",
        )
        assert result.success
        assert len(result.output) == 5


# ============================================================
# Forbidden builtins removed
# ============================================================
class TestForbiddenBuiltins:
    @pytest.mark.parametrize("forbidden", [
        "open", "exec", "eval", "compile", "__import__",
        "globals", "locals", "vars",
    ])
    def test_builtin_removed(self, forbidden):
        assert forbidden not in _SAFE_BUILTINS, \
            f"{forbidden} 仍在 safe builtins — security hole"

    def test_safe_builtins_preserved(self):
        """常用的 safe builtins 該保留"""
        for safe in ("len", "range", "list", "dict", "tuple", "str",
                      "int", "float", "bool", "max", "min", "sum",
                      "round", "abs", "sorted", "reversed", "enumerate",
                      "zip", "map", "filter", "print"):
            assert safe in _SAFE_BUILTINS, f"{safe} 該保留但不見了"

    def test_exec_open_blocked(self):
        result = safe_exec_pandas(
            code="raw_df = open('/etc/passwd').read()",
            inputs={"source_df": pd.DataFrame({"x": [1]})},
            expected_output_var="raw_df",
        )
        assert result.success is False
        # 預期 NameError — open 從 builtins 移除了
        assert "open" in str(result.error).lower() or \
               "NameError" in str(result.error_type or "")

    def test_eval_blocked(self):
        result = safe_exec_pandas(
            code="x = eval('1+1')\nraw_df = pd.DataFrame({'x': [x]})",
            inputs={},
            expected_output_var="raw_df",
        )
        assert result.success is False

    def test_dunder_import_blocked(self):
        result = safe_exec_pandas(
            code="os = __import__('os')\nraw_df = pd.DataFrame({'x': [1]})",
            inputs={},
            expected_output_var="raw_df",
        )
        assert result.success is False


# ============================================================
# Timeout
# ============================================================
class TestTimeout:
    def test_short_timeout_triggers(self):
        # 寫一個小回圈 sleep — 但 time 不在 safe_builtins,所以這測試需要 manual
        # 用 while True 觸發 timeout(回圈不需 builtins)
        result = safe_exec_pandas(
            code="x = 0\nwhile True:\n    x += 1\n    if x > 10**12: break\nraw_df = pd.DataFrame({'x': [x]})",
            inputs={},
            expected_output_var="raw_df",
            timeout_s=0.3,
        )
        assert result.success is False
        assert result.error_type == "TimeoutError"


# ============================================================
# Output validation
# ============================================================
class TestOutputValidation:
    def test_missing_output_var(self):
        result = safe_exec_pandas(
            code="filtered = source_df.copy()",   # 沒寫 raw_df
            inputs={"source_df": pd.DataFrame({"x": [1]})},
            expected_output_var="raw_df",
        )
        assert result.success is False
        assert result.error_type == "MissingOutput"

    def test_wrong_output_type(self):
        result = safe_exec_pandas(
            code="raw_df = 'a string'",
            inputs={},
            expected_output_var="raw_df",
        )
        assert result.success is False
        assert result.error_type == "WrongOutputType"

    def test_series_auto_to_frame(self):
        result = safe_exec_pandas(
            code="raw_df = source_df['x']",   # 單欄 → Series
            inputs={"source_df": pd.DataFrame({"x": [1, 2, 3]})},
            expected_output_var="raw_df",
        )
        # Series 應自動轉 DataFrame
        assert result.success
        assert isinstance(result.output, pd.DataFrame)


# ============================================================
# Row / col limits
# ============================================================
class TestLimits:
    def test_truncate_rows(self):
        big = pd.DataFrame({"x": range(1000)})
        result = safe_exec_pandas(
            code="raw_df = source_df.copy()",
            inputs={"source_df": big},
            expected_output_var="raw_df",
            max_rows=100,
        )
        assert result.success
        assert len(result.output) == 100
        assert result.truncated is True

    def test_truncate_cols(self):
        big = pd.DataFrame({f"col_{i}": [1] for i in range(50)})
        result = safe_exec_pandas(
            code="raw_df = source_df.copy()",
            inputs={"source_df": big},
            expected_output_var="raw_df",
            max_cols=10,
        )
        assert result.success
        assert result.output.shape[1] == 10
        assert result.truncated is True


# ============================================================
# Error handling
# ============================================================
class TestErrorHandling:
    def test_syntax_error(self):
        result = safe_exec_pandas(
            code="raw_df = source_df[invalid syntax]",
            inputs={"source_df": pd.DataFrame({"x": [1]})},
            expected_output_var="raw_df",
        )
        assert result.success is False
        assert "Syntax" in (result.error or "") or \
               "SyntaxError" in (result.error_type or "")

    def test_key_error(self):
        result = safe_exec_pandas(
            code="raw_df = source_df[['missing_col']]",
            inputs={"source_df": pd.DataFrame({"x": [1]})},
            expected_output_var="raw_df",
        )
        assert result.success is False
        # pandas raises KeyError
        assert "KeyError" in (result.error_type or "") or \
               "missing_col" in (result.error or "")


# ============================================================
# check_dataframe_limits 工具
# ============================================================
class TestCheckDataframeLimits:
    def test_within_limits(self):
        df = pd.DataFrame({"x": range(100)})
        ok, err = check_dataframe_limits(df, max_rows=100_000, max_cols=500)
        assert ok is True
        assert err is None

    def test_too_many_rows(self):
        df = pd.DataFrame({"x": range(100_001)})
        ok, err = check_dataframe_limits(df, max_rows=100_000)
        assert ok is False
        assert "rows" in err

    def test_too_many_cols(self):
        df = pd.DataFrame({f"c_{i}": [1] for i in range(501)})
        ok, err = check_dataframe_limits(df, max_cols=500)
        assert ok is False
        assert "cols" in err
