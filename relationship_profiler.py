"""
relationship_profiler.py — v0.18 M2

Cross-table relationship inference per spec §8. Output shape matches
spec §5.3 `upload_relationship_candidates` (minus dataset_id /
metadata_version, which are repository-level concerns added at persist
time).

# What changed vs. M5.3 (the v0.15 version replaced here)

The pre-M2 version returned a heuristic 0.85-base score and hard-coded
every relationship as `many_to_one`. This rewrite implements the spec
§8.1 weighted formula and §8.2 type inference, and uses the spec §5.3
evidence schema (different field names from the old version).

# Confidence formula (spec §8.1)

    score = (
        0.25 * name_score        # 1.0 if normalized names match exactly
      + 0.20 * type_score        # 1.0 if physical types compatible, else 0.0
      + 0.30 * overlap_score     # ratio of `from` values present in `to`
      + 0.20 * uniqueness_score  # distinct/row ratio on the `to` side (PK-ness)
      - penalties                # null / free-text / low-cardinality penalties
    )

    score is clamped to [0.0, 1.0].

# Status tiers (spec §8.1)

    >= 0.90  high confidence — recommend user confirm with one click
    0.70-0.89 review required
    0.50-0.69 weak candidate — default off
    < 0.50   ignore by default (not returned)

# Type inference (spec §8.2)

    source_unique >= 0.95 AND target_unique >= 0.95 → one_to_one
    source_unique <  0.95 AND target_unique >= 0.95 → many_to_one
    source_unique >= 0.95 AND target_unique <  0.95 → one_to_many
    source_unique <  0.95 AND target_unique <  0.95 → many_to_many_candidate

# Many-to-many guardrail (spec §8.2 boundary)

m2m candidates are detected and returned with relationship_type =
"many_to_many_candidate" but caller / executor must NOT auto-join them.
That guardrail lives in the join execution path (M4 DuckDB engine), not
here — but the type tag exists so the executor can spot them.

# Evidence dict (spec §5.3)

    {
      "name_similarity": float       # 1.0 if exact normalized match
      "type_compatible": bool        # both physical types in same family
      "from_to_overlap_ratio": float # |from ∩ to| / |from|
      "to_unique_ratio": float       # |distinct(to)| / |to|
      "sample_match_count": int      # |from ∩ to|
    }

# What's intentionally out-of-scope (deferred to follow-up PRs)

- Fuzzy name match (cust_id ↔ customer_id) — only exact normalized match here.
  Tracked as TODO: "spec §8 signal 2 (different names, high overlap)".
- Composite-key relationships (multiple columns).
- Self-references.
- Cross-dataset relationships.

# Public API

    from relationship_profiler import detect_relationships, build_relationship_id

    result = detect_relationships(
        tables={"orders": df_orders, "customers": df_customers},
        # OR: tables=[("orders", df_orders), ("customers", df_customers)]
    )
    # → {"relationships": [...], "n_pairs_scanned": int, "n_relationships_found": int}

# Spec cross-refs

- §5.3 upload_relationship_candidates schema
- §8.1 confidence scoring
- §8.2 type inference + many-to-many guardrail
- §17 M2 milestone
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional, Union

import pandas as pd

logger = logging.getLogger(__name__)


# ============================================================
# Thresholds (spec §8.1)
# ============================================================
HIGH_CONFIDENCE_MIN = 0.90
REVIEW_REQUIRED_MIN = 0.70
WEAK_CANDIDATE_MIN = 0.50

# Type inference (spec §8.2)
UNIQUE_HIGH_THRESHOLD = 0.95  # distinct/row >= this → "high" uniqueness

# Penalty thresholds
NULL_PENALTY_PCT_THRESHOLD = 30.0  # null_pct above this triggers penalty
NULL_PENALTY_AMOUNT = 0.20
FREE_TEXT_AVG_LEN_THRESHOLD = 50.0  # avg string len above → looks like free text
FREE_TEXT_PENALTY_AMOUNT = 0.25
LOW_CARDINALITY_DISTINCT_THRESHOLD = 5  # distinct < this → looks categorical
LOW_CARDINALITY_PENALTY_AMOUNT = 0.20
PENALTY_TOTAL_CAP = 0.50  # max combined penalty


# Score weights (spec §8.1)
WEIGHT_NAME = 0.25
WEIGHT_TYPE = 0.20
WEIGHT_OVERLAP = 0.30
WEIGHT_UNIQUENESS = 0.20


# ============================================================
# Helpers
# ============================================================
_NORMALIZE_RE = re.compile(r"[^a-zA-Z0-9]+")


def _normalize_field_name(name: str) -> str:
    """Normalize a column name for fuzzy matching."""
    return _NORMALIZE_RE.sub("", name).lower()


def _physical_type_family(series: pd.Series) -> str:
    """Group pandas dtypes into compatible families for join purposes."""
    if pd.api.types.is_bool_dtype(series):
        return "bool"
    if pd.api.types.is_integer_dtype(series):
        return "integer"
    if pd.api.types.is_float_dtype(series):
        return "number"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "datetime"
    if pd.api.types.is_string_dtype(series) or series.dtype == object:
        return "string"
    return "unknown"


def _types_compatible(from_series: pd.Series, to_series: pd.Series) -> bool:
    """Two columns can join if their families match.

    integer and number are deliberately NOT cross-compatible: joining
    an integer FK to a float PK would silently lose precision on floats
    like 1.5 — and any reasonable PK is integer or string, not float.
    """
    return _physical_type_family(from_series) == _physical_type_family(to_series)


def build_relationship_id(
    from_table: str, to_table: str, from_field: str,
) -> str:
    """Deterministic relationship_id per spec §5.3 example.

    `rel_attendance_employee_employee_id`
    → rel_<from_table>_<to_table>_<from_field>

    Idempotent: same args always produce the same id. The repository
    uses this for upsert + dedup.
    """
    return f"rel_{from_table}_{to_table}_{from_field}"


def confidence_tier(score: float) -> str:
    """Map a numeric score to one of the spec §8.1 tier labels."""
    if score >= HIGH_CONFIDENCE_MIN:
        return "high"
    if score >= REVIEW_REQUIRED_MIN:
        return "review_required"
    if score >= WEAK_CANDIDATE_MIN:
        return "weak"
    return "ignore"


# ============================================================
# Penalty calculation
# ============================================================
def _calc_penalties(
    from_series: pd.Series,
    to_series: pd.Series,
) -> float:
    """Return total penalty (clamped to PENALTY_TOTAL_CAP)."""
    total = 0.0

    # null penalty on either side
    for s in (from_series, to_series):
        if len(s) == 0:
            continue
        null_pct = float(s.isna().sum()) / len(s) * 100
        if null_pct > NULL_PENALTY_PCT_THRESHOLD:
            total += NULL_PENALTY_AMOUNT
            break  # apply once even if both sides have nulls

    # free-text penalty — string family with long values
    for s in (from_series, to_series):
        if _physical_type_family(s) != "string":
            continue
        try:
            clean = s.dropna().astype(str)
            if len(clean) == 0:
                continue
            avg_len = clean.str.len().mean()
            if avg_len and avg_len > FREE_TEXT_AVG_LEN_THRESHOLD:
                total += FREE_TEXT_PENALTY_AMOUNT
                break
        except Exception:
            pass

    # low-cardinality penalty — categorical-looking column
    for s in (from_series, to_series):
        try:
            distinct = s.nunique(dropna=True)
            if 0 < distinct < LOW_CARDINALITY_DISTINCT_THRESHOLD:
                total += LOW_CARDINALITY_PENALTY_AMOUNT
                break
        except TypeError:
            pass

    return min(total, PENALTY_TOTAL_CAP)


# ============================================================
# Per-pair detection
# ============================================================
def _detect_pair_relationships(
    from_table: str, from_df: pd.DataFrame,
    to_table: str, to_df: pd.DataFrame,
) -> list[dict]:
    """Find candidate FK relationships from `from_table` → `to_table`.

    Only "exact normalized column name match" strategy in this version
    (Strategy 1 from the spec §8 signal list). Strategy 2 (different
    names, value overlap) is a TODO — needs O(M*N) value comparison.
    """
    out: list[dict] = []

    # Build normalized → original column name maps for both sides.
    from_cols_norm: dict[str, str] = {
        _normalize_field_name(c): c for c in from_df.columns
    }
    to_cols_norm: dict[str, str] = {
        _normalize_field_name(c): c for c in to_df.columns
    }
    common_normalized = set(from_cols_norm.keys()) & set(to_cols_norm.keys())

    for norm in common_normalized:
        from_col = from_cols_norm[norm]
        to_col = to_cols_norm[norm]
        try:
            rel = _score_pair(
                from_table, from_col, from_df[from_col],
                to_table, to_col, to_df[to_col],
            )
        except Exception as e:
            logger.warning(
                f"_score_pair failed for {from_table}.{from_col} → "
                f"{to_table}.{to_col}: {e}"
            )
            continue
        if rel is not None:
            out.append(rel)

    return out


def _score_pair(
    from_table: str, from_field: str, from_series: pd.Series,
    to_table: str, to_field: str, to_series: pd.Series,
) -> Optional[dict]:
    """Score one (from, to) field pair. Returns None if confidence < weak."""
    from_clean = from_series.dropna()
    to_clean = to_series.dropna()
    if len(from_clean) == 0 or len(to_clean) == 0:
        return None

    try:
        from_vals = set(from_clean.unique())
        to_vals = set(to_clean.unique())
    except TypeError:
        # unhashable values — can't form a set
        return None
    if not from_vals or not to_vals:
        return None

    # name_score: 1.0 since we only get here via exact normalized match.
    name_score = (
        1.0 if _normalize_field_name(from_field) == _normalize_field_name(to_field)
        else 0.0
    )

    # type_score
    type_compatible = _types_compatible(from_series, to_series)
    type_score = 1.0 if type_compatible else 0.0

    # overlap_score: |from ∩ to| / |from|
    intersect = from_vals & to_vals
    overlap_score = len(intersect) / len(from_vals)

    # uniqueness_score: how PK-like the `to` side is.
    to_distinct = len(to_vals)
    to_total = len(to_clean)
    to_unique_ratio = to_distinct / to_total if to_total else 0.0
    uniqueness_score = to_unique_ratio

    # Penalties
    penalties = _calc_penalties(from_series, to_series)

    # Weighted score, clamped to [0,1]
    raw_score = (
        WEIGHT_NAME * name_score
        + WEIGHT_TYPE * type_score
        + WEIGHT_OVERLAP * overlap_score
        + WEIGHT_UNIQUENESS * uniqueness_score
        - penalties
    )
    score = max(0.0, min(1.0, raw_score))

    # Below weak tier → drop entirely (spec §8.1 "ignore by default")
    if score < WEAK_CANDIDATE_MIN:
        return None

    # Source uniqueness for type inference
    from_distinct = len(from_vals)
    from_total = len(from_clean)
    from_unique_ratio = from_distinct / from_total if from_total else 0.0

    relationship_type = _infer_relationship_type(
        source_unique=from_unique_ratio,
        target_unique=to_unique_ratio,
    )

    # default_join_type per relationship_type:
    #   m2o, m2m_candidate → "left" (preserve from-side rows)
    #   1:1, o2m            → "inner" (symmetric / pickier)
    default_join_type = {
        "many_to_one": "left",
        "one_to_one": "inner",
        "one_to_many": "inner",
        "many_to_many_candidate": "left",
    }.get(relationship_type, "left")

    return {
        "relationship_id": build_relationship_id(from_table, to_table, from_field),
        "from_table": from_table,
        "from_field": from_field,
        "to_table": to_table,
        "to_field": to_field,
        "relationship_type": relationship_type,
        "default_join_type": default_join_type,
        "confidence": round(score, 3),
        "confidence_tier": confidence_tier(score),
        "evidence": {
            "name_similarity": round(name_score, 3),
            "type_compatible": bool(type_compatible),
            "from_to_overlap_ratio": round(overlap_score, 3),
            "to_unique_ratio": round(to_unique_ratio, 3),
            "sample_match_count": int(len(intersect)),
        },
        "status": "candidate",  # spec §5.3 default
    }


def _infer_relationship_type(
    source_unique: float, target_unique: float,
) -> str:
    """Spec §8.2 table."""
    src_high = source_unique >= UNIQUE_HIGH_THRESHOLD
    tgt_high = target_unique >= UNIQUE_HIGH_THRESHOLD
    if src_high and tgt_high:
        return "one_to_one"
    if not src_high and tgt_high:
        return "many_to_one"
    if src_high and not tgt_high:
        return "one_to_many"
    return "many_to_many_candidate"  # guardrail tag


# ============================================================
# Main entry — pairwise scan + dedup
# ============================================================
def detect_relationships(
    tables: Union[dict[str, pd.DataFrame], list],
    profiles: Optional[dict] = None,
    max_pairs: int = 50,
    max_sample_rows: int = 5000,
) -> dict[str, Any]:
    """Detect cross-table relationship candidates.

    Args:
        tables: Either {table_name: df} dict (spec §8 signature) OR
                legacy [(table_name, df), ...] list. Both accepted.
        profiles: Optional profile dict from multi_table_profiler — used
                  to short-circuit some checks. (Currently unused; param
                  kept for future Strategy-2 implementation.)
        max_pairs: Hard cap on (i, j) pair scans for big workbooks.
        max_sample_rows: Per spec §8 default; reserved for future
                  Strategy-2 sampling. Currently unused.

    Returns:
        {
          "relationships": list[dict],  # one per spec §5.3 (minus
                                          dataset_id / metadata_version)
          "n_pairs_scanned": int,
          "n_relationships_found": int,
        }
    """
    # Normalize input shape
    if isinstance(tables, dict):
        items: list[tuple[str, pd.DataFrame]] = list(tables.items())
    else:
        items = list(tables)

    if len(items) < 2:
        return {
            "relationships": [],
            "n_pairs_scanned": 0,
            "n_relationships_found": 0,
        }

    all_rels: list[dict] = []
    pairs_scanned = 0

    for i in range(len(items)):
        for j in range(len(items)):
            if i == j:
                continue
            if pairs_scanned >= max_pairs:
                logger.warning(
                    f"detect_relationships: hit max_pairs={max_pairs} limit"
                )
                break
            from_name, from_df = items[i]
            to_name, to_df = items[j]
            try:
                rels = _detect_pair_relationships(
                    from_name, from_df, to_name, to_df,
                )
                all_rels.extend(rels)
            except Exception as e:
                logger.warning(
                    f"detect_pair({from_name} → {to_name}) failed: {e}"
                )
            pairs_scanned += 1

    # Dedup by relationship_id (same id from multiple paths → keep highest
    # confidence)
    by_id: dict[str, dict] = {}
    for r in all_rels:
        rid = r["relationship_id"]
        if rid not in by_id or r["confidence"] > by_id[rid]["confidence"]:
            by_id[rid] = r

    dedup = list(by_id.values())
    dedup.sort(key=lambda r: -r["confidence"])

    return {
        "relationships": dedup,
        "n_pairs_scanned": pairs_scanned,
        "n_relationships_found": len(dedup),
    }
