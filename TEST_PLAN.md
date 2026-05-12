# tFlex GenBI · 系統測試計畫

> **目標**:用 15 個由淺至深的 case,完整驗證 Agentic Workflow 的 5 階段流程
> (Plan → MongoDB → Pandas → Chart → Insight),並涵蓋 9 種圖表型態
> 與 4 種「資料不足」拒絕情境。
>
> **使用方式**:在 `streamlit run app.py` 開啟 GenBI 後,依序貼問題進去測試,
> 對照每個 Case 的「期望輸出」與「檢查清單」打勾。
> 預設 sidebar 設定:`資料源=MongoDB (real)`、`圖表引擎=ECharts`、`啟用 Phase D`。

---

## 📋 圖表型態覆蓋矩陣

| Case | 圖表型態 | ECharts 關鍵特性 | 資料維度 |
|---|---|---|---|
| 01 | 雙軸 bar + line | 兩個 yAxis、smooth line、`{value}%` formatter | 公司 × (count + rate) |
| 02 | 單軸 bar (含百分比 label) | percentage formatter | 兩家公司比較 |
| 03 | Stacked bar | `series[].stack` | 公司 × (PAY/RTN) |
| 04 | Percentage bar | yAxis formatter `{value}%` | 公司 × ai_rate |
| 05 | Grouped bar | 兩 series 同 xAxis 不 stack | 公司 × (AI/Human) |
| 06 | Single rate bar | sort + percentage axis | 公司 × completion_rate |
| 07 | Categorical bar | 類別維度 (非公司) | application_category × count |
| 08 | Heatmap | `series[].type='heatmap'` + visualMap | 公司 × 類別 |
| 09 | Scatter | `series[].type='scatter'` + 標籤 | per-company (rate1, rate2) |
| 10 | Sorted Top-N bar | 排序 + slice | 退件數 top 5 |
| 11 | Table fallback | `option = {_use_table: True}` → st.dataframe | 全 KPI 寬表 |
| T1 | **精美 dashboard 表** | `_kpi_cards` (st.metric) + ProgressColumn 漸層條 | 完整公司 × 7 KPI |
| T2 | **異常排行表** | sorted + ProgressColumn 視覺對比 | 公司 × 退單率 |
| T3 | **TOP/BOTTOM 對比** | wide 切片表或左右並排 | 公司 × 申請數 |

---

## 🚫 拒絕測試矩陣 (data_limitations)

| Case | 觸發限制 | 預期 Phase 0/A 行為 |
|---|---|---|
| 12 | 缺 application_date | Plan 階段直接回拒,引用「No application date」 |
| 13 | 缺 department_code | Plan 階段拒絕,建議改看公司維度 |
| 14 | 缺 payment_amount | Plan 階段拒絕 |
| 15 | 缺 review_completed_timestamp | Plan 階段拒絕 |

---

## ✅ 全域檢查項 (每個 case 都要打勾)

- [ ] **Sidebar 狀態正常**:LLM endpoint 顯示 `localhost:11434/v1`、MongoDB 顯示 `✅ Connected — tflex_demo`
- [ ] **無系統級例外**:右下角無 `❌ 系統執行中斷` 紅框
- [ ] **無 Streamlit duplicate key warning**(echarts widget key 唯一)
- [ ] **歷史訊息可回放**:重新整理頁面後,過去的圖表仍能正確渲染
- [ ] **plan / pipeline / preprocess / plot 四個 expander 都有內容可展開**

---

# Happy Path 案例

---

## Case 01 · 各公司退單率與申請數比較

**🎯 問題:**
```
比較各公司的退單率與申請數,我想同時看到絕對量與比率
```

**Tags:** `雙軸 bar+line` · `跨表 join` · `比率轉百分比` · `小樣本警告`

### Phase 0 · Plan 制定
**預期內容:**
- A 段:指出需要 `tflex_applications` join `tflex_company_hc`
- B 段:明確列出 `return_count`、`completed_count`、`average_return_rate = return_count / completed_count`
- C 段:建議雙軸 bar+line 或 grouped bar,**禁止 pie**

**檢查項:**
- [ ] 有清楚列出 A/B/C 三段
- [ ] 引用了正確的 KPI 名稱 (`average_return_rate`、`completed_count`)
- [ ] 提醒分母不可包含 in-progress (`review_status='N'`)

