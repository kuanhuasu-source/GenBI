# Sprint Plan · Closing the Remaining Spec Gaps (post-v0.17)

**Date:** 2026-05-28
**Spec reference:** `GenBI_Upload_Workspace_System_Extension_Spec_v0.2.pdf`
**Audit source:** `V0_17_SPEC_AUDIT.md`
**Status:** Plan locked in; sprints not yet started.

---

## 0. What's left

Per the v0.17 audit, MVP + Phase 2 spec sections (§1 - §16) are essentially done. What remains:

1. **4 pre-existing gaps from audit** (R1-R4) — small, tactical
2. **Phase 3 enterprise features** (§4.3) — large, strategic
3. **Documentation hygiene** — spec PDF still says `pages/05_upload_workspace.py` etc.

Below: three candidate sprints. Each is self-contained, with section boundaries, acceptance criteria, and effort estimates. Pick which (or all) to run.

---

## Sprint C · v0.17.1 patch · Audit gap closure (3-4h)

**Goal:** Close the 4 audit gaps so the spec and code line up. Single small release.

### C1 · `UploadAnalysisService.rerun_with_metadata_version()` (§12.6) · 1-2h

**Why missing:** spec §12.6 lists this method; never implemented. Currently the Saved Assets page "Rerun" button just stuffs the source query into session_state and tells the user to paste it into chat — that violates the spec promise of "asset rerun bound to metadata_version".

**Implementation:**

```python
# upload_analysis_service.py
def rerun_with_metadata_version(
    self,
    session_id: str,
    metadata_version: int,
    on_phase: Optional[PhaseCallback] = None,
) -> dict:
    """重新執行 session.last_analysis.query 但用指定 metadata version。

    用於 Saved Asset rerun + drift compare 流程。
    """
    session = self.repo.get_session(session_id)
    if not session:
        return self._error_result(f"Session `{session_id}` 不存在")
    last = session.get("last_analysis")
    if not last or not last.get("query"):
        return self._error_result("Session 沒有 last_analysis 可重跑")
    # Switch active metadata version 暫時(transactional)
    dataset_id = session["dataset_id"]
    original_v = self.repo.get_active_metadata(dataset_id)["version"]
    try:
        if metadata_version != original_v:
            self.repo.activate_metadata_version(dataset_id, metadata_version)
        return self.handle_query(
            session_id=session_id,
            query=last["query"],
            chart_engine=last.get("chart_engine", "ECharts"),
            on_phase=on_phase,
        )
    finally:
        if metadata_version != original_v:
            self.repo.activate_metadata_version(dataset_id, original_v)
```

**Wire on Saved Assets page** (`pages/09_saved_assets.py`):
- Existing Rerun button currently writes to `st.session_state["_replay_query"]` — replace with a direct call to `analysis_service.rerun_with_metadata_version(session_id, asset.metadata_version)` and `st.switch_page("pages/08_data_analysis.py")`.

**Tests:**
- `test_rerun_with_metadata_version_switches_then_restores` — assert active version restored after call
- `test_rerun_with_missing_last_analysis_returns_error`

### C2 · Saved Assets link on Data Workspace page (§10.1) · 5min

**Why missing:** spec §10.1 lists "Saved Assets entry" as a block on Upload Workspace. Pre-v0.17 had no such link; v0.17 has analysis button but not Saved Assets entry.

**Implementation:** Add a third button alongside "→ 開始分析" in `pages/07_data_workspace.py` tail:

```python
col_a, col_b = st.columns(2)
with col_a:
    if st.button("📊 開始分析此資料集 →", type="primary"):
        st.session_state["analysis_dataset_id"] = selected_id
        st.switch_page("pages/08_data_analysis.py")
with col_b:
    if st.button("⭐ 查看已存圖表 →", type="secondary"):
        st.session_state["assets_filter_dataset_id"] = selected_id
        st.switch_page("pages/09_saved_assets.py")
```

Then on 09 page, read `assets_filter_dataset_id` as default filter.

**Tests:** manual smoke only.

### C3 · Method rename decision (§12.6) · 30min

**Decision:** Keep code names (`handle_query`, `handle_upload`, `profile_dataframe`) and update spec §12.6 to match. Rationale: code is shipped, has tests, used by 2 pages; spec is a draft.

**Implementation:**
- Edit spec PDF? Hard. Create `SPEC_V0.3_ADDENDUM.md` instead, noting:
  - `UploadAnalysisService.run_query` → actual name `handle_query`
  - `UploadService.upload_file` → actual name `handle_upload`
  - `DataProfiler.profile_table` → actual name `profile_dataframe`
- Update README `📜 文件` section to add the addendum.

**Tests:** none.

### C4 · Spec page numbering correction (§7) · 15min

**Implementation:** in `SPEC_V0.3_ADDENDUM.md`, document:

