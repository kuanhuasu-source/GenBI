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
# tFlex 18 case 完整回歸 (~9 分鐘)
python test_runner.py

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

# 📂 Section 15 · Full Source Code

> 以下是核心模組的完整源碼,給 LLM agent 直接查閱用,不需另外 navigate repo。
> 順序:`requirements.txt` → `.env.example` → `config.py` → `llm_service.py` → `app.py` → `tflex_task_metadata_agent_v3.py`(metadata 範本)
> 其他檔案(test_runner.py / test_generality.py / setup_mongodb.sh)結構與這些類似,本文件略。

---

## 15.1 · `requirements.txt`

```
# UI 前端框架
streamlit>=1.30.0

# LLM API 溝通 (用來打 vLLM 的 OpenAI 相容 API)
openai>=1.10.0

# 資料處理與科學運算
pandas>=2.0.0
numpy>=1.24.0

# 互動式視覺化套件
plotly>=5.18.0
# ECharts (透過 streamlit component 渲染,提供 BI 風格動畫與互動)
streamlit-echarts>=0.4.0

# MongoDB 連線驅動程式
pymongo>=4.6.0

# Phase D insight 用 DataFrame.to_markdown() 預覽 Q 表
tabulate>=0.9.0```

---

## 15.2 · `.env.example`

```dotenv
# ============================================================
# GenBI — 環境變數範例
# 將此檔複製為 .env (或直接 export) 即可生效
# ============================================================

# --- LLM Provider 選擇 ---
# 支援:ollama (預設) | vllm | openai | custom
# 不同 provider 會自動套用合理預設值,以下 *_BASE_URL / *_MODEL 可選擇覆寫
HRDA_MODEL_PROVIDER=ollama
HRDA_MODEL_TEMPERATURE=0.0


# ============================================================
# [Profile 1] Ollama on localhost (本機開發推薦)
# ============================================================
HRDA_MODEL_BASE_URL=http://localhost:11434/v1
HRDA_MODEL_API_KEY=ollama
HRDA_MODEL_NAME=qwen3-coder:30b
HRDA_MODEL_TIMEOUT_S=180


# ============================================================
# [Profile 2] vLLM on A100 (production 用)
# ============================================================
# HRDA_MODEL_PROVIDER=vllm
# HRDA_MODEL_BASE_URL=http://localhost:8000/v1
# HRDA_MODEL_API_KEY=vllm-dummy
# HRDA_MODEL_NAME=qwen-coder
# HRDA_MODEL_TIMEOUT_S=60
#
# 啟動 vLLM 範例:
#   docker run --gpus all -p 8000:8000 \
#     vllm/vllm-openai:latest \
#     --model Qwen/Qwen2.5-Coder-32B-Instruct-AWQ \
#     --served-model-name qwen-coder \
#     --max-model-len 16384 --gpu-memory-utilization 0.9


# ============================================================
# [Profile 3] OpenAI cloud API
# ============================================================
# HRDA_MODEL_PROVIDER=openai
# HRDA_MODEL_BASE_URL=https://api.openai.com/v1
# HRDA_MODEL_API_KEY=sk-your-real-key-here
# HRDA_MODEL_NAME=gpt-4o-mini
# HRDA_MODEL_TIMEOUT_S=60


# ============================================================
# MongoDB
# ============================================================
# 連不到時系統會自動 fallback 到 CSV
MONGO_URI=mongodb://localhost:27017/
MONGO_DB=tflex_demo
MONGO_COLLECTION_APPLICATIONS=tflex_applications
MONGO_COLLECTION_COMPANY_HC=tflex_company_hc
MONGO_SERVER_SELECTION_TIMEOUT_MS=2000
```

---

## 15.3 · `config.py`

```python
"""
GenBI 統一設定檔 — 所有 LLM / MongoDB 連線參數的單一來源 of truth。

# Provider 支援
- **ollama**(預設,本機開發):本機跑 qwen3-coder:30b 之類
- **vllm**(production):A100 上跑 Qwen2.5-Coder-32B-Instruct-AWQ
- **openai**(雲端 API):OpenAI / 任何 OpenAI-compatible
- **custom**:完全自定義 endpoint

# 切換方式
最簡單:在 `.env` 中設定 `HRDA_MODEL_PROVIDER=ollama|vllm|openai`,
其他欄位若不指定會自動套用該 provider 的合理預設。

# 環境變數優先序
1. `HRDA_MODEL_*`(預設名稱,跨專案一致)
2. `VLLM_*` / `OLLAMA_*`(舊式別名,向下相容)
3. Provider 預設值
4. 程式碼最終 fallback
"""

import os


# ============================================================
# Provider 預設值表
# ============================================================
_PROVIDER_DEFAULTS = {
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "api_key": "ollama",
        "model": "qwen3-coder:30b",
        "timeout_s": 180.0,  # 本機 thinking 模型首次推論慢,給足
    },
    "vllm": {
        "base_url": "http://localhost:8000/v1",
        "api_key": "vllm-dummy",
        "model": "qwen-coder",  # 對應 vLLM --served-model-name
        "timeout_s": 60.0,
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "api_key": "",  # 必須由 env 提供
        "model": "gpt-4o-mini",
        "timeout_s": 60.0,
    },
}


# ============================================================
# 1. LLM 設定
# ============================================================
LLM_PROVIDER: str = os.getenv("HRDA_MODEL_PROVIDER", "ollama").lower()
_defaults = _PROVIDER_DEFAULTS.get(LLM_PROVIDER, _PROVIDER_DEFAULTS["ollama"])


def _normalize_base_url(url: str) -> str:
    """接受 `/v1`、`/v1/chat/completions`、無尾的 `/` — 統一成 `/v1` 結尾。"""
    if not url:
        return ""
    url = url.rstrip("/")
    if url.endswith("/chat/completions"):
        url = url[: -len("/chat/completions")]
    if not url.endswith("/v1"):
        url = url + "/v1"
    return url


LLM_BASE_URL: str = _normalize_base_url(
    os.getenv("HRDA_MODEL_BASE_URL")
    or os.getenv("VLLM_URL")
    or _defaults["base_url"]
)

# LLMService 接受 `/chat/completions` 形式
LLM_API_URL: str = LLM_BASE_URL + "/chat/completions"

LLM_API_KEY: str = (
    os.getenv("HRDA_MODEL_API_KEY")
    or os.getenv("VLLM_API_KEY")
    or _defaults["api_key"]
)

LLM_MODEL: str = (
    os.getenv("HRDA_MODEL_NAME")
    or os.getenv("VLLM_MODEL")
    or _defaults["model"]
)

LLM_TIMEOUT_S: float = float(
    os.getenv("HRDA_MODEL_TIMEOUT_S", str(_defaults["timeout_s"]))
)

LLM_TEMPERATURE: float = float(os.getenv("HRDA_MODEL_TEMPERATURE", "0.0"))


def llm_service_kwargs() -> dict:
    """回傳可直接 `LLMService(**kwargs)` 使用的 dict。
    使用方式:
        from llm_service import LLMService
        from config import llm_service_kwargs
        llm = LLMService(**llm_service_kwargs(), task_metadata=METADATA)
    """
    return {
        "api_url": LLM_API_URL,
        "api_key": LLM_API_KEY,
        "model_name": LLM_MODEL,
        "timeout_s": LLM_TIMEOUT_S,
        "default_temperature": LLM_TEMPERATURE,
    }


# ============================================================
# 2. MongoDB 設定
# ============================================================
MONGO_URI: str = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
MONGO_DB: str = os.getenv("MONGO_DB", "tflex_demo")
MONGO_COLL_APPLICATIONS: str = os.getenv(
    "MONGO_COLLECTION_APPLICATIONS", "tflex_applications"
)
MONGO_COLL_COMPANY_HC: str = os.getenv(
    "MONGO_COLLECTION_COMPANY_HC", "tflex_company_hc"
)
MONGO_SERVER_SELECTION_TIMEOUT_MS: int = int(
    os.getenv("MONGO_SERVER_SELECTION_TIMEOUT_MS", "2000")
)


# ============================================================
# 3. 開發 / 路徑相關
# ============================================================
import pathlib as _pl

PROJECT_ROOT = _pl.Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"


# ============================================================
# 4. Helpers
# ============================================================
def mask_secret(value: str, keep: int = 4) -> str:
    """敏感字串遮罩 — 用於印出設定不洩漏 api key。"""
    if not value:
        return "(empty)"
    if len(value) <= keep:
        return "***"
    return value[:keep] + "***"


def print_summary() -> None:
    """啟動時印出當前設定 (敏感資訊已遮罩)。"""
    print("─" * 60)
    print(" GenBI Config Summary")
    print("─" * 60)
    print(f"  LLM provider     : {LLM_PROVIDER}")
    print(f"  LLM endpoint     : {LLM_BASE_URL}")
    print(f"  LLM model        : {LLM_MODEL}")
    print(f"  LLM timeout      : {LLM_TIMEOUT_S}s")
    print(f"  LLM api_key      : {mask_secret(LLM_API_KEY)}")
    print(f"  LLM temperature  : {LLM_TEMPERATURE}")
    print(f"  MongoDB URI      : {MONGO_URI}")
    print(f"  MongoDB DB       : {MONGO_DB}")
    print(f"  Mongo app coll   : {MONGO_COLL_APPLICATIONS}")
    print(f"  Mongo hc coll    : {MONGO_COLL_COMPANY_HC}")
    print("─" * 60)


if __name__ == "__main__":
    # 執行 `python config.py` 直接看當前設定
    print_summary()
```

---

## 15.4 · `llm_service.py`

