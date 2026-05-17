# AI_CONTEXT.md — GenBI 專案 LLM 簡報

> **這份文件是給 LLM / 接手開發者用的單檔上手簡介。**
> 涵蓋:架構、檔案職責、設定、安裝、測試、常見錯誤對照。
> 讀完後應該能在新環境跑起來、做 debug、進行漸進式擴充。

---

## 1. 一句話定位

GenBI 是一個 **schema-driven 自然語言 BI 系統**:使用者用中/英文問問題,系統用 5-phase agentic workflow(LLM 規劃 → MongoDB pipeline → Pandas 計算 → ECharts/Plotly 視覺化 → 商業洞察)產出分析。Domain 解耦 — 換資料集只需寫一份 metadata 檔。

**Stack**:Python 3.10+ · Streamlit · OpenAI-compatible LLM(預設 Ollama / Qwen3-Coder 30B)· MongoDB 7+ · ECharts via `streamlit-echarts` · Plotly · pandas。

---

## 2. 30 秒安裝(本機開發)

```bash
# 前置:已裝 Homebrew + Ollama
ollama pull qwen3-coder:30b

git clone <repo>
cd GenBI
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env       # 預設 ollama profile,可不改

bash setup_mongodb.sh      # 一鍵裝 MongoDB Community + 匯入 tFlex 示例資料 (15 公司、147K 申請)

streamlit run app.py       # http://localhost:8501
```

**不裝 MongoDB 也能跑**:Sidebar 切到 `CSV fallback (dev)`,系統用 `data/*.csv` + pandas 模擬。

---

## 3. 架構地圖(7 層 routing + 防禦)

```
使用者 query
   │
   ▼
[Pre-Phase 0 · Intent Router] (純 metadata 推理,0 LLM call)
   ├─ greeting / intro / data_overview / data_check / guidance → meta response, END
   ├─ follow-up (有 last_analysis + 修改詞) → 注入 followup_preamble 到 Phase 0
   ├─ out_of_scope (query 與 metadata vocab 無交集) → 友善引導, END
   └─ analysis ↓
   │
   ▼
[Phase 0 · Plan]  LLM 規劃,輸出 A/B/C 三段,或 `[REFUSE]` 短路
   │
   ├─ [REFUSE] 偵測 → 顯示資料不足訊息, END
   │
   ▼
[Phase A · MongoDB Pipeline]  LLM 產 JSON aggregation pipeline,執行撈 raw_df
   │  ├─ Rule: 禁 $group/$sort/$limit/$divide/$cond (DB 端只撈不算)
   │  └─ Rule: $project 必須保留所有 metadata 描述欄位
   ▼
[Phase B · Pandas]  LLM 產 Python code,exec 後得 DataFrame `Q`
   │  ├─ 3 次 retry,失敗時帶 cheatsheet 重生
   │  ├─ 安全網: 若 Q.shape == raw_df.shape (LLM 漏寫 `Q = grouped`),自動 fallback 到聚合結果
   │  └─ Dashboard 模式: 偵測「dashboard / 執行摘要」→ Phase B 走 row-level pass-through
   ▼
[Phase C · 視覺化]  LLM 產 ECharts option dict 或 Plotly fig
   │  ├─ 3 次 retry,失敗時帶錯誤回饋重生
   │  ├─ Fallback: 連 3 次失敗 → 降級為 `render_pretty_table(Q)` 不 crash
   │  └─ 鎖死: q_columns 是唯一可信欄位來源
   ▼
[Phase D · 商業洞察]  LLM 看 Q 的 markdown 預覽 → 產出觀察與建議
   │
   ▼
寫入 st.session_state.last_analysis 供下次 follow-up 使用
```

---

## 4. 檔案職責表

| 檔案 | 職責 | LoC 級別 |
|---|---|---|
| **`app.py`** | Streamlit UI、workflow orchestration、5-phase pipeline 串接、retry/fallback 控制 | ~650 |
| **`llm_service.py`** | LLM service class、6 個 generate_* 方法、Pre-Phase 0 router、所有 prompts、anti-pattern cheatsheet | ~1400 |
| **`config.py`** | 統一管理 LLM (ollama/vllm/openai) + MongoDB 設定;`llm_service_kwargs()` 工廠函式 | ~150 |
| **`tflex_task_metadata_agent_v3.py`** | tFlex domain metadata:schema / KPI / 限制 / charting_guidance | ~800 |
| `_test_ecommerce_metadata.py` | 通用性測試用電商 metadata | ~180 |
| `_test_healthcare_metadata.py` | 通用性測試用健保 metadata | ~200 |
| `import_tflex_to_mongodb.py` | CSV → MongoDB 匯入工具,upsert/drop_insert 模式 | ~340 |
| `setup_mongodb.sh` | 一鍵 brew install mongodb + 匯入資料 | shell |
| **`test_runner.py`** | tFlex 18-case headless 回歸測試(對齊 TEST_PLAN.md) | ~870 |
| **`test_generality.py`** | 多 domain 通用性測試,`python test_generality.py {ecommerce|healthcare}` | ~480 |
| `requirements.txt` | streamlit / openai / pandas / plotly / streamlit-echarts / pymongo / tabulate | - |
| `.env.example` | 含 ollama/vllm/openai 三組 profile 範本 | - |
| `data/*.csv` | tFlex 合成原始資料 | - |
| `TEST_PLAN.md` | 18 case 詳細測試文件 | - |
| `TEST_UX_SCENARIOS.md` | 57 case UX 整合測試(冷啟動到完整 journey) | - |
| `CHANGELOG.md` | SemVer 變更紀錄 | - |
| `README.md` | 對外文件 | - |

---

## 5. 關鍵函式 / 模組 API

### `llm_service.py`

```python
class LLMService:
    def __init__(api_url, api_key, model_name, timeout_s, default_temperature, task_metadata)
    def generate_plan(query, followup_context=None) -> {"status", "message"}
    def generate_pipeline(query, plan_text, previous_code="", previous_error="") -> str  # JSON
    def generate_preprocess_code(query, plan_text, available_columns, raw_df_sample, dashboard_hint, previous_code, previous_error) -> str
    def generate_plot_code(query, plan_text, q_columns, previous_code, previous_error) -> str  # Plotly
    def generate_echarts_option(query, plan_text, q_columns, previous_code, previous_error) -> str
    def generate_insight(query, plan_text, q_preview_md) -> {"status", "message"}
    def classify_intent_for_query(query, last_analysis=None) -> {"intent", "subject", "is_followup"}
    def generate_meta_response(intent, subject="", query="") -> str  # markdown
    def reset_call_log()
    def get_call_summary() -> dict  # telemetry
```

