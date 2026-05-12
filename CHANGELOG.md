# Changelog

All notable changes to GenBI will be documented in this file.
版本格式採用 [Semantic Versioning](https://semver.org/):`MAJOR.MINOR.PATCH`。

---

## [0.2.1] · 2026-05-12 — Docs:LLM handoff brief

**Patch release · docs only,無功能變動。**

### 📝 新增

- **`AI_CONTEXT.md`** — 單檔自足的專案濃縮文件,給 LLM agent / 接手開發者直接讀。
  - ~163 KB / 3,714 行,14 個 narrative 區段 + 7 個內嵌源碼區段。
  - 涵蓋:架構地圖、檔案職責、模組 API、環境變數對照、常見錯誤速查、Debug SOP、測試指令、新 domain 接入指南、設計原則。
  - 內嵌完整源碼:`requirements.txt` / `.env.example` / `config.py` / `llm_service.py` / `app.py` / `tflex_task_metadata_agent_v3.py`。
  - 用途:可直接餵進新 LLM session 取得專案全貌,不需另外 navigate repo。

---

## [0.2.0] · 2026-05-12 — Pre-Phase 0 UX Layer + Continuity

新增「**對話式 BI**」的 UX 基礎建設:Intent Router、Follow-up Detection、out_of_scope 拒絕。
使用者從第一次見面到深度迭代分析的完整 journey 都被覆蓋,且大部分 meta query 是 **0 LLM call** 毫秒級回應。

### ✨ 新增 — Pre-Phase 0 路由層

- **Intent Router**(6 種 intent · 全部 0 LLM call):
  - `greeting`(hi / 你好)→ 簡短歡迎 + 下一步建議
  - `intro`(你會做什麼?)→ 從 metadata 生成產品介紹 + 範例問題
  - `data_overview`(你有什麼資料?)→ 列出 collections / KPI / 限制
  - `data_check`(你有 X 嗎?)→ subject 萃取 + metadata 搜尋,引用 data_limitations
  - `guidance`(怎麼開始?)→ 分類引導 + 範例
  - **`out_of_scope`**(今天天氣 / 股價 / 翻譯 等)→ 從 metadata 建 bilingual vocab,query 無 vocab match 時友善引導
- **Follow-up Detection** — 偵測「改成 X / 也加 Y / 排序 / 只看 Z」等修改詞 + last_analysis 存在時自動注入前次脈絡到 Phase 0
- **Routing 優先序**:explicit intent → follow-up → out_of_scope → analysis(follow-up 優先於 out_of_scope,避免短修改指令被誤判離題)

### ✨ 新增 — 對話延續性

- `st.session_state.last_analysis` 儲存前次分析脈絡(query / Q.columns / chart type / plan summary)
- Phase 0 follow-up preamble 採用 **Minimal Change Principle**:
  - 純改圖表 → A/B 段沿用,只改 C
  - 加 KPI → A 段保持,B 段加新欄位,C 段加 series
  - 收窄範圍 → A 段加 $match,B/C 沿用
- Sidebar 加「🆕 開始新分析」「🗑️ 清除對話歷史」按鈕,使用者可手動中斷接續

### 🛡️ 結構性防禦強化

- **比率類 KPI 標準骨架** — Phase B prompt 直接內建三步驟 (bool flag → sum → int/int rate),防 follow-up 加 KPI 時誤把 string 欄位當分母
- **Anti-pattern cheatsheet 新增** — 「string / int 除法」TypeError 對照表
- **單一指標 stack 處理** — 若前次 Q 只有 1 個 numeric 指標而使用者要 stacked bar,prompt 提供 3 條合理應對(保留 bar / 建議改看占比 / 用戶明示堆疊指標)
- **絕對禁忌列表** — 不要把 hc 當 x-axis 維度、不要產生重複 Q 行、不要 raw count 配 `{value}%` formatter

### 🎨 UX 細節

- 移除預設 welcome panel — **極簡開場**,只在使用者主動問才呈現引導(`你會做什麼?` 等)
- Chat input placeholder 含 3 個 meta query 提示
- Follow-up 偵測到時顯示「🔗 偵測為延續性分析」info banner,透明化
- out_of_scope 響應「🧭 你問的不在範圍內」+ 來自 metadata 的範例問題引導

### 📋 測試與文件

- `TEST_UX_SCENARIOS.md` — 完整 9 個 scenario / 57 case 的 UI 整合測試計畫
- 涵蓋:冷啟動 / 探索資料 / 標準分析 / 接續修改 / 拒絕路徑 / 完全離題 / 複合需求 / 完整 User Journey / 邊界防禦

### 🐛 修正

- Routing 順序 bug:「改成 stacked bar」這類短的修改指令不再被誤判為 out_of_scope
- _GENERIC_BI_TERMS 擴大:加入 `stacked / bar / line / scatter / heatmap / 圖` 等視覺化術語

### ⚠️ Known Issue

- 接續分析在「同時換圖表類型 + LLM 自由發揮」時,偶爾仍會誤解維度(如 hc 當 x 軸)
- 後續可考慮 architectural fast path:純改圖表類型的 follow-up 跳過 Phase 0/A/B,直接重新跑 Phase C

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