```python
import json
import re
import time
from openai import OpenAI


# ============================================================
# 1. 從任意 task_metadata 動態組裝 LLM 用的 Domain Knowledge
#    這裡只把對 LLM 真正關鍵的部分濃縮出來,避免 prompt 過長。
#    所有函式都接受 metadata 參數,因此可以輕鬆替換成其他 domain。
# ============================================================

def _build_schema_block(metadata: dict) -> str:
    """從 metadata.collections 組出 schema 描述字串。"""
    lines = []
    for coll_name, coll_meta in metadata.get("collections", {}).items():
        lines.append(f"【{coll_name}】({coll_meta.get('description', '')})")
        lines.append(f"  grain: {coll_meta.get('grain', '')}")
        lines.append(f"  primary_key: {coll_meta.get('primary_key', '')}")
        for fname, fmeta in coll_meta.get("fields", {}).items():
            allowed = fmeta.get("allowed_values")
            if isinstance(allowed, dict):
                allowed_str = ", ".join(f"{k}={v}" for k, v in allowed.items())
            elif isinstance(allowed, list):
                allowed_str = ", ".join(allowed)
            else:
                allowed_str = ""
            extra = f" | allowed: {allowed_str}" if allowed_str else ""
            lines.append(
                f"    - {fname} ({fmeta.get('type', '')}): {fmeta.get('description', '')}{extra}"
            )
    return "\n".join(lines)


def _build_kpi_block(metadata: dict) -> str:
    lines = []
    for kpi_key, kpi_meta in metadata.get("kpi_definitions", {}).items():
        line = f"  - {kpi_meta['name']} ({kpi_key}): {kpi_meta['formula']}"
        if kpi_meta.get("important_note"):
            line += f"  ⚠️ {kpi_meta['important_note']}"
        lines.append(line)
    return "\n".join(lines)


def _build_limitation_block(metadata: dict) -> str:
    lim = metadata.get("data_limitations", {})
    missing = lim.get("missing_dimensions", [])
    not_supported = lim.get("not_supported_analysis", [])
    return (
        "缺少欄位 (NOT AVAILABLE): " + "; ".join(missing) + "\n"
        "不支援分析類型 (MUST REFUSE): " + "; ".join(not_supported)
    )


def _build_relationship_block(metadata: dict) -> str:
    lines = []
    for rel in metadata.get("relationships", []):
        lines.append(
            f"  {rel['from_collection']}.{rel['from_field']} "
            f"-[{rel['type']}]-> {rel['to_collection']}.{rel['to_field']}"
        )
    return "\n".join(lines)


def build_domain_knowledge(metadata: dict) -> str:
    """組裝完整的 Domain Knowledge text block,完全由 metadata 驅動。"""
    db_name = metadata.get("recommended_mongodb", {}).get("database", "unknown")
    dataset_name = metadata.get("dataset_name") or metadata.get("dataset_id") or "Dataset"
    return f"""### {dataset_name} (database: {db_name})

# Collections & 欄位定義
{_build_schema_block(metadata)}

# 關聯關係 (跨表 join 用)
{_build_relationship_block(metadata)}

# KPI 公式 (鐵律 — 必須嚴格依此計算,禁止自創邏輯)
{_build_kpi_block(metadata)}

# 資料限制 (CRITICAL — 違反必須回覆「資料不足」)
{_build_limitation_block(metadata)}
"""


def build_echarts_few_shot(metadata: dict) -> str:
    """
    從 metadata.charting_guidance.recommended_charts 自動合成 ECharts 範例。
    使用當前 domain 真實欄位名,確保 LLM 不會把舊 domain 的欄位帶過來。
    """
    charts_meta = (metadata.get("charting_guidance") or {}).get("recommended_charts", {})
    if not charts_meta:
        return "(metadata 未提供 recommended_charts;LLM 須依 schema 自行設計)"

    examples = []
    seen_types: set[str] = set()
    color_palette = '"color": ["#5470c6", "#91cc75", "#fac858", "#ee6666", "#73c0de"],'

    for chart_name, chart_def in charts_meta.items():
        ct = chart_def.get("chart_type", "bar")
        if ct in seen_types:
            continue
        seen_types.add(ct)
        x_col = chart_def.get("x", "")
        y_def = chart_def.get("y", "")

        if ct == "bar" and isinstance(y_def, str):
            example = f"""### 範例 — {chart_name} (bar, x={x_col}, y={y_def})
```python
option = {{
    "title": {{"text": "<圖表標題>", "left": "center"}},
    "tooltip": {{"trigger": "axis"}},
    {color_palette}
    "xAxis": {{"type": "category", "data": Q["{x_col}"].tolist()}},
    "yAxis": {{"type": "value"}},
    "series": [
        {{"name": "{y_def}", "type": "bar", "data": Q["{y_def}"].tolist()}}
    ],
    "grid": {{"left": 60, "right": 30, "top": 60, "bottom": 40}}
}}
```"""
            examples.append(example)
        elif ct == "stacked_bar" and isinstance(y_def, list) and len(y_def) >= 2:
            s_lines = ",\n        ".join(
                f'{{"name": "{y}", "type": "bar", "stack": "total", "data": Q["{y}"].tolist()}}'
                for y in y_def
            )
            example = f"""### 範例 — {chart_name} (stacked_bar, x={x_col}, y={y_def})
```python
option = {{
    "title": {{"text": "<圖表標題>", "left": "center"}},
    "tooltip": {{"trigger": "axis"}},
    "legend": {{"data": {y_def}, "top": 30}},
    {color_palette}
    "xAxis": {{"type": "category", "data": Q["{x_col}"].tolist()}},
    "yAxis": {{"type": "value"}},
    "series": [
        {s_lines}
    ],
    "grid": {{"left": 60, "right": 30, "top": 60, "bottom": 40}}
}}
```"""
            examples.append(example)
        elif ct == "heatmap":
            example = f"""### 範例 — {chart_name} (heatmap)
```python
# Q 為 long-format,含 (row_dim, col_dim, value) 三欄
option = {{
    "title": {{"text": "<圖表標題>", "left": "center"}},
    "tooltip": {{"trigger": "item"}},
    "xAxis": {{"type": "category", "data": Q["<col_dim>"].unique().tolist()}},
    "yAxis": {{"type": "category", "data": Q["<row_dim>"].unique().tolist()}},
    "visualMap": {{"min": int(Q["<value>"].min()), "max": int(Q["<value>"].max()),
                   "calculable": True, "orient": "horizontal", "left": "center", "bottom": 0}},
    "series": [{{
        "name": "<value 名稱>", "type": "heatmap",
        "data": Q[["<col_dim>", "<row_dim>", "<value>"]].values.tolist(),
        "label": {{"show": True}}
    }}],
    "grid": {{"left": 80, "right": 30, "top": 60, "bottom": 80}}
}}
```"""
            examples.append(example)
        if len(examples) >= 3:
            break

    # 永遠附帶一個 table fallback 範例
    examples.append("""### 範例 — Executive Summary (use_table + KPI cards)
適用「dashboard」「執行摘要」「完整一覽」等查詢。LLM 自由選擇 3-4 個最具代表性的 KPI。
```python
option = {
    "_use_table": True,
    "_kpi_cards": [
        # 用 f-string 在 Q 上即時運算,標題 8 字內
        {"label": "<KPI 中文名>", "value": f"{Q['<col>'].sum():,}"},
        {"label": "<比率 KPI>",  "value": f"{Q['<rate_col>'].mean()*100:.2f}%"},
    ],
    "_table_caption": f"資料筆數:{len(Q)}"
}
```""")

    return "\n\n".join(examples)


_DASHBOARD_KEYWORDS = (
    "dashboard", "執行摘要", "overview", "匯總", "kpi overview",
    "summary", "管理面板", "一覽", "卡片", "總覽",
)


_INTENT_PATTERNS = {
    "greeting": [
        # 只認很短的純打招呼
        re.compile(r"^\s*(hi|hello|hey|嗨+|你好|哈囉|哈嘍|早安|午安|晚安)[\s,.!?。!?]*$",
                   re.IGNORECASE),
    ],
    "intro": [
        re.compile(r"(你會做什麼|你能做什麼|你的功能|你會什麼|你能幫.*?做|"
                   r"介紹一?下你|你是.*?系統|介紹這個系統|介紹.*?產品)", re.IGNORECASE),
        re.compile(r"(what can you do|what.*?capabilities|what are you|tell me about you|"
                   r"introduce yourself)", re.IGNORECASE),
    ],
    "data_overview": [
        re.compile(r"(你有什麼資料|有什麼資料可|什麼資料可分析|資料概覽|資料字典|"
                   r"資料.*?簡介|schema|有什麼欄位|有什麼表|有什麼指標|有什麼 ?kpi)",
                   re.IGNORECASE),
        re.compile(r"(what data|what.*?available|what.*?fields|what.*?tables|"
                   r"what.*?kpis|show.*?schema)", re.IGNORECASE),
    ],
    "guidance": [
        re.compile(r"(怎麼開始|怎麼用|如何使用|如何開始|有範例|有什麼範例|舉例|"
                   r"範例問題|sample|example|可以怎麼問|可以問什麼)", re.IGNORECASE),
        re.compile(r"(how (to|do).*?(start|use|begin)|getting started|"
                   r"give me.*?example|show.*?example)", re.IGNORECASE),
    ],
    "data_check": [
        # 「你有 X 嗎 / 是否有 X / 有沒有 X」之類,subject 後面接「資料/欄位/嗎/?」
        re.compile(r"(你有沒有|你有|是否有|是不是有|有沒有)\s*(.+?)\s*(?:資料|欄位|這個|嗎|呢)?[\s?。?]*$",
                   re.IGNORECASE),
        re.compile(r"(do you have|is there|are there)\s+(.+?)[\s?.]*$", re.IGNORECASE),
    ],
}


_GENERIC_BI_TERMS = frozenset({
    # 通用分析詞 (中)
    "比較", "對比", "排名", "排序", "分佈", "分布", "占比", "佔比",
    "組成", "結構", "最多", "最少", "最高", "最低", "前", "top",
    "顯示", "列出", "看", "畫", "分析", "看看", "分析一下",
    "公司", "類別", "種類", "數量", "比例", "比率",
    "kpi", "dashboard", "圖表", "報表", "趨勢",
    # 視覺化術語 (避免「改成 stacked bar」這類短 query 被誤判)
    "圖", "柱狀", "長條", "圓餅", "折線", "散點", "熱力", "熱圖",
    "堆疊", "stacked", "bar", "line", "pie", "scatter", "heatmap",
    "histogram", "area", "donut", "treemap",
    # 英文分析動詞
    "compare", "rank", "show", "plot", "chart", "analyze",
    "list", "give me", "what is", "how many", "how much",
})


def _tokenize_for_vocab(text: str) -> set:
    """產生 bilingual token set:英文單字 + 中文 bi-gram。"""
    tokens: set = set()
    if not text:
        return tokens
    text = text.lower()
    # 英文單字 (≥3 字)
    for word in re.findall(r"[a-z][a-z_]{2,}", text):
        tokens.add(word)
    # 中文 bi-gram
    for run in re.findall(r"[一-鿿]+", text):
        for i in range(len(run) - 1):
            tokens.add(run[i:i + 2])
    return tokens


def build_metadata_vocab(metadata: dict) -> set:
    """
    從 metadata 抽出詞彙集,用於 out_of_scope 偵測。
    回傳 set 內容:英文單字 + 中文 bi-gram + 完整欄位/KPI名。
    """
    vocab: set = set()

    # Collections
    for coll_name, coll in metadata.get("collections", {}).items():
        vocab.add(coll_name.lower())
        # Fields
        for field_name, field_meta in coll.get("fields", {}).items():
            vocab.add(field_name.lower())
            vocab |= _tokenize_for_vocab(field_meta.get("description", ""))
            # allowed_values 也算 vocab (例:公司代碼 TST 之類)
            av = field_meta.get("allowed_values")
            if isinstance(av, dict):
                for k, v in av.items():
                    vocab.add(str(k).lower())
                    vocab |= _tokenize_for_vocab(str(v))
            elif isinstance(av, list):
                for v in av:
                    vocab.add(str(v).lower())
                    vocab |= _tokenize_for_vocab(str(v))

    # KPI definitions
    for kpi_key, kpi in metadata.get("kpi_definitions", {}).items():
        vocab.add(kpi_key.lower())
        vocab |= _tokenize_for_vocab(kpi.get("name", ""))
        vocab |= _tokenize_for_vocab(kpi.get("formula", ""))

    # Business description
    biz = metadata.get("business_context", {})
    vocab |= _tokenize_for_vocab(biz.get("business_description", ""))
    vocab |= _tokenize_for_vocab(biz.get("domain", ""))
    for q in biz.get("main_business_questions", []) or []:
        vocab |= _tokenize_for_vocab(q)

    return vocab


def is_out_of_scope(query: str, metadata_vocab: set) -> bool:
    """
    判斷 query 是否與 metadata 完全無關。
    純啟發式,保守判斷 — 漏判時 Phase 0 refusal 會接住,雙層防禦。
    """
    if not query or len(query.strip()) < 3:
        return False
    full_vocab = (metadata_vocab or set()) | _GENERIC_BI_TERMS
    if not full_vocab:
        return False  # 沒 vocab 不下判斷
    q_lower = query.lower()
    # 任一 vocab 詞作為 substring 出現於 query → 不算 out_of_scope
    for v in full_vocab:
        if len(v) >= 2 and v in q_lower:
            return False
    return True


def classify_intent(query: str) -> dict:
    """
    Pre-Phase 0 · 把使用者查詢分類到 6 種 intent 之一。
    純 heuristic 推理,零 LLM call,毫秒級延遲。

    返回:
        {"intent": "intro"|"data_overview"|"data_check"|"guidance"|"greeting"|"analysis",
         "subject": "<for data_check, what they ask about>"}

    設計原則:**只在明確匹配時才分為 meta 類型,否則一律 analysis**(優先 precision over recall)。
    """
    if not query or not query.strip():
        return {"intent": "analysis", "subject": ""}

    q = query.strip()

    # 1. greeting (最嚴格 — 只認短訊息)
    for pat in _INTENT_PATTERNS["greeting"]:
        if pat.match(q):
            return {"intent": "greeting", "subject": ""}

    # 2. intro / data_overview / guidance (短語匹配)
    for intent in ("intro", "data_overview", "guidance"):
        for pat in _INTENT_PATTERNS[intent]:
            if pat.search(q):
                return {"intent": intent, "subject": ""}

    # 3. data_check (帶 subject 萃取,但要小心別跟分析查詢混淆)
    for pat in _INTENT_PATTERNS["data_check"]:
        m = pat.search(q)
        if m:
            # 萃取 subject - 第 2 個 capture group
            try:
                subject = m.group(2).strip(" 的?,。!?")
            except (IndexError, AttributeError):
                subject = ""
            # 過濾掉太長的 subject (可能誤判,例如「你有什麼建議要給 TST 公司...」)
            if subject and len(subject) <= 20:
                return {"intent": "data_check", "subject": subject}

    # 4. 預設 analysis
    return {"intent": "analysis", "subject": ""}


_FOLLOWUP_MARKERS = (
    # 修改類動詞
    "改成", "改為", "改用", "改畫", "改看", "改一下", "改", "換成", "換用",
    # 追加類
    "再加", "再來", "再看", "再分析", "也加", "也看", "也展示", "也要",
    "順便", "還要", "另外", "額外", "加上",
    # 範圍調整
    "縮小", "擴大", "範圍", "只看", "只要",
    # 排序/重整
    "排序", "排排看", "重新", "倒過來",
    # 代詞 / 上下文指代
    "上面", "剛才", "剛剛", "前面", "上一個", "上一張", "上次",
    "這張", "這份", "這個結果", "那張", "那份",
    # 英文
    "change", "instead", "switch", "make it", "rather than",
    "also show", "also add", "add to", "remove", "drop", "filter",
    "narrow down", "expand", "sort by", "rank by",
)


def is_followup_query(current_query: str, last_analysis: dict | None) -> bool:
    """
    Pre-Phase 0 · 判斷此 query 是否為延續性提問(modification of last analysis)。
    純 heuristic,零 LLM call。

    觸發條件 (需同時成立):
    1. last_analysis 存在 (有可接續的前次分析)
    2. current_query 含至少一個 follow-up marker
    """
    if not last_analysis or not current_query:
        return False
    q = current_query.strip().lower()
    return any(m in q or m in current_query for m in _FOLLOWUP_MARKERS)


def build_followup_preamble(last_analysis: dict) -> str:
    """產生「接續分析提示」preamble,塞到 Phase 0 的 user message 開頭。"""
    if not last_analysis:
        return ""
    prev_query = last_analysis.get("query", "")
    prev_plan = (last_analysis.get("plan_summary") or "")[:300]
    prev_cols = last_analysis.get("Q_cols") or []
    prev_chart = last_analysis.get("chart_descriptor", "")

    return f"""【🔗 接續分析提示 — CRITICAL】

使用者現在的訊息是「對前一個分析的修改/延伸」,**不是新分析**。

【前次分析脈絡】
- 原問題: {prev_query}
- 前次 Q.columns: {prev_cols}
- 前次圖表類型: {prev_chart or "(未知)"}
- Plan 摘要: {prev_plan or "(無)"}

══════════════════════════════════════════════
【🎯 最高指導原則:MINIMAL CHANGE】
══════════════════════════════════════════════

❶ 若使用者只是要「**換圖表類型**」(改成 X / 換成 Y / 改畫 Z):
   ⭐ A 段(資料獲取)**完全沿用前次**
   ⭐ B 段(Pandas)**完全沿用前次**,Q.columns 必須與前次一致
   ⭐ C 段(視覺化)只改圖表類型
   ⭐ 不要重新解讀維度、不要新增/刪除欄位、不要重做 KPI

❷ 若使用者要「**也加 / 再加** 某個 KPI / 指標」:
   ⭐ A 段保持
   ⭐ B 段在 groupby agg 加新欄位(原欄位不動)
   ⭐ C 段加對應 series

❸ 若使用者要「**只看 / 縮窄 / 改範圍**」:
   ⭐ A 段在 $match 加新過濾
   ⭐ B 段沿用,C 段沿用

══════════════════════════════════════════════
【⚠️ 單一指標 stack 的處理】
══════════════════════════════════════════════

若前次 Q 只有 1 個 numeric 指標(例如只有 `average_return_rate`),
而使用者要求 `stacked bar`,**這在統計上無意義**。請選一:

  (a) 保留為一般 bar,在 plan 中說明「單一指標無法 stack」
  (b) 主動建議:「想看 PAY / RTN / InProgress 的 100% 占比 stacked 嗎?」
  (c) 若使用者明示要堆疊哪些指標,才在 B 段加入

══════════════════════════════════════════════
【🚫 絕對禁忌(常犯錯誤)】
══════════════════════════════════════════════

- 不要把 `hc` / `headcount` 當 x-axis 維度 — 那是參考值,不是分組依據
- 不要產生有重複行的 Q(每個 dimension 值應該只出現一次)
- 不要把 raw count 配 `axisLabel.formatter = "{{value}}%"` — formatter 不會自動 / 100
- 不要在「占比」場景以外,把 yAxis 軸線拉到 > 100% 範圍
- 不要保留前次的「比率類 KPI 欄位名」但配上不同的 raw 數據

══════════════════════════════════════════════
"""


def is_dashboard_query(query: str) -> bool:
    """
    啟發式偵測:此查詢是否為「dashboard / 執行摘要」場景。

    為什麼需要這個:
    這類查詢通常需要算「整體 scalar KPI」(總數、平均率等),
    沒有明確 groupby 維度。LLM 容易在 Phase B 嘗試 `Q.agg(named_agg)`
    這種 anti-pattern 並失敗。
    偵測到後改走「row-level pass-through + Phase C `_kpi_cards`」路徑。
    """
    if not query:
        return False
    q = query.lower()
    if not any(kw in q for kw in _DASHBOARD_KEYWORDS):
        return False
    # 排除明確有 groupby 維度的句子(避免誤判)
    strong_groupby = (
        "by ", "per ", "各", "依", "by region", "by category",
        "by channel", "by company", "by department", "by specialty",
    )
    has_groupby = any(g in q for g in strong_groupby)
    return not has_groupby


PANDAS_ANTIPATTERN_CHEATSHEET = """
### 🛡 常見 Pandas Anti-pattern 速查表 (重生時請對照,避免再犯):

❌  `Q.agg(name=(col, op), ...)` 直接對 DataFrame 用 named agg
    為什麼:這個 syntax 只在 `Q.groupby(...).agg(...)` 內合法,沒 groupby 直接用會出現奇怪形狀。
    ✅  做整體 (overall scalar) 聚合請改用 scalar 變數:
        total = len(Q)
        paid  = (Q['col'] == 'Y').sum()
        amt   = (Q['x'] * Q['y']).sum()
        Q = pd.DataFrame({{"metric": [...], "value": [...]}})

❌  `Q['col'].first()` (Series 方法幻覺)
    為什麼:Series 沒有 `.first()` 方法,會 AttributeError。
    ✅  `Q['col'].iloc[0]` 取首列值;groupby 內可用 `agg(col=('col', 'first'))` (字串 'first')。

❌  `Q.merge(Q[[...]], on='col')` self-merge
    為什麼:同欄位名衝突,pandas 自動 rename 成 `col_x` / `col_y`,後續引用 KeyError。
    ✅  用 `agg(col=('col', 'first'))` 在 groupby 結果直接帶上參考欄位。

❌  漏寫 `Q = grouped` 最終指派
    為什麼:中間做了 groupby/agg,但忘了把結果 assign 回 Q,Phase C 找不到 KPI 欄位。
    ✅  不管中間用什麼變數名 (grouped/result/agg_df...),**最後一行**必寫 `Q = <最終結果>`。

❌  引用 raw_df 樣本中沒有的欄位 (幻覺欄位)
    為什麼:即使你「直覺認為」某欄位該存在,只要不在 avail_cols 中,引用就會 KeyError。
    ✅  寫前心算每個 `Q['xxx']` 是否存在於 avail_cols;計 row 數請用 `Q.groupby(...).size()`。

❌  `Q.pivot(index=A, columns=B)` 把 Q 變 wide format
    為什麼:把值散到動態欄位名後,Phase C 沒辦法用固定欄位名引用,且 ECharts heatmap 要 long format。
    ✅  維持 long format `[dim_a, dim_b, value]` 三欄;只有純表格 (use_table) 場景可考慮 wide。

❌  比率/除法 用 string 欄位當分子或分母 (例:`Q['count'] / Q['employee_id']`)
    為什麼:string 欄位 (任何 ID / code / category / status / mechanism) 無法做算術運算,
    會炸 `TypeError: operation 'rtruediv' not supported for dtype 'str'`。
    識別:metadata 中 `type: "string"` 或 `"string_or_null"` 的欄位都是字串型,**只能用於 filter / groupby / nunique**。
    ✅  比率類 KPI 的分子分母**必須都是 numeric** (透過 sum/count 等得到):
        ```python
        Q['is_ai'] = (Q['review_status']=='Y') & (Q['review_mechanism']=='AI')  # bool
        agg = Q.groupby('<dim>').agg(
            ai_count=('is_ai', 'sum'),              # int
            completed=('is_completed', 'sum'),       # int
        )
        agg['ai_rate'] = agg['ai_count'] / agg['completed']  # ✅ int / int
        ```
    ✅  Distinct string ID 計數請用 `nunique()`,**不要用 div**:
        `submitter_count=('employee_id', 'nunique')`
"""


class LLMService:
    """
    封裝 4 階段 LLM 呼叫:
        Phase 0  generate_plan          — 規劃三階段
        Phase A  generate_pipeline      — 產 MongoDB pipeline
        Phase B  generate_preprocess_code — 產 Pandas 處理腳本 (Q)
        Phase C  generate_plot_code     — 產 Plotly 繪圖腳本 (fig)
        Phase D  generate_insight       — 產商業洞察文字
    所有 code-gen 方法都支援 previous_code / previous_error 作為自我修正回饋。
    """

    def __init__(self,
                 api_url: str = "http://localhost:11434/v1/chat/completions",
                 api_key: str = "ollama",
                 model_name: str = "qwen3-coder:30b",
                 timeout_s: float = 180.0,
                 default_temperature: float = 0.0,
                 task_metadata: dict | None = None):
        """
        參數預設指向 Ollama (localhost:11434);
        若你在用 vLLM,把 api_url 改成 http://localhost:8000/v1/chat/completions、
        model_name 改成 vLLM 啟動時 --served-model-name 設定的值即可。

        timeout_s: 本地 thinking 模型首次推論可能 120-180s,給足。
        default_temperature: code-gen 任務建議 0.0,Plan/Insight 會在內部自行抬高。
        task_metadata: domain 描述 dict (schema/KPI/限制/recommended_charts)。
                       若 None,自動載入 tflex_task_metadata_agent_v3.TASK_METADATA。
                       換不同 domain 時傳入該 domain 的 metadata 即可,不必改本 module 的程式碼。
        """
        self.client = OpenAI(
            base_url=api_url.replace("/chat/completions", ""),
            api_key=api_key,
            timeout=timeout_s,
        )
        self.model_name = model_name
        self.default_temperature = default_temperature
        self.timeout_s = timeout_s

        # ── 載入並組裝 domain knowledge / few-shot ──
        if task_metadata is None:
            from tflex_task_metadata_agent_v3 import TASK_METADATA as _DEFAULT_META
            task_metadata = _DEFAULT_META
        self.task_metadata = task_metadata
        self.domain_knowledge = build_domain_knowledge(task_metadata)
        self.echarts_few_shot = build_echarts_few_shot(task_metadata)
        # Pre-Phase 0 out_of_scope 偵測用的 vocab(一次建好)
        self._metadata_vocab = build_metadata_vocab(task_metadata)

        # ── Telemetry:每次 LLM call 的耗時與 token 用量 ──
        # 由外部測試框架在 case 開始前呼叫 reset_call_log(),結束時 get_call_summary()
        self.call_log: list[dict] = []

    def classify_intent_for_query(
        self, query: str, last_analysis: dict | None = None
    ) -> dict:
        """
        Pre-Phase 0 路由(instance 版本)。

        判斷順序(優先級從高到低):
        1. 5 個 explicit meta intent (intro / data_overview / data_check / guidance / greeting)
        2. **Follow-up**(若有 last_analysis + 含修改詞,視為 analysis,
            標 `is_followup=True` 讓 app.py 注入前次脈絡)
        3. out_of_scope(query 與 metadata 完全無關)
        4. analysis(預設 fallthrough)

        ⭐ Follow-up 優先於 out_of_scope,因為短的修改指令(「改成 X」「也加 Y」)
        本來就常缺 metadata vocab,不該被誤判離題。
        """
        base = classify_intent(query)
        if base["intent"] != "analysis":
            return base

        # ⭐ 接續分析優先 — 在 out_of_scope 之前檢查
        if is_followup_query(query, last_analysis):
            return {"intent": "analysis", "subject": "", "is_followup": True}

        # 真正的 out_of_scope
        if is_out_of_scope(query, self._metadata_vocab):
            return {"intent": "out_of_scope", "subject": query.strip()[:60]}

        return base

    # --------------------------------------------------------
    # 內部工具
    # --------------------------------------------------------
    def _call_llm(self, messages, temperature=None, max_tokens=2048,
                   phase: str = "unknown"):
        if temperature is None:
            temperature = self.default_temperature
        t0 = time.time()
        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except Exception as e:
            self.call_log.append({
                "phase": phase,
                "elapsed_s": round(time.time() - t0, 2),
                "prompt_tokens": None,
                "completion_tokens": None,
                "total_tokens": None,
                "error": str(e),
            })
            raise RuntimeError(f"LLM API 呼叫失敗: {str(e)}")

        elapsed = round(time.time() - t0, 2)
        usage = getattr(response, "usage", None)
        self.call_log.append({
            "phase": phase,
            "elapsed_s": elapsed,
            "prompt_tokens": getattr(usage, "prompt_tokens", None) if usage else None,
            "completion_tokens": getattr(usage, "completion_tokens", None) if usage else None,
            "total_tokens": getattr(usage, "total_tokens", None) if usage else None,
        })
        return response.choices[0].message.content

    def reset_call_log(self) -> None:
        """測試 framework 在每個 case 開始前呼叫,清空累積 telemetry。"""
        self.call_log = []

    def get_call_summary(self) -> dict:
        """彙總目前 call_log 的 cost telemetry。"""
        if not self.call_log:
            return {"calls": 0, "total_elapsed_s": 0.0,
                    "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                    "by_phase": {}}
        by_phase: dict[str, dict] = {}
        for c in self.call_log:
            p = c.get("phase", "unknown")
            d = by_phase.setdefault(p, {"calls": 0, "elapsed_s": 0.0,
                                        "prompt_tokens": 0, "completion_tokens": 0})
            d["calls"] += 1
            d["elapsed_s"] += c.get("elapsed_s") or 0
            d["prompt_tokens"] += c.get("prompt_tokens") or 0
            d["completion_tokens"] += c.get("completion_tokens") or 0
        total = {
            "calls": len(self.call_log),
            "total_elapsed_s": round(sum(c.get("elapsed_s") or 0 for c in self.call_log), 2),
            "prompt_tokens": sum(c.get("prompt_tokens") or 0 for c in self.call_log),
            "completion_tokens": sum(c.get("completion_tokens") or 0 for c in self.call_log),
            "total_tokens": sum(c.get("total_tokens") or 0 for c in self.call_log),
            "by_phase": {p: {**d, "elapsed_s": round(d["elapsed_s"], 2)} for p, d in by_phase.items()},
        }
        return total

    @staticmethod
    def _strip_code_fence(raw: str, lang: str = "") -> str:
        """去除 ```python ... ``` 或 ```json ... ``` 之類的圍欄。"""
        if not raw:
            return ""
        pattern = rf"^```(?:{lang})?\s*|\s*```$"
        return re.sub(pattern, "", raw, flags=re.MULTILINE).strip()

    @staticmethod
    def _format_retry_hint(previous_code: str, previous_error: str,
                            cheatsheet: str = "") -> str:
        """把上一次失敗的 code + traceback 轉成 LLM 修正提示。
        可選 cheatsheet 附在後面,提示常見 anti-pattern。"""
        if not previous_code and not previous_error:
            return ""
        hint = (
            "\n\n### 🔁 自我修正提示\n"
            "你上一次回覆的程式碼執行失敗,請仔細檢查錯誤訊息後重新生成正確版本。\n"
            "**禁止再犯同樣錯誤**,也不要對結構做不必要的改動。\n\n"
            "上次的程式碼:\n```python\n"
            f"{previous_code}\n```\n"
            "錯誤訊息 (Traceback):\n```\n"
            f"{previous_error}\n```\n"
        )
        if cheatsheet:
            hint += "\n" + cheatsheet
        return hint

    # --------------------------------------------------------
    # Phase 0: 計畫
    # --------------------------------------------------------
    def generate_plan(self, query, followup_context: dict | None = None):
        system_prompt = f"""你是專業的 AI 商業智慧助理。請以上方 Domain Knowledge 為唯一依據規劃分析。

{self.domain_knowledge}

### 🚨 拒絕協定 (Schema-Driven · 每次從 metadata 推理,不要記憶特定詞彙)

⚠️ 核心原則:**寧可不拒絕、走計畫,也不要 false positive 拒絕。**
判斷的唯一依據是**當前 metadata 的 `collections.*.fields` 與 `data_limitations`**,
不要因為查詢含某個中文/英文詞就條件反射拒絕 — 永遠重新從 schema 推理。

【三步推理流程】

**Step 1 · 解析 query** — 識別查詢需要哪些「資料維度/指標」。例如:
- 查詢提到「過去三個月」→ 需要【時間維度】
- 查詢提到「平均金額」→ 需要【金額/數量指標】
- 查詢提到「比較各公司」→ 需要【公司類別維度】
- 查詢提到「分佈」「熱力圖」「占比」→ **這只是分析操作,不需要特定維度**

**Step 2 · 查 metadata 的 schema** — 對應的維度/指標是否存在於 `collections.*.fields`?
- 是 → Step 3 不適用,**走 A/B/C 計畫**
- 否 → 進入 Step 3

**Step 3 · 查 data_limitations** — 該分析類型是否被 `missing_dimensions` 或
`not_supported_analysis` 明文列為不支援?
- 是 → 拒絕 (`[REFUSE]`)
- 否 → 即使欄位缺,也嘗試走計畫(讓使用者看到下游錯誤而非錯拒絕)

【判斷練習(以當前 metadata 為準,LLM 自行推理)】

範例 1:「畫一張熱力圖,看不同公司在四個申請類別的分佈差異」
- Step 1:需要 company 維度 + category 維度,「熱力圖」「分佈」只是分析操作
- Step 2:`company_code` 與 `application_category` 都在 schema 中
- ✅ 走計畫

範例 2:「我想看過去三個月的申請趨勢」
- Step 1:需要時間維度(「過去三個月」+「趨勢」明確要求時間軸)
- Step 2:查 schema 是否有 date/timestamp 類欄位 → 沒有
- Step 3:`data_limitations.not_supported_analysis` 是否含 "trend" 或 "time" → 是
- ❌ `[REFUSE]`

範例 3:「平均申請金額」
- Step 1:需要金額/價格類數值欄位
- Step 2:查 schema 是否有 amount/price 類欄位 → 沒有
- Step 3:`data_limitations` 是否明示金額不支援 → 是
- ❌ `[REFUSE]`

⚠️ 注意:**今天的 metadata 可能下個月變**(domain expert 新增欄位後,以前不支援的分析變成支援)。
你的推理必須**完全基於 prompt 上方提供的當前 metadata 內容**,不要對某 domain 預設「永遠不支援 X」。

【拒絕回覆格式】(僅在 Step 3 確認後使用):
```
[REFUSE] 缺少 <metadata 中真實的欄位/維度名>,無法執行 <分析類型>。
此為 data_limitations 中 "<引用原文限制名>" 的限制。
建議改問:<以 metadata 中存在的欄位為基礎的替代分析>。
```
**不要寫 A/B/C 三段。** 下游會根據 [REFUSE] 標記自動中止。

【一般回覆格式】(Step 2 通過時使用):
**A. 資料獲取:** ...
**B. 資料處理:** ...
**C. 視覺化建議:** ...

### 三階段執行計畫格式 (僅在【未拒絕】時使用,Markdown 精簡有力):
**A. 資料獲取:** 起手 collection、需 join 的表、需要的 $match 過濾條件 (參照 schema 的維度欄位)。
**B. 資料處理:** 要算哪些 KPI (引用上方 kpi_definitions 公式) 與 pandas 邏輯重點。
**C. 視覺化建議:** 圖型選擇與理由。多類別比較禁止 pie chart;若是「dashboard / 執行摘要」場景,建議走表格 + KPI 卡片。
"""
        # 接續分析時注入前次脈絡
        followup_preamble = build_followup_preamble(followup_context) if followup_context else ""
        user_msg = followup_preamble + f"需求:{query}\n請給出計畫:"
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ]
        try:
            return {"status": "success", "message": self._call_llm(messages, temperature=0.2, phase="plan")}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    # --------------------------------------------------------
    # Phase A: MongoDB pipeline
    # --------------------------------------------------------
    def generate_pipeline(self, query, plan_text="",
                          previous_code: str = "", previous_error: str = ""):
        system_prompt = f"""你是精通 MongoDB 的資料庫工程師,負責【A. 資料獲取】。
{self.domain_knowledge}

### 實作守則 (CRITICAL RULES):
1. 【輸出格式】(CRITICAL FATAL) 必須輸出合法 JSON,包含 `start_collection` (字串) 與 `pipeline` (陣列),
   除 JSON 外不要包含任何說明文字。
2. 🔗【關聯鐵律】當 KPI 公式需要其他 collection 的欄位 (參照上方 relationships),
   必須 `$lookup` 該表並緊接 `$unwind` (使用 `preserveNullAndEmptyArrays: true`)。
3. 🚫【禁止寫入】禁止 `$out`、`$merge`。
4. 🚫【嚴禁在 DB 端聚合】(CRITICAL FATAL) 任務是撈「明細」交給 Pandas。
   禁止 `$group`、`$count`、`$sort`、`$limit`,只能用 `$match`、`$lookup`、`$unwind`、`$project`。
5. 🚫【禁止在 DB 算比例】(CRITICAL FATAL) 禁止 `$divide`、`$cond` 算 KPI,
   一切比例都交給 Pandas。
6. ✅【$project 鐵律】(CRITICAL FATAL) `$project` 必須保留 source collection
   與 join 表中【所有 metadata 描述過的欄位】(除 `_id` 外),不要為了「精簡」而砍欄位。
   原因:Phase B 的 Pandas 程式可能會引用任何原始欄位 (計 count、再次驗證 filter 條件等),
   提早砍掉會讓 Phase B KeyError。
   即使你的 `$match` 已過濾某欄位的某值,仍要把該欄位留在 $project 中。
   具體欄位清單請對照上方 Domain Knowledge 中各 collection 的 fields 區塊。

### 輸出範例結構 (僅做撈取與關聯,以 metadata 中真實 collection / 欄位為準):
{{
    "start_collection": "<上方 schema 中的主表名>",
    "pipeline": [
        {{ "$match": {{ "<dimension_field>": {{ "$in": ["<value1>", "<value2>"] }} }} }},
        {{ "$lookup": {{ "from": "<關聯表>", "localField": "<join_key>",
            "foreignField": "<join_key>", "as": "<別名>" }} }},
        {{ "$unwind": {{ "path": "$<別名>", "preserveNullAndEmptyArrays": true }} }},
        {{ "$project": {{ "_id": 0,
            "<主表所有 metadata 描述欄位>": 1,
            "<關聯表欄位>": "$<別名>.<關聯表欄位>"
        }} }}
    ]
}}"""
        user_msg = f"需求:{query}\n計畫:{plan_text}"
        user_msg += self._format_retry_hint(previous_code, previous_error)
        raw = self._call_llm(
            [{"role": "system", "content": system_prompt},
             {"role": "user", "content": user_msg}],
            phase="pipeline",
        )
        return self._strip_code_fence(raw, lang="json")

    # --------------------------------------------------------
    # Phase B: Pandas 處理
    # --------------------------------------------------------
    def generate_preprocess_code(self, query, plan_text="", available_columns=None,
                                  raw_df_sample: str = "",
                                  dashboard_hint: bool = False,
                                  previous_code: str = "", previous_error: str = ""):
        cols_info = (
            f"目前 raw_df 的欄位 (鎖死,不可亂改名): {available_columns}"
            if available_columns else "欄位未知。"
        )
        if raw_df_sample:
            cols_info += (
                "\n\n### raw_df 實際前 3 列樣本 (你必須以此為準,不要憑訓練資料猜測欄位):\n"
                f"{raw_df_sample}\n\n"
                "⚠️ 上面沒列出的欄位,Phase A 可能已 $project 砍掉了,**絕對禁止引用**。\n"
                "⚠️ 上游 $match 已過濾的欄位,值可能全部一致 (例如某狀態欄位全為固定值),"
                "不需要再做相同過濾,但仍可用於計算。"
            )
        dashboard_block = ""
        if dashboard_hint:
            dashboard_block = """
### 🎯 DASHBOARD MODE (系統偵測到此為 dashboard / 執行摘要場景)
此查詢屬於「整體 KPI 一覽」性質,沒有明確 groupby 維度,**請走 row-level pass-through**:

✅ 推薦做法:Q 保持 row-level (raw_df + 衍生 bool 欄位),把 scalar 算式交給 Phase C 的 `_kpi_cards`:
```python
Q = raw_df.copy()
# 只加衍生 bool / 數值欄位,不做 groupby/agg
Q['is_<state_a>'] = (Q['<status_col>'] == '<val_a>')
Q['is_<state_b>'] = (Q['<status_col>'] == '<val_b>')
# 不要再做 groupby/agg!Phase C 會用 f"{Q['col'].sum():,}" 計算總量
```

🚫 嚴禁:`Q.agg(name=(col, op))` 或任何「沒 groupby 的 named aggregation」
🚫 嚴禁:`Q = pd.DataFrame({{'metric': [...], 'value': [...]}})` 這種預組 KPI 表
   (因為 Phase C 處理 `_kpi_cards` 時會自己組,你做了反而干擾。)
🚫 嚴禁:在 Q 中加入 TOTAL / SUMMARY / GRAND TOTAL / 合計 / 總計 / 全公司 之類的「聚合摘要列」!
   (Phase C 的 `_kpi_cards` 會用 `Q['col'].sum()` 算總量,
    如果 Q 已含 TOTAL 列會導致數值【雙倍計算】。)
   ✅ 若你做了 groupby,Q 就是每組一列 (15 家公司 = 15 列),**不要再加第 16 列當 TOTAL**。

"""

        system_prompt = f"""你是精通 Pandas 的資深資料工程師,負責【B. 資料處理】。
{cols_info}

{self.domain_knowledge}
{dashboard_block}
### 實作守則 (CRITICAL RULES):
1. 🎯【最外層產出 Q】(CRITICAL FATAL) 最外層必須宣告 `Q` (DataFrame)。
   禁止包在 function/class 內,禁止 `if __name__`,禁止 `print`。

1.5 🎯【終態必須 Q = 最終結果】(CRITICAL FATAL — 最常犯) 不管你中間用什麼變數名
   (`grouped`、`result`、`agg_df`、`heatmap_data`、`return_counts` 等),
   **最後一行一定要寫 `Q = <你的最終結果>`**!
   ❌ 反例 (Phase C 會 KeyError,因為 Q 仍是原 raw_df):
   ```python
   Q = raw_df.copy()
   grouped = Q.groupby('<dim>').agg(...)
   grouped['<new_metric>'] = ...
   # 沒了!Q 還是 raw_df!
   ```
   ✅ 正解:
   ```python
   Q = raw_df.copy()
   grouped = Q.groupby('<dim>').agg(...)
   grouped['<new_metric>'] = ...
   Q = grouped            # ← 這一行絕對不能忘!
   ```

2. 🔠【大小寫鎖死 + KPI 欄名對齊】絕不擅自修改欄位名稱格式。
   ⭐ **新增欄位時(例如做完 groupby+agg 後),請優先使用 `kpi_definitions` 的 key
   作為欄位名(例:`total_applications`、`average_return_rate`、`ai_review_rate` 等)**,
   這樣 Phase C 能根據相同名稱穩定引用,避免 KeyError。
3. 🛡️【KPI 公式來源】(CRITICAL) 所有 KPI 計算【嚴格】依照上方 Domain Knowledge 中的
   `kpi_definitions`,不可自創邏輯或自行重新定義分子分母。特別注意:
   - 比率類 KPI 注意分母的精確定義 (常見錯誤:用 total count 取代 metadata 指定的 base count)。
   - 注意每個 KPI 的 `important_note`(若有),通常標示「不可包含 X 狀態」之類的限制。
   - 涉及 distinct 計數一律用 `nunique()`,不要 `len(set(...))`。
   - 涉及 ID 欄位 (主鍵類字串) 不要轉 int,保持字串。
4. 🛡️【小樣本處理】若涉及比率,請保留分子分母絕對數,便於後續判讀。
5. 🚫【禁止外部 IO】不可呼叫 read_csv / read_sql / open。raw_df 已備好。
6. 🚫【禁止 self-merge】(CRITICAL FATAL) raw_df 已是上游 join 完成的長表,
   絕對禁止 `Q.merge(Q[[...]], on='col')` 自我 merge,
   會讓欄位被 pandas 自動 rename 成 `col_x` / `col_y`,後續 KeyError。
   若要在 groupby 結果中帶上某參考欄位 (例如維度上的 headcount),
   請用 `agg(my_col=('my_col', 'first'))`,不要再去 merge raw_df。
7. 🚫【禁止幻覺欄位】只使用上方 avail_cols 與 raw_df 樣本中【實際出現】的欄位。
   即使你「直覺認為」某欄位(如主鍵 / 某狀態欄位)應該存在,只要不在 avail_cols 中就禁止引用。
   要計 row 數量請用 `Q.groupby(...).size()`,不要依賴特定 ID 欄位的 `count`。
8. ✅【寫前自我驗證】寫 code 前先在腦中跑一次:每個 `Q['xxx']` 的 'xxx'
   都必須在 avail_cols 中,否則改寫。
8.5 🎯【比率類 KPI 標準骨架】(CRITICAL — 避免型別錯誤)
   當需要新增比率類 KPI 欄位(例:AI 採用率、退單率、完成率),統一走這個骨架:
   ```python
   # Step 1: 在 raw level 建 bool flag (用 metadata 中的 string 狀態做 == 比較)
   Q['is_<state>'] = (Q['<status_col>'] == '<value>')
   #   例:Q['is_ai'] = (Q['review_status']=='Y') & (Q['review_mechanism']=='AI')

   # Step 2: groupby + agg 把 bool sum 成 int count
   agg = Q.groupby('<dim_col>').agg(
       <numerator>=('is_<state>', 'sum'),       # int
       <denominator>=('is_<base>', 'sum'),       # int
   ).reset_index()

   # Step 3: 用兩個 int 欄位相除得 float rate
   agg['<rate>'] = agg['<numerator>'] / agg['<denominator>']

   Q = agg
   ```
   ⚠️ **絕對禁止** 直接用 string 欄位做除法 (例如 `Q['review_mechanism'] / Q['count']`),
   會炸 `TypeError: operation 'rtruediv' not supported for dtype 'str'`。

9. 🚫【Series.first() 禁區】(CRITICAL — 常見幻覺) Series 物件**沒有 `.first()` 方法**!
   - ❌ `Q['hc'].first()` → AttributeError
   - ✅ `Q['hc'].iloc[0]` (取首列值)
   - ✅ `Q.groupby(...).agg(hc=('hc', 'first'))` (此處 'first' 是 agg function 字串,合法)
   - 一般取「每組第一筆」請用 `groupby(...).first()` (回傳 DataFrame,合法)。

9.5 🎯【100% Stacked / 占比分佈樣板】(CRITICAL — 常被誤解)
    當 query 含「**占比分佈**」、「**比例分佈**」、「**100% stacked**」、
    「**percentage stack**」、「占比 stacked bar」+「百分比」之類語義時,
    意思是「每組內各 sub-state 加總應為 100」(per-group 歸一化),**不是**直接顯示 raw count 再加 % 符號。

    正確做法:**Phase B 必須 per-group normalize**,Q 的數值已是 0-100 範圍:
    ```python
    # 例:每類別內各狀態占比(approved / returned / in_progress 加總=100)
    Q = raw_df.copy()
    Q['is_approved']    = (Q['review_status']=='Y') & (Q['review_result']=='Y')
    Q['is_returned']    = (Q['review_status']=='Y') & (Q['review_result']=='N')
    Q['is_in_progress'] = (Q['review_status']=='N')

    agg = Q.groupby('application_category').agg(
        approved=('is_approved', 'sum'),
        returned=('is_returned', 'sum'),
        in_progress=('is_in_progress', 'sum'),
    ).reset_index()

    # ⭐ per-row normalize 到 100
    agg['_total'] = agg['approved'] + agg['returned'] + agg['in_progress']
    agg['approved_pct']    = (agg['approved']    / agg['_total'] * 100).round(2)
    agg['returned_pct']    = (agg['returned']    / agg['_total'] * 100).round(2)
    agg['in_progress_pct'] = (agg['in_progress'] / agg['_total'] * 100).round(2)

    Q = agg.drop(columns=['_total'])  # 不需 _total 給下游
    ```
    這樣 Phase C 拿到的 *_pct 欄位已是 0-100 範圍,stacked 時每柱加總 = 100%。

10. 🎯【保持 long / tidy format】(CRITICAL) Q 最終結果應為 long-form (tidy data):
    每列代表一個 observation,每欄是一個變數。
    - ✅ Long (推薦):`[dim_a, dim_b, value]` 三欄,row 為 dim_a × dim_b 的笛卡兒積。
    - ❌ Wide (除非使用者明說要表格):`pivot(index=dim_a, columns=dim_b)` 把 dim_b 的值散開成欄位名。
    為何?下游 ECharts / Plotly 在 long 格式下能直接用 `Q['col_name'].tolist()` 取值;
    wide 格式下欄位名變動態,Phase C 無法穩定引用。
    例外:純表格呈現 (LLM 後續決定走 `_use_table` fallback) 可保留 wide 以便人類閱讀。
    Heatmap 場景:**強制 long format**,因為 ECharts heatmap series.data 需要 `[[x_idx, y_idx, value], ...]`。

### 範例骨架 (展示流程,具體欄位以你 domain 為準):
```python
import pandas as pd
import numpy as np

Q = raw_df.copy()
# 1. 視需要建立布林輔助欄位以對應 KPI 公式中的條件
#    Q['is_<state>'] = (Q['<status_col>'] == '<value>')

# 2. groupby + agg 算出每組 KPI
agg = Q.groupby('<dimension_col>').agg(
    <metric_a>=('<col_a>', 'size'),           # 計數
    <metric_b>=('<bool_col>', 'sum'),          # 條件計數
    <reference>=('<reference_col>', 'first'),  # 維度級參考值(避免 self-merge)
).reset_index()

# 3. 衍生比率類 KPI
agg['<rate>'] = agg['<numerator>'] / agg['<denominator>']

Q = agg   # ⚠️ 絕對不能忘的終態指派
```
請只輸出 python code,不要前言不要說明。
"""
        user_msg = f"需求:{query}\n計畫:{plan_text}"
        user_msg += self._format_retry_hint(
            previous_code, previous_error,
            cheatsheet=PANDAS_ANTIPATTERN_CHEATSHEET,
        )
        raw = self._call_llm(
            [{"role": "system", "content": system_prompt},
             {"role": "user", "content": user_msg}],
            phase="preprocess",
        )
        return self._strip_code_fence(raw, lang="python")

    # --------------------------------------------------------
    # Phase C: Plotly 繪圖
    # --------------------------------------------------------
    def generate_plot_code(self, query, plan_text="", q_columns=None,
                            previous_code: str = "", previous_error: str = ""):
        cols_info = (
            f"`Q` 已備好,欄位: {q_columns}"
            if q_columns else "`Q` 欄位未知。"
        )
        system_prompt = f"""你是精通 Plotly 的資深前端工程師,負責【C. 視覺化繪圖】。
{cols_info}

### 實作守則 (CRITICAL RULES):
1. 🎯 產出名為 `fig` 的 Plotly Figure 物件。禁止 `fig.show()`、禁止 `streamlit` 相關呼叫。
2. 🚫【禁止二次計算】`Q` 已完美,絕對禁止在此再做過濾/聚合。
3. 🎯【企業視覺規範與 Table 鐵律】(CRITICAL FATAL)
   - 若要畫資料表格 (go.Table),請【完全照抄】以下語法,
     絕對禁止使用不存在的 `textfont` 屬性:
     ```
     fig = go.Figure(data=[go.Table(
         header=dict(values=list(Q.columns), fill_color='#2c3e50',
                     font=dict(color='white')),
         cells=dict(values=[Q[col] for col in Q.columns], fill_color='#f8f9fa',
                    font=dict(color='#2c3e50'))
     )])
     fig.update_layout(title="<你的標題>")
     ```
   - ⚠️ 若資料超過 500 筆,畫 Table 前請務必加上 `Q = Q.head(500)`。
4. 🚫【防幻覺】(CRITICAL FATAL) Plotly 沒有 `matchticks` 屬性。
   如需同步 Y 軸請用 `matches='y'`。
5. 🎨【格式建議】比率類 y 軸請用 `tickformat='.1%'`;
   比較多家公司時,優先 bar / grouped bar / stacked bar,禁止 pie chart。
6. 📦【import】請只 import 真正用到的模組,例如:
   `import plotly.express as px` 或 `import plotly.graph_objects as go`。
請只輸出 python code,不要前言不要說明。
"""
        user_msg = f"需求:{query}\n計畫:{plan_text}"
        user_msg += self._format_retry_hint(previous_code, previous_error)
        raw = self._call_llm(
            [{"role": "system", "content": system_prompt},
             {"role": "user", "content": user_msg}],
            phase="plotly",
        )
        return self._strip_code_fence(raw, lang="python")

    # --------------------------------------------------------
    # Phase C (alt): ECharts option dict
    # --------------------------------------------------------
    def generate_echarts_option(self, query, plan_text="", q_columns=None,
                                 previous_code: str = "", previous_error: str = ""):
        """產生 ECharts 5 option Python dict literal,變數名 `option`。"""
        cols_info = (
            f"`Q` 實際欄位 (THE ONLY SOURCE OF TRUTH): {q_columns}\n"
            "⚠️ 上面這份 q_columns 是 Phase B 實際產出的欄位。\n"
            "⚠️ 不論下方 Domain Knowledge 提到什麼 KPI 名稱,**你只能使用 q_columns 中的欄位**。\n"
            "⚠️ 若你想引用的 KPI 在 q_columns 中沒對應欄位,改用最接近的、或直接放棄該指標。"
            if q_columns else "`Q` 欄位未知。"
        )
        system_prompt = f"""你是精通 Apache ECharts 5 的資深前端工程師,負責【C. 視覺化繪圖 (ECharts)】。
{cols_info}

### 任務說明
請輸出名為 `option` 的 Python dict literal,內容符合 ECharts 5 option 規範。
app 端會把這個 dict 直接餵給 `st_echarts(option, height="520px")` 渲染。

### 實作守則 (CRITICAL RULES):
0. 🚨【欄位名鎖死】(CRITICAL FATAL — 最常犯錯) 你只能用上方 `Q 實際欄位` 中列出的欄位名。
   即使 Domain Knowledge 提到某個 KPI(如 `total_applications`、`average_return_rate`),
   只要該名稱不在 `q_columns` 中,**絕對禁止引用**,會炸 KeyError。
   寫前在心裡跑一遍:每個 `Q['<name>']` 的 `<name>` 都要在 q_columns 中。

1. 🎯【變數產出】(CRITICAL FATAL) 最外層必須宣告 `option` (dict)。
   禁止包在 function/class 內;禁止 `print`;不要再 import 任何套件。
2. 🚫【禁止函式 formatter】(CRITICAL) ECharts 透過 JSON 傳遞,formatter 只能用字串模板
   (如 '{{value}}%'、'{{b}}: {{c}}'),不能放 Python lambda / def。
3. 🚫【禁止二次處理 Q】`Q` 已完美,只允許 `Q['col'].tolist()`、`Q['col'].round(N).tolist()`、
   `(Q['col'] * 100).round(2).tolist()` 這類取值,不可再 groupby/filter。

3.5 🔢【數值精度鐵律】(CRITICAL — 影響使用者觀感) series.data 傳入 ECharts 前
    必須 `.round(N)`,否則 label 會顯示 16 位浮點(如 `1.3076923076923077`)。
    依數量級判斷精度:
    - **整數類**(count、人數、件數): 維持 int,不需 round
      ```python
      "data": Q['total_applications'].tolist()
      ```
    - **比率類**(0-1):先 `* 100` 再 `.round(2)`,顯示百分比
      ```python
      "data": (Q['return_rate'] * 100).round(2).tolist()
      ```
    - **平均/人均/連續類**(0-100 範圍): `.round(2)`
      ```python
      "data": Q['per_capita'].round(2).tolist()
      ```
    - **金額類大數**: `.round(0).astype(int)` 或 `.round(2)`
      ```python
      "data": Q['revenue'].round(0).astype(int).tolist()
      ```
    若 series.label 顯示數值,務必在 `data` 階段就 round,而不是只設 `formatter`
    (formatter 僅控制顯示樣式,不改變 tooltip 與 hover 中的原始值)。
4. 🎯【必備 keys】title、tooltip、xAxis、yAxis、series。bar/line 類請用
   `tooltip: {{"trigger": "axis", "axisPointer": {{"type": "cross"}}}}`。
5. 🎨【視覺規範】
   - 多家公司比較禁止 pie chart,優先 bar / grouped bar / stacked bar。
   - 比率欄位 (0-1) 請先 `* 100`,並設 `yAxis.axisLabel.formatter = "{{value}}%"`。
   - 數量級差很大時使用雙 yAxis (left=count,right=rate)。
   - 類別數 > 20 時加 `dataZoom: [{{"type": "inside"}}, {{"type": "slider"}}]`。

5.3 ⚠️【formatter vs data 語義分離】(CRITICAL — 常見誤用)
   `axisLabel.formatter = "{{value}}%"` **只是把 % 符號加在 label 顯示上,不會把資料 / 100**!
   - ❌ 錯誤做法:data 是 raw count (例如 28000),formatter `{{value}}%` → y 軸顯示 "28000%"
   - ✅ 正解:**data 必須先在 Phase B 轉成 0-100 範圍**,formatter `{{value}}%` 才會顯示正確「28%」
   - 對於「100% stacked」,Phase B 應已 normalize per-group 加總=100;
     Phase C 設 `yAxis: {{max: 100, axisLabel: {{formatter: "{{value}}%"}}}}` 保證軸不超出 100。
5.5 📚【Stack 觸發判斷】(CRITICAL) 當查詢符合以下任一條件,
    **兩個 (或以上) 同類 series 必須加 `"stack": "<相同字串>"` 變成 stacked bar**:
    (a) 互斥狀態對比:任何「A vs B」且 A、B 為 mutually exclusive 的成對狀態
        (通過/拒絕、成功/失敗、是/否、有/無、男/女 等)。判斷依據是
        metadata 中 allowed_values 是否描述了互斥的取值集合。
    (b) 整體量語意關鍵字:「總量」、「合計」、「堆疊」、「累積」、「整體」、「工作量」、「全貌」。
    (c) 隱含結構占比:「哪個 X 量最大」、「占比」、「組成」、「分佈」、「結構」。
    (d) 查詢明說「stacked bar」、「堆疊長條圖」、「100% stacked」、「堆疊圖」。
    違反 = grouped bar(柱子並排)= 看不出每組總量 = 視覺溝通失敗。
    反例(不要 stack):使用者明說「並排比較」、「分別看」、「對照」、「橫向對比」時。

5.6 📚【100% Stacked Bar 完整配方】(配合 Phase B 5.5 規則使用)
    當查詢含「占比分佈」「比例分佈」「100% stacked」+「百分比」時:
    - Phase B 已 per-group normalize 後 Q 的 *_pct 欄位是 0-100 範圍
    - Phase C 需設定:
      ```python
      "yAxis": {{
          "type": "value",
          "max": 100,                                      # ⭐ 鎖住 0-100,不讓 ECharts 自動拉到 10,000
          "axisLabel": {{"formatter": "{{value}}%"}}
      }},
      "series": [
          {{"name": "<state_a>", "type": "bar", "stack": "pct",
            "data": Q['<state_a>_pct'].tolist()}},
          {{"name": "<state_b>", "type": "bar", "stack": "pct",
            "data": Q['<state_b>_pct'].tolist()}},
          {{"name": "<state_c>", "type": "bar", "stack": "pct",
            "data": Q['<state_c>_pct'].tolist()}},
      ],
      ```
    - 所有 series 同名 `stack`,每柱加總 = 100%。
6. 🎁【色盤】使用 `color: ['#5470c6', '#91cc75', '#fac858', '#ee6666', '#73c0de', '#3ba272']`。
7. 📐【grid 留白】`grid: {{"left": 60, "right": 60, "top": 60, "bottom": 40}}` 起手。
8. 📋【表格 fallback + KPI 卡片】(慎重觸發,反例優先)

   ✅ 何時應走 `_use_table`(必須同時符合):
   - 使用者**明確要求**「表格 / 匯總 / KPI overview / dashboard / 執行摘要 / 一覽 / 摘要報表」
   - 且 Q 至少有 4 個以上獨立 KPI 欄位(欄位多到畫圖會擠)

   🚫 何時【嚴禁】走 `_use_table`(即使 Q 有多欄,也必須畫圖):
   - 查詢含「**比較**」、「對比」、「vs」(這是視覺化任務,不是匯總任務)
   - 查詢含「**最多**」、「**最少**」、「**最高**」、「**最低**」、「**排名**」、「**Top N**」
     (這類查詢需要 sorted bar 才能一眼看出極值)
   - 查詢已明確指定圖型(「畫長條圖」、「畫熱力圖」、「scatter」等)
   - 查詢含「**分佈**」、「**占比**」、「**組成**」(這類用 stacked bar / pie / heatmap 更直觀)

   【決策範例】
   - 「畫 KPI 一覽表」→ 走 _use_table ✅
   - 「dashboard 顯示總申請與完成率」→ 走 _use_table ✅
   - 「各公司核准與退件比較,哪家退件最多」→ **畫 sorted stacked bar**,**不走 _use_table** ❌
   - 「比較 AI 與人工的退件率」→ 畫 bar / grouped bar,不走 _use_table ❌
   - 「全公司 KPI 完整一覽:申請、完成、退件、AI 率、員工率」→ 走 _use_table ✅

   若決定走 _use_table,以下是精美表格樣式 (具體欄位請對應你 domain 的 Q):

   ⚠️【數學鐵律 - 比率類 KPI 必看】(CRITICAL FATAL)
   - 比率 / 平均率類 KPI **絕對禁止用 `Q['rate_col'].mean()`!**
     理由:這是「簡單平均率」(每組 rate 加起來除以組數),小組 (低樣本) 會把大組的真實率拉偏。
   - 正確做法:**用加權平均** = `sum(分子) / sum(分母)`
     ```python
     # 例:平均退單率
     total_rate = Q['return_count'].sum() / Q['completed_count'].sum()
     # 例:整體完成率
     total_rate = Q['completed_count'].sum() / Q['total_applications'].sum()
     ```

   ⚠️【總量類 KPI - 防 TOTAL 列雙倍計算】
   - 若 Q 含有 TOTAL / SUMMARY / 合計 等聚合摘要列 (理論上 Phase B 不該加,但保險起見),
     `_kpi_cards` 計算前要先過濾掉:
     ```python
     # 防禦寫法
     _df = Q[~Q['<dim_col>'].astype(str).str.upper().isin(['TOTAL', 'SUMMARY', 'GRAND TOTAL', '合計', '總計'])]
     total = int(_df['<count_col>'].sum())
     ```

   ### 完整 KPI 卡片範例
   ```python
   option = {{
       "_use_table": True,
       "_kpi_cards": [               # 表格上方的 st.metric 卡片 (最多 4 張)
           # 總量類:用 sum,必要時先過濾 TOTAL 列
           {{"label": "<總量類 KPI>",
             "value": f"{{int(Q['<count_col>'].sum()):,}}"}},
           # 比率類:用加權平均 (分子 sum / 分母 sum)
           {{"label": "<品質比率>",
             "value": f"{{(Q['<numerator>'].sum() / Q['<denominator>'].sum() * 100):.2f}}%"}},
           {{"label": "<效率比率>",
             "value": f"{{(Q['<ai_count>'].sum() / Q['<base_count>'].sum() * 100):.1f}}%"}},
           {{"label": "<維度計數>", "value": f"{{len(Q)}}"}},
       ],
       "_table_caption": f"共 {{len(Q)}} 筆"
   }}
   ```
   - app 會自動把名字含 `rate`/`率` 的欄位渲染成漸層進度條 (ProgressColumn),
     大整數加千分位逗號 — 你不需要再對 Q 做格式轉換。
   - `_kpi_cards` 的 value 請用 f-string 在 exec 階段即時運算 Q,
     不要硬編入魔法數字。每張卡 label 控制在 8 字以內。
   - 卡片數建議 3-4 張,涵蓋「總量、品質指標、效率指標」三維度。

### 套用此 domain 的圖表範例 (由 metadata.charting_guidance 自動產生,以實際欄位名為準):
{{ECHARTS_FEW_SHOT}}

請只輸出 python code,不要前言不要說明。
"""
        # 注入此 service 實例對應 domain 的 few-shot
        system_prompt = system_prompt.replace("{{ECHARTS_FEW_SHOT}}", self.echarts_few_shot)
        user_msg = f"需求:{query}\n計畫:{plan_text}"
        user_msg += self._format_retry_hint(previous_code, previous_error)
        raw = self._call_llm(
            [{"role": "system", "content": system_prompt},
             {"role": "user", "content": user_msg}],
            phase="echarts",
        )
        return self._strip_code_fence(raw, lang="python")

    # --------------------------------------------------------
    # Phase D: 商業洞察
    # --------------------------------------------------------
    # --------------------------------------------------------
    # Pre-Phase 0: Meta response (intent != analysis 時的回應)
    # 所有 generator 都是純 metadata 推理,不打 LLM,零延遲。
    # --------------------------------------------------------
    def _sample_questions(self, n: int = 5) -> list[str]:
        """從 metadata 抽出範例問題。"""
        biz = self.task_metadata.get("business_context", {})
        questions = biz.get("main_business_questions") or []
        if questions:
            return questions[:n]
        # Fallback:從 KPI 自動合成
        return [
            f"分析 {kpi['name']}"
            for kpi in list(self.task_metadata.get("kpi_definitions", {}).values())[:n]
        ]

    def generate_intro_response(self) -> str:
        """產品能力介紹。"""
        md = self.task_metadata
        name = md.get("dataset_name") or md.get("dataset_id") or "Dataset"
        biz_desc = md.get("business_context", {}).get("business_description", "")
        sample_qs = self._sample_questions(5)

        out = [f"## 👋 你好,我是 **GenBI 分析助理**\n"]
        out.append(f"我目前載入的資料集是 **{name}**。\n")
        if biz_desc:
            out.append(f"> {biz_desc}\n")
        out.append("### 我能幫你做什麼?")
        out.append("- 📊 **視覺化分析** — bar / stacked / heatmap / scatter / 雙軸圖等")
        out.append("- 📋 **KPI 一覽** — dashboard 風格的執行摘要 + 漸層進度條")
        out.append("- 🧠 **商業洞察** — 自動產出觀察、建議、解讀注意事項")
        out.append("- 🛡️ **誠實拒絕** — 資料不夠時會說明而不亂編")
        out.append("")
        out.append("### 💡 試試這些問題:")
        for q in sample_qs:
            out.append(f"- {q}")
        out.append("")
        out.append("或直接輸入你想分析的任何問題,我會自動規劃流程。")
        return "\n".join(out)

    def generate_data_overview_response(self) -> str:
        """資料概覽 — schema + KPIs + 限制。"""
        md = self.task_metadata
        name = md.get("dataset_name") or md.get("dataset_id") or "Dataset"
        db = md.get("recommended_mongodb", {}).get("database", "—")

        out = [f"## 📋 **{name}** 資料概覽\n"]
        out.append(f"`MongoDB database: {db}`\n")

        # Collections
        out.append("### 📦 資料表")
        for coll_name, coll in md.get("collections", {}).items():
            desc = coll.get("description", "")
            grain = coll.get("grain", "")
            out.append(f"\n**`{coll_name}`** — {desc}")
            if grain:
                out.append(f"  · grain: _{grain}_")
            fields = list(coll.get("fields", {}).keys())
            if fields:
                fields_str = ", ".join(f"`{f}`" for f in fields)
                out.append(f"  · 欄位: {fields_str}")

        # KPIs
        kpis = md.get("kpi_definitions", {})
        if kpis:
            out.append("\n### 📐 可計算的 KPI")
            for kpi_key, kpi in kpis.items():
                line = f"- **{kpi['name']}** (`{kpi_key}`): {kpi['formula']}"
                if kpi.get("important_note"):
                    line += f"  ⚠️ _{kpi['important_note']}_"
                out.append(line)

        # Relationships
        rels = md.get("relationships", [])
        if rels:
            out.append("\n### 🔗 跨表關聯")
            for r in rels:
                out.append(f"- `{r['from_collection']}.{r['from_field']}` "
                           f"→ `{r['to_collection']}.{r['to_field']}` ({r['type']})")

        # Limitations
        lim = md.get("data_limitations", {})
        missing = lim.get("missing_dimensions", [])
        not_supp = lim.get("not_supported_analysis", [])
        if missing or not_supp:
            out.append("\n### ⚠️ 已知資料限制")
            for m in missing:
                out.append(f"- 缺欄位:{m}")
            for n in not_supp:
                out.append(f"- 不支援:{n}")

        return "\n".join(out)

    def generate_data_check_response(self, subject: str) -> str:
        """檢查 metadata 是否含 subject 相關資料。"""
        md = self.task_metadata
        if not subject:
            return ("🤔 不確定你想查什麼。試試 `你有什麼資料?` 看完整資料字典,"
                    "或直接輸入你想做的分析問題。")
        s = subject.lower()

        # 搜尋 fields
        found_fields: list[str] = []
        for coll_name, coll in md.get("collections", {}).items():
            for fname, fmeta in coll.get("fields", {}).items():
                if (s in fname.lower()
                    or s in fmeta.get("description", "").lower()):
                    found_fields.append(f"`{coll_name}.{fname}` — {fmeta.get('description', '')}")

        # 搜尋 KPIs
        found_kpis: list[str] = []
        for kpi_key, kpi in md.get("kpi_definitions", {}).items():
            if (s in kpi_key.lower()
                or s in kpi.get("name", "").lower()
                or s in kpi.get("formula", "").lower()):
                found_kpis.append(f"**{kpi['name']}** (`{kpi_key}`) — {kpi['formula']}")

        # 搜尋 limitations
        lim_match: list[str] = []
        for m in md.get("data_limitations", {}).get("missing_dimensions", []):
            if s in m.lower():
                lim_match.append(m)
        for n in md.get("data_limitations", {}).get("not_supported_analysis", []):
            if s in n.lower():
                lim_match.append(n)

        out: list[str] = []
        if found_fields or found_kpis:
            out.append(f"## ✅ 有「{subject}」相關資料\n")
            if found_fields:
                out.append("### 📋 相關欄位")
                out.extend(f"- {f}" for f in found_fields)
            if found_kpis:
                out.append("\n### 📐 相關 KPI")
                out.extend(f"- {k}" for k in found_kpis)
            out.append("\n💬 你可以直接問例如:「比較各組的 X 表現」或「列出 X 排名」。")
        elif lim_match:
            out.append(f"## ❌ 沒有「{subject}」")
            out.append("\n**資料限制明確標示:**")
            out.extend(f"- {m}" for m in lim_match)
            out.append("\n💡 建議:換個分析角度,例如改看靜態分布或 KPI 比較。")
        else:
            out.append(f"## 🤔 沒在 metadata 中找到「{subject}」的明確對應")
            out.append("")
            out.append("可能的情況:")
            out.append("- 你想找的東西**不在這個資料集**裡")
            out.append("- 或用了不同的名稱(例如「金額」可能對應 `amount` 或 `revenue`)")
            out.append("")
            out.append("試試:")
            out.append("- 輸入 `你有什麼資料?` 看完整 schema")
            out.append("- 直接輸入完整問題,我會嘗試分析或回應「資料不足」")

        return "\n".join(out)

    def generate_guidance_response(self) -> str:
        """新手引導 + 範例分類。"""
        sample_qs = self._sample_questions(8)

        out = ["## 🚀 怎麼開始?\n"]
        out.append("### 直接用自然語言問問題")
        out.append("我會自動處理 5 個階段:**Plan → Pipeline → Pandas → 視覺化 → 商業洞察**。")
        out.append("")
        out.append("### 常見問題類型")
        out.append("")
        out.append("**📊 比較與排名類:**")
        out.append("- 「比較各 X 的 Y」")
        out.append("- 「Top 5 / Bottom 5 的 Z」")
        out.append("- 「哪個 X 的 Y 最高/最低?」")
        out.append("")
        out.append("**📋 概覽 / Dashboard 類:**")
        out.append("- 「給我一份 X 的 dashboard」")
        out.append("- 「KPI 一覽表」")
        out.append("- 「執行摘要」")
        out.append("")
        out.append("**🔍 分佈與占比類:**")
        out.append("- 「畫熱力圖看 X × Y」")
        out.append("- 「按類別分組的占比」")
        out.append("- 「stacked bar 看 X 結構」")
        out.append("")
        if sample_qs:
            out.append("### 💡 來自這個資料集的範例:")
            for q in sample_qs:
                out.append(f"- {q}")
            out.append("")
        out.append("### 🛠 小技巧")
        out.append("- 圖表類型不滿意?直接重問:「改畫成 stacked bar」")
        out.append("- 想看資料字典?輸入「你有什麼資料?」")
        out.append("- Sidebar 可切換 ECharts ↔ Plotly 雙引擎")
        return "\n".join(out)

    def generate_out_of_scope_response(self, query: str = "") -> str:
        """超出資料集範圍的查詢 — 友善引導使用者回到能處理的範圍。"""
        name = self.task_metadata.get("dataset_name") or "this dataset"
        sample_qs = self._sample_questions(5)
        truncated = query.strip()[:80] if query else ""

        out = [f"## 🧭 你問的看起來不在 **{name}** 範圍內\n"]
        if truncated:
            out.append(f"> 你輸入的:_{truncated}_\n")
        out.append("我只能分析這個資料集涵蓋的內容。試試以下方向:\n")
        out.append("- 輸入 `你有什麼資料?` 看完整 schema 與 KPI")
        out.append("- 輸入 `你會做什麼?` 看完整能力介紹")
        out.append("- 輸入 `怎麼開始?` 看範例問題分類")
        if sample_qs:
            out.append("\n**📌 來自此資料集的具體範例問題:**")
            for q in sample_qs:
                out.append(f"- {q}")
        return "\n".join(out)

    def generate_greeting_response(self) -> str:
        """簡短歡迎。"""
        name = self.task_metadata.get("dataset_name") or "this dataset"
        return (
            f"👋 你好!我是 **GenBI 分析助理**,目前載入 **{name}**。\n\n"
            "你可以:\n"
            "- 輸入 `你會做什麼?` 看完整能力介紹\n"
            "- 輸入 `有什麼資料?` 看 schema 與 KPI\n"
            "- 輸入 `怎麼開始?` 看範例問題\n"
            "- 或直接輸入你想分析的問題 🚀"
        )

    def generate_meta_response(self, intent: str, subject: str = "", query: str = "") -> str:
        """根據 intent 派發到對應的 generator。純 metadata 推理,不打 LLM。"""
        if intent == "intro":
            return self.generate_intro_response()
        if intent == "data_overview":
            return self.generate_data_overview_response()
        if intent == "data_check":
            return self.generate_data_check_response(subject)
        if intent == "guidance":
            return self.generate_guidance_response()
        if intent == "greeting":
            return self.generate_greeting_response()
        if intent == "out_of_scope":
            return self.generate_out_of_scope_response(query or subject)
        return ""  # analysis 走原本 pipeline

    # --------------------------------------------------------
    # Phase D: 商業洞察
    # --------------------------------------------------------
    def generate_insight(self, query, plan_text="", q_preview_md: str = ""):
        """根據處理後的 Q 表 (markdown 預覽) 產出商業洞察文字。"""
        system_prompt = f"""你是資深商業分析師,負責撰寫【D. 商業洞察】。
請只以上方 Domain Knowledge 描述的範圍與限制為依據,**不可超出**。

{self.domain_knowledge}

### 通用分析原則:
- 絕對量與比率並陳,避免被小樣本誤導 (注意 metadata 中提及的小樣本警告)。
- 高比率但低量未必營運關鍵;高絕對量但中等比率可能更需要關注 (體量影響)。
- 衍生比率類 KPI 一定要依照 kpi_definitions 中的分母定義,不可自行擴大分母。
- 若 metadata 中某狀態被定義為「進行中 / 待處理」,該筆不可算入「完成」或「失敗」的分子。

### 禁忌 (cautions):
- 不可推論 data_limitations.missing_dimensions 中所列的任何維度
  (例如若 metadata 標示無時間欄位 → 禁止做趨勢 / 月度 / 季節性分析;
   若無金額欄位 → 禁止推論金額;若無部門欄位 → 禁止做部門比較)。
- 不可超出 data_limitations.not_supported_analysis 所列範圍。
- 不可推論非客觀內容 (例如使用者滿意度、員工意圖) 除非 metadata 中明示有相關欄位。

### 輸出格式 (Markdown,精簡):
**🔑 重點摘要** — 3-5 條 bullet,每條一句話、含具體數字。
**📌 觀察與建議** — 2-3 條 bullet,提出可行動的建議或追蹤指標。
**⚠️ 解讀注意事項** — 1-2 條 bullet,主動標註小樣本或資料限制。
"""
        user_msg = (
            f"使用者問題:{query}\n\n"
            f"分析計畫:\n{plan_text}\n\n"
            f"處理後資料表 (Q,前 30 列 markdown):\n{q_preview_md}\n\n"
            f"請產出商業洞察。"
        )
        try:
            return {
                "status": "success",
                "message": self._call_llm(
                    [{"role": "system", "content": system_prompt},
                     {"role": "user", "content": user_msg}],
                    temperature=0.3,
                    phase="insight",
                ),
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}
```

---

## 15.5 · `app.py`

```python
"""
tFlex GenBI — Agentic Workflow (Streamlit + vLLM + MongoDB)

Phase 0 → Plan       (LLM 規劃)
Phase A → MongoDB    (LLM 產 pipeline → 真實 DB 撈取 / CSV fallback)
Phase B → Pandas     (LLM 產 preprocess code → 計算 KPI)
Phase C → Plotly     (LLM 產 plot code → 視覺化)
Phase D → Insight    (LLM 產商業洞察文字)
"""