### Module-level helpers (純函式,可獨立 import 測試)

```python
def build_domain_knowledge(metadata) -> str          # 動態組 prompt context
def build_echarts_few_shot(metadata) -> str          # 動態組 few-shot
def build_metadata_vocab(metadata) -> set            # 給 out_of_scope 用
def build_followup_preamble(last_analysis) -> str    # 接續分析 preamble

def classify_intent(query) -> dict                   # heuristic 分類 (無 metadata)
def is_dashboard_query(query) -> bool                # dashboard mode 偵測
def is_followup_query(query, last_analysis) -> bool  # follow-up 偵測
def is_out_of_scope(query, vocab) -> bool            # 離題偵測

# 結構性防禦 (v0.2.3+ · test_runner / app.py 共用)
def sanitize_pipeline(pipeline) -> list              # strip 鍵空白 + 補回漏掉的 $
def rescue_empty_echarts(option, Q) -> tuple        # 偵測空殼 option,自動 pivot 補回 series
def ensure_default_styling(option, query) -> tuple  # v0.2.4:色盤擴充 / heatmap numpy cast / 長尾 log
```

### `app.py` 結構性 helpers

```python
def get_mongo_db()            # cached MongoDB 連線 + fallback 訊息
def load_csv_fallback()       # CSV 兩表 merge 模擬 MongoDB
def execute_pipeline_on_pandas(raw_df, pipeline)  # 把 $match/$project 套用到 pandas
def render_pretty_table(Q, option, key_prefix)    # KPI cards + ProgressColumn 表格
def try_recover_Q(ns, raw_df) -> (DataFrame, msg) # Phase B 漏寫 Q = ... 的救援
```

### `config.py`

```python
LLM_PROVIDER, LLM_BASE_URL, LLM_API_URL, LLM_API_KEY, LLM_MODEL, LLM_TIMEOUT_S, LLM_TEMPERATURE
MONGO_URI, MONGO_DB, MONGO_COLL_APPLICATIONS, MONGO_COLL_COMPANY_HC, MONGO_SERVER_SELECTION_TIMEOUT_MS
PROJECT_ROOT, DATA_DIR

llm_service_kwargs() -> dict  # 直接 unpack 到 LLMService()
print_summary()               # 印出當前所有設定 (api_key 已 mask)
```

---

## 6. 環境變數對照表

所有設定都從 env 讀取(支援 `.env`),預設 fallback 已合理(本機 Ollama)。

| Env Var | 預設(ollama) | 預設(vllm) | 說明 |
|---|---|---|---|
| `HRDA_MODEL_PROVIDER` | `ollama` | `vllm` | 切換 profile,影響其他預設 |
| `HRDA_MODEL_BASE_URL` | `http://localhost:11434/v1` | `http://localhost:8000/v1` | LLM endpoint |
| `HRDA_MODEL_API_KEY` | `ollama` | `vllm-dummy` | OpenAI client 需要非空 |
| `HRDA_MODEL_NAME` | `qwen3-coder:30b` | `qwen-coder` | 模型名 |
| `HRDA_MODEL_TIMEOUT_S` | `180` | `60` | 本機 thinking 模型首次推論慢 |
| `HRDA_MODEL_TEMPERATURE` | `0.0` | `0.0` | code-gen 用 0,Plan/Insight 內部抬到 0.2/0.3 |
| `MONGO_URI` | `mongodb://localhost:27017/` | — | MongoDB 連線字串 |
| `MONGO_DB` | `tflex_demo` | — | DB name |
| `MONGO_COLLECTION_APPLICATIONS` | `tflex_applications` | — | |
| `MONGO_COLLECTION_COMPANY_HC` | `tflex_company_hc` | — | |

`python config.py` 直接執行可印當前設定(api_key 自動 mask)。

---

## 7. 常見錯誤 → 修正速查

| 症狀 | 最可能根因 | 在哪修 |
|---|---|---|
| `ModuleNotFoundError: openai` | venv 沒 activate | `source .venv/bin/activate && pip install -r requirements.txt` |
| MongoDB sidebar 顯示「⚠️ 無法連線」 | mongod 沒跑 | `brew services start mongodb-community`;或切到 CSV fallback |
| Phase 0 一直卡「制定計畫中...」 | Ollama 沒 serve / 模型沒 pull | `ollama list` 確認 `qwen3-coder:30b` 有;`curl http://localhost:11434/api/tags` |
| `LLM API 呼叫失敗: timeout` | 模型首次推論慢 | 提高 `HRDA_MODEL_TIMEOUT_S` 到 240+;或 warm up 先送一條 query |
| `KeyError: 'xxx'` 在 Phase B/C | LLM 用了不存在的欄位(常見) | 已有 3 次 retry + cheatsheet,通常自癒;若仍失敗看 expander 的 LLM code |
| `TypeError: ... 'str' with dtype 'int64'` | LLM 用 string 欄位做除法 | 已加 cheatsheet 條目;若 retry 仍失敗看實際 Phase B code |
| Phase C 連續失敗 3 次 | LLM 寫的 chart code 仍錯 | 系統自動 fallback 到 `render_pretty_table(Q)`,使用者看得到資料 |
| `[REFUSE]` 誤觸發(不該拒絕被拒) | Phase 0 prompt 過度敏感 | 看 `generate_plan` 中的 "拒絕協定 (Schema-Driven)" 規則 |
| 「改成 X」被當 out_of_scope | Routing 順序 bug | 已修(v0.2.0):follow-up 優先;若仍中,看 `_GENERIC_BI_TERMS` 是否缺常見 viz 詞 |
| Streamlit hot reload 後行為沒變 | Python module cache | 按 R 重跑 / 重啟 streamlit;或檢查是否真的存檔 |
| `ImportError: cannot import name 'X'` | 改 llm_service.py 後忘記同步 | 看 app.py 頂部 import 列表 |
| Phase D insight 在 LLM 拒絕 case 仍跑 | Plan 沒帶 [REFUSE] | 看 app.py 中 `is_refusal` 偵測邏輯 |

