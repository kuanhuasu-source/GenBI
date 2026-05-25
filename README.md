# GenBI — From Question to Chart in Seconds

> 一個 **schema-driven** 的自然語言 BI 系統。HR / 業務 / 任何非工程師都能用對話形式探索資料,看圖看 insight,不寫 SQL。

![logo](assets/genbi_logo.svg)

## ✨ 核心特性

- 🗣️ **對話式 BI**:中英文都行,例如「比較各公司退單率與申請數」「畫一張熱力圖看各部門分佈」
- 🧠 **5-Phase Agentic Workflow**:Plan → MongoDB Pipeline → Pandas → ECharts → Insight
- 🌐 **多 Domain 切換**:Sidebar 一鍵切換 tflex / ecommerce / healthcare(v0.3.0+),confirm dialog 防誤觸
- 🗂️ **內容外部化(v0.3.0+)**:prompts / metadata / test cases / test runs 全進 MongoDB,線上 UI 編輯不用 redeploy
- 📊 **ECharts + Plotly 雙引擎**:Sidebar 可切換,真實 BI 視覺體驗
- 💎 **精美表格 + KPI 卡片**:dashboard 場景自動降級為 `st.metric` + `ProgressColumn` 漸層條
- 🛡️ **三道結構性防禦**:`sanitize_pipeline` / `rescue_empty_echarts` / `ensure_default_styling` 攔住 LLM 常見出包
- ⛔ **Schema-driven refusal**:LLM 從 metadata 推理該不該拒絕,不靠 hardcoded 關鍵詞
- 📈 **Baseline + 跑分追蹤**:每次 test_runner 自動寫 `test_runs`,可 vs baseline 對比 pass rate / token
- 📤 **Upload Workspace(v0.12+)**:BYOD 第二條路徑 — 上傳 CSV/Excel/Parquet → 自動 profile + 語意推理 → 對話分析,不必先建 schema
- 🔍 **RAG Dynamic Prompt(v0.16+)**:5 個 specialized vector index 動態剪裁 prompt,Sprint 2 champion 上線 +11.5pp pass-rate,-10% cost/success;env flag 即可關回 v0.15 byte-equal

## 🏗️ 架構

```
┌───────────────────────────────────────────────────────────┐
│ Layer 1: System Code (domain-agnostic)                     │
│   - llm_service.py / app.py / repositories                 │
│   - upload_*.py        (BYOD path, v0.12+)                  │
│   - retrieval_orchestrator.py (RAG, v0.16+)                 │
└───────────────────────┬───────────────────────────────────┘
                        │ injects via Repository / Orchestrator
                        ▼
┌───────────────────────────────────────────────────────────┐
│ Layer 2: MongoDB (live content, editable via admin UI)     │
│   - prompt_templates    (5 phase prompts, Jinja2)           │
│   - domain_metadata     (schema / KPI / 限制)               │
│   - upload_* (7 colls)  (datasets / profiles / metadata)    │
│   - test_cases          (per-domain test definitions)       │
│   - test_runs           (歷史 baseline + 跑分快照,含 rag_*) │
│   - rag_index_versions  (champion / challenger 紀錄)        │
│   - learning_* (3 colls)(self-learning observations…)       │
└───────────────────────┬───────────────────────────────────┘
                        │ 60s cache + embedded fallback (緊急救援)
                        ▼
┌───────────────────────────────────────────────────────────┐
│ Layer 2.5: RAG Vector Indices (Chroma embedded, v0.16+)    │
│   - schema_index / kpi_index / few_shot_index               │
│   - anti_pattern_index / chart_recipe_index                 │
│   - sentence-transformers/all-MiniLM-L6-v2 (384-dim, CPU)   │
└───────────────────────┬───────────────────────────────────┘
                        │ slot injection per phase
                        ▼
┌───────────────────────────────────────────────────────────┐
│ Layer 3: LLM (Qwen 3 Coder / vLLM / Ollama / OpenAI)        │
└───────────────────────────────────────────────────────────┘
```

## 🚀 快速啟動

### 前置需求

- macOS / Linux,Python 3.10+
- MongoDB 7+(也可 CSV fallback dev mode)
- 任一個 OpenAI-compatible LLM endpoint:
  - **Ollama**(本機推薦):`ollama pull qwen3-coder:30b`
  - **vLLM**(production):`Qwen2.5-Coder-32B-Instruct-AWQ` on A100
  - **OpenAI / Anthropic-proxy API**(雲端)