import os
import json
import traceback
from pathlib import Path

import pandas as pd
import streamlit as st
from pymongo import MongoClient
from pymongo.errors import PyMongoError, ServerSelectionTimeoutError
from streamlit_echarts import st_echarts

from llm_service import (
    LLMService,
    is_dashboard_query,
    classify_intent,
    is_followup_query,
)
import config


# ============================================================
# ⚙️ 環境設定:全部由 config.py 提供 (.env / 環境變數驅動)
# ============================================================
MONGO_URI = config.MONGO_URI
MONGO_DB_NAME = config.MONGO_DB
MONGO_COLL_APPLICATIONS = config.MONGO_COLL_APPLICATIONS
MONGO_COLL_COMPANY_HC = config.MONGO_COLL_COMPANY_HC
MONGO_SERVER_TIMEOUT_MS = config.MONGO_SERVER_SELECTION_TIMEOUT_MS

LLM_PROVIDER = config.LLM_PROVIDER
LLM_BASE_URL = config.LLM_BASE_URL
LLM_API_URL = config.LLM_API_URL
LLM_API_KEY = config.LLM_API_KEY
LLM_MODEL = config.LLM_MODEL
LLM_TEMPERATURE = config.LLM_TEMPERATURE
LLM_TIMEOUT_S = config.LLM_TIMEOUT_S

