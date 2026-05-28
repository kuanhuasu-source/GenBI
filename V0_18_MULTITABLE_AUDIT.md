# v0.18 Multi-table Upgrade · Gap Audit

**Date:** 2026-05-28
**Spec reference:** `GenBI_Upload_Workspace_MultiTable_Upgrade_Spec.pdf` (v0.1 draft, 2026-05-28)
**Prior audit / plan:** `V0_17_SPEC_AUDIT.md`, `V0_18_SPRINT_PLAN.md` (both target the older single-table spec `GenBI_Upload_Workspace_System_Extension_Spec_v0.2.pdf`)
**Posture:** Reuse-and-extend. Existing single-table modules are the foundation; multi-table is a layer above, not a rewrite.

---

## TL;DR

The repo is much closer to the multi-table spec than the page count suggests. Five of the spec's ten core modules already exist as standalone files (some shipped in v0.15 marked `M5.*`), and four more already exist but are wired single-table only. The real work is **enabling, persisting, and reviewing** multi-table state, not building it from scratch.

Rough split of effort vs spec M1-M7:

| Bucket | Items | Where the work concentrates |
|---|---|---|
| Already done (just needs a flip / wiring) | ~25% | M1 parser, M3 metadata versioning skeleton, M7 safety primitives |
| Existing module needs multi-table extension | ~35% | M1 profiler, M2 review UI, M4 DuckDB integration, M5 analysis_steps |
| Missing module / collection / test surface | ~40% | M2 relationship persistence, M5 step service, M6 derived-table assets, M7 regression gate script |

Recommended ordering at the bottom of this doc.

---

## 1. Architecture coverage (spec §3)

### §3.1 Two-path architecture

| Path | Status |
|---|---|
| Schema-driven main path (app.py / pages/main_chat.py / test_runner baseline) | ✅ untouched — v0.17 refactor preserved it |
| Upload-driven path (Upload Workspace → metadata review → UploadAnalysisService → Pandas exec → saved assets) | ✅ exists as single-table; needs multi-table extension |

### §3.2-§3.4 Principles

| Principle | Status | Note |
|---|---|---|
| §3.2 Metadata First (no guessing from sample rows) | ✅ enforced | `data_profiler.py` runs before any LLM call |
| §3.3 HITL required (grain / PK / aggregation rule / relationship / status code / PII) | ⚠️ partial | Single-table HITL exists in `pages/07_data_workspace.py`; relationship review UI missing |
| §3.4 Structural Defense First (validator + safe exec + fallback) | ✅ in place | `safe_exec.py`, `phase_a_validator.py`, Phase B/C retry + table fallback |

---

## 2. Module map (spec §4)

| Spec module | Status | Current file / line | Gap |
|---|---|---|---|
| `file_parser.py` (multi-sheet parser) | ✅ **exists** | `file_parser.py:172` `parse_excel_all_sheets()` (v0.15.0 M5.1) | None — function already returns `{sheet_name: df}` with empty-sheet skip + warning |
| `multi_table_profiler.py` (sheet profile + role) | ❌ **missing as named file** | `data_profiler.py` + `semantic_profiler.py` cover single-table fields | Need thin wrapper that iterates sheets and adds `table_role`, `grain`, `primary_key` per table |
| `relationship_profiler.py` | ⚠️ **exists, formula differs from spec** | `relationship_profiler.py:153` `detect_relationships()` | Confidence is `0.85 base + boost/penalty` heuristic; spec §8.1 wants `0.25·name + 0.20·type + 0.30·overlap + 0.20·uniqueness − penalties` weighted score. No PK-side `source_unique_ratio` or `null_ratio_penalty`. Many-to-many guardrail (§8.2) absent. |
| `upload_metadata_generator.py` | ⚠️ **exists, single-table** | `upload_metadata_generator.py` (495 LoC) | Iterates one table; needs multi-table loop + relationship section in output metadata |
| `metadata_correction_service.py` | ✅ exists | `metadata_correction_service.py` (227 LoC) | Already writes corrections + audit; verify it covers relationship edit ops |
| `upload_repository.py` | ⚠️ **6 of 7 collections exist** | 7 constants at `upload_repository.py:87-93` | Missing `upload_relationship_candidates` (§5.3) and `analysis_steps` (§5.4); currently has `analysis_sessions` + `analysis_assets` instead |
| `duckdb_engine.py` | ✅ **exists** | `duckdb_engine.py` (245 LoC, v0.15.0 M5.5) | SQL guardrails match spec §11.3 (forbidden `INSERT/UPDATE/DELETE/DROP/ATTACH/COPY/EXPORT/INSTALL/LOAD/PRAGMA`). Missing `register_dataset(dataset_id, tables: dict)` convenience + `allowed_tables` enforcement against confirmed relationships |
| `analysis_step_service.py` | ❌ **missing** | — | New: persists step lineage, derived-table state, enables rerun |
| `upload_analysis_service.py` | ⚠️ **exists, single-table** | `upload_analysis_service.py:378` `table = tables[0]  # MVP single-table` | Needs Phase 0 to produce action plan (§10.1), Phase A to dispatch to Pandas or DuckDB, lineage write |
| `pages/08_dataset_metadata_builder.py` | ❌ **missing as named page** | Logic merged into `pages/07_data_workspace.py` (single-table iteration only at line 446 `metadata["collections"].keys())[0]`) | Spec wants a dedicated page so upload / review / analysis don't blur. v0.17 already partially split (07 vs 08); spec wants the metadata builder split out further. |
| `pages/09_analysis_workspace.py` | ❌ **missing as named page** | `pages/08_data_analysis.py` exists | Current page is single-table chat; spec wants action-plan-driven loop with `extract_data` / `join_tables` / `add_column` / `aggregate` / `create_table` / `visualize` / `generate_insight` / `save_asset` |
| `scripts/run_regression_gate.py` | ❌ **missing** | — | Spec §14.4 — unified entry running py_compile + unit tests + upload acceptance + multi-table acceptance + schema-driven baseline + golden tests + safety tests |

