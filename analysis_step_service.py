"""
analysis_step_service.py — v0.18 M5

Interactive analysis step orchestrator per spec §10 + §5.4.

# Overview

The Upload-driven analysis path runs as a sequence of discrete steps,
each persisted as an `analysis_steps` row (spec §5.4). Steps are
chainable — later steps reference earlier steps' `output_table`. The
service:

  1. dispatches `add_step(action_type, params)` to the right handler,
  2. persists a step record to MongoDB,
  3. writes the derived DataFrame to a parquet file alongside the source
     uploads,
  4. exposes `resolve_table(name)` which walks BOTH source tables AND
     prior step outputs so subsequent steps can reference either.

# Action types (spec §10)

| action_type    | params                                                        |
|----------------|---------------------------------------------------------------|
| extract_data   | {input_table, filters?: list[{column, op, value}]}            |
| add_column     | {input_table, new_column, formula} — formula via df.eval()    |
| aggregate      | {input_table, group_by: list[str], aggregations: list}        |
| create_table   | {input_table, new_name}                                       |
| visualize      | {input_table, chart_type, x?, y?, series?, options?}          |

# Pure functions vs. side-effect entrypoints

Action handlers are pure `_handle_*(df, params) -> df` functions —
they take a DataFrame, return a derived DataFrame, do NO I/O. The
side-effects (parquet write, step doc insert) live in `add_step`.

This separation makes the handlers trivially unit-testable and means
`rerun_step` (a follow-up PR) can re-execute by calling the same
handlers without DB churn.

# Out of scope (deferred per Rule 2 simplicity)

- `rerun_step` — re-execute a step against latest source data
- Branching / forking step history (linear chain only for M5)
- Cross-session step references
- LLM-generated action plans (M4 Tier B / M2-Tier-B territory)
- Streamlit UI (`pages/09_analysis_workspace.py` — separate PR)

# Spec cross-refs

- §5.4 analysis_steps doc schema
- §10 Interactive Analysis Workspace flow
- §10.1 Agent output schema (the action plan that drives `add_step` calls)
- §11.1 Pandas MVP execution path (this is where it lives)
- §17 M5 milestone
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd

import file_parser
from upload_repository import UploadRepository

logger = logging.getLogger(__name__)


# ============================================================
# Configuration
# ============================================================
_VALID_ACTION_TYPES = frozenset({
    "extract_data", "add_column", "aggregate", "create_table",
    "visualize", "insight",
})

# Aggregations allowed in `aggregate` action params.
_VALID_AGGREGATIONS = frozenset({
    "sum", "mean", "median", "min", "max",
    "count", "count_distinct", "first", "last",
})

# Row-count safety cap for derived tables. Subsequent Phase B is also
# capped, but this stops a runaway groupby from filling disk.
_DERIVED_TABLE_ROW_LIMIT = 1_000_000


def generate_step_id() -> str:
    """Format: step_<14ts>_<6hex>. Sortable + unique."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"step_{ts}_{secrets.token_hex(3)}"


