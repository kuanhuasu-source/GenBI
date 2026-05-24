"""
relationship_profiler.py — v0.15.0+ (M5.3)

跨 table / sheet 推論 relationship — 對齊 spec §4.2 item 2 + 3 + §9.4。

# 偵測策略(由強到弱)

1. **相同欄名**(strong):兩個 table 都有 `customer_id`(or normalized 後同)
2. **值域 80%+ 重疊**(medium):tableA.col_a unique values 跟 tableB.col_b unique 高度交集
3. **基數 ≈ row count**(weak):一邊 unique count == row count(可能 primary key),
   另一邊 value 範圍落在前者內(可能 foreign key)

# Output

```python
{
  "relationships": [
    {
      "from_table": "orders",
      "from_field": "customer_id",
      "to_table": "customers",
      "to_field": "customer_id",
      "relationship_type": "many_to_one",   # MVP 統一 m2o(FK on left)
      "confidence": 0.95,
      "reason": "欄名相同 + 值域 100% 在 customers 內",
      "evidence": {
        "name_match": True,
        "value_overlap_pct": 100.0,
        "left_distinct": 50,
        "right_distinct": 30,
        "right_is_pk": True,    # right table 的此欄 distinct == row count
      },
    },
    ...
  ],
}
```

# 用法

```python
from relationship_profiler import detect_relationships

result = detect_relationships(tables=[
    ("orders", df_orders),
    ("customers", df_customers),
])
```

# Phase 2 簡化邊界

MVP 只偵測 1-to-many / many-to-1(FK ↔ PK)。
**Not in scope**:
- many-to-many(需要 junction table 推論)
- composite key(多欄組合)
- self-reference
- 跨 dataset(只在同 dataset 內 tables)
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


# ============================================================
# Thresholds
# ============================================================
NAME_MATCH_CONFIDENCE_BASE = 0.85   # 欄名相同的 base confidence
VALUE_OVERLAP_HIGH = 0.80            # 80%+ 視為強 overlap
VALUE_OVERLAP_MED = 0.50             # 50-80% 為 medium
PK_DISTINCT_PCT = 0.99               # distinct/row > 99% 視為 PK


# ============================================================
# Single-pair detector
# ============================================================
def _detect_pair_relationship(
    left_name: str, left_df: pd.DataFrame,
    right_name: str, right_df: pd.DataFrame,
) -> list[dict]:
    """偵測 left → right 的 FK 關係(可能多條)。

    Returns:
        list of relationship dict(可能多條,例如 left 有 2 個 FK 指 right)。
    """
    found: list[dict] = []

    # Strategy 1:相同欄名
    common_cols = set(left_df.columns) & set(right_df.columns)
    for col in common_cols:
        # Skip 該欄完全是 None / empty
        left_vals = set(left_df[col].dropna().unique())
        right_vals = set(right_df[col].dropna().unique())
        if not left_vals or not right_vals:
            continue

        # 值域 overlap(left 的值有多少 % 在 right 內)
        intersect = left_vals & right_vals
        if not intersect:
            continue
        overlap_pct = len(intersect) / len(left_vals) * 100

        # 看 right 是否該欄是 PK(distinct == row count)
        right_distinct = right_df[col].nunique()
        right_row_count = len(right_df)
        right_is_pk = (right_distinct >= right_row_count * PK_DISTINCT_PCT)

        # Confidence:name_match 已 0.85 base,boost by overlap
        confidence = NAME_MATCH_CONFIDENCE_BASE
        if overlap_pct >= VALUE_OVERLAP_HIGH * 100:
            confidence = min(0.95, NAME_MATCH_CONFIDENCE_BASE + 0.10)
        elif overlap_pct < VALUE_OVERLAP_MED * 100:
            confidence = NAME_MATCH_CONFIDENCE_BASE - 0.20

        reason = (
            f"欄名相同 `{col}` + left 的值有 {overlap_pct:.0f}% 在 right 內"
        )
        if right_is_pk:
            reason += f"(right 此欄是 primary key)"

        found.append({
            "from_table": left_name,
            "from_field": col,
            "to_table": right_name,
            "to_field": col,
            "relationship_type": "many_to_one",
            "confidence": round(confidence, 3),
            "reason": reason,
            "evidence": {
                "name_match": True,
                "value_overlap_pct": round(overlap_pct, 2),
                "left_distinct": len(left_vals),
                "right_distinct": int(right_distinct),
                "right_is_pk": bool(right_is_pk),
            },
        })

    # Strategy 2:不同欄名但值域高度交集(medium signal)
    # 這條成本較高(O(left_cols × right_cols)),先 skip MVP
    # TODO Phase 3:跨 schema rename 的場景

    return found


# ============================================================
# Main entry — pairwise scan
# ============================================================
def detect_relationships(
    tables: list[tuple[str, pd.DataFrame]],
    max_pairs: int = 50,
) -> dict[str, Any]:
    """掃 tables list,兩兩偵測 relationship。

    Args:
        tables: [(table_name, df), ...] (例 [("orders", df_orders), ("customers", df_customers)])
        max_pairs:protection,table 數量太多 → 只掃前 max_pairs pair

    Returns:
        {
          "relationships": list of dict(每筆同上 schema),
          "n_pairs_scanned": int,
          "n_relationships_found": int,
        }
    """
    if len(tables) < 2:
        return {
            "relationships": [],
            "n_pairs_scanned": 0,
            "n_relationships_found": 0,
        }

    all_rels: list[dict] = []
    pairs_scanned = 0

    for i in range(len(tables)):
        for j in range(len(tables)):
            if i == j:
                continue
            if pairs_scanned >= max_pairs:
                logger.warning(
                    f"detect_relationships: 已掃 {max_pairs} pairs,提早停。"
                )
                break
            left_name, left_df = tables[i]
            right_name, right_df = tables[j]
            try:
                rels = _detect_pair_relationship(
                    left_name, left_df, right_name, right_df,
                )
                all_rels.extend(rels)
            except Exception as e:
                logger.warning(
                    f"detect_relationship({left_name} → {right_name}) failed: {e}"
                )
            pairs_scanned += 1

    # Dedup:相同 (from_table, from_field, to_table, to_field) 留 confidence 最高
    seen: dict[tuple, dict] = {}
    for r in all_rels:
        key = (r["from_table"], r["from_field"],
               r["to_table"], r["to_field"])
        if key not in seen or r["confidence"] > seen[key]["confidence"]:
            seen[key] = r
    dedup_rels = list(seen.values())
    # Sort by confidence desc
    dedup_rels.sort(key=lambda r: -r["confidence"])

    return {
        "relationships": dedup_rels,
        "n_pairs_scanned": pairs_scanned,
        "n_relationships_found": len(dedup_rels),
    }


# ============================================================
# Confirm relationship — used by Review UI
# ============================================================
def apply_user_confirmation(
    detected: list[dict],
    user_confirmations: list[dict],
) -> list[dict]:
    """套用使用者確認到偵測結果。

    user_confirmations 格式:
        [{"index": 0, "confirmed": True / False, "edited_relationship_type": "many_to_one"}, ...]
    """
    out: list[dict] = []
    confirmed_indices = {c["index"]: c for c in user_confirmations}
    for i, rel in enumerate(detected):
        user = confirmed_indices.get(i)
        if user is None:
            # 沒被 review → 保留為 unconfirmed
            r = dict(rel)
            r["user_confirmed"] = False
            out.append(r)
            continue
        if not user.get("confirmed", False):
            # User rejected — skip
            continue
        r = dict(rel)
        r["user_confirmed"] = True
        # 允許 user 改 relationship_type
        if "edited_relationship_type" in user:
            r["relationship_type"] = user["edited_relationship_type"]
        out.append(r)
    return out