DATA_DIR = config.DATA_DIR
CSV_APPLICATIONS = DATA_DIR / "tflex_applications_rawdata_v2.csv"
CSV_COMPANY_HC = DATA_DIR / "tflex_company_hc_rawdata_v2.csv"

# ============================================================
# 🔌 MongoDB 連線 (cached)
# ============================================================
@st.cache_resource(show_spinner=False)
def get_mongo_db():
    """嘗試建立 MongoDB 連線,失敗則回傳 None 並附帶錯誤訊息。"""
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=MONGO_SERVER_TIMEOUT_MS)
        # 強制 ping 一次確認真的連得上
        client.admin.command("ping")
        return client[MONGO_DB_NAME], None
    except (ServerSelectionTimeoutError, PyMongoError) as e:
        return None, str(e)
    except Exception as e:
        return None, str(e)


@st.cache_data(show_spinner=False)
def load_csv_fallback() -> pd.DataFrame:
    """CSV fallback:把兩張表 left-join 後當成 raw_df 來源。"""
    apps = pd.read_csv(
        CSV_APPLICATIONS,
        dtype={"employee_id": str, "application_no": str, "company_code": str},
        keep_default_na=False, na_values=[""],
    )
    hc = pd.read_csv(
        CSV_COMPANY_HC,
        dtype={"company_code": str},
    )
    merged = apps.merge(hc, on="company_code", how="left")
    return merged