---

## 8. Debug 流程(從現象到根因)

### 8.1 Streamlit UI 異常(畫面 crash 或圖表錯)

1. 看頁面上的 expander:
   - `📋 檢視 AI 執行計畫` — Phase 0 LLM 寫了什麼
   - `🛠️ 檢視 MongoDB Pipeline` — Phase A JSON
   - `🐍 檢視 Python 資料處理腳本` — Phase B code
   - `🎨 檢視 ECharts/Plotly 繪圖腳本` — Phase C code
2. 找到失敗 Phase,把 LLM code 貼出來看
3. 對照常見錯誤表
4. 若是 prompt 引導不夠,在 `llm_service.py` 對應 `generate_*` 方法加規則

### 8.2 LLM 連不上

```bash
# 1. 看 endpoint 是否活著
curl http://localhost:11434/v1/models   # ollama
curl http://localhost:8000/v1/models    # vllm

# 2. 看當前 config
python config.py

# 3. 看 streamlit 日誌(在啟動 streamlit 的 terminal)
```

### 8.3 MongoDB 失敗

```bash
# 1. 看 service 狀態
brew services list | grep mongodb

# 2. 連線測試
mongosh --eval "db.runCommand({ping:1})"

# 3. 看 tflex 資料筆數
mongosh tflex_demo --eval 'db.tflex_applications.countDocuments({})'
# 期待:147526
```

### 8.4 「改成 X」之類 follow-up 沒正確接續

1. 確認左下角有 `🔗 偵測為延續性分析` info banner
2. 沒有 → 看 sidebar:`🔗 延續性分析狀態` 區塊是否存在 last_analysis
3. 沒 last_analysis → 前一次分析可能未成功完成(Phase B 或 C 中途 crash)
4. 在 `is_followup_query` 加 print 看 query / last_analysis 內容

---

## 9. 測試指令

```bash
# 全部 26 case 完整回歸 (~18-25 分鐘 · 18 原始 case + 8 STK case)
python test_runner.py

# 只跑 STK 系列 (~6-7 分鐘) — 迭代 stacked bar 用
python test_runner.py --filter STK

# 只跑指定 case (逗號分隔)
python test_runner.py --only STK-01,STK-04

# 跨 domain 通用性 (~4 分鐘 each)
python test_generality.py ecommerce
python test_generality.py healthcare

# 只看 syntax 不執行
python -m py_compile app.py llm_service.py config.py test_runner.py test_generality.py

# 看當前設定
python config.py

# Smoke test classify_intent (沒 LLM call)
python -c "from llm_service import classify_intent; print(classify_intent('你會做什麼?'))"
```

> STK case 規格在 `STACKED_BAR_TEST.md`,7 個 case 共通檢查項(stack 屬性 / xAxis unique / series 長度對齊 / yAxis max / 真實 legend...)。

每次測試會產出:
- `test_results.md` / `test_results.json` — tFlex 結果
- `test_generality_ecommerce.json` / `test_generality_healthcare.json` — 通用性結果
- stdout 含 Cost & Latency summary(wall time / LLM calls / tokens / 3 家 cloud API 估價)

---

## 10. 在新 domain 上接 GenBI

寫一份新 `<domain>_metadata.py` 檔即可,**不必動任何核心程式碼**。Schema 結構(完整版見 `tflex_task_metadata_agent_v3.py`):

```python
MY_METADATA = {
    "dataset_id": "my_domain",
    "dataset_name": "My Dataset Display Name",
    "recommended_mongodb": {
        "database": "...",
        "collections": {...},
        "join_key": "...",
    },
    "business_context": {
        "business_description": "...",
        "main_business_questions": ["範例問題 1", "範例問題 2", ...],  # 給 sample_questions
    },
    "collections": {
        "<coll_name>": {
            "primary_key": "...",
            "fields": {
                "<field>": {"type": "string|integer|number|string_or_null",
                             "description": "...",
                             "allowed_values": [...] or {...}},
            },
        },
    },
    "relationships": [{"type": "many_to_one", "from_collection": ..., ...}],
    "kpi_definitions": {
        "<kpi_key>": {"name": "中文名", "formula": "公式", "important_note": "..."},
    },
    "data_limitations": {
        "missing_dimensions": [...],
        "not_supported_analysis": [...],
    },
    "charting_guidance": {
        "recommended_charts": {
            "<chart_name>": {"chart_type": "bar|stacked_bar|heatmap", "x": "...", "y": "..."},
        },
    },
}
```

在 `app.py` 改一行 import:
```python
from my_domain_metadata import MY_METADATA
llm_service = LLMService(**config.llm_service_kwargs(), task_metadata=MY_METADATA)
```

---

## 11. 已知議題 / 待改進

- **First-pass success rate ~70-75%**(3 次 retry 後通常 ~90%)
- **接續分析 + 換圖表** 偶爾誤解維度 — 未來可加 architectural fast path(純改圖表類型 → 跳過 Phase 0/A/B,只跑 Phase C)
- **100% stacked bar** 需要 LLM 正確 per-group normalize,目前 prompt 已強化但仍偶失敗
- **Out_of_scope 偵測** 是 heuristic 為主 — 若 query 巧合含 metadata 字眼可能漏判;此時 Phase 0 schema-driven refusal 接住
- **Streamlit cache** 在改 `llm_service.py` 後不會自動清,需手動 R 鍵 rerun

---

## 12. 一張表看版本演進