### Phase A · MongoDB Pipeline
**預期 JSON:**
```json
{
  "start_collection": "tflex_applications",
  "pipeline": [
    {"$lookup": {"from": "tflex_company_hc", "localField": "company_code",
                 "foreignField": "company_code", "as": "hc_info"}},
    {"$unwind": {"path": "$hc_info", "preserveNullAndEmptyArrays": true}},
    {"$project": {"_id": 0, "company_code": 1, "review_status": 1,
                  "review_result": 1, "hc": "$hc_info.hc"}}
  ]
}
```

**檢查項:**
- [ ] 是合法 JSON,Streamlit 能 json.loads 解析成功
- [ ] 有 `$lookup` + `$unwind`(`preserveNullAndEmptyArrays: true`)
- [ ] **沒有** `$group` / `$count` / `$sort` / `$divide` / `$cond`(DB 端禁聚合)
- [ ] `$project` 只保留下游需要的欄位

### Phase B · Pandas 處理
**預期程式碼骨架:**
```python
Q = raw_df.copy()
agg = Q.groupby("company_code").agg(
    total_applications=("review_status", "size"),
    completed_count=("review_status", lambda s: (s == "Y").sum()),
    return_count=("review_result", lambda s: ((s == "N") & (Q.loc[s.index, "review_status"] == "Y")).sum()),
).reset_index()
agg["average_return_rate"] = agg["return_count"] / agg["completed_count"]
Q = agg
```

**檢查項:**
- [ ] 最外層宣告了 `Q`(非 function 包覆)
- [ ] `completed_count` 用 `review_status == 'Y'`(不可用 total)
- [ ] `return_count` 同時檢查 `review_status='Y'` AND `review_result='N'`
- [ ] 退單率分母是 `completed_count`,**不是** `total_applications`
- [ ] Q 至少有 `company_code`、`total_applications`、`return_count`、`average_return_rate` 四欄

### Phase C · ECharts 視覺化
**預期 option 重點:**
- 兩個 `yAxis`:左軸 `name='申請數'`,右軸 `name='退單率'` + `axisLabel.formatter='{value}%'`
- `series[0].type='bar'`(申請數),`series[1].type='line'` + `yAxisIndex=1`(退單率)
- 退單率資料已 `* 100` 並 `round(2)`,確保不會出現 0.0356 這種小數

**檢查項:**
- [ ] 圖表正常渲染,沒有空白
- [ ] 右軸最大值 ≤ 10(因為退單率多在 1-6%),不是 0-1
- [ ] hover 時 tooltip 同時顯示兩個系列
- [ ] TSK 那根 bar 幾乎看不到(因為只有 7 件),退單率為 0%

### Phase D · 商業洞察
**預期內容:**
- 重點摘要:點名 TST 申請數最高、TSN 退單率最高 (~5.7%)
- 觀察與建議:高量公司即使比率不高,RTN 絕對數仍可觀
- ⚠️ 警告:**TSK 樣本太小(hc=2),退單率 0% 不具代表性**

**檢查項:**
- [ ] 出現 TSK / 小樣本相關警告字眼
- [ ] 沒有提到「趨勢」、「月度」、「部門」、「金額」(無此資料)
- [ ] 至少 3 條 bullet,且每條都帶具體數字

---

## Case 02 · TST 與 TSK 員工送單率比較

**🎯 問題:**
```
比較 TST 與 TSK 兩家公司的員工送單率
```

**Tags:** `多公司 $match` · `distinct nunique` · `小樣本陷阱`

### Phase 0 · Plan
**檢查項:**
- [ ] 提到 `employee_submission_rate = distinct(employee_id) / hc`
- [ ] 主動標註 TSK hc=2 為極端小樣本

### Phase A · Pipeline
**預期重點:** `$match: {"company_code": {"$in": ["TST", "TSK"]}}` + lookup + project

**檢查項:**
- [ ] `$match` 用 `$in` 而非兩個 OR
- [ ] 投影包含 `employee_id`(計算 distinct 用)

### Phase B · Pandas
**檢查項:**
- [ ] 使用 `nunique()` 計算 distinct employee_id(**不可** `len(set())`)
- [ ] 分母 hc 來自 join 後欄位
- [ ] Q 應為 2 列(TST、TSK)