# ============================================================
# 🪛 Pipeline 解譯器 (僅在 CSV fallback 模式下使用)
#     - 把 LLM 產的 pipeline 當「過濾意圖」執行在 pandas 上
#     - 僅支援 $match / $project,其他 stage 一律忽略
# ============================================================
def _apply_match(df: pd.DataFrame, match_doc: dict) -> pd.DataFrame:
    out = df
    for field, cond in match_doc.items():
        if field.startswith("$"):
            # $and / $or 之類的複雜邏輯先跳過
            continue
        if isinstance(cond, dict):
            if "$in" in cond and field in out.columns:
                out = out[out[field].isin(cond["$in"])]
            elif "$eq" in cond and field in out.columns:
                out = out[out[field] == cond["$eq"]]
            elif "$ne" in cond and field in out.columns:
                out = out[out[field] != cond["$ne"]]
        else:
            if field in out.columns:
                out = out[out[field] == cond]
    return out


def _apply_project(df: pd.DataFrame, project_doc: dict) -> pd.DataFrame:
    keep = [k for k, v in project_doc.items() if v in (1, True) and k != "_id"]
    # 處理 alias / rename,例如 "hc": "$hc_info.hc"  → 若已 join 過,直接保留 hc 即可
    keep = [k for k in keep if k in df.columns]
    if not keep:
        return df
    return df[keep].copy()