| Version | Date | Highlight |
|---|---|---|
| `v0.1.0` | 2026-05-12 | Initial Release — 5-phase workflow / domain-agnostic / ECharts+Plotly / structural defenses / multi-provider LLM / cost telemetry |
| `v0.2.0` | 2026-05-12 | Pre-Phase 0 UX layer — Intent Router (6 intents)、Follow-up Detection、out_of_scope、minimal start screen、Minimal Change Principle for follow-ups、rate KPI skeleton |
| `v0.2.1` | 2026-05-12 | Docs — AI_CONTEXT.md 單檔自足專案濃縮文件 (此檔) |
| `v0.2.2` | 2026-05-12 | Fix — Phase C ECharts prompt long-format 對齊 (rule 5.55) |
| `v0.2.3` | 2026-05-13 | Stacked Bar 結構性防禦 — `sanitize_pipeline` / `rescue_empty_echarts` 兩道 utility,Phase A/C 多條 rule (5.5/5.55/5.58/5.65),STK-01~08 測試套件,test_runner `--filter`/`--only` CLI,follow-up setup 支援,denial_markers 擴大 |
| `v0.2.4` | 2026-05-13 | UI 大翻修 + 圖表渲染品質 — GenBI 品牌建立、slogan、廚師 logo SVG、Current Question 橫條、Phase A/B 收 expander、Phase C inline banner、第三道救援 `ensure_default_styling`(色盤擴充 / heatmap 三雷 / 偏態 log scale)、`rescue_empty_echarts` 雙軸支援、rule 5.7/5.7H/5.8 加入、Stack vs 100% Stack 預設邏輯翻轉 |
| `v0.3.0` | 2026-05-14 | Repository 層 + DB-backed content — 4 個 Repository class、5 個 prompts / 3 domain metadata / 26 test cases 全進 MongoDB、3 道 migration script(idempotent + byte-equal verify)、Streamlit multi-page admin UI(test_cases + test_runs)、4 個 admin CLI、sidebar domain switcher + confirm dialog、test_runner 寫 test_runs with active_versions snapshot |
| `v0.3.1` | 2026-05-14 | Admin UI 補完 + per-domain baseline 修 — `pages/03_prompts.py` Jinja2 編輯器、`pages/04_metadata.py` JSON 編輯器、`get_baseline(domain=)` 修 cross-domain 污染、文件拆 AI_CONTEXT.md / AI_CODE.md |
| `v0.3.2` | 2026-05-14 | Critical hotfix — `load_dotenv()` 補上(`.env` 從沒被讀過!)、embedded 預設只 tflex(不再 ghost ecommerce/healthcare)、`datetime.utcnow` → `datetime.now(timezone.utc)`、Per-domain baseline 隔離、Admin pages 補完 |
| `v0.3.3` | 2026-05-14 | CLI 顯示修正 + 文件 refresh — `admin/list_test_runs.py` OK/total 算 `passed + refusal_detected`、README 更新到 v0.3.x、CHANGELOG v0.3.2/3 entry |

---

## 13. 關鍵設計原則(讀懂這個專案的世界觀)

1. **Schema-driven, not keyword-driven** — 業務邏輯放 metadata,system code 不寫死特定 domain 詞彙
2. **結構性防禦 > 加 prompt 規則** — 失敗時優先 graceful degradation(retry → fallback → table),而非無止境加規則
3. **Pre-Phase 0 路由優先,LLM 為輔** — 啟發式能判斷的 0-LLM 處理,模糊的才丟給 LLM
4. **Two-layer defense** — Layer 1 vocab/heuristic 快但寬鬆,Layer 2 LLM Phase 0 精準但慢;漏判時下一層接住
5. **可觀測** — 每 phase 透明 expander、每次 retry 透明、每筆 LLM call 含 token 統計
6. **Cost-aware** — meta query / refusal / out_of_scope 都是 0-1 LLM call,完整分析 5 calls;cost telemetry 內建

---

## 14. 緊急聯絡(repo info)

- GitHub: https://github.com/kuanhuasu-source/GenBI (private)
- 主 branch: `main`
- 最新 release: 看 GitHub Releases 頁面


---

## 16. v0.3.0 · Repository / DB-backed Content 架構

從 v0.3.0 起,prompts / metadata / test cases / test runs 全部從 hardcoded Python 檔案移到 MongoDB collection。三層 fallback 保證 DB 沒接時系統仍能跑。

### 16.1 · 四個 MongoDB Collections

| Collection | Doc 數量級 | 主用途 | Repo |
|---|---|---|---|
| `prompt_templates` | 5 docs (per domain × 5 phases) | 五個 LLM phase 的 Jinja2 模板 | `PromptRepository` |
| `domain_metadata` | 1 doc per domain | schema / kpi / 限制 / charting_guidance | `PromptRepository`(共用)|
| `test_cases` | N docs per domain | test runner 跑的 case 定義 | `TestCaseRepository` |
| `test_runs` | 累計增加 | 每次 test_runner 跑的快照(含 active_versions)| `TestRunRepository` |

### 16.2 · 三層 Fallback 機制(關鍵 design)

```
讀取流程(以 PromptRepository.get_template() 為例):
  1) check in-memory cache (60s TTL) → 命中即回
  2) 若 PROMPT_REPO_ENABLED + DB 連線 → query MongoDB → cache + 回
  3) 若 DB 失敗 / 內容缺 → fallback to embedded_prompts.EMBEDDED_PROMPTS
  4) 連 embedded 都沒 → 才 raise KeyError
```

**結果**:
- `PROMPT_REPO_ENABLED=false` 預設 → 直接用 embedded,行為跟 v0.2.x 100% 一致
- `=true` 但 DB 沒 seed → embedded 接住
- `=true` + DB 已 seed → 從 DB 讀,可線上編輯

### 16.3 · Domain Isolation

- **Prompts 全部 domain-agnostic**(`domain_scope="*"`) — 不寫死 domain 詞彙,domain 內容透過 `{{ domain_knowledge }}` 注入
- **加新 domain = 只寫 metadata**,不必改 prompt
- **UI sidebar domain switcher** 切換時 confirm dialog + 重建 LLMService

### 16.4 · 新檔案職責對照

```
prompt_repository.py          ← PromptRepository (含 metadata methods)
test_case_repository.py       ← TestCaseRepository
test_run_repository.py        ← TestRunRepository
embedded_prompts.py           ← 5 phase templates (Jinja2)
embedded_metadata.py          ← 3 domains metadata fallback
embedded_test_cases.py        ← 26 tflex cases fallback

migrations/
  001_seed_prompts.py         ← seed embedded_prompts → DB
  002_seed_metadata.py        ← seed embedded_metadata → DB
  003_seed_test_cases.py      ← seed embedded_test_cases → DB

pages/
  01_test_cases.py            ← Streamlit page: test case CRUD UI
  02_test_runs.py             ← Streamlit page: run history + baseline + compare

admin/
  list_prompts.py             ← CLI: 列當前 active prompts
  list_test_runs.py           ← CLI: 列最近 runs
  mark_baseline.py            ← CLI: 標 baseline (含 --latest)
  compare_baseline.py         ← CLI: latest vs baseline diff
```

