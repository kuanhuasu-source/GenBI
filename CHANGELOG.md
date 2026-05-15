# Changelog

All notable changes to GenBI will be documented in this file.
版本格式採用 [Semantic Versioning](https://semver.org/):`MAJOR.MINOR.PATCH`。

---

## [0.4.7] · 2026-05-15 — STK-04 / STK-05 phaseC_fallback 救援 + 防呆

**Patch · 修「100% stacked 空殼陷阱」第二代，救回兩個長期 fallback。**

### 🐛 修正

歷史問題：STK-04（三狀態 100% stacked，Q wide format）與 STK-05（TST/TSC AI vs Human stacked，Q long format）在 baseline 跑時連續 3 次都產出 `xAxis.data=[] + series=[]` 空殼，最終降表格。實際 root cause 不是「rescue 沒救」，而是兩個獨立問題：

1. **LLM exec-fail 於 rescue**：LLM 寫 `option = {empty shell}` 後接著重做 Phase B 該做的事（`Q['review_status'/'review_result']`），但 Q 已被 Phase B 處理過、底層欄位不存在 → KeyError → exec 整個煸，`rescue_empty_echarts` 根本沒機會跑。
2. **`rescue_empty_echarts` 不支援 wide format**：原本只接受 `≥2 dim + ≥1 numeric` 走 pivot 路徑，STK-04 的 `1 dim + 3 numerics`（每個百分比一欄）被拒。

**三層修正**：

- **`rescue_empty_echarts` 加 wide format 路徑**：1 dim + N numerics → 每個 numeric 當一條 series，沿用同一個 stack 名（預設 `stack`）。橫向 / 直向 axis 自動偵測。STK-04 直接救回。
- **retry loop 在 exec 失敗時也試 rescue**（app.py + test_runner.py）：exec 拋例外時，先檢查 `namespace['option']` 是否仍是 dict，若是就送 rescue。救得回就跳出 retry、視為成功（toast 提示「從半殘空殼救回」）。STK-05 跟 STK-04 都吃這條。
- **Phase C prompt rule 3.1 新增**：明文禁止「空殼 + dynamic fill」 anti-pattern，給 Q 已是 Phase B 終態的提醒（不要再算 `Q['review_status']`），口訣「option literal 寫完就是完整的」。降低 LLM 一開始就寫錯的機率。

### ✅ 測試

- 4 個 unit test：STK-04 wide / STK-05 long / 完整 option 不誤觸 / 空 Q 不 crash。
- byte-equal：Phase 0/A/C 三個 prompt 全部維持 byte-equal（2 spaces drift 修正）。
- export_pptx smoke 7/7 仍綠。

---

## [0.4.6] · 2026-05-14 — Phase C numpy scalar coercion(BidiComponent JS error fix)

**Patch · 修 streamlit-echarts `Cannot convert undefined or null to object` JS 錯誤。**

實際 case:「請幫忙依照不同的 Company Code,計算員工數量(H/C),並以圓餅圖呈現」 — LLM Phase C 寫 `{"value": Q['total_hc'].iloc[i], "name": Q['company_code'].iloc[i]}`,這些是 `numpy.int64` / `numpy.str_`,streamlit-echarts BidiComponent serializer 序列化為 JS `null`,JS 端 `Object.keys(null)` 直接炸。LLM stochasticity 造成第二次重問常壞。

**雙層防護**:
- **App 端 structural sanitizer** `coerce_option_native_types`:遞迴走 `final_option`,把所有 `numpy.generic` / `pandas.Timestamp` / `pandas.Timedelta` / NaN / Inf 轉成 Python native。Wire 在 `app.py` 與 `test_runner.py`,緊跟在 `ensure_default_styling` 之後。
- **Phase C prompt rule 3.3 新增**:把「numpy/pandas scalar 必須 cast 為 native」從 rule 5.7H(heatmap-only)拉到**全圖型通用鐵律**。給三種正解(顯式 cast / `.tolist()` / `to_dict('records')`)+ 反例 + 口訣。

byte-equal:Phase 0/A/C 三個 prompt 全部維持 byte-equal。export_pptx smoke 7/7 綠。

---

## [0.4.5] · 2026-05-14 — Export button moved to script tail(rerun race fix)

**Patch · 修 Export Insight button 跑完成功分析後仍然看不見的 bug。**

v0.4.4 雖然讓 button 「always visible」,但仍放在 chat history 之後、`chat_input` 之前的位置 — Streamlit 是 top-down 一次跑完的腳本,這個區塊在「messages 還沒被 append、payload 還沒被 set」的時候就先評估完了 → button block 跳過 → 直到 user 主動觸發下一次 rerun 才會看到。

把整個 Export / Download button block 搬到 `app.py` 最尾端(在 `if query:` 的 `try/except` 之後),這樣不管走什麼 rerun 路徑(初始載入、chat 送出、button click),button 區塊都會在 query handler 跑完之後才評估,看到的就是最新的 `messages` 跟 `last_export_payload` 狀態。內部加 `st.rerun()` 讓 Download button 在生成完 PPTX 後即時 enabled。

已知限制(可接受):4 個 `st.stop()` 路徑(meta_response / [REFUSE] Plan / Phase B 3-retry fail / fatal exception)會中斷腳本、跳過尾端 button block。但這 4 種情境本來就「沒有成功分析可匯出」,button 隱藏在語意上正確。

---

## [0.4.4] · 2026-05-14 — Export Insight button always visible(disabled when no payload)

**Patch · 修 Export button 在「沒成功跑過分析」時完全不出現、讓使用者以為 feature 沒安裝的 UX bug。**

v0.4.0 的 Export button 條件 gating 在 `st.session_state.last_export_payload`,只有 Phase A→D 全部成功才會 set。若使用者只跑過被 refuse 的 query(例如 v0.4.3 修好前的 pie chart 假拒絕),button 不會 render → 視覺上 feature 不存在。

UX 修正:button 區塊改成「`st.session_state.messages` 非空就 render」,空狀態用 `disabled=True` 區隔。3-column layout `[Export | Download | caption]`,搭配 `st.divider()` 跟條件 caption(「請先跑一次成功分析」或「📊 已備好上次分析:...」)。Download 也用 disabled placeholder 維持版面完整。

---

## [0.4.3] · 2026-05-14 — Phase 0 false positive refusal 防線

**Patch · 修 pie chart H/C query 被誤判為 data_limitations 違規。**

### 🐛 修正

實際例:「請依照 Company Code:TSA,TWT,TSU,TDI,TDC,計算員工數量(H/C),並以圓餅圖呈現」被 Plan LLM 誤拒,理由是「缺少 application date,無法執行 圓餅圖呈現」 — 但實際上 pie chart 跟 application date 完全無關,且 `tflex_company_hc` 表本身就有 `company_code` + `hc` 兩個欄位,可以直接畫圖。

**三層防線**:

- **Step 1 加鐵律**:「圖型詞」(圓餅圖 / pie / bar / line / heatmap / scatter / stacked bar)**完全不參與 refuse 判斷**。明確列出 6 大類圖型詞,避免 LLM 把「pie chart 通常需要時間軸」這種訓練資料偏見帶進來。
- **新增「拒絕前的最後檢查」**:Step 3 通過後還要驗證引用的 `missing_dimension` / `not_supported_analysis` 是否真的對應 query 中明確提及的需求。引用不一致(只是湊理由)→ 撤回拒絕,走計畫。
- **新增反例 4**:H/C pie chart query 作為「絕對不可拒絕」的訓練範例,內含完整的三步推理過程,直接呼應實際失敗 case。
- **軟化「多類別比較禁止 pie chart」**:改成 type-aware 路由:類別數 ≤ 7 + 點名 pie → 走 pie;> 7 → 建議改 bar 但仍走計畫;明確標註「pie chart 適不適合」是視覺化建議、不是拒絕理由。

### 📐 改動範圍

- `embedded_prompts.py` · `_PHASE_0_PLAN_TEMPLATE`(rule 加固 + 反例 4)
- `llm_service.py` · `_inline_phase_0_plan_prompt()`(byte-equal 同步 3,247 chars)
- 三個 prompt(Phase 0 / Phase A / Phase C)的 embedded vs inline 全部 byte-equal

---

## [0.4.2] · 2026-05-14 — Phase C 雙軸 bar+line 強制路由

**Patch · 修 case 01 失敗(雙軸 query 走錯成 KPI 卡片)。**

### 🐛 修正

- **Phase C rule 5.9 新增**:當 query 同時含「絕對量(件數/數量)」+「比率(率/比例/%)」+「比較性副詞(比較/同時看到/vs)」,**必須**走 dual-axis bar+line。完整配方含 `yAxisIndex=0/1` / `min:0,max:100` 比率軸 / smart 0-1 偵測 `* 100`。
- **Phase C rule 8 收緊**:`_use_table` 嚴禁清單加上「絕對量 + 比率/率/比例」,並明確標 case 01 query 作反例。
- **`_inline_phase_c_echarts_prompt()` 同步**(byte-equal 21,712 chars,含先前漏接的 v0.3.5 句型擴充)。

### 🎯 預期效果

- case 01「比較各公司的退單率與申請數,我想同時看到絕對量與比率」 → 走 bar+line 雙軸,**不再**走 KPI cards。

---

## [0.4.1] · 2026-05-14 — Phase A `$cond` blacklist

**Patch · 修 case 01 secondary issue($cond 在 `$project` 違規)。**

### 🐛 修正

- **`sanitize_pipeline()` 升級**:回傳改為 `(pipeline, warnings)` tuple。偵測 `$project` / `$addFields` / `$set` 內含派生表達式(`$cond` / `$switch` / `$ifNull` / `$divide` / `$multiply` / `$add` / `$subtract` 等)的欄位 → **自動移除**,讓 Phase B 用 pandas 重算。產生 warning list 給 app 端 toast / runner 端 log。
- **Phase A prompt 加 rule 5 反例**:派生 operator 完整黑名單(條件類 / 算術類 / 字串類 / 聚合類)+ 「Phase A 撈,Phase B 算」口訣 + JSON 反例 + Python 正解。
- **`app.py` / `test_runner.py` 接 warnings**:app 走 `st.toast(icon="🧹")`,runner 走 print + 記到 `phases.pipeline.sanitize_warnings`。
- **`_inline_phase_a_pipeline_prompt()` byte-equal 同步**(2,972 chars)。
- **新增 4 個 sanitize_pipeline unit test**:`$cond+$divide` strip、clean pipeline 不誤殺、missing `$` 修補、巢狀 `$cond` 偵測。

### 🛠️ Breaking change(內部 API)

- `sanitize_pipeline(pipeline) -> list` → `sanitize_pipeline(pipeline) -> tuple[list, list[str]]`。
- 所有 caller(app.py / test_runner.py)已同步更新。

---

## [0.4.0] · 2026-05-14 — Export Insight → PPTX

**Minor · 新增「一鍵將分析結果導成單頁 PPTX 報告」功能。**

### ✨ 新功能

- **Phase D 結束後出現 `📤 Export Insight → PPTX` 按鈕**
  - 點擊後產出單張投影片(16:9):左半為圖表 / 右半為商業洞察
  - 投影片含品牌條(HR 紅 + 話圖黑)、查詢字串、領域、資料來源、生成時間
- **支援 6+ 種圖型 → matplotlib 渲染**
  - Bar(single / grouped / stacked / horizontal)
  - Line(支援雙軸 twinx)
  - Pie
  - Heatmap(imshow + colorbar)
  - KPI cards(textbox grid 排版)
  - Table fallback(python-pptx 原生表格 + 斑馬條)
- **Insight markdown 解析器**:把 Phase D 的 markdown 轉成 python-pptx 段落
  - 支援 `#` heading、`-/*/+/數字.` bullet、`**bold**` run
  - bullet 縮排層級依 markdown 縮排自動推斷
- **跨平台 CJK 字體偵測**(`_pick_font_stack`):
  - macOS:PingFang TC / Heiti TC / Hiragino Sans GB → Latin+CJK 一次到位
  - Linux:Noto Sans CJK / Source Han Sans / WenQuanYi
  - Windows:Microsoft JhengHei / YaHei
  - 失敗 fallback 到 DejaVu Sans(Latin only)

### 🗂️ 新檔案

- `export_pptx.py` — 全部對外只有兩個函式
  - `render_chart_to_image(option, Q, chart_engine, fig=None) -> bytes`
  - `build_report_pptx(query, plan_text, Q, final_option, final_fig, insight_text, ...) -> bytes`
- `scripts/smoke_export_pptx.py` — 7 個場景 × 人造 Q 的端到端驗證腳本

### 🔧 修改

- `app.py`:
  - Phase D 後寫 `st.session_state.last_export_payload`
  - chat 歷史下方新增 Export / Download 按鈕(只在有 payload 時出現)
- `requirements.txt`:加 `python-pptx>=0.6.21` / `matplotlib>=3.5.0`

### 📐 設計取捨

- **matplotlib 為共通分母**:ECharts option 在 PPTX 端逆向萃取為 matplotlib;Plotly 走 `fig.to_image()`(若 kaleido 缺則 graceful fallback 到表格)。
- **不依賴 headless browser**:整個 export 是純 Python 程式,啟動 < 0.5s。
- **section header 用 `▎` 而非 emoji**:emoji 跨平台字體覆蓋差,改用 Latin1 thick bar 視覺等價。

---

## [0.3.3] · 2026-05-14 — CLI display fix + docs refresh

**Patch · 修正 admin CLI 顯示讓 baseline 對比直觀。**

### 🐛 修正

- **`admin/list_test_runs.py` `pass/total` 改 `OK/total`**(含 `passed + refusal_detected`)
  - 原本 CLI 只顯示嚴格 `passed`,易讓人誤以為退步(20 看起來比 22 差)
  - 對齊 `test_runner.py main()` 的 `pass_count` 計算方式
  - 新增獨立 `refusal` 欄位顯示拒絕計數
  - `failed` 欄位也含 `phaseA_error`(更全面)

### 📚 文件

- **`README.md` 全面更新到 v0.3.x**:加 Repository / admin UI / migration / sidebar domain switcher / 新 fallback 機制等
- **`CHANGELOG.md` 補上 v0.3.2 / v0.3.3 entry**
- `AI_CONTEXT.md` 版本表加 v0.3.2 / v0.3.3 行

---

## [0.3.2] · 2026-05-14 — Critical .env loading fix + embedded cleanup + datetime deprecation

**Patch · 三個關鍵 hotfix。**

### 🚨 Critical fix · `.env` 從來沒被讀進來!

**Root cause**:整個 codebase 都沒呼叫 `load_dotenv()`,`.env` 一直被當裝飾品。系統能跑只是因為 shell 剛好 export 過環境變數。

**症狀**:某次重啟 terminal / 換 shell,`.env` 設定全部失效,LLM endpoint 連到錯地方(例如顯示「Powered by gpt-4o-mini」但實際 endpoint 是 ollama URL)。

**修正**:
- `config.py` 開頭加 `load_dotenv(override=False)`(shell 已 export 的 env 優先,`.env` 是備援)
- `requirements.txt` 加 `python-dotenv>=1.0.0`

### 🧹 Embedded metadata 預設只 tflex

**Root cause**:`embedded_metadata.py` 自動 import `_test_ecommerce_metadata` / `_test_healthcare_metadata`(v0.2.x 跨 domain 測試 fixture),merge 進 `EMBEDDED_PROMPTS`,造成 UI sidebar 出現 3 個 domain。

**修正**:
- `embedded_metadata.py` 預設只 import `tflex`
- 新增 `load_test_fixture_metadata()` 函式 — 可選載入(給 `test_generality.py` 用)
- `migrations/002_seed_metadata.py` 加 `--include-test-fixtures` flag(預設不帶)
- `app.py` / `pages/01_test_cases.py` / `pages/04_metadata.py` 的 domain selector 預設**永遠優先 tflex**

### ⏰ Per-domain baseline isolation(從 v0.3.1 D8i 帶進來)

**Root cause**:`TestRunRepository.get_baseline()` 沒 domain filter,跨 domain compare 會交叉污染。

**修正**:
- `get_baseline(domain=None)` / `get_latest(domain=None)` 接受 domain filter
- `compare_with_baseline(run_id)` 自動讀 `run.domain` 找對應 baseline
- `admin/compare_baseline.py` / `mark_baseline.py` 跟著 domain-aware
- `pages/02_test_runs.py` 用 sidebar 當前 domain filter 撈 baseline

### 📝 Prompts / Metadata admin pages(從 v0.3.1 D8g/D8h 帶進來)

- **`pages/03_prompts.py`** · Prompt 版本管理
  - 5 phase × domain_scope 選擇器
  - 版本歷史 + activate 按鈕
  - Jinja2 編輯器 + sample 變數即時 render preview
  - 儲存為新版本 + auto-activate option
- **`pages/04_metadata.py`** · Metadata 版本管理
  - Domain selector + 「➕ 新增 domain」精靈
  - 4-metric summary(collections / KPIs / limitations / charts)
  - 6 個 expandable sections + JSON 編輯器
  - 版本歷史 + activate

### 📅 datetime UTC deprecation 修正

Python 3.12+ 開始警告 `datetime.utcnow()` / `datetime.utcfromtimestamp()` 即將移除。全 codebase 4 個檔案改用 `datetime.now(timezone.utc)`:
- `test_run_repository.py`
- `test_case_repository.py`
- `prompt_repository.py`
- `test_runner.py`

### 📚 文件拆分

- **`AI_CONTEXT.md`**(603 行,25KB)— 架構 + API + deployment(主要餵 LLM agent 的文件)
- **`AI_CODE.md`**(3372 行,148KB,新增)— v0.2.x 完整源碼快照
- v0.3.x 新檔(repositories / migrations / admin / pages)的 API 在 AI_CONTEXT.md section 17 完整列出,不重複 embed

### 📦 受影響檔案

新增:
- `AI_CODE.md`
- `pages/03_prompts.py` / `pages/04_metadata.py`

修改:
- `config.py` — load_dotenv 修正
- `requirements.txt` — python-dotenv 依賴
- `embedded_metadata.py` — 預設只 tflex + `load_test_fixture_metadata()` helper
- `migrations/002_seed_metadata.py` — `--include-test-fixtures` flag
- `test_run_repository.py` — `get_baseline(domain)` / `get_latest(domain)` 加 filter
- `admin/compare_baseline.py` / `mark_baseline.py` — domain-aware
- `app.py` / `pages/01_test_cases.py` / `pages/04_metadata.py` — sidebar tflex 優先
- `test_runner.py` / `test_case_repository.py` / `prompt_repository.py` — datetime 修正
- `AI_CONTEXT.md` — section 19 加「源碼移到 AI_CODE.md」指引

---

## [0.3.1] · 2026-05-14 — Prompt / Metadata admin pages + per-domain baseline

**Patch · v0.3.0 follow-up,補完 admin UI 缺口 + 修 cross-domain baseline 污染。**

### ✨ 新增 admin UI

- **`pages/03_prompts.py`** — Prompt 版本管理
  - 5 phase prompt × domain_scope 選擇器
  - 版本歷史 + 一鍵 activate(自動 deactivate 同 key/scope 其他版本)
  - Jinja2 編輯器 + sample 變數即時預覽(`Render preview` 按鈕)
  - 儲存為新版本 + auto-activate option

- **`pages/04_metadata.py`** — Metadata 管理
  - Domain selector + 「➕ 新增 domain」精靈
  - 4-metric summary(collections / KPIs / limitations / charts)
  - 6 個 expandable sections(collections / kpi_definitions / data_limitations / charting_guidance / relationships / business_context)
  - 版本歷史 + activate
  - 完整 JSON 編輯器(含 validation)

### 🐛 修正 · Per-domain baseline 隔離

**根因**:v0.3.0 的 `get_baseline()` / `get_latest()` / `compare_with_baseline()` 沒有 domain filter,造成 tflex 跟 ecommerce baseline 會交叉污染。

- `TestRunRepository.get_baseline(domain=None)` / `get_latest(domain=None)` 加 domain filter
- `compare_with_baseline(run_id)` 自動讀 `run.domain` 找對應 baseline
- `admin/compare_baseline.py` 預設先讀 latest run 推斷 domain → 找該 domain 的 baseline
- `admin/mark_baseline.py` `--latest` 模式偵測該 domain 已有舊 baseline 時 warn
- `pages/02_test_runs.py` 用 sidebar 當前 domain filter 撈 baseline

**現在每個 domain 都是獨立的 baseline pipeline**。

### 📚 文件拆分

- **`AI_CONTEXT.md`**(603 行,25KB)— 架構 + API + deployment(主要餵 LLM agent 的文件)
- **`AI_CODE.md`**(3372 行,148KB,新增)— v0.2.x 完整源碼快照(深入 debug / 移植時用)
- v0.3.0 新檔(repositories / migrations / admin / pages)的 API 在 AI_CONTEXT.md section 17 完整列出,不重複 embed

### 📦 受影響檔案

新增:
- `pages/03_prompts.py` / `pages/04_metadata.py`
- `AI_CODE.md`

修改:
- `test_run_repository.py` — `get_baseline` / `get_latest` 加 domain filter
- `admin/compare_baseline.py` / `admin/mark_baseline.py` — per-domain 邏輯
- `pages/02_test_runs.py` — baseline lookup 用 domain filter
- `AI_CONTEXT.md` — section 19 加「源碼移到 AI_CODE.md」指引

---

## [0.3.0] · 2026-05-14 — Repository 層 + DB-backed prompts / metadata / test infra

**Minor release · 內容外部化的關鍵架構升級。所有 prompt / metadata / test case / test run 全部從 hardcoded Python 檔案搬進 MongoDB,且有 Streamlit 管理 UI。**

### 🏗️ 架構大方向

從 v0.3.0 起,LLM agent 的核心內容(prompts、domain metadata、test cases)不再寫死在程式碼:

```
v0.2.x:                              v0.3.0:
  ┌─────────────────┐                 ┌─────────────────┐
  │ llm_service.py  │                 │ llm_service.py  │ ← code logic only
  │  + 5 inline     │                 └────────┬────────┘
  │    f-strings    │                          │
  │  + TASK_METADATA│                          ↓
  └─────────────────┘                 ┌─────────────────┐
                                      │  Repository 層  │
                                      │  - prompt       │
                                      │  - metadata     │
                                      │  - test_cases   │
                                      │  - test_runs    │
                                      └────────┬────────┘
                                               │
                                MongoDB (live) ┼─→ embedded fallback (緊急救援)
```

改 prompt 不用 redeploy、新增 domain 不用改 code、test cases 線上可編輯。

### ✨ 新增

- **4 個 Repository class**(`prompt_repository.py` / `test_case_repository.py` / `test_run_repository.py`):
  - `PromptRepository.get_template(key, domain) / .render(key, domain, **vars)` — Jinja2 template
  - `PromptRepository.get_metadata(domain) / .save_new_metadata_version() / .list_active_domains()`
  - `TestCaseRepository.get_cases(domain, filter_prefix, case_ids) / .upsert_case() / .activate / .deactivate / .delete`
  - `TestRunRepository.save_run() / .list_recent() / .mark_as_baseline() / .compare()`
  - 三層 fallback:DB → 60s in-memory cache → embedded 副本(緊急救援)
- **Embedded fallback 副本**(完整,保證 DB 沒接時系統不掛):
  - `embedded_prompts.py` — 5 個 phase 的 Jinja2 模板(~30K chars)
  - `embedded_metadata.py` — 3 個 domain metadata(tflex/ecommerce/healthcare)
  - `embedded_test_cases.py` — 26 個 tflex test cases
- **3 個 Migration scripts**(idempotent + byte-level verify):
  - `migrations/001_seed_prompts.py` — 推 5 個 prompts 進 DB
  - `migrations/002_seed_metadata.py` — 推 3 個 domain metadata
  - `migrations/003_seed_test_cases.py` — 推 26 個 tflex cases
- **2 個 Streamlit admin page**(multi-page app):
  - `pages/01_test_cases.py` — Test case CRUD UI(add / edit / activate / deactivate / delete + ECharts checks)
  - `pages/02_test_runs.py` — Run history viewer + baseline mark + side-by-side compare
- **4 個 Admin CLI 工具**:
  - `admin/list_prompts.py` — 列當前 active prompt versions
  - `admin/list_test_runs.py` — 列最近 N 筆 runs(table or JSON)
  - `admin/mark_baseline.py` — 標 / 取消 baseline(含 `--latest` 快捷)
  - `admin/compare_baseline.py` — 兩筆 run 差異對比(預設 latest vs baseline)

### 🌐 Domain Switching

- **Sidebar `🌐 Active Domain` selector** — 列 active domains,切換時顯示 confirm dialog(清空對話脈絡 + 重建 LLMService)
- **Current Question 橫條加 domain badge**(暗紅 pill)
- **`--domain` flag in test_runner** — 跑指定 domain 的 cases
- **Prompts 全部 domain-agnostic (`domain_scope="*"`)** — 加新 domain = 只寫 metadata,不改 prompt

### 🔄 LLMService 改造

- Constructor 新增 `prompt_repo` + `domain` 參數
- 5 個 `_render_phase_X_prompt()` 方法走 repo,失敗 fallback inline
- 5/5 byte-equal 通過驗證(repo render == inline f-string)
- `PROMPT_REPO_ENABLED=false`(預設)時行為 100% 跟 v0.2.4 一致 — **零 breaking change**

### 📊 Performance Tracking

- 每次 `test_runner.py` 跑完寫入 `test_runs` collection,含:
  - `active_versions` 快照(prompts + metadata 的 ObjectId)
  - `git_commit` short SHA
  - `summary`:pass / fail / refusal / wall time / tokens
  - 完整 `case_results`
- `--baseline` flag 自動標 baseline,後續 runs 用 `compare_baseline.py` 對比

### 📦 Dependencies

- 新增 `jinja2>=3.1.0`(template engine)

### 🛡️ 已知 silent bug 保留(將於 v0.3.1 修正)

- **Phase C `{{ECHARTS_FEW_SHOT}}` 從沒被注入過** — 原 f-string `{{...}}` 解析為 `{...}` 後,`.replace("{{...}}", ...)` 雙括號比對 mismatch。v0.3.0 byte-equal 規範下保留,v0.3.1 用 Jinja2 變數正解。

### ⚙️ 部署 / 啟用流程

```bash
# 1) 安裝 dependency
pip install jinja2

# 2) 三道 migration(順序重要)
python migrations/001_seed_prompts.py
python migrations/002_seed_metadata.py
python migrations/003_seed_test_cases.py

# 3) 啟用 repo 模式
export GENBI_PROMPT_REPO=true

# 4) 跑 baseline run
python test_runner.py --baseline

# 5) 啟動 Streamlit(主頁 + 兩個 admin pages)
streamlit run app.py
```

### 📚 受影響檔案

新增:
- `prompt_repository.py` / `test_case_repository.py` / `test_run_repository.py`
- `embedded_prompts.py` / `embedded_metadata.py` / `embedded_test_cases.py`
- `migrations/__init__.py` + 3 個 seed scripts
- `pages/01_test_cases.py` / `pages/02_test_runs.py`
- `admin/__init__.py` + 4 個 CLI tools

修改:
- `config.py` — 5 個 collection name env override + `PROMPT_REPO_ENABLED` flag
- `requirements.txt` — 加 jinja2
- `llm_service.py` — 5 個 phase prompt 改用 repo,prompt_repo + domain 構造參數
- `app.py` — sidebar domain switcher + confirm dialog + Current Question domain badge
- `test_runner.py` — `--domain` / `--baseline` / `--no-save-run` flags + 寫 test_runs collection

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
