"""tests/unit/test_duckdb_engine.py — unit tests for duckdb_engine.py (M5.5)."""

from __future__ import annotations

import pandas as pd
import pytest

# 沒裝 duckdb 的環境跳過
duckdb = pytest.importorskip("duckdb")

from duckdb_engine import (
    DuckDBEngine,
    _validate_table_name,
)


# ============================================================
# Table name validation
# ============================================================
class TestValidateTableName:
    @pytest.mark.parametrize("good", [
        "orders", "ORDERS", "order_items", "x", "_private", "t1",
    ])
    def test_valid_names(self, good):
        assert _validate_table_name(good) == good

    @pytest.mark.parametrize("bad", [
        "1table",   # 開頭數字
        "table-name",   # 含 dash
        "table name",   # 空白
        "table;DROP",   # SQL injection
        "table.column",   # dot
        "",
        " ",
    ])
    def test_invalid_names(self, bad):
        with pytest.raises(ValueError):
            _validate_table_name(bad)


# ============================================================
# SQL safety
# ============================================================
class TestSQLSafety:
    @pytest.mark.parametrize("bad_sql", [
        "DROP TABLE orders",
        "DELETE FROM orders",
        "UPDATE orders SET x=1",
        "INSERT INTO orders VALUES (1)",
        "CREATE TABLE x (a INT)",
        "ATTACH '/tmp/bad.db'",
        "COPY orders TO '/tmp/out.csv'",
        "EXPORT DATABASE '/tmp/out'",
        "INSTALL httpfs",
        "LOAD spatial",
        "PRAGMA database_list",
    ])
    def test_blocks_dangerous_sql(self, bad_sql):
        engine = DuckDBEngine()
        result = engine.execute_safe(bad_sql)
        engine.close()
        assert result.success is False
        assert result.error_type == "SQLForbidden"

    def test_blocks_dangerous_in_subquery(self):
        engine = DuckDBEngine()
        result = engine.execute_safe(
            "SELECT * FROM (SELECT * FROM x; DROP TABLE x) y"
        )
        engine.close()
        assert result.success is False

    def test_rejects_non_select(self):
        engine = DuckDBEngine()
        # 即使沒 forbidden keyword,非 SELECT/WITH/VALUES/TABLE 開頭該擋
        result = engine.execute_safe("foo bar")
        engine.close()
        assert result.success is False

    def test_empty_sql(self):
        engine = DuckDBEngine()
        result = engine.execute_safe("")
        engine.close()
        assert result.success is False


# ============================================================
# Happy path:register + select
# ============================================================
class TestRegisterAndSelect:
    def test_register_dataframe_and_select(self):
        engine = DuckDBEngine()
        df = pd.DataFrame({
            "x": [1, 2, 3, 4, 5],
            "y": ["a", "b", "a", "b", "c"],
        })
        engine.register_dataframe("t", df)
        result = engine.execute_safe("SELECT * FROM t WHERE y = 'a'")
        engine.close()
        assert result.success
        assert len(result.df) == 2
        assert set(result.df.columns) == {"x", "y"}

    def test_aggregate_query(self):
        engine = DuckDBEngine()
        df = pd.DataFrame({
            "cat": ["a", "a", "b", "b", "b"],
            "val": [10, 20, 30, 40, 50],
        })
        engine.register_dataframe("t", df)
        result = engine.execute_safe(
            "SELECT cat, SUM(val) AS total FROM t GROUP BY cat ORDER BY cat"
        )
        engine.close()
        assert result.success
        assert len(result.df) == 2
        assert result.df.iloc[0]["cat"] == "a"
        assert result.df.iloc[0]["total"] == 30

    def test_with_cte(self):
        engine = DuckDBEngine()
        df = pd.DataFrame({"x": [1, 2, 3, 4]})
        engine.register_dataframe("t", df)
        result = engine.execute_safe(
            "WITH big AS (SELECT * FROM t WHERE x > 2) SELECT COUNT(*) AS c FROM big"
        )
        engine.close()
        assert result.success
        assert result.df.iloc[0]["c"] == 2