---

## 17. Repository API Surface

### 17.1 · `PromptRepository`

```python
from prompt_repository import PromptRepository, build_default_repo

repo = build_default_repo(mongo_db=db)   # or mongo_db=None for pure embedded

# Prompts
repo.get_template(prompt_key, domain="*") -> str        # Jinja2 source
repo.render(prompt_key, domain="*", **vars) -> str      # rendered prompt
repo.save_new_version(prompt_key, domain, template, notes, created_by, activate)
repo.activate(doc_id)                                    # 啟用某版本(自動下線其他)
repo.list_versions(prompt_key, domain) -> list[dict]

# Metadata (per domain)
repo.get_metadata(domain) -> dict
repo.list_active_domains() -> list[str]
repo.save_new_metadata_version(domain, metadata, notes, activate)
repo.activate_metadata(doc_id)
repo.list_metadata_versions(domain) -> list[dict]

# Cache
repo.invalidate_all()
```

### 17.2 · `TestCaseRepository`

```python
from test_case_repository import TestCaseRepository, build_default_test_case_repo

repo = build_default_test_case_repo(mongo_db=db)

repo.get_cases(domain, filter_prefix="", case_ids=None, include_inactive=False) -> list[dict]
repo.get_case(domain, case_id) -> dict | None
repo.count(domain, include_inactive=False) -> int
repo.list_domains_with_cases() -> list[str]
repo.upsert_case(domain, case_id, case_data, user="system") -> ObjectId
repo.activate_case(domain, case_id, user)
repo.deactivate_case(domain, case_id, user)
repo.delete_case(domain, case_id) -> bool          # 真刪(建議用 deactivate)
repo.ensure_indexes()                              # idempotent
repo.invalidate(domain=None)
```

### 17.3 · `TestRunRepository`

```python
from test_run_repository import TestRunRepository

repo = TestRunRepository(mongo_db=db)

repo.save_run(run_data, active_versions=None, git_commit=None) -> ObjectId
repo.list_recent(limit=20, filter_only=None) -> list[dict]
repo.get_by_run_id(run_id) -> dict | None
repo.get_baseline() -> dict | None
repo.get_latest() -> dict | None
repo.mark_as_baseline(run_id, notes="") -> bool
repo.unmark_baseline(run_id) -> bool
repo.compare(run_id_a, run_id_b) -> dict           # 摘要 delta + case_changes
repo.compare_with_baseline(run_id) -> dict | None
```

---

## 18. v0.3.0 Deployment Playbook

### 18.1 · 第一次部署 / 從 v0.2.x 升級

```bash
# 1) 安裝 dependency
pip install jinja2

# 2) 三道 migration(順序很重要 — metadata 要先,test_cases 才有依據)
python migrations/001_seed_prompts.py        # 5 prompts → DB
python migrations/002_seed_metadata.py       # 3 domains → DB
python migrations/003_seed_test_cases.py     # 26 cases → DB

# 3) 啟用 repo 模式(env var)
export GENBI_PROMPT_REPO=true

# 4) 跑 baseline run
python test_runner.py --baseline             # 同時寫 test_runs + 標 baseline

# 5) 啟動 Streamlit
streamlit run app.py
```

### 18.2 · 日常操作 cheat sheet

```bash
# 改完 prompt 想對比有沒退步
python test_runner.py                        # 跑完寫 test_runs
python admin/compare_baseline.py             # 自動 latest vs baseline diff

# 看當前 active prompts
python admin/list_prompts.py

# 列最近 20 筆 runs
python admin/list_test_runs.py

# 標新 baseline
python admin/mark_baseline.py --latest "post-v0.3.1 improvements"

# 跑某 domain 的 STK 系列 only
python test_runner.py --domain tflex --filter STK
```

### 18.3 · Streamlit 三頁

- **主頁 (`app.py`)** — 對話式 BI(chat input + agentic workflow + sidebar domain switcher)
- **🧪 Test Cases (`pages/01_test_cases.py`)** — case CRUD UI
- **📊 Test Runs (`pages/02_test_runs.py`)** — history viewer + baseline mark + compare

### 18.4 · 環境變數

| Env Var | 預設 | 說明 |
|---|---|---|
| `GENBI_PROMPT_REPO` | `false` | `true` 才從 DB 讀(否則純 embedded)|
| `GENBI_PROMPT_CACHE_TTL_S` | `60` | Repository in-memory cache 秒數 |
| `GENBI_PROMPT_COLLECTION` | `prompt_templates` | Override collection 名(多環境共用 DB 時)|
| `GENBI_METADATA_COLLECTION` | `domain_metadata` | 同上 |
| `GENBI_TEST_CASES_COLLECTION` | `test_cases` | 同上 |
| `GENBI_TEST_RUNS_COLLECTION` | `test_runs` | 同上 |

### 18.5 · 緊急救援

DB 整個壞掉?系統不會死:
1. Repo 偵測 DB read 失敗 → 自動 fallback to `embedded_*.py` 副本
2. embedded 副本內容就是 v0.3.0 launch 時的快照
3. log.warning 會印「DB read failed for X, falling back to embedded」

要硬切回 embedded:`export GENBI_PROMPT_REPO=false` 重啟。


---

## 19. 完整源碼參考 → 見 `AI_CODE.md`

從 v0.3.0 起,完整檔案源碼從本檔案抽到 **`AI_CODE.md`**(同 repo 內),以保持本檔精瘦。

`AI_CODE.md` 內容索引(v0.2.x snapshot,內含 v0.3.0 前的關鍵檔案):
- `requirements.txt` / `.env.example` / `config.py`(v0.2.x 版本)
- `llm_service.py`(完整,v0.2.x f-string 版本)
- `app.py`(完整,v0.2.x 版本)
- `tflex_task_metadata_agent_v3.py`(metadata 範本)
- 其他 domain metadata 簡例

