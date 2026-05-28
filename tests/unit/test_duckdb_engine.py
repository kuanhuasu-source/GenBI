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


# ============================================================
# v0.18 M4: JOIN extraction + confirmed-relationship gate
# ============================================================
from duckdb_engine import (
    extract_joins_from_sql,
    validate_joins_against_confirmed,
)


class TestExtractJoinsFromSql:
    def test_simple_inner_join(self):
        sql = "SELECT * FROM a JOIN b ON a.x = b.y"
        joins = extract_joins_from_sql(sql)
        assert len(joins) == 1
        j = joins[0]
        assert j["joined_table"] == "b"
        assert j["from_table"] == "a"
        assert j["from_field"] == "x"
        assert j["to_table"] == "b"
        assert j["to_field"] == "y"

    def test_left_join(self):
        sql = "SELECT * FROM orders LEFT JOIN customers ON orders.cid = customers.cid"
        assert len(extract_joins_from_sql(sql)) == 1

    def test_right_outer_join(self):
        sql = "SELECT * FROM a RIGHT OUTER JOIN b ON a.x = b.x"
        assert len(extract_joins_from_sql(sql)) == 1

    def test_full_outer_join(self):
        sql = "SELECT * FROM a FULL OUTER JOIN b ON a.k = b.k"
        assert len(extract_joins_from_sql(sql)) == 1

    def test_multiple_joins(self):
        sql = (
            "SELECT * FROM orders "
            "JOIN customers ON orders.cid = customers.cid "
            "JOIN products ON orders.pid = products.pid"
        )
        joins = extract_joins_from_sql(sql)
        assert len(joins) == 2

    def test_no_join(self):
        assert extract_joins_from_sql("SELECT * FROM t") == []

    def test_empty_sql(self):
        assert extract_joins_from_sql("") == []

    def test_cross_join_not_matched(self):
        # CROSS JOIN has no ON clause → MVP limitation, not matched.
        sql = "SELECT * FROM a CROSS JOIN b"
        assert extract_joins_from_sql(sql) == []


# Fixtures for relationship dicts in JOIN validator tests.
def _confirmed_rel(
    from_table="orders", from_field="customer_id",
    to_table="customers", to_field="customer_id",
    status="confirmed",
    relationship_type="many_to_one",
    rid=None,
):
    return {
        "relationship_id": rid or f"rel_{from_table}_{to_table}_{from_field}",
        "from_table": from_table,
        "from_field": from_field,
        "to_table": to_table,
        "to_field": to_field,
        "relationship_type": relationship_type,
        "status": status,
    }


class TestValidateJoinsAgainstConfirmed:
    def test_confirmed_match_returns_no_errors(self):
        sql = "SELECT * FROM orders JOIN customers ON orders.customer_id = customers.customer_id"
        errors = validate_joins_against_confirmed(sql, [_confirmed_rel()])
        assert errors == []

    def test_edited_also_counts_as_confirmed(self):
        # Per spec §5.3, "edited" means user fixed + accepted → same trust.
        sql = "SELECT * FROM orders JOIN customers ON orders.customer_id = customers.customer_id"
        errors = validate_joins_against_confirmed(
            sql, [_confirmed_rel(status="edited")],
        )
        assert errors == []

    def test_candidate_status_blocks(self):
        sql = "SELECT * FROM orders JOIN customers ON orders.customer_id = customers.customer_id"
        errors = validate_joins_against_confirmed(
            sql, [_confirmed_rel(status="candidate")],
        )
        assert len(errors) == 1
        assert "candidate" in errors[0]

    def test_rejected_status_blocks(self):
        sql = "SELECT * FROM orders JOIN customers ON orders.customer_id = customers.customer_id"
        errors = validate_joins_against_confirmed(
            sql, [_confirmed_rel(status="rejected")],
        )
        assert len(errors) == 1
        assert "rejected" in errors[0]

    def test_unknown_relationship_blocks(self):
        # JOIN refers to tables/columns that have no relationship at all.
        sql = "SELECT * FROM a JOIN b ON a.x = b.y"
        errors = validate_joins_against_confirmed(sql, [])
        assert len(errors) == 1
        assert "no matching relationship" in errors[0]

    def test_m2m_candidate_blocks_even_when_confirmed(self):
        # Spec §8.2 guardrail: m2m_candidate cannot auto-join even if
        # user confirmed. They must first re-classify to one_to_*.
        sql = "SELECT * FROM tag JOIN post ON tag.post_id = post.id"
        rels = [_confirmed_rel(
            from_table="tag", from_field="post_id",
            to_table="post", to_field="id",
            status="confirmed",
            relationship_type="many_to_many_candidate",
        )]
        errors = validate_joins_against_confirmed(sql, rels)
        assert len(errors) == 1
        assert "many_to_many_candidate" in errors[0]

    def test_reverse_direction_also_matches(self):
        # Relationship stored as orders→customers; SQL writes it the
        # other way (customers JOIN orders ON customers.x = orders.x).
        # Both directions reference the same FK link.
        sql = "SELECT * FROM customers JOIN orders ON customers.customer_id = orders.customer_id"
        errors = validate_joins_against_confirmed(sql, [_confirmed_rel()])
        assert errors == []

    def test_case_insensitive_match(self):
        # Relationships stored with original sheet case "Customers"
        # but SQL uses lowercase (DuckDB normalizes identifiers).
        sql = "SELECT * FROM orders JOIN customers ON orders.customer_id = customers.customer_id"
        rels = [_confirmed_rel(to_table="Customers")]
        errors = validate_joins_against_confirmed(sql, rels)
        assert errors == []

    def test_multi_join_one_unconfirmed_blocks_only_that_one(self):
        sql = (
            "SELECT * FROM orders "
            "JOIN customers ON orders.customer_id = customers.customer_id "
            "JOIN products ON orders.product_id = products.product_id"
        )
        rels = [
            _confirmed_rel(),  # orders→customers OK
            # products NOT in confirmed list
        ]
        errors = validate_joins_against_confirmed(sql, rels)
        assert len(errors) == 1
        assert "orders.product_id" in errors[0]

    def test_no_joins_no_errors(self):
        # Single-table SELECT — nothing to validate.
        assert validate_joins_against_confirmed("SELECT * FROM t", []) == []


