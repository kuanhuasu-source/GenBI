# v0.17 Implementation Audit · vs. `GenBI_Upload_Workspace_System_Extension_Spec_v0.2.pdf`

**Date:** 2026-05-28
**Subject:** Does the v0.17 UI refactor still satisfy the original spec?
**TL;DR:** ✅ Yes — and v0.17 split actually realises §6.1 architecture more faithfully than v0.12-v0.16 did. **Zero new divergences introduced.** The 3 minor gaps were all pre-existing in v0.16.

---

## 1. Architecture-level finding

§6.1 高階架構 lists these UI surfaces as **separate**:

```
- Upload page
- Data understanding review page
- Chat analysis workspace
- Saved assets panel
- Debug / execution log
```

| Spec surface | v0.16 implementation | v0.17 implementation | Verdict |
|---|---|---|---|
| Upload page | merged into `07_upload_workspace.py` §1-2 | `07_data_workspace.py` §1-2 | aligned |
| Data understanding review page | merged into `07_upload_workspace.py` §3-9 | `07_data_workspace.py` §3-9 | aligned |
| Chat analysis workspace | merged into `07_upload_workspace.py` §10 | **`08_data_analysis.py` §10** (separate page) | **v0.17 more aligned** |
| Saved assets panel | `08_saved_assets.py` | `09_saved_assets.py` (renamed) | aligned |
| Debug / execution log | merged into `07_upload_workspace.py` §12 | `08_data_analysis.py` §12 | aligned |

v0.16 collapsed three of the five spec surfaces into one 1608-line page. v0.17 separates "Chat analysis workspace" into its own page — closer to spec intent.

---

## 2. Per-section satisfaction (spec §10 / §11 / §12 / §14 / §16)

### §10 Metadata Review UI

| Requirement | Where | Status |
|---|---|---|
| §10.1 File upload panel | `07_data_workspace.py` §1 | ✅ |
| §10.1 Parse preview | `07_data_workspace.py` §3 (sample data) | ✅ |
| §10.1 Data profile summary | `07_data_workspace.py` §3 (column profile) | ✅ |
| §10.1 AI understanding review | `07_data_workspace.py` §4 | ✅ |
| §10.1 Field editor | `07_data_workspace.py` §5 | ✅ |
| §10.1 Data limitation editor | `07_data_workspace.py` §8 | ✅ |
| §10.1 Confirm metadata button | `07_data_workspace.py` §9 | ✅ |
| §10.1 Start analysis button | `07_data_workspace.py` tail · "→ 開始分析" (v0.17 NEW) | ✅ |
| §10.1 Saved Assets entry | (none on Upload page — reach via sidebar nav) | ⚠️ pre-existing gap |
| §10.2 Field Review Table | `07_data_workspace.py` §5 (`data_editor`) | ✅ |
| §10.3 Status Code Editor | `07_data_workspace.py` §7 | ✅ |
| §10.4 Grain Confirmation | `07_data_workspace.py` §6 | ✅ |
| §10.5 Data Limitation Confirmation | `07_data_workspace.py` §8 | ✅ |
| §10.6 Metadata Confirmation Status badge | `07_data_workspace.py` §4 (✅ Confirmed / ⚠️ Unconfirmed) | ✅ |

### §10.7 Acceptance criteria (8 items)

| # | Criterion | v0.17 location | Status |
|---|---|---|---|
| 1 | Dataset summary + columns + sample values | 07 §3 | ✅ |
| 2 | Every field has physical_type + semantic_role + description + confidence | 07 §5 | ✅ |
| 3 | User can edit semantic_role / description / unit / default_aggregation | 07 §5 | ✅ |
| 4 | User can confirm grain | 07 §6 | ✅ |
| 5 | Confirm Metadata → active version | 07 §9 | ✅ |
| 6 | Unconfirmed metadata blocks analysis with prompt | **08 has defensive `md_status != "confirmed"` gate AND 07 button only enabled when confirmed** | ✅ (now 2 layers) |
| 7 | identifier 不可 sum / avg | Phase B prompt + `phase_b_validator` | ✅ (unchanged) |
| 8 | No date column → trend refuse | data_limitations refusal | ✅ (unchanged) |

### §11 Agent Workflow

§11.1 enumerates: Pre-Phase U0 / Phase U1 / Phase 0 / A / B / C / D / E (Asset Save).
v0.17 changes nothing in service logic — `handle_query` runs the same 5 phases, only adds an **optional** `on_phase` callback. When `on_phase=None` (default), behavior is byte-equal v0.16. ✅

§11.2-§11.4 Phase A/B/C prompt rules — unchanged. ✅

### §12 API/Service interfaces