⚠️ **v0.3.0 新增的檔案 *(尚未*)embed 進 AI_CODE.md**:
- `prompt_repository.py` / `test_case_repository.py` / `test_run_repository.py`
- `embedded_prompts.py` / `embedded_metadata.py` / `embedded_test_cases.py`
- `migrations/00*.py` / `pages/*.py` / `admin/*.py`

要看這些新檔案,請直接到 repo 對應路徑。**或者**:本文件 section 17 已列出全部 API surface(函式簽名 + 用法)— 通常足夠 LLM agent 理解使用方式。

---

## 17. v0.4.x–v0.7.x · Phase 修補 + Task Trace + Modular Prompts

`v0.3.x` → `v0.7.x` 期間累積一連串 5-phase workflow 強化,**不改架構**只強化內部:

- **v0.4.x · 5-phase 細修**:Plan pie-chart false positive 防線、Phase A `sanitize_pipeline`(strip `$cond`/`$divide` 等派生 operator)、Phase C 雙軸 routing、Export Insight → PPTX。
- **v0.5.x · 模組化 prompt**:Phase C 拆 universal header + 11 chart intent blocks(pie / stacked_100 / stacked_raw / line_dual / heatmap / bar_horizontal / line_single / scatter / kpi_table / bar_grouped / bar_basic),`_detect_chart_intent()` 純 heuristic 路由。Prompt 大小 24K → 6-9K per call。
- **v0.6.x · Phase B 模組化**:同手法拆 Phase B 成 universal header + 6 intent block(dashboard_kpi / stacked_long_pct / stacked_wide / ratio_kpi / time_series / simple_groupby),`_detect_preprocess_intent()`。
- **v0.7.0 · Task Trace**:`task_trace.py` 模組,每次 user query 記錄每個 phase 耗時 + 每次 LLM call 完整 messages + response + tokens 到 `task_traces` collection。Streamlit `pages/05_task_traces.py` 可逐步驟展開檢視。
- **v0.7.2 · Prompt invariants**:`scripts/check_prompt_invariants.py` sentinel check 防止 refactor 漏接 critical rule。17 prompts × 52 sentinels。

---

## 18. v0.8.x–v0.9.x · Self-Learning Layer(MVP 6 週完成)

對齊 `GenBI_v1.3_Self_Learning_MVP_Implementation_Spec.md`。**End-to-end loop**:

```
失敗 task_trace
    ↓ failure_filter            (撈最近 N 天 failed/refused trace)
    ↓ observation_extractor     (LLM 抽 context/action/result/cause/recommendation + dedupe)
learning_observations [candidate]
    ↓ verifier                  (LLM 獨立驗 + confidence 4-component composite)
learning_observations [verified | rejected]
    ↓ instinct_consolidator     (cluster ≥3 同意 obs + avg conf ≥0.80 → 升 instinct)
learning_instincts [candidate]
    ↓ candidate_generator       (active + conf≥0.85 + evidence≥3 → 升 prompt patch candidate)
prompt_rule_candidates [candidate]
    ↓ test_runner               (套 candidate 重跑 baseline)
    ↓ regression_gate           (4 gate: no regression / pass↑ / latency<10% / cost<15%)
prompt_rule_candidates [approved | rejected]
    ↓ 人類在 pages/06_learning_review.py 確認 → 手動 merge 進 prompt template

並行 jobs:
    resolved trace → resolution_detector → test_cases [type='regression']
    verified obs vs active instinct → contradiction_scan → auto-degrade + 寫 notification
    Dormant instinct (90d 無 evidence) → confidence_decay → conf -= 0.02
```

### 18.1 · 11 個新模組

| 模組 | 功能 |
|---|---|
| `learning/bootstrap.py` | 13 條歷史 hotfix(v0.3.x–v0.7.x 累積)seed 進 `learning_instincts` |
| `learning/failure_filter.py` | 從 `task_traces` 撈 failed/refused/手動 flag trace |
| `learning/observation_extractor.py` | LLM 抽 5+1 欄位 observation + sha256 dedupe |
| `learning/verifier.py` | LLM 獨立判 accept/revise/reject + 套 confidence |
| `learning/confidence.py` | 4-component composite (evidence/specificity/consistency/novelty) |
| `learning/instinct_consolidator.py` | Jaccard cluster + contradiction handling + confidence decay(v0.9.2)|
| `learning/resolution_detector.py` | resolved failure → auto regression test_case |
| `learning/candidate_generator.py` | instinct → prompt_rule_candidate |
| `learning/regression_gate.py` | 4 條 gate(no regression / pass / latency / cost) |
| `learning/dashboard_metrics.py` | operational / quality / impact 指標計算 |
| `pages/06_learning_review.py` | Streamlit admin review UI(metric / candidate / contradiction / observation browser) |

### 18.2 · 5 個新 MongoDB collections(migration 005 自動建)

| Collection | 用途 |
|---|---|
| `learning_observations` | 從 trace 抽出的觀察(7 indexes 含 dedupe_key unique) |
| `verifier_results` | Verifier agent 對 observation 的獨立判決 |
| `learning_instincts` | 累積的 instinct(13 seed + auto-consolidated)|
| `learning_jobs` | 所有 batch job 執行記錄(包含 contradiction notifications) |
| `prompt_rule_candidates` | 從 instinct 升的 prompt patch candidate,等人類審 |

### 18.3 · Orchestrator(`scripts/run_learning_jobs.py`)

一鍵 nightly cron 跑完 7 個 job 序列:

```bash
# 推薦 cron 部署
0 2 * * * /usr/bin/python /path/to/GenBI/scripts/run_learning_jobs.py

# Flag:
#   --dry-run             所有 job dry-run
#   --skip <job_name>     跳過(可多次)
#   --only <job1,job2>    只跑某幾個
#   --window-days N       failure window
#   --extraction-limit N  observation extraction 上限
```

### 18.4 · Domain-agnostic 加強(v0.8.3)

- **Plan prompt 加 `raw_columns_needed`**:Plan 明列要 $project 的 raw 欄位
- **Phase A `column_clusters` 鐵律**:metadata 定義 cluster(如 tflex 的 `[review_status, review_result, review_mechanism]`),Phase A 引用任一就**必須**全撈
- **`q_numeric_must_vary` test check**:抓「跑得起來但答錯」silent failure(全 0 / 全 NaN / 全同值)