**Net:** 5 modules fully exist, 4 need multi-table extension, 4 are missing (incl. 2 pages).

---

## 3. Data model (spec §5)

Constants live in `upload_repository.py:87-93`.

| Spec collection | Status | Repo equivalent |
|---|---|---|
| §5.1 `uploaded_datasets` | ✅ exists | `DEFAULT_DATASETS_COLLECTION = "uploaded_datasets"` |
| §5.2 `upload_tables` | ✅ exists | `DEFAULT_TABLES_COLLECTION = "upload_tables"` — schema includes `table_id`, `sheet_name`, `storage_ref`, but missing spec fields: `table_role`, `grain`, `primary_key` (currently lives in metadata not table doc) |
| §5.3 `upload_relationship_candidates` | ❌ **missing** | Relationships are derived live by `relationship_profiler.detect_relationships()`, not persisted. No `status: candidate/confirmed/rejected/edited` field. |
| §5.4 `analysis_steps` | ❌ **missing** | Have `analysis_sessions` + `analysis_assets` (asset-level), but no step-level `session_id` / `metadata_version` / `step_no` / `action_type` / `generated_code` / `output_table` lineage record |
| (bonus) `upload_profiles`, `upload_metadata_versions`, `upload_user_corrections` | ✅ exist | All useful, none conflict with spec |

Migration impact: 2 new collections, +3 fields on `upload_tables`, +1 field on `analysis_assets` (`source_step_ids` per §27).

---

## 4. Per-section coverage (spec §6 - §16)

### §6 Multi-sheet parser
- `parse_excel_all_sheets()` already: reads all visible sheets ✅, skips empty ✅, normalized table names ✅ (via `normalize_dataframe_columns`), `max_sheets` protection ✅, no LLM call ✅, `NO_VALID_SHEET` error ✅ (`FileParseError`).
- **Gap:** header row detection / merged-header warning — spec §6 says "detect, warn, don't over-guess." Current parser uses pandas default `header=0`.
- **Gap:** `upload_service.handle_upload(excel_multi_sheet=...)` flag defaults `False`. Spec wants multi-sheet ON for all Excel uploads.

### §7 Profiler
`data_profiler.py` covers most of §7.1. Cross-check:

| §7.1 required field | Status |
|---|---|
| row_count / column_count | ✅ |
| inferred physical type | ✅ |
| null_count / null_ratio | ✅ |
| distinct_count / distinct_ratio | ✅ |
| min / max for numeric and datetime | ⚠️ numeric yes, datetime not explicit |
| sample values | ✅ |
| possible primary key | ⚠️ only via `suspect_id` warning, not a dedicated field |
| possible total row | ❌ marked "future enhancement" in `data_profiler.py:30` |
| duplicate row warning | ❌ not a warning type |
| high cardinality text warning | ✅ `high_cardinality` + `suspect_id` |
| date parse failure warning | ❌ not explicit |

