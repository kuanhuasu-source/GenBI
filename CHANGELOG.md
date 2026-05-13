# Changelog

All notable changes to GenBI will be documented in this file.
版本格式採用 [Semantic Versioning](https://semver.org/):`MAJOR.MINOR.PATCH`。

---

## [0.2.4] · 2026-05-13 — UI 大翻修 + 圖表呈現品質

**Minor patch · 品牌 / UX 全面升級 + 多個圖表渲染防禦補強。**

### 🎨 品牌 / UX

- **GenBI 品牌建立** — 標題改 `GenBI`,加 slogan `From question to chart in seconds`
- **廚師 logo v5** — `assets/genbi_logo.svg`(SVG 矢量),圓胖大臉 + 淺膚色 + 翹鬍子拿掉 + 紅領巾 + 暗紅圓背景 + 鍋拿食材跳起
- **字 + 圖同步放大** — 標題 2.6rem(+30%),logo 110px(+53%),column ratio `[1, 6]` 給 logo 更多空間
- **Current Question 醒目橫條** — 米黃底 + 紅左邊框釘在 assistant response 頂端,workflow 跑長也看得到使用者問什麼;follow-up 自動帶紅色 pill 標示

### 📦 過程資訊整理

- **Phase A / Phase B 中介資料表收進 expander** — `raw_df.head(100)` 與 `Q.head(100)` 預設 collapsed,不再 dominate 視野
- **Phase C 完成 inline banner** — 補齊 Phase A/B/C 視覺對稱,顯示「引擎:ECharts」或「降級為表格」
- **Status label 帶 query 摘要** — `🧠 處理中:{query[:60]}…`,即使 status 收起來也看得到

### 🛡️ 圖表渲染防禦(`llm_service.py` 為單一來源)

- **`ensure_default_styling(option, query)`** — 第三道結構性救援:
  - **色盤循環防禦**:預設 20 色 + HSL 黃金比例自動擴充,15+ series 也不撞色(解 TST/TDC 都紅色 bug)
  - **Heatmap 三雷防禦**:numpy 型別 cast 成 float、`tooltip.trigger="cell"` 改 `"item"`、`visualMap.inRange.color` 缺則補預設藍漸層
  - **長尾偏態 auto log scale**:bar/line series `max/min > 100` 自動切 `yAxis.type="log"`,小公司不再被壓扁(解 TST 80K vs TSK 2 場景)
  - **率類欄位保護**:`name` 含「率 / rate / ratio / 百分比 / percent」即使值域偏也不切 log
- **`rescue_empty_echarts` 雙軸支援** — `yAxis=list` 或 `xAxis=list` 自動跳過,不再炸 AttributeError

### ✨ Prompt 強化(Phase C)

- **Rule 5.7 預設樣式鐵律** — label + legend 自動帶上(bar/line/scatter/pie/heatmap 各有 position 規範),含智慧抑制(>15 條 bar 自動關 label)
- **Rule 5.7H Heatmap 完整配方** — 3 個雷 + 正解配方並列,解 numpy 序列化失敗、tooltip 失效、cell 顏色不顯
- **Rule 5.8 偏態分佈 auto log scale** — 觸發條件 + 解法優先序(log → horizontal sorted → split view)
- **Rule 6 色盤 20 色擴充** — Few-shot 同步更新

### 🔄 Stack vs 100% Stack 預設邏輯翻轉(關鍵 UX)

- **預設「stacked bar」走 raw count**,只有明示「100%」「百分比+堆疊」「比例+堆疊」「占比分佈」「percentage stack」才 100% normalize
- 「占比 / 組成 / 結構 / 分佈」單獨出現 → raw count(避免誤判)
- Rule 9.5(Phase B)+ Rule 5.6(Phase C)同步更新,含判斷練習對照表

### 📚 受影響檔案

- `llm_service.py` — 加 `ensure_default_styling` / `DEFAULT_COLOR_PALETTE` / `_extend_palette`;rule 5.7/5.7H/5.8 + rule 9.5/5.6 翻轉
- `app.py` — 品牌標題 + slogan + logo 並排佈局、Current Question 橫條、Phase A/B expander、Phase C banner
- `assets/genbi_logo.svg`(新增) — 廚師 logo 矢量檔
- `test_runner.py` — 沿用三道救援 utility

---

## [0.2.3] · 2026-05-13 — Stacked Bar 結構性防禦 + 測試強化

**Patch release · 收斂 stacked bar 失敗模式 + 兩道結構性防禦 + STK 測試套件。**

### 🛡️ 結構性防禦(新增,單一來源於 `llm_service.py`)

- **`sanitize_pipeline(pipeline)`** — Phase A 救援:strip stage 鍵的前後空白,缺 `$` 補回。
  防 LLM 寫 `" $project"`、`"match"` 觸發 `Unrecognized pipeline stage`。test_runner / app.py 都調用。
- **`rescue_empty_echarts(option, Q)`** — Phase C 救援:偵測「結構完整但 data 全空」的 option
  (series=[]、所有 series.data=[]、category 軸 data 缺),從 Q 自動 pivot 補回 series。
  支援橫向偵測(`yAxis.type=category` → 灌 pivot.index 到 yAxis)。

### ✨ Prompt 強化(Phase A / C)

- **Rule 5.5 ✅ Entity 過濾鐵律** — 使用者明列實體值(TST/TSN/TSC、Apparel/Books 等)時,
  Phase A `$match` **必須**含 `$in` 過濾,不要讓下游 Pandas 處理。
- **Rule 5.55 ⚠️ Stacked Bar 強制 Pivot 鐵律(CRITICAL FATAL)** — 不論 Q 是 long 或 wide,
  Phase C 一律先做 `pivot_table().fillna(0)`,從 `pivot.index` / `pivot.columns` 取 xAxis / series。
  絕對禁止 `Q[Q['col']==literal]` filter 模式(會缺漏組合 → series.data 長度不齊)。
- **Rule 5.58 🔢 百分比禁止重覆 ×100** — 命名含 `_pct` / `percent` / `percentage` / `rate`
  的欄位已是 0-100,Phase C 不可再 `* 100`(否則變 0-10000)。
- **Rule 5.65 ↔️ 橫向 Bar 強制走 5.55 pivot** — 橫向 stacked bar 不是只換軸而已,
  pivot 後從 `pivot.index` 取 yAxis,series.data 從 `pivot[col]` 取。

### 🧪 測試框架擴充

- **STK-01 ~ STK-08** — 8 個 stacked bar 專屬 case(`STACKED_BAR_TEST.md` 提供規格):
  100% stacked / transposed / raw count / 三狀態 / filter / hc 範圍 / follow-up / 橫向。
- **新檢查項** — `echarts_xaxis_unique`、`echarts_data_length_aligned`、`echarts_yaxis_max`、
  `echarts_no_placeholder_series_name`、`echarts_no_nan_in_data`、
  `echarts_should_have_yaxis_category` / `_should_have_xaxis_value` / `_data_length_aligned_horizontal`。
- **`--filter` / `--only` CLI** — `python test_runner.py --filter STK` 只跑 STK-* 案例;
  `--only STK-01,STK-04` 跑指定 case,迭代速度大幅提升。
- **Follow-up setup 支援** — case 加 `follow_up_setup_query` 時,合成 `last_analysis` dict 注入
  `generate_plan(query, followup_context=...)`,讓 STK-07 能真正測 follow-up 路徑。
- **`denial_markers` 擴大** — 加入 caveat / forward-looking / hedging 詞群
  (`未考量`、`未涵蓋`、`可能`、`是否`、`建議`、`協助`、`視覺化`、`或地區`、`或職級` 等),
  避免 LLM 在 insight 的「觀察與建議」/「解讀注意事項」用 `部門/金額/趨勢` 時被誤判為 hallucination。

### 🐛 修正

- **`generate_pipeline` f-string nesting** — 原本 rule 5.5 的 JSON 範例用單層 `{}` 觸發
  Python f-string 巢狀深度上限,改為敘述 + 點到範例結構。
- **rule 5.5 「match / $match」混淆** — 文案改寫,明示「完整鍵名是 `"$match"`(含錢字符號),
  不要寫成 `"match"`」並指向範例結構區。

### 📊 跑分(`python test_runner.py` 全跑)

- **22/26 pass(84.6%)** — STK 從 1/8 → 7/8;另 3 個原始 case 失敗已用 denial_markers 修正,
  預期下次 25/26(96.2%)。
- 剩 case 03 是 query 模糊性設計問題(沒明說 "stacked" 但 case name 期待 stacked),保留當
  「LLM 彈性判讀」測試。

### 📚 受影響檔案

- `llm_service.py` — 加 `sanitize_pipeline` / `rescue_empty_echarts`;rule 5.5/5.55/5.58/5.65 改寫
- `app.py` / `test_runner.py` — 都引入兩個 utility
- `test_runner.py` — STK case + 新檢查項 + CLI flags + follow-up setup + denial markers
- `STACKED_BAR_TEST.md`(新增) — STK 測試規格

---

## [0.2.2] · 2026-05-12 — Fix:Long format + Stacked Bar 對齊

**Patch release · 修 Phase C ECharts prompt 的 long-format 對齊 bug。**

### 🐛 修正

- **Long format Q + Stacked Bar 對齊鐵律(新規則 5.55)**
  - **症狀**:當 Phase B 產出 long format Q(每列 = 一個 dim_a × dim_b 組合,例如 company × category),LLM 在 Phase C 用 `Q["company_code"].tolist()` 直接當 xAxis,導致 xAxis 列出重複的 N 次公司代碼,series 資料只填到前幾個位置,**所有 bar 擠在最左邊、後方空白**。
  - **根因**:long format Q 沒被 pivot 成 wide,xAxis 與 series.data 順序未對齊。
  - **修正**:Phase C prompt 加 CRITICAL 規則 5.55,明示「xAxis.data 必須用 `unique().tolist()`」+ 提供 pivot_table + reindex + fillna 的標準配方範例(❌ 反例 vs ✅ 正解對照)。
  - **影響**:`generate_echarts_option` system prompt,~30 行新內容。

### 📚 受影響的場景

- 公司 × 類別 stacked bar(各公司類別占比)
- 公司 × 狀態 stacked bar(各公司核准/退件/進行中占比)
- 任何 long format groupby 結果做 stacked bar / grouped bar 的場景

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
