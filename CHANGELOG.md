# Changelog

All notable changes to GenBI will be documented in this file.
版本格式採用 [Semantic Versioning](https://semver.org/):`MAJOR.MINOR.PATCH`。

---

## [0.10.3] · 2026-05-17 — Option 1 saner:temp 降溫 + 部門 / horizontal max / synonym 三補

**Patch · v0.10.2 retry temp=0.3 引發 variance(洗牌但 net 0:fix 6 新壞 6)→ 降溫 + 修 3 個具體 bug。**

### 🔴 v0.10.2 baseline 觀察

跑出來 17/26 (65%) 跟 v0.9.3 一樣,但**6 個 stuck case 被救 + 6 個新壞**(穩定 case 因 temp 抬高被翻盤)。Net 0 = bad trade-off。

仍 fail 3 個 chronic case:
- Case 03(LLM 產 `rtn_count`,synonym list 沒這名)
- STK-01(LLM horizontal 但漏 `xAxis.max=100`)
- STK-04(Phase C fallback)

新發現:Case 08 因 Phase D 寫「部門」被 denial marker 誤判(類似「趨勢」的 false positive)。

### ✨ P1:`_retry_temp` 0.3 → **0.15**

降一半。原 0.3 太 chaotic,0.15 還是有 break-stuck 機會但 noise 降低。4 個 generate_* 同步調:

```python
# v0.10.2 → v0.10.3:
_retry_temp = 0.15 if previous_error else self.default_temperature  # 之前是 0.3
```

### ✨ P2:`部門` 加進 `_CONTEXT_REQUIREMENTS`

跟「趨勢」同套機制 — 只在「依/各/分/間/比較/by」等 dept-analytical context 才算 misuse:

```python
_CONTEXT_REQUIREMENTS = {
    "趨勢": ("過去", "未來", "月", "週", "季", "年", "日", "時間", ...),
    "部門": ("各部門", "依部門", "分部門", "by department",
              "部門間", "部門比較", "部門別", "部門差異", "departmental"),  # 新
}
```

5 個 unit test 全綠:
- 「考慮按部門細分」→ OK(forward-looking 建議)
- 「**各部門**退單率差異」→ 違規(真誤用)
- 「**若有**部門資料」→ OK(caveat)
- 「**部門間**流動率比較」→ 違規(真分析)
- 「**無**部門欄位,**無法**做部門比較」→ OK(denial context)

### ✨ P3:Phase C `STACKED_100_HORIZONTAL` block 強化 `xAxis.max=100`

baseline 多次:LLM 寫對 horizontal 但漏 `"max": 100`,bar 看起來 100% 但實際 scale 0~total。

`_PHASE_C_BLOCK_STACKED_100_HORIZONTAL` 加 **CRITICAL FATAL** 警告:

```
🚨【CRITICAL FATAL — v0.10.3 強化】橫向 100% stacked 必須寫
   `xAxis: {"max": 100, ...}`!value 軸從 vertical 的 yAxis 換到
   xAxis,但「max=100 鎖頂」這條規則沒消失,只是搬到 xAxis 上。

   ❌【baseline 多次踩雷】:
   `xAxis: {"type": "value", "axisLabel": {"formatter": "{value}%"}}`
   忘 "max": 100
   ✅ 正解:"max": 100 必須跟 formatter "{value}%" 一起,缺一不可
```

### ✨ P4:Case 03 synonym 補 `rtn_count` / `return_cnt`

baseline 看到 LLM 產 `rtn_count` 不在 synonym list,加進去。兩處同步(`test_runner.py` + `embedded_test_cases.py`):

```python
['return_count', 'RTN', 'rtn', 'RET', 'rtn_count', 'return_cnt']
```

### ✅ 驗證

- 4 檔 AST OK
- 5 個 patch 點 sentinel check 全在
- 5 個 部門 context unit test 全綠(boundary 含 denial / caveat / 真誤用)

### 📋 預期 baseline 影響

| 項目 | 預期 |
|---|---|
| temp 0.15 變異性 | 減半,可能 ±1-2 case |
| Case 03 | ✅(synonym 命中 rtn_count) |
| Case 08 部門誤判 | ✅(context 不含 "by/依/各 部門") |
| STK-01 horizontal max=100 | 🎲 取決於 LLM 是否吃 prompt 警告 |

預期 17 → **20-22/26 (77-85%)**

### 🚧 若還是不夠 → Level 2

semantic validator(retry 條件加上「exec OK 但內容錯」),預估 ~半天 work。

---

## [0.10.2] · 2026-05-17 — Retry temperature bump(Level 1 strengthening)

**Patch · 打破 LLM 在 `temp=0` deterministic 下連續犯同錯的 stuck pattern。**

### 🔴 觀察

v0.9.3 baseline 6 個 LLM bug 中,3 個是 Phase B/C **連 3 次同錯**(Case 01 KeyError, STK-03/05 fallback)。Cause:`temperature=0` deterministic + 同個 error feedback 餵回去 → 同樣 output stuck。

retry hint 早就給了具體 fix(v0.8.7+),但 LLM 「看到了但沒採用」。

### ✨ Fix

4 個 generate_* method 都加 retry temp bump,attempt 1 維持 default,attempt 2+ (`previous_error` 非空)用 `temp=0.3`:

| Method | Trigger |
|---|---|
| `generate_pipeline` | internal 3-attempt loop,`attempt > 0` 時 temp=0.3 |
| `generate_preprocess_code` | external retry,`previous_error` 非空時 temp=0.3 |
| `generate_plot_code` | 同上 |
| `generate_echarts_option` | 同上 |

```python
# 各 method 統一 pattern:
_retry_temp = 0.3 if previous_error else self.default_temperature
raw = self._call_llm([...], temperature=_retry_temp, phase="...")
```

### 🎯 預期影響

- **Case 01 / STK-03 / STK-05** 等 stuck-pattern 案,attempt 2 因 temp 抬高有機會走不同 path
- **不動 attempt 1 行為**:大多數 case 還是 1 次過,維持穩定
- **Cost 中性**:同樣 3-attempt 上限,只是 attempt 2 換 temp

### ⚠️ 風險

- temp 0.3 引入隨機性 → 同 query 在不同 baseline run 結果**更不一致**(本來 deterministic stuck 但至少穩定錯,現在每次可能不同)
- 若 attempt 1 OK,完全不受影響(99% 行為跟之前一樣)
- 若需要更 deterministic 的 production behavior,改回 `temp=0.0 or 0.1` 都很簡單

### ✅ 驗證

- `llm_service.py` AST OK(3351 行)
- 8 處 `_retry_temp` 標記(4 methods × comment+assign)

### 📋 Next(等 baseline 看 ROI)

- v0.10.3 Level 2 — Semantic validator for Phase C(catch「exec OK 但內容錯」STK-02 維度倒置那種)
- v0.10.4 Level 3 — Multi-shot voting(只在 Level 1+2 不夠時才做)

---

## [0.10.1] · 2026-05-17 — Hotfix:test_runner 補做 chart-orientation aware

**Hotfix · v0.9.1 加 horizontal stacked block 後留下的 test framework gap**。

### 🔴 Bug

v0.9.3 baseline 17/26 (65%) — dig 進去發現 **3 個 case (STK-01 / STK-04 / STK-06) 是 LLM 正確選 horizontal stacked,但 test framework hard-code 假設 vertical 而誤判**:

```python
# STK-01 actual chart code:
"xAxis": {"type": "value", "axisLabel": {"formatter": "{value}%"}},
"yAxis": {"type": "category", "data": Q["company_code"].unique().tolist()},
```

- test check `xAxis.data 是 list` → 失敗(value axis 沒 data)
- test check `yAxis.max == 100` → 失敗(value 在 xAxis,max:100 在 xAxis)

### ✨ Fix · `_is_horizontal` orientation detector + axis-aware checks

`test_runner.py · run_case()`:

```python
_xaxis_dict = option.get("xAxis", {}) or {}
_yaxis_dict = option.get("yAxis", {}) or {}
_is_horizontal = (
    _xaxis_dict.get("type") == "value"
    and _yaxis_dict.get("type") == "category"
)
_cat_axis_data = (_yaxis_dict if _is_horizontal else _xaxis_dict).get("data")
_val_axis_dict = _xaxis_dict if _is_horizontal else _yaxis_dict
```

3 個 check 全部 orientation-aware:
- `xAxis.data 無重複` → 改檢查 `_cat_axis_data`(垂直 = xAxis,橫向 = yAxis)
- `所有 series.data 長度 == X.data 長度` → 同上,動態 label
- `yAxis.max == 100` → 改檢查 `_val_axis_dict.max`(垂直 = yAxis,橫向 = xAxis)

訊息也動態:橫向時 label 顯示「(橫向,category 在 yAxis)」讓 debug 直覺。

### ✅ 驗證

- `test_runner.py` AST OK(1550 行,net +25)
- 4 個 hook 點全在(_is_horizontal / _cat_axis_data / _val_axis_dict / horizontal label)

### 📋 預計影響

v0.9.3 baseline → v0.10.1:
- **STK-01** fail(2) → ✅(2 個 axis check 全是 false positive)
- **STK-04** fail(2) → ✅(同上)
- **STK-06** fail(1) → ✅(只 1 個 axis check)

預計 17/26 → **20/26 (77%)**。

剩下沒救的 6 個是真 LLM bug:
- 01(Phase B KeyError)
- 03(synonym list 沒命中 — case 寫法問題?)
- 06(Phase C fallback)
- STK-02(LLM 維度倒置,series=15 應該=3)
- STK-03(Phase C fallback)
- STK-05(Phase C fallback)

這些是 LLM stochasticity / 真錯,不是 test framework 問題。

---

## [0.10.0] · 2026-05-17 — Composite chart layout (路徑 C:chart + Q side panel)

**Minor · BI-grade composite dashboard 第一階段。** chart 旁邊新增 Q DataFrame side panel,使用者看 chart 同時看到背後的 row-level 資料。

對齊先前討論的 **路徑 C → B 漸進策略**:v0.10.0 落實 C(統一 layout),v0.10.1+ 漸進加 B(intent-specific composite)。

### ✨ D1:Sidebar 加「🧩 圖表呈現模式」toggle

3 個 option,default = **標準**:

| Mode | 效果 |
|---|---|
| **精簡** | 只渲染主圖,全寬度(=v0.9.x 行為)|
| **標準**(預設)| chart 左 60% + Q DataFrame 右 40% side panel |
| **複合** | 同「標準」(v0.10.0 階段);v0.10.1+ 會依 chart intent 換 layout |

存進 `st.session_state["chart_layout_mode"]`,切 mode 時 history 也自動跟著重渲。

### ✨ D2:`render_composite_chart()` + `_render_q_side_panel()` helper

`render_composite_chart(chart_render_fn, Q, intent, mode, key_prefix)` — 統一 chart 渲染 wrapper。
- 接收 0-arg `chart_render_fn` callable(`st_echarts` / `st.plotly_chart` 包起來)
- 依 mode 決定:`精簡` → 直接呼叫;其他 → `st.columns([3, 2])` 分左右

`_render_q_side_panel(Q, intent, key_prefix, max_rows=100)` — side panel 渲染。
- 自動 `column_config`:rate / ratio / 率 → percentage format;int → 千分位;float → 2 decimal
- Cap 100 row,height 動態(35 + n_shown × 28,最高 520px)
- 上方 caption「📊 處理後資料 Q · {N} 列 × {C} 欄(顯示前 N)」

### ✨ D3:Live render + history loop 都走 composite

- **Live path**(`if query:` 區塊):chart 渲染包進 `_render_main_chart` 後丟給 `render_composite_chart`。Detect intent 用 `_detect_chart_intent(query)`。
- **History loop**:每個 assistant msg 也走 composite(讀 `msg["chart_intent"]` + `msg["q_for_composite"]`),user 切 mode 時 history 跟著重渲。
- **Message append**:新增 `q_for_composite` (Q.copy()) 跟 `chart_intent`,replay 用。

table fallback case **不加 side panel**(table 本身就是主體),維持原行為。

### ✅ 驗證

- `app.py` AST OK(1413 行,net +140)
- 9 個 hook 點全在(sidebar var / label / 2 helpers / 2 msg fields / 2 history renderers / 1 live wrapper)
- 不動 `LLMService` / 不動 `embedded_prompts` / 不動 `test_runner`(test framework 看的是 `option` dict,跟 layout 無關)

### 📋 Roadmap(下個 minor)

v0.10.1+ 路徑 B — intent-specific composite(replace `_render_q_side_panel` 內 default 為 intent-aware):

| Intent | composite layout |
|---|---|
| `bar / bar_horizontal / pie` | chart 60% \| **Top-N 排序 table**(highlight 第 1/2/3) 40% |
| `stacked_100 / stacked_raw / *_horizontal` | chart top \| **per-stack 總計 + 占比 table** bottom |
| `line_single / line_dual` | chart 70% \| **first / last / Δ% / min / max** summary 30% |
| `scatter` | chart 60% \| **outlier(±2σ)list** 40% |
| `heatmap` | chart top \| **top-5 highest cells + 兩軸 legend** bottom |
| `kpi_table` | (無改變,本來就是 table)|

### 📋 已知限制

- Streamlit `st.columns` 不 responsive — 窄螢幕還是並排(BI 通常 desktop,可接受)
- side panel cap 100 列 — 大資料集只看前段(future 加 pagination 可選)
- mode toggle 是 session-level,page reload 會 reset 成 default「標準」

---

## [0.9.3] · 2026-05-16 — Hotfix:page navigation 後 phase outputs 消失

**Hotfix · User 回報的 Streamlit stateless rendering 經典坑。**

### 🔴 Bug

User 在 app 頁 submit query → 5-phase workflow 跑到一半 → 切到 `🔍 Task Traces` 等其他 page → 切回 app 頁 → **只剩 Current Question banner,Phase 0/A/B/C/D 全部消失**。

### 🎯 根因

Streamlit 每次 page navigation 觸發整個 script rerun。Phase 階段的 `st.expander` / `st.markdown` / `st.code` 是**渲染呼叫**,只在當下 script 執行寫 DOM,**沒寫進 `st.session_state` 就不會 persist**。原本只在 5 phase 全部完成後才 `messages.append({final})`,中途切走就遺失所有中間階段。

### ✨ Fix · piggyback `st.session_state.messages`

不增新 state,改用既有 `messages` 漸進式 enrich:

1. **Query 提交時** → append 一個 `in_progress=True` assistant slot:
   ```python
   {"role": "assistant", "content": "🧠 分析進行中...",
     "in_progress": True, "phases_done": {}}
   ```

2. **每個 phase 完成** → snapshot 進 `messages[-1]["phases_done"]`:
   - `plan`: text
   - `pipeline`: start_collection / json / summary / n_rows / raw_df_head
   - `preprocess`: code / q_info / q_head
   - `echarts_code`: raw plot_code(option dict 走既有 `echarts_option` field)

3. **最終 success / refuse / error** → **REPLACE**(不 append)last message,保留 phases_done。

4. **history loop 加 `_render_phases_done(phases, interrupted)`** 重渲已完成階段(navigation back 走這條)。

5. **每次 script 啟動,標前次未完成的 `in_progress` 為 `interrupted=True`** → 切回來時顯示 `⚠️ 上次執行被中斷` 警告 + 已完成階段內容。

### 🎁 額外得益

`pages/05_task_traces.py` / `06_learning_review.py` 切回來都自動正確,因為 history loop 本來就會跑。

Page reload(F5)後同樣也會看到 `interrupted` 警告 + 已完成階段,跟 navigation 同行為。

### ✅ 驗證

- `app.py` AST OK(1274 行,net +120)
- 8 個 phase snapshot 全在(in_progress init / Phase 0 / A / B / C / interrupted detection / final replace × 2)
- 既有完成 path(refuse / final success / Phase B exhausted / 系統 exception)都改 REPLACE 不 append,確保不會雙寫 assistant message

### 📋 不變

- `LLMService` 完全沒動
- 既有 history 渲染 fig / echarts_option / table_df / insight 都不變
- LLM call 流程不變,純 state 管理改動

---

## [0.9.2] · 2026-05-16 — Confidence decay + STK 邊角 + nightly orchestrator + docs

**Patch · 補完 self-learning MVP 缺漏項 + 解 baseline 剩 2 fail + cron 整合 + 文件刷新。**

### ✨ P1:Confidence decay job(spec §16 補做)

`learning/instinct_consolidator.py` 加 `apply_confidence_decay()`:
- Active instinct `updated_at` ≥ 90 天 (dormancy_days) → `confidence -= 0.02`
- `confidence < 0.50` → `status='deprecated'`
- 每次 decay 成功把 `updated_at` 撥 now + 寫 `last_decay_at`(日內 idempotent)
- 寫 `learning_jobs` record(job_type='confidence_decay')
- CLI:`python -m learning.instinct_consolidator --skip-consolidation --skip-contradiction`(或直接跑全部就會帶 decay)

5 個 unit test 全綠:dormant 100d / fresh 30d / boundary 0.50→0.48 deprecate / boundary 0.51→0.49 deprecate / dry-run 不寫 DB。

### ✨ P2:STK-04 multi-state composite column rule

Phase B `_PHASE_B_BLOCK_STACKED_LONG_PCT` 加 generic rule:query 列舉 ≥3 個衍生狀態(核准/退件/進行中)時,**先 derive 一個 categorical state column 再 groupby**,不要硬塞 raw status 欄位。

附 ❌ 反例(漏 review_result 只剩 2 state)+ ✅ 用 `np.select()` derive 3 state 的標準骨架。

### ✨ P3:test_runner「趨勢」denial marker 收緊

`is_misused()` 加 `_CONTEXT_REQUIREMENTS` 機制:某些 term 在中文裡是「一般用法 + 特定用法」雙義,只有出現特定 context 字眼才算 misuse。

```python
_CONTEXT_REQUIREMENTS = {
    "趨勢": ("過去", "未來", "月", "週", "季", "年", "日", "時間",
              "trend", "time", "monthly", "yearly", "weekly"),
}
```

- 「申請趨勢一致」「需求趨勢」「整體申請趨勢」 → OK(一般中文)
- 「過去三個月趨勢」「每月申請趨勢」「本季審核趨勢」 → 違規(有時間 context)
- 拒絕語境內 → OK(維持原邏輯)

9 個 unit test 全綠(3 OK / 3 違規 / 2 拒絕 / 其他 term 維持原行為)。

### ✨ P4:`scripts/run_learning_jobs.py` orchestrator(spec §16.5)

Nightly cron 一鍵入口,跑 7 個 self-learning job 序列:

```
observation_extraction → verification → consolidation →
contradiction_scan → confidence_decay → resolution_detection →
candidate_generation → dashboard_snapshot
```

**Flag**:
- `--dry-run` 所有 job dry-run
- `--skip <job_name>` 跳過某 job(可多次)
- `--only <job1,job2>` 只跑某幾個
- `--window-days N` / `--extraction-limit N` / `--verifier-limit N` 等

**特性**:
- 一個 job 失敗不影響後續(orchestrator catch + 印 traceback + 繼續)
- 結束印 dashboard snapshot
- exit code:任何 job error → 1(給 cron 判斷)
- LLM service 失敗 → 自動 skip extraction/verification

**推薦 cron**:`0 2 * * * python scripts/run_learning_jobs.py`

### ✨ P5:`AI_CONTEXT.md` / `README.md` 更新

`AI_CONTEXT.md` 加 3 個新 section:
- §17 v0.4.x–v0.7.x Phase 修補 + Task Trace + Modular Prompts 總覽
- §18 v0.8.x–v0.9.x Self-Learning Layer(loop 圖、11 個新模組、5 個 collections、orchestrator、baseline 50→92% 迭代)
- §19 更新版的版本演進表(v0.4–v0.9.2 全列)

`README.md`:Admin pages 從 4 → 7(加 `05_task_traces` / `06_learning_review`)。

### ✅ 驗證

- 4 檔 AST OK
- `scripts/check_prompt_invariants.py` 17 prompts × 52 sentinels 全綠
- 15 個 unit test 全綠(decay 5 + 趨勢 9 + orchestrator JOB_ORDER vs JOB_RUNNERS 對齊)

### 🎯 Self-Learning MVP 至此**完整**

Spec §16(decay)補做 → spec §16.5(scheduler)做完 → 所有 backend 模組到位。剩下:
- Production end-to-end 真實跑(等 user 累積 trace)
- Beyond MVP(L3 Skills / cross-domain / autonomous curation,spec §31)

---

## [0.9.1] · 2026-05-16 — Horizontal stacked bar + Phase B 0-100 normalize

**Patch · user 截圖回報 2 個 bug:「橫向」被忽略 + percentage 顯示成 0.26%(實際是 26%)。**

### 🔴 user 截圖 bug report

Query「請協助整理**橫向**堆疊百分圖,依據 company_code: TST,TSN,TSC 畫出多條 bar, 每條 bar 中呈現 application_category 的佔比」

**Bug 1**: 圖出來是 vertical(縱向)stacked bar,user 的「橫向」被忽略。

**Bug 2**: 圖上顯示「0.26% / 0.34% / 0.25%」之類,實際應是「26% / 34% / 25%」。Phase B 把 percentage normalize 成 **0-1 decimal**,Phase C 套 `{value}%` formatter → 顯示「0.26%」。

### ✨ P1:`_detect_chart_intent` 加 horizontal × stacked 正交組合

`llm_service.py · _detect_chart_intent`:orientation 跟 chart type 本來就是兩個正交維度,user 明說「橫向 / 水平 / horizontal」是強信號,優先級高於 stack 組合詞。

新邏輯(Tier 3 內判 horizontal):

```python
has_horizontal = _has_any(query, _CHART_HORIZONTAL_WORDS)
if has_stack and (has_100pct or '百分比' in query or intra_bar_proportion):
    return "stacked_100_horizontal" if has_horizontal else "stacked_100"
if has_stack:
    return "stacked_raw_horizontal" if has_horizontal else "stacked_raw"
```

### ✨ P2:加 2 個 Phase C blocks(`stacked_100_horizontal` / `stacked_raw_horizontal`)

跟 vertical 版本的 delta:

- **xAxis / yAxis 對調**:xAxis=value, yAxis=category
- **xAxis max=100**(原本是 yAxis max=100)
- **formatter** 移到 xAxis.axisLabel
- **label position="inside"**(原本 `inside` 對 vertical 是垂直內部,對 horizontal 變水平內部,自然合理)
- **grid.left=100** 留 category label 空間(原本 60)

註冊進 `_PHASE_C_INTENT_BLOCKS` map。

### ✨ P3:Plan prompt — orientation 詞優先於 chart type

`_PHASE_0_PLAN_TEMPLATE · C 段視覺化建議` 加 **「orientation 鐵律」**:

```
若 query 明說「橫向 / 水平 / horizontal」,這是強信號,優先級高於組合詞
(stacked / 100% / 占比 / 比例)。必須保留 orientation 在視覺化建議裡,
讓下游 Phase C router 偵測得到。

❌ 反例:user 說「橫向堆疊百分圖」,Plan 寫「堆疊長條圖」(掉了橫向)
✅ 正解:寫「橫向堆疊百分長條圖(horizontal 100% stacked bar)」
```

### ✨ P4:Phase B `stacked_long_pct` 加 CRITICAL FATAL 0-100 normalize 警告

`_PHASE_B_BLOCK_STACKED_LONG_PCT` 加新區塊:

```
🚨 CRITICAL FATAL:percentage 欄絕對必須乘以 100 表達成 0-100 範圍!
   若你算完 count / total 沒乘 100,留 0-1 decimal,下游 Phase C 套
   formatter "{value}%" 會把 0.26 顯示成「0.26%」(意思:約四分之一個
   百分點),完全不是 user 要的「26%」。永遠記得 *100。

❌ 反例(baseline 截圖實際發生):
   counts['percentage'] = counts['count'] / counts['_total_per_group']
   → 結果 0~1,Phase C 渲染顯示 0.26%

✅ 正解:永遠 * 100
   counts['percentage'] = (counts['count'] / counts['_total_per_group'] * 100).round(2)

驗證心法:寫完看 Q.head(),若 percentage 欄值都 < 1 → 幾乎一定漏 *100。
```

### ✨ P5(順手)keyword 補強

- `_CHART_100PCT_WORDS` 加「百分圖 / 百分比圖 / 百分百」
- `_CHART_INTRA_BAR_WORDS` 補無空格變體(`每條bar` / `每個bar` / `每一條bar` 對齊原本有空格的版本 — 中文輸入習慣不一定加空格)

### ✅ 驗證

- 2 檔 AST OK(embedded_prompts 2277 行 / llm_service 3330 行)
- `scripts/check_prompt_invariants.py` 17 prompts × 52 sentinels 全綠
- **10 個 `_detect_chart_intent` + `_detect_preprocess_intent` 單元測試全綠**:
  - actual screenshot query → `stacked_100_horizontal`(原 `stacked_100`)+ Phase B `stacked_long_pct` ✓
  - 多種「橫向 / 水平」+ stacked 組合都正確 route
  - 沒「橫向」字眼的維持原 vertical 行為(STK-01 / 既有 stacked_100 case 不破)
  - 純 `橫向 bar`(無 stacked)維持 `bar_horizontal`

### 📋 預期成果

下次重跑 baseline 預期:
- 截圖 query 走 `stacked_100_horizontal` → 出來是橫向圖
- Phase B percentage 是 0-100 範圍,Phase C 顯示「26%」不是「0.26%」
- 既有 STK-01 / STK-02 維持 vertical(沒「橫向」字眼)
- 既有 STK-08(橫向 100% stacked,本來就過)維持 OK

---

## [0.9.0] · 2026-05-16 — Self-Learning MVP Week 6:Dashboard + Human Review UI 🎉

**Minor · self-learning MVP 6 週 milestone 完整達成。**

對齊 spec §15.5 Manual Review Notification + §20 Human Approval Workflow + §25 Learning Dashboard Metrics。

### ✨ D1:`pages/06_learning_review.py`(366 行)— Streamlit admin page

**4 個 section**:

1. **📊 Dashboard Metrics** — 4 個 metric card 一覽:
   - Observations · verified(+ candidate 數)
   - Instincts · active(+ candidate 數)
   - Candidates · approved(+ pending 數)
   - Baseline pass rate(從最新 `is_baseline=True` test_run 算)
   - Expander 展開看 quality details(retry rate / fallback rate / window)+ full operational JSON

2. **📝 Pending Prompt Rule Candidates** — 待審 candidate 列表:
   - 每筆展開看 source instinct / proposed_rule / supporting observations
   - 3 個按鈕:**✅ Approve / ❌ Reject / 🧪 Mark testing**
   - 點下去**直接寫 DB**(`prompt_rule_candidates.status`),配 `approved_at` / `rejected_at` 時戳 + `approved_by='manual_review'` audit。

3. **⚠️ Contradiction Review Queue** — auto-degrade 偵測到的潛在矛盾:
   - 顯示 obs vs instinct 兩邊內容對照
   - **✅ Confirm degrade** — 接受 degrade,標 `review_decision='confirmed'`
   - **↩️ Dismiss (revert)** — 認為是 false positive,把 instinct 的 `confidence +0.05` + `contradiction_count -1` + `status='active'` 全部 revert

4. **🔍 Recent Observations Browser** — filter + drill-down:
   - 3 個 filter:status / phase / limit
   - Dataframe 顯示(id / phase / status / conf / tags / rec preview / created_at)
   - 選某 obs_id 看完整 JSON(含 cause / recommendation / verifier_decision / dedupe_key)

### ✨ D2:`learning/dashboard_metrics.py`(303 行)— 共用 metric 計算

對齊 spec §25 三個指標分類:

| 分類 | 函式 | 內容 |
|---|---|---|
| **Operational** | `operational_metrics(db, window_days=None)` | observations / instincts 各 status 計數,可選時間窗 |
| **Quality** | `quality_metrics(db, window_days=7)` | retry_rate / fallback_rate(從 task_traces 算)+ latest_baseline_pass_rate |
| **Impact** | `impact_metrics(db)` | candidates pending / testing / approved / rejected 計數 |

加 `needs_review_queue(db, limit=20)` 撈 `learning_jobs.job_type='contradiction_review'` + `status='needs_review'`,給 page 用。

`full_snapshot(db, window_days)` 一次拉所有區段,給 page 渲染 + CLI snapshot 共用。

**CLI**:

```bash
# Human-readable snapshot
python -m learning.dashboard_metrics --days 7

# JSON output (給 API / 監控用)
python -m learning.dashboard_metrics --days 7 --json
```

### ✅ 驗證

- 2 檔 AST OK(`dashboard_metrics.py` 303 行 / `06_learning_review.py` 366 行)
- 4 個 unit test 全綠(mocked DB):
  - `operational_metrics` 各 status 計數正確
  - `impact_metrics` candidate status breakdown 正確
  - `needs_review_queue` 回 list 不 crash
  - `full_snapshot` 含 4 個 top-level keys

### 🎉 Self-Learning MVP 6 週全部達成 (spec §28 Roadmap)

| Week | 模組 | tag |
|---|---|---|
| 1 | bootstrap + collections + failure_filter | v0.8.0 / v0.8.1 |
| 2 | observation_extractor + dedupe | v0.8.2 |
| 3 | verifier + confidence | v0.8.4 |
| 4 | consolidator + contradiction | v0.8.5 |
| 5 | resolution + candidate + gate | v0.8.10 |
| 6 | **dashboard + human review UI** | **v0.9.0** |

**End-to-end self-learning loop 第一版上線,可以開始放在 production 累積實際資料、跑 nightly job、用 dashboard 觀察 instinct 演化。**

### 📋 用法總覽

```bash
# Streamlit 直接打開 admin page(假設 app.py 已在跑)
# http://localhost:8501/learning_review

# 或單獨跑這頁
streamlit run pages/06_learning_review.py

# CLI snapshot(假設一天結束 ops 看一眼)
python -m learning.dashboard_metrics --days 7
```

### 🚧 Beyond MVP(spec §31 Future Scope)

留作 v1.0+ 的事:

- L3 Skills(多步驟 workflow learned pattern)
- L4 Strategic Rules(domain knowledge level)
- Cross-domain promotion(instinct 從 tflex 升 global)
- Autonomous curation(AI 直接 promote,免人類)
- HyperAgents self-modification

---

## [0.8.10] · 2026-05-16 — Self-Learning MVP Week 5:Resolution → TestCase → Candidate → Gate

**Patch · self-learning MVP Week 5 完成 — 3 個新模組,把 self-learning loop 從「觀察 + 聚合 instinct」延伸到「自動產 regression test + 升 prompt candidate + benchmark gate 把關」。**

對齊 `GenBI_v1.3_Self_Learning_MVP_Implementation_Spec.md` §17 + §17.5 + §18 + §19 + §19.5 + §7.5。

### ✨ D1:`learning/resolution_detector.py`(386 行)

掃 `task_traces` 找「同 query_hash 先 failed 後 completed 且時差 < 30 天」的配對,**自動產生 regression test_case**。

**Algorithm**(spec §17.5):

```
For each query_hash:
    if failed run exists
    and later completed run exists
    and 時差 < window_days
    and 沒既有 regression case
        → 寫 regression test_case
```

**新 test_case schema**(寫進 `test_cases` collection):

```python
{
    "case_id": "AUTO-NNNNN",       # 避開 manual case 的 01/02/STK-XX/Txx
    "type": "regression",          # 新 type,跟 happy_path / refusal 分開
    "source": "auto_resolution",
    "name": "Auto-regression from resolved failure",
    "query": "<原 user query>",
    "expected_q_cols_all": [...],  # 從 resolved trace 的 Phase B 實際輸出 capture
    "echarts_required_keys": ["title", "xAxis", "yAxis", "series"],
    "auto_meta": {
        "failed_trace_id", "resolved_trace_id",
        "failed_at", "resolved_at",
        "elapsed_days", "query_hash",
    },
}
```

**Idempotent**:`auto_meta.query_hash` 已存在 → skip。
**CLI**:`python -m learning.resolution_detector --days 30 --limit 50 [--dry-run]`

MVP 暫不檢查 prompt 版本變更(spec §17.5 條件 4),目前 trace 沒記錄 prompt version,用「沒既有 regression case」近似。

### ✨ D2:`learning/candidate_generator.py`(327 行)

把 active instinct 升成 `prompt_rule_candidate`(寫進 `prompt_rule_candidates` collection),等人類審後 merge 進 prompt template。

**升 candidate 條件**(spec §18):

- `instinct.status = 'active'`
- `confidence >= 0.85`
- `evidence_count >= 3`

**target_component 推導**(從 instinct.phase):

| instinct.phase | target_component |
|---|---|
| phase_0 | phase_0_plan |
| phase_a | phase_a_pipeline |
| phase_b | phase_b_preprocess |
| phase_c | phase_c_echarts |
| phase_d | phase_d_insight |
| meta | meta |

**新 candidate schema**(對齊 spec §7.5):

```python
{
    "candidate_id": "PRC-NNNNNN",
    "instinct_id": "INST-AUTO-NNNNN",
    "target_component": "phase_c_echarts",
    "proposed_rule": "<from instinct.rule>",
    "evidence_count": ...,
    "confidence": ...,
    "supporting_observation_ids": [...],
    "status": "candidate",     # candidate / testing / approved / rejected
}
```

**Idempotent**:同 instinct_id 已有 candidate(status in candidate/testing/approved)→ skip。
**CLI**:`python -m learning.candidate_generator --min-confidence 0.85 --min-evidence 3 [--dry-run]`

MVP 直接複製 `instinct.rule` 當 `proposed_rule`(spec 沒強制 LLM 二次潤飾),節省 LLM call 成本。

### ✨ D3:`learning/regression_gate.py`(415 行)

比較 baseline test_run 與 candidate test_run,gate 決定 candidate 能否 promote 到 `approved`。

**4 條 gate**(spec §19):

| Gate | 條件 |
|---|---|
| **1** | No critical regression(沒 case 從 pass 變 fail) |
| **2** | Pass count 不降(`candidate.passed >= baseline.passed`) |
| **3** | Latency 增幅 < 10%(spec §19.5,可調) |
| **4** | Cost 增幅 < 15%(spec §19.5,可調) |

**對外 API**:

- `compare_runs(baseline_run, candidate_run, ...)` — pure logic,不寫 DB,回 verdict dict
- `run_gate(db, candidate_run_id, candidate_id, ...)` — 從 DB 抓 baseline + candidate run,跑 gate,且自動更新 `prompt_rule_candidates.status`:
  - 過 → `approved` + 寫 `gate_verdict` / `approved_at`
  - 沒過 → `rejected` + 寫 `gate_verdict` / `rejected_at`

**Verdict dict** 詳細含 4 gates 各自 pass/fail、`critical_regressions` 清單、`metrics`(baseline/candidate 各項數值 + delta)、`reasons`(人類可讀的 fail 原因)。

**CLI**:

```bash
python -m learning.regression_gate \
    --candidate-run-id 20260516_1922 \
    --candidate-id PRC-000001 \
    --domain tflex \
    [--latency-threshold 0.10] \
    [--cost-threshold 0.15] \
    [--dry-run]
```

### ✅ 驗證

- 3 檔 AST OK(resolution 386 / candidate 327 / gate 415 行,共 1128 行)
- **15 個 unit test 全綠**:
  - `_query_hash` whitespace normalize + 唯一性
  - `_days_between` 邊界處理
  - `_component_for` 6 種 phase mapping
  - `_build_candidate_doc` 9 個欄位正確
  - `compare_runs` 5 種情境:happy path / critical regression / latency 超標 / cost 超標 / empty edge case

### 🚧 Week 5 完成 — Self-learning MVP loop 全綠

```
failed task_trace
    ↓ failure_filter
    ↓ observation_extractor   (LLM 抽 + dedupe)
learning_observations [candidate]
    ↓ verifier               (LLM 獨立 + confidence)
learning_observations [verified | rejected]
    ↓ instinct_consolidator  (cluster ≥3 + avg conf ≥0.80)
learning_instincts [candidate]   ← 人類審
    ↓ ─── (Week 5 新增以下流程) ───
    ↓ candidate_generator    (active + conf≥0.85 + evidence≥3)
prompt_rule_candidates [candidate]
    ↓ test_runner (套用 candidate)
test_runs [candidate run]
    ↓ regression_gate        (4 條 gate)
prompt_rule_candidates [approved | rejected]
    ↓ 人類 merge 進 prompt template

並行:
    resolved task_trace (failed → later completed)
        ↓ resolution_detector
    test_cases [auto-regression, type='regression']
```

Self-learning MVP **5 週 milestone 全部達成**(spec §28 Roadmap)。Week 6(dashboard + promotion workflow UI)留作 follow-up,核心 backend loop 已可運作。

### 📋 CLI 一次跑通

```bash
# 1. 撈 failed traces → 抽 observation
python -m learning.observation_extractor --days 7 --limit 10

# 2. 驗 candidate observation
python -m learning.verifier --limit 20

# 3. 聚合 verified obs → candidate instinct
python -m learning.instinct_consolidator

# 4. resolved failures → regression test_case
python -m learning.resolution_detector --days 30

# 5. active instinct → prompt_rule_candidate
python -m learning.candidate_generator

# 6. 套 candidate 重跑 baseline → 寫 test_run
python test_runner.py --domain tflex   # save 進 test_runs

# 7. Gate(baseline vs candidate run)
python -m learning.regression_gate \
    --candidate-run-id 20260516_1922 \
    --candidate-id PRC-000001 \
    --domain tflex
```

---

## [0.8.9] · 2026-05-16 — v0.8.8 baseline iteration:4 連修(STK-01/02 + STK-03 + T1/T2)

**Patch · 從 v0.8.8 baseline 結果(18/26 = 69%)dig 4 個 case 實際 Phase C code,定位每個 root cause 並批次修。**

### 🔴 v0.8.8 baseline 分析

| Case | Phase C 實際 code 問題 |
|---|---|
| STK-01/02 | Phase C 正確 dedupe + filter,但 `yAxis.max=100` 沒寫(走進 `stacked_raw` block 而非 `stacked_100`) |
| STK-03 | LLM 把 long format Q = `[company_code, review_result, count]` 誤判為 wide,只做 1 series 並 `Q['count'].tolist()` |
| T2 | 第 6 次 `.round()` — `(rate * 100).round(2)` 在 list comp 內,每個 rate 是 scalar |
| T1 | LLM 幻覺 `Q['application_count'].sum()` 但 Q 是 row-level pass-through 含 `is_X` bool flags |

### ✨ P1:`_detect_chart_intent` 同步 intra-bar 邏輯

`llm_service.py · _detect_chart_intent` 跟 v0.8.8 `_detect_preprocess_intent` 對齊。原本 Phase B 已正確走 `stacked_long_pct`(P3),但 Phase C 還是走舊判斷 → 進 `stacked_raw` block 不會寫 `yAxis.max=100`。

```python
intra_bar_proportion = has_intra_bar and has_proportion
if has_stack and (has_100pct or '百分比' in query or intra_bar_proportion):
    return "stacked_100"
```

直接收 STK-01 + STK-02 兩個 case。

### ✨ P2:Phase C rule 0 — 3-col long format 明確偵測

STK-03 LLM 看 Q.cols=`[company_code, review_result, count]` 沒看出來是 long format,寫了 wide-style 單 series。原 rule 0 decision tree 不夠明確,「count」沒 `_xxx` 後綴但 LLM 還是把它當 KPI 欄位。

修 rule 0:加 **「Long / tidy(3-col + multi-series 場景必看)」決定性描述**:
- 只有 1 個 numeric 欄位(`count` / `percentage` / 「裸名」)
- 另外 2 欄是 dim + sub_dim(string)
- sub_dim 欄位裡的值有限(2-10 種 enum)

加判斷口訣 3 條:**numeric 欄位數、string 欄位數、row 數 vs unique(dim) 比例**。

「不確定 long/wide 時,**優先當成 long format**」— filter 不對應的 sub_dim 只會少 series(可控失敗),但 wide 誤認成 long 會炸 KeyError(致命失敗)。

### ✨ P3:`.round()` 第 6 次 — `(expr).round()` + list comp 變種

T2 寫 `[(rate * 100).round(2) for rate in Q["return_rate"].tolist()]`。`rate` 從 `.tolist()` 出來是 Python float,`rate * 100` 還是 Python float,`.round()` 失效。

`embedded_prompts.py · rule 3.5` 加 3 個新反例:
```python
(rate * 100).round(2)                          # ❌ expr 結果是 scalar
[(v * 100).round(2) for v in Q['x'].tolist()]  # ❌ list comp 每元素 scalar
[v.round(2) for v in some_list]                # ❌ 同上
```

正解兩條路:
```python
(Q['rate'] * 100).round(2).tolist()                 # ✅ Series 鏈式
[round(v * 100, 2) for v in Q['rate'].tolist()]     # ✅ list comp 用 builtin
```

`llm_service.py · _format_retry_hint` 對應的 hint 加同樣變種 + 結尾「**禁止寫 `x.round(2)`**」直接命令。

### ✨ P4:dashboard row-level Q 模式 KPI cards 提示

T1 Phase B 走 row-level pass-through 模式(147526 rows,12 cols 含 `is_completed` / `is_returned` / `is_ai_reviewed` / `is_payable` 4 個 bool flag)。但 Phase C 寫 `Q['application_count'].sum()` 假設 aggregated KPI col 存在。

`_PHASE_C_BLOCK_KPI_TABLE` 加 **「dashboard row-level Q 模式偵測」**段:

```
若 q_columns 含 is_X bool flag 欄位 + Q 是 raw row-level(row 數 ≈ raw_df 級),
代表 Phase B 走 row-level pass-through dashboard 模式,
Q.columns **不會有** aggregated KPI 欄位 — 自己用 `.sum()` 算出來。
```

附 ✅ 完整正解 + ❌ T1 baseline 踩雷反例。口訣:**看到 `is_X` bool 欄就用 `Q['is_X'].sum()`,看到 row 數很大就用 `len(Q)`,不要假設 KPI col 已存在**。

### ✅ 驗證(全綠)

- 2 檔 AST OK(embedded_prompts.py 2165 行 / llm_service.py 3328 行)
- `scripts/check_prompt_invariants.py` 17 prompts × 52 sentinels 全綠
- **5 個 _detect_chart_intent 單元測試全綠**:
  - STK-01/02 → `stacked_100` ✓(P1 修)
  - STK-03(無 intra-bar)→ `stacked_raw` ✓(沒誤觸發)
  - 「畫 100% stacked bar」→ `stacked_100` ✓
  - 「占比」單獨 → `stacked_raw` ✓
- 4 個 sentinel 對應 P1-P4 patch 都在

### 📋 預計 baseline 改善

| Case | v0.8.8 | v0.8.9 預期 |
|---|---|---|
| STK-01 | ❌ yAxis.max | ✅ |
| STK-02 | ❌ yAxis.max | ✅ |
| STK-03 | ❌ series=1 + xAxis 重複 | ✅(P2 修)|
| T2 | ❌ `.round()` 第 6 次 | ✅(P3 修)|
| T1 | ❌ 幻覺 KPI col | ✅(P4 修)|

理論上 baseline pass rate **18/26 (69%) → 23/26 (88%)**。

剩下的 fail(STK-04 / Case 08 / STK-07)是更 case-specific 的 Phase B 邏輯 bug,留 v0.8.10 處理。

### 📋 須在 production 套用

⚠️ 若 `PROMPT_REPO_ENABLED=true`:`python migrations/001_seed_prompts.py --force`(Phase C prompt 改了)。
intent 偵測 + retry hint 是純 Python,不需 DB 同步。

---

## [0.8.8] · 2026-05-16 — v0.8.7 baseline iteration:3 連修

**Patch · v0.8.7 baseline 跑完發現新 dominant pattern + 1 個 design conflict,3 連修。**

### 🔴 v0.8.7 baseline 結果觀察

- 16/26 (62%) pass(12 真 pass + 4 refusal 正確拒絕)
- **v0.8.7 P1+P2 命中**:Cases 02/04/06/STK-06(`.round()` 4/5)、STK-04(`in_progress` 幻覺)、STK-03/04/08(long format)
- 新 dominant pattern 浮現:**Phase C 對 aggregated Q 用 raw_df 級欄位 filter**(Cases 03/05 連 hit)

### ✨ P1:Phase C rule 0 強化 — Q 是 post-aggregation 終態

`embedded_prompts.py · _PHASE_C_HEADER_TEMPLATE` rule 0 大幅改寫。原規則只說「欄位名鎖死」太抽象,LLM 還是寫 `Q[Q['review_result']=='Y']`。新版規則點明:

- raw_df 級欄位(`review_status` / `review_result` / `review_mechanism` / `application_no` / `_id` / 任何原始 status / id / code 欄位)在 Phase B **絕大多數情況已被 aggregate 掉,不會出現在 Q.columns**
- 提供「**怎麼判斷 Q 是 long 還是 wide / aggregated**」decision tree:
  - **Aggregated wide** — 含 `_count` / `_rate` / `_sum` / `_avg` / `_pct` 後綴 → 多 series 每個 KPI column 一個,**禁止 filter**
  - **Long / tidy** — 有 dim + sub_dim + value 3 欄結構 → 多 series 用 filter sub_dim 值
- 附 ❌ 3 種錯誤示範(filter / groupby on raw col)+ ✅ 3 種正解(直接用 KPI column)

### ✨ P2:retry feedback 加 raw-col KeyError pattern

`llm_service.py · _format_retry_hint` 新增第 7 種 error 對應(現共 7 pattern):

```
KeyError on review_status / review_result / review_mechanism /
application_no / employee_id / status / _id 等 raw 級欄位
   ↓
🚨 Phase B 已 aggregate;Q 是終態,raw 級欄位幾乎一定不在 Q.columns。
   只用 q_columns 內的 KPI 欄位,**完全禁止 filter Q**。
   附 multi-series stacked bar 正解 code snippet。
```

放在「value-as-column」hint 之後、generic KeyError 之前,優先匹配。

### ✨ P3:100% normalize 觸發詞補強(STK-01/02 design conflict)

baseline 觀察 STK-01/02 query「**每條 bar 中呈現** TST、TSN、TSC 的占比」應該觸發 100% normalize,但 v0.8.7 還是走 raw count stack。原因:`_CHART_100PCT_WORDS` 只認「100%」「百分比堆疊」「占比分佈」「比例分佈」「percentage stack」,user 用「每條 bar 內占比」這類自然語言 phrasing 走不到。

`llm_service.py` 新加 2 個 keyword tuple + intent 邏輯:

```python
_CHART_INTRA_BAR_WORDS = (
    '每條 bar', '每個 bar', '每一條 bar', '每條柱', '每柱', '每根',
    'each bar', 'per bar', 'within each bar', 'inside each bar',
)
_CHART_PROPORTION_WORDS = ('占比', '佔比', '比例', 'proportion', 'share')
```

`_detect_preprocess_intent` 加 `intra_bar_proportion = has_intra_bar and has_proportion` 判斷:
- `stack + (has_100pct OR '百分比' OR intra_bar_proportion)` → `stacked_long_pct`
- 「占比」單獨還是走 `stacked_wide`(維持原 spec 設計)
- 「**每條 bar 內** + **占比/佔比/比例**」**新觸發** `stacked_long_pct`

### ✅ 驗證

- 2 檔 AST OK(embedded_prompts.py 2086 行 / llm_service.py 3315 行)
- `scripts/check_prompt_invariants.py` 17 prompts × 52 sentinels 全綠
- **8 個 _detect_preprocess_intent 單元測試全綠**:
  - STK-01/02 → `stacked_long_pct` ✓(原本走 stacked_wide)
  - STK-03(無 intra-bar 字)→ 維持 `stacked_wide` ✓(沒誤觸發)
  - 「畫 100% stacked bar」→ 維持 `stacked_long_pct` ✓
  - 「占比」單獨 → 維持 `stacked_wide` ✓(spec 設計)
  - 「占比分佈」→ 維持 `stacked_long_pct` ✓
  - 「佔比」(formal char)+「每條 bar」→ `stacked_long_pct` ✓
  - 無 stack 的 ratio 走 `ratio_kpi` ✓

### 📋 預計收掉的 v0.8.7 baseline failure

| Baseline 失敗 | v0.8.8 patch | 預期 |
|---|---|---|
| Cases 03 / 05(raw-col filter on aggregated Q)| P1 + P2 | 2 個 ✅ |
| STK-01 / STK-02(yAxis.max=100 design conflict)| P3 | 2 個 ✅ |

預計 baseline pass rate **62% → 75%+**。

剩下的 fail(T2 .round 第 5 次、T3 / Case 11 / Case 01 Phase B 路徑分歧、STK-05 dim 翻轉、STK-07 follow-up)需要更具體的 case-by-case 分析,留 v0.8.9 處理。

### 📋 須在 production 套用

⚠️ 若 `PROMPT_REPO_ENABLED=true`:`python migrations/001_seed_prompts.py --force`(Phase C prompt 改了)。
retry feedback + intent 偵測是純 Python,不需 DB 同步。

---

## [0.8.7] · 2026-05-16 — Baseline 4 連修 + retry feedback 升級

**Patch · 從 baseline 全程觀察找到 4 個 systematic bug 一起修。**

### 🔴 Baseline 觀察(20+ case)

| Pattern | Phase | 次數 |
|---|---|---|
| `.round()` 對 scalar 失效 | C | **5×** |
| xAxis 沒 dedupe(long format) | C | **3×** |
| Phase B 弄丟維度欄位 | B | 1× (T3) |
| LLM 把 long-format value 當 column | C | 1× (STK-07) |
| str/numeric divide | B | 1× (Case 01) |
| bracket / tuple-comp syntax | C | 2× |

**共同病因**:retry feedback 不夠具體,LLM 看到 error 3 attempts 同錯。

### ✨ P1:Phase C rule 3.5 — `.round()` API 用法澄清

`_PHASE_C_HEADER_TEMPLATE` rule 3.5 改寫。原規則只說「必須 `.round(N)`」沒講清楚對什麼物件 call。LLM 錯把 scalar 也用 `.round()`,結果踩雷 5 次。

新規則明確區分:

```
Series / DataFrame → .round(N)
scalar (Python float / numpy.float / str) → round(x, N) builtin

✅ Q['col'].round(2).tolist()         # Series 鏈式
✅ round(Q['col'].iloc[0], 2)         # scalar 用 builtin
✅ [round(v, 2) for v in raw_list]    # list 元素是 scalar

❌ value.round(2)                      # AttributeError
❌ Q['rate'].iloc[0].round(2)          # iloc[0] 可能回 Python float
❌ min(Q['rate']).round(2)             # Python min() 返純 float
```

### ✨ P2:Phase C rule 3.2 — Long format → ECharts 完整 example

3 個 STK case 連續中相同的雷:LLM `Q['<dim>'].tolist()` 直接塞 xAxis,沒 dedupe。`series.data` 長度跟 xAxis 對不上,整張圖壞。

新加 rule 3.2 含**完整 code snippet**示範:

```python
x_data = Q['company_code'].unique().tolist()       # ✅ dedupe!
series = []
for cat in Q['category'].unique():
    per_company = (
        Q[Q['category'] == cat]
          .set_index('company_code')['count']
          .reindex(x_data).fillna(0).tolist()
    )
    series.append({"name": str(cat), "type": "bar",
                    "stack": "total",
                    "data": [int(v) for v in per_company]})
```

同時點出常見的 **「value 當 column」誤用**(`Q['PAY']` → KeyError 因為 PAY 是 review_result 欄位的值)。

### ✨ P3:retry feedback error→hint mapping 升級

`llm_service.py · _format_retry_hint` 新增 5 個 error pattern 對應(原本只有 2 個 — `ModuleNotFoundError` 跟 `KeyError`):

| Error 模式 | Fix hint |
|---|---|
| `'X' object has no attribute 'round'` | 用 `round(value, N)` builtin |
| `KeyError: '<value-str>'`(PAY/RTN/...)| Long format value 不是 column,用 `Q[Q['<col>']=='<val>']` filter row |
| `KeyError`(generic)| 加碼提醒:groupby 後保留維度級欄位用 `agg(col=('col', 'first'))` |
| `TypeError: rtruediv ... str` | 比率 KPI 走 boolean flag(`Q['is_X'] = (Q['col']=='val')` → sum → divide)|
| `SyntaxError: does not match opening parenthesis` / `EOF while` / `forget parentheses around comprehension target` | bracket 配對 / tuple-in-comp 加括號 / 拆多步 |
| `AttributeError`(generic)| 檢查變數型別,scalar 用 builtin,Series 用 method |

baseline 觀察 LLM 在 3 retries 重複同錯,具體 fix hint 預期顯著降低 retry-fail 比例。

### ✨ P5:Phase B rule 6.5 — groupby 後保留維度級欄位

T3 case `KeyError: 'hc'`:Phase B groupby('company_code') 後 hc 消失,LLM 後面引用就炸。

新加 rule 6.5 + ✅/❌ 範例:

```python
agg = Q.groupby('company_code').agg(
    total_count=('application_no', 'size'),  # 新算
    hc=('hc', 'first'),                       # ✅ 維度級 — 帶上!
).reset_index()
```

### ✅ 驗證

- 2 檔 AST OK(embedded_prompts.py 2016 行 / llm_service.py 3276 行)
- `scripts/check_prompt_invariants.py` 17 prompts × 52 sentinels 全綠
- 5 個 unit assertion 綠:
  - P1: `round(value, 2)` builtin 範例 + 「對 scalar 呼叫」警告 in Phase C header
  - P2: `Long format Q → ECharts 鐵律` + `unique().tolist()` + `reindex(x_data)` example
  - P3: retry hint 含 round / rtruediv / bracket / tuple-comp 共 5 個新 pattern
  - P5: `groupby 後保留維度級欄位` + `hc=('hc', 'first')` example

### 📋 預計收掉的 baseline failure

| Baseline 失敗 | v0.8.7 patch | 預期 |
|---|---|---|
| Case 02/04/06/STK-06/T2(.round 系列)| P1 + P3 | 4-5 個 ✅ |
| STK-02/03/05(xAxis dedupe)| P2 + P3 | 2-3 個 ✅ |
| Case 01(str/num divide)| P3 | retry 可能救回 |
| STK-01(bracket mismatch)| P3 | retry 可能救回 |
| STK-04(`in_progress` 幻覺)| 既有 KeyError hint | 部分救 |
| STK-07(value-as-col + dup row)| P2 + P3 | 部分救 |
| T3(`hc` 弄丟)| P5 + P3 | ✅ |

預計總體 baseline pass rate **明顯提升**(理論最高從 ~50% 拉到 ~80%+)。

### 📋 須在 production 套用

⚠️ 若 `PROMPT_REPO_ENABLED=true`,需重 seed:

```bash
python migrations/001_seed_prompts.py --force  # Phase B + Phase C prompt 都改了
```

retry feedback 是純 Python code,不需 DB 同步。

---

## [0.8.6] · 2026-05-16 — Hotfix:sync embedded_test_cases.py

**已含於 v0.8.6**:test framework 自己的 bug,v0.7.3 / v0.7.4 / v0.8.3 D4 的 test case 改動沒同步進 embedded_test_cases.py(test_runner 實際讀的源頭)。補同步 Case 03 PAY/RTN synonym、Case 09 q_numeric_must_vary、Case 10 return_count synonym。

---

## [0.8.5] · 2026-05-16 — Self-Learning MVP Week 4:Consolidator + Contradiction

**Patch · self-learning MVP Week 4 D1+D2 完成。**

對齊 `GenBI_v1.3_Self_Learning_MVP_Implementation_Spec.md` §14 + §15 + §15.5。

### ✨ `learning/instinct_consolidator.py`(618 行)

兩個獨立但相關的 maintenance job:

#### `consolidate_instincts(db, ...)`

把語意相似的 verified observation 聚合成 candidate instinct。

**Algorithm**:

1. 撈所有 `status='verified'` 的 observation
2. 依 `phase` 分組(不同 phase 一定不聚)
3. 組內依 `recommendation` 跑貪婪 Jaccard cluster(threshold 0.45)
4. cluster size ≥ 3 且 avg confidence ≥ 0.80 → 建 instinct
5. 取 cluster 內 confidence 最高的 obs 的 recommendation 當 canonical rule
6. **status='candidate'**(人類審後在 dashboard 改 `active`)
7. **Idempotent**:同 supporting_observation_ids signature 已建 → skip

**新 instinct 欄位**(對齊 §7.3):

| 欄位 | 來源 |
|---|---|
| instinct_id | `INST-AUTO-NNNNN`(避免撞 seed `INST-SEED-NNN`) |
| name | `consolidated_inst_auto_nnnnn` |
| rule | cluster 內最高 conf 那筆的 recommendation |
| domain | CLI 帶入(default `tflex`) |
| phase | cluster 共同 phase |
| tags | cluster tag union(去重保留順序,cap 10) |
| confidence | cluster avg verifier_confidence |
| evidence_count | cluster size |
| supporting_observation_ids | cluster 所有 obs_id |
| source | `consolidated` |
| status | `candidate` |

#### `detect_contradictions(db, ...)`

掃 verified observations vs active instincts,找潛在矛盾 + auto-degrade。

**命中規則**(全部成立才算 contradicting):

1. 同 phase
2. tags 有交集
3. Jaccard(obs.cause+rec, inst.rule) ≥ 0.50
4. **Negation 詞 presence 不同**(一邊有「禁止/不要/forbid/avoid/not」,另一邊沒有 — 立場相反)

**Negation 啟發式**:

- 英文 token-level:`not / no / never / avoid / forbid / disallow / forbidden / dont`
- 中文 substring:`不要 / 不可 / 不准 / 不能 / 禁止 / 避免 / 勿 / 別`
- 「不」單字太常見(可能是「不過」「不一定」),沒加

**命中動作**:

1. `instinct.contradiction_count++`
2. `confidence -= 0.05`(spec §15.2)
3. `confidence < 0.60` → `status='deprecated'`(spec §15.3)
4. 寫一筆 `learning_jobs` notification(`job_type='contradiction_review'`,`status='needs_review'`)讓人類在 dashboard 審
5. `applied_contradiction_obs_ids` 加進 instinct,**idempotent**(同 obs 不重複扣)

**為什麼 auto-degrade 但寫 notification**?
> 啟發式必有 false positive。auto 扣分讓系統反應快,寫 notification 讓人類能複查;真誤判時 dashboard 上可手動 revert。

#### CLI

```bash
# 跑 consolidation + contradiction(default)
python -m learning.instinct_consolidator

# Dry run 看會做什麼
python -m learning.instinct_consolidator --dry-run

# 只跑其中一項
python -m learning.instinct_consolidator --skip-consolidation
python -m learning.instinct_consolidator --skip-contradiction

# 換 cluster threshold(預設 3 obs + avg conf 0.80)
python -m learning.instinct_consolidator --min-observations 5 --min-avg-confidence 0.85
```

### ✅ 驗證

- AST OK,618 行
- 10 個 unit check 全綠:
  - `_has_negation` EN token / ZH substring / mixed / 非 str 防呆
  - `_cluster_obs_by_similarity` 3 個近似 paraphrase → 1 cluster + 1 個分離
  - 3 個 diverse rec → 3 cluster(no false union)
  - `_build_instinct_doc` 正確取最高 conf 代表 / 合併 tag / 算 avg / `status='candidate'` / `source='consolidated'`
  - `_is_contradicting` 4 種 matrix(正命中 / 跨 phase / 低 sim / 同 negation side / 同 positive side)

### 🚧 Week 4 完成 → MVP loop 全綠

```
failed task_trace
    ↓ failure_filter
    ↓ observation_extractor   (LLM 抽 + dedupe)
learning_observations [candidate]
    ↓ verifier               (LLM 獨立 + confidence)
learning_observations [verified | rejected | candidate-revise]
    ↓ instinct_consolidator  (cluster ≥3 + avg conf ≥0.80)
learning_instincts [candidate]   ← 人類在 dashboard 改 active
    ↓ detect_contradictions  (掃 verified obs vs active instincts)
    ↓ auto-degrade + notification
learning_instincts [deprecated 或 lower conf]
```

Next:Week 5 — failure-to-test 自動轉換 + prompt rule candidate 產生 + regression gate。

---

## [0.8.4] · 2026-05-16 — Self-Learning MVP Week 3:Verifier + Confidence

**Patch · self-learning MVP Week 3 D1+D2 完成。**

對齊 `GenBI_v1.3_Self_Learning_MVP_Implementation_Spec.md` §12 + §13 + §13.5。

### ✨ D1:`learning/confidence.py`(299 行)

對齊 spec §13 的 4 sub-component composite。Pure Python,**沒 LLM、沒 DB 也能跑**(consistency / novelty 在 db=None 時保守給 1.0,代表「沒撞既有」)。

```python
confidence =
    0.40 * evidence_support
  + 0.30 * specificity
  + 0.20 * consistency
  + 0.10 * novelty
```

**Sub-components**:

| Component | 範圍 | 計算 |
|---|---|---|
| evidence_support | 0–1 | `min(trace_quotes_count / 3.0, 1.0)` |
| specificity | 0–1 | 0.5 if 含 column name / operator (snake_case identifier、反引號、`Q['col']`、ALL_CAPS enum、`==`/`!=`...);+0.3 if 含數字閾值;+0.2 if 含 "if X" / "add rule" / "必須/禁止" 等可測試語言 |
| consistency | 0–1 | 查 learning_observations 同 phase+tags 有交集者,Jaccard token 相似度 ≥ 0.4 算「相似」,≥ 0.7 算「同意」,回 agree / similar |
| novelty | 0–1 | `1 - max(Jaccard(obs vs each active instinct))`(spec 寫 cosine,MVP 沒 embedding model 用 Jaccard 替代) |

**對外 API**:

```python
from learning.confidence import compute_confidence

scores = compute_confidence(
    observation,        # 含 phase / tags / cause / recommendation
    trace_quotes_count=3,
    db=db,              # 可 None
)
# {confidence, evidence_support, specificity, consistency, novelty}
```

### ✨ D2:`learning/verifier.py`(485 行)

對齊 spec §12 Verifier Agent + §7.2 verifier_results schema。

**核心 API**:

```python
from learning.verifier import verify_observation, run_verification

# 單筆 verify(LLM 跑判決 + confidence 算 numeric score)
result = verify_observation(observation, trace, llm_service, db=db)

# 批次(撈 candidate observations 一次驗)
stats = run_verification(db, llm_service, run_id=None, limit=10)
```

**Verifier LLM prompt**(獨立於 extractor)— 給 observation + trace digest,要求回 JSON:

```json
{
  "decision": "accept|revise|reject",
  "reasoning": "<one paragraph, refer to trace evidence>",
  "issues": ["<concrete problems>"],
  "trace_quotes_count": <0..10 — how many distinct trace pieces support cause>
}
```

**Final decision matrix**(綜合 LLM 判 + numeric confidence):

| LLM decision | confidence | Final |
|---|---|---|
| reject | * | **reject** |
| * | < 0.60 | **reject** |
| revise | ≥ 0.60 | **revise** |
| accept | 0.60–0.74 | **revise** |
| accept | ≥ 0.75 | **accept** |

**狀態同步**(verifier 跑完後):

- `accept` → observation.status = `verified`
- `reject` → observation.status = `rejected`
- `revise` → 保持 `candidate`,但 verifier_results 已寫,dashboard 看得到 issues

**`verifier_results` schema**(對齊 spec §7.2):

| 欄位 | 來源 |
|---|---|
| observation_id | obs.observation_id |
| decision | accept / revise / reject |
| confidence | composite 0–1 |
| reasoning | LLM 一段話 |
| issues | LLM 找到的具體問題 list |
| sub_scores | 4 個 component 分數(額外存,讓 dashboard 拆解) |
| llm_decision | LLM 原始判決(在 final 之前) |
| llm_trace_quotes_count | LLM 自報引了幾條 trace evidence |
| created_at | UTC now |

**CLI**:

```bash
python -m learning.verifier --limit 5 --dry-run         # 跑 LLM 不寫 DB
python -m learning.verifier --limit 10                  # 寫 verifier_results + 更新 obs.status
python -m learning.verifier --run-id <uuid> --limit 5   # 只驗某批 extraction
```

### ✅ 驗證

- 2 個檔 AST OK(confidence.py 299 行 / verifier.py 485 行)
- `compute_specificity` 8 種 edge case 全綠(vague / col-only / col+num / full spec / underscore-prefix `_pct` 都正確判別)
- `compute_confidence` composite 上下界正確(全綠 → 1.0,空 obs → 0.30)
- `_normalize_decision` 9 種輸入(accept/Accept/approved/reject/rejected/revise/revision/garbage/None/int)正確收斂
- `_final_decision` 10 種 decision matrix 全綠(含 boundary `0.75/0.74/0.60/0.59`)

### 🚧 Week 3 完成 → Week 4 啟動條件

✅ End-to-end self-learning loop 第一版可跑了:

```
failed task_trace
    ↓ failure_filter
    ↓ observation_extractor  (LLM 抽 5+1 欄位)
    ↓ dedupe_key             (sha256 防重)
learning_observations (status=candidate)
    ↓ verifier              (LLM 獨立判 + confidence 4 sub-component)
    ↓ status: verified / rejected / candidate(revise)
verifier_results
```

Next:Week 4 — `instinct_consolidator` + contradiction handling。

---

## [0.8.3] · 2026-05-16 — Case 09 4 連修:Plan/Phase A/Cheatsheet/test_runner

**Patch · 從 Case 09 baseline 找到 4 個獨立 bug,一起修。**

### 🔴 Baseline 觀察(Case 09:AI 審查率 vs 退單率散點圖)

- Phase B attempt 1 炸 `SyntaxError: invalid character '，' (U+FF0C)`(LLM 寫全形逗號)
- Phase B attempt 2 炸 `KeyError: 'review_status'`(Phase A 漏撈)
- Phase B attempt 3 跑過了,但 `average_return_rate` **全 15 家公司都是 0.0**(退化公式) — 最危險的 silent failure

### ✨ D1:Plan prompt 加「需要的原始欄位」明列段

`embedded_prompts.py · _PHASE_0_PLAN_TEMPLATE` A 段加一條 bullet,要求 LLM 在 Plan 階段就列出 `raw_columns_needed: [col_a, col_b, ...]`,Phase A 才能正確 $project。

特別點名「狀態欄位(review_status/status/state)與其相依的子欄位(result/mechanism)必須一起列出,漏一個就會讓 Phase B 算出全 0 退化結果」。

### ✨ D2:Phase A 加 `column_clusters` 同生共死機制

**Metadata 層**:`tflex_task_metadata_agent_v3.py` 在 `data_preprocessing_guidance` 加 `column_clusters` 區塊。tflex 首批宣告 `review_state` cluster = `[review_status, review_result, review_mechanism]`。

**注入層**:`llm_service.py` 新加 `_build_column_clusters_block(metadata)`,`build_domain_knowledge` 把它組進 Domain Knowledge 文字。**Metadata 沒定義 column_clusters 時,section 不顯示,零侵入。**

**Prompt 層**:`_PHASE_A_PIPELINE_TEMPLATE` 加 **Rule 6.5「欄位 cluster 同生共死(CRITICAL FATAL)」**:若 `$project` 引用 cluster 內任一欄位,**必須**把整 cluster 所有欄位一起 $project。

口訣:**「cluster 一動就要全動」**。

### ✨ D3:`PANDAS_ANTIPATTERN_CHEATSHEET` 加全形標點黑名單

加一條新 anti-pattern:**程式碼內所有標點必須全部 ASCII 半形**,禁止 `，` (U+FF0C) / `；` (U+FF1B) / `（` (U+FF08) / `）` (U+FF09) / `：` (U+FF1A) 等全形字元。

Comment / docstring 內的中文標點 OK;只有 code 結構字元必須 ASCII。

### ✨ D4:`test_runner.py` 加 `q_numeric_must_vary` 檢查

抓「跑得起來但答錯」silent failure。新 case field:

```python
"q_numeric_must_vary": [
    ["ai_review_rate", "ai_rate", "AI 審查率"],          # synonym list
    ["average_return_rate", "return_rate", "退單率"],
]
```

對每個欄位檢查 `Q[col].nunique() > 1`,可同時抓:

- 全 0(常見:狀態欄位漏撈 → Phase B 退化公式)
- 全 NaN(常見:filter 全濾掉)
- 全同值(常見:groupby 維度錯)

訊息會點明退化原因(全 0 / 全 NaN / 全部相同),debug 直覺。

Case 09 首批加上;其他 rate KPI case 可按需追加。

### ✅ 驗證(全綠)

- 4 個檔 AST OK(embedded_prompts.py / llm_service.py / test_runner.py / tflex_task_metadata_agent_v3.py)
- `scripts/check_prompt_invariants.py` 17 prompts · 52 sentinels 全過(D1/D2 改動沒破壞 critical rule)
- 8 個 unit check 綠:
  - column_clusters 從 metadata 正確讀出
  - `_build_column_clusters_block` 對 tflex / 空 metadata 都行為正確
  - `build_domain_knowledge` 含 cluster section
  - test_runner 4 種退化情境(全 0 / 全 NaN / 全同 / 有變異)都正確判別
  - Plan prompt 含 `raw_columns_needed` + 「需要的原始欄位」
  - Phase A prompt 含 cluster 鐵律 + `review_mechanism` 例子
  - Cheatsheet 含 `U+FF0C` + 「全形」

### 📋 須在 production 套用

⚠️ 若 `PROMPT_REPO_ENABLED=true`,需重 seed:

```bash
python migrations/001_seed_prompts.py --force        # Plan + Phase A 改了
python migrations/002_seed_metadata.py --force --include-test-fixtures   # metadata 加 column_clusters
```

D3 (cheatsheet) 跟 D4 (test_runner) 是純 code 層,沒有 DB 同步動作。

---

## [0.8.2] · 2026-05-16 — Self-Learning MVP Week 2:Observation Extractor + Dedupe

**Patch · self-learning MVP Week 2 D1+D2 完成。**

對齊 `GenBI_v1.3_Self_Learning_MVP_Implementation_Spec.md` §10–§11。

### ✨ `learning/observation_extractor.py`(669 行)

把一個 failed `task_trace` 用 LLM 抽成 5+1 欄位的 structured observation,寫進 `learning_observations` collection,並用 `dedupe_key` 防重。

**核心 API**:

```python
from learning.observation_extractor import (
    extract_observation,            # 單一 trace → observation
    run_observation_extraction,     # 批次:撈 → 抽 → dedupe → 寫
)

# 一鍵跑(會自動串 failure_filter + LLM + dedupe + 寫 learning_jobs record)
stats = run_observation_extraction(
    db, llm_service,
    since_days=7, limit=5,
    statuses=("failed", "refused"),
    dry_run=False,
)
# stats = {run_id, input_count, extracted, rejected, deduped, errors, observations}
```

**Observation schema(寫進 `learning_observations`)**:

| 欄位 | 來源 | 說明 |
|---|---|---|
| `observation_id` | `OBS-NNNNNN` auto | unique |
| `run_id` | uuid | 一批 extraction 共用,讓 `learning_jobs` 對得起來 |
| `source_trace_id` | trace doc | 反查原 trace |
| `query_hash` | sha256(query) | 跨 trace 找同 query 用 |
| `phase` | LLM tag 推斷 → step error fallback | phase_a/b/c/d/0/meta |
| `context / action / result / cause / recommendation` | LLM | 5 個 required field |
| `tags` | LLM | 最多 5 個 snake_case,優先匹 controlled vocab |
| `dedupe_key` | sha256(phase ‖ cause ‖ recommendation) | unique index 擋重複 |
| `status` | `candidate / rejected` | 通過 validation 才 candidate |
| `created_at` | UTC now | |

**Rejection rules**(對齊 spec §10.3):

1. 任一 required field 缺或空字串 → `missing_field:<name>`
2. `cause` < 15 chars → `cause_too_short`(防「LLM made a mistake」這種敷衍)
3. `recommendation` < 25 chars → `recommendation_too_short`
4. `recommendation` 含 generic 字眼(如「improve the prompt」、「fix the bug」)且 < 80 chars → `recommendation_generic:<phrase>`
5. dedupe_key 撞既有(unique index 觸發 DuplicateKeyError)→ `deduped`(stats 計數,不算 reject)

**Strict JSON 解析**:

- LLM system prompt 規定回 strict JSON only(6 個 key:5 fields + tags)
- 沿用 v0.3.6 `extract_json_block` balanced-brace parser,容忍 preamble / markdown fence
- JSON parse 失敗 → `extraction_error='json_parse_failed: ...'`,計入 `errors`

**Trace digest**(送進 LLM 的 context):

- query / status / intent_chart / intent_preprocess / trace.error
- 每個 step 的 phase + kind + elapsed + error
- error step + 最後 step 多塞 LLM payload(user msg 尾段 400 字、response 500 字)
- 總長度 cap 8000 chars(控成本,單筆 trace 可能 100KB+)

**Cost control**(spec §22):

- default `limit=5`(每天建議 ≤ 50)
- LLM 呼叫沿用 `LLMService._call_llm`,自動被 task_trace recorder hook 到(若有 set)
- `--dry-run` 跑 LLM 但不寫 DB,可預覽抽出來會長什麼樣

**`learning_jobs` record**(讓 dashboard 看跑了什麼):

每次 `run_observation_extraction` 結尾寫一筆 `job_type='observation_extraction'`,含 input/output/rejected/deduped/error count + run_id + params。

### ✅ 驗證

- AST OK,669 行
- 8 個 unit check 全綠:
  - `_compute_dedupe_key` 大小寫 + 多空白 normalize 一致
  - `_validate_observation` happy path / missing field / generic / short cause 都正確
  - `_normalize_tags` cap 5 個 + lowercase
  - `_build_trace_digest` 不 crash + 含 error 訊息

### 📋 用法

```bash
# Dry run(撈 7 天內 failed trace,LLM 抽但不寫 DB)
python -m learning.observation_extractor --days 7 --limit 3 --dry-run

# 實際跑(寫 learning_observations + learning_jobs)
python -m learning.observation_extractor --days 7 --limit 5
```

### 🚧 Next:Week 3 啟動

- `learning/verifier.py`:獨立 LLM agent 驗 candidate observation(accept/revise/reject)
- `learning/confidence.py`:4 個 sub-component(evidence/specificity/consistency/novelty)
- Decision rule:`confidence >= 0.75 AND not duplicate AND actionable` → 升 verified

---

## [0.8.1] · 2026-05-16 — Self-Learning MVP Week 1 D2+D3:Collections + Failure Filter

**Patch · 完成 self-learning MVP Week 1。**

對齊 `GenBI_v1.3_Self_Learning_MVP_Implementation_Spec.md` §7 + §9。

### ✨ Week 1 D2:其他 4 個 learning_* collections + indexes

**`migrations/005_create_learning_collections.py`**:idempotent migration,建好 4 個 collection + 共 19 個 index。

| Collection | Indexes |
|---|---|
| `learning_observations` | 7(含 `observation_id` unique、`dedupe_key` unique、`phase + status` 複合) |
| `verifier_results` | 3(observation_id、decision、created_at desc) |
| `learning_jobs` | 4(job_id unique、status、job_type、started_at desc) |
| `prompt_rule_candidates` | 5(candidate_id unique、status、instinct_id、target_component、created_at desc) |

設計細節:
- collection 已存在 → skip(不破壞既有 data)
- index 已存在 → skip(`list_indexes()` pre-check)
- `--dry-run` 預覽不寫
- 跟 migration 001-004 同風格,可由 admin CLI 呼叫

### ✨ Week 1 D3:`learning/failure_filter.py`

從 `task_traces` 撈出需要做 observation extraction 的 trace。

**API**:

```python
from learning.failure_filter import get_failed_traces, get_trace_by_id

# 撈最近 7 天 failed/refused 的 trace summary
traces = get_failed_traces(
    db,
    since_days=7,
    statuses=("failed", "refused"),
    include_step_errors=True,    # 任一 step 有 error 的也算
    include_manual_flag=True,    # needs_review=True 也算
    limit=200,
)

# 二次撈完整 trace(含 messages + response)給下游 extractor
full = get_trace_by_id(db, "uuid-...")
```

**設計重點**:
- summary 不撈 messages payload(可能 50KB+/trace),只撈 phase/error 等 metadata。caller 視需要用 trace_id 二次撈。
- `$or` 三條件:status 命中 ∪ step error ∪ 手動 flag。任一即返回。
- 時間窗口 `started_at >= now - since_days`,避免撈舊資料。
- CLI:`python -m learning.failure_filter --days 7 --limit 20`。

### ✅ 驗證

- 兩個檔 syntax OK
- `COLLECTION_SPECS` = 4 collection,19 個 index 設定
- `_summarize_trace` synthetic test 正確抽出 has_step_error / failure_reason
- `get_failed_traces` 查詢結構正確(3 個 $or clauses)
- 兩個 CLI `--help` 都正常

### 🚧 Week 1 完成 → Week 2 啟動條件

✅ Week 1 三天 deliverable 全完成:
- D1:bootstrap layer + 13 historical seeds(v0.8.0)
- D2:其他 4 個 collections + indexes(v0.8.1)
- D3:failure_filter(v0.8.1)

REPL 應可跑通:

```python
from pymongo import MongoClient
import config
from learning.bootstrap import seed_all
from learning.failure_filter import get_failed_traces

db = MongoClient(config.MONGO_URI)[config.MONGO_DB]

# 1. seed historical instincts(D1)
seed_all(db)  # 13 inserted

# 2. failed traces 可被撈(D3)
failures = get_failed_traces(db, since_days=7)
print(f"Found {len(failures)} failed traces")

# 3. collections 都建好(D2)
print(db.list_collection_names())  # 應含 learning_observations 等 4 個
```

Week 2 啟動:`observation_extractor.py` + dedupe 邏輯。

---

## [0.8.0] · 2026-05-16 — Self-Learning MVP Week 1 D1:Bootstrap Layer

**Minor · 開啟 self-learning MVP 第一條 milestone。**

對齊 `GenBI_v1.3_Self_Learning_MVP_Implementation_Spec.md` §8.1 Historical Seed Rules。

### ✨ 新增

- **`learning/` package**:Self-learning 模組根目錄,含 `__init__.py` 標明後續 8 個 module 的職責(failure_filter / observation_extractor / verifier / confidence / instinct_consolidator / candidate_generator / promotion_gate / dashboard_metrics 待 Week 2-6 補)。
- **`learning/bootstrap.py`**:
  - `HISTORICAL_SEEDS` list:13 條對齊 GenBI v0.3.x-v0.7.x hotfix 的 instinct seeds,涵蓋 Phase A (3) / Phase B (3) / Phase C (5) / Phase 0 (1) / Meta (1)。
  - 每條 seed 含:`instinct_id` / `name` / `rule` / `phase` / `error_class` / `tags` / `implementation` 實際 GenBI code 引用 / `version_source` 來源 hotfix。
  - `seed_all(db, dry_run, verbose)` idempotent upsert function:已存在的 `historical_seed` 記錄覆蓋更新,production-modified 記錄保留(由 `source` 欄判斷)。
  - `_ensure_indexes(db)`:確保 4 個 index(instinct_id unique / status / domain / phase / tags)。
  - CLI:`python -m learning.bootstrap [--dry-run] [--skip-indexes]`。
- **`migrations/004_bootstrap_learning_instincts.py`**:跟既有 001-003 migration 一致風格,wrap `learning.bootstrap.seed_all` 並加 verification 步驟。

### 📊 13 條 seed 對照表(對齊 codebase 真實實作)

| ID | Name | Phase | Implementation |
|---|---|---|---|
| 001 | strip_derived_expressions | A | `sanitize_pipeline()` |
| 002 | defensive_json_extraction | A | `extract_json_block()` |
| 003 | phase_a_retry_with_error_feedback | A | `generate_pipeline()` retry |
| 004 | series_to_dataframe_safety_net | B | Series safety net |
| 005 | forbid_import_in_phase_b | B | Phase B prompt rule 1 (v0.7.1) |
| 006 | forbid_phase_b_replay_raw_df | B | Phase B prompt rule 3.1 |
| 007 | coerce_numpy_to_native | C | `coerce_option_native_types()` |
| 008 | rescue_empty_echarts | C | `rescue_empty_echarts()` |
| 009 | rescue_in_except_path | C | except path |
| 010 | dual_axis_force_route | C | rule 5.9 |
| 011 | forbid_empty_shell_dynamic_fill | C | rule 3.1 |
| 012 | chart_word_not_refuse_trigger | 0 | Phase 0 Step 1 |
| 013 | prompt_invariants_enforcement | meta | `check_prompt_invariants.py` |

### ✅ 驗證

- syntax OK(3 個 file 全 py_compile pass)
- HISTORICAL_SEEDS = 13 條,instinct_id / name 都 unique
- Phase 分布:phase_a=3, phase_b=3, phase_c=5, phase_0=1, meta=1
- CLI `--help` 正確顯示
- `seed_all(MockDB, dry_run=True)` 回 `{'inserted': 13, 'updated': 0, 'skipped': 0, 'total': 13}`

### 🚧 後續(Week 1 D2 / D3)

- Week 1 D2:其他 4 個 MongoDB collection schema + indexes(`learning_observations` / `verifier_results` / `learning_jobs` / `prompt_rule_candidates`)
- Week 1 D3:`learning/failure_filter.py` 從 `task_traces` 撈 failed runs

---

## [0.7.4] · 2026-05-16 — test_runner echarts_required_keys 改 chart-type aware

**Patch · 修 Case 07 false fail(LLM 選 pie chart 但 test 要求 xAxis/yAxis)。**

### 🐛 修正

實際 case:Case 07「四個福利申請類別,哪個最熱門?」LLM 合理選 pie chart,但 test 設定 `echarts_required_keys = ['title', 'xAxis', 'yAxis', 'series']` 對 pie 不適用(pie 沒 axis),導致 false fail。

**修法**(`test_runner.py`):
- 從 `option['series'][0].get('type')` 偵測 chart type
- 若是 axis-less type(`pie` / `radar` / `treemap` / `sunburst` / `gauge` / `funnel`),自動從 `required_keys` 排除 `xAxis` / `yAxis`
- 其他 chart type(bar / heatmap / line)仍嚴格 check
- 顯示「(chart=pie,xAxis/yAxis 不適用)」標註,讓 log 一眼看出來

### ✅ 驗證

6 個 logic test 全綠:
- pie chart(no axis)→ pass
- bar chart(needs axis)→ pass
- bar missing xAxis → fail(預期)
- heatmap → 仍 strict check
- radar(axis-less)→ pass
- empty series(type 不明)→ fallback strict check

---

## [0.7.3] · 2026-05-16 — test_runner expected_q_cols_all 加 synonym list 支援

**Patch · 修 Case 03 false fail(user 字眼 vs canonical column name 不一致)。**

### 🐛 修正

實際 case:Case 03「畫出各公司的 PAY 與 RTN 申請數量,我想看哪家公司退件量最大」LLM 忠於 user 字眼產出 `PAY` / `RTN` 欄位,但 test 期待 `pay_count` / `return_count` → false fail。LLM 行為其實合理(BI 場景該忠於 user vocabulary)。

**修法**(`test_runner.py`):
- `expected_q_cols_all` 內每個項目可以是:
  - `str`(literal 比對)
  - `list` / `tuple`(any-of synonym list,任一命中即通過)
- 失敗訊息顯示「(pay_count | PAY | pay)」這種 OR 格式,debug 一眼看出來
- 向後相容:其他 string-only case 行為不變

**修了哪些 case**:
- Case 03:`[["pay_count", "PAY", "pay"], ["return_count", "RTN", "rtn", "RET"], "company_code"]`
- Case 10(預防性):`["company_code", ["return_count", "退件數", "退件數量", "RTN", "rtn", "rtn_count", "ret_count"]]`

### ✅ 驗證

6 個 logic test 全綠(含正例 / 反例 / 缺欄位 / 中文 synonym / 向後相容 4 種 case)。

---

## [0.7.2] · 2026-05-15 — Sentinel-based prompt invariants check

**Patch · 防止 v0.7.1 那種「重構漏接 critical rule」未來再發生。**

### 🎯 動機

v0.6.0 重構 Phase B 為 modular 時,universal header rule 1 漏掉「禁止 import」這條關鍵 rule,導致 Case 03 連續 3 次失敗。Code review 沒抓到。需要一個自動化機制來保證:**所有歷次 hotfix 沉澱下來的 critical rules 都還在,沒有被後續重構漏掉。**

### ✨ 新檔 `scripts/check_prompt_invariants.py`

Sentinel-based 不變式檢查:
- 對每個 phase prompt + intent 變體(共 **17 prompts**)
- 檢查 **52 個 sentinels**(critical 字眼,缺一即視為 regression)
- 用 `or` 邏輯(多個變體任一命中即過,避免綁死特定措辭)

### 涵蓋的 invariants

**Phase 0**:拒絕協定 / REFUSE 格式 / 圖型詞鐵律(v0.4.3)/ 最後檢查 / 三步推理
**Phase A**:禁 `$group`/`$cond` / `$project` 鐵律 / Entity 過濾 / 「Phase A 撈,Phase B 算」口訣
**Phase B universal**:Q 變數產出 / 禁 `print` / **禁 `import`**(v0.7.1) / 禁 self-merge / Series.first 禁區
**Phase B 5 個 intent blocks**:row-level pass-through / transform / weighted avg / to_datetime / groupby+agg
**Phase C universal**:option 變數 / formatter 限制 / 空殼禁(v0.4.7)/ numpy cast(v0.4.6)/ 色盤
**Phase C 7 個 intent blocks**:pie / stacked_100(max=100)/ stacked_raw(pivot)/ line_dual(yAxisIndex)/ heatmap(3 雷)/ horizontal / kpi_table
**Phase D**:KPI definitions / data_limitations / Markdown 格式

### ✅ 初次跑結果

```
Total: 17 prompts, 52 sentinels
Passed: 52 / 52
Failed: 0
```

所有歷史 hotfix 累積的 critical rules 都在(包含剛補回的 v0.7.1 禁 import)。

### 🚧 後續整合(留 v0.7.3+)

- 加進 smoke 流程(以後 CI 自動跑)
- pre-commit hook(commit 時自動 check)
- 加新 hotfix 時順手加新 sentinel(防止 hotfix 補的 rule 之後又被砍)

---

## [0.7.1] · 2026-05-15 — Phase B 禁 import 缺失補回 + retry hint 強化

**Patch · 修 v0.6.0 重構時漏接的「禁止 import」rule;順手強化 retry hint。**

### 🐛 修正

實際 case:baseline Case 03「畫出各公司的 PAY 與 RTN 申請數量,我想看哪家公司退件量最大」連續 3 次失敗,原因都是 `ModuleNotFoundError: No module named 'matplotlib'`。LLM 在 Phase B 寫 `import matplotlib`,但 Phase B exec namespace 只給 `pd / np / raw_df`。

Root cause:**v0.6.0 重構 Phase B 為 modular 時,universal header rule 1 漏接「禁止 import 任何套件」**。原版 Phase B 有這條(舊 `_PHASE_B_PREPROCESS_TEMPLATE` rule 1 含「不要再 import 任何套件」),v0.6.0 universal header 只寫了「禁止 print」就斷掉。LLM 看到「畫出 / stacked」query 觸發畫圖直覺 → 自動 `import matplotlib`。

**修法**:

- `_PHASE_B_HEADER_TEMPLATE_V6` rule 1 補:
  - 「**禁止 import 任何套件**」
  - 列舉黑名單:`matplotlib` / `plotly` / `seaborn`(典型 plot 套件)
  - 明確職責邊界:「Phase B 只負責資料處理,**不畫圖**」
- `_format_retry_hint` 加錯誤類型偵測:
  - `ModuleNotFoundError` / `No module named` → 加「關鍵修正提示:把 import 刪掉」
  - `KeyError` → 加「只能用 q_columns 中真實欄位」

### ✅ 預期效果

- 下一輪 baseline:Phase B 不會再寫 `import matplotlib`
- 即使萬一 LLM 還是寫 import,retry 第二次看到強化 hint 應該能修正

### 🚧 對當前 baseline 影響

當前 baseline(已跑到 Case 03 失敗)會繼續跑剩下的 case,**v0.7.1 修法對當前 run 不生效**(Python process 已 load 舊 prompt)。建議:
1. 讓當前 baseline 跑完,觀察其他 case 是否也有 ModuleNotFoundError(可能 STK-01~08 受影響)
2. push 完 v0.7.1 後重啟 → 跑新一輪 baseline

---

## [0.7.0] · 2026-05-15 — Task Trace Recorder(end-to-end query 追蹤)

**Minor · 加入完整 task trace,可逐步檢視每次 query 的所有 LLM call 與函式呼叫。**

### 🎯 動機

跑完 query 想 debug 哪一步慢、哪個 prompt 出了什麼問題、LLM 實際給了什麼回應 — 之前要從 console scrollback 撈,無法跨 session 保留。需要一個結構化、可長期保存、可 UI 檢視的 trace 系統。

### ✨ 主要改動

- **`task_trace.py` 新模組**:
  - `TaskTrace` class:`step(phase, kind)` context manager + `record_llm_call(...)` + `set_chart_intent / set_preprocess_intent` + `finalize(status)` 寫進 MongoDB
  - `TaskTraceRepository`:`list_recent` / `get_by_id` / `delete` / `purge_older_than`
  - Silent-fail 設計:DB 寫入失敗不影響 user query;訊息超大時自動截斷
- **`LLMService._call_llm` 加 hook**:
  - 新增 `self.trace` attribute(default None,向後相容)
  - 每次 call 完整記錄 `messages` + `response` + tokens 進 trace
  - 失敗 call 也記錄(含 error message)
- **`app.py` 整合**:
  - 每次 user query 開始時建 TaskTrace,attach 到 `llm_service.trace`
  - 偵測完 intent 後立刻記到 trace
  - 成功 / refuse / Phase B retry 失敗 / 系統例外 4 個路徑都會 finalize
  - 顯示 `🔍 Trace 已記錄 · <id>` toast
- **`pages/05_task_traces.py` admin 頁面**:
  - 上半:trace list table(time / domain / query / status / wall / tokens / chart intent / preprocess intent)
  - 下半:選擇 trace → 5 個 metric card + step 耗時 bar chart + 逐 step expander(LLM call 可展開看完整 messages + response + tokens)
  - Raw JSON debug view
  - 個別刪除 / N 天 purge
- **`config.py`**:加 `TASK_TRACES_COLLECTION` env(預設 `task_traces`)

### 📊 資料結構

每筆 trace ~20-100 KB,含:
- query / domain / intent / wall_time / status / error
- steps array(每個 step 含 phase / kind / elapsed_s / meta / llm_call payload)
- llm_call payload = `{model, messages[], response, prompt_tokens, completion_tokens, intent}`
- summary aggregates(LLM call 數 / 總 token)

### ✅ Sanity tests

- TaskTrace lifecycle with `db=None`:✅
- step ordering / kinds / elapsed / summary aggregation:✅
- LLMService `.trace` attribute 存在:✅
- pages/05_task_traces.py 語法 OK
- export_pptx smoke 7/7 仍綠

### 🚧 已知限制

- meta_response 路徑(intro/greeting/data_check 等)目前不會建 trace(早於 trace 建立點)
- test_runner.py 目前還沒整合 trace(待 v0.7.1,讓 baseline 也有 trace 可分析)
- 沒有 cross-trace 比較功能(待 v0.7.2)

---

## [0.6.1] · 2026-05-15 — Phase 0 + Phase A 範例壓縮(邊際改善)

**Patch · 收尾型瘦身,範例濃縮但不破壞語意。**

### ✨ 改動

- **Phase 0 範例 4 → 2**:
  - 砍 範例 1(heatmap → 走計畫,跟 範例 4 性質重疊)
  - 砍 範例 3(平均金額 → REFUSE,跟 範例 2 性質重疊)
  - 留 範例 1(時間/金額類 REFUSE 經典)+ 範例 2(反 false positive,圖型詞不可拒)
  - 範例 2 也順手抽象化為 `<實體列表>` / `<某指標>` placeholder(per v0.5.1 通用化紀律)
- **Phase A rule 5 反例壓縮**:
  - operator 黑名單收成 3 條(原 4 條)
  - JSON 反例 + Python 正解從多行範例縮成 1 行
  - 口訣保留(「Phase A 撈,Phase B 算」)

### 📊 量測結果

| Phase | Template baseline | v0.6.1 | 改善 |
|---|---:|---:|---:|
| Phase 0 | 3,247 | 2,837 | -12.6% |
| Phase A | 2,972 | 2,675 | -10.0% |

**Per Rule 12 老實說**:這比 v0.5.0(Phase C -80%)、v0.6.0(Phase B -46%)是**邊際改善**(per call 只省 ~700 chars)。但風險低、byte-equal 維持、smoke OK,屬於收尾。

### ✅ 驗證

- Phase 0 / Phase A / Phase C 三個 prompt embedded vs inline 全部 byte-equal
- Phase 0 critical sentinels 全在(圖型詞 / 完全不參與 / 撤回拒絕 / 最後檢查 / 範例 2)
- export_pptx smoke 7/7 仍綠

---

## [0.6.0] · 2026-05-15 — Phase B preprocess modular routing(-40~49% prompt size)

**Minor · 把 v0.5.0 的 Phase C 套路套到 Phase B,再砍一筆 prompt token。**

### 🎯 動機

- Phase C v0.5.0 已 -80% prompt size。次大目標是 Phase B(原 ~9.7K,佔 per-case 第二多)。
- 同設計原則(intent routing + universal header + 注入 block),從一開始就 domain-generic。

### ✨ 主要改動

- **`_detect_preprocess_intent(query, dashboard_hint, metadata)` 新增**(`llm_service.py`,純 Python heuristic):
  - 6 種 intent:`dashboard_kpi` / `stacked_long_pct` / `stacked_wide` / `ratio_kpi` / `time_series` / `simple_groupby`(default)
  - 重用 v0.5.1 的 `_has_rate` / `_has_count` regex(domain-generic)
  - **`time_series` 走 schema-driven 偵測**:檢查 `metadata.collections.*.fields` 有沒有 date/datetime/timestamp,沒有就不走(避免在沒時間欄的 domain 誤觸)
  - 20 個 unit test 全綠
- **Phase B template 拆 modular**(`embedded_prompts.py`):
  - `_PHASE_B_HEADER_TEMPLATE_V6`:universal rules(rule 1/1.5/2/3/4/5/6/7/8/9/10)
  - `_PHASE_B_INTENT_BLOCKS` dict:6 個 block
  - `_PHASE_B_FOOTER_TEMPLATE_V6`
  - `compose_phase_b_prompt_modular(intent, cols_info, domain_knowledge, dashboard_block)` helper
- **`generate_preprocess_code` 走 router**:`_detect_preprocess_intent(query, dashboard_hint, self.task_metadata)` → 注入對應 block
- **legacy Phase B template 保留**:DB repo 仍有舊版,v0.6.0 inline path 跳過(per Option A,migration 留 v0.6.1)

### 📊 量測結果(sandbox 驗證)

| Intent | Size | vs 舊 9.7K |
|---|---:|---:|
| `dashboard_kpi` | 5,870 | -39.5% |
| `stacked_long_pct` | 5,302 | -45.3% |
| `stacked_wide` | 4,959 | **-48.9%** |
| `ratio_kpi` | 5,175 | -46.6% |
| `time_series` | 5,170 | -46.7% |
| `simple_groupby` | 5,100 | -47.4% |

**平均 -46%**。疊加 v0.5.0 Phase C 的 -80%,**Phase B+C 合計 per case 從 ~31K → ~10K(-67%)**。

### 🐛 修正過的 bug

實作過程踩到 Jinja2 雷:intent block 內含 Python `pd.DataFrame({{'metric': [...]}})` 範例的 `{{` 被 Jinja 誤判為表達式 → crash。修法:intent block **不走 Jinja render**,改為 literal `.replace("{{ dashboard_block }}", ...)` 只處理 dashboard_kpi 的特殊 placeholder。

### 🌐 通用化驗證

| Domain | dashboard | stacked_100 | ratio | time_series | simple |
|---|:---:|:---:|:---:|:---:|:---:|
| Healthcare | ✅ | ✅ | ✅ | ✅ | ✅ |
| E-commerce | ✅ | ✅ | ✅ | ✅ | ✅ |
| HR | ✅ | ✅ | ✅ | ✅ | ✅ |
| tflex | ✅ | ✅ | ✅ | ✅ | ✅ |

`time_series` 用 metadata schema 偵測時間欄,避免在沒時間欄的 domain 誤觸。

### ✅ Sandbox 測試

- 20/20 detector unit test 全綠
- 6 個 intent 全部 < 6K(預估 5-6K,達標)
- end-to-end router dispatch:6/6 正確
- dashboard_block 注入驗證:✅ marker round-trip 通過
- export_pptx smoke 7/7 仍綠

### 🚧 後續(v0.6.1 規劃)

- DB migration:把 Phase B + Phase C 的 modular template seed 進 MongoDB
- 視 v0.6.0 baseline 結果決定是否要把某些 rule 拉回 universal header

---

## [0.5.1] · 2026-05-15 — Domain-generic intent detector(regex-based rate / count)

**Patch · 補 v0.5.0 兩個 domain-specific leak,確保新 domain 進來不用改 code。**

### 🐛 修正

v0.5.0 audit 發現兩處 tflex 殘留:

1. **`_CHART_RATE_WORDS` 列舉「通過率/退單率/達成率/完成率」**:任何新 domain 的「X 率」compound(健保「再入院率」/「住院率」、電商「轉換率」/「跳出率」、HR「離職率」/「升遷率」)都不會命中。
2. **`_PHASE_C_BLOCK_LINE_DUAL` 帶 case 01 註解**:「比較各公司的退單率與申請數,同時看到絕對量與比率」這條 tflex-specific 範例會讓 LLM 偏向 tflex 思考。

跨 domain 測試也意外發現 **`_CHART_COUNT_WORDS` 同樣漏接**:「員工數」「人次」「訂單筆」等通用詞被列舉式列表漏掉。

### ✨ 修法 — 一致用 regex pattern(non-enumeration)

- **`_has_rate(query)`**:universal 短詞 quick path + `[一-鿿]+率` regex 抓任何 domain 的 X 率。
- **`_has_count(query)`**:universal 短詞 + `[一-鿿]+(?:數|量|次|筆|件)(?!率)` regex 抓「X 數」「X 量」「X 次」compound。`(?!率)` 負向後查避免「成功率」誤觸 count。
- **line_dual block 註解抽象化**:「case 01 原型:比較各公司的退單率與申請數」改「比較各 <實體>,同時看到 <絕對量> 與 <比率>」placeholder。

### ✅ 驗證

22 cases × 3 fictitious domain + 10 tflex regression = **32/32 全綠**:
- Healthcare:`再入院率 vs 出院人次,比較各醫院` → `line_dual` ✅
- E-commerce:`跳出率 vs 訪問量,比較各 landing page` → `line_dual` ✅
- HR:`離職率 vs 員工數,比較各部門` → `line_dual` ✅
- tflex(10/10):全部 regression 通過,沒退步

### 📐 設計原則(per CLAUDE.md Rule 2 / Rule 8)

| 原則 | 例子 |
|---|---|
| Pattern-based,不 enumeration | ✅ regex `[一-鿿]+率` / ❌ list `('通過率','退單率',...)` |
| Negative lookahead 避免重疊 | `(?:數\|量\|次\|筆\|件)(?!率)` 排除「成功率」 |
| 註解抽象化 | `<實體>` / `<絕對量>` / `<比率>`,不用「公司」/「退單率」/「申請數」 |

未來新 domain 進來 → metadata 自行 seed,detector / blocks 不需改。

---

## [0.5.0] · 2026-05-15 — Phase C prompt modular routing(-80% prompt size)

**Minor · 主目標是加速 Phase C(echarts 那一站很慢的問題)。**

### 🎯 動機

baseline 量測:Phase C prompt 21.7K chars,佔每個 case 5 個 phase 總長度的 ~46%。Phase C 還會 retry,造成本輪 baseline 60%+ 的 prompt_tokens 都耗在重複送這個 24K 的 prompt。

### ✨ 主要改動

- **`_detect_chart_intent(query)` 新增**(`llm_service.py`,純 Python heuristic,零 LLM call):從 query 偵測 chart intent → 11 種 intent(`pie` / `stacked_100` / `stacked_raw` / `line_dual` / `heatmap` / `bar_horizontal` / `line_single` / `scatter` / `kpi_table` / `bar_grouped` / `bar_basic` default)。26 個 unit test 全綠。
- **Phase C template 拆 modular**(`embedded_prompts.py`):
  - `_PHASE_C_HEADER_TEMPLATE`(~3.5K,universal rules:0/1/2/3/3.1/3.3/3.5/4/5.3/5.7/6/7)
  - `_PHASE_C_INTENT_BLOCKS` dict(11 個 block,每個 0.3-1.5K)
  - `_PHASE_C_FOOTER_TEMPLATE`(few_shot + 結尾)
  - `compose_phase_c_prompt_modular(intent, cols_info, echarts_few_shot)` 組裝函式
- **`generate_echarts_option` 走 router**:`_detect_chart_intent(query)` → 對應 block → `_render_phase_c_echarts_prompt(cols_info, intent)` 注入只需要的規則。
- **legacy `_PHASE_C_ECHARTS_TEMPLATE` 保留**:DB repo 仍有舊 24K 版,v0.5.0 inline path 跳過它直接 modular(per 設計案 Option A)。v0.5.1 migration 才會 deprecate DB 端。

### 📊 量測結果(sandbox 驗證)

| Intent | Prompt size | vs 舊 21.7K |
|---|---:|---:|
| pie | 4,344 | **-80.0%** |
| stacked_100 | 4,607 | -78.8% |
| stacked_raw | 4,429 | -79.6% |
| line_dual | 4,764 | -78.1% |
| heatmap | 5,019 | -76.9% |
| bar_horizontal | 4,401 | -79.7% |
| line_single | 4,055 | -81.3% |
| scatter | 4,074 | -81.2% |
| kpi_table | 4,389 | -79.8% |
| bar_grouped | 4,048 | -81.4% |
| bar_basic | 3,850 | -82.3% |

**平均 -80%**,預期對應 wall time -40~50%(LLM 處理 prompt 時間約線性,但 completion time 不變)。

### ⚠️ 已知風險

- prompt 砍得比預估激進(原預估 60%,實際 80%),**品質可能掉**:
  - rule 5.53 完整版被精簡(Series 動態產出鐵律的詳盡反例消失)
  - rule 5.54 完整句型表被壓成口訣
  - rule 5.55 的 hardcode 反例細節砍掉
- **需要真實 baseline 驗證**:wall time -30%+ + OK rate ≥ 22/26 才算合格;若任一不達標,須回頭加 rules 回 universal header

### ✅ Sandbox 測試

- Detector:26/26 unit test 全綠
- Compose:11 個 intent 全部 < 5.1K
- Router 端到端 dispatch:3/3 正確
- export_pptx smoke 7/7 仍綠

### 🚧 後續(v0.5.1 規劃)

- DB migration 003:把 modular template seed 進 MongoDB
- 視 v0.5.0 baseline 結果決定是否要把某些 rule 拉回 universal

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