§7.2 semantic roles in `semantic_profiler.py` — verify coverage of all 13 roles (`identifier / foreign_key / dimension / measure / date / datetime / categorical_status / free_text / boolean / percentage / currency / quantity / unknown`).

**§7 Hard rule:** "identifier/foreign_key default_aggregation must be `none`" — needs explicit validator check in metadata confirmation (`pages/07` confirm button + `metadata_correction_service`).

### §8 Relationship profiler
- Current confidence formula (`relationship_profiler.py:73-118`): name-match base 0.85 + overlap boost / penalty.
- Spec §8.1 formula: `score = 0.25*name + 0.20*type + 0.30*overlap + 0.20*uniqueness − penalties` with status tiers `>=0.90 high / 0.70-0.89 review / 0.50-0.69 weak / <0.50 ignore`.
- Spec §8.2 type inference (one_to_one / many_to_one / one_to_many / many_to_many_candidate based on source/target uniqueness). Current code emits `many_to_one` for everything (`relationship_profiler.py:22-23` literally says `# MVP 統一 m2o`).
- **Spec §8 guardrail:** many-to-many candidates must NOT auto-join — currently not blocked because m2m isn't detected.
- **§8 evidence dict:** current code returns `name_match / value_overlap_pct / left_distinct / right_distinct / right_is_pk`; spec wants `name_similarity / type_compatible / from_to_overlap_ratio / to_unique_ratio / sample_match_count`. Overlap is similar but field names differ.

### §9 Metadata Review UI
| Subsection | Status |
|---|---|
| Dataset Overview / Sheet Review / Field Review / Data Limitation Review | ✅ in `pages/07_data_workspace.py` for single table |
| Relationship Review (§9.1: candidate table, evidence, confirm/reject/edit/manual add) | ❌ no UI block |
| Metadata Diff | ⚠️ basic version diff exists in `upload_metadata_versions`; no UI viewer |
| Confirm Metadata button behavior (§9.2: validate, ensure ≥1 table, no aggregatable identifiers, compatible relationship fields, new version, status=confirmed, set active_metadata_version) | ⚠️ partial — version write + status update exist; identifier-no-agg + relationship-field-compatible validations missing |

### §10 Interactive Analysis Workspace
- Currently `pages/08_data_analysis.py` (chat UI + progressive phase render from v0.17). Has phase callbacks ✅.
- **Gap:** spec wants a **structured action plan** flow with 8 action types (`inspect_table / extract_data / join_tables / add_column / aggregate / create_table / visualize / generate_insight / save_asset`). Current flow does `extract_data → preprocess → visualize → insight` in one shot — no explicit action loop, no `join_tables` action, no `create_table`.
- **Spec §10.1 Agent output schema:** `{"status": "ok", "actions": [...]}` — no equivalent in `llm_service`.

### §11 Execution engine
- §11.1 Pandas MVP: covered — `upload_analysis_service` does select / filter / merge (single-table) / calc col / groupby / pivot / return Q. **Gap:** "safe merge by confirmed relationship" — current path doesn't gate join on confirmed relationship.
- §11.2 DuckDB engine: ✅ `duckdb_engine.py` has `execute_safe(sql, allowed_tables)` and `register_parquet`. **Gap:** no `register_dataset(dataset_id, tables: dict[str, str])` convenience and no caller from `upload_analysis_service`.
- §11.3 SQL guardrails: ✅ all blacklisted keywords present (`duckdb_engine.py:77-89`), SELECT-only enforced, query timeout configured.

### §12 Upload-driven prompt / agent workflow
- ✅ `llm_service.py:1908` `phase_0_plan_upload` prompt key exists; inline default at `:2097`.
- ✅ `llm_service.py:2148` `generate_pandas_extraction()` exists.
- ❌ `generate_duckdb_sql()` missing.
- ⚠️ **Spec hard rule "Do not call generate_pipeline for upload-driven dataset"** — needs explicit guard in `upload_analysis_service` (currently relies on `is_upload` flag at prompt selection only).
- ⚠️ Phase 0 plan needs to be able to emit `need_metadata_confirmation` status if user hasn't confirmed metadata yet.

