# GenBI — Schema-Driven Generative Business Intelligence

> 一個 **domain-agnostic** 的自然語言到 BI 視覺化系統。把 metadata 餵進去,就能用對話形式探索任何結構化資料集。

## ✨ 核心特性

- 🗣️ **自然語言查詢**:中英文混合都行,例如「比較各公司退單率與申請數」
- 🧠 **5-Phase Agentic Workflow**:Plan → MongoDB Pipeline → Pandas → ECharts → Insight
- 🔌 **Domain 解耦**:換不同資料集只需改 metadata 檔,不必動程式碼
- 📊 **ECharts + Plotly 雙引擎**:Sidebar 可切換,真實 BI 視覺體驗
- 💎 **精美表格 + KPI 卡片**:dashboard 場景自動降級為 `st.metric` + `ProgressColumn` 漸層條
- 🚦 **結構性防禦**:retry 3 次 + Phase B/C 安全網 + 失敗時降級為表格,不會 hard crash
- ⛔ **Schema-driven refusal**:LLM 從 metadata 推理該不該拒絕,不靠 hardcoded 關鍵詞
- 📈 **Cost telemetry**:每 query 自動記錄 wall time、tokens、retry 次數

## 🏗️ 架構

```
┌────────────────────────────────────────────────────────┐
│ Layer 1: System (domain-agnostic)                       │
│   - llm_service.py  ← 5 phase prompts,通用規則         │
│   - app.py          ← Streamlit UI + workflow 路由     │
└──────────────────────────┬─────────────────────────────┘
                           │ runtime injection
                           ▼
┌────────────────────────────────────────────────────────┐
│ Layer 2: Metadata (per-domain,由 domain expert 維護)    │
│   - collections.fields  (schema)                        │
│   - kpi_definitions     (能算什麼)                      │
│   - data_limitations    (不支援什麼)                    │
│   - charting_guidance.recommended_charts  (few-shot)    │
└──────────────────────────┬─────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────┐
│ Layer 3: LLM (Qwen 3 Coder / vLLM / Ollama)             │
│   - 把 query 對映到 metadata 的 schema                  │
│   - OpenAI-compatible API,可換任何 LLM provider        │
└────────────────────────────────────────────────────────┘
```

## 🚀 快速啟動

### 前置需求

- macOS / Linux,Python 3.10+
- MongoDB 7+(或用 CSV fallback dev mode)
- 任一個 OpenAI-compatible LLM endpoint:
  - **Ollama**(推薦,本機開發):`ollama pull qwen3-coder:30b`
  - **vLLM**(production):部署 `Qwen2.5-Coder-32B-Instruct-AWQ` 在 A100
  - **OpenAI / Anthropic API**(雲端)

### 安裝步驟

```bash
# 1. 建立 venv 並安裝
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. 設定環境變數
cp .env.example .env
# 編輯 .env,設定 HRDA_MODEL_BASE_URL、HRDA_MODEL_NAME 等

# 3. 安裝 MongoDB + 匯入示例資料 (macOS Homebrew)
bash setup_mongodb.sh

# 4. 啟動 Streamlit UI
streamlit run app.py
```

打開瀏覽器 `http://localhost:8501` 開始對話。

### 不裝 MongoDB 也能跑(dev mode)

Sidebar 切到 `CSV fallback (dev)`,系統會把 `data/*.csv` 載入 pandas 模擬 MongoDB 查詢。Pipeline 的 `$match` / `$project` 會被解譯到 pandas 上,Phase B/C/D 完全照常運作。

## 📁 專案結構

```
GenBI/
├── app.py                          # Streamlit 主入口 (5-phase workflow)
├── llm_service.py                  # LLM service + 5 phase prompts (domain-agnostic)
├── tflex_task_metadata_agent_v3.py # tFlex domain metadata (員工福利申請)
├── _test_ecommerce_metadata.py     # 通用性測試用:電商訂單 metadata
├── _test_healthcare_metadata.py    # 通用性測試用:健保理賠 metadata
├── import_tflex_to_mongodb.py      # CSV → MongoDB 匯入工具
├── setup_mongodb.sh                # 一鍵安裝 mongodb + 匯入資料
├── test_runner.py                  # tFlex 18 case headless 回歸測試
├── test_generality.py              # 多 domain 通用性測試
├── TEST_PLAN.md                    # 18 case 測試計畫文件
├── requirements.txt
├── .env.example
└── data/
    ├── tflex_applications_rawdata_v2.csv
    └── tflex_company_hc_rawdata_v2.csv
```

