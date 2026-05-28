"""
multi_table_profiler.py — v0.18 (M1)

Thin orchestration layer above `data_profiler.profile_table` that produces
table-level enrichments for multi-sheet workbooks. Corresponds to spec §4
module list + §5.2 `upload_tables` doc shape + §7.1 physical profile fields
that `data_profiler` marks as "future enhancement".

# What this adds beyond data_profiler.profile_table

For each table:
  - `sheet_name`     — preserved from the workbook key
  - `table_name`     — normalized (lowercase, safe chars, deduped across sheets)
  - `table_id`       — `tbl_<table_name>` per spec §5.2 example
  - `possible_primary_key`   — list[str] of columns where distinct == row_count
                                and null_pct ~= 0
  - `possible_total_row`     — bool — last row looks like TOTAL / 合計
  - `duplicate_row_count`    — int — number of full-row duplicates
  - `table_warnings`         — list[str] — table-level warnings
  - `table_role`             — one of "fact" | "dimension" | "bridge" | "unknown"
  - `grain`                  — human-readable string e.g. "one row per employee"
                                (or None if PK couldn't be inferred)

# Why a separate module instead of extending data_profiler

`data_profiler` is column-level + table-level **structural** facts. This module
adds **semantic / cross-table** inferences (role, grain, FK candidates). They
belong on different layers because:
  - column profile is what feeds semantic_profiler and Phase A code generation
  - table_role + grain are what feed the metadata review UI + relationship
    profiler

# Design constraints

- No LLM call. Pure rules + lookups.
- Deterministic — same workbook always yields same profile (matters for
  `upload_profiles` versioning).
- Cross-table reasoning is contained to one place: bridge detection compares
  each table's column names against other tables' inferred PKs.

# Single entry point

    from multi_table_profiler import profile_multi_table
    workbook = parse_excel_all_sheets(path)         # {sheet_name: df}
    profile = profile_multi_table(workbook)         # {tables: [...], n_sheets, total_rows}

# Out of scope (per spec §6 Phase-2 boundary)

- Header row / merged-header detection — parser's job
- Composite key beyond name listing
- Self-referential relationships
- Semantic role per column — that's semantic_profiler

# Spec cross-reference

- §4 module table row: "multi_table_profiler.py — 對每張 sheet 產生 physical profile"
- §5.2 `upload_tables` doc: table_id / sheet_name / table_name / row_count /
  column_count / table_role / grain / primary_key
- §7.1 required fields: possible_primary_key / possible_total_row /
  duplicate_row_warning
- §7.2 / §8 inputs: table-level role + PK list, consumed by relationship_profiler
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

import pandas as pd

from data_profiler import profile_table

logger = logging.getLogger(__name__)


# ============================================================
# Thresholds (intentionally not config — see Rule 2)
# ============================================================
PK_DISTINCT_PCT_MIN = 0.99       # distinct/row_count >= 99% → PK candidate
PK_NULL_PCT_MAX_PCT = 1.0        # null_pct (0-100) <= 1.0 → PK candidate
BRIDGE_FK_COUNT_MIN = 2          # bridge has >= 2 FK-like columns
FACT_MEASURE_RATIO_MIN = 0.3     # >=30% non-id numeric → likely a fact

# Last-row TOTAL keywords (case-insensitive substring match against any
# string column in the final row). Bilingual because tFlex / Taiwanese
# HR data routinely uses both.
TOTAL_ROW_KEYWORDS = (
    "total", "grand total", "subtotal", "sum",
    "合計", "總計", "小計", "全部",
)


# ============================================================
# Name normalization
# ============================================================
_SAFE_NAME_RE = re.compile(r"[^a-zA-Z0-9_一-鿿]+")


def normalize_sheet_name(
    sheet_name: str,
    taken: Optional[set[str]] = None,
) -> str:
    """Normalize a sheet name into a SQL-safe table name.

    "Employee List" → "employee_list"
    "員工 清單"        → "員工_清單"        (CJK preserved)
    ""               → "sheet"

    If `taken` is given, ensures uniqueness by appending `_2`, `_3`, ...
    """
    if not sheet_name or not sheet_name.strip():
        candidate = "sheet"
    else:
        cleaned = _SAFE_NAME_RE.sub("_", sheet_name.strip())
        cleaned = cleaned.strip("_").lower()
        if not cleaned:
            candidate = "sheet"
        elif cleaned[0].isdigit():
            candidate = f"sheet_{cleaned}"
        else:
            candidate = cleaned

    if taken is None or candidate not in taken:
        return candidate

    i = 2
    while f"{candidate}_{i}" in taken:
        i += 1
    return f"{candidate}_{i}"


def make_table_id(table_name: str) -> str:
    """`employee` → `tbl_employee` per spec §5.2."""
    return f"tbl_{table_name}"


# ============================================================
# Table-level enrichments
# ============================================================
def find_primary_key_candidates(
    table_profile: dict, row_count: int,
) -> list[str]:
    """Return column names where distinct ~= row_count and null_pct ~= 0.

    A column with row_count=0 cannot be a PK (no rows to be unique over).
    """
    if row_count == 0:
        return []
    candidates: list[str] = []
    for col in table_profile.get("columns", []):
        distinct = col.get("distinct_count")
        null_pct = col.get("null_pct", 0)
        if distinct is None or distinct < 0:
            # data_profiler marks unhashable cols with distinct_count=None
            continue
        distinct_pct = distinct / row_count if row_count else 0
        if (distinct_pct >= PK_DISTINCT_PCT_MIN
                and null_pct <= PK_NULL_PCT_MAX_PCT):
            # null_pct comes from data_profiler in 0-100 scale; constant
            # is also in 0-100 scale, so compare directly.
            candidates.append(col["name"])
    return candidates


def detect_total_row(df: pd.DataFrame) -> bool:
    """Heuristic: any string column in the last row contains a TOTAL keyword.

    Examples that match:
      ['TOTAL',      450,    9.2],
      ['合計',        450,    9.2],
      ['SubTotal Q1', 450,    9.2],

    Examples that DON'T match:
      ['Sales',      'subtotaling done', 9.2]    — not last row
      [' ',          null,   null]               — no keyword
    """
    if len(df) == 0:
        return False
    last_row = df.iloc[-1]
    for val in last_row:
        if not isinstance(val, str):
            continue
        v_lower = val.lower()
        for kw in TOTAL_ROW_KEYWORDS:
            if kw in v_lower:
                return True
    return False


def count_duplicate_rows(df: pd.DataFrame) -> int:
    """Number of rows that are full duplicates of an earlier row."""
    if len(df) == 0:
        return 0
    try:
        return int(df.duplicated(keep="first").sum())
    except TypeError:
        # Columns contain unhashable values (lists, dicts) — pandas can't dedupe
        return 0


def infer_table_role(
    table_profile: dict,
    pk_candidates: list[str],
    all_table_pks: dict[str, list[str]],
    table_name: str,
) -> str:
    """Infer fact / dimension / bridge / unknown.

    Decision tree (first match wins):

      1. bridge: this table has >= 2 columns whose names match another table's
         PK candidate (and those columns are NOT this table's own PK).
         → Indicates a junction table linking two entities.

      2. fact: this table has a PK candidate AND >= 30% of non-PK non-ID
         columns are numeric measures.

      3. dimension: this table has a PK candidate but few measures (mostly
         attributes / categorical columns).

      4. unknown: no PK candidate found.

    The signals are deliberately weak — the metadata review UI lets the
    user override. False positives cost less than missing PKs entirely.
    """
    columns = table_profile.get("columns", [])
    column_names = {c["name"]: c for c in columns}

    # Count FK-like columns: this table has a column matching another table's PK.
    fk_count = 0
    for other_table, other_pks in all_table_pks.items():
        if other_table == table_name:
            continue
        for pk in other_pks:
            if pk in column_names and pk not in pk_candidates:
                fk_count += 1

    if fk_count >= BRIDGE_FK_COUNT_MIN:
        return "bridge"

    # Count measure-like columns (numeric, not identifier-shaped, not PK).
    measure_count = 0
    for c in columns:
        if c.get("physical_type") not in ("integer", "number"):
            continue
        name_lower = c["name"].lower()
        if name_lower.endswith("_id") or name_lower == "id":
            continue
        if c["name"] in pk_candidates:
            continue
        measure_count += 1

    measure_ratio = (measure_count / len(columns)) if columns else 0.0

    if pk_candidates and measure_ratio >= FACT_MEASURE_RATIO_MIN:
        return "fact"

    if pk_candidates:
        return "dimension"

    return "unknown"


def describe_grain(
    primary_key: list[str], table_name: str,
) -> Optional[str]:
    """Generate human-readable grain description.

    ["employee_id"]               → "one row per employee"
    ["order_id"]                  → "one row per order"
    ["customer_id", "product_id"] → "one row per (customer_id, product_id)"
    []                            → None
    """
    if not primary_key:
        return None
    if len(primary_key) == 1:
        pk = primary_key[0]
        # Strip common trailing modifiers for nicer phrasing.
        lower = pk.lower()
        for suffix in ("_id", "_no", "_code"):
            if lower.endswith(suffix):
                entity = pk[: -len(suffix)]
                return f"one row per {entity}"
        return f"one row per {pk}"
    return f"one row per ({', '.join(primary_key)})"


# ============================================================
# Main entry
# ============================================================
def profile_multi_table(
    workbook: dict[str, pd.DataFrame],
) -> dict[str, Any]:
    """Profile every sheet in a workbook.

    Args:
        workbook: Mapping of original sheet name → DataFrame, as returned by
                  `file_parser.parse_excel_all_sheets`.

    Returns:
        dict with keys:
          - tables: list[dict] — one record per sheet, see module docstring
                                  for full field list
          - n_sheets: int
          - total_rows: int — sum of row_count across all tables

    Empty input returns `{"tables": [], "n_sheets": 0, "total_rows": 0}`.
    """
    if not workbook:
        return {"tables": [], "n_sheets": 0, "total_rows": 0}

    # First pass: column-level profile, name normalization, per-table PK.
    # We collect a tuple per table because we need a second pass for
    # cross-table role inference (bridge detection needs other tables' PKs).
    taken_names: set[str] = set()
    first_pass: list[dict] = []
    for sheet_name, df in workbook.items():
        if df is None:
            logger.warning(f"sheet `{sheet_name}` is None, skipping")
            continue

        table_name = normalize_sheet_name(sheet_name, taken=taken_names)
        taken_names.add(table_name)
        table_id = make_table_id(table_name)

        base_profile = profile_table(df, table_id)
        pk_candidates = find_primary_key_candidates(
            base_profile, base_profile["row_count"],
        )

        # Table-level warnings.
        warnings: list[str] = []
        has_total = detect_total_row(df)
        if has_total:
            warnings.append("has_total_row")
        dup_count = count_duplicate_rows(df)
        if dup_count > 0:
            warnings.append("has_duplicates")
        if not pk_candidates:
            warnings.append("no_clear_pk")
        if base_profile["row_count"] == 0:
            warnings.append("empty_table")

        first_pass.append({
            **base_profile,
            "sheet_name": sheet_name,
            "table_name": table_name,
            "possible_primary_key": pk_candidates,
            "possible_total_row": has_total,
            "duplicate_row_count": dup_count,
            "table_warnings": warnings,
            # table_role + grain filled below
        })

    # Build the PK lookup used by bridge detection.
    all_table_pks = {
        rec["table_name"]: rec["possible_primary_key"]
        for rec in first_pass
    }

    # Second pass: cross-table role + grain.
    for rec in first_pass:
        rec["table_role"] = infer_table_role(
            rec,
            rec["possible_primary_key"],
            all_table_pks,
            rec["table_name"],
        )
        rec["grain"] = describe_grain(
            rec["possible_primary_key"], rec["table_name"],
        )

    return {
        "tables": first_pass,
        "n_sheets": len(first_pass),
        "total_rows": sum(t["row_count"] for t in first_pass),
    }
