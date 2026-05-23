# ACCEPTANCE.md — Upload Workspace MVP

對齊 `GenBI_Upload_Workspace_System_Extension_Spec_v0.2.pdf` §16.4 / §16.5。
分自動驗證(`tests/acceptance/`)+ 手動 UI 驗證兩塊。

## 1. 自動驗收(13 條 — 跑 `pytest tests/acceptance/ -v`)

| # | Spec 對應 | Test                                            | Status |
|---|---|-------------------------------------------------|---|
| 1 | §16.4 #1 | test_acceptance_1_upload_csv                    | 自動 |
| 2 | §16.4 #2 | test_acceptance_2_profile_and_semantic          | 自動 |
| 3 | §16.4 #4 | test_acceptance_3_metadata_versioning           | 自動 |
| 4 | §6.3 + §11.2 | test_acceptance_4_source_type_upload        | 自動 |
| 5 | §14.1 | test_acceptance_5_file_size_limit              | 自動 |
| 5 | §14.2 | test_acceptance_5_forbidden_builtins           | 自動 |
| 5 | §14.2 | test_acceptance_5_phase_a_blocks_open          | 自動 |
| 6 | §12A.7 #2 | test_acceptance_6_saved_chart_lineage         | 自動 |
| 7 | §12A.7 #5 | test_acceptance_7_saved_metric_writeback      | 自動 |
| 8 | §12A.7 #8 | test_acceptance_8_asset_lineage_binding       | 自動 |
| 9 | §14.3 | test_acceptance_9_pii_marked                   | 自動 |
| 10 | §14.2 | test_acceptance_10_safe_exec_wired            | 自動 |
| 11 | §10.7 #6 | test_acceptance_11_unconfirmed_metadata_blocks_analysis | 自動 |
| 12 | §10.7 #7 | test_acceptance_12_identifier_not_sum         | 自動 |

## 2. 手動驗收(`streamlit run app.py` 看 UI)

### Upload Workspace page

- [ ] **§10.1 file upload widget** — 拖拉 CSV / Excel 上傳順
- [ ] **§10.2 Field Review Table** — 表格 editable,改 semantic_role 後 default_aggregation 連動
- [ ] **§10.3 Status Code Editor** — categorical_status 欄展開可編 allowed_values description
- [ ] **§10.4 Grain Confirmation** — radio 選 primary key + grain text
- [ ] **§10.5 Data Limitation Editor** — text_area 可加 / 刪 missing_dimensions
- [ ] **§10.6 Metadata Confirmation Status** — Active version 顯示 ✅ Confirmed / ⚠️ Unconfirmed badge
- [ ] **§10.7 Unconfirmed badge** — 未確認時 Section 10 Chat 該擋

### Chat Analysis flow(Section 10)

- [ ] **§16.5 #1 dataset summary 顯示** — 看到欄位列表 + sample values
- [ ] **§16.5 #2 confidence 顯示** — 每欄位 confidence ProgressColumn
- [ ] **§16.5 #4 Confirm metadata 按鈕** — 按下後 active version 升,badge 變 ✅
- [ ] **chart 渲染** — bar / pie / histogram + markLine 各種 query 都不炸
- [ ] **table fallback** — Phase C 3 次失敗時降級表格(看 banner 提示)
- [ ] **insight 顯示** — Phase D 訊息出現在 expander
- [ ] **follow-up** — 第二輪 query 用「改成 X」之類詞,該帶 last_analysis 脈絡

### Saved Assets page

- [ ] **§16.5 #5 Save Chart** — 跑完分析按按鈕 → Saved Charts tab 看到
- [ ] **§16.5 #6 重新打開 Saved Chart** — View detail 看到 chart 重渲
- [ ] **§16.5 #7 Save Metric** — 寫回 kpi_definitions(切回 Metadata Review 看到新 KPI)
- [ ] **§16.5 #8 後續分析引用 KPI** — 新 query 提到該 KPI 名稱,LLM prompt 看得到
- [ ] **§16.5 #9 Rerun** — 點 Rerun 把 query 推進 replay queue
- [ ] **Drift warning** — 舊 asset(metadata_version 過時)顯示 ⚠️ stale badge
- [ ] **Rename / Delete** — 都生效

### Debug Panel(Section 12)

- [ ] **Tab 1 Dataset / Session** — 顯示 dataset_id / metadata_version / status
- [ ] **Tab 2 Metadata history** — 列出每個 version + user corrections audit
- [ ] **Tab 3 Last analysis trace** — Phase output 大小 / Q info / fallback reason
- [ ] **Tab 4 Assets summary** — Total / by type / 最近 10 個 asset
- [ ] **Tab 5 System status** — Safety limits / LLM config / Collection counts

### Schema-driven 主路徑(凍結驗證)

- [ ] **`streamlit run app.py` 主對話**(`Active Domain` 選 tflex) — 跑老 query 行為跟之前一致
- [ ] **`python test_runner.py`** — pass 數落在 17-21 噪聲帶

## 3. v0.14.x 整體 commit / tag 軌跡

| Version | Milestone | 主要內容 |
|---|---|---|
| v0.14.0 | M4a | Golden datasets + 165 unit tests |
| v0.14.1 | M4b | PII detector + safe_exec module + integration tests(230 pass)|
| v0.14.2 | M4c | safe_exec 整進 Phase A/B + Debug Panel + Acceptance suite |