### 安裝步驟

```bash
# 1. 建立 venv 並安裝
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. 設定環境變數
cp .env.example .env
# 編輯 .env(預設 ollama profile,可不改)

# 3. 安裝 MongoDB + 匯入示例資料 (macOS Homebrew)
bash setup_mongodb.sh

# 4. (v0.3.0+) 把 prompts / metadata / test cases seed 進 DB
python migrations/001_seed_prompts.py
python migrations/002_seed_metadata.py
python migrations/003_seed_test_cases.py

# 5. 啟用 repo 模式(.env 加一行 或 export)
echo "GENBI_PROMPT_REPO=true" >> .env

# 6. (v0.16.0+ optional) 啟用 RAG dynamic prompt(+11.5pp pass-rate)
# 第一次跑會下載 sentence-transformers/all-MiniLM-L6-v2(~90MB)
python scripts/build_rag_indices.py --full-rebuild --domain tflex
echo "GENBI_RAG_ENABLED=true" >> .env

# 7. 啟動 Streamlit UI
streamlit run app.py
```

打開瀏覽器 `http://localhost:8501` 開始對話。

### 不裝 MongoDB 也能跑(dev mode)

Sidebar 切到 `CSV fallback (dev)`,系統會把 `data/*.csv` 載入 pandas 模擬 MongoDB 查詢。Pipeline 的 `$match` / `$project` 會被解譯到 pandas 上,Phase B/C/D 完全照常運作。

Prompts / metadata / test cases 在 DB 沒接時自動回退到 `embedded_*.py` 副本,**系統不會死**。

## 🖥️ 七個 Admin Page(Streamlit multi-page)

啟動 `streamlit run app.py` 後 sidebar 自動列出:

| Page | 功能 |
|---|---|
| **主頁** | 對話式 BI(chat input + 5-phase workflow + Current Question 醒目橫條 + sidebar domain switcher)|
| **🧪 Test Cases** (`01_test_cases.py`)| Test case CRUD + tag 篩選 + activate/deactivate |
| **📊 Test Runs** (`02_test_runs.py`)| 歷史 runs(per domain)+ baseline mark + side-by-side compare |
| **📝 Prompts** (`03_prompts.py`)| 5 phase × domain 的 Jinja2 模板編輯 + Render preview + 版本啟用 |
| **🗂️ Metadata** (`04_metadata.py`)| Per-domain metadata JSON 編輯 + 新增 domain 精靈 + 版本歷史 |
| **🔍 Task Traces** (`05_task_traces.py`, v0.7.0+)| 每次 query 完整 phase + LLM call 記錄 |
| **🧠 Learning Review** (`06_learning_review.py`, v0.9.0+)| Self-learning admin:metric dashboard / candidate review / contradiction review / observation browser |

## 📁 專案結構

```
GenBI/
├── app.py                         # Streamlit 主入口 (5-phase workflow + sidebar domain switcher)
├── llm_service.py                 # LLM service + 5 phase prompts(走 repo,inline fallback)
├── prompt_repository.py           # Prompt + Metadata MongoDB repo (60s cache + embedded fallback)
├── test_case_repository.py        # Test case CRUD repo
├── test_run_repository.py         # Test run save + baseline + compare
├── embedded_prompts.py            # 5 phase Jinja2 templates (絕對 fallback)
├── embedded_metadata.py           # tflex production metadata fallback
├── embedded_test_cases.py         # 26 tflex test cases fallback
├── tflex_task_metadata_agent_v3.py  # tFlex domain metadata 原始檔
├── pages/
│   ├── 01_test_cases.py
│   ├── 02_test_runs.py
│   ├── 03_prompts.py
│   └── 04_metadata.py
├── migrations/
│   ├── 001_seed_prompts.py
│   ├── 002_seed_metadata.py
│   └── 003_seed_test_cases.py
├── admin/
│   ├── list_prompts.py
│   ├── list_test_runs.py
│   ├── mark_baseline.py
│   └── compare_baseline.py
├── assets/genbi_logo.svg          # 廚師 logo
├── test_runner.py                 # 26 case headless 回歸 + 寫 test_runs
├── test_generality.py             # 多 domain 通用性測試
├── config.py                      # 統一管理 LLM / MongoDB / Repo 設定
├── requirements.txt
├── .env.example
└── data/                          # tFlex 合成原始資料
```