### §13 RAG / Prompt context
- §13 suggests 3 new indices: `upload_table_profile_index`, `upload_relationship_index`, `upload_derived_asset_index`.
- **Status:** none of the three exist. `scripts/build_rag_indices.py` builds `schema_index`, `kpi_index`, `few_shot_index`, `anti_pattern_index`, `chart_recipe_index` only.
- Spec says "critical rules hard-code, others retrieve" — for multi-table this matters because workbook metadata can blow past the prompt budget. Without these indices, Phase 0 would have to stuff full sheet profiles into the prompt.

### §14 Regression
- §14.1 Frozen baseline rule: schema-driven path must not be modified. Currently respected — upload path is fully separate from `main_chat.py` / `test_runner.py`. Verify no shared mutable state when adding multi-table work.
- §14.2 Required regression commands: all four currently work today (`py_compile`, `pytest tests/unit/`, `pytest tests/acceptance/`, `test_runner.py --domain tflex`). RAG variants too. ✅
- §14.3 Baseline pass gate: enforced manually; needs to become a CI / regression-gate-script check.
- §14.4 `scripts/run_regression_gate.py`: ❌ missing.
- §14.5 Multi-table acceptance tests (12 listed): **0 of 12 exist.** `tests/acceptance/` only has `test_mvp_acceptance.py`. No `test_multisheet_*` files.
- §14.6 Regression anti-pattern tests (10 listed): not implemented as a dedicated test file; some are partially covered by existing unit tests.

### §15 Safety / Governance
- `safe_exec.py` exists ✅ (restricted builtins, timeout)
- `pii_detector.py` exists ✅
- File-size limit + allowed extensions + SHA256 dedup: in `upload_service.py` (verify limits configurable).
- **Gap:** no "no formulas/macros executed" check — pandas read_excel doesn't execute macros by default; should add explicit warning if `.xlsm` uploaded.

### §16 Observability / Debug Panel
- `task_traces` collection ✅ (every phase + LLM call recorded)
- `pages/05_task_traces.py` viewer ✅
- **Gap:** spec wants debug panel to show `dataset_id / metadata_version / confirmed status / table profiles / relationship candidates / user corrections / current analysis session / analysis steps / generated SQL / row counts before-after join / fallback reason / chart option / LLM call summary / regression trace id` — many of these need the new `analysis_steps` collection to exist first.

---

## 5. Milestone breakdown (spec §17)

| Milestone | Spec deliverables | Audit status | Estimated work |
|---|---|---|---|
| **M1** Multi-sheet Parser + Profile | `file_parser.py`, parquet staging, `multi_table_profiler.py`, profile UI, unit tests | 60% — parser done, profiler-per-table wrapper + UI iteration missing | S (1-2d) |
| **M2** Relationship Profiler + Review UI | `relationship_profiler.py`, relationship_candidates collection, confirm/reject/edit/manual-add UI | 30% — profiler exists with wrong formula, no persistence, no UI | M (3-5d) |
| **M3** Multi-table Metadata Versioning | metadata generator, validator, versioning, correction audit, confirmed badge | 70% — versioning + correction audit exist; needs multi-table extension + validator for spec §7 hard rule | S-M (2-3d) |
| **M4** DuckDB Multi-table Analysis | `duckdb_engine.py`, safe SQL validator, confirmed join path, `UploadAnalysisService` integration | 60% — engine + guardrails done; integration + join-via-confirmed-relationship missing | M (3-4d) |
| **M5** Interactive Analysis Steps | `analysis_steps` collection, derived tables, add_column / aggregate / create_table / rerun / lineage | 10% — `analysis_assets` exists but step-level lineage doesn't | M-L (4-6d) |
| **M6** Assets 2.0 | Save Derived Table / Join View / Analysis Template, metadata-drift warning | 40% — single-table saved-chart / metric / template exist; derived-table + join-view missing | M (3-4d) |
| **M7** Full Regression Gate | `run_regression_gate.py`, golden tests (6 files per Appendix A), frozen baseline tests, CI checklist | 5% — none of the regression gate script / golden tests / multi-table acceptance tests exist | M (3-4d) |

**Total estimate:** ~20-28 dev-days, single contributor.

---

## 6. Recommended sprint ordering

Constraint: the spec §14 frozen-baseline clause says no multi-table change ships without M7 regression evidence. So M7 needs to be **alive enough to gate** before later milestones merge, even if not feature-complete.