### Phase C · ECharts
**預期:**
- 單一 bar series,xAxis = `["TST", "TSK"]`
- yAxis.axisLabel.formatter = `'{value}%'`
- 資料應為 `[95.16, 100.00]`(轉百分比後)

**檢查項:**
- [ ] 柱子明顯非 0(避免比率沒轉百分比)
- [ ] tooltip 顯示百分比格式

### Phase D · Insight
**檢查項:**
- [ ] **明確警告 TSK 雖然 100% 但只有 2 個員工**
- [ ] 不可推論「TSK 員工參與感較高」這種被小樣本誤導的結論

---

## Case 03 · 各公司 PAY vs RTN 申請數對比

**🎯 問題:**
```
畫出各公司的 PAY 與 RTN 申請數量,我想看哪家公司退件量最大
```

**Tags:** `stacked bar` · `絕對量比較`

### Phase 0 · Plan
**檢查項:**
- [ ] 提到 `pay_count = (review_status='Y' AND review_result='Y')`,`return_count` 同邏輯不同 result
- [ ] 建議 stacked bar 而非 pie

### Phase A · Pipeline
**檢查項:**
- [ ] 不需要 hc,可以不 lookup(或 lookup 也接受,只是浪費)
- [ ] 投影至少含 `company_code`、`review_status`、`review_result`

### Phase B · Pandas
**檢查項:**
- [ ] 同時計算 `pay_count`、`return_count`,兩者分子都帶 `review_status=='Y'` 前綴
- [ ] Q 含 `company_code`、`pay_count`、`return_count` 三欄,15 列

### Phase C · ECharts
**預期 option:**
- `series` 兩個元素,**都帶 `"stack": "result"`**(同一個 stack 名)
- 配色用 `#73c0de`(PAY)、`#ee6666`(RTN)

**檢查項:**
- [ ] 柱子是堆疊的,**不是並排**
- [ ] TST 那根明顯最高,RTN 區塊(紅色)在頂端較小
- [ ] legend 顯示 PAY / RTN 兩項

### Phase D · Insight
**檢查項:**
- [ ] 點名 TST 是 RTN 絕對量最大公司(4,285 件)
- [ ] 提醒「高 RTN 量 ≠ 高 RTN 率」

---

## Case 04 · 各公司 AI 審查率

**🎯 問題:**
```
哪些公司的 AI 審查率比較高?跟 43% 的目標比起來如何?
```

**Tags:** `percentage bar` · `benchmark line` · `completed-only 過濾`

### Phase 0 · Plan
**檢查項:**
- [ ] 明確說「分母 = completed_count」(只算 review_status='Y')
- [ ] 提到可加 markLine 在 43% 標示 target

### Phase B · Pandas
**檢查項:**
- [ ] 篩選 `review_status == 'Y'` 後才計數
- [ ] `ai_review_rate = ai_count / completed_count`
- [ ] **絕對不可** 把 `review_mechanism is null` 的算進分子

### Phase C · ECharts
**預期重點:**
- yAxis `{value}%`
- `series[0].markLine = {data: [{yAxis: 43, name: 'target'}]}`(若 LLM 有寫進來更好,沒寫也算過,只要主圖正確)

**檢查項:**
- [ ] y 軸顯示百分比,不是 0-1
- [ ] 有公司明顯超過 43%(metadata 顯示部分公司應達標)

### Phase D · Insight
**檢查項:**
- [ ] 與 43% 標準對比,點出哪些公司達標 / 未達標
- [ ] 不出現「AI 取代人類審查員」之類的越界判斷

---

## Case 05 · AI 審查 vs 人工審查 by 公司

**🎯 問題:**
```
看一下每家公司在 AI 審查跟人工審查的件數分佈,我想找出還是高度依賴人工的公司
```

**Tags:** `grouped bar`

### Phase B · Pandas
**檢查項:**
- [ ] 分別計算 `ai_review_count` 與 `human_review_count`,都帶 `review_status='Y'` 前綴
- [ ] Q 至少含 `company_code`、`ai_review_count`、`human_review_count`

### Phase C · ECharts
**預期:** 兩個 bar series,**不帶** `stack`,並排顯示