# ============================================================
# Parquet registration
# ============================================================
class TestRegisterParquet:
    def test_register_parquet_view(self, tmp_path):
        df = pd.DataFrame({"a": [10, 20, 30], "b": ["x", "y", "z"]})
        parquet_path = tmp_path / "data.parquet"
        df.to_parquet(parquet_path, index=False)

        engine = DuckDBEngine()
        engine.register_parquet("source", parquet_path)
        result = engine.execute_safe("SELECT * FROM source WHERE a >= 20")
        engine.close()
        assert result.success
        assert len(result.df) == 2

    def test_list_tables(self, tmp_path):
        df = pd.DataFrame({"x": [1]})
        f = tmp_path / "f.parquet"
        df.to_parquet(f, index=False)
        engine = DuckDBEngine()
        engine.register_parquet("t1", f)
        engine.register_dataframe("t2", df)
        assert set(engine.list_tables()) == {"t1", "t2"}
        engine.close()


# ============================================================
# Row limit enforcement
# ============================================================
class TestRowLimit:
    def test_auto_limit_when_no_limit_in_sql(self):
        engine = DuckDBEngine(max_result_rows=3)
        big = pd.DataFrame({"x": list(range(10))})
        engine.register_dataframe("big", big)
        result = engine.execute_safe("SELECT * FROM big")
        engine.close()
        assert result.success
        # 該被截斷到 3 列
        assert len(result.df) == 3

    def test_respects_user_limit(self):
        """如果 SQL 自己有 LIMIT,不該被 wrap"""
        engine = DuckDBEngine(max_result_rows=100)
        big = pd.DataFrame({"x": list(range(50))})
        engine.register_dataframe("big", big)
        result = engine.execute_safe("SELECT * FROM big LIMIT 5")
        engine.close()
        assert result.success
        assert len(result.df) == 5


# ============================================================
# Result dataclass
# ============================================================
class TestResultDataclass:
    def test_success_carries_df(self):
        engine = DuckDBEngine()
        engine.register_dataframe("t", pd.DataFrame({"x": [1]}))
        result = engine.execute_safe("SELECT * FROM t")
        engine.close()
        assert result.success
        assert isinstance(result.df, pd.DataFrame)
        assert result.error is None
        assert result.exec_time_s >= 0

    def test_error_carries_message(self):
        engine = DuckDBEngine()
        # Reference unregistered table → DuckDB raise
        result = engine.execute_safe("SELECT * FROM nonexistent")
        engine.close()
        assert result.success is False
        assert result.error is not None


# ============================================================
# build_engine_for_dataset(integration with upload_repository)
# ============================================================
@pytest.mark.requires_mongo
class TestBuildEngineForDataset:
    def test_register_all_tables(self, mongo_db, tmp_path):
        from upload_repository import UploadRepository
        from duckdb_engine import build_engine_for_dataset

        repo = UploadRepository(mongo_db)
        repo.ensure_indexes()
        repo.create_dataset({
            "_id": "ds-1", "dataset_name": "x.csv",
            "owner": "a", "source_type": "file_upload",
            "file": {}, "status": "profiled",
        })
        # 寫 2 個 parquet
        df1 = pd.DataFrame({"id": [1, 2], "name": ["A", "B"]})
        df2 = pd.DataFrame({"id": [10, 20], "category": ["x", "y"]})
        p1 = tmp_path / "t1.parquet"
        p2 = tmp_path / "t2.parquet"
        df1.to_parquet(p1, index=False)
        df2.to_parquet(p2, index=False)
        repo.create_table({
            "dataset_id": "ds-1", "table_id": "users",
            "table_name": "Users", "row_count": 2, "column_count": 2,
            "storage": {"format": "parquet", "path": str(p1)},
        })
        repo.create_table({
            "dataset_id": "ds-1", "table_id": "tags",
            "table_name": "Tags", "row_count": 2, "column_count": 2,
            "storage": {"format": "parquet", "path": str(p2)},
        })

        engine = build_engine_for_dataset(repo, "ds-1")
        assert set(engine.list_tables()) == {"users", "tags"}
        # Cross-table 該能 join
        result = engine.execute_safe(
            "SELECT u.name, t.category FROM users u "
            "LEFT JOIN tags t ON u.id = t.id"
        )
        engine.close()
        assert result.success