Suggested order:

1. **M1 + M3 together** (small) — multi-sheet parser flip + per-table profile + per-table metadata + identifier-no-agg validator. Easy win, unblocks everything. ~3-4d.
2. **M7 scaffold** — `scripts/run_regression_gate.py` skeleton + 2-3 of the 12 acceptance tests (`test_multisheet_1_parse_all_sheets`, `test_multisheet_2_profile_each_sheet`, `test_multisheet_12_schema_driven_baseline_unchanged`). Just enough that subsequent PRs have a gate to fail. ~2d.
3. **M2** — relationship profiler formula rewrite + `upload_relationship_candidates` collection + Relationship Review UI. Adds the highest-risk new HITL surface. ~4-5d.
4. **M4** — DuckDB integration into `upload_analysis_service`. Behind a feature flag. ~3-4d.
5. **M5** — `analysis_step_service.py` + step lineage. Unlocks M6 derived tables. ~4-6d.
6. **M6** — Assets 2.0 (derived table, join view, drift warning). ~3-4d.
7. **M7 completion** — backfill remaining acceptance tests + golden test fixtures (Appendix A: 6 xlsx files). ~2d.

Critical path: M1 → M3 → M2 → M4. M5/M6/M7-completion can parallelize once M4 is in.

---

## 7. Reconciliation with existing `V0_18_SPRINT_PLAN.md`

The existing `V0_18_SPRINT_PLAN.md` (dated today) targets the **older** single-table spec. It plans three small sprints:

- **Sprint C** (3-4h) — `rerun_with_metadata_version()`, Saved Assets link, doc renames.
- **Sprint D** (unread but referenced) — likely Phase 3 enterprise features.
- Doc hygiene.

Recommendation: **fold Sprint C into M3** (metadata versioning milestone). The `rerun_with_metadata_version()` method is exactly the kind of versioning op M3 needs anyway, and the doc hygiene work pairs naturally with M1-M3 documentation updates. Don't ship Sprint C as its own v0.17.1 — the multi-table spec changes enough that it's wasted release overhead.

Sprint D / Phase 3 enterprise features were already out-of-scope per the older spec (§4.3) and remain out-of-scope per the new spec §2 ("不在本次範圍"). Defer indefinitely.

---

## 8. Risks & open questions

1. **Spec §8.1 confidence formula vs. current code** — switching formulas may break existing relationship suggestions in any test fixtures that exist. Verify before changing.
2. **`analysis_sessions` vs `analysis_steps`** — these are different shapes. Decision needed: extend `analysis_sessions` with an embedded steps array, or create a new `analysis_steps` collection per spec §5.4. Spec implies separate collection; check whether that creates a 2-write transactional concern.
3. **Page renumbering** — spec wants `pages/08_dataset_metadata_builder.py` and `pages/09_analysis_workspace.py`. Current uses `08_data_analysis.py` and `09_saved_assets.py`. Renumbering breaks v0.17 `st.navigation()` registry in `app.py`. Need to either rename + update nav, or use new numbers (10/11) and let names drift from spec.
4. **RAG indices (§13)** — building 3 new indices is part of the spec's "建議" (suggestion) not hard requirement. Could defer to a v0.18.x patch if prompt-size pressure isn't observed in M1-M4 testing.
5. **Golden test xlsx fixtures (Appendix A)** — 6 files need to be authored/found. Cost depends on whether realistic HR / project / orders data is available or has to be synthesized.

---

## Appendix · Verified file references

Every claim above is grounded in one of:
- `file_parser.py:172` `parse_excel_all_sheets`
- `relationship_profiler.py:73-118` confidence formula, `:153` entry, `:22-23` "MVP 統一 m2o"
- `upload_repository.py:87-93` collection constants
- `upload_analysis_service.py:378` `tables[0]  # MVP single-table`
- `pages/07_data_workspace.py:9,121,264,446` single-sheet copy + "Phase 2" comments
- `llm_service.py:1908,1954,2097,2148` upload prompt key + `generate_pandas_extraction`
- `duckdb_engine.py:77-89` forbidden SQL keywords
- `data_profiler.py:24-32` warning list (incl. `# future enhancement` for suspect_total_row)
- `scripts/build_rag_indices.py` no `upload_*_index` builders
- `tests/acceptance/` contains only `test_mvp_acceptance.py`
