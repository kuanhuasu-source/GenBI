# Changelog

All notable changes to GenBI will be documented in this file.
版本格式採用 [Semantic Versioning](https://semver.org/):`MAJOR.MINOR.PATCH`。

---

## [0.1.0] · 2026-05-12 — Initial Release

第一個可用版本。系統已完成核心 architecture,在三個 domain (tFlex / 電商 / 健保) 上驗證通用化能力。

### ✨ 新增

- **5-Phase Agentic Workflow** — Plan → MongoDB Pipeline → Pandas → ECharts → Insight
- **Domain-Agnostic 架構** — 新增 domain 只需寫一個 metadata 檔,不必動 system code
- **多 Provider LLM 設定** — `config.py` 統一管理,單一 env var (`HRDA_MODEL_PROVIDER`) 可切 ollama / vllm / openai
- **ECharts + Plotly 雙引擎** — Sidebar 可切換;ECharts few-shot 從 `metadata.charting_guidance` 自動產生
- **精美表格 + KPI 卡片** — dashboard 場景自動產出 `st.metric` cards + `ProgressColumn` 漸層進度條;比率欄位自動轉百分比
- **Schema-Driven Refusal** — LLM 從 metadata 推理該不該拒絕,**不依賴 hardcoded 關鍵詞**;使用 `[REFUSE]` 標記讓 app.py 結構性 short-circuit
- **Cost Telemetry** — 每 query 追蹤 wall time、prompt/completion tokens、retry 次數,並提供 3 家 cloud API 的成本估算
- **3 個示例 Domain** — tFlex (員工福利申請、147K rows)、E-commerce (訂單、模擬資料)、Healthcare (理賠、模擬資料)

### 🛡️ 結構性防禦

- Phase B 連續失敗 3 次的 retry loop,每次帶上 traceback + Pandas anti-pattern 速查表 (`Q.agg without groupby`、`Series.first()`、self-merge、漏終態指派、幻覺欄位、wide pivot)
- Phase B 安全網:`try_recover_Q` 偵測 LLM 漏寫 `Q = grouped` 時自動 fallback 到最聚合的候選 DataFrame
- Phase C 連續失敗 3 次自動降級為精美表格 (`render_pretty_table`),不會 hard crash
- Dashboard 場景 (`is_dashboard_query`) Phase B 走 row-level pass-through,把 scalar 算式交給 Phase C 的 `_kpi_cards`
- `_kpi_cards` 比率類 KPI 強制用加權平均 (`sum/sum`),禁止 `.mean()`;總量類 KPI 自動過濾 `TOTAL` 列防雙倍計算

### 🧪 測試套件

- `test_runner.py` — 18 個 tFlex case 完整 headless 回歸 (對齊 `TEST_PLAN.md`),含拒絕路徑驗證
- `test_generality.py` — 多 domain CLI 通用性測試 (`python test_generality.py ecommerce|healthcare`)
- 智慧禁忌詞偵測 (整句斷句 + 18 個 denial markers + hedging 詞)
- 每測試自動產出 cost summary (per-case wall time / tokens / retries + aggregate + 3 家 cloud API 估價)

### 📊 圖表支援

- 單軸 / 雙軸 bar + line
- Stacked bar、100% Stacked bar (per-group normalize 樣板)
- Grouped bar
- Sorted bar / TOP-N
- Heatmap (含 visualMap)
- Scatter
- Categorical bar (非公司維度)
- 精美表格 + KPI cards (dashboard 場景)

### ⚙️ 設定 / 部署

- `config.py` 統一管理 LLM / MongoDB 設定,支援 3 個 provider profile
- `.env.example` 含 ollama / vllm / openai 三組範本
- `setup_mongodb.sh` 一鍵 brew install + 匯入示例資料
- `import_tflex_to_mongodb.py` 支援 upsert / drop_insert 兩種模式
- `app.py` MongoDB 連線失敗時自動 fallback 到 CSV (本機開發友善)

### ⚠️ 已知限制

- 部分視覺化邊緣 case 仍在打磨 (例如複雜的兩階段聚合查詢)
- First-pass success rate 約 70-75%(3 次 retry 後通常能恢復)
- 建議 production 部署:A100 + vLLM + Qwen2.5-Coder-32B-Instruct-AWQ
- LLM 行為仍有隨機波動,同個 query 多次跑可能產出不同 chart 樣式 (溫度設 0 但 OpenAI-compatible 後端仍有少量非確定性)

### 📦 Tech Stack

- **Frontend**: Streamlit 1.30+
- **LLM Client**: OpenAI Python SDK (compatible with Ollama / vLLM / OpenAI / Anthropic-via-proxy)
- **Visualization**: streamlit-echarts + Plotly
- **Data**: pandas + pymongo + MongoDB 7+
- **Default LLM**: Qwen3-Coder 30B (Ollama) — production 推薦 Qwen2.5-Coder-32B-Instruct-AWQ on vLLM

---

> 後續版本將依照 SemVer:
> - `0.x.y` 階段,API 可能 breaking change
> - 到 `1.0.0` 後,breaking change 升 major version