## 🌐 新增一個 Domain

不用寫 code 了(v0.3.0+):

**方法 A · UI 介面**:
1. 啟動 Streamlit → 點 `🗂️ Metadata` 頁
2. Sidebar 選 `➕ 新增 domain...`
3. 填 domain name 跟 metadata JSON(template 已給)→ 一鍵建立
4. Sidebar 切到新 domain → 開始對話

**方法 B · CLI**:
1. 寫 metadata 檔(參考 `tflex_task_metadata_agent_v3.py`)
2. import 進 `embedded_metadata.py`
3. `python migrations/002_seed_metadata.py --domain your_domain`

## 🧪 測試

```bash
# 跑當前 domain 全部 cases + 寫 test_runs collection + 標 baseline
python test_runner.py --baseline

# 只跑 STK 系列
python test_runner.py --filter STK

# 跑指定 case
python test_runner.py --only 03,STK-04

# 切 domain 跑(等該 domain 有 cases 後)
python test_runner.py --domain ecommerce

# 看歷史 runs
python admin/list_test_runs.py

# 比較最新 vs baseline
python admin/compare_baseline.py
```

每次測試會自動:
- 寫一筆紀錄到 `test_runs` collection(含 prompts + metadata 版本快照、git_commit、token 用量)
- 本地產 `test_results.md` / `test_results.json`(向下相容)
- 印速覽 + Cost Summary(3 家 cloud API 估價對照)

## 🎯 5-Phase Workflow

| Phase | 輸入 | 輸出 | LLM Role |
|---|---|---|---|
| **0 · Plan** | 使用者查詢 + domain knowledge | A/B/C 三段計畫 or `[REFUSE]` 拒絕 | 商業分析師 |
| **A · Pipeline** | Plan + metadata | MongoDB aggregation JSON(只撈,不算) | 資料庫工程師 |
| **B · Preprocess** | raw_df + Plan | `Q` DataFrame(計算 KPI) | Pandas 工程師 |
| **C · Visualize** | `Q` + Plan | ECharts `option` dict 或 fallback 表格 | 前端工程師 |
| **D · Insight** | `Q` + Plan + 查詢 | 商業洞察 Markdown(觀察 + 注意事項) | 商業分析師 |

## 🛡️ 結構性保護

- **三道 utility 救援**(v0.2.3+):
  - `sanitize_pipeline` — Phase A 鍵名 strip / 補 `$` 前綴(防 `"match"` / `" $project"` 等 bug)
  - `rescue_empty_echarts` — 偵測 LLM 產空殼 option,自動 `pivot_table` 補回 series
  - `ensure_default_styling` — 色盤循環擴充(20 色)、heatmap numpy cast、長尾偏態 auto log scale
- **3 次 retry + cheatsheet**:Phase B 失敗時把 pandas anti-pattern 速查表餵回 LLM
- **Phase C fallback**:3 次失敗自動降級為 `render_pretty_table(Q)`,絕不 crash
- **拒絕短路**:Plan 標示 `[REFUSE]` 時,直接呈現拒絕訊息,不執行下游
- **DB ↔ Embedded 三層 fallback**:`PROMPT_REPO_ENABLED=true` 也能在 DB 失敗時自動回退 embedded 副本

## 📊 圖表能力

- **單軸 / 雙軸 bar + line**(雙軸自動偵測偏態切 log scale)
- **Stacked / 100% stacked bar**(預設 raw count,明示百分比才 normalize)
- **Grouped bar**
- **Sorted bar / TOP-N**
- **Heatmap**(自動 numpy cast + visualMap 漸層)
- **Pie / Donut**(預設帶 label + legend)
- **Scatter**
- **Horizontal stacked bar**(rule 5.65 強制 pivot)
- **精美表格 + KPI cards**(dashboard 場景)

## 🤝 設計哲學

1. **Schema-driven**:domain 業務邏輯放 metadata,系統只是推理引擎
2. **Content externalization**:prompts / metadata / test cases 都進 DB,改不用 redeploy
3. **三層 Fallback**:DB → cache → embedded,任一層掛系統仍能跑
4. **結構性防禦 > 加 prompt 規則**:失敗時優先 graceful degradation,而非無止境加規則
5. **可觀測**:每 phase 透明、每次 retry 透明、每筆 LLM call 含 token 統計,test_runs 完整快照