def generate_session_id() -> str:
    """Format: sess_<14ts>_<6hex>."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"sess_{ts}_{secrets.token_hex(3)}"


# ============================================================
# Pure action handlers — no I/O, no DB
# ============================================================
def _handle_extract_data(
    df: pd.DataFrame, params: dict,
) -> pd.DataFrame:
    """Extract rows from a source table, optionally filtered.

    params:
        filters: list[{column, op, value}] — AND-combined
            op ∈ {==, !=, >, <, >=, <=, in, not_in, isnull, notnull}
    """
    filters = params.get("filters") or []
    if not filters:
        return df.copy()
    mask = pd.Series([True] * len(df), index=df.index)
    for f in filters:
        col = f.get("column")
        op = f.get("op")
        val = f.get("value")
        if col not in df.columns:
            raise ValueError(
                f"extract_data: filter column `{col}` not in input"
            )
        s = df[col]
        if op == "==":
            mask &= (s == val)
        elif op == "!=":
            mask &= (s != val)
        elif op == ">":
            mask &= (s > val)
        elif op == "<":
            mask &= (s < val)
        elif op == ">=":
            mask &= (s >= val)
        elif op == "<=":
            mask &= (s <= val)
        elif op == "in":
            mask &= s.isin(val)
        elif op == "not_in":
            mask &= ~s.isin(val)
        elif op == "isnull":
            mask &= s.isna()
        elif op == "notnull":
            mask &= s.notna()
        else:
            raise ValueError(f"extract_data: unknown filter op `{op}`")
    return df[mask].copy()


def _handle_add_column(
    df: pd.DataFrame, params: dict,
) -> pd.DataFrame:
    """Add one calculated column via pandas df.eval().

    params:
        new_column: str — destination column name
        formula:    str — pandas eval expression, e.g. "salary * 12"
                          (references other columns by name)

    Uses df.eval() with `engine='python'` (slower but safer than
    numexpr; we accept the trade-off because typical derived columns
    are small relative to original data).

    df.eval() can NOT call arbitrary functions or imports — only
    arithmetic + comparison + a small set of pandas-known functions
    (abs, sin, etc). This is what the spec means by "safe" in §15.
    """
    new_col = params.get("new_column")
    formula = params.get("formula")
    if not new_col or not isinstance(new_col, str):
        raise ValueError("add_column: `new_column` (str) required")
    if not formula or not isinstance(formula, str):
        raise ValueError("add_column: `formula` (str) required")
    if new_col in df.columns:
        raise ValueError(
            f"add_column: column `{new_col}` already exists; "
            f"use a different name or aggregate first"
        )
    try:
        result = df.eval(formula, engine="python")
    except Exception as e:
        raise ValueError(
            f"add_column: pandas eval failed on formula `{formula}`: "
            f"{type(e).__name__}: {e}"
        ) from e
    out = df.copy()
    out[new_col] = result
    return out


def _handle_aggregate(
    df: pd.DataFrame, params: dict,
) -> pd.DataFrame:
    """Aggregate via pandas groupby.

    params:
        group_by:     list[str] — grouping columns (may be empty for
                                  whole-table aggregation)
        aggregations: list[{column, function, alias?}] — output cols

    Returns:
        DataFrame with one row per group (or one row total when
        group_by is empty), columns = group_by + each alias.
    """
    group_by = params.get("group_by") or []
    aggs = params.get("aggregations") or []
    if not aggs:
        raise ValueError("aggregate: at least one aggregation required")
    for g in group_by:
        if g not in df.columns:
            raise ValueError(f"aggregate: group_by col `{g}` not in input")
    for a in aggs:
        col = a.get("column")
        fn = a.get("function")
        if col not in df.columns:
            raise ValueError(f"aggregate: agg col `{col}` not in input")
        if fn not in _VALID_AGGREGATIONS:
            raise ValueError(
                f"aggregate: function `{fn}` not allowed; "
                f"valid: {sorted(_VALID_AGGREGATIONS)}"
            )

    if not group_by:
        # Whole-table aggregation → one row.
        row: dict[str, Any] = {}
        for a in aggs:
            col, fn = a["column"], a["function"]
            alias = a.get("alias") or f"{fn}_{col}"
            row[alias] = _apply_agg(df[col], fn)
        return pd.DataFrame([row])

    grouped = df.groupby(group_by, dropna=False)
    pieces: dict[str, pd.Series] = {}
    for a in aggs:
        col, fn = a["column"], a["function"]
        alias = a.get("alias") or f"{fn}_{col}"
        pieces[alias] = _apply_grouped_agg(grouped[col], fn)
    out = pd.concat(pieces, axis=1).reset_index()
    return out


def _apply_agg(s: pd.Series, fn: str) -> Any:
    if fn == "sum":             return s.sum()
    if fn == "mean":            return s.mean()
    if fn == "median":          return s.median()
    if fn == "min":             return s.min()
    if fn == "max":             return s.max()
    if fn == "count":           return int(s.count())
    if fn == "count_distinct":  return int(s.nunique(dropna=True))
    if fn == "first":           return s.iloc[0] if len(s) else None
    if fn == "last":            return s.iloc[-1] if len(s) else None
    raise ValueError(f"unknown agg fn `{fn}`")


def _apply_grouped_agg(g, fn: str) -> pd.Series:
    if fn == "sum":             return g.sum()
    if fn == "mean":            return g.mean()
    if fn == "median":          return g.median()
    if fn == "min":             return g.min()
    if fn == "max":             return g.max()
    if fn == "count":           return g.count()
    if fn == "count_distinct":  return g.nunique(dropna=True)
    if fn == "first":           return g.first()
    if fn == "last":            return g.last()
    raise ValueError(f"unknown agg fn `{fn}`")


def _handle_create_table(
    df: pd.DataFrame, params: dict,
) -> pd.DataFrame:
    """Rename an existing derived/source df to a new logical name.

    No data transformation — just a registration step so downstream
    steps can reference the new name. params:
        new_name: str — must be a valid identifier
    """
    new_name = params.get("new_name")
    if not new_name or not isinstance(new_name, str):
        raise ValueError("create_table: `new_name` (str) required")
    return df.copy()


def _handle_visualize(
    df: pd.DataFrame, params: dict,
) -> pd.DataFrame:
    """Visualize action — records a chart spec; pass-through on df.

    The actual rendering happens in M6 (Saved Chart). For M5 we just
    persist the chart spec on the step doc so the UI / asset layer
    can pick it up later.

    params validation:
        chart_type: str — required, e.g. 'bar' / 'line' / 'pie' / 'table'
        x:          str — optional column reference
        y:          str | list[str] — optional column reference(s)
    """
    chart_type = params.get("chart_type")
    if not chart_type or not isinstance(chart_type, str):
        raise ValueError("visualize: `chart_type` (str) required")
    for axis in ("x", "y"):
        val = params.get(axis)
        if val is None:
            continue
        if isinstance(val, str):
            if val not in df.columns:
                raise ValueError(
                    f"visualize: {axis}=`{val}` not in input columns"
                )
        elif isinstance(val, list):
            for v in val:
                if v not in df.columns:
                    raise ValueError(
                        f"visualize: {axis} element `{v}` not in input"
                    )
        else:
            raise ValueError(
                f"visualize: {axis} must be str or list[str]"
            )
    # Pass through — visualize doesn't transform data.
    return df.copy()


# ============================================================
# Service
# ============================================================
class AnalysisStepService:
    """Drives the interactive analysis flow per spec §10.

    Lifecycle:
        svc = AnalysisStepService(upload_repo, uploads_root)
        session_id = svc.create_session(dataset_id, owner="alice")
        s1 = svc.add_step(
            session_id, action_type="extract_data",
            params={"input_table": "employee"},
            user_query="show employee rows",
        )
        s2 = svc.add_step(
            session_id, action_type="add_column",
            params={"input_table": s1["output_table"],
                    "new_column": "tenure_yr",
                    "formula": "years_employed"},
        )
        # Resolve any registered name to a DataFrame:
        df = svc.resolve_table(session_id, s2["output_table"])
    """

    def __init__(
        self,
        upload_repo: UploadRepository,
        uploads_root: Path,
    ):
        self.repo = upload_repo
        self.uploads_root = Path(uploads_root)

    # --------------------------------------------------------
    # Session management
    # --------------------------------------------------------
    def create_session(
        self,
        dataset_id: str,
        owner: str = "anonymous",
    ) -> str:
        """Create a new analysis_sessions row for this dataset.

        Delegates to UploadRepository.create_session which generates its
        own session_id and pins the session to whatever metadata_version
        is currently active on the dataset (lineage anchor per spec §5.4).
        """
        dataset = self.repo.get_dataset(dataset_id)
        if not dataset:
            raise ValueError(
                f"create_session: dataset `{dataset_id}` not found"
            )
        metadata_version = dataset.get("active_metadata_version") or 1
        return self.repo.create_session(
            dataset_id=dataset_id,
            metadata_version=metadata_version,
            user=owner,
        )

    # --------------------------------------------------------
    # Step dispatch
    # --------------------------------------------------------
    def add_step(
        self,
        session_id: str,
        action_type: str,
        params: dict,
        user_query: str = "",
    ) -> dict:
        """Run one action + persist the step doc + return the step.

        Returns:
            The full step doc (including output_table name, row_count,
            generated_code, status='completed' or 'failed').
        """
        if action_type not in _VALID_ACTION_TYPES:
            raise ValueError(
                f"add_step: action_type `{action_type}` not in "
                f"{sorted(_VALID_ACTION_TYPES)}"
            )
        params = dict(params or {})
        input_table = params.get("input_table")
        if not input_table:
            raise ValueError("add_step: `params.input_table` required")

        # 1. Resolve input
        input_df = self.resolve_table(session_id, input_table)

        # 2. Dispatch
        step_id = generate_step_id()
        step_no = self.repo.next_step_no(session_id)
        session = self.repo.get_session(session_id)
        if not session:
            raise ValueError(f"session `{session_id}` not found")
        dataset_id = session["dataset_id"]

        handler = {
            "extract_data": _handle_extract_data,
            "add_column":   _handle_add_column,
            "aggregate":    _handle_aggregate,
            "create_table": _handle_create_table,
            "visualize":    _handle_visualize,
            "insight":      lambda df, p: df.copy(),  # passthrough — see M6
        }[action_type]

        # 3. Decide the output_table name. For non-create_table actions
        # it's `<step_id>` (anonymous derived); for create_table the
        # user-supplied name wins so later steps can reference it
        # mnemonically.
        output_name = (
            params["new_name"] if action_type == "create_table"
            and params.get("new_name")
            else step_id
        )

        status = "completed"
        error_message: Optional[str] = None
        row_count = 0
        storage_path: Optional[str] = None
        output_schema: list[dict] = []

        try:
            out_df = handler(input_df, params)
            if len(out_df) > _DERIVED_TABLE_ROW_LIMIT:
                logger.warning(
                    f"step {step_id} produced {len(out_df)} rows — "
                    f"truncating to {_DERIVED_TABLE_ROW_LIMIT}"
                )
                out_df = out_df.head(_DERIVED_TABLE_ROW_LIMIT)
            row_count = int(len(out_df))
            output_schema = self._schema_for_df(out_df)
            # visualize doesn't materialize — chart spec lives on the
            # step doc itself, the "output_table" is just the input
            # passed through.
            if action_type != "visualize":
                storage_path = self._write_derived_parquet(
                    dataset_id, step_id, out_df,
                )
        except Exception as e:
            status = "failed"
            error_message = f"{type(e).__name__}: {e}"
            logger.warning(
                f"step {step_id} ({action_type}) failed: {error_message}"
            )

        step_doc: dict[str, Any] = {
            "step_id": step_id,
            "session_id": session_id,
            "dataset_id": dataset_id,
            "metadata_version": session.get("metadata_version"),
            "step_no": step_no,
            "action_type": action_type,
            "user_query": user_query,
            "input_tables": [input_table],
            "output_table": output_name,
            "params": params,
            "output_schema": output_schema,
            "row_count": row_count,
            "status": status,
        }
        if storage_path:
            step_doc["storage"] = {"format": "parquet",
                                    "path": storage_path}
        if action_type == "visualize":
            step_doc["chart_spec"] = {
                k: params.get(k)
                for k in ("chart_type", "x", "y", "series", "options")
                if params.get(k) is not None
            }
        if error_message:
            step_doc["error_message"] = error_message

        self.repo.save_analysis_step(step_doc)
        return step_doc

    # --------------------------------------------------------
    # Table resolution (lineage walk)
    # --------------------------------------------------------
    def resolve_table(
        self,
        session_id: str,
        table_name: str,
    ) -> pd.DataFrame:
        """Look up a table by name within a session.

        Resolution order:
          1. Prior step output (most recent step where
             output_table == table_name and status == 'completed').
          2. Source upload_tables row matching `table_name` either by
             `table_id` (exact) or `table_name` field (original sheet).

        Raises:
            ValueError if neither resolution succeeds.
        """
        session = self.repo.get_session(session_id)
        if not session:
            raise ValueError(f"session `{session_id}` not found")
        dataset_id = session["dataset_id"]

        # 1. Try derived (latest-step-wins for same name).
        steps = self.repo.list_analysis_steps(session_id, status="completed")
        # walk reversed → most recent first
        for s in reversed(steps):
            if (s.get("output_table") == table_name
                    and s.get("storage", {}).get("path")):
                return file_parser.load_parquet(s["storage"]["path"])

        # 2. Try source upload_tables.
        source_tables = self.repo.list_tables(dataset_id)
        for t in source_tables:
            if (t.get("table_id") == table_name
                    or t.get("table_name") == table_name):
                return file_parser.load_parquet(t["storage"]["path"])

        raise ValueError(
            f"resolve_table: name `{table_name}` not found among "
            f"derived tables nor source upload_tables (dataset "
            f"`{dataset_id}`)"
        )

    # --------------------------------------------------------
    # Helpers
    # --------------------------------------------------------
    def _schema_for_df(self, df: pd.DataFrame) -> list[dict]:
        return [
            {"name": str(c), "dtype": str(df[c].dtype)}
            for c in df.columns
        ]

    def _write_derived_parquet(
        self,
        dataset_id: str,
        step_id: str,
        df: pd.DataFrame,
    ) -> str:
        out_dir = self.uploads_root / dataset_id / "derived"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{step_id}.parquet"
        df.to_parquet(path, index=False)
        return str(path)