**檢查項:**
- [ ] 柱子並排(grouped),不是堆疊
- [ ] 兩色清楚:藍 (#5470c6) + 黃 (#fac858)

### Phase D · Insight
**檢查項:**
- [ ] 指出仍以人工為主的公司
- [ ] 結合 Case 04 的 AI 率思考(若 LLM 有跨案例聯想更好)

---

## Case 06 · 各公司審核完成率

**🎯 問題:**
```
排序各公司的審核完成率,看誰積壓最嚴重
```

**Tags:** `single rate bar` · `sort xAxis` · `in_progress 解讀`

### Phase B · Pandas
**檢查項:**
- [ ] `completion_rate = completed_count / total_applications`(注意分母是 total)
- [ ] 排序由低到高(積壓嚴重排前面)

### Phase C · ECharts
**檢查項:**
- [ ] xAxis 順序符合排序結果
- [ ] yAxis 是百分比
- [ ] (加分項)TDI 應為最低(74.3%)

### Phase D · Insight
**檢查項:**
- [ ] 說明完成率低 = in-progress 多 = backlog
- [ ] **不可** 推論成「審核效率差」或「人手不足」(沒有 reviewer 資料)

---

## Case 07 · 各類別申請數分布

**🎯 問題:**
```
四個福利申請類別,哪個最熱門?
```

**Tags:** `categorical bar (非公司維度)`

### Phase A · Pipeline
**檢查項:**
- [ ] 投影包含 `application_category`
- [ ] 可以不 lookup(類別與 hc 無關)

### Phase B · Pandas
**檢查項:**
- [ ] groupby `application_category` count
- [ ] Q 為 4 列(Family Care、Wellness、Medical & Insurance、Development & Voluteering)

### Phase C · ECharts
**檢查項:**
- [ ] xAxis 4 個類別文字完整(不被截斷)
- [ ] 由高到低排序更好(非必要)

---

## Case 08 · 公司 × 類別 申請數熱力圖

**🎯 問題:**
```
我想看不同公司在不同申請類別的分佈,有沒有熱力圖可以畫?
```

**Tags:** `heatmap` · `2D pivot` · `visualMap`

### Phase B · Pandas
**預期:**
```python
pivot = Q.groupby(["company_code", "application_category"]).size().reset_index(name="count")
# 或直接 pivot_table → long format
```

**檢查項:**
- [ ] Q 為 long format:[company_code, application_category, count]
- [ ] 或為 pivot wide:index=company_code, columns=application_category

### Phase C · ECharts
**預期 option 重點:**
- `series[0].type = "heatmap"`
- `xAxis: {type: "category", data: companies}`
- `yAxis: {type: "category", data: categories}`
- `visualMap: {min, max, ...}`

**檢查項:**
- [ ] 圖確實是熱力矩陣,不是 bar
- [ ] 有 visualMap 顏色條
- [ ] TST × 各類別 應該明顯偏紅(資料量大)

---

## Case 09 · AI 審查率 vs 退單率散點圖

**🎯 問題:**
```
AI 審查率跟退單率有相關嗎?畫個散點圖看看
```

**Tags:** `scatter` · `相關性洞察` · `每點標註 company_code`

### Phase B · Pandas
**檢查項:**
- [ ] Q 為 per-company:[company_code, ai_review_rate, average_return_rate]
- [ ] 兩個比率都 `* 100` 轉百分比(便於閱讀)

### Phase C · ECharts
**預期:**
- `series[0].type = "scatter"`
- `series[0].data = [[ai_rate, return_rate], ...]`
- `series[0].label = {show: true, formatter: 公司代碼}`(或用 series[0].name 區隔)

**檢查項:**
- [ ] 是散點,不是 bar/line
- [ ] hover 能看到公司代碼
- [ ] 軸標籤都是百分比

### Phase D · Insight
**檢查項:**
- [ ] 提到相關性方向(正/負/無)
- [ ] **不可武斷下因果結論** — 可說「相關」、不能說「AI 審查導致退件」

---

## Case 10 · TOP 5 退件最多的公司

**🎯 問題:**
```
列出退件數量最多的前 5 名公司,搭配柱狀圖
```

**Tags:** `sorted bar` · `slice top N` · `LLM 不可在 DB 端 sort/limit`

### Phase A · Pipeline
**🚨 關鍵檢查:**
- [ ] **絕對不可** 出現 `$sort` 或 `$limit`(這是 LLM 容易犯規的點)
- [ ] 一律撈明細,排序與切片交給 Pandas

### Phase B · Pandas
**檢查項:**
- [ ] `Q.sort_values("return_count", ascending=False).head(5)`
- [ ] Q 最終為 5 列

### Phase C · ECharts
**檢查項:**
- [ ] 柱狀圖由高到低排列
- [ ] 第一名應為 TST(4,285 件)

---

## Case 11 · 全公司完整 KPI 一覽表

**🎯 問題:**
```
幫我整理一張完整的公司 KPI 表格:申請數、完成數、PAY、RTN、退單率、AI 率、員工送單率全都要
```

**Tags:** `use_table fallback` · `LLM 主動選擇表格`

### Phase B · Pandas
**檢查項:**
- [ ] Q 一次性把所有 KPI 都算出來
- [ ] 比率欄位四捨五入到 2-4 位

### Phase C · ECharts
**🎯 期望路徑:**
- LLM 應主動輸出 `option = {"_use_table": True}`
- app.py 偵測到後 fallback 到 `st.dataframe(Q)`
- **或** LLM 強行畫 heatmap / multi-series bar — 也算過,但表格 fallback 是最佳實踐

**檢查項:**
- [ ] 表格正常呈現(15 列 × N 欄)
- [ ] 數字格式合理(比率帶 % 或小數點)

### Phase D · Insight
**檢查項:**
- [ ] 即使主視覺是表格,洞察仍能正確排序、點名異常公司

---

# 進階表格與 KPI 匯總案例

> 這組 case 專門驗證 `_use_table` 的精美渲染 (KPI metric 卡片 + ProgressColumn 漸層進度條
> + 千分位數字)、以及多 KPI 一覽表的執行摘要呈現。

---

## Case T1 · 公司 KPI 執行摘要(KPI 卡片 + 漸層表)

**🎯 問題:**
```
幫我做一份完整的公司 KPI 執行摘要 dashboard:申請數、完成率、退單率、AI 採用率、員工送單率都要,並在最上方放總體 KPI 卡片
```

**Tags:** `_use_table` · `_kpi_cards` · `ProgressColumn` · `dashboard 場景`

### Phase 0 · Plan
**檢查項:**
- [ ] Plan 主動建議「以表格 + KPI 卡片」呈現,而非畫圖
- [ ] 列出至少 5 個 KPI 並引用 metadata 公式

### Phase A · Pipeline
**檢查項:**
- [ ] 有 `$lookup` join hc(送單率分母需要)
- [ ] 投影包含所有 KPI 計算需要的欄位

### Phase B · Pandas
**預期 Q 結構(15 列 × ≥7 欄):**
```
company_code | hc | total_applications | pay_count | return_count |
completed_count | average_return_rate | ai_review_rate | employee_submission_rate
```

**檢查項:**
- [ ] Q 至少含 6 個 KPI 欄位
- [ ] 比率欄位數值在 0-1 範圍(不可預先 ×100,讓 app 自動處理)
- [ ] 員工送單率分母為 hc(整數),分子為 distinct employee_id

### Phase C · ECharts(預期走 `_use_table`)
**🎯 必中項目:**
- [ ] **`option["_use_table"] == True`** — LLM 主動選擇表格而非圖表
- [ ] **`option["_kpi_cards"]` 存在且包含 3-4 張卡片**
- [ ] 卡片 label 含「總申請數」、「平均退單率」、「AI 採用率」之類
- [ ] 卡片 value 是用 f-string + Q 運算 (`Q['col'].sum()`、`Q['col'].mean()*100`)
- [ ] (加分項)`_table_caption` 有提及資料來源或公司數

**渲染後 UI 檢查:**
- [ ] 表格上方有 3-4 張 metric 卡片並排
- [ ] **比率欄位顯示為漸層進度條 (ProgressColumn)** 而非純數字
- [ ] 大整數欄位有千分位逗號 (e.g., `128,922` 不是 `128922`)
- [ ] 表格 height 適中(不至於太擠或太空)
- [ ] index 隱藏(不顯示 0, 1, 2, ...)

### Phase D · Insight
**檢查項:**
- [ ] 即使主視覺是表格,洞察仍能掃描所有 KPI 點出異常公司
- [ ] 不會出現「圖表中可以看到 ...」這類錯位語(因為是表格)

---

## Case T2 · 退單率異常排行表

**🎯 問題:**
```
找出退單率最高的公司排行表,並標註哪些公司明顯高於整體平均
```

**Tags:** `sorted table` · `異常標註` · `ProgressColumn 視覺對比`

### Phase B · Pandas
**預期:**
```python
Q = ... sort_values("average_return_rate", ascending=False)
# 可選:加 is_anomaly 欄位 (return_rate > mean + 1σ)
```

**檢查項:**
- [ ] Q 按退單率由高到低排序
- [ ] (加分)有 `is_anomaly` 或 `level` 欄位標示異常

### Phase C
**檢查項:**
- [ ] 走 `_use_table` 或畫 sorted bar 都算過
- [ ] 若走 table:`return_rate` 欄位是 ProgressColumn,**異常公司的進度條明顯較長**
- [ ] (加分)若 LLM 加了 status 欄位 (🔴/🟡/🟢) 並用 emoji 顯示分級

### Phase D
**檢查項:**
- [ ] 點名 TSN 退單率最高 (~5.7%)、TWT 次之 (~5.2%)
- [ ] 對比整體平均 (~3.67%) 給定相對位置

---

## Case T3 · TOP 5 vs BOTTOM 5 對比

**🎯 問題:**
```
申請量最大跟最少的各 5 家公司,做一張對比表呈現
```

**Tags:** `TOP/BOTTOM 切片` · `寬表 / 並排表`

### Phase B · Pandas
**預期(兩種策略都接受):**
- 策略 A:Q 為單一 wide table,加 `category` 欄位(TOP/BOTTOM)區分 10 列
- 策略 B:Q 為單表,但 sort 後取首尾各 5,共 10 列

**檢查項:**
- [ ] Q 至少 10 列(或精確 10 列)
- [ ] 含 `company_code`、申請數、可辨識的 TOP/BOTTOM 標記
- [ ] BOTTOM 公司應為 TSK、TDC、TSJ、TSE、ESM(低 hc 群)

### Phase C
**檢查項:**
- [ ] 走 `_use_table` 並有 KPI 卡片(總公司數、量級差、平均量等)
- [ ] 或畫橫向 bar 比較,sorted 順序明確

---

# 拒絕路徑案例 (data_limitations)

---

## Case 12 · 過去三個月的申請趨勢 (REFUSE)

**🎯 問題:**
```
我想看過去三個月每週的申請趨勢
```

**期望行為:**
- **Phase 0 (Plan) 直接拒絕**
- 引用 `data_limitations.missing_dimensions` 中的 `No application date`
- 建議改問:各公司申請數比較 (Case 03)

**檢查項:**
- [ ] Plan 回覆包含「資料不足」、「無 date」、「No application date」之類字眼
- [ ] **不會** 繼續往 Phase A 走
- [ ] 系統不會 crash,而是停在 Phase 0 並提示使用者
- [ ] (加分項)LLM 主動建議可改問什麼

⚠️ 若 LLM 仍硬產 pipeline,Phase A 撈不到 date 欄位、Phase B 大概率報錯 → 也算「最終拒絕」,但屬於下策。

---

## Case 13 · 各部門退單率 (REFUSE)

**🎯 問題:**
```
各部門的退單率比較,哪個部門最高?
```

**檢查項:**
- [ ] Plan 拒絕,引用 `No employee department`
- [ ] 建議改用「公司維度」(Case 01 / 03)
- [ ] 不會把 company_code 誤當成 department

---

## Case 14 · 平均申請金額 (REFUSE)

**🎯 問題:**
```
員工每次申請的平均金額是多少?哪家公司平均最高?
```

**檢查項:**
- [ ] Plan 拒絕,引用 `No payment amount`
- [ ] 不會虛構金額 / 不會誤把件數當金額

---

## Case 15 · 平均審核時間 (REFUSE)

**🎯 問題:**
```
平均審核需要幾天?AI 跟人工誰比較快?
```

**檢查項:**
- [ ] Plan 拒絕,引用 `No review completion timestamp` / `No submission date`
- [ ] 不會虛構時間欄位

---

# 🔧 進階整合測試

## Integration Test A · 資料源 fallback 切換

1. 停掉 mongod (`brew services stop mongodb-community`)
2. 重整 Streamlit
3. Sidebar 應顯示「⚠️ 無法連線,將自動 fallback 到 CSV」
4. 跑 Case 03 的問題
5. 期望:
   - [ ] 資料源顯示 `CSV fallback (本機 pandas)`
   - [ ] 結果應與真實 MongoDB 模式一致(因為都是同一份 CSV)
   - [ ] Pipeline 的 `$match` 仍被 pandas 解譯器套用

## Integration Test B · 圖表引擎 A/B 切換

1. 跑 Case 01,引擎選 `ECharts`,記下渲染結果
2. Sidebar 切換到 `Plotly`,重送一次同樣的問題
3. 期望:
   - [ ] 兩種引擎都成功 render
   - [ ] 數值結論(TSN 退單率最高、TST 量最大)兩邊一致
   - [ ] 切換引擎不會出現 widget key 衝突或 component error

## Integration Test C · Phase B/C 自我修正觸發

> ⚠️ 這個測試難以人工強制觸發,但可在多次測試中觀察以下狀況:

- 當看到 toast `⚠️ Phase B 第一次失敗,正在帶錯誤回饋重生...` 出現時:
  - [ ] 第二次重生後成功
  - [ ] 在 expander 中展開腳本,能看到第二版確實修正了第一版的問題
  - [ ] **不會** 出現「死亡筆記本」(連續失敗紅框)

## Integration Test D · LLM warm-up 容忍度

1. 重啟 Ollama (`ollama stop && ollama serve &`)
2. 第一次送 query,期望:
   - [ ] 不會在 30s/60s 內超時(timeout 設定為 180s)
   - [ ] 看到 status 持續顯示「Phase 0 制定計畫中...」直到回應

---

# 📊 通過判定總表

| Case | Phase 0 | Phase A | Phase B | Phase C | Phase D | 整體 |
|---|---|---|---|---|---|---|
| 01 | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| 02 | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| 03 | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| 04 | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| 05 | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| 06 | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| 07 | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| 08 | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| 09 | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| 10 | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| 11 | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| T1 | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| T2 | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| T3 | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| 12 | 拒絕 | N/A | N/A | N/A | N/A | ☐ |
| 13 | 拒絕 | N/A | N/A | N/A | N/A | ☐ |
| 14 | 拒絕 | N/A | N/A | N/A | N/A | ☐ |
| 15 | 拒絕 | N/A | N/A | N/A | N/A | ☐ |

---

# 🐛 常見失敗模式 (debug 速查表)

| 症狀 | 最可能原因 | 看哪裡 |
|---|---|---|
| Phase A JSON 解析失敗 | LLM 在 pipeline 前後加了 ```json fence 沒被剝乾淨 | 看 `_strip_code_fence` regex,或在 expander 看 raw |
| Q 為空 / KeyError | LLM 用了 raw_df 沒投影出來的欄位 | 對照 `avail_cols` log |
| 退單率算錯 (太高) | 分母誤用 total_applications | Phase B expander 看 LLM 寫的分母 |
| ECharts 不渲染 | option 缺 series 或 series.data 是 ndarray 不是 list | 確認 `.tolist()` 被呼叫 |
| 退單率柱子高度為 0 | 沒乘 100,軸是 0-1 | 看 yAxis.axisLabel.formatter 與 data 範圍 |
| 第一次 query 超時 | Ollama 模型還沒 warm up | 第二次再送就會快;確認 `HRDA_MODEL_TIMEOUT_S=180` |
| widget key 衝突 | 同一個 ECharts key 被重複用 | 確認 `key=f"echarts_history_{idx}"` 與 `echarts_live_{...}` 不相同 |

---

# ⏱️ 建議測試順序

**Smoke test (10 分鐘速通):** 03 → 12 → 11 — 涵蓋 happy path bar、拒絕路徑、table fallback
**完整 happy path (30 分鐘):** 01 → 02 → 03 → 04 → 05 → 06 → 07 → 08 → 09 → 10 → 11
**邊界與拒絕 (10 分鐘):** 12 → 13 → 14 → 15
**整合測試 (15 分鐘):** A → B → D (C 觀察用,無需主動觸發)