## 📜 文件

- `CHANGELOG.md` — SemVer 變更紀錄(v0.1.0 → **v0.16.0**)
- `AI_CONTEXT.md` — LLM agent / 接手開發者用的單檔簡介(架構 + API + deployment;**§22 Upload Workspace + §23 RAG**)
- `AI_CODE.md` — 完整源碼快照(用 `python make_ai_code.py` 重新產生)
- `SELF_LEARNING_OPS.md` — **v0.11.0+ 維運手冊**(self-learning pipeline 操作 / 監控 / 故障排除)
- `DEPLOYMENT.md` — **v0.16.0+ production / air-gap 部署手冊**(systemd / wheel cache / model upgrade / rollback)
- `GENBI_RAG_PROMPT_DESIGN.md` — **v0.16.0 RAG 動態 prompt 規格**(24 sections,1277 行)
- `GENBI_RAG_SPRINT_PLAN.md` — RAG 4 sprint 日程 + acceptance criteria
- `SPRINT2_RUN_GUIDE.md` — Mac/air-gap 操作完整流程(含 embedding model 離線安裝)
- `SPRINT3_RESULT.md` — Sprint 3 Phase B/C wire 決策軌跡 + deferred checklist
- `GenBI_Upload_Workspace_System_Extension_Spec_v0.2.pdf` — Upload Workspace BYOD 系統規格(v0.12+)
- `GenBI_v1.3_Self_Learning_MVP_Implementation_Spec.md` — Self-learning MVP 實作規格
- `STACKED_BAR_TEST.md` — Stacked bar 8 個 STK case 測試規格
- `TEST_PLAN.md` — 18 case 主測試計畫(v0.2.x 版本)
- `TEST_UX_SCENARIOS.md` — 57 case UX 整合測試

## 🆕 v0.4-v0.16 重要里程碑

- **v0.4.0** PPTX export(matplotlib renderer / table fallback / Insight bullet)
- **v0.5.0/6.0** Phase B/C prompt 拆 router + intent block(prompt size -40%)
- **v0.7.0** `task_trace` module + admin UI 第 5 頁(每 query 完整 LLM call 紀錄)
- **v0.8-v0.9** Self-learning MVP 7 個 module:bootstrap / failure_filter / observation_extractor / verifier / instinct_consolidator(Jaccard cluster + contradiction)/ resolution_detector / candidate_generator / regression_gate / dashboard_metrics + admin UI 第 6 頁 + nightly cron orchestrator
- **v0.10.0** Composite chart layout(sidebar 精簡/標準/複合)+ Q side panel
- **v0.10.4-0.10.5** Phase B/C **semantic validator**(抓「exec OK 但內容錯」silent failure)
- **v0.10.6** **Model profile system**(`default` vs `reasoning_distilled`,per-phase sampling + `<think>` strip)
- **v0.10.7** `bench_model.py` benchmark tool,確定主力模型 **qwen3-coder:30b**
- **v0.11.0** test_runner 接 TaskTrace,baseline run 也餵料給 self-learning loop
- **v0.12-v0.13** **Upload Workspace MVP**(BYOD 路徑):MetadataProvider 抽象 / file parser / semantic profiler(12 roles)/ Phase A pandas + safe_exec sandbox / PII detector / Phase A validator(5 checks)/ Saved Charts + Saved Metrics(寫回 kpi_definitions)
- **v0.14-v0.15** Upload Workspace Phase 2:Excel multi-sheet / parquet direct upload / profile versioning / relationship profiler / upload → domain export / DuckDB SQL engine / Analysis Template cross-dataset apply
- **v0.16.0** 🔍 **RAG Dynamic Prompt**(4 sprints,Sprint 2 champion):5 specialized vector index / per-phase retrieval policy / `{%- if rag_X %}` Jinja guards / sentence-transformers + Chroma 本地 embedding / air-gap deployment hardening / 476 tests(+151)

**v0.16.0 baseline**(Sprint 2 RAG-on champion): **25/26 (96%) pass** · 1482s wall · 140 LLM call · cost $0.0045/success(-10% vs v0.15.0 baseline 22/26)

## 📝 License

MIT