```
spec §7 module table  →  actual paths
pages/05_upload_workspace.py  →  pages/07_data_workspace.py (sections 1-9) +
                                  pages/08_data_analysis.py (sections 10-12)
pages/06_upload_datasets.py   →  merged into 07_data_workspace.py §2
pages/07_saved_assets.py      →  pages/09_saved_assets.py
```

### C · Acceptance criteria

- [ ] `UploadAnalysisService.rerun_with_metadata_version()` exists and tests pass
- [ ] Saved Assets page Rerun button calls it (no longer "paste into chat" guidance)
- [ ] `pages/07_data_workspace.py` has Saved Assets link button
- [ ] `SPEC_V0.3_ADDENDUM.md` exists and is linked from README
- [ ] 482+ tests still pass; 2 new test count
- [ ] CHANGELOG v0.17.1 entry added
- [ ] Tag v0.17.1 created

**Total effort:** 3-4 hours · v0.17.1 patch

---

## Sprint D · v0.18.0 · Enterprise hardening (permissions + retention + PII)

**Goal:** Pick the 3 Phase 3 features that unblock real-world enterprise deployment without committing to dashboard/team-workspace complexity. Roughly equivalent to bringing the upload workspace to "internal pilot ready".

**Phase 3 items addressed:** §4.3 #2 (PII strengthening), #3 (permissions + retention)

### D1 · Permissions model (~6h)

**Why:** Currently `_upload_owner` field is just a typed-in string; anyone can see anyone's data.

**Scope:**
- `upload_repository`: every list_* method gains `viewer_user` arg; filter by `owner == viewer_user` OR `shared_with` includes viewer.
- New collection `dataset_permissions`: `{dataset_id, user, role: "owner" | "viewer", granted_at, granted_by}`
- UI: Streamlit auth (st.user, or pinned `_current_user` from sidebar input). Sidebar shows current user; pages 07/08/09 only list datasets the current user can see.

**Out of scope:** SSO, SAML, fine-grained column-level perms.

**Tests:** 4-5 unit tests for permission filter, 1 integration test.

### D2 · Retention policy enforcement (~3h)

**Why:** Spec §14.4 says 30-day retention for upload + parquet but it's only doc-policy now.

**Scope:**
- New module `retention_cleaner.py` with `def run_cleanup(repo, uploads_root, retention_days=30) -> dict`
- Cron-style script `scripts/cleanup_expired_uploads.py` that:
  - Deletes parquet files older than 30 days
  - Marks dataset `status="expired"` but keeps metadata + corrections + assets (per spec — "long-term retention")
  - Returns audit log
- UI: page 07 §2 shows "expired" status for old datasets; reload offered if file still on disk.
- Operations doc in `SELF_LEARNING_OPS.md` for cron setup.

**Tests:** 3 unit tests, 1 integration test.

### D3 · PII detection strengthening (~4h)

**Why:** Spec §4.3 #2 and §14.3 — current `pii_detector.py` handles basic patterns. Strengthen to:

**Scope:**
- Add national-ID patterns for TW / US (regex with checksum)
- Add credit-card Luhn validation
- Add Chinese name detection via character-set heuristic
- UI: page 07 §5 Field Review Table — if `semantic_role=="pii"`, add red-bordered row + 「⚠️ PII 偵測,prompt 不會顯示原值」 caption
- `llm_service`: when building prompts, mask PII columns' sample values as `<redacted>` (already partial behavior — needs explicit check)

**Tests:** 6-8 unit tests for new pattern matchers; 1 integration test confirming masked sample values reach the LLM.

### D · Acceptance criteria

- [ ] Page 07/08/09 only list datasets the current user owns/shares
- [ ] `scripts/cleanup_expired_uploads.py` runs via cron, deletes >30day parquet, logs to `retention_audit` collection
- [ ] PII columns mask sample values in LLM prompts; field review shows red badge
- [ ] All existing 482 tests pass; ~15 new tests
- [ ] CHANGELOG v0.18.0 entry; tag v0.18.0

**Total effort:** ~13h · v0.18.0 minor

---

## Sprint E · v0.19.0+ · Full Phase 3 — Collaborative workspace

**Goal:** Spec §4.3 #1, #4-#10. Multi-tenant, scheduled, polished.

This is multi-week scope. Plan-only; recommend NOT starting unless D ships first and gets pilot user feedback.

### E1 · Data catalog integration (#1) · 1-2 weeks

Connect to external catalog (Atlas, DataHub) so confirmed metadata can be published as a domain dataset upstream.

### E2 · Profile approval workflow (#4) · 1 week

Currently `Confirm Metadata` is single-user. Add review state: `draft → pending → approved`. Reviewer not owner.

### E3 · Dataset sharing (#5) · already covered partially in D1, add link sharing here

UI: "Share with..." dialog. URL with read token. Streamlit auth integration for view-only mode.

### E4 · Scheduled refresh (#6) · 1 week