| Spec class.method | Actual code | Notes |
|---|---|---|
| `UploadService.upload_file` | `UploadService.handle_upload` | pre-existing name divergence |
| `UploadService.parse_file` | `upload_service._handle_upload_async` | internal step |
| `DataProfiler.profile_table` | `data_profiler.DataProfiler.profile_dataframe` | pre-existing name divergence |
| `SemanticProfiler.infer_columns` | `semantic_profiler.SemanticProfiler.infer_*` | aligned |
| `UploadMetadataGenerator.generate_metadata` | exists | ✅ |
| `UploadRepository.{create_dataset,get_dataset,...}` | exists | ✅ |
| `UploadAnalysisService.start_session` | exists (+ extra `metadata_version` arg) | ✅ |
| `UploadAnalysisService.run_query` | **`handle_query`** in actual code | ⚠️ pre-existing name divergence |
| `UploadAnalysisService.rerun_with_metadata_version` | **not implemented** | ⚠️ pre-existing gap |
| `AnalysisAssetService.{save_chart,save_metric,save_template,...}` | exists | ✅ |

### §12A Analysis Asset (9 acceptance criteria)

All ✅ — none touched by v0.17.

### §14 Safety

| § | Requirement | Status |
|---|---|---|
| §14.1 #1 100MB upload limit | ✅ |
| §14.1 #3 SHA256 of file | ✅ (in `uploaded_datasets.file.sha256`) |
| §14.2 Code execution sandbox | ✅ (`safe_exec_pandas`) |
| §14.3 PII detection | ✅ (`pii_detector.py` + `data_profiler.py` integration) |
| §14.4 30-day retention | (policy doc, not code-enforced — pre-existing) |

### §15 Observability / Debug

UI debug panel migrated to `08_data_analysis.py` §12 (was in 07 §12). All 6 debug tabs preserved (Dataset/Session, Metadata history, Last analysis trace, Assets summary, Relationships, System status). ✅

### §16 測試規格

§16.4 Acceptance criteria (10 items) — `tests/acceptance/test_mvp_acceptance.py` (13 tests, all ✅).
§16.5 MVP 新增驗收標準 (10 items) — covered, none affected by v0.17.

---

## 3. Pre-existing divergences (NOT introduced by v0.17)

These are honest gaps between spec and code that existed in v0.12-v0.16 and remain in v0.17. Worth tracking but not blockers:

1. **Page path numbering** — spec proposes `05_upload_workspace.py / 06_upload_datasets.py / 07_saved_assets.py`. Actual landed at `07/08/09` because `05_task_traces.py` and `06_learning_review.py` already occupy 05/06 (noted in README §"Page 編號說明").
2. **Saved Assets entry on Upload Workspace** (§10.1) — spec lists this as a block on Upload Workspace page; actual provides access only via sidebar nav. v0.16 same situation.
3. **Method names** — spec `UploadAnalysisService.run_query`; code `handle_query`. Spec `UploadService.upload_file`; code `handle_upload`. Spec `DataProfiler.profile_table`; code `profile_dataframe`. All pre-existing.
4. **`rerun_with_metadata_version` method missing** — spec §12.6 lists it; not implemented. v0.16 same gap.

---

## 4. What v0.17 added beyond spec (positive additions)

1. **Progressive phase callback** — `handle_query(on_phase=callback)`. Spec didn't propose this; it's a UX improvement enabled by Streamlit's container model. Doesn't contradict §11 Agent Workflow.
2. **st.navigation() sidebar groups** — 📊 分析工作區 / ⚙️ 系統管理. Not in spec but improves §6.1 surface separation.
3. **Cross-page `analysis_dataset_id` session state** — natural consequence of the page split.

---

## 5. Recommendations (optional, not blockers)

| # | Item | Priority | Effort |
|---|---|---|---|
| R1 | Update spec to v0.3 reflecting the page rename (`05/06/07 → 07/08/09`) and the new chat-analysis-as-separate-page architecture | low | 1h (annotate spec PDF or maintain markdown companion) |
| R2 | Add a "→ Saved Assets" link on `07_data_workspace.py` (close §10.1 spec gap that's been open since v0.12) | low | 5 min |
| R3 | Implement `UploadAnalysisService.rerun_with_metadata_version(session_id, version)` — used by Saved Assets rerun flow | medium | 1-2h (currently rerun uses replay via session_state, not a service method) |
| R4 | Rename `handle_query` → `run_query` to match spec, OR update spec §12.6 to match code | low | trivial (need to grep callers) |

---

## 6. Bottom line

v0.17 introduces **no new spec divergences** and **clarifies the §6.1 surface separation** the spec called for from day one. All four pre-existing gaps predate this release and are unchanged by it. The acceptance suite (`tests/acceptance/test_mvp_acceptance.py`, 13 tests) continues to pass at 482 total unit+integration tests.

Safe to consider v0.17 release-ready against this spec.