### 18.5 · Baseline 50% → 92% 的 4 輪迭代

v0.8.7 → v0.9.1 用 baseline 驅動,每輪從實際 `phases.echarts.code` JSON 反推 root cause,**批次修 + unit test 驗證**:

| 版本 | Pass% | 主修 |
|---|---|---|
| v0.8.7 | 62% | `.round()` builtin / Phase C long-format example / retry feedback 7 種 error→hint mapping |
| v0.8.8 | 69% | Phase C「Q 是 post-aggregation 終態」/ raw-col KeyError hint / 100% normalize 觸發詞 |
| v0.8.9 | **92%** | `_detect_chart_intent` 同步 intra-bar / 3-col long format / `(expr).round()` 變種 / dashboard row-level KPI |
| v0.9.1 | (待測) | Horizontal stacked bar(orientation 與 chart type 正交)/ Phase B 0-100 normalize 強化 |

### 18.6 · 用法

```bash
# 一次跑通整個 loop
python scripts/run_learning_jobs.py                       # 所有 job 跑一輪

# 或單獨跑某 module
python -m learning.observation_extractor --days 7
python -m learning.verifier --limit 20
python -m learning.instinct_consolidator                  # consolidation + contradiction + decay
python -m learning.resolution_detector --days 30
python -m learning.candidate_generator
python -m learning.regression_gate --candidate-run-id <id> --candidate-id PRC-NNNNNN

# Dashboard
python -m learning.dashboard_metrics --days 7             # CLI snapshot
streamlit run pages/06_learning_review.py                 # 人類 review UI
```

---

## 19. 更新後的 12 · 版本演進表(2026-05)

| Version | Highlight |
|---|---|
| `v0.4.0` | Export Insight → PPTX(python-pptx,matplotlib renderer + table/KPI fallback)|
| `v0.4.1–v0.4.3` | Phase A `sanitize_pipeline` strip `$cond` / Phase C 雙軸 routing / Plan pie chart false positive 防線 |
| `v0.5.0–v0.5.1` | Phase C 拆 universal + 11 intent blocks + `_detect_chart_intent` 路由(prompt 24K→9K)|
| `v0.6.0–v0.6.1` | Phase B 拆 universal + 6 intent blocks + `_detect_preprocess_intent` 路由 |
| `v0.7.0` | Task Trace 模組(每 query 完整 phase + LLM call 記錄)+ `pages/05_task_traces.py` |
| `v0.7.1–v0.7.4` | Phase B「禁止 import」hotfix / sentinel prompt invariants check / test_runner synonym list + chart-type aware key check |
| `v0.8.0–v0.8.5` | **Self-Learning MVP Week 1-4** — bootstrap + 5 collections + failure_filter + observation_extractor + dedupe + verifier + confidence + consolidator + contradiction |
| `v0.8.3` | Plan `raw_columns_needed` + Phase A `column_clusters` 鐵律 + cheatsheet 全形標點 + `q_numeric_must_vary` silent-failure check |
| `v0.8.6` | hotfix:sync `embedded_test_cases.py` 跟 `test_runner.py` 的 v0.7.3/7.4/8.3 改動 |
| `v0.8.7–v0.8.9` | **Baseline 50%→92% 的 4 輪迭代** — `.round()` / long-format dedupe / raw-col filter / intra-bar 100% / dashboard row-level KPI |
| `v0.8.10` | **Self-Learning Week 5** — resolution_detector + candidate_generator + regression_gate |
| `v0.9.0` | **Self-Learning Week 6** — `pages/06_learning_review.py` + `learning/dashboard_metrics.py`(4 section admin UI: metric / candidate review / contradiction review / observation browser)|
| `v0.9.1` | Horizontal stacked bar(orientation 與 chart type 正交)+ Phase B 0-100 normalize 強化 |
| `v0.9.2` | confidence decay(spec §16 補)+ STK-04 multi-state composite + 趨勢 denial false positive + `scripts/run_learning_jobs.py` cron orchestrator + 本文件更新 |
| `v0.9.3` | hotfix · page navigation 後 phase outputs 消失 |
| `v0.10.0` | Sidebar 圖表呈現模式 toggle(精簡/標準/複合)+ `render_composite_chart()` + Q side panel |
| `v0.10.1` | hotfix · test_runner chart-orientation aware axis checks |
| `v0.10.2-0.10.3` | retry 機制強化:retry temp 0→0.15 打破 LLM stuck pattern + `部門` synonym + horizontal `max` |
| `v0.10.4` | **Phase C semantic validator**(`phase_c_validator.py`, 5 個 check:axis_align / 100pct_max / cat_dedupe / long_format / non_degenerate)+ wire 進 test_runner / app.py Phase C retry loop |
| `v0.10.5` | Phase B-aware retry hint(KeyError 區分 phase)+ **Phase B semantic validator**(`phase_b_validator.py`, 5 個 check:not_empty / numeric_not_zero / no_total_row / long_format_dedupe / comparison_multi_rows)|
| `v0.10.6` | **Model profile system** — `config.MODEL_PROFILES`(default / reasoning_distilled)、`_resolve_phase_sampling` per-phase 解析、`presence_penalty` 支援、`<think>...</think>` strip |
| `v0.10.6.1` | hotfix · `model_profile` 沒實際 wire 進 app.py / test_runner.py |
| `v0.10.7` | **`bench_model.py`** 輕量 benchmark + `Modelfile-coder30b`(8K ctx)。**模型選型結論:qwen3-coder:30b**(對照 80B-A3B 2.5x / 35B-A3B 8x 較慢)|
| `v0.11.0` | test_runner.py 接 TaskTrace,baseline run 也餵料 self-learning loop |
| `v0.11.0.1` | hotfix · `task_trace._safe_doc` 保留 datetime(自 v0.7.0 起的隱形 bug,failure_filter 終於能 match)|
| `v0.11.1` | `SELF_LEARNING_OPS.md` 維運手冊 + 全面更新 README / AI_CONTEXT |

**v0.10.7 baseline**:24/26 (92%) pass,1346s wall clock,134 LLM call,564K tokens(prompt:completion = 92:8)。

---