For datasets backed by a stable source (e.g. S3 URL), periodic re-upload → re-profile → new metadata version. New collection `refresh_jobs`.

### E5 · Report template generation (#7) · 1 week

Turn an Analysis Template into a PDF / HTML report skeleton. Auto-runs the template on the latest data + adds chart + insight + audit trail.

### E6 · Dashboard publish (#8) · 2-3 weeks

The Looker Studio-style dashboard canvas explicitly deferred in spec §1.2. Multi-chart layout, save / share / export.

### E7 · Team Workspace (#9) · 2 weeks

Shared dataset library per team. Roles: admin/member/viewer. Activity feed.

### E8 · Scheduled PDF/CSV delivery (#10) · 1 week

Email or Slack delivery of saved chart / template runs on a schedule.

### E · Acceptance criteria

Per-item; this sprint is more accurately a multi-release roadmap. Defer detailed planning until D is shipped.

**Total effort:** ~10-12 weeks across 3-4 minor releases (v0.19, v0.20, v1.0)

---

## Recommended ordering

```
v0.17.0  ───────── 已 ship · UI refactor
   │
   ▼
v0.17.1  ───────── Sprint C · 3-4h · audit gap close
   │               (低風險、立刻可做、清掉技術債)
   │
   ▼
v0.18.0  ───────── Sprint D · ~13h · enterprise hardening
   │               (permissions + retention + PII)
   │               → 推 pilot user 上線
   │
   ▼
[使用者 feedback]
   │
   ▼
v0.19.x  ───────── Sprint E · multi-week
                   (按 pilot 回饋 prioritize E1-E8 子項)
```

---

## Risk register

| Risk | Severity | Mitigation |
|---|---|---|
| Sprint C R3 (rerun_with_metadata_version) — temporary activation race condition if two users hit the same dataset | M | Use per-session view of metadata version, don't actually switch active globally. Wrap in try/finally as drafted. |
| Sprint D D1 — Streamlit auth APIs aren't great; may need cookie + custom session | M | Start with pinned username in sidebar; SSO later. |
| Sprint D D2 — cron orchestration on macOS dev vs Linux prod | L | Both supported; doc both setups in SELF_LEARNING_OPS.md |
| Sprint E E6 (dashboard publish) — large scope, unclear UX | H | Defer; spec also defers. Don't start E6 without dedicated design pass. |

---

## Decision log

| 議題 | 選 | 為什麼 |
|---|---|---|
| Sprint C 是否在 v0.17.1 一次包?| 是 | 4 個小 item 都互相獨立、各 30min-2h,合一 release 比拆 4 個快 |
| Phase 3 是否一次做完?| 否 | E6 / E7 是 multi-week design 工作,沒 pilot 用戶需求不該先做 |
| Sprint D 選哪 3 個 Phase 3 item?| Permissions + Retention + PII | 都是 internal pilot blocker;Dashboard / Team Workspace 是 nice-to-have |
| `handle_query` 改名 `run_query`?| 否,改 spec | 程式碼 shipped + tested,改名 churn 大 |

---

## Execution checklist for whoever takes this on

```
☐ Sprint C (v0.17.1 patch)
   ☐ Read upload_analysis_service.py to confirm handle_query signature
   ☐ Implement rerun_with_metadata_version()
       ☐ Add unit test test_rerun_with_metadata_version_switches_then_restores
       ☐ Add unit test test_rerun_with_missing_last_analysis_returns_error
   ☐ Wire Saved Assets page Rerun button to new method
   ☐ Add Saved Assets link button on pages/07_data_workspace.py
   ☐ Write SPEC_V0.3_ADDENDUM.md (method name reconciliation + page renumbering)
   ☐ Update README "📜 文件" section
   ☐ CHANGELOG v0.17.1 entry
   ☐ Tag v0.17.1

☐ Sprint D (v0.18.0 enterprise hardening)
   ☐ D1 Permissions
       ☐ Add dataset_permissions collection schema
       ☐ Wrap repo.list_* with viewer_user filter
       ☐ Sidebar current-user input + session_state
       ☐ 4-5 unit tests
   ☐ D2 Retention
       ☐ retention_cleaner.py module
       ☐ scripts/cleanup_expired_uploads.py
       ☐ Page 07 §2 expired status display
       ☐ SELF_LEARNING_OPS.md cron doc
   ☐ D3 PII strengthening
       ☐ Add TW / US national-ID regex with checksum
       ☐ Credit-card Luhn check
       ☐ Chinese name char-set heuristic
       ☐ Page 07 §5 PII red badge
       ☐ LLM prompt masking integration test
   ☐ CHANGELOG v0.18.0 entry; tag

☐ Sprint E (v0.19+ collaborative · plan-only for now)
   ☐ Wait for pilot feedback from v0.18
   ☐ Prioritize E1-E8 based on actual user requests
   ☐ Detailed planning for top 2-3 items only
```