def execute_pipeline_on_pandas(raw_df: pd.DataFrame, pipeline: list) -> pd.DataFrame:
    """在 fallback 模式下,把 pipeline 的 $match / $project 套用到已 join 的 DataFrame。"""
    out = raw_df.copy()
    for stage in pipeline:
        if "$match" in stage:
            out = _apply_match(out, stage["$match"])
        elif "$project" in stage:
            out = _apply_project(out, stage["$project"])
        else:
            # $lookup / $unwind / 其他 stage 在 fallback 已等同預先 join,直接略過
            continue
    return out


# ============================================================
# 🎨 精美表格渲染:KPI 卡片 + Auto column_config
# ============================================================
def try_recover_Q(ns: dict, raw_df: pd.DataFrame) -> tuple["pd.DataFrame | None", str | None]:
    """
    Phase B 安全網:若 LLM 忘了把聚合結果指派回 Q,試著在 namespace 中找替代品。
    回傳 (替代 DataFrame, 訊息) — 如果不需要替代或找不到,回傳 (None, None)。
    觸發條件:Q 列數 == raw_df 列數 (高度疑似 LLM 沒做最終指派)。
    """
    Q = ns.get("Q")
    if Q is None or not isinstance(Q, pd.DataFrame):
        return None, None
    if Q.shape[0] != raw_df.shape[0]:
        return None, None  # Q 已經聚合過,不需要救援

    # 找出比 raw_df 「更聚合」的候選 DataFrame
    candidates: list[tuple[str, pd.DataFrame]] = []
    for name, val in ns.items():
        if name == "Q" or name.startswith("_") or not isinstance(val, pd.DataFrame):
            continue
        # 列數比 raw_df 少 (聚合過),且至少 1 列
        if 1 <= len(val) < len(raw_df) * 0.9:
            candidates.append((name, val))

    if not candidates:
        return None, None

    # 選列數最少的 (最聚合的) — 通常那就是 LLM 的「最終結果」
    candidates.sort(key=lambda x: len(x[1]))
    name, df = candidates[0]
    msg = (
        f"⚠️ Phase B 安全網觸發:LLM 似乎忘了 `Q = {name}` 終態指派,"
        f"自動 fallback 到 `{name}` (shape={df.shape})。建議重新 prompt 強調終態指派。"
    )
    return df, msg


def render_pretty_table(Q: pd.DataFrame, option: dict | None = None, key_prefix: str = "") -> None:
    """
    取代純 st.dataframe 的進階表格:
    - option 可選帶入 `_kpi_cards` (list of {label, value, delta, help}) → 表格上方顯示 st.metric 卡片
    - option 可選帶入 `_table_caption` → 表格下方 caption
    - 自動將比率欄位 (名含 rate / ratio / 率) 轉為 ProgressColumn (含百分比格式)
    - 整數欄位自動千分位逗號
    - 表格高度依列數動態縮放,最多 800px
    """
    option = option or {}

    # === 1. KPI 卡片區 (頂部) ===
    cards = option.get("_kpi_cards") or []
    if cards:
        n_cols = min(len(cards), 4)
        row_objs = st.columns(n_cols)
        for i, card in enumerate(cards):
            with row_objs[i % n_cols]:
                st.metric(
                    label=str(card.get("label", "—")),
                    value=str(card.get("value", "—")),
                    delta=card.get("delta"),
                    help=card.get("help"),
                )
        st.markdown("")  # 與下方表格留白

    # === 2. 自動 column_config ===
    display_Q = Q.copy()
    column_cfg: dict = {}

    rate_keywords = ("rate", "ratio", "百分", "佔比")
    for col in display_Q.columns:
        s = display_Q[col]
        col_str = str(col)
        col_lower = col_str.lower()
        is_rate = any(k in col_lower for k in rate_keywords) or "率" in col_str

        if not pd.api.types.is_numeric_dtype(s):
            continue

        # 比率欄位:0-1 範圍 → 轉百分比 + ProgressColumn
        s_clean = s.dropna()
        if is_rate and not s_clean.empty and s_clean.max() <= 1.5:
            display_Q[col] = (s * 100).round(2)
            column_cfg[col] = st.column_config.ProgressColumn(
                col_str,
                format="%.1f%%",
                min_value=0,
                max_value=100,
                help="比率欄位 — 進度條長度反映 0-100% 範圍",
            )
        # 整數欄位:千分位逗號
        elif pd.api.types.is_integer_dtype(s) or (s_clean % 1 == 0).all():
            column_cfg[col] = st.column_config.NumberColumn(col_str, format="%,d")
        # 小數欄位:兩位小數
        else:
            column_cfg[col] = st.column_config.NumberColumn(col_str, format="%.2f")

    # === 3. 渲染表格 ===
    n_rows = len(display_Q)
    table_height = min(800, 35 * (n_rows + 1) + 20)
    st.dataframe(
        display_Q,
        use_container_width=True,
        column_config=column_cfg,
        hide_index=True,
        height=table_height,
    )

    # === 4. 表格 caption ===
    if option.get("_table_caption"):
        st.caption(option["_table_caption"])


# ============================================================
# 🚀 系統初始化
# ============================================================
st.set_page_config(page_title="tFlex GenBI", page_icon="📊", layout="wide")
st.title("📊 tFlex 員工福利申請 GenBI 系統")
st.markdown(f"**Powered by `{LLM_MODEL}` via OpenAI-compatible endpoint**")

if "messages" not in st.session_state:
    st.session_state.messages = []

# 用於延續性分析:儲存上一次成功(或部分成功)的分析脈絡
if "last_analysis" not in st.session_state:
    st.session_state.last_analysis = None

# 用於 sample question 按鈕注入到 chat input
if "pending_query" not in st.session_state:
    st.session_state.pending_query = None

# LLMService 用 session_state 快取避免重複建立 OpenAI client
if "llm_service" not in st.session_state:
    st.session_state.llm_service = LLMService(
        api_url=LLM_API_URL,
        api_key=LLM_API_KEY,
        model_name=LLM_MODEL,
        timeout_s=LLM_TIMEOUT_S,
        default_temperature=LLM_TEMPERATURE,
    )
llm_service = st.session_state.llm_service

mongo_db, mongo_err = get_mongo_db()

# ============================================================
# 🧭 Sidebar:資料源狀態 + 切換
# ============================================================
with st.sidebar:
    st.header("🔧 系統狀態")

    st.markdown(f"**LLM ({LLM_PROVIDER})**")
    st.code(
        f"endpoint: {LLM_BASE_URL}\n"
        f"model:    {LLM_MODEL}\n"
        f"timeout:  {LLM_TIMEOUT_S:.0f}s\n"
        f"temp:     {LLM_TEMPERATURE}",
        language="text",
    )

    st.markdown("**MongoDB**")
    if mongo_db is not None:
        st.success(f"✅ Connected — {MONGO_DB_NAME}")
    else:
        st.warning("⚠️ 無法連線,將自動 fallback 到 CSV")
        with st.expander("錯誤詳情"):
            st.code(mongo_err or "(unknown error)", language="text")

    available_sources = ["MongoDB (real)"] if mongo_db is not None else []
    if CSV_APPLICATIONS.exists() and CSV_COMPANY_HC.exists():
        available_sources.append("CSV fallback (dev)")
    if not available_sources:
        available_sources = ["⛔ 無可用資料源"]

    data_source = st.radio(
        "資料來源",
        options=available_sources,
        index=0,
        help="MongoDB 未啟動或未匯入資料時,可選擇 CSV fallback 來開發測試。",
    )

    st.divider()
    chart_engine = st.radio(
        "📊 圖表引擎",
        options=["ECharts", "Plotly"],
        index=0,
        help="ECharts:BI 風格動畫與互動,適合 demo 與管理層匯報。"
             "Plotly:Pythonic、表格類渲染直接 (go.Table)。",
    )

    enable_insight = st.toggle("啟用 Phase D 商業洞察", value=True)
    st.divider()

    # 接續分析狀態
    if st.session_state.last_analysis:
        st.markdown("**🔗 延續性分析狀態**")
        la = st.session_state.last_analysis
        st.caption(f"前次:_{(la.get('query') or '')[:40]}_")
        if st.button("🆕 開始新分析(清除延續脈絡)"):
            st.session_state.last_analysis = None
            st.rerun()
    if st.session_state.messages:
        if st.button("🗑️ 清除對話歷史"):
            st.session_state.messages = []
            st.session_state.last_analysis = None
            st.rerun()

    st.divider()
    st.caption("💡 環境變數可調:HRDA_MODEL_BASE_URL / HRDA_MODEL_NAME / MONGO_URI …")

# ============================================================
# 💬 歷史訊息渲染
# ============================================================
for idx, msg in enumerate(st.session_state.messages):
    with st.chat_message(msg["role"]):
        st.write(msg["content"])
        # 圖表回放 — Plotly fig 或 ECharts option dict 二擇一
        if msg.get("fig") is not None:
            st.plotly_chart(msg["fig"], use_container_width=True)
        elif msg.get("echarts_option") is not None:
            st_echarts(
                options=msg["echarts_option"],
                height="520px",
                key=f"echarts_history_{idx}",
            )
        elif msg.get("table_df") is not None:
            render_pretty_table(
                msg["table_df"],
                msg.get("table_option"),
                key_prefix=f"hist_{idx}",
            )

        if msg.get("insight"):
            with st.expander("🧠 商業洞察", expanded=False):
                st.markdown(msg["insight"])

# ============================================================
# 🚀 核心執行引擎 (Agentic Workflow)
# ============================================================
# 極簡開場 — 不顯示預設範例 / 按鈕,引導資訊在使用者主動問時才出現
chat_input_value = st.chat_input(
    "輸入你想分析的問題;若不確定可問「你會做什麼?」「有什麼資料?」「怎麼開始?」"
)
# pending_query 機制保留(供未來功能注入查詢使用,例如 follow-up 建議按鈕)
query = chat_input_value or st.session_state.pending_query
if st.session_state.pending_query:
    st.session_state.pending_query = None  # consume