## 🌐 換到另一個 Domain

寫一份新 metadata 檔即可,例如 `my_domain_metadata.py`:

```python
MY_METADATA = {
    "dataset_name": "My Sales Dataset",
    "recommended_mongodb": {
        "database": "sales_demo",
        "collections": {"orders": "orders", "customers": "customers"},
        "join_key": "customer_id",
    },
    "collections": {
        "orders": {
            "primary_key": "order_id",
            "fields": {
                "order_id": {"type": "string", "description": "..."},
                "amount": {"type": "number", "description": "..."},
                # ...
            },
        },
        # ...
    },
    "kpi_definitions": {
        "total_revenue": {"name": "...", "formula": "..."},
        # ...
    },
    "data_limitations": {
        "missing_dimensions": [...],
        "not_supported_analysis": [...],
    },
    "charting_guidance": {"recommended_charts": {...}},
}
```

在 `app.py` 改一行 import 即可:
```python
from my_domain_metadata import MY_METADATA
llm_service = LLMService(task_metadata=MY_METADATA, ...)
```

## 🧪 測試

```bash
# tFlex 18 case 完整回歸
python test_runner.py

# 電商 domain 通用性
python test_generality.py ecommerce

# 健保 domain 通用性
python test_generality.py healthcare
```

每次測試會輸出:
- 速覽表(per case: status / wall time / LLM calls / tokens / retries)
- Cost summary(3 家 cloud API 估價對照)
- `test_results.md`(完整 case-level 分析)

## 🎯 5-Phase Workflow

| Phase | 輸入 | 輸出 | LLM Role |
|---|---|---|---|
| **0 · Plan** | 使用者查詢 | A/B/C 三段計畫 or `[REFUSE]` 拒絕 | 商業分析師 |
| **A · Pipeline** | Plan + metadata | MongoDB aggregation JSON | 資料庫工程師 |
| **B · Preprocess** | raw_df + Plan | `Q` DataFrame(計算 KPI) | Pandas 工程師 |
| **C · Visualize** | `Q` + Plan | ECharts `option` dict 或 fallback 表格 | 前端工程師 |
| **D · Insight** | `Q` + Plan + 查詢 | 商業洞察 Markdown | 商業分析師 |

## 🛡️ 結構性保護

- **Pipeline 鐵律**:`$project` 必須保留所有原始欄位,避免下游幻覺
- **Phase B 安全網**:若 `Q.shape == raw_df.shape`,自動 fallback 到最聚合的候選 DataFrame
- **3 次 retry + cheatsheet**:Phase B 失敗時把 pandas anti-pattern 速查表餵回 LLM
- **Phase C fallback**:3 次失敗自動降級為 `render_pretty_table(Q)`,絕不 crash
- **拒絕短路**:Plan 標示 `[REFUSE]` 時,直接呈現拒絕訊息,不執行下游

## 📊 圖表能力

- **單軸 / 雙軸 bar + line**
- **Stacked / 100% stacked bar**(自動 per-group 歸一化)
- **Grouped bar**
- **Sorted bar / TOP-N**
- **Heatmap**(2D matrix)
- **Scatter**
- **Categorical bar**(類別維度)
- **精美表格 + KPI cards**(dashboard 場景)

## 🤝 設計哲學

1. **Schema-driven**:domain 業務邏輯放 metadata,系統只是推理引擎
2. **結構性防禦 > 加 prompt 規則**:失敗時優先 graceful degradation,而非無止境加規則
3. **可觀測**:每 phase 透明、每次 retry 透明、每筆 LLM call 含 token 統計

## 📝 License

MIT