## 20. v0.10 · Phase B/C semantic validator + Model profile

### 20.1 Phase B/C semantic validator(v0.10.4-v0.10.5)

既有 retry 機制只 catch **exception**。實務有大量「exec OK 但內容語意錯」silent failure(axis 對不齊、100% stacked 漏 yAxis.max=100、long format 沒 filter、Q 是空 / 全 0 / 重複 key)被當成功往下送,結果在 Phase C / 渲染階段才爆,提示又指向 wrong layer。

對策:**post-exec semantic validator**。Phase B 跟 Phase C 各 5 個 check,回 `list[str]` issue;若 non-empty → 餵回 LLM 當 `previous_error` retry。

| 檔案 | Checks | 觸發 case |
|---|---|---|
| `phase_b_validator.py` | Q_EMPTY / Q_ALL_NAN / Q_ALL_ZERO / Q_TOTAL_LEAK / Q_LONG_FORMAT_DUPE / Q_SINGLE_ROW_FOR_COMPARISON | STK-05 等 |
| `phase_c_validator.py` | AXIS_ALIGN / 100PCT_MAX / CAT_DEDUPE / LONG_FORMAT_ONE_SERIES / SERIES_NON_DEGENERATE | STK-01/02/03/05 等 |

### 20.2 Phase-aware retry hint(v0.10.5)

`_format_retry_hint` 加 `phase` 參數,KeyError 在 `phase=="preprocess"` 給 Phase B 特定情境(agg 漏列、bool flag agg 沒帶),其他 phase 維持通用 hint。4 個 caller(`generate_pipeline / preprocess / plot / echarts`)全部加 phase 參數。

### 20.3 Model profile(v0.10.6)

不同 model 系列 sampling 需求差很多。`config.MODEL_PROFILES` 把 per-phase sampling 抽出來:

```python
MODEL_PROFILES = {
    "default": {  # 既有 hardcoded 行為(qwen3-coder 系列)
        "pipeline":   {"temperature": 0.0, "retry_temperature": 0.15},
        "insight":    {"temperature": 0.3},
        ...
    },
    "reasoning_distilled": {  # Qwen3.6 reasoning 系列
        "plan":       {"temperature": 1.0},         # thinking
        "pipeline":   {"temperature": 0.6, "retry_temperature": 0.75},  # coding
        "insight":    {"temperature": 0.7, "presence_penalty": 1.5},   # non-thinking
        ...
    },
}
```

切 profile:`HRDA_MODEL_PROFILE=reasoning_distilled`(env)。

新功能:
- `_resolve_phase_sampling(phase, is_retry, fallback_temp)` 每 phase 解析 sampling
- `_call_llm` 加 `presence_penalty` 參數(只有 profile 明確設才傳給 OpenAI client)
- `<think>...</think>` strip(在 `_call_llm` 出口處,trace 保留含 think 的 raw output 方便 debug)
- ⚠️ 必須走 `config.llm_service_kwargs()` 或 `LLMService(model_profile=config.MODEL_PROFILE, ...)`,**v0.10.6.1 修了 caller 沒帶 profile 的 bug**

### 20.4 模型選型結論(v0.10.7)

跑 `bench_model.py` × 3 model × 3 query × 4 phase 對照:

| Model | Per-query | Calls | Tok/sec | 結論 |
|---|---|---|---|---|
| **qwen3-coder:30b-8k**(default profile) | **43.5s** 🏆 | 13(clean) | 375.8 | 主力 |
| qwen3-coder-next-8k 80B-A3B(default) | 107.6s(2.5x 慢) | 13 | 154 | 64GB Mac swap 壓力 |
| qwen36-a3b-8k 35B-A3B(reasoning_distilled) | 342.7s(8x 慢) | 19(+6 retry) | 90 | thinking trace 漏進 JSON parser → 3 次 retry |

GenBI 既有 prompt 是針對 code-tuned non-thinking 模型寫的,換 reasoning 系列要重寫 system prompt 才有意義。

---

## 21. v0.11 · Self-learning loop end-to-end validation

### 21.1 起點 — v0.10.7 baseline 之後的 prerequisite gap

`test_runner.py` 沒接 `TaskTrace`(只有 `app.py` 有),v0.10.7 baseline 0 個 trace 寫進 DB → learning pipeline 沒料。

### 21.2 v0.11.0 P1 — test_runner 接 TaskTrace

每 case 包一個 TaskTrace,finalize 帶 `_STATUS_TO_TRACE` 映射:
- `pass` → completed
- `refusal_detected` → refused
- `phaseC_fallback_used / phaseB_exec_error / fatal_error` → failed

### 21.3 v0.11.0.1 — 修 task_trace datetime 序列化(隱形 bug 6 個月)

**自 v0.7.0 起就埋的 bug**:`task_trace.py:_safe_doc` 用 `json.dumps(default=str)` round-trip,**datetime 被無聲轉成字串**。MongoDB 存進去不是 BSON Date,所有 `{'started_at': {'$gte': dt}}` 查詢全 miss。learning pipeline `failure_filter` 永遠 0 candidate。

修正:`_safe_doc` 改用 `_sanitize()` recursive traversal,datetime 第一層 short-circuit return,完全不經字串化路徑。

Backfill script:`scripts/backfill_task_traces_datetime.py`(dry-run by default,`--apply` 才寫)。

### 21.4 Self-learning Pipeline 操作流程

完整流程見 **`SELF_LEARNING_OPS.md`**。簡述:

1. `task_traces` 累積(app.py 跟 test_runner.py 自動寫)
2. `python scripts/run_learning_jobs.py [--dry-run]` 跑 7 個 job
3. Admin UI(`pages/06_learning_review.py`)review candidate
4. Approve → 注入 prompt_templates 下次 query 生效

### 21.5 維運要點(細節看 SELF_LEARNING_OPS.md)

- **nightly cron**:`0 2 * * *`
- **monitor**:Admin UI 4 個區塊(operational / quality / impact / detail)
- **故障排除**:看 SELF_LEARNING_OPS §6(常見 5 個 issue)
- **回滾**:approved candidate 出包用 `deactivate_candidate(db, candidate_id, reason)`
- **災備**:每週 mongodump 6 個 learning_* + task_traces collection

---

# (以下為文件結束)