class TestExecuteSafeWithJoinValidation:
    def test_confirmed_join_executes(self, tmp_path):
        # End-to-end: register two parquets, confirm a join, run it.
        df_o = pd.DataFrame({
            "order_id": [1, 2, 3],
            "customer_id": ["C1", "C2", "C1"],
        })
        df_c = pd.DataFrame({
            "customer_id": ["C1", "C2"],
            "name": ["Alice", "Bob"],
        })
        po = tmp_path / "orders.parquet"
        pc = tmp_path / "customers.parquet"
        df_o.to_parquet(po, index=False)
        df_c.to_parquet(pc, index=False)

        engine = DuckDBEngine()
        engine.register_dataset_tables({
            "orders": str(po),
            "customers": str(pc),
        })
        rels = [_confirmed_rel()]
        result = engine.execute_safe_with_join_validation(
            "SELECT orders.order_id, customers.name "
            "FROM orders JOIN customers "
            "ON orders.customer_id = customers.customer_id",
            confirmed_relationships=rels,
        )
        engine.close()
        assert result.success, f"unexpected fail: {result.error}"
        assert len(result.df) == 3

    def test_unconfirmed_join_blocked_before_execution(self, tmp_path):
        # No confirmed relationships → JOIN rejected pre-execution
        # → error_type set + no DataFrame returned.
        df = pd.DataFrame({"x": [1, 2]})
        p1 = tmp_path / "a.parquet"
        p2 = tmp_path / "b.parquet"
        df.to_parquet(p1, index=False)
        df.to_parquet(p2, index=False)

        engine = DuckDBEngine()
        engine.register_dataset_tables({"a": str(p1), "b": str(p2)})
        result = engine.execute_safe_with_join_validation(
            "SELECT * FROM a JOIN b ON a.x = b.x",
            confirmed_relationships=[],
        )
        engine.close()
        assert result.success is False
        assert result.error_type == "JoinNotConfirmed"
        assert result.df is None
        assert "no matching relationship" in result.error.lower()

    def test_register_dataset_tables_returns_mapping(self, tmp_path):
        df = pd.DataFrame({"x": [1]})
        p = tmp_path / "t.parquet"
        df.to_parquet(p, index=False)

        engine = DuckDBEngine()
        result = engine.register_dataset_tables({"my_table": str(p)})
        engine.close()
        assert result == {"my_table": "my_table"}

    def test_register_dataset_tables_rejects_bad_name(self, tmp_path):
        df = pd.DataFrame({"x": [1]})
        p = tmp_path / "t.parquet"
        df.to_parquet(p, index=False)
        engine = DuckDBEngine()
        with pytest.raises(ValueError):
            engine.register_dataset_tables({"1-bad-name": str(p)})
        engine.close()