if query:
    st.session_state.messages.append({"role": "user", "content": query})
    with st.chat_message("user"):
        st.write(query)

    # ============================================================
    # 🎯 Pre-Phase 0 · Intent Router
    # 偵測非分析類查詢(intro / data_overview / data_check / guidance / greeting)
    # 直接回應 meta response,不走 Phase 0/A/B/C/D
    # ============================================================
    # 🔧 routing 優先序:explicit intent → follow-up → out_of_scope → analysis
    intent_result = llm_service.classify_intent_for_query(
        query, last_analysis=st.session_state.last_analysis
    )
    intent = intent_result.get("intent", "analysis")

    if intent != "analysis":
        with st.chat_message("assistant"):
            # out_of_scope 與 data_check 都需要 query 內容做 subject 萃取
            meta_md = llm_service.generate_meta_response(
                intent,
                subject=intent_result.get("subject", ""),
                query=query,
            )
            st.markdown(meta_md)
            st.session_state.messages.append({
                "role": "assistant",
                "content": meta_md,
                "meta_intent": intent,
            })
        st.stop()  # 不進入分析 pipeline

    # ============================================================
    # 🔗 Pre-Phase 0 · Follow-up flag(由 classifier 提供)
    # 延續性分析會在 Phase 0 注入前次脈絡
    # ============================================================
    is_followup = intent_result.get("is_followup", False)
    followup_context = st.session_state.last_analysis if is_followup else None

    with st.chat_message("assistant"):
        if is_followup:
            st.info(
                "🔗 **偵測為延續性分析** — 將帶入前次的 Q 欄位、圖表類型、計畫摘要等脈絡到 Phase 0。"
                "若需開新分析,可在左側 sidebar 按「🆕 開始新分析」清除脈絡。"
            )
        status = st.status("🧠 Agent 思考與執行中...", expanded=True)
        workflow_namespace = {"pd": pd, "np": __import__("numpy")}
        final_fig = None
        insight_text = None

        try:
            # ============================================================
            # Phase 0 — 制定分析計畫
            # ============================================================
            status.update(label="📋 Phase 0:制定分析計畫..." +
                          (" (含接續脈絡)" if followup_context else ""))
            plan_res = llm_service.generate_plan(query, followup_context=followup_context)
            if plan_res["status"] == "error":
                raise Exception(plan_res["message"])
            plan_text = plan_res["message"]

            with st.expander("📋 檢視 AI 執行計畫", expanded=False):
                st.markdown(plan_text)

            # 🛑 拒絕短路:Plan 若標示 [REFUSE] 或明確拒絕,直接呈現結果並中止
            plan_head = plan_text.strip()[:400]
            is_refusal = (
                plan_head.startswith("[REFUSE]")
                or "[REFUSE]" in plan_head
                or any(kw in plan_head for kw in (
                    "無法執行", "無法分析", "無法計算", "無法進行",
                    "不支援此分析", "資料限制觸犯",
                ))
            )

            if is_refusal:
                status.update(label="🛑 偵測到 data_limitations,中止分析",
                              state="error", expanded=False)
                clean_msg = plan_text.replace("[REFUSE]", "").strip()
                st.warning(
                    "⚠️ **資料不足** — 此分析觸犯 metadata 中的 data_limitations,"
                    "系統不執行 Phase A/B/C/D。"
                )
                st.markdown(clean_msg)
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": f"⚠️ 資料不足\n\n{clean_msg}",
                })
                st.stop()  # 中止後續 phase

            # ============================================================
            # Phase A — MongoDB pipeline → 撈資料
            # ============================================================
            status.update(label="🛢️ Phase A:產生 MongoDB pipeline 並撈資料...")
            db_json_str = llm_service.generate_pipeline(query, plan_text)

            try:
                db_instruction = json.loads(db_json_str)
            except json.JSONDecodeError:
                raise ValueError(f"LLM 未能回傳合法的 JSON 格式:\n{db_json_str}")

            start_collection = db_instruction.get("start_collection")
            pipeline = db_instruction.get("pipeline", [])

            with st.expander(f"🛠️ 檢視 MongoDB Pipeline (起點: {start_collection})", expanded=False):
                st.code(json.dumps(db_instruction, indent=2, ensure_ascii=False), language="json")

            # === 依資料來源實際撈取 ===
            if data_source.startswith("MongoDB") and mongo_db is not None:
                cursor = mongo_db[start_collection].aggregate(pipeline)
                raw_df = pd.DataFrame(list(cursor))
                source_label = f"MongoDB ({MONGO_DB_NAME}.{start_collection})"
            elif data_source.startswith("CSV"):
                merged = load_csv_fallback()
                raw_df = execute_pipeline_on_pandas(merged, pipeline)
                source_label = "CSV fallback (本機 pandas)"
            else:
                raise RuntimeError("沒有可用的資料源,請啟動 MongoDB 或確認 CSV 路徑。")

            if "_id" in raw_df.columns:
                raw_df = raw_df.drop(columns=["_id"])
            if raw_df.empty:
                raise ValueError("Phase A 撈取結果為空,請檢查 pipeline 的 $match 條件。")

            workflow_namespace["raw_df"] = raw_df
            st.markdown(
                f"📥 **Phase A 完成** · 來源:{source_label} · "
                f"撈出 {len(raw_df):,} 筆明細 · 欄位:{list(raw_df.columns)}"
            )
            st.dataframe(raw_df.head(100), use_container_width=True)

            # ============================================================
            # Phase B — Pandas 處理 (帶錯誤回饋自我修正)
            # ============================================================
            status.update(label="🐍 Phase B:Pandas 計算與 KPI 處理...")
            avail_cols = list(raw_df.columns)
            try:
                raw_df_sample_md = raw_df.head(3).to_markdown(index=False)
            except Exception:
                raw_df_sample_md = raw_df.head(3).to_string(index=False)

            # 🎯 結構性路由:偵測 dashboard 場景 → 走 row-level pass-through
            dashboard_mode = is_dashboard_query(query)
            if dashboard_mode:
                st.info("📊 偵測為 dashboard 查詢,Phase B 走 row-level pass-through(scalar 由 Phase C 處理)")

            prep_code = None
            prep_err = None

            for attempt in range(3):
                prep_code = llm_service.generate_preprocess_code(
                    query, plan_text, avail_cols,
                    raw_df_sample=raw_df_sample_md,
                    dashboard_hint=dashboard_mode,
                    previous_code=prep_code if attempt > 0 else "",
                    previous_error=prep_err if attempt > 0 else "",
                )
                try:
                    exec(prep_code, workflow_namespace, workflow_namespace)
                    if "Q" not in workflow_namespace:
                        raise ValueError("LLM 未在最外層宣告變數 Q!")
                    # 🛡️ Phase B 安全網:救援忘記終態指派的情況
                    fallback_df, recover_msg = try_recover_Q(workflow_namespace, raw_df)
                    if recover_msg:
                        st.warning(recover_msg)
                        workflow_namespace["Q"] = fallback_df
                    with st.expander("🐍 檢視 Python 資料處理腳本", expanded=False):
                        st.code(prep_code, language="python")
                    break
                except Exception:
                    prep_err = traceback.format_exc()
                    if attempt < 2:  # 還有重試機會
                        st.toast(
                            f"⚠️ Phase B 第 {attempt + 1} 次失敗,帶錯誤回饋 + anti-pattern 速查表重生...",
                            icon="🔄",
                        )
                    else:
                        st.error("❌ **Phase B 連續失敗 3 次**")
                        st.info("👇 LLM 最後一版腳本:")
                        st.code(prep_code, language="python")
                        with st.expander("🔍 展開 Traceback"):
                            st.code(prep_err, language="bash")
                        st.stop()

            Q = workflow_namespace.get("Q")
            if Q is None or (hasattr(Q, "empty") and Q.empty):
                raise ValueError("Phase B 處理後 Q 為空,請檢查篩選條件。")

            st.markdown(f"⚙️ **Phase B 完成** · KPI 已計算 (共 {len(Q):,} 筆)")
            st.dataframe(Q.head(100), use_container_width=True)

            # ============================================================
            # Phase C — 視覺化 (引擎依 sidebar 切換 / 帶錯誤回饋自我修正)
            # ============================================================
            status.update(label=f"🎨 Phase C:{chart_engine} 繪圖中...")
            q_cols = list(Q.columns)
            plot_code = None
            plot_err = None
            final_fig = None
            final_option = None
            use_table_fallback = False

            for attempt in range(3):
                if chart_engine == "ECharts":
                    plot_code = llm_service.generate_echarts_option(
                        query, plan_text, q_cols,
                        previous_code=plot_code if attempt > 0 else "",
                        previous_error=plot_err if attempt > 0 else "",
                    )
                else:
                    plot_code = llm_service.generate_plot_code(
                        query, plan_text, q_cols,
                        previous_code=plot_code if attempt > 0 else "",
                        previous_error=plot_err if attempt > 0 else "",
                    )
                    # 🛡️ 物理防護罩:消滅 textfont 幻覺
                    if "go.Table" in plot_code and "textfont" in plot_code:
                        plot_code = plot_code.replace("textfont", "font")

                try:
                    exec(plot_code, workflow_namespace, workflow_namespace)
                    if chart_engine == "ECharts":
                        final_option = workflow_namespace.get("option")
                        if not isinstance(final_option, dict):
                            raise ValueError("執行腳本後,未產生 dict 型別的 `option`。")
                        # 表格 fallback 旗標
                        use_table_fallback = bool(final_option.get("_use_table"))
                        # 基本健全性:非表格情境必須有 series
                        if not use_table_fallback and "series" not in final_option:
                            raise ValueError("ECharts option 缺少必備 key `series`。")
                    else:
                        final_fig = workflow_namespace.get("fig")
                        if not final_fig:
                            raise ValueError("執行腳本後,未產生 `fig` 物件。")

                    with st.expander(f"🎨 檢視 {chart_engine} 繪圖腳本", expanded=False):
                        st.code(plot_code, language="python")
                    break
                except Exception:
                    plot_err = traceback.format_exc()
                    if attempt < 2:
                        st.toast(
                            f"⚠️ Phase C ({chart_engine}) 第 {attempt + 1} 次失敗,帶錯誤回饋重生...",
                            icon="🔄",
                        )
                    else:
                        # 🛡️ 3 次都失敗 — 結構性 fallback:渲染表格而非 st.stop()
                        st.warning(
                            f"⚠️ Phase C ({chart_engine}) 連續 3 次失敗,自動降級為表格渲染 "
                            f"(`render_pretty_table`),你仍可看到 Q 的內容。"
                        )
                        with st.expander("👇 展開最後一版失敗腳本與 traceback", expanded=False):
                            st.code(plot_code, language="python")
                            st.code(plot_err, language="bash")
                        # 設定 fallback 狀態 — 後面渲染區塊會走 use_table_fallback 分支
                        use_table_fallback = True
                        final_option = {"_use_table": True, "_phase_c_fallback": True}
                        final_fig = None

            status.update(label="🖼️ Phase C 完成,繪圖呈現中...")
            if chart_engine == "ECharts":
                if use_table_fallback:
                    st.info("📋 LLM 判斷此查詢更適合用表格呈現,套用精美 KPI 表格樣式。")
                    render_pretty_table(Q, final_option, key_prefix=f"live_{len(st.session_state.messages)}")
                else:
                    st_echarts(
                        options=final_option,
                        height="520px",
                        key=f"echarts_live_{len(st.session_state.messages)}",
                    )
            else:
                st.plotly_chart(final_fig, use_container_width=True)

            # ============================================================
            # Phase D — 商業洞察 (可選)
            # ============================================================
            if enable_insight:
                status.update(label="🧠 Phase D:產生商業洞察...")
                # 給 LLM 一個 markdown 預覽,避免 prompt 過長
                try:
                    q_preview_md = Q.head(30).to_markdown(index=False)
                except Exception:
                    q_preview_md = Q.head(30).to_string(index=False)

                insight_res = llm_service.generate_insight(query, plan_text, q_preview_md)
                if insight_res["status"] == "success":
                    insight_text = insight_res["message"]
                    with st.expander("🧠 商業洞察", expanded=True):
                        st.markdown(insight_text)
                else:
                    st.warning(f"Phase D 失敗 (不影響主流程):{insight_res['message']}")

            # ============================================================
            # 🎉 最終呈現
            # ============================================================
            status.update(label="✅ 分析完成", state="complete", expanded=False)

            st.session_state.messages.append({
                "role": "assistant",
                "content": "分析已完成,如上方資料、圖表與洞察所示。",
                "fig": final_fig,
                "echarts_option": None if use_table_fallback else final_option,
                "table_df": Q if use_table_fallback else None,
                "table_option": final_option if use_table_fallback else None,
                "insight": insight_text,
            })

            # 🔗 寫入「上次分析脈絡」供下一輪 follow-up 使用
            if use_table_fallback:
                chart_descriptor = f"{chart_engine} table fallback"
            elif chart_engine == "ECharts" and isinstance(final_option, dict):
                series_types = [s.get("type", "?") for s in final_option.get("series", [])]
                chart_descriptor = f"ECharts ({'/'.join(series_types) or 'unknown'})"
            elif chart_engine == "Plotly":
                chart_descriptor = "Plotly chart"
            else:
                chart_descriptor = chart_engine

            st.session_state.last_analysis = {
                "query": query,
                "plan_summary": plan_text[:400],
                "Q_cols": list(Q.columns) if Q is not None else [],
                "chart_engine": chart_engine,
                "chart_descriptor": chart_descriptor,
                "is_dashboard": dashboard_mode,
                "was_followup": is_followup,
            }

        except Exception as e:
            status.update(label="❌ 系統執行中斷", state="error", expanded=True)
            st.error(f"發生系統級錯誤:\n{str(e)}")
            with st.expander("🔍 展開 Traceback"):
                st.code(traceback.format_exc(), language="bash")
```

---

## 15.6 · `tflex_task_metadata_agent_v3.py` (metadata 範本)

> 這是 tFlex domain 的範例 metadata,新 domain 接入時可參考此結構複製改寫。

```python
# -*- coding: utf-8 -*-
# Agent-oriented metadata for tFlex employee benefit application dataset.
# Usage: from tflex_task_metadata_agent_v3 import TASK_METADATA

