"""tests/unit/test_relationship_profiler.py — unit tests for relationship_profiler.py (M5.3)."""

from __future__ import annotations

import pandas as pd
import pytest

from relationship_profiler import (
    apply_user_confirmation,
    detect_relationships,
)


# ============================================================
# Happy path
# ============================================================
class TestDetectRelationships:
    def test_classic_fk_pk(self):
        """Orders.customer_id → Customers.customer_id(PK)"""
        orders = pd.DataFrame({
            "order_id": [1, 2, 3, 4, 5],
            "customer_id": [1, 2, 1, 3, 2],
            "amount": [100, 200, 50, 300, 75],
        })
        customers = pd.DataFrame({
            "customer_id": [1, 2, 3, 4],
            "name": ["A", "B", "C", "D"],
        })
        result = detect_relationships([("orders", orders), ("customers", customers)])
        assert result["n_relationships_found"] >= 1
        rels = result["relationships"]
        # orders → customers 該被偵測(orders.customer_id → customers.customer_id)
        match = next(
            (r for r in rels
             if r["from_table"] == "orders" and r["to_table"] == "customers"
             and r["from_field"] == "customer_id"),
            None,
        )
        assert match is not None
        # confidence 該高(欄名相同 + 100% overlap + right is PK)
        assert match["confidence"] >= 0.85
        assert match["evidence"]["right_is_pk"] is True
        assert match["evidence"]["value_overlap_pct"] == 100.0

    def test_low_overlap_low_confidence(self):
        """欄名相同但值域 overlap 很低 → 低 confidence"""
        a = pd.DataFrame({"x": [1, 2, 3, 4, 5]})
        b = pd.DataFrame({"x": [99, 100, 101]})   # 完全不重疊
        result = detect_relationships([("a", a), ("b", b)])
        # 該完全不出 relationship(no intersect)
        assert all(r["evidence"]["value_overlap_pct"] > 0
                    for r in result["relationships"])

    def test_no_shared_columns(self):
        a = pd.DataFrame({"foo": [1, 2]})
        b = pd.DataFrame({"bar": [3, 4]})
        result = detect_relationships([("a", a), ("b", b)])
        assert result["n_relationships_found"] == 0

    def test_self_pair_skipped(self):
        a = pd.DataFrame({"x": [1, 2]})
        # 給同 table list 應該不 self-pair
        result = detect_relationships([("a", a)])
        assert result["n_relationships_found"] == 0
        assert result["n_pairs_scanned"] == 0

    def test_three_table_chain(self):
        """3 個 table 串接:orders → customers + order_items → orders"""
        orders = pd.DataFrame({
            "order_id": [1, 2, 3], "customer_id": [10, 20, 10]})
        customers = pd.DataFrame({
            "customer_id": [10, 20, 30], "name": ["A", "B", "C"]})
        items = pd.DataFrame({
            "item_id": [1, 2, 3, 4], "order_id": [1, 1, 2, 3], "qty": [1, 2, 1, 1]})

        result = detect_relationships([
            ("orders", orders), ("customers", customers), ("items", items),
        ])
        # 該偵測 orders→customers + items→orders
        rel_keys = {
            (r["from_table"], r["to_table"])
            for r in result["relationships"]
        }
        assert ("orders", "customers") in rel_keys
        assert ("items", "orders") in rel_keys


class TestDedup:
    def test_dedup_keeps_highest_confidence(self):
        """偵測時兩 direction 都掃,A→B 跟 B→A 該各自存(不同 from/to)"""
        a = pd.DataFrame({"k": [1, 2, 3]})
        b = pd.DataFrame({"k": [1, 2, 3, 4]})
        result = detect_relationships([("a", a), ("b", b)])
        # 該有 2 條(a→b 跟 b→a),都不同 key
        keys = {(r["from_table"], r["from_field"],
                  r["to_table"], r["to_field"])
                for r in result["relationships"]}
        assert ("a", "k", "b", "k") in keys
        assert ("b", "k", "a", "k") in keys


# ============================================================
# User confirmation
# ============================================================
class TestApplyUserConfirmation:
    def test_user_rejects_one(self):
        detected = [
            {"from_table": "a", "from_field": "x",
             "to_table": "b", "to_field": "x",
             "relationship_type": "many_to_one",
             "confidence": 0.9, "reason": "", "evidence": {}},
            {"from_table": "c", "from_field": "y",
             "to_table": "d", "to_field": "y",
             "relationship_type": "many_to_one",
             "confidence": 0.85, "reason": "", "evidence": {}},
        ]
        confirmations = [
            {"index": 0, "confirmed": True},
            {"index": 1, "confirmed": False},
        ]
        out = apply_user_confirmation(detected, confirmations)
        assert len(out) == 1
        assert out[0]["from_table"] == "a"
        assert out[0]["user_confirmed"] is True

    def test_user_edits_type(self):
        detected = [{
            "from_table": "a", "from_field": "x",
            "to_table": "b", "to_field": "x",
            "relationship_type": "many_to_one",
            "confidence": 0.9, "reason": "", "evidence": {},
        }]
        confirmations = [{
            "index": 0, "confirmed": True,
            "edited_relationship_type": "one_to_one",
        }]
        out = apply_user_confirmation(detected, confirmations)
        assert out[0]["relationship_type"] == "one_to_one"

    def test_no_confirmation_marks_unconfirmed(self):
        detected = [{
            "from_table": "a", "from_field": "x",
            "to_table": "b", "to_field": "x",
            "relationship_type": "many_to_one",
            "confidence": 0.9, "reason": "", "evidence": {},
        }]
        # 沒 confirmation → 預設 unconfirmed 但保留
        out = apply_user_confirmation(detected, [])
        assert len(out) == 1
        assert out[0]["user_confirmed"] is False


# ============================================================
# Edge cases
# ============================================================
class TestEdgeCases:
    def test_all_null_columns(self):
        a = pd.DataFrame({"k": [None, None]})
        b = pd.DataFrame({"k": [1, 2]})
        result = detect_relationships([("a", a), ("b", b)])
        # null-only column 不該觸發 relationship
        assert result["n_relationships_found"] == 0

    def test_empty_tables(self):
        a = pd.DataFrame({"k": []})
        b = pd.DataFrame({"k": [1, 2]})
        result = detect_relationships([("a", a), ("b", b)])
        # 空 table 也不該炸
        assert result["n_relationships_found"] == 0

    def test_max_pairs_protection(self):
        # 6 個 table → 30 pairs(6×5),max_pairs=10 該止住
        tables = [(f"t{i}", pd.DataFrame({"x": [1]})) for i in range(6)]
        result = detect_relationships(tables, max_pairs=10)
        assert result["n_pairs_scanned"] == 10