TASK_METADATA = {
    "metadata_version": "agent_v3",
    "dataset_id": "tflex_employee_benefit_application",
    "dataset_name": "tFlex Employee Benefit Application Dataset",
    "generated_at": "2026-05-12T05:01:55",
    "purpose": "Agent-oriented metadata for LLM prompt context, text-to-MongoDB, data preprocessing, charting, reporting, and insight generation.",
    "source_files": {
        "applications_rawdata_csv": "tflex_applications_rawdata_v2.csv",
        "company_hc_rawdata_csv": "tflex_company_hc_rawdata_v2.csv",
        "mongodb_import_script": "import_tflex_to_mongodb.py"
    },
    "recommended_mongodb": {
        "database": "tflex_demo",
        "collections": {
            "applications": "tflex_applications",
            "company_hc": "tflex_company_hc"
        },
        "join_key": "company_code"
    },
    "business_context": {
        "system_name": "tFlex",
        "domain": "Employee benefit application",
        "business_description": "This dataset records employee benefit application forms submitted through tFlex. Each application belongs to one employee and one subsidiary company. Applications may be completed or still in progress. Completed applications may be approved for payment or returned.",
        "business_grain": {
            "tflex_applications": "one document per benefit application form",
            "tflex_company_hc": "one document per subsidiary company headcount reference"
        },
        "main_business_questions": [
            "Which companies have higher or lower employee submission rates?",
            "Which companies have higher return rates?",
            "How many applications are still in progress?",
            "What is the distribution of benefit application categories?",
            "What is the AI review adoption rate among completed applications?",
            "Which companies have high application volume and high return workload?"
        ]
    },
    "collections": {
        "tflex_applications": {
            "description": "Application-level tFlex benefit claim records",
            "grain": "one document per application form",
            "primary_key": "application_no",
            "fields": {
                "employee_id": {
                    "type": "string",
                    "description": "Six-digit employee ID. Keep as string, not integer.",
                    "format": "^[0-9]{6}$"
                },
                "company_code": {
                    "type": "string",
                    "description": "Three-letter subsidiary company code.",
                    "allowed_values": [
                        "TST",
                        "TSC",
                        "TSA",
                        "TSN",
                        "JSM",
                        "TWT",
                        "TSU",
                        "TDI",
                        "TDJ",
                        "ESM",
                        "TSE",
                        "TRJ",
                        "TDC",
                        "TSJ",
                        "TSK"
                    ],
                    "join_to": "tflex_company_hc.company_code"
                },
                "application_no": {
                    "type": "string",
                    "description": "Eight-digit sequential application number. Keep as string.",
                    "format": "^[0-9]{8}$"
                },
                "application_category": {
                    "type": "string",
                    "description": "Benefit application category.",
                    "allowed_values": [
                        "Family Care",
                        "Wellness",
                        "Medical & Insurance",
                        "Development & Voluteering"
                    ]
                },
                "review_status": {
                    "type": "string",
                    "description": "Y = completed, N = in progress.",
                    "allowed_values": {
                        "Y": "completed",
                        "N": "in progress"
                    }
                },
                "review_result": {
                    "type": "string_or_null",
                    "description": "Y = approved/payable, N = returned/rejected, null = not completed yet.",
                    "allowed_values": {
                        "Y": "approved and payable",
                        "N": "returned or rejected",
                        "null": "not completed yet"
                    }
                },
                "review_mechanism": {
                    "type": "string_or_null",
                    "description": "AI = AI review, H = human review, null = not completed yet.",
                    "allowed_values": {
                        "AI": "AI review",
                        "H": "human review",
                        "null": "not completed yet"
                    }
                }
            }
        },
        "tflex_company_hc": {
            "description": "Company-level headcount reference view",
            "grain": "one document per company",
            "primary_key": "company_code",
            "fields": {
                "company_code": {
                    "type": "string",
                    "description": "Three-letter subsidiary company code.",
                    "allowed_values": [
                        "TST",
                        "TSC",
                        "TSA",
                        "TSN",
                        "JSM",
                        "TWT",
                        "TSU",
                        "TDI",
                        "TDJ",
                        "ESM",
                        "TSE",
                        "TRJ",
                        "TDC",
                        "TSJ",
                        "TSK"
                    ]
                },
                "hc": {
                    "type": "integer",
                    "description": "Company headcount"
                }
            }
        }
    },
    "relationships": [
        {
            "type": "many_to_one",
            "from_collection": "tflex_applications",
            "from_field": "company_code",
            "to_collection": "tflex_company_hc",
            "to_field": "company_code",
            "description": "Many application documents belong to one company headcount reference record."
        }
    ],
    "kpi_definitions": {
        "headcount": {
            "name": "H/C",
            "formula": "hc from tflex_company_hc"
        },
        "submitter_count": {
            "name": "送單人數",
            "formula": "distinct count of employee_id in tflex_applications"
        },
        "total_applications": {
            "name": "總申請張數",
            "formula": "count of documents in tflex_applications"
        },
        "pay_count": {
            "name": "PAY",
            "formula": "count where review_status='Y' and review_result='Y'"
        },
        "return_count": {
            "name": "RTN",
            "formula": "count where review_status='Y' and review_result='N'"
        },
        "completed_count": {
            "name": "Completed applications",
            "formula": "count where review_status='Y'"
        },
        "in_progress_count": {
            "name": "In-progress applications",
            "formula": "count where review_status='N'"
        },
        "employee_submission_rate": {
            "name": "員工送單率",
            "formula": "distinct employee_id count / company hc"
        },
        "average_return_rate": {
            "name": "平均退單率",
            "formula": "return_count / completed_count",
            "important_note": "Do not include in-progress applications in the denominator."
        },
        "completion_rate": {
            "name": "審核完成率",
            "formula": "completed_count / total_applications"
        },
        "ai_review_rate": {
            "name": "AI 審查率",
            "formula": "count where review_status='Y' and review_mechanism='AI' / completed_count"
        }
    },
    "mongo_query_guidance": {
        "default_database": "tflex_demo",
        "default_collections": {
            "applications": "tflex_applications",
            "company_hc": "tflex_company_hc"
        },
        "join_pattern": {
            "from": "tflex_company_hc",
            "localField": "company_code",
            "foreignField": "company_code",
            "as": "company_info"
        },
        "common_filters": {
            "completed_only": {
                "review_status": "Y"
            },
            "in_progress_only": {
                "review_status": "N"
            },
            "pay_only": {
                "review_status": "Y",
                "review_result": "Y"
            },
            "returned_only": {
                "review_status": "Y",
                "review_result": "N"
            },
            "ai_reviewed_only": {
                "review_status": "Y",
                "review_mechanism": "AI"
            },
            "human_reviewed_only": {
                "review_status": "Y",
                "review_mechanism": "H"
            }
        },
        "aggregation_rules": [
            "Use $group by company_code for company-level analysis.",
            "Use $addToSet employee_id and then $size for distinct submitter count.",
            "Use $lookup when calculating employee_submission_rate because hc is stored in tflex_company_hc.",
            "Do not calculate return rate using in-progress applications as denominator.",
            "Do not treat null review_result as returned or approved.",
            "Do not cast employee_id or application_no to integer."
        ]
    },
    "example_mongodb_aggregations": {
        "company_level_kpi_summary": [
            {
                "$group": {
                    "_id": "$company_code",
                    "submitters": {
                        "$addToSet": "$employee_id"
                    },
                    "total_applications": {
                        "$sum": 1
                    },
                    "completed_count": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$eq": [
                                        "$review_status",
                                        "Y"
                                    ]
                                },
                                1,
                                0
                            ]
                        }
                    },
                    "pay_count": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$and": [
                                        {
                                            "$eq": [
                                                "$review_status",
                                                "Y"
                                            ]
                                        },
                                        {
                                            "$eq": [
                                                "$review_result",
                                                "Y"
                                            ]
                                        }
                                    ]
                                },
                                1,
                                0
                            ]
                        }
                    },
                    "return_count": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$and": [
                                        {
                                            "$eq": [
                                                "$review_status",
                                                "Y"
                                            ]
                                        },
                                        {
                                            "$eq": [
                                                "$review_result",
                                                "N"
                                            ]
                                        }
                                    ]
                                },
                                1,
                                0
                            ]
                        }
                    },
                    "ai_review_count": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$and": [
                                        {
                                            "$eq": [
                                                "$review_status",
                                                "Y"
                                            ]
                                        },
                                        {
                                            "$eq": [
                                                "$review_mechanism",
                                                "AI"
                                            ]
                                        }
                                    ]
                                },
                                1,
                                0
                            ]
                        }
                    },
                    "human_review_count": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$and": [
                                        {
                                            "$eq": [
                                                "$review_status",
                                                "Y"
                                            ]
                                        },
                                        {
                                            "$eq": [
                                                "$review_mechanism",
                                                "H"
                                            ]
                                        }
                                    ]
                                },
                                1,
                                0
                            ]
                        }
                    }
                }
            },
            {
                "$lookup": {
                    "from": "tflex_company_hc",
                    "localField": "_id",
                    "foreignField": "company_code",
                    "as": "company_info"
                }
            },
            {
                "$unwind": "$company_info"
            },
            {
                "$addFields": {
                    "company_code": "$_id",
                    "hc": "$company_info.hc",
                    "submitter_count": {
                        "$size": "$submitters"
                    },
                    "employee_submission_rate": {
                        "$divide": [
                            {
                                "$size": "$submitters"
                            },
                            "$company_info.hc"
                        ]
                    },
                    "average_return_rate": {
                        "$cond": [
                            {
                                "$gt": [
                                    "$completed_count",
                                    0
                                ]
                            },
                            {
                                "$divide": [
                                    "$return_count",
                                    "$completed_count"
                                ]
                            },
                            None
                        ]
                    },
                    "ai_review_rate": {
                        "$cond": [
                            {
                                "$gt": [
                                    "$completed_count",
                                    0
                                ]
                            },
                            {
                                "$divide": [
                                    "$ai_review_count",
                                    "$completed_count"
                                ]
                            },
                            None
                        ]
                    }
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "company_code": 1,
                    "hc": 1,
                    "submitter_count": 1,
                    "total_applications": 1,
                    "completed_count": 1,
                    "pay_count": 1,
                    "return_count": 1,
                    "ai_review_count": 1,
                    "human_review_count": 1,
                    "employee_submission_rate": 1,
                    "average_return_rate": 1,
                    "ai_review_rate": 1
                }
            },
            {
                "$sort": {
                    "total_applications": -1
                }
            }
        ]
    },
    "data_preprocessing_guidance": {
        "id_handling": [
            "employee_id is a six-digit string and must not be converted to integer.",
            "application_no is an eight-digit string and must not be converted to integer."
        ],
        "missing_value_rules": [
            "review_result is null when review_status=N.",
            "review_mechanism is null when review_status=N.",
            "Null review_result means in progress, not returned."
        ],
        "derived_fields": {
            "is_completed": "review_status == 'Y'",
            "is_in_progress": "review_status == 'N'",
            "is_pay": "review_status == 'Y' and review_result == 'Y'",
            "is_returned": "review_status == 'Y' and review_result == 'N'",
            "is_ai_review": "review_status == 'Y' and review_mechanism == 'AI'",
            "is_human_review": "review_status == 'Y' and review_mechanism == 'H'"
        },
        "small_sample_warning": [
            "Companies with very small hc, such as TSK, should not be over-interpreted.",
            "When hc or completed_count is small, show both rate and absolute count."
        ]
    },
    "charting_guidance": {
        "recommended_charts": {
            "company_total_applications": {
                "chart_type": "bar",
                "x": "company_code",
                "y": "total_applications"
            },
            "company_submission_rate": {
                "chart_type": "bar",
                "x": "company_code",
                "y": "employee_submission_rate"
            },
            "pay_vs_return_by_company": {
                "chart_type": "stacked_bar",
                "x": "company_code",
                "y": [
                    "pay_count",
                    "return_count"
                ]
            },
            "return_rate_by_company": {
                "chart_type": "bar",
                "x": "company_code",
                "y": "average_return_rate"
            },
            "ai_vs_human_review": {
                "chart_type": "stacked_bar",
                "x": "company_code",
                "y": [
                    "ai_review_count",
                    "human_review_count"
                ]
            },
            "category_distribution": {
                "chart_type": "bar",
                "x": "application_category",
                "y": "application_count"
            }
        },
        "chart_rules": [
            "For rate charts, format y-axis as percentage.",
            "For small companies, show count labels together with percentages.",
            "Avoid pie charts when there are many companies.",
            "Use stacked bar charts for PAY vs RTN or AI vs H comparison."
        ]
    },
    "reporting_guidance": {
        "default_report_structure": [
            "Executive Summary",
            "Company Comparison",
            "Review Result Analysis",
            "AI Review Analysis",
            "Category Analysis",
            "Key Findings and Recommendations"
        ],
        "tone": "business analytical, concise, suitable for HR operations and management reporting"
    },
    "insight_guidance": {
        "analysis_principles": [
            "Always compare both absolute volume and rate.",
            "High return rate with low volume may not be operationally critical.",
            "High return count with moderate return rate may still be important because of workload impact.",
            "AI review rate should only be calculated among completed applications.",
            "In-progress applications may indicate workload backlog but not rejection risk."
        ],
        "cautions": [
            "Do not infer employee satisfaction directly from this dataset.",
            "Do not infer payment amount because there is no amount field.",
            "Do not infer trend because there is no application date field.",
            "Do not infer review duration because there is no submission or completion timestamp."
        ]
    },
    "data_limitations": {
        "missing_dimensions": [
            "No application date",
            "No payment amount",
            "No employee department",
            "No employee level",
            "No application reason",
            "No reviewer ID",
            "No review completion timestamp",
            "No policy version",
            "No country or location field"
        ],
        "not_supported_analysis": [
            "Trend analysis over time",
            "Seasonality analysis",
            "Payment amount analysis",
            "Review cycle time analysis",
            "Reviewer productivity analysis",
            "Department-level comparison",
            "Employee demographic analysis"
        ],
        "recommended_future_fields": [
            "application_date",
            "review_completed_date",
            "payment_amount",
            "department_code",
            "employee_grade",
            "reviewer_id",
            "return_reason_code",
            "policy_rule_id"
        ]
    },
    "statistics_reference": {
        "overall": {
            "hc": 91907,
            "submitter_count": 86483,
            "pay": 130313,
            "rtn": 4963,
            "total_applications_by_company_sum": 147526,
            "completed_applications": 135276,
            "in_progress_applications": 12250,
            "employee_submission_rate": 0.94098382060126,
            "average_return_rate": 0.03668795647417132,
            "completion_rate": 0.9169637894337269,
            "ai_review_target_rate_completed": 0.43
        },
        "company_statistics": {
            "TST": {
                "hc": 80919,
                "submitter_count": 77004,
                "pay": 114744,
                "rtn": 4285,
                "total_applications": 128922,
                "completed_applications": 119029,
                "in_progress_applications": 9893,
                "employee_submission_rate": 0.9516182849516183,
                "average_return_rate": 0.03599963034218552,
                "completion_rate": 0.9232636788135462
            },
            "TSC": {
                "hc": 2427,
                "submitter_count": 2399,
                "pay": 3680,
                "rtn": 168,
                "total_applications": 4184,
                "completed_applications": 3848,
                "in_progress_applications": 336,
                "employee_submission_rate": 0.988463123197363,
                "average_return_rate": 0.04365904365904366,
                "completion_rate": 0.9196940726577438
            },
            "TSA": {
                "hc": 2235,
                "submitter_count": 1392,
                "pay": 2176,
                "rtn": 62,
                "total_applications": 2699,
                "completed_applications": 2238,
                "in_progress_applications": 461,
                "employee_submission_rate": 0.6228187919463087,
                "average_return_rate": 0.02770330652368186,
                "completion_rate": 0.8291959985179697
            },
            "TSN": {
                "hc": 2121,
                "submitter_count": 2068,
                "pay": 3484,
                "rtn": 211,
                "total_applications": 4224,
                "completed_applications": 3695,
                "in_progress_applications": 529,
                "employee_submission_rate": 0.975011786892975,
                "average_return_rate": 0.0571041948579161,
                "completion_rate": 0.8747632575757576
            },
            "JSM": {
                "hc": 1852,
                "submitter_count": 1724,
                "pay": 3240,
                "rtn": 139,
                "total_applications": 3876,
                "completed_applications": 3379,
                "in_progress_applications": 497,
                "employee_submission_rate": 0.9308855291576674,
                "average_return_rate": 0.041136430896715,
                "completion_rate": 0.8717750257997936
            },
            "TWT": {
                "hc": 1051,
                "submitter_count": 711,
                "pay": 1061,
                "rtn": 58,
                "total_applications": 1266,
                "completed_applications": 1119,
                "in_progress_applications": 147,
                "employee_submission_rate": 0.6764985727878211,
                "average_return_rate": 0.05183199285075961,
                "completion_rate": 0.8838862559241706
            },
            "TSU": {
                "hc": 378,
                "submitter_count": 344,
                "pay": 562,
                "rtn": 11,
                "total_applications": 669,
                "completed_applications": 573,
                "in_progress_applications": 96,
                "employee_submission_rate": 0.91005291005291,
                "average_return_rate": 0.019197207678883072,
                "completion_rate": 0.8565022421524664
            },
            "TDI": {
                "hc": 348,
                "submitter_count": 316,
                "pay": 530,
                "rtn": 10,
                "total_applications": 727,
                "completed_applications": 540,
                "in_progress_applications": 187,
                "employee_submission_rate": 0.9080459770114943,
                "average_return_rate": 0.018518518518518517,
                "completion_rate": 0.7427785419532325
            },
            "TDJ": {
                "hc": 270,
                "submitter_count": 254,
                "pay": 404,
                "rtn": 10,
                "total_applications": 465,
                "completed_applications": 414,
                "in_progress_applications": 51,
                "employee_submission_rate": 0.9407407407407408,
                "average_return_rate": 0.024154589371980676,
                "completion_rate": 0.8903225806451613
            },
            "ESM": {
                "hc": 78,
                "submitter_count": 67,
                "pay": 97,
                "rtn": 3,
                "total_applications": 102,
                "completed_applications": 100,
                "in_progress_applications": 2,
                "employee_submission_rate": 0.8589743589743589,
                "average_return_rate": 0.03,
                "completion_rate": 0.9803921568627451
            },
            "TSE": {
                "hc": 72,
                "submitter_count": 60,
                "pay": 94,
                "rtn": 1,
                "total_applications": 106,
                "completed_applications": 95,
                "in_progress_applications": 11,
                "employee_submission_rate": 0.8333333333333334,
                "average_return_rate": 0.010526315789473684,
                "completion_rate": 0.8962264150943396
            },
            "TRJ": {
                "hc": 54,
                "submitter_count": 52,
                "pay": 101,
                "rtn": 2,
                "total_applications": 119,
                "completed_applications": 103,
                "in_progress_applications": 16,
                "employee_submission_rate": 0.9629629629629629,
                "average_return_rate": 0.019417475728155338,
                "completion_rate": 0.865546218487395
            },
            "TDC": {
                "hc": 53,
                "submitter_count": 46,
                "pay": 67,
                "rtn": 2,
                "total_applications": 79,
                "completed_applications": 69,
                "in_progress_applications": 10,
                "employee_submission_rate": 0.8679245283018868,
                "average_return_rate": 0.028985507246376812,
                "completion_rate": 0.8734177215189873
            },
            "TSJ": {
                "hc": 47,
                "submitter_count": 44,
                "pay": 70,
                "rtn": 1,
                "total_applications": 81,
                "completed_applications": 71,
                "in_progress_applications": 10,
                "employee_submission_rate": 0.9361702127659575,
                "average_return_rate": 0.014084507042253521,
                "completion_rate": 0.8765432098765432
            },
            "TSK": {
                "hc": 2,
                "submitter_count": 2,
                "pay": 3,
                "rtn": 0,
                "total_applications": 7,
                "completed_applications": 3,
                "in_progress_applications": 4,
                "employee_submission_rate": 1.0,
                "average_return_rate": 0.0,
                "completion_rate": 0.42857142857142855
            }
        },
        "note": "The user-provided Total row reported total_applications=140295, but the sum of company-level total_applications is 147526. The generated rawdata follows company-level details."
    },
    "llm_prompt_context": {
        "role": "This metadata describes a MongoDB dataset for an employee benefit application system called tFlex. Use it to answer business analysis questions, generate MongoDB aggregation pipelines, preprocess data, create charts, write reports, and generate insights.",
        "collections_summary": "There are two MongoDB collections.\n1. tflex_applications: one document per benefit application form. Fields: employee_id, company_code, application_no, application_category, review_status, review_result, review_mechanism.\n2. tflex_company_hc: one document per company. Fields: company_code, hc.",
        "kpi_summary": "Total applications = count of tflex_applications. Submitter count = distinct count of employee_id. Employee submission rate = distinct submitter count / hc. PAY = review_status=Y and review_result=Y. RTN = review_status=Y and review_result=N. Completed = review_status=Y. In-progress = review_status=N. Average return rate = RTN / completed. AI review rate = completed AI reviews / completed.",
        "query_rules": "Use company_code to join collections. Do not treat null review_result as rejection. Calculate return rate only among completed applications. Keep employee_id and application_no as strings. For small companies, show both percentage and absolute count.",
        "analysis_limitations": "No date field, so do not perform trend/monthly/seasonality analysis. No amount field, so do not analyze payment amount. No department field, so do not analyze department-level patterns. No review timestamp, so do not calculate review cycle time."
    }
}
```

---

## 15.7 · 附錄:其他 domain metadata 簡例

`_test_ecommerce_metadata.py` 與 `_test_healthcare_metadata.py` 是通用性測試用的精簡 metadata,結構與 tFlex 相同但欄位完全不同(電商 = orders+products,健保 = claims+providers)。讀完上方 tFlex metadata 後可推知,本文件略。

---

**Section 15 結束。專案源碼已完整內嵌,LLM 可直接根據此檔協助安裝、測試、debug、擴充新 domain。**
