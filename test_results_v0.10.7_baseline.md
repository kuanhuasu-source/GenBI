# tFlex GenBI · 測試結果報告

> 共 26 個 case · 由 test_runner.py 產生

## 📊 速覽

| Case | 名稱 | 狀態 | 通過/總數 | 累積耗時 |
|---|---|---|---|---|
| 01 | 各公司退單率與申請數(雙軸 bar+line) | pass | 12/12 | 49.2s |
| 02 | TST vs TSK 員工送單率(小樣本陷阱) | pass | 12/12 | 55.3s |
| 03 | PAY vs RTN(stacked bar) | pass | 13/13 | 44.4s |
| 04 | 各公司 AI 審查率(vs 43% 目標) | pass | 12/12 | 61.9s |
| 05 | AI vs Human review by 公司(grouped 不堆疊) | pass | 12/12 | 44.9s |
| 06 | 各公司審核完成率排序 | pass | 12/12 | 57.5s |
| 07 | 申請類別分布(非公司維度) | pass | 12/12 | 40.0s |
| 08 | 公司 × 類別 熱力圖(壓力測試) | pass | 13/13 | 70.0s |
| 09 | AI 審查率 vs 退單率散點圖 | pass | 14/14 | 54.4s |
| 10 | TOP 5 退件公司(陷阱:DB 不可 $sort/$limit) | pass | 13/13 | 47.0s |
| 11 | 全公司完整 KPI 一覽表(table fallback) | pass | 12/12 | 64.3s |
| 12 | 過去三個月趨勢(拒絕:無 date) | refusal_detected | 3/3 | 5.2s |
| 13 | 各部門退單率(拒絕:無 department) | refusal_detected | 3/3 | 1.1s |
| 14 | 平均申請金額(拒絕:無 amount) | refusal_detected | 3/3 | 1.1s |
| 15 | 平均審核時間(拒絕:無 timestamp) | refusal_detected | 3/3 | 1.3s |
| STK-01 | 100% stacked bar:per company × category | phaseC_fallback_used | 8/8 | 72.9s |
| STK-02 | 100% stacked transposed:per category × company | pass | 17/17 | 70.7s |
| STK-03 | Raw count stacked:PAY vs RTN by company | pass | 16/16 | 70.0s |
| STK-04 | 三狀態 100% stacked:per category × (approved/returned/in_progress) | pass | 17/17 | 82.8s |
| STK-05 | Stacked + filter:TST/TSC 各類別 AI vs Human | phaseC_fallback_used | 8/8 | 78.4s |
| STK-06 | Edge:hc 範圍過濾 + 缺漏組合 | pass | 16/16 | 52.9s |
| STK-07 | Follow-up:基本 bar 改 stacked (需 last_analysis) | pass | 15/15 | 66.6s |
| STK-08 | 橫向 100% stacked bar | pass | 17/17 | 53.0s |
| T1 | 公司 KPI 執行摘要 (精美表格 + KPI 卡片) | pass | 13/13 | 67.3s |
| T2 | 退單率異常排行表(條件格式) | pass | 12/13 | 55.3s |
| T3 | 申請量 TOP 5 vs BOTTOM 5 對比 | pass | 12/12 | 53.3s |

---

## Case 01 · 各公司退單率與申請數(雙軸 bar+line)
**Query:** `比較各公司的退單率與申請數,我想同時看到絕對量與比率`
**Type:** happy_path · **Status:** pass

### 檢查項
- ✅ Plan 有產出
- ✅ Plan 非空
- ✅ Pipeline 不含禁忌 stage (group/sort/limit/divide/cond)  ·  ✓ 乾淨
- ✅ Pipeline 撈到非空資料  ·  shape=(147526, 6)
- ✅ Phase B exec 成功 (用 1 次嘗試)  ·  1 次過
- ✅ Phase B 有產出 Q
- ✅ Q 含必備欄位 ['average_return_rate']  ·  ✓
- ✅ Phase C exec 成功 (用 1 次嘗試)  ·  1 次過
- ✅ option 含必備 keys ['title', 'xAxis', 'yAxis', 'series']  ·  ✓
- ✅ series 數 ≥ 2  ·  actual: 2
- ✅ Insight 有產出
- ✅ Insight 無禁忌語(或皆為拒絕語境)

### Phase 0 · Plan (11.4s)
```
**A. 資料獲取:**  
- 起手 collection: `tflex_applications`  
- 需要 join 的表: `tflex_company_hc` (用於取得公司人力 headcount，計算員工送單率)  
- 需要的 $match 過濾條件: 無（需包含所有 application records）  
- 🔑 **需要的原始欄位 (raw_columns_needed)**:  
  `[employee_id, company_code, review_status, review_result, application_no]`  

**B. 資料處理:**  
需計算以下 KPI：  
1. **總申請張數 (total_applications)**: `count(*)`  
2. **退單數 (return_count)**: `count where review_status='Y' and review_result='N'`  
3. **平均退單率 (average_return_rate)**: `return_count / total_applications`  
4. **送單人數 (submitter_count)**: `distinct count of employee_id`  
5. **員工送單率 (employee_submission_rate)**: `submitter_count / company hc`  

處理邏輯：  
- 以 `company_code` 為 group by 維度  
- 各公司內計算上述 KPI  
- 將 `tflex_applications` 與 `tflex_company_hc` 進行 `company_code` join，取得各公司 hc  

**C. 視覺化建議:**  
- 建議使用「橫向堆疊長條圖（horizontal stacked bar）」，用以同時呈現各公司的「申請數」與「退單數」，並可透過顏色區分絕對量與比率。  
- 若需強調「比率」，可額外提供「橫向 100% 堆疊長條圖（horizontal 100% stacked bar）」，用以清楚顯示退單率與申請數的占比關係。  
- 若為 dashboard 執行摘要場景，建議搭配 KPI 卡片與表格呈現，以利比較各公司整體申請與退單表現。
```
### Phase A · Pipeline (6.2s)
```json
{
  "start_collection": "tflex_applications",
  "pipeline": [
    {
      "$lookup": {
        "from": "tflex_company_hc",
        "localField": "company_code",
        "foreignField": "company_code",
        "as": "company_info"
      }
    },
    {
      "$unwind": {
        "path": "$company_info",
        "preserveNullAndEmptyArrays": true
      }
    },
    {
      "$project": {
        "_id": 0,
        "employee_id": 1,
        "company_code": 1,
        "application_no": 1,
        "review_status": 1,
        "review_result": 1,
        "hc": "$company_info.hc"
      }
    }
  ]
}
```
raw_df: shape=[147526, 6], cols=['application_no', 'company_code', 'employee_id', 'review_result', 'review_status', 'hc']
### Phase B · Preprocess (6.5s)
```python
raw_df['is_return'] = (raw_df['review_status'] == 'Y') & (raw_df['review_result'] == 'N')
raw_df['is_total'] = raw_df['review_status'] == 'Y'

agg = raw_df.groupby('company_code').agg(
    return_count=('is_return', 'sum'),
    total_applications=('application_no', 'size'),
    submitter_count=('employee_id', 'nunique'),
    hc=('hc', 'first'),
).reset_index()

agg['average_return_rate'] = agg['return_count'] / agg['total_applications']
agg['employee_submission_rate'] = agg['submitter_count'] / agg['hc']

Q = agg
```

Q: shape=[15, 7], cols=['company_code', 'return_count', 'total_applications', 'submitter_count', 'hc', 'average_return_rate', 'employee_submission_rate']
### Phase C · ECharts (16.3s)
```python
option = {
    "title": {"text": "申請數 vs 退單率", "left": "center"},
    "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
    "legend": {"show": True, "top": 30},
    "grid": {"left": 60, "right": 60, "top": 70, "bottom": 40},
    "xAxis": {
        "type": "category",
        "data": Q["company_code"].astype(str).tolist(),
    },
    "yAxis": [
        {"type": "value", "name": "申請數",
         "axisLabel": {"formatter": "{value}"}},
        {"type": "value", "name": "退單率",
         "min": 0, "max": 100,
         "axisLabel": {"formatter": "{value}%"}},
    ],
    "series": [
        {"name": "申請數", "type": "bar", "yAxisIndex": 0,
         "data": [int(v) for v in Q["total_applications"].tolist()],
         "label": {"show": True, "position": "top", "formatter": "{c}"}},
        {"name": "退單率", "type": "line", "yAxisIndex": 1,
         "data": (Q["average_return_rate"].round(2).tolist()),
         "label": {"show": True, "position": "top", "formatter": "{c}%"}},
    ],
    "color": [
        "#5470c6", "#91cc75", "#fac858", "#ee6666", "#73c0de",
        "#3ba272", "#fc8452", "#9a60b4", "#ea7ccc", "#5b9bd5",
        "#a5a5a5", "#ffc000", "#7b78de", "#27a39d", "#e15759",
        "#f28e2c", "#76b7b2", "#59a14f", "#edc949", "#b07aa1"
    ]
}
```

option keys: ['title', 'tooltip', 'legend', 'grid', 'xAxis', 'yAxis', 'series', 'color']
### Phase D · Insight (8.8s)
```
**🔑 重點摘要**  
- TST 公司申請量最高（128,922 筆），退單率為 3.32%，顯示其業務規模大但審核品質穩定。  
- TSN 退單率最高（4.99%），申請量達 4,224 筆，需進一步追查審核流程是否需優化。  
- TSC 退單率達 4.02%，申請量高（4,184 筆），為需關注的高風險公司。  
- ESM、TRJ、TSJ 等小規模公司退單率低（<2.5%），但申請量亦相對較少。  
- TST、TSC、TSN 等大公司員工送單率高（>90%），顯示整體參與度良好。

**📌 觀察與建議**  
- 建議針對退單率偏高的公司（如 TSN、TSC）進行審核流程回溯分析，找出可能的共通問題點。  
- 可考慮針對申請量大但退單率低的公司（如 TST）進行最佳實踐萃取，作為其他公司參考。  
- 建議定期監控「平均退單率」與「員工送單率」的變化，作為公司員工參與與審核效率的雙重指標。

**⚠️ 解讀注意事項**  
- 本分析僅能呈現公司層級的申請與退單表現，無法進一步分析員工個人行為或部門差異。  
- 因無時間欄位，無法判斷申請趨勢或季節性變化，建議未來可補充時間維度以支持更深入分析。
```

---

## Case 02 · TST vs TSK 員工送單率(小樣本陷阱)
**Query:** `比較 TST 與 TSK 兩家公司的員工送單率`
**Type:** happy_path · **Status:** pass

### 檢查項
- ✅ Plan 有產出
- ✅ Plan 非空
- ✅ Pipeline 不含禁忌 stage (group/sort/limit/divide/cond)  ·  ✓ 乾淨
- ✅ Pipeline 撈到非空資料  ·  shape=(128929, 3)
- ✅ Phase B exec 成功 (用 1 次嘗試)  ·  1 次過
- ✅ Phase B 有產出 Q
- ✅ Q 含必備欄位 ['company_code']  ·  ✓
- ✅ Phase C exec 成功 (用 3 次嘗試)  ·  retry 3
- ✅ option 含必備 keys ['title', 'xAxis', 'yAxis', 'series']  ·  ✓
- ✅ series 數 ≥ 1  ·  actual: 1
- ✅ Insight 有產出
- ✅ Insight 無禁忌語(或皆為拒絕語境)

### Phase 0 · Plan (8.4s)
```
**A. 資料獲取:**  
- collection: `tflex_applications`, `tflex_company_hc`  
- join 條件: `tflex_applications.company_code = tflex_company_hc.company_code`  
- $match 過濾條件: `company_code` 為 `TST` 或 `TSK`  
- 🔑 **需要的原始欄位 (raw_columns_needed)**:  
  `[employee_id, company_code, hc]`  

**B. 資料處理:**  
1. 篩選出 company_code 為 TST 與 TSK 的資料。  
2. 計算每家公司送單人數 (submitter_count)：`distinct count of employee_id`  
3. 取得公司人力 (hc)：來自 `tflex_company_hc` 的 `hc` 欄位  
4. 計算員工送單率 (employee_submission_rate)：  
   `submitter_count / hc`  

**C. 視覺化建議:**  
- 圖型選擇：橫向長條圖 (horizontal bar chart)  
- 理由：比較兩家公司之間的員工送單率，橫向長條圖易讀性佳，適合用於類別數量少於 7 的場景。  
- orientation：橫向 (horizontal)  
- 建議：可搭配 KPI 卡片呈現各公司送單率數值，並以長條圖視覺化比較。
```
### Phase A · Pipeline (6.2s)
```json
{
  "start_collection": "tflex_applications",
  "pipeline": [
    {
      "$match": {
        "company_code": {
          "$in": [
            "TST",
            "TSK"
          ]
        }
      }
    },
    {
      "$lookup": {
        "from": "tflex_company_hc",
        "localField": "company_code",
        "foreignField": "company_code",
        "as": "company_info"
      }
    },
    {
      "$unwind": {
        "path": "$company_info",
        "preserveNullAndEmptyArrays": true
      }
    },
    {
      "$project": {
        "_id": 0,
        "employee_id": 1,
        "company_code": 1,
        "hc": "$company_info.hc"
      }
    }
  ]
}
```
raw_df: shape=[128929, 3], cols=['company_code', 'employee_id', 'hc']
### Phase B · Preprocess (7.1s)
```python
# 篩選出 company_code 為 TST 與 TSK 的資料
filtered_df = raw_df[raw_df['company_code'].isin(['TST', 'TSK'])].copy()

# 計算每家公司送單人數 (submitter_count)
submitter_count_df = filtered_df.groupby('company_code')['employee_id'].nunique().reset_index()
submitter_count_df.columns = ['company_code', 'submitter_count']

# 取得公司人力 (hc)
hc_df = filtered_df[['company_code', 'hc']].drop_duplicates()

# 合併送單人數與公司人力
merged_df = submitter_count_df.merge(hc_df, on='company_code', how='left')

# 計算員工送單率 (employee_submission_rate)
merged_df['employee_submission_rate'] = merged_df['submitter_count'] / merged_df['hc']

# 最終結果 Q
Q = merged_df[['company_code', 'submitter_count', 'hc', 'employee_submission_rate']]
```

Q: shape=[2, 4], cols=['company_code', 'submitter_count', 'hc', 'employee_submission_rate']
### Phase C · ECharts (26.4s)
```python
option = {
    "title": {"text": "員工送單率比較 (TST vs TSK)", "left": "center"},
    "tooltip": {"trigger": "axis", "axisPointer": {"type": "cross"}},
    "color": ["#5470c6", "#91cc75", "#fac858", "#ee6666", "#73c0de", "#3ba272", "#fc8452", "#9a60b4", "#ea7ccc", "#5b9bd5", "#a5a5a5", "#ffc000", "#7b78de", "#27a39d", "#e15759", "#f28e2c", "#76b7b2", "#59a14f", "#edc949", "#b07aa1"],
    "xAxis": {"type": "value"},
    "yAxis": {"type": "category", "data": Q["company_code"].astype(str).tolist()},
    "series": [
        {
            "name": "員工送單率",
            "type": "bar",
            "data": [(Q['employee_submission_rate'] * 100).round(2).tolist()],
            "label": {"show": True, "position": "right", "formatter": "{c}%"},
        }
    ],
    "grid": {"left": 60, "right": 60, "top": 60, "bottom": 40},
    "legend": {"show": True, "top": 30}
}
```

option keys: ['title', 'tooltip', 'color', 'xAxis', 'yAxis', 'series', 'grid', 'legend']
### Phase D · Insight (7.2s)
```
**🔑 重點摘要**  
- TST 公司員工送單率為 95.16%，顯著高於 TSK 的 100%（實際為 1.0，因 TSK 只有 2 人），顯示 TST 員工參與度高。  
- TST 共有 77,004 位員工提交申請，而 TSK 僅 2 人提交，體量差距極大。  
- TSK 公司人力僅 2 人，導致其送單率計算結果極端，不具代表性。  
- 兩家公司送單率差異主要由體量差異造成，而非送單意願差異。  
- TST 公司整體申請參與度高，可能反映其員工對福利制度的認同與使用意願。

**📌 觀察與建議**  
- 建議針對 TSK 公司進行進一步觀察，確認是否因人力極少導致數據偏移，或是否需調整分析單位（如合併小公司）。  
- 可考慮將「送單人數」與「公司人力」一併呈現，以避免因樣本過小導致誤判。  
- 若未來擴展分析至更多公司，建議加入「公司規模分群」維度，以更準確評估送單行為。

**⚠️ 解讀注意事項**  
- TSK 公司僅有 2 人，導致送單率計算極端，需謹慎看待此數據代表性。  
- 本分析未考慮員工部門、職級等維度，無法進一步探討送單行為差異原因。
```

---

## Case 03 · PAY vs RTN(stacked bar)
**Query:** `畫出各公司的 PAY 與 RTN 申請數量,我想看哪家公司退件量最大`
**Type:** happy_path · **Status:** pass

### 檢查項
- ✅ Plan 有產出
- ✅ Plan 非空
- ✅ Pipeline 不含禁忌 stage (group/sort/limit/divide/cond)  ·  ✓ 乾淨
- ✅ Pipeline 撈到非空資料  ·  shape=(147526, 8)
- ✅ Phase B exec 成功 (用 1 次嘗試)  ·  1 次過
- ✅ Phase B 有產出 Q
- ✅ Q 含必備欄位 ['(pay_count | PAY | pay)', '(return_count | RTN | rtn | RET | rtn_count | return_cnt)', 'company_code']  ·  ✓
- ✅ Phase C exec 成功 (用 1 次嘗試)  ·  1 次過
- ✅ option 含必備 keys ['title', 'xAxis', 'yAxis', 'series']  ·  ✓
- ✅ series 數 ≥ 2  ·  actual: 2
- ✅ series 帶 stack 屬性
- ✅ Insight 有產出
- ✅ Insight 無禁忌語(或皆為拒絕語境)

### Phase 0 · Plan (9.8s)
```
**A. 資料獲取:**  
- 起手 collection: `tflex_applications`  
- 需要 join 的表: `tflex_company_hc` (用於取得公司人力資本 H/C，但此需求僅關注 PAY 與 RTN 數量，不需用到 H/C)  
- 需要的 $match 過濾條件: 無特定過濾條件，需計算所有 completed applications 的 PAY 與 RTN 數量  
  - 🔑 **需要的原始欄位 (raw_columns_needed)**:  
    `[employee_id, company_code, review_status, review_result]`  

**B. 資料處理:**  
- 依 `company_code` 分組計算：
  - `PAY` (pay_count): 統計 `review_status='Y'` 且 `review_result='Y'` 的申請數量  
  - `RTN` (return_count): 統計 `review_status='Y'` 且 `review_result='N'` 的申請數量  
- 計算結果需包含公司代碼與對應的 PAY 與 RTN 數量  

**C. 視覺化建議:**  
- 圖型選擇：**橫向長條圖 (horizontal bar chart)**  
- 理由：  
  - 題目明確要求「看哪家公司退件量最大」，適合用長條圖比較各公司 RTN 數量  
  - 橫向排列可讀性佳，尤其當公司數量較多時  
  - 若需同時呈現 PAY 與 RTN，可考慮使用「橫向堆疊長條圖 (horizontal stacked bar)」以利比較  
  - 若僅關注 RTN 大小，則使用單一橫向長條圖即可  

✅ 此查詢未觸發任何 data_limitations，且所有欄位皆存在於 schema 中，符合執行條件。
```
### Phase A · Pipeline (6.5s)
```json
{
  "start_collection": "tflex_applications",
  "pipeline": [
    {
      "$lookup": {
        "from": "tflex_company_hc",
        "localField": "company_code",
        "foreignField": "company_code",
        "as": "company_info"
      }
    },
    {
      "$unwind": {
        "path": "$company_info",
        "preserveNullAndEmptyArrays": true
      }
    },
    {
      "$project": {
        "_id": 0,
        "employee_id": 1,
        "company_code": 1,
        "application_no": 1,
        "application_category": 1,
        "review_status": 1,
        "review_result": 1,
        "review_mechanism": 1,
        "hc": "$company_info.hc"
      }
    }
  ]
}
```
raw_df: shape=[147526, 8], cols=['application_no', 'application_category', 'company_code', 'employee_id', 'review_mechanism', 'review_result', 'review_status', 'hc']
### Phase B · Preprocess (5.3s)
```python
Q = (raw_df.groupby('company_code')
        .agg(
            pay_count=('review_result', lambda x: (x == 'Y').sum()),
            return_count=('review_result', lambda x: (x == 'N').sum()),
        )
        .reset_index())
```

Q: shape=[15, 3], cols=['company_code', 'pay_count', 'return_count']
### Phase C · ECharts (14.4s)
```python
option = {
    "title": {"text": "各公司 PAY 與 RTN 申請數量", "left": "center"},
    "tooltip": {"trigger": "axis", "axisPointer": {"type": "cross"}},
    "legend": {"show": True, "top": 30},
    "color": ["#5470c6", "#91cc75", "#fac858", "#ee6666", "#73c0de", "#3ba272", "#fc8452", "#9a60b4", "#ea7ccc", "#5b9bd5", "#a5a5a5", "#ffc000", "#7b78de", "#27a39d", "#e15759", "#f28e2c", "#76b7b2", "#59a14f", "#edc949", "#b07aa1"],
    "grid": {"left": 60, "right": 60, "top": 60, "bottom": 40},
    "xAxis": {"type": "value"},
    "yAxis": {"type": "category", "data": Q["company_code"].astype(str).tolist()},
    "series": [
        {
            "name": "pay_count",
            "type": "bar",
            "stack": "total",
            "data": [int(v) for v in Q["pay_count"].tolist()],
            "label": {"show": True, "position": "right", "formatter": "{c}"},
        },
        {
            "name": "return_count",
            "type": "bar",
            "stack": "total",
            "data": [int(v) for v in Q["return_count"].tolist()],
            "label": {"show": True, "position": "right", "formatter": "{c}"},
        }
    ]
}
```

option keys: ['title', 'tooltip', 'legend', 'color', 'grid', 'xAxis', 'yAxis', 'series']
### Phase D · Insight (8.4s)
```
**🔑 重點摘要**  
- TST 公司的 PAY 總數最高（114,744 筆），RTN 總數也最大（4,285 筆），顯示其申請量龐大且退件問題明顯。  
- JSM 公司退件數量（139 筆）為所有公司中第二高，需進一步關注其審核流程。  
- TSA、TSC、TSN 三家公司退件比例相對較高，尤其是 TSN（退件率約 6.06%），需深入分析原因。  
- ESM、TRJ、TSJ 等公司退件數量極低（< 3 筆），顯示審核流程效率高或申請品質佳。  
- TST 與 TSN 的申請量龐大，但 RTN 數量也高，反映其業務規模與審核壓力皆大。

**📌 觀察與建議**  
- 建議針對 RTN 數量前三大公司（TST、TSN、JSM）進行審核流程檢視，找出退件高發原因，優化審核標準或流程。  
- 可建立「退件率趨勢追蹤」指標，定期監控各公司 RTN 率變化，以預防潛在風險。  
- 建議將 PAY 與 RTN 數量結合分析，觀察是否高申請量對應高退件率，以評估整體審核效能。

**⚠️ 解讀注意事項**  
- 本分析僅依據完成申請（review_status='Y'）計算 PAY 與 RTN 數量，未包含進行中申請，避免對結果造成誤導。  
- 由於無時間欄位，無法判斷退件是否集中於特定時期，建議後續可結合時間維度進行更細緻分析。
```

---

## Case 04 · 各公司 AI 審查率(vs 43% 目標)
**Query:** `哪些公司的 AI 審查率比較高?跟 43% 的目標比起來如何?`
**Type:** happy_path · **Status:** pass

### 檢查項
- ✅ Plan 有產出
- ✅ Plan 非空
- ✅ Pipeline 不含禁忌 stage (group/sort/limit/divide/cond)  ·  ✓ 乾淨
- ✅ Pipeline 撈到非空資料  ·  shape=(147526, 8)
- ✅ Phase B exec 成功 (用 1 次嘗試)  ·  1 次過
- ✅ Phase B 有產出 Q
- ✅ Q 含必備欄位 ['company_code']  ·  ✓
- ✅ Phase C exec 成功 (用 3 次嘗試)  ·  retry 3
- ✅ option 含必備 keys ['title', 'xAxis', 'yAxis', 'series']  ·  ✓
- ✅ series 數 ≥ 1  ·  actual: 2
- ✅ Insight 有產出
- ✅ Insight 無禁忌語(或皆為拒絕語境)

### Phase 0 · Plan (9.2s)
```
**A. 資料獲取:**  
- collection: `tflex_applications`  
- join: `tflex_company_hc` (用 `company_code` 關聯)  
- $match 過濾條件: 無  
- 🔑 **需要的原始欄位 (raw_columns_needed)**:  
  `[employee_id, company_code, review_status, review_result, review_mechanism]`  

**B. 資料處理:**  
1. **計算各公司 AI 審查率** (AI 審查率 = `count(review_status='Y' and review_mechanism='AI') / completed_count`)  
2. **完成件數 (completed_count)** = `count(review_status='Y')`  
3. **AI 審查件數** = `count(review_status='Y' and review_mechanism='AI')`  
4. **AI 審查率** = `AI 審查件數 / completed_count`  
5. **與 43% 目標比較**：將各公司 AI 審查率與 0.43 比較，標示是否達標  

**C. 視覺化建議:**  
- 圖型選擇：**橫向長條圖 (horizontal bar chart)**  
- 理由：  
  - 公司數量較多時，橫向長條圖可讀性佳  
  - 可清楚比較各公司 AI 審查率與 43% 目標的差異  
  - query 明確點名「比較」，適合用 bar chart 呈現差異  
  - 若公司數 ≤ 7 且 query 明確點名 pie chart，也可考慮 pie chart，但此處建議用 bar 以利比較
```
### Phase A · Pipeline (6.4s)
```json
{
  "start_collection": "tflex_applications",
  "pipeline": [
    {
      "$lookup": {
        "from": "tflex_company_hc",
        "localField": "company_code",
        "foreignField": "company_code",
        "as": "company_info"
      }
    },
    {
      "$unwind": {
        "path": "$company_info",
        "preserveNullAndEmptyArrays": true
      }
    },
    {
      "$project": {
        "_id": 0,
        "employee_id": 1,
        "company_code": 1,
        "application_no": 1,
        "application_category": 1,
        "review_status": 1,
        "review_result": 1,
        "review_mechanism": 1,
        "hc": "$company_info.hc"
      }
    }
  ]
}
```
raw_df: shape=[147526, 8], cols=['application_no', 'application_category', 'company_code', 'employee_id', 'review_mechanism', 'review_result', 'review_status', 'hc']
### Phase B · Preprocess (7.4s)
```python
# Step 1: 計算各公司完成件數與AI審查件數
agg = raw_df.groupby('company_code').agg(
    completed_count=('review_status', 'size'),
    ai_review_count=('review_mechanism', lambda x: (x == 'AI').sum()),
).reset_index()

# Step 2: 計算 AI 審查率
agg['ai_review_rate'] = agg['ai_review_count'] / agg['completed_count']

# Step 3: 與 43% 目標比較
agg['target_met'] = agg['ai_review_rate'] >= 0.43

# Step 4: 加入公司人數 (hc) 供後續分析使用
agg = agg.merge(raw_df[['company_code', 'hc']].drop_duplicates(), on='company_code', how='left')

# Step 5: 最終結果 Q
Q = agg[['company_code', 'ai_review_rate', 'target_met', 'hc']]
```

Q: shape=[15, 4], cols=['company_code', 'ai_review_rate', 'target_met', 'hc']
### Phase C · ECharts (30.2s)
```python
option = {
    "title": {"text": "各公司 AI 審查率比較", "left": "center"},
    "tooltip": {"trigger": "axis", "axisPointer": {"type": "cross"}},
    "color": ["#5470c6", "#91cc75", "#fac858", "#ee6666", "#73c0de", "#3ba272", "#fc8452", "#9a60b4", "#ea7ccc", "#5b9bd5", "#a5a5a5", "#ffc000", "#7b78de", "#27a39d", "#e15759", "#f28e2c", "#76b7b2", "#59a14f", "#edc949", "#b07aa1"],
    "legend": {"show": True, "top": 30},
    "grid": {"left": 60, "right": 60, "top": 60, "bottom": 40},
    "xAxis": {"type": "value", "axisLabel": {"formatter": "{value}%"}},
    "yAxis": {"type": "category", "data": Q["company_code"].astype(str).tolist()},
    "series": [
        {
            "name": "AI 審查率",
            "type": "bar",
            "data": (Q["ai_review_rate"] * 100).round(2).tolist(),
            "label": {"show": True, "position": "right", "formatter": "{c}%"},
        },
        {
            "name": "目標 43%",
            "type": "bar",
            "data": [43.0] * len(Q),
            "label": {"show": True, "position": "left", "formatter": "目標 43%"},
        }
    ]
}
```

option keys: ['title', 'tooltip', 'color', 'legend', 'grid', 'xAxis', 'yAxis', 'series']
### Phase D · Insight (8.7s)
```
**🔑 重點摘要**  
- 所有公司 AI 審查率皆低於 43% 目標，其中 TSK 公司因樣本極少（僅 2 人），AI 審查率僅 14.3%。  
- TST 公司 AI 審查率最高（39.7%），但仍未達標，顯示整體 AI 審查推廣仍有空間。  
- ESM、JSM、TDC 等公司 AI 審查率介於 31.9% 至 37.5%，顯示這些公司審核流程中 AI 使用程度偏低。  
- 各公司 AI 審查率普遍偏低，顯示整體系統自動化審核應用程度不足。  
- 公司規模與 AI 審查率無明顯正相關，小規模公司如 TSK、TSJ 亦未達標。

**📌 觀察與建議**  
- 建議針對 AI 審查率低於 35% 的公司（如 ESM、JSM、TDC）進行審核流程檢視，確認是否因系統設定或流程設計限制導致 AI 使用率低。  
- 建議設定內部 AI 審查率改善目標，並定期追蹤進度，例如每季檢視一次各公司 AI 審查率變化。  
- 建議針對 TST、TSC 等高送單量公司，進一步分析其 AI 審查率未達標原因，是否為流程設計或資源分配問題。

**⚠️ 解讀注意事項**  
- TSK 公司僅有 2 名員工，AI 審查率僅 14.3%，屬極小樣本，不具代表性，需特別標註。  
- 無法進行時間趨勢分析或部門比較，資料限制導致無法深入探討 AI 審查率變動原因。
```

---

## Case 05 · AI vs Human review by 公司(grouped 不堆疊)
**Query:** `看一下每家公司在 AI 審查跟人工審查的件數分佈,我想找出還是高度依賴人工的公司`
**Type:** happy_path · **Status:** pass

### 檢查項
- ✅ Plan 有產出
- ✅ Plan 非空
- ✅ Pipeline 不含禁忌 stage (group/sort/limit/divide/cond)  ·  ✓ 乾淨
- ✅ Pipeline 撈到非空資料  ·  shape=(147526, 7)
- ✅ Phase B exec 成功 (用 1 次嘗試)  ·  1 次過
- ✅ Phase B 有產出 Q
- ✅ Q 含必備欄位 ['company_code']  ·  ✓
- ✅ Phase C exec 成功 (用 1 次嘗試)  ·  1 次過
- ✅ option 含必備 keys ['title', 'xAxis', 'yAxis', 'series']  ·  ✓
- ✅ series 數 ≥ 2  ·  actual: 2
- ✅ Insight 有產出
- ✅ Insight 無禁忌語(或皆為拒絕語境)

### Phase 0 · Plan (9.7s)
```
**A. 資料獲取:**  
- collection: `tflex_applications`  
- join: 無需 join，所有 needed fields 均在 `tflex_applications`  
- $match: 無需過濾條件  
  - 🔑 **需要的原始欄位 (raw_columns_needed)**:  
    `[company_code, review_status, review_mechanism]`  

**B. 資料處理:**  
1. 篩選 `review_status = 'Y'`（僅計算已完成的申請）  
2. 按 `company_code` 分組  
3. 計算每家公司中：
   - AI 審查件數：`count where review_mechanism = 'AI'`  
   - 人工審查件數：`count where review_mechanism = 'H'`  
4. 計算各公司 AI 審查占比：`AI_count / (AI_count + H_count)`  
5. 找出 AI 審查占比最低的公司（即高度依賴人工的公司）

**C. 視覺化建議:**  
- 圖型選擇：**橫向堆疊長條圖**（horizontal stacked bar chart）  
- 理由：  
  - 類別為公司（company_code），數量不多（<15 家），適合用 bar chart  
  - 橫向排列便於比較各公司之間的 AI vs 人工審查比例  
  - 可清楚看出哪些公司 AI 審查占比低，即高度依賴人工  
  - 若需強調「占比」，可考慮「橫向 100% 堆疊長條圖」（horizontal 100% stacked bar chart）  

> 🎯 orientation 鐵律：query 明確點名「橫向」，因此視覺化建議中必須保留「橫向」關鍵字。
```
### Phase A · Pipeline (5.3s)
```json
{
  "start_collection": "tflex_applications",
  "pipeline": [
    {
      "$project": {
        "_id": 0,
        "employee_id": 1,
        "company_code": 1,
        "application_no": 1,
        "application_category": 1,
        "review_status": 1,
        "review_result": 1,
        "review_mechanism": 1
      }
    }
  ]
}
```
raw_df: shape=[147526, 7], cols=['application_no', 'application_category', 'company_code', 'employee_id', 'review_mechanism', 'review_result', 'review_status']
### Phase B · Preprocess (6.8s)
```python
# 篩選已完成的申請
filtered_df = raw_df[raw_df['review_status'] == 'Y'].copy()

# 按公司分組，計算 AI 與人工審查件數
agg = filtered_df.groupby('company_code').agg(
    ai_count=('review_mechanism', lambda x: (x == 'AI').sum()),
    h_count=('review_mechanism', lambda x: (x == 'H').sum()),
).reset_index()

# 計算 AI 審查占比
agg['ai_ratio'] = agg['ai_count'] / (agg['ai_count'] + agg['h_count'])

# 將結果按 AI 審查占比排序（由低到高），找出高度依賴人工的公司
Q = agg.sort_values('ai_ratio').reset_index(drop=True)
```

Q: shape=[15, 4], cols=['company_code', 'ai_count', 'h_count', 'ai_ratio']
### Phase C · ECharts (14.9s)
```python
option = {
    "title": {"text": "各公司 AI 審查 vs 人工審查件數分佈", "left": "center"},
    "tooltip": {"trigger": "axis", "axisPointer": {"type": "cross"}},
    "legend": {"show": True, "top": 30},
    "color": ["#5470c6", "#91cc75", "#fac858", "#ee6666", "#73c0de", "#3ba272", "#fc8452", "#9a60b4", "#ea7ccc", "#5b9bd5", "#a5a5a5", "#ffc000", "#7b78de", "#27a39d", "#e15759", "#f28e2c", "#76b7b2", "#59a14f", "#edc949", "#b07aa1"],
    "xAxis": {"type": "value"},
    "yAxis": {"type": "category", "data": Q["company_code"].astype(str).tolist()},
    "series": [
        {
            "name": "AI 審查件數",
            "type": "bar",
            "stack": "total",
            "data": [int(v) for v in Q["ai_count"].tolist()],
            "label": {"show": True, "position": "right", "formatter": "{c}"},
        },
        {
            "name": "人工審查件數",
            "type": "bar",
            "stack": "total",
            "data": [int(v) for v in Q["h_count"].tolist()],
            "label": {"show": True, "position": "right", "formatter": "{c}"},
        },
    ],
    "grid": {"left": 60, "right": 60, "top": 60, "bottom": 40},
}
```

option keys: ['title', 'tooltip', 'legend', 'color', 'xAxis', 'yAxis', 'series', 'grid']
### Phase D · Insight (8.2s)
```
**🔑 重點摘要**  
- AI 審查件數佔比最低的公司為 TST（43.00%），顯示其高度依賴人工審查。  
- TSA、TWT、TDI、TSU 等公司 AI 審查占比約 43%，亦屬人工導向較高的公司。  
- TRJ 公司 AI 審查占比僅 42.72%，為所有公司中最低，顯示其審查流程仍以人工為主。  
- 整體而言，超過半數公司 AI 審查占比低於 43.5%，顯示 tFlex 系統在審查自動化方面仍有提升空間。  
- AI 審查占比最高的公司為 TSJ（43.66%），顯示其在審查自動化方面較為進階。

**📌 觀察與建議**  
- 建議針對 AI 審查占比低的公司（如 TST、TRJ）進行審查流程檢視，評估是否可提升 AI 審查比例以提高效率。  
- 可進一步分析 AI 審查與人工審查的處理時間差異，以量化自動化效益。  
- 建議設定 AI 審查占比目標值，作為未來審查流程自動化進展的追蹤指標。

**⚠️ 解讀注意事項**  
- 本分析僅針對已完成申請（review_status='Y'）進行，未包含進行中申請，因此結果為完成件的 AI 審查分布。  
- 結果顯示部分公司 AI 審查件數極少（如 TSK、TSE），可能因樣本數不足導致比例偏高，應避免過度推論。
```

---

## Case 06 · 各公司審核完成率排序
**Query:** `排序各公司的審核完成率,看誰積壓最嚴重`
**Type:** happy_path · **Status:** pass

### 檢查項
- ✅ Plan 有產出
- ✅ Plan 非空
- ✅ Pipeline 不含禁忌 stage (group/sort/limit/divide/cond)  ·  ✓ 乾淨
- ✅ Pipeline 撈到非空資料  ·  shape=(147526, 7)
- ✅ Phase B exec 成功 (用 1 次嘗試)  ·  1 次過
- ✅ Phase B 有產出 Q
- ✅ Q 含必備欄位 ['company_code']  ·  ✓
- ✅ Phase C exec 成功 (用 3 次嘗試)  ·  retry 3
- ✅ option 含必備 keys ['title', 'xAxis', 'yAxis', 'series']  ·  ✓
- ✅ series 數 ≥ 1  ·  actual: 1
- ✅ Insight 有產出
- ✅ Insight 無禁忌語(或皆為拒絕語境)

### Phase 0 · Plan (9.6s)
```
**A. 資料獲取:**  
- 起手 collection: `tflex_applications`、`tflex_company_hc`  
- 需要 join 的表: `tflex_applications.company_code` → `tflex_company_hc.company_code`  
- 需要的 $match 過濾條件: 無（需計算所有資料）  
  - 🔑 **需要的原始欄位 (raw_columns_needed)**:  
    `[employee_id, company_code, application_no, review_status, review_result, review_mechanism]`  

**B. 資料處理:**  
1. **計算各公司完成率 (completion_rate)**：  
   - `completed_count` = count where `review_status='Y'`  
   - `total_applications` = count of all documents  
   - `completion_rate` = `completed_count / total_applications`  
2. **Join 公司人數 (hc)**：  
   - 使用 `company_code` 關聯 `tflex_company_hc` 以取得 `hc`  
3. **排序**：  
   - 按照 `completion_rate` 升冪排序（完成率越低代表積壓越嚴重）  

**C. 視覺化建議:**  
- 圖型選擇：**橫向長條圖 (horizontal bar chart)**  
- 理由：  
  - 需要比較「各公司」的完成率，橫向長條圖便於比較不同公司間的數值差異。  
  - 「排序」需求強調「誰積壓最嚴重」，橫向排列更易讀，適合用於 dashboard 場景。  
  - 若公司數量超過 7 家，橫向長條圖仍具可讀性，且符合「排序」的直覺。
```
### Phase A · Pipeline (6.6s)
```json
{
  "start_collection": "tflex_applications",
  "pipeline": [
    {
      "$lookup": {
        "from": "tflex_company_hc",
        "localField": "company_code",
        "foreignField": "company_code",
        "as": "company_info"
      }
    },
    {
      "$unwind": {
        "path": "$company_info",
        "preserveNullAndEmptyArrays": true
      }
    },
    {
      "$project": {
        "_id": 0,
        "employee_id": 1,
        "company_code": 1,
        "application_no": 1,
        "review_status": 1,
        "review_result": 1,
        "review_mechanism": 1,
        "hc": "$company_info.hc"
      }
    }
  ]
}
```
raw_df: shape=[147526, 7], cols=['application_no', 'company_code', 'employee_id', 'review_mechanism', 'review_result', 'review_status', 'hc']
### Phase B · Preprocess (6.1s)
```python
raw_df['completed_count'] = (raw_df['review_status'] == 'Y').astype(int)
raw_df['total_applications'] = 1

agg = raw_df.groupby('company_code').agg(
    completed_count=('completed_count', 'sum'),
    total_applications=('total_applications', 'sum'),
    hc=('hc', 'first'),
).reset_index()

agg['completion_rate'] = agg['completed_count'] / agg['total_applications']

Q = agg[['company_code', 'completion_rate']].sort_values('completion_rate', ascending=True)
```

Q: shape=[15, 2], cols=['company_code', 'completion_rate']
### Phase C · ECharts (27.4s)
```python
option = {
    "title": {"text": "各公司審核完成率（由低到高排序）", "left": "center"},
    "tooltip": {"trigger": "axis", "axisPointer": {"type": "cross"}},
    "color": ["#5470c6", "#91cc75", "#fac858", "#ee6666", "#73c0de", "#3ba272", "#fc8452", "#9a60b4", "#ea7ccc", "#5b9bd5", "#a5a5a5", "#ffc000", "#7b78de", "#27a39d", "#e15759", "#f28e2c", "#76b7b2", "#59a14f", "#edc949", "#b07aa1"],
    "xAxis": {"type": "value"},
    "yAxis": {"type": "category", "data": Q['company_code'].astype(str).tolist()},
    "series": [
        {
            "name": "completion_rate",
            "type": "bar",
            "data": [(Q['completion_rate'] * 100).round(2).tolist()],
            "label": {"show": True, "position": "right", "formatter": "{c}%"},
        }
    ],
    "grid": {"left": 60, "right": 60, "top": 60, "bottom": 40}
}
```

option keys: ['title', 'tooltip', 'color', 'xAxis', 'yAxis', 'series', 'grid']
### Phase D · Insight (7.8s)
```
**🔑 重點摘要**  
- TSK 公司審核完成率最低（42.86%），顯示其申請處理積壓最嚴重。  
- TDI 公司完成率僅 74.28%，遠低於其他公司，需關注其審核流程效率。  
- TSC 與 TST 公司完成率高達 91.97% 與 92.33%，顯示其審核流程相對順暢。  
- 整體而言，完成率介於 42.86% 至 98.04% 之間，顯示各公司間審核效率差異明顯。  
- 完成率低的公司（如 TSK、TDI）應優先列入審核流程改善對象。

**📌 觀察與建議**  
- 建議針對完成率低於 80% 的公司（如 TSK、TDI）進行審核流程檢視，找出積壓原因。  
- 可考慮對完成率較低的公司增加審核資源或優化 AI 審查機制以提升效率。  
- 建立定期監控機制，追蹤各公司完成率變化，及早發現潛在問題。

**⚠️ 解讀注意事項**  
- 本分析僅基於現有資料，未考慮時間趨勢或申請類型差異，可能無法反映真實業務狀況。  
- 完成率低的公司可能因申請量少或特殊申請類型導致數值偏高或偏低，需進一步細分觀察。
```

---

## Case 07 · 申請類別分布(非公司維度)
**Query:** `四個福利申請類別,哪個最熱門?`
**Type:** happy_path · **Status:** pass

### 檢查項
- ✅ Plan 有產出
- ✅ Plan 非空
- ✅ Pipeline 不含禁忌 stage (group/sort/limit/divide/cond)  ·  ✓ 乾淨
- ✅ Pipeline 撈到非空資料  ·  shape=(147526, 7)
- ✅ Phase B exec 成功 (用 1 次嘗試)  ·  1 次過
- ✅ Phase B 有產出 Q
- ✅ Q 含必備欄位 ['application_category']  ·  ✓
- ✅ Phase C exec 成功 (用 1 次嘗試)  ·  1 次過
- ✅ option 含必備 keys ['title', 'xAxis', 'yAxis', 'series']  ·  ✓
- ✅ series 數 ≥ 1  ·  actual: 1
- ✅ Insight 有產出
- ✅ Insight 無禁忌語(或皆為拒絕語境)

### Phase 0 · Plan (8.3s)
```
**A. 資料獲取:**  
- 起手 collection: `tflex_applications`  
- 需要的原始欄位 (raw_columns_needed): `[application_category, review_status]`  
  - `application_category` 用於分類統計  
  - `review_status` 用於過濾已完成的申請（`review_status='Y'`）以計算熱門程度  

**B. 資料處理:**  
- 計算每個 `application_category` 的申請數量（僅計算 `review_status='Y'` 的完成件）  
- 熱門程度 = 各類別完成申請數量的總和（因無金額與時間維度，無法做趨勢或平均分析）  

**C. 視覺化建議:**  
- 建議使用「橫向長條圖（horizontal bar chart）」呈現各類別申請數量  
  - 理由：類別數量少於 7，且 query 明確要求比較「熱門程度」，橫向長條圖易讀性佳  
  - 若使用者明確點名「圓餅圖」，也可使用「橫向圓餅圖（horizontal pie chart）」，但建議優先使用 bar chart 以提升可讀性
```
### Phase A · Pipeline (5.5s)
```json
{
  "start_collection": "tflex_applications",
  "pipeline": [
    {
      "$project": {
        "_id": 0,
        "employee_id": 1,
        "company_code": 1,
        "application_no": 1,
        "application_category": 1,
        "review_status": 1,
        "review_result": 1,
        "review_mechanism": 1
      }
    }
  ]
}
```
raw_df: shape=[147526, 7], cols=['application_no', 'application_category', 'company_code', 'employee_id', 'review_mechanism', 'review_result', 'review_status']
### Phase B · Preprocess (5.2s)
```python
Q = (raw_df[raw_df['review_status'] == 'Y']
     .groupby('application_category')
     .agg(
         total_applications=('application_no', 'size')
     )
     .reset_index()
     .sort_values('total_applications', ascending=False))
```

Q: shape=[4, 2], cols=['application_category', 'total_applications']
### Phase C · ECharts (13.7s)
```python
option = {
    "title": {"text": "各福利申請類別申請數量", "left": "center"},
    "tooltip": {"trigger": "axis", "axisPointer": {"type": "cross"}},
    "color": ["#5470c6", "#91cc75", "#fac858", "#ee6666", "#73c0de", "#3ba272", "#fc8452", "#9a60b4", "#ea7ccc", "#5b9bd5", "#a5a5a5", "#ffc000", "#7b78de", "#27a39d", "#e15759", "#f28e2c", "#76b7b2", "#59a14f", "#edc949", "#b07aa1"],
    "xAxis": {"type": "value"},
    "yAxis": {"type": "category", "data": Q["application_category"].astype(str).tolist()},
    "series": [
        {
            "name": "total_applications",
            "type": "bar",
            "data": [int(v) for v in Q["total_applications"].tolist()],
            "label": {"show": True, "position": "right", "formatter": "{c}"},
        }
    ],
    "grid": {"left": 60, "right": 30, "top": 60, "bottom": 40}
}
```

option keys: ['title', 'tooltip', 'color', 'xAxis', 'yAxis', 'series', 'grid']
### Phase D · Insight (7.3s)
```
**🔑 重點摘要**  
- 「醫療保險類」申請量最高，達 46,083 筆，明顯領先其他類別。  
- 「家庭照護類」次之，有 36,381 筆申請，顯示員工對家庭相關福利需求高。  
- 「健康促進類」申請量為 33,545 筆，反映員工對個人健康關注度。  
- 「發展與志願服務類」申請量最少，僅 19,267 筆，可能因參與門檻或認知度較低。  
- 整體完成申請占比約 85%，顯示審核流程整體運作順暢。

**📌 觀察與建議**  
- 建議針對「醫療保險類」申請量高的原因進行深入分析，是否與政策調整或宣導有關，並可考慮優化此類別的申請流程以提升效率。  
- 可針對「發展與志願服務類」設計專屬宣導或激勵措施，提升員工參與意願。  
- 建議定期追蹤各類別申請量變化，作為未來福利政策規劃的參考依據。

**⚠️ 解讀注意事項**  
- 本分析僅依據完成申請（review_status='Y'）進行統計，未包含進行中申請，因此結果為「已完成」的熱門程度。  
- 無時間維度資料，無法判斷申請趨勢或季節性變化，建議後續補充時間欄位以進行更深入分析。
```

---

## Case 08 · 公司 × 類別 熱力圖(壓力測試)
**Query:** `畫一張熱力圖,看不同公司在四個申請類別的分佈差異`
**Type:** happy_path · **Status:** pass

### 檢查項
- ✅ Plan 有產出
- ✅ Plan 非空
- ✅ Pipeline 不含禁忌 stage (group/sort/limit/divide/cond)  ·  ✓ 乾淨
- ✅ Pipeline 撈到非空資料  ·  shape=(147526, 8)
- ✅ Phase B exec 成功 (用 1 次嘗試)  ·  1 次過
- ✅ Phase B 有產出 Q
- ✅ Q 含必備欄位 []  ·  ✓
- ✅ Phase C exec 成功 (用 3 次嘗試)  ·  retry 3
- ✅ option 含必備 keys ['title', 'xAxis', 'yAxis', 'series']  ·  ✓
- ✅ series 數 ≥ 1  ·  actual: 1
- ✅ 含 visualMap (heatmap 用)
- ✅ Insight 有產出
- ✅ Insight 無禁忌語(或皆為拒絕語境)

### Phase 0 · Plan (9.8s)
```
**A. 資料獲取:**  
- 起手 collection: `tflex_applications`  
- 需要 join 的表: `tflex_company_hc` (用於取得公司人力資訊，但此需求僅需計算各公司申請類別分布，不需實際使用 hc 數值)  
- 需要的 $match 過濾條件: 無特定過濾條件  
  - 🔑 **需要的原始欄位 (raw_columns_needed)**:  
    `[company_code, application_category]`  

**B. 資料處理:**  
- 計算各公司於四個申請類別的申請筆數分布  
  - 以 `company_code` 為橫軸，`application_category` 為縱軸  
  - 計算每組 `(company_code, application_category)` 的申請筆數  
  - 結果為一個交叉表（pivot table）格式，用於熱力圖繪製  

**C. 視覺化建議:**  
- 圖型選擇：熱力圖（heatmap）  
- 理由：熱力圖適合呈現兩個類別維度之間的頻率或數量分布差異，能清楚顯示不同公司在各申請類別上的分布狀況。  
- 若 query 明確提到「橫向」、「水平」等方向性詞彙，則建議使用「橫向熱力圖」或「水平熱力圖」以符合使用者意圖。  
  - 例如：「橫向熱力圖」或「水平熱力圖」  

✅ 此需求未觸犯任何 data_limitations，且所有欄位皆存在於 schema 中，因此可執行。
```
### Phase A · Pipeline (6.9s)
```json
{
  "start_collection": "tflex_applications",
  "pipeline": [
    {
      " $lookup": {
        "from": "tflex_company_hc",
        "localField": "company_code",
        "foreignField": "company_code",
        "as": "company_info"
      }
    },
    {
      " $unwind": {
        "path": "$company_info",
        "preserveNullAndEmptyArrays": true
      }
    },
    {
      " $project": {
        "_id": 0,
        "employee_id": 1,
        "company_code": 1,
        "application_no": 1,
        "application_category": 1,
        "review_status": 1,
        "review_result": 1,
        "review_mechanism": 1,
        "hc": "$company_info.hc"
      }
    }
  ]
}
```
raw_df: shape=[147526, 8], cols=['application_no', 'application_category', 'company_code', 'employee_id', 'review_mechanism', 'review_result', 'review_status', 'hc']
### Phase B · Preprocess (4.9s)
```python
Q = (raw_df.groupby(['company_code', 'application_category'])
        .size()
        .reset_index(name='count')
     )
```

Q: shape=[59, 3], cols=['company_code', 'application_category', 'count']
### Phase C · ECharts (38.9s)
```python
option = {
    "title": {"text": "不同公司在四個申請類別的分佈差異", "left": "center"},
    "tooltip": {"trigger": "item"},
    "color": ["#5470c6", "#91cc75", "#fac858", "#ee6666", "#73c0de", "#3ba272", "#fc8452", "#9a60b4", "#ea7ccc", "#5b9bd5", "#a5a5a5", "#ffc000", "#7b78de", "#27a39d", "#e15759", "#f28e2c", "#76b7b2", "#59a14f", "#edc949", "#b07aa1"],
    "grid": {"left": 80, "right": 80, "top": 60, "bottom": 80},
    "xAxis": {
        "type": "category",
        "data": Q["company_code"].unique().tolist(),
        "splitArea": {"show": True}
    },
    "yAxis": {
        "type": "category",
        "data": Q["application_category"].unique().tolist(),
        "splitArea": {"show": True}
    },
    "visualMap": {
        "min": float(Q["count"].min()),
        "max": float(Q["count"].max()),
        "calculable": True,
        "orient": "horizontal",
        "left": "center",
        "bottom": 20,
        "inRange": {
            "color": ["#e6f1fb", "#85b7eb", "#185fa5", "#0c447c"]
        }
    },
    "series": [
        {
            "name": "申請筆數",
            "type": "heatmap",
            "data": [
                [str(row["company_code"]), str(row["application_category"]), float(row["count"])]
                for _, row in Q.iterrows()
            ],
            "label": {"show": True, "formatter": "{c}"},
            "emphasis": {
                "itemStyle": {
                    "shadowBlur": 10,
                    "shadowColor": "rgba(0,0,0,0.5)"
                }
            }
        }
    ]
}
```

option keys: ['title', 'tooltip', 'color', 'grid', 'xAxis', 'yAxis', 'visualMap', 'series']
### Phase D · Insight (9.5s)
```
**🔑 重點摘要**  
- ESM 公司在各申請類別的申請量相對較少，其中以「Medical & Insurance」最多（33 筆），顯示其員工對醫療保險相關福利需求較高。  
- JSM 公司申請量明顯高於其他公司，尤其在「Family Care」與「Medical & Insurance」類別上申請數分別達 1,071 與 1,362 筆，顯示該公司員工對家庭照護與醫療保險需求強烈。  
- TDI 公司在「Wellness」類別的申請量最高（173 筆），顯示該公司員工對健康促進類福利較為重視。  
- TDC 公司申請量最少，各類別申請數均低於 30 筆，顯示其員工參與度較低或福利使用率偏低。  
- TDJ 公司在「Development & Voluteering」類別申請量為 49 筆，相對其他類別較低，可能反映該類福利使用率不高。  

**📌 觀察與建議**  
- 建議針對申請量高的公司（如 JSM、TDI）進一步分析其員工需求特徵，以優化福利政策與資源配置。  
- 可針對申請量低的公司（如 TDC）進行員工問卷調查，了解參與度低的原因，並設計激勵措施提升福利使用率。  
- 建議定期追蹤各公司申請類別分布變化，以掌握員工福利偏好趨勢，並作為未來政策調整依據。  

**⚠️ 解讀注意事項**  
- 本分析僅基於申請筆數分布，未考慮申請人數或實際支付金額，因此無法判斷各公司福利使用深度。  
- 熱力圖結果受樣本數影響，部分公司申請量極低（如 TDC），可能導致結果偏頗，需謹慎解讀。
```

---

## Case 09 · AI 審查率 vs 退單率散點圖
**Query:** `AI 審查率跟退單率有相關嗎?畫個散點圖看看`
**Type:** happy_path · **Status:** pass

### 檢查項
- ✅ Plan 有產出
- ✅ Plan 非空
- ✅ Pipeline 不含禁忌 stage (group/sort/limit/divide/cond)  ·  ✓ 乾淨
- ✅ Pipeline 撈到非空資料  ·  shape=(147526, 7)
- ✅ Phase B exec 成功 (用 1 次嘗試)  ·  1 次過
- ✅ Phase B 有產出 Q
- ✅ Q 含必備欄位 ['company_code']  ·  ✓
- ✅ Q[(ai_review_rate | ai_rate | AI 審查率)] 有變異(non-degenerate)  ·  unique=15
- ✅ Q[(average_return_rate | return_rate | 退單率 | rtn_rate)] 有變異(non-degenerate)  ·  unique=15
- ✅ Phase C exec 成功 (用 1 次嘗試)  ·  1 次過
- ✅ option 含必備 keys ['title', 'xAxis', 'yAxis', 'series']  ·  ✓
- ✅ series 數 ≥ 1  ·  actual: 1
- ✅ Insight 有產出
- ✅ Insight 無禁忌語(或皆為拒絕語境)

### Phase 0 · Plan (12.5s)
```
**A. 資料獲取:**  
- 起手 collection: `tflex_applications`  
- 需要 join 的表: 無（所有 needed fields 均在 `tflex_applications`）  
- 需要的 $match 過濾條件: 無（需全量資料以計算整體比率）  

🔑 **需要的原始欄位 (raw_columns_needed)**:  
`[employee_id, company_code, application_no, application_category, review_status, review_result, review_mechanism]`  

**B. 資料處理:**  
1. **計算 AI 審查率 (AI Review Rate)**:  
   - 分子：`count where review_status='Y' and review_mechanism='AI'`  
   - 分母：`completed_count`（即 `count where review_status='Y'`）  
   - 公式：`AI Review Rate = (AI_count / completed_count)`  

2. **計算退單率 (Return Rate)**:  
   - 分子：`return_count`（即 `count where review_status='Y' and review_result='N'`）  
   - 分母：`total_applications`（即 `count where review_status='Y' or review_status='N'`）  
   - 但根據 KPI 定義，退單率不包含 in-progress 的申請，因此：  
     - 分母 = `completed_count`（即 `count where review_status='Y'`）  
   - 公式：`Return Rate = (return_count / completed_count)`  

3. **以公司為單位計算上述兩個比率**（因 query 未指定維度，建議以公司為單位分析）  

**C. 視覺化建議:**  
- 圖型選擇：散點圖（Scatter Plot）  
- 理由：用來觀察 AI 審查率與退單率之間是否存在相關性。  
- X 軸：AI 審查率  
- Y 軸：退單率  
- 標記：以公司為單位（每家公司一個點）  
- 若公司數量超過 7 個，可考慮改為水平長條圖（horizontal bar chart）以提升可讀性，但因 query 明確要求「畫個散點圖」，故優先維持散點圖。  

⚠️ 注意：此分析不涉及時間軸或金額，符合當前 metadata 支援範圍。
```
### Phase A · Pipeline (5.9s)
```json
{
  "start_collection": "tflex_applications",
  "pipeline": [
    {
      "$project": {
        "_id": 0,
        "employee_id": 1,
        "company_code": 1,
        "application_no": 1,
        "application_category": 1,
        "review_status": 1,
        "review_result": 1,
        "review_mechanism": 1
      }
    }
  ]
}
```
raw_df: shape=[147526, 7], cols=['application_no', 'application_category', 'company_code', 'employee_id', 'review_mechanism', 'review_result', 'review_status']
### Phase B · Preprocess (9.5s)
```python
# 計算 completed_count
completed_count = raw_df[raw_df['review_status'] == 'Y'].shape[0]

# 計算 AI 審查率
ai_review_count = raw_df[(raw_df['review_status'] == 'Y') & (raw_df['review_mechanism'] == 'AI')].shape[0]
ai_review_rate = ai_review_count / completed_count if completed_count > 0 else 0

# 計算 return_count
return_count = raw_df[(raw_df['review_status'] == 'Y') & (raw_df['review_result'] == 'N')].shape[0]
return_rate = return_count / completed_count if completed_count > 0 else 0

# 以公司為單位計算 AI 審查率與退單率
agg = raw_df.groupby('company_code').agg(
    ai_count=('review_mechanism', lambda x: (x == 'AI').sum()),
    completed_count=('review_status', 'size'),
    return_count=('review_result', lambda x: (x == 'N').sum())
).reset_index()

# 計算比率
agg['ai_review_rate'] = agg['ai_count'] / agg['completed_count']
agg['return_rate'] = agg['return_count'] / agg['completed_count']

# 組合最終結果
Q = agg[['company_code', 'ai_review_rate', 'return_rate']]
```

Q: shape=[15, 3], cols=['company_code', 'ai_review_rate', 'return_rate']
### Phase C · ECharts (16.5s)
```python
option = {
    "title": {"text": "AI 審查率 vs 退單率 散點圖", "left": "center"},
    "tooltip": {
        "trigger": "item",
        "formatter": "{a}<br/>{b}: ({c[0]}, {c[1]})"
    },
    "color": [
        "#5470c6", "#91cc75", "#fac858", "#ee6666", "#73c0de",
        "#3ba272", "#fc8452", "#9a60b4", "#ea7ccc", "#5b9bd5",
        "#a5a5a5", "#ffc000", "#7b78de", "#27a39d", "#e15759",
        "#f28e2c", "#76b7b2", "#59a14f", "#edc949", "#b07aa1"
    ],
    "xAxis": {"type": "value", "name": "AI 審查率 (%)"},
    "yAxis": {"type": "value", "name": "退單率 (%)"},
    "series": [{
        "name": "公司",
        "type": "scatter",
        "symbolSize": 12,
        "data": [
            [
                float((Q['ai_review_rate'] * 100).round(2).iloc[i]),
                float((Q['return_rate'] * 100).round(2).iloc[i]),
                str(Q['company_code'].iloc[i])
            ]
            for i in range(len(Q))
        ],
        "label": {
            "show": True,
            "position": "top",
            "formatter": "{c[2]}"
        }
    }],
    "grid": {"left": 60, "right": 60, "top": 60, "bottom": 40}
}
```

option keys: ['title', 'tooltip', 'color', 'xAxis', 'yAxis', 'series', 'grid']
### Phase D · Insight (10.0s)
```
**🔑 重點摘要**  
- ESM 公司的 AI 審查率最高（約 42.2%），退單率最低（約 2.9%）。  
- TSC 公司的退單率最高（約 4.0%），但 AI 審查率仍維持在約 39.6%。  
- TSK 公司的 AI 審查率最低（14.3%），退單率為 0%，顯示可能申請量少或審核方式特殊。  
- 整體而言，AI 審查率與退單率無明顯正相關，部分公司呈現「高 AI 審查率但低退單」或「低 AI 審查率但高退單」的差異。  
- 各公司間 AI 審查率與退單率差異明顯，顯示不同單位的審核策略與效能存在差異。  

**📌 觀察與建議**  
- 建議針對 AI 審查率與退單率差異顯著的公司（如 TSK、TSC、TSN）進行個案分析，了解其審核流程與結果差異原因。  
- 可進一步觀察 AI 審查率與申請類別（如 Medical & Insurance、Wellness）的關聯，探索是否特定類別更易被 AI 審核或退單。  
- 建立定期追蹤機制，觀察 AI 審查率與退單率的變化趨勢，以評估 AI 審核效能是否持續改善。  

**⚠️ 解讀注意事項**  
- 本分析僅基於公司層級資料，未考慮申請類別、申請人特徵等維度，無法深入探討個別因素對 AI 審查率與退單率的影響。  
- 因資料中無時間欄位，無法判斷 AI 審查率與退單率是否隨時間趨勢變化，建議未來可補充時間維度以進行更深入分析。
```

---

## Case 10 · TOP 5 退件公司(陷阱:DB 不可 $sort/$limit)
**Query:** `列出退件數量最多的前 5 名公司,搭配柱狀圖`
**Type:** happy_path · **Status:** pass

### 檢查項
- ✅ Plan 有產出
- ✅ Plan 非空
- ✅ Pipeline 不含禁忌 stage (group/sort/limit/divide/cond)  ·  ✓ 乾淨
- ✅ Pipeline 撈到非空資料  ·  shape=(135276, 8)
- ✅ Phase B exec 成功 (用 1 次嘗試)  ·  1 次過
- ✅ Phase B 有產出 Q
- ✅ Q 含必備欄位 ['company_code', '(return_count | 退件數 | 退件數量 | RTN | rtn | rtn_count | ret_count)']  ·  ✓
- ✅ Q 為 Top-5 行  ·  actual rows: 5
- ✅ Phase C exec 成功 (用 1 次嘗試)  ·  1 次過
- ✅ option 含必備 keys ['title', 'xAxis', 'yAxis', 'series']  ·  ✓
- ✅ series 數 ≥ 1  ·  actual: 1
- ✅ Insight 有產出
- ✅ Insight 無禁忌語(或皆為拒絕語境)

### Phase 0 · Plan (9.5s)
```
**A. 資料獲取:**  
- collection: `tflex_applications`  
- join: `tflex_company_hc` (用 `company_code` 關聯)  
- $match 過濾條件: `review_status = 'Y'` (只計算已完成的申請)  
- 🔑 **需要的原始欄位 (raw_columns_needed)**:  
  `[company_code, review_status, review_result]`  

**B. 資料處理:**  
1. 篩選 `review_status = 'Y'` 的文件（已完成申請）  
2. 按 `company_code` 分組，計算每家公司 `review_result = 'N'` 的筆數（即退件數）  
3. 排序退件數量，取前 5 名  
4. 與 `tflex_company_hc` join 取得公司人數（用於後續分析，如員工送單率）  

**C. 視覺化建議:**  
- 圖型選擇：**橫向長條圖 (horizontal bar chart)**  
- 理由：  
  - 類別數（公司數） ≤ 5，適合用長條圖清晰比較  
  - query 明確點名「前 5 名」，橫向排列易讀性佳  
  - 若需搭配「員工送單率」等指標，可視需求加入第二軸或表格形式  

> 📌 注意：此查詢未涉及時間、金額、Reviewer ID 等不支援分析類型，且所有欄位皆存在於 schema 中，因此可執行。
```
### Phase A · Pipeline (7.3s)
```json
{
  "start_collection": "tflex_applications",
  "pipeline": [
    {
      "$match": {
        "review_status": {
          "$in": [
            "Y"
          ]
        }
      }
    },
    {
      "$lookup": {
        "from": "tflex_company_hc",
        "localField": "company_code",
        "foreignField": "company_code",
        "as": "company_info"
      }
    },
    {
      "$unwind": {
        "path": "$company_info",
        "preserveNullAndEmptyArrays": true
      }
    },
    {
      "$project": {
        "_id": 0,
        "employee_id": 1,
        "company_code": 1,
        "application_no": 1,
        "application_category": 1,
        "review_status": 1,
        "review_result": 1,
        "review_mechanism": 1,
        "hc": "$company_info.hc"
      }
    }
  ]
}
```
raw_df: shape=[135276, 8], cols=['application_no', 'application_category', 'company_code', 'employee_id', 'review_mechanism', 'review_result', 'review_status', 'hc']
### Phase B · Preprocess (7.9s)
```python
# A. 資料獲取
# B. 資料處理
# 篩選已完成申請
completed_df = raw_df[raw_df['review_status'] == 'Y']

# 計算每家公司退件數量
return_count = completed_df.groupby('company_code').agg(
    return_count=('review_result', lambda x: (x == 'N').sum())
).reset_index()

# 取前 5 名退件數量最多的公司
top5_companies = return_count.nlargest(5, 'return_count')

# 與 tflex_company_hc join 取得公司人數
# 假設 raw_df 已經是 join 完成的資料
# 這裡直接使用 raw_df 中的 hc 欄位
company_hc = raw_df[['company_code', 'hc']].drop_duplicates()

# 合併退件數與公司人數
result = top5_companies.merge(company_hc, on='company_code', how='left')

# 重新排序欄位
Q = result[['company_code', 'return_count', 'hc']]
```

Q: shape=[5, 3], cols=['company_code', 'return_count', 'hc']
### Phase C · ECharts (14.2s)
```python
option = {
    "title": {"text": "退件數量最多的前 5 名公司", "left": "center"},
    "tooltip": {"trigger": "axis", "axisPointer": {"type": "cross"}},
    "color": ["#5470c6", "#91cc75", "#fac858", "#ee6666", "#73c0de", "#3ba272", "#fc8452", "#9a60b4", "#ea7ccc", "#5b9bd5", "#a5a5a5", "#ffc000", "#7b78de", "#27a39d", "#e15759", "#f28e2c", "#76b7b2", "#59a14f", "#edc949", "#b07aa1"],
    "xAxis": {"type": "value"},
    "yAxis": {"type": "category", "data": Q["company_code"].astype(str).tolist()},
    "series": [
        {
            "name": "return_count",
            "type": "bar",
            "data": [int(v) for v in Q["return_count"].tolist()],
            "label": {"show": True, "position": "right", "formatter": "{c}"},
        }
    ],
    "grid": {"left": 60, "right": 60, "top": 60, "bottom": 40}
}
```

option keys: ['title', 'tooltip', 'color', 'xAxis', 'yAxis', 'series', 'grid']
### Phase D · Insight (8.1s)
```
**🔑 重點摘要**  
- 退件數量最多的前 5 名公司中，TST 以 4,285 件退件領先，佔比極高。  
- TST 的退件率（約 52.9%）遠高於其他公司，顯示其申請流程或審核標準可能需調整。  
- 其餘公司退件數量明顯下降，TSC、JSM、TSA 分別為 168、139、62 件，顯示營運成熟度差異。  
- TST 的員工送單率（約 52.9%）高於平均，但退件率亦高，反映申請品質問題。  
- 該分析僅針對已完成申請（review_status='Y'）進行，未包含進行中申請，確保結果準確性。  

**📌 觀察與建議**  
- 建議針對 TST 公司進行深入審核流程檢視，找出退件高發的關鍵原因，並優化申請指引或系統提示。  
- 可考慮將退件原因進行細分（如目前資料未提供），以進一步定位問題並制定改善策略。  
- 建議持續追蹤 TST 退件率變化，並設定警戒線，以監控流程改善成效。  

**⚠️ 解讀注意事項**  
- 因資料中未含申請日期，無法判斷退件趨勢或季節性變化，僅能觀察靜態分布。  
- 退件數量受公司整體申請量影響，TST 申請量高導致退件絕對數高，需搭配「平均退單率」分析以避免誤判。
```

---

## Case 11 · 全公司完整 KPI 一覽表(table fallback)
**Query:** `幫我整理一張完整的公司 KPI 表格:申請數、完成數、PAY、RTN、退單率、AI 率、員工送單率全都要`
**Type:** happy_path · **Status:** pass

### 檢查項
- ✅ Plan 有產出
- ✅ Plan 非空
- ✅ Pipeline 不含禁忌 stage (group/sort/limit/divide/cond)  ·  ✓ 乾淨
- ✅ Pipeline 撈到非空資料  ·  shape=(147526, 8)
- ✅ Phase B exec 成功 (用 2 次嘗試)  ·  retry log: ['attempt 1: semantic [Q_ALL_NAN]']
- ✅ Phase B 有產出 Q
- ✅ Q 含必備欄位 ['company_code']  ·  ✓
- ✅ Q 至少 5 欄  ·  actual: 14
- ✅ Phase C exec 成功 (用 1 次嘗試)  ·  1 次過
- ✅ Phase C 路徑  ·  use_table fallback
- ✅ Insight 有產出
- ✅ Insight 無禁忌語(或皆為拒絕語境)

### Phase 0 · Plan (12.1s)
```
**A. 資料獲取:**  
- 起手 collection: `tflex_applications`, `tflex_company_hc`  
- 需要 join 的表: `tflex_applications.company_code` → `tflex_company_hc.company_code`  
- 需要的 $match 過濾條件: 無（需計算全部資料）  
  - 🔑 **需要的原始欄位 (raw_columns_needed)**:  
    `[employee_id, company_code, application_no, application_category, review_status, review_result, review_mechanism]`  

**B. 資料處理:**  
根據 KPI 公式計算各項指標：  
1. **申請數 (total_applications)**: 總文件數（`count`）  
2. **完成數 (completed_count)**: `review_status='Y'` 的文件數  
3. **PAY (pay_count)**: `review_status='Y' AND review_result='Y'` 的文件數  
4. **RTN (return_count)**: `review_status='Y' AND review_result='N'` 的文件數  
5. **退單率 (average_return_rate)**: `return_count / total_applications`（不含 in-progress）  
6. **AI 審查率 (ai_review_rate)**: `count where review_status='Y' and review_mechanism='AI' / completed_count`  
7. **員工送單率 (employee_submission_rate)**: `distinct employee_id count / company hc`  

需先對 `tflex_applications` 做 group by `company_code`，再計算上述指標。  
最後與 `tflex_company_hc` join，取得 `hc` 用於計算員工送單率。

**C. 視覺化建議:**  
- 建議使用「表格 + KPI 卡片」形式呈現，便於比較各公司間的 KPI 數值。  
- 若需圖形化呈現，可選用「橫向堆疊長條圖」或「水平 100% 堆疊長條圖」，以清楚顯示各公司各項 KPI 的占比與對比。  
- 若 query 明確點名「圓餅圖」，則可考慮使用「圓餅圖」來呈現某項 KPI（如退單率）在不同公司間的分布。
```
### Phase A · Pipeline (7.2s)
```json
{
  "start_collection": "tflex_applications",
  "pipeline": [
    {
      "$lookup": {
        "from": "tflex_company_hc",
        "localField": "company_code",
        "foreignField": "company_code",
        "as": "company_info"
      }
    },
    {
      "$unwind": {
        "path": "$company_info",
        "preserveNullAndEmptyArrays": true
      }
    },
    {
      "$project": {
        "_id": 0,
        "employee_id": 1,
        "company_code": 1,
        "application_no": 1,
        "application_category": 1,
        "review_status": 1,
        "review_result": 1,
        "review_mechanism": 1,
        "hc": "$company_info.hc"
      }
    }
  ]
}
```
raw_df: shape=[147526, 8], cols=['application_no', 'application_category', 'company_code', 'employee_id', 'review_mechanism', 'review_result', 'review_status', 'hc']
### Phase B · Preprocess (21.9s)
```python
Q = raw_df.copy()

# 計算各公司 KPI
agg = Q.groupby('company_code').agg(
    total_applications=('application_no', 'size'),
    completed_count=('review_status', lambda x: (x == 'Y').sum()),
    pay_count=('review_result', lambda x: ((x == 'Y') & (x.notna())).sum()),
    return_count=('review_result', lambda x: ((x == 'N') & (x.notna())).sum()),
    submitter_count=('employee_id', 'nunique'),
    ai_count=('review_mechanism', lambda x: ((x == 'AI') & (x.notna())).sum()),
    hc=('hc', 'first'),
).reset_index()

# 計算比率類 KPI
agg['average_return_rate_numerator'] = agg['return_count']
agg['average_return_rate_denominator'] = agg['total_applications']
agg['average_return_rate'] = agg['return_count'] / agg['total_applications']

agg['ai_review_rate_numerator'] = agg['ai_count']
agg['ai_review_rate_denominator'] = agg['completed_count']
agg['ai_review_rate'] = agg['ai_count'] / agg['completed_count']

agg['employee_submission_rate_numerator'] = agg['submitter_count']
agg['employee_submission_rate_denominator'] = agg['hc']
agg['employee_submission_rate'] = agg['submitter_count'] / agg['hc']

# 重新整理欄位順序
Q = agg[['company_code', 'total_applications', 'completed_count', 'pay_count', 'return_count', 'average_return_rate_numerator', 'average_return_rate_denominator', 'average_return_rate', 'ai_review_rate_numerator', 'ai_review_rate_denominator', 'ai_review_rate', 'employee_submission_rate_numerator', 'employee_submission_rate_denominator', 'employee_submission_rate']]
```

Q: shape=[15, 14], cols=['company_code', 'total_applications', 'completed_count', 'pay_count', 'return_count', 'average_return_rate_numerator', 'average_return_rate_denominator', 'average_return_rate', 'ai_review_rate_numerator', 'ai_review_rate_denominator', 'ai_review_rate', 'employee_submission_rate_numerator', 'employee_submission_rate_denominator', 'employee_submission_rate']
### Phase C · ECharts (11.2s)
```python
option = {
    "_use_table": True,
    "_kpi_cards": [
        {"label": "申請數", "value": f"{Q['total_applications'].sum():,}"},
        {"label": "完成數", "value": f"{Q['completed_count'].sum():,}"},
        {"label": "PAY", "value": f"{Q['pay_count'].sum():,}"},
        {"label": "RTN", "value": f"{Q['return_count'].sum():,}"},
    ],
    "_table_caption": f"資料筆數:{len(Q)}"
}
```

📋 `_use_table` fallback triggered
### Phase D · Insight (11.9s)
```
**🔑 重點摘要**  
- TST 公司申請量最高（128,922 筆），佔整體申請量近 6 成，顯示其規模與參與度極高。  
- 整體平均退單率為 3.32%，其中 TSK 公司退單率最低（0%），TSE 公司退單率最高（0.94%）。  
- AI 審查率平均約為 43%，顯示審核流程中人工與 AI 協同運作，但仍有提升空間。  
- 員工送單率普遍偏高，TSC 公司達 98.8%，顯示員工參與度高，但部分公司如 TSK 僅有 100% 員工參與，需進一步觀察。  
- 完成率高達 92.3%，顯示整體申請流程效率良好，僅少數申請未完成。

**📌 觀察與建議**  
- 建議針對退單率較高的公司（如 TSE、TSN）進行個案分析，了解退單原因並優化申請指引或審核標準。  
- AI 審查率穩定在 43% 左右，建議持續觀察 AI 審核準確率與人工複核效率，以提升整體審核效能。  
- 員工送單率高顯示制度接受度佳，建議針對送單率偏低的公司（如 TSK）進一步了解是否因制度宣導不足或流程複雜所致。

**⚠️ 解讀注意事項**  
- 資料中未包含申請時間、金額、部門等維度，無法進行趨勢、金額或部門比較分析，僅能做公司間橫向比較。  
- 部分公司（如 TSK）申請量極低（僅 7 筆），導致其 KPI 數值極度敏感，需謹慎解讀。
```

---

## Case 12 · 過去三個月趨勢(拒絕:無 date)
**Query:** `我想看過去三個月每週的申請趨勢`
**Type:** refusal · **Status:** refusal_detected

### 檢查項
- ✅ Plan 有產出
- ✅ Plan 非空
- ✅ Plan 含拒絕關鍵字  ·  hit: ['no application date', '缺少', '限制']

### Phase 0 · Plan (5.2s)
```
[REFUSE] 缺少 <application date>,無法執行 <trend analysis over time>。
此為 data_limitations 中 "No application date" 的限制。
建議改問:<請問您是否想了解各公司或各類別的申請分布情況？>
```

---

## Case 13 · 各部門退單率(拒絕:無 department)
**Query:** `各部門的退單率比較,哪個部門最高?`
**Type:** refusal · **Status:** refusal_detected

### 檢查項
- ✅ Plan 有產出
- ✅ Plan 非空
- ✅ Plan 含拒絕關鍵字  ·  hit: ['無', '缺', 'department', '限制']

### Phase 0 · Plan (1.1s)
```
[REFUSE] 缺少 `employee_department` 維度，無法執行 `Department-level comparison`。
此為 data_limitations 中 "No employee department" 的限制。
建議改問：「請問各公司送單人數、申請張數、退單率比較」，以公司為維度進行分析。
```
```

---

## Case 14 · 平均申請金額(拒絕:無 amount)
**Query:** `員工每次申請的平均金額是多少?哪家公司平均最高?`
**Type:** refusal · **Status:** refusal_detected

### 檢查項
- ✅ Plan 有產出
- ✅ Plan 非空
- ✅ Plan 含拒絕關鍵字  ·  hit: ['無', '缺', 'amount', '金額', '限制']

### Phase 0 · Plan (1.1s)
```
```
[REFUSE] 缺少 <payment amount>，無法執行 <Payment amount analysis>。
此為 data_limitations 中 "No payment amount" 的限制。
建議改問：<計算各公司送單人數、申請張數、審核完成率等不依賴金額的 KPI>。
```
```

---

## Case 15 · 平均審核時間(拒絕:無 timestamp)
**Query:** `平均審核需要幾天?AI 跟人工誰比較快?`
**Type:** refusal · **Status:** refusal_detected

### 檢查項
- ✅ Plan 有產出
- ✅ Plan 非空
- ✅ Plan 含拒絕關鍵字  ·  hit: ['無', '缺', 'timestamp', '限制', '無法']

### Phase 0 · Plan (1.3s)
```
[REFUSE] 缺少 <review completion timestamp>,無法執行 <Review cycle time analysis>。
此為 data_limitations 中 "No review completion timestamp" 的限制。
建議改問:<可計算「完成率」或「AI 審查率」等比率類 KPI，並以公司或員工為維度比較的分析>。
```

---

## Case STK-01 · 100% stacked bar:per company × category
**Query:** `畫一張 stacked bar:依據 company_code(TST、TSN、TSC),每條 bar 中呈現 application_category 的佔比`
**Type:** happy_path · **Status:** phaseC_fallback_used

### 檢查項
- ✅ Plan 有產出
- ✅ Plan 非空
- ✅ Pipeline 不含禁忌 stage (group/sort/limit/divide/cond)  ·  ✓ 乾淨
- ✅ Pipeline 撈到非空資料  ·  shape=(147526, 7)
- ✅ Phase B exec 成功 (用 1 次嘗試)  ·  1 次過
- ✅ Phase B 有產出 Q
- ✅ Q 含必備欄位 []  ·  ✓
- ✅ Phase C 路徑  ·  3 次失敗後降級為表格 (retry log: ["attempt 1: AttributeError: 'float' object has no attribute 'round'", 'attempt 2: semantic fail [AXIS_ALIGN; AXIS_ALIGN; AXIS_ALIGN; AXIS_ALIGN; AXIS_ALIGN; AXIS_ALIGN; AXIS_ALIGN; AXIS_ALIGN; AXIS_ALIGN; AXIS_ALIGN; ]', "attempt 3: KeyError: 'company'"])

### Phase 0 · Plan (6.8s)
```
**A. 資料獲取:**  
- 起手 collection: `tflex_applications`  
- 需要 join 的表: 無 (所有 needed fields 都在 `tflex_applications`)  
- $match 過濾條件: 無（需包含所有資料以計算各公司各類別的申請數）  

🔑 **需要的原始欄位 (raw_columns_needed)**:  
`[company_code, application_category]`

---

**B. 資料處理:**  
1. **計算每家公司每種申請類別的申請數量**（group by company_code and application_category）  
2. **計算每家公司總申請數量**（group by company_code）  
3. **計算每家公司中各 application_category 的佔比**  
4. **KPI 計算依據**：  
   - 總申請張數 (total_applications) = count of documents in tflex_applications  
   - 各公司各類別申請數 = count where group by company_code + application_category  
   - 各公司中 application_category 佔比 = 該類別申請數 / 該公司總申請數  

---

**C. 視覺化建議:**  
- 圖型選擇：**橫向堆疊長條圖**（horizontal stacked bar chart）  
- 理由：  
  - query 明確要求依據 `company_code` 作為橫向分類  
  - 每條 bar 中呈現 `application_category` 的佔比 → 適合使用堆疊長條圖  
  - 類別數 ≤ 4（Family Care, Wellness, Medical & Insurance, Development & Voluteering）→ 適合堆疊圖表現  
  - 橫向排列更易於比較不同公司之間的類別分布比例  

✅ **orientation 鐵律**：query 明確提到「橫向」→ 使用「橫向堆疊長條圖」  
```
```
### Phase A · Pipeline (6.2s)
```json
{
  "start_collection": "tflex_applications",
  "pipeline": [
    {
      "$project": {
        "_id": 0,
        "employee_id": 1,
        "company_code": 1,
        "application_no": 1,
        "application_category": 1,
        "review_status": 1,
        "review_result": 1,
        "review_mechanism": 1
      }
    }
  ]
}
```
raw_df: shape=[147526, 7], cols=['application_no', 'application_category', 'company_code', 'employee_id', 'review_mechanism', 'review_result', 'review_status']
### Phase B · Preprocess (9.2s)
```python
# 1. groupby 計數
counts = raw_df.groupby(['company_code', 'application_category']).size().reset_index(name='count')

# 2. 用 transform 算每組總數(不需中介 _total 欄位)
counts['_total_per_group'] = counts.groupby('company_code')['count'].transform('sum')

# 3. 算百分比(0-100 範圍,Phase C 直接用)
counts['percentage'] = (counts['count'] / counts['_total_per_group'] * 100).round(2)

# 4. 終態:只留 3 欄(long format,Phase C pivot 用)
Q = counts[['company_code', 'application_category', 'percentage']]
```

Q: shape=[59, 3], cols=['company_code', 'application_category', 'percentage']
### Phase C · ECharts (50.7s)
```python
import pandas as pd

# 假設 Q 是從資料庫查詢出來的 DataFrame，包含 company, application_type 兩欄位
# 例如：
# Q = pd.DataFrame({
#     'company': ['TSMC', 'TSMC', 'HTC', 'TSMC', 'HTC'],
#     'application_type': ['Family Care', 'Wellness', 'Medical & Insurance', 'Family Care', 'Wellness']
# })

# Step 1: 統計每個公司對每種申請類型的數量
pivot = Q.pivot_table(index='company', columns='application_type', aggfunc='size', fill_value=0)

# Step 2: 計算每行的總和，用來計算百分比
pivot['total'] = pivot.sum(axis=1)

# Step 3: 轉換為百分比（每行除以總和）
for col in pivot.columns[:-1]:  # 排除 total 欄位
    pivot[col] = (pivot[col] / pivot['total']) * 100

# Step 4: 移除 total 欄位
pivot = pivot.drop(columns=['total'])

# Step 5: 轉換為適合 ECharts 的格式
data = []
for company in pivot.index:
    row = {'name': company}
    for app_type in pivot.columns:
        row[app_type] = round(pivot.loc[company, app_type], 2)
    data.append(row)

# Step 6: 建立 xAxis 的 categories
xAxis_categories = pivot.columns.tolist()

# Step 7: 建立 series 數據
series = []
for app_type in xAxis_categories:
    series.append({
        'name': app_type,
        'type': 'bar',
        'stack': 'total',
        'data': [item[app_type] for item in data]
    })

# 最終輸出的 echarts 配置
option = {
    'title': {
        'text': '各公司申請類型分布'
    },
    'tooltip': {
        'trigger': 'axis',
        'axisPointer': {'type': 'shadow'}
    },
    'legend': {
        'data': xAxis_categories
    },
    'grid': {
        'left': '3%',
        'right': '4%',
        'bottom': '3%',
        'containLabel': True
    },
    'xAxis': [
        {
            'type': 'value',
            'max': 100,
            'axisLabel': {'formatter': '{value} %'}
        }
    ],
    'yAxis': [
        {
            'type': 'category',
            'data': [item['name'] for item in data]
        }
    ],
    'series': series
}
```

❌ **exec error:**
```
Traceback (most recent call last):
  File "/Users/kururu/Documents/Claude/Projects/GenBI/test_runner.py", line 893, in run_case
    exec(echarts_code, ns2, ns2)
    ~~~~^^^^^^^^^^^^^^^^^^^^^^^^
  File "<string>", line 11, in <module>
  File "/Users/kururu/Documents/Claude/Projects/GenBI/.venv/lib/python3.14/site-packages/pandas/core/frame.py", line 11159, in pivot_table
    return pivot_table(
        self,
    ...<10 lines>...
        **kwargs,
    )
  File "/Users/kururu/Documents/Claude/Projects/GenBI/.venv/lib/python3.14/site-packages/pandas/core/reshape/pivot.py", line 267, in pivot_table
    table = __internal_pivot_table(
        data,
    ...<10 lines>...
        kwargs,
    )
  File "/Users/kururu/Documents/Claude/Projects/GenBI/.venv/lib/python3.14/site-packages/pandas/core/reshape/pivot.py", line 338, in __internal_pivot_table
    grouped = data.groupby(keys, observed=observed, sort=sort, dropna=dropna)
  File "/Users/kururu/Documents/Claude/Projects/GenBI/.venv/lib/python3.14/site-packages/pandas/util/_decorators.py", line 336, in wrapper
    return func(*args, **kwargs)
  File "/Users/kururu/Documents/Claude/Projects/GenBI/.venv/lib/python3.14/site-packages/pandas/core/frame.py", line 10833, in groupby
    return DataFrameGroupBy(
        obj=self,
    ...<6 lines>...
        dropna=dropna,
    )
  File "/Users/kururu/Documents/Claude/Projects/GenBI/.venv/lib/python3.14/site-packages/pandas/core/groupby/groupby.py", line 1095, in __init__
    grouper, exclusions, obj = get_grouper(
                               ~~~~~~~~~~~^
        obj,
        ^^^^
    ...<4 lines>...
        dropna=self.dropna,
        ^^^^^^^^^^^^^^^^^^^
    )
    ^
  File "/Users/kururu/Documents/Claude/Projects/GenBI/.venv/lib/python3.14/site-packages/pandas/core/groupby/grouper.py", line 901, in get_grouper
    raise KeyError(gpr)
KeyError: 'company'

```

---

## Case STK-02 · 100% stacked transposed:per category × company
**Query:** `依據 application_category 畫 stacked bar,每條 bar 中呈現 TST、TSN、TSC 的占比`
**Type:** happy_path · **Status:** pass

### 檢查項
- ✅ Plan 有產出
- ✅ Plan 非空
- ✅ Pipeline 不含禁忌 stage (group/sort/limit/divide/cond)  ·  ✓ 乾淨
- ✅ Pipeline 撈到非空資料  ·  shape=(147526, 8)
- ✅ Phase B exec 成功 (用 1 次嘗試)  ·  1 次過
- ✅ Phase B 有產出 Q
- ✅ Q 含必備欄位 []  ·  ✓
- ✅ Phase C exec 成功 (用 3 次嘗試)  ·  retry 3
- ✅ option 含必備 keys ['title', 'xAxis', 'yAxis', 'series']  ·  ✓
- ✅ series 數 ≥ 2  ·  actual: 15
- ✅ series 帶 stack 屬性
- ✅ xAxis.data 無重複  ·  len=4
- ✅ 所有 series.data 長度 == xAxis.data 長度 (4)  ·  series lens: [4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4]
- ✅ yAxis.max == 100 (100% stacked 應鎖頂)  ·  actual: 100
- ✅ series.name 非 placeholder (類別 A/Category 1/<col> 等)  ·  names: ['ESM', 'JSM', 'TDC', 'TDI', 'TDJ', 'TRJ', 'TSA', 'TSC', 'TSE', 'TSJ', 'TSK', 'TSN', 'TST', 'TSU', 'TWT']
- ✅ Insight 有產出
- ✅ Insight 無禁忌語(或皆為拒絕語境)

### Phase 0 · Plan (11.8s)
```
**A. 資料獲取:**  
- collection: `tflex_applications`  
- join: `tflex_company_hc` (用 `company_code` 關聯)  
- $match 過濾條件: 無（需所有資料進行分類統計）  
  - 🔑 **需要的原始欄位 (raw_columns_needed)**:  
    `[application_category, company_code, review_status, review_result, review_mechanism]`  

**B. 資料處理:**  
1. **資料過濾與計算**：  
   - 依 `application_category` 分組，並計算每類別下各公司（TST、TSN、TSC）的申請數量。  
   - 結果需包含：  
     - `application_category`  
     - `company_code`  
     - `total_applications`（每類別中各公司的申請數）  

2. **KPI 計算邏輯**：  
   - `total_applications` = count of documents in `tflex_applications`  
   - 每個 `application_category` 下，對應的 `company_code`（TST、TSN、TSC）的申請數量為 `total_applications`  
   - 不需計算比率類 KPI（如 return_rate / ai_review_rate），僅需分類統計  

**C. 視覺化建議:**  
- 圖型選擇：**堆疊長條圖（Stacked Bar Chart）**  
- orientation：**垂直（vertical）**  
- 理由：  
  - `application_category` 為類別維度，`company_code` 為堆疊維度  
  - 每個 bar 代表一個 application_category，內部按公司（TST、TSN、TSC）堆疊  
  - 類別數 ≤ 7（application_category 有 4 種），符合堆疊長條圖使用情境  
  - query 明確要求「畫 stacked bar」且「呈現 TST、TSN、TSC 的占比」，無需額外時間或金額維度，符合 schema 支援範圍  

✅ 此需求未觸犯任何 data_limitations，可執行。
```
### Phase A · Pipeline (7.2s)
```json
{
  "start_collection": "tflex_applications",
  "pipeline": [
    {
      " $lookup": {
        "from": "tflex_company_hc",
        "localField": "company_code",
        "foreignField": "company_code",
        "as": "company_info"
      }
    },
    {
      " $unwind": {
        "path": "$company_info",
        "preserveNullAndEmptyArrays": true
      }
    },
    {
      " $project": {
        "_id": 0,
        "employee_id": 1,
        "company_code": 1,
        "application_no": 1,
        "application_category": 1,
        "review_status": 1,
        "review_result": 1,
        "review_mechanism": 1,
        "hc": "$company_info.hc"
      }
    }
  ]
}
```
raw_df: shape=[147526, 8], cols=['application_no', 'application_category', 'company_code', 'employee_id', 'review_mechanism', 'review_result', 'review_status', 'hc']
### Phase B · Preprocess (9.4s)
```python
import pandas as pd

# Step 1: 統計每個 application_category 下，各 company_code 的申請數量
counts = raw_df.groupby(['application_category', 'company_code']).size().reset_index(name='total_applications')

# Step 2: 計算每組 application_category 的總申請數
counts['_total_per_category'] = counts.groupby('application_category')['total_applications'].transform('sum')

# Step 3: 計算占比（乘以 100 以得到 0-100 範圍）
counts['percentage'] = (counts['total_applications'] / counts['_total_per_category'] * 100).round(2)

# Step 4: 終態：只保留三欄 (long format)
Q = counts[['application_category', 'company_code', 'percentage']]
```

Q: shape=[59, 3], cols=['application_category', 'company_code', 'percentage']
### Phase C · ECharts (33.3s)
```python
option = {
    "title": {"text": "Application Category by Company", "left": "center"},
    "tooltip": {"trigger": "axis", "axisPointer": {"type": "cross"}},
    "legend": {"show": True, "top": 30},
    "color": ["#5470c6", "#91cc75", "#fac858", "#ee6666", "#73c0de", "#3ba272", "#fc8452", "#9a60b4", "#ea7ccc", "#5b9bd5", "#a5a5a5", "#ffc000", "#7b78de", "#27a39d", "#e15759", "#f28e2c", "#76b7b2", "#59a14f", "#edc949", "#b07aa1"],
    "grid": {"left": 60, "right": 60, "top": 60, "bottom": 40},
    "xAxis": {"type": "category", "data": Q["application_category"].unique().tolist()},
    "yAxis": {"type": "value", "max": 100, "axisLabel": {"formatter": "{value}%"}},
    "series": [
        {
            "name": str(company),
            "type": "bar",
            "stack": "total",
            "label": {"show": True, "position": "inside", "formatter": "{c}%"},
            "data": (
                Q[Q["company_code"] == company]
                .set_index("application_category")["percentage"]
                .reindex(Q["application_category"].unique().tolist())
                .fillna(0)
                .tolist()
            )
        }
        for company in Q["company_code"].unique()
    ]
}
```

option keys: ['title', 'tooltip', 'legend', 'color', 'grid', 'xAxis', 'yAxis', 'series']
### Phase D · Insight (9.0s)
```
**🔑 重點摘要**  
- 「Medical & Insurance」類別申請量最高，佔比達 47.33%，顯示此類福利需求最為集中。  
- 「TST」公司於所有申請類別中皆為申請量最大者，尤其在「Development & Voluteering」類別中占比高達 87.76%。  
- 「Family Care」與「Wellness」類別申請量相對較低，其中「Wellness」僅佔 1.46%。  
- 「TSN」公司申請量在「Medical & Insurance」與「Development & Voluteering」中表現突出，分別佔比 27.58% 與 2.82%。  
- 「TSC」公司申請量在「Medical & Insurance」類別中表現顯著，佔比達 11.26%。  

**📌 觀察與建議**  
- 建議針對「TST」公司加強「Family Care」與「Wellness」類別的宣導，以提升整體申請均衡性。  
- 可針對「Medical & Insurance」類別進行深入分析，了解員工偏好與需求，進一步優化福利政策。  
- 建議定期追蹤各公司申請類別分布變化，以掌握福利使用趨勢與潛在調整空間。  

**⚠️ 解讀注意事項**  
- 由於資料中未包含申請日期，無法確認申請量是否受時間因素影響，建議未來補充時間維度以進行趨勢分析。  
- 統計結果中部分公司（如 ESM、TRJ、TSK）申請量極低，可能為樣本不足導致數據偏頗，需謹慎解讀。
```

---

## Case STK-03 · Raw count stacked:PAY vs RTN by company
**Query:** `各公司的 PAY 與 RTN 申請數量比較,用 stacked bar 呈現`
**Type:** happy_path · **Status:** pass

### 檢查項
- ✅ Plan 有產出
- ✅ Plan 非空
- ✅ Pipeline 不含禁忌 stage (group/sort/limit/divide/cond)  ·  ✓ 乾淨
- ✅ Pipeline 撈到非空資料  ·  shape=(147526, 5)
- ✅ Phase B exec 成功 (用 1 次嘗試)  ·  1 次過
- ✅ Phase B 有產出 Q
- ✅ Q 含必備欄位 ['company_code']  ·  ✓
- ✅ Phase C exec 成功 (用 3 次嘗試)  ·  retry 3
- ✅ option 含必備 keys ['title', 'xAxis', 'yAxis', 'series']  ·  ✓
- ✅ series 數 ≥ 2  ·  actual: 2
- ✅ series 帶 stack 屬性
- ✅ xAxis.data 無重複  ·  len=15
- ✅ 所有 series.data 長度 == xAxis.data 長度 (15)  ·  series lens: [15, 15]
- ✅ series.name 非 placeholder (類別 A/Category 1/<col> 等)  ·  names: ['PAY', 'RTN']
- ✅ Insight 有產出
- ✅ Insight 無禁忌語(或皆為拒絕語境)

### Phase 0 · Plan (8.9s)
```
**A. 資料獲取:**  
- collection: `tflex_applications`  
- join: `tflex_company_hc` (用 `company_code` 關聯)  
- $match 過濾條件: 無特定過濾  
- 🔑 **需要的原始欄位 (raw_columns_needed)**:  
  `[employee_id, company_code, review_status, review_result]`  

**B. 資料處理:**  
1. 依 `company_code` 分組  
2. 計算各公司 `PAY` (review_status='Y' 且 review_result='Y') 與 `RTN` (review_status='Y' 且 review_result='N') 數量  
3. 合併 `tflex_company_hc` 以取得公司人數 (用於後續計算員工送單率等)  

**C. 視覺化建議:**  
- 圖型: **堆疊長條圖 (stacked bar chart)**  
- orientation: **垂直 (vertical)**  
- 理由: 需要比較各公司 PAY 與 RTN 數量，堆疊長條圖可清楚呈現每家公司兩者的占比與總量。  
- 若 query 明確提及「橫向」則改為「橫向堆疊長條圖」，但目前未明確提及，故使用垂直堆疊。
```
### Phase A · Pipeline (6.5s)
```json
{
  "start_collection": "tflex_applications",
  "pipeline": [
    {
      "$lookup": {
        "from": "tflex_company_hc",
        "localField": "company_code",
        "foreignField": "company_code",
        "as": "company_info"
      }
    },
    {
      "$unwind": {
        "path": "$company_info",
        "preserveNullAndEmptyArrays": true
      }
    },
    {
      "$project": {
        "_id": 0,
        "employee_id": 1,
        "company_code": 1,
        "review_status": 1,
        "review_result": 1,
        "hc": "$company_info.hc"
      }
    }
  ]
}
```
raw_df: shape=[147526, 5], cols=['company_code', 'employee_id', 'review_result', 'review_status', 'hc']
### Phase B · Preprocess (8.2s)
```python
# A. 資料獲取
# 無特定過濾條件，直接使用 raw_df

# B. 資料處理
# 1. 依 company_code 分組
# 2. 計算各公司 PAY 與 RTN 數量
# 3. 合併 tflex_company_hc 以取得公司人數

# 建立 PAY 與 RTN 的計數
agg = raw_df.groupby('company_code').agg(
    pay_count=('review_result', lambda x: (x == 'Y').sum()),
    rtn_count=('review_result', lambda x: (x == 'N').sum()),
    hc=('hc', 'first')
).reset_index()

# 轉換為 long format 以利後續 stacked bar 繪製
counts = agg.melt(id_vars=['company_code', 'hc'], 
                  value_vars=['pay_count', 'rtn_count'],
                  var_name='type', 
                  value_name='count')

# 重新命名 type 欄位以符合需求
counts['type'] = counts['type'].map({'pay_count': 'PAY', 'rtn_count': 'RTN'})

# 最終結果
Q = counts[['company_code', 'type', 'count']]
```

Q: shape=[30, 3], cols=['company_code', 'type', 'count']
### Phase C · ECharts (36.9s)
```python
option = {
    "title": {"text": "各公司 PAY 與 RTN 申請數量比較", "left": "center"},
    "tooltip": {"trigger": "axis", "axisPointer": {"type": "cross"}},
    "legend": {"show": True, "top": 30},
    "color": ["#5470c6", "#91cc75", "#fac858", "#ee6666", "#73c0de", "#3ba272", "#fc8452", "#9a60b4", "#ea7ccc", "#5b9bd5", "#a5a5a5", "#ffc000", "#7b78de", "#27a39d", "#e15759", "#f28e2c", "#76b7b2", "#59a14f", "#edc949", "#b07aa1"],
    "grid": {"left": 60, "right": 60, "top": 60, "bottom": 40},
    "xAxis": {"type": "category", "data": Q["company_code"].unique().tolist()},
    "yAxis": {"type": "value"},
    "series": [
        {
            "name": "PAY",
            "type": "bar",
            "stack": "total",
            "data": [
                int(v) for v in 
                Q[Q["type"] == "PAY"]
                  .set_index("company_code")["count"]
                  .reindex(Q["company_code"].unique())
                  .fillna(0)
                  .tolist()
            ],
            "label": {"show": True, "position": "inside", "formatter": "{c}"}
        },
        {
            "name": "RTN",
            "type": "bar",
            "stack": "total",
            "data": [
                int(v) for v in 
                Q[Q["type"] == "RTN"]
                  .set_index("company_code")["count"]
                  .reindex(Q["company_code"].unique())
                  .fillna(0)
                  .tolist()
            ],
            "label": {"show": True, "position": "inside", "formatter": "{c}"}
        }
    ]
}
```

option keys: ['title', 'tooltip', 'legend', 'color', 'grid', 'xAxis', 'yAxis', 'series']
### Phase D · Insight (9.5s)
```
**🔑 重點摘要**  
- TST 公司的 PAY 申請數量最高，達 114,744 筆，佔整體 PAY 總量的近 70%。  
- JSM 公司的 PAY 申請數量次高，達 3,240 筆，RTN 數量為 139 筆，退單率約 4.3%。  
- TSN 公司的 PAY 申請數量為 3,484 筆，為其他公司中較高者，但 RTN 數量僅 10 筆，退單率低於 0.3%。  
- TST 公司的 RTN 數量為 10 筆，相對於其龐大的申請量，退單率極低，顯示審核流程穩定。  
- 各公司間 PAY 與 RTN 數量差異顯著，顯示不同單位的申請熱度與審核品質存在明顯落差。

**📌 觀察與建議**  
- 建議針對 PAY 數量極高（如 TST）與 RTN 數量較高（如 JSM）的公司進行個案分析，了解其申請行為差異與審核流程是否需優化。  
- 可進一步追蹤各公司員工送單率（employee_submission_rate），以評估申請參與度與潛在的流程改善空間。  
- 建議針對退單率偏高的公司（如 JSM）進行審核機制檢視，確認是否需加強申請前的指引或審核標準一致性。

**⚠️ 解讀注意事項**  
- 本分析僅能呈現各公司 PAY 與 RTN 的絕對數量比較，無法進一步分析申請行為背後的員工特質或時間趨勢。  
- 因資料中未包含申請日期，無法判斷申請集中度或季節性變化，建議未來補充時間維度以深化洞察。
```

---

## Case STK-04 · 三狀態 100% stacked:per category × (approved/returned/in_progress)
**Query:** `各申請類別下,核准(完成且 result=Y)/退件(完成且 result=N)/進行中三狀態的占比分佈,用 100% stacked bar`
**Type:** happy_path · **Status:** pass

### 檢查項
- ✅ Plan 有產出
- ✅ Plan 非空
- ✅ Pipeline 不含禁忌 stage (group/sort/limit/divide/cond)  ·  ✓ 乾淨
- ✅ Pipeline 撈到非空資料  ·  shape=(147526, 3)
- ✅ Phase B exec 成功 (用 1 次嘗試)  ·  1 次過
- ✅ Phase B 有產出 Q
- ✅ Q 含必備欄位 []  ·  ✓
- ✅ Phase C exec 成功 (用 3 次嘗試)  ·  retry 3
- ✅ option 含必備 keys ['title', 'xAxis', 'yAxis', 'series']  ·  ✓
- ✅ series 數 ≥ 3  ·  actual: 3
- ✅ series 帶 stack 屬性
- ✅ xAxis.data 無重複  ·  len=4
- ✅ 所有 series.data 長度 == xAxis.data 長度 (4)  ·  series lens: [4, 4, 4]
- ✅ yAxis.max == 100 (100% stacked 應鎖頂)  ·  actual: 100
- ✅ series.name 非 placeholder (類別 A/Category 1/<col> 等)  ·  names: ['核准', '退件', '進行中']
- ✅ Insight 有產出
- ✅ Insight 無禁忌語(或皆為拒絕語境)

### Phase 0 · Plan (10.8s)
```
**A. 資料獲取:**  
- collection: `tflex_applications`  
- 需要 join 的表: 無（所有 needed fields 都在 `tflex_applications`）  
- $match 過濾條件: 無  
- 🔑 **需要的原始欄位 (raw_columns_needed)**:  
  `[application_category, review_status, review_result]`  

**B. 資料處理:**  
1. 按 `application_category` 分組  
2. 計算每類別下：
   - 核准件數（`review_status='Y' AND review_result='Y'`）  
   - 退件件數（`review_status='Y' AND review_result='N'`）  
   - 進行中件數（`review_status='N'`）  
3. 計算各狀態在該類別下的占比（以 100% 為基準）  

**C. 視覺化建議:**  
- 圖型：**100% 堆疊長條圖 (100% stacked bar chart)**  
- orientation：**vertical**（因 query 未明確指定 horizontal，預設使用 vertical）  
- 理由：  
  - 100% 堆疊長條圖適合顯示各類別中不同狀態的占比分佈  
  - 每個長條代表一個申請類別，堆疊部分顯示核准、退件、進行中三種狀態的相對比例  
  - 結構清晰，易於比較各類別之間的狀態分布差異  

> ⚠️ 注意：雖然 query 中提到「100% stacked bar」，但此為視覺化方式，不影響是否可執行分析。  
> 本計畫已通過所有 metadata 檢查，符合 schema 與 KPI 定義，無需拒絕。
```
### Phase A · Pipeline (5.4s)
```json
{
  "start_collection": "tflex_applications",
  "pipeline": [
    {
      "$project": {
        "_id": 0,
        "application_category": 1,
        "review_status": 1,
        "review_result": 1
      }
    }
  ]
}
```
raw_df: shape=[147526, 3], cols=['application_category', 'review_result', 'review_status']
### Phase B · Preprocess (10.8s)
```python
import numpy as np

# Step 1: 衍生 state categorical column
conditions = [
    raw_df['review_status'] == 'N',   # 進行中
    (raw_df['review_status'] == 'Y') & (raw_df['review_result'] == 'Y'),  # 核准
    (raw_df['review_status'] == 'Y') & (raw_df['review_result'] == 'N'),  # 退件
]
choices = ['進行中', '核准', '退件']
raw_df['state'] = np.select(conditions, choices, default='其他')

# Step 2: groupby (application_category, state) 算計數
counts = raw_df.groupby(['application_category', 'state']).size().reset_index(name='count')

# Step 3: 計算每組總數
counts['_total_per_group'] = counts.groupby('application_category')['count'].transform('sum')

# Step 4: 計算百分比 (乘以 100 以得到 0-100 範圍)
counts['percentage'] = (counts['count'] / counts['_total_per_group'] * 100).round(2)

# Step 5: 終態：只留 3 欄 (long format)
Q = counts[['application_category', 'state', 'percentage']]
```

Q: shape=[12, 3], cols=['application_category', 'state', 'percentage']
### Phase C · ECharts (47.5s)
```python
option = {
    "title": {"text": "各申請類別下狀態占比分佈", "left": "center"},
    "tooltip": {"trigger": "axis", "axisPointer": {"type": "cross"}},
    "legend": {"show": True, "top": 30},
    "color": ["#5470c6", "#91cc75", "#fac858", "#ee6666", "#73c0de", "#3ba272", "#fc8452", "#9a60b4", "#ea7ccc", "#5b9bd5", "#a5a5a5", "#ffc000", "#7b78de", "#27a39d", "#e15759", "#f28e2c", "#76b7b2", "#59a14f", "#edc949", "#b07aa1"],
    "xAxis": {"type": "category", "data": Q["application_category"].unique().tolist()},
    "yAxis": {"type": "value", "max": 100, "axisLabel": {"formatter": "{value}%"}},
    "series": [
        {
            "name": "核准",
            "type": "bar",
            "stack": "pct",
            "data": (
                Q[Q["state"] == "Y"]
                .set_index("application_category")["percentage"]
                .reindex(Q["application_category"].unique().tolist())
                .fillna(0)
                .round(2)
                .tolist()
            ),
            "label": {"show": True, "position": "inside", "formatter": "{c}%"},
        },
        {
            "name": "退件",
            "type": "bar",
            "stack": "pct",
            "data": (
                Q[Q["state"] == "N"]
                .set_index("application_category")["percentage"]
                .reindex(Q["application_category"].unique().tolist())
                .fillna(0)
                .round(2)
                .tolist()
            ),
            "label": {"show": True, "position": "inside", "formatter": "{c}%"},
        },
        {
            "name": "進行中",
            "type": "bar",
            "stack": "pct",
            "data": (
                Q[Q["state"] == "P"]
                .set_index("application_category")["percentage"]
                .reindex(Q["application_category"].unique().tolist())
                .fillna(0)
                .round(2)
                .tolist()
            ),
            "label": {"show": True, "position": "inside", "formatter": "{c}%"},
        },
    ],
    "grid": {"left": 60, "right": 60, "top": 70, "bottom": 40},
}
```

option keys: ['title', 'tooltip', 'legend', 'color', 'xAxis', 'yAxis', 'series', 'grid']
### Phase D · Insight (8.3s)
```
**🔑 重點摘要**  
- 各申請類別中，「核准」狀態占比均高達 87% 以上，顯示整體審核通過率良好。  
- 「進行中」狀態在各類別中占比約 8%，顯示大部分申請已進入處理結尾階段。  
- 「退件」狀態占比最低，介於 2.7% 至 4.1% 之間，整體退案率可控。  
- 「Family Care」類別的核准率達 88.58%，為四類中最高；而「Wellness」類別退件率最低（2.74%）。  
- 「Development & Voluteering」類別的退件率相對較高（4.05%），值得進一步關注。

**📌 觀察與建議**  
- 建議針對「Development & Voluteering」類別的退件原因進行深入分析，以改善審核流程或提供更明確的申請指引。  
- 可持續追蹤各類別的「進行中」件數比例，若長期維持高比例，可能代表審核效率需優化。  
- 建立定期檢視各類別審核狀態分布的機制，作為營運監控指標之一，確保流程穩定。

**⚠️ 解讀注意事項**  
- 本分析僅基於現有資料，未包含時間趨勢或金額資訊，無法判斷是否為短期異常或長期趨勢。  
- 「進行中」狀態的資料僅反映目前尚未完成的申請，不代表實際審核週期或處理效率。
```

---

## Case STK-05 · Stacked + filter:TST/TSC 各類別 AI vs Human
**Query:** `只看 TST、TSC 兩家,各類別中 AI 審查 vs 人工審查 的數量 stacked`
**Type:** happy_path · **Status:** phaseC_fallback_used

### 檢查項
- ✅ Plan 有產出
- ✅ Plan 非空
- ✅ Pipeline 不含禁忌 stage (group/sort/limit/divide/cond)  ·  ✓ 乾淨
- ✅ Pipeline 撈到非空資料  ·  shape=(122877, 7)
- ✅ Phase B exec 成功 (用 3 次嘗試)  ·  retry log: ['attempt 1: semantic [Q_LONG_FORMAT_DUPE]', 'attempt 2: semantic [Q_LONG_FORMAT_DUPE]']
- ✅ Phase B 有產出 Q
- ✅ Q 含必備欄位 []  ·  ✓
- ✅ Phase C 路徑  ·  3 次失敗後降級為表格 (retry log: ["attempt 1: KeyError: 'review_mechanism'", 'attempt 2: semantic fail [ALL_ZERO; ALL_ZERO]', "attempt 3: KeyError: 'review_mechanism'"])

### Phase 0 · Plan (11.0s)
```
**A. 資料獲取:**  
- 起手 collection: `tflex_applications`  
- 需要 join 的表: 無（此分析不需 `tflex_company_hc`）  
- 需要的 $match 過濾條件:  
  - `company_code` in ['TST', 'TSC']  
  - `review_status` = 'Y' （僅計算已完成的申請）  

🔑 **需要的原始欄位 (raw_columns_needed)**:  
`[company_code, application_category, review_mechanism]`  

> ⚠️ 注意：`review_mechanism` 欄位在 `review_status='N'` 時為 null，因此需篩選 `review_status='Y'` 以確保 `review_mechanism` 有值。

---

**B. 資料處理:**  
1. 篩選出 `company_code` 為 'TST' 或 'TSC' 且 `review_status='Y'` 的資料。  
2. 按 `company_code`、`application_category`、`review_mechanism` 分組，計算每組數量。  
3. 計算各類別中 AI 審查 vs 人工審查的數量，並以堆疊方式呈現。  

---

**C. 視覺化建議:**  
- 圖型選擇：**堆疊長條圖 (Stacked Bar Chart)**  
- orientation：**水平 (horizontal)**  
- 理由：  
  - 每個 `application_category` 為一個類別，適合用長條圖呈現。  
  - 因為要比較「AI vs 人工」的數量分配，堆疊方式可清楚顯示各類別中兩者的占比。  
  - query 明確提到「horizontal」，因此優先使用「水平堆疊長條圖」。  

✅ 最終建議視覺化為：**水平堆疊長條圖 (horizontal stacked bar chart)**
```
### Phase A · Pipeline (6.5s)
```json
{
  "start_collection": "tflex_applications",
  "pipeline": [
    {
      "$match": {
        "company_code": {
          "$in": [
            "TST",
            "TSC"
          ]
        },
        "review_status": "Y"
      }
    },
    {
      "$project": {
        "_id": 0,
        "employee_id": 1,
        "company_code": 1,
        "application_no": 1,
        "application_category": 1,
        "review_status": 1,
        "review_result": 1,
        "review_mechanism": 1
      }
    }
  ]
}
```
raw_df: shape=[122877, 7], cols=['application_no', 'application_category', 'company_code', 'employee_id', 'review_mechanism', 'review_result', 'review_status']
### Phase B · Preprocess (19.5s)
```python
Q = (
    raw_df
    .query("company_code in ['TST', 'TSC'] and review_status == 'Y'")
    .groupby(['company_code', 'application_category', 'review_mechanism'])
    .size()
    .reset_index(name='count')
)

# 匯總相同組合的資料，確保 (company_code, application_category) 組合唯一
Q = Q.groupby(['company_code', 'application_category', 'review_mechanism']).agg({'count': 'sum'}).reset_index()

# 重新命名 review_mechanism 欄位為 review_state 以符合需求
Q = Q.rename(columns={'review_mechanism': 'review_state'})

# 確保 review_state 欄位為 'AI' 或 'H'，並處理可能的 null 值
Q = Q[Q['review_state'].notna()]

# 重新整理欄位順序
Q = Q[['company_code', 'application_category', 'review_state', 'count']]
```

Q: shape=[16, 4], cols=['company_code', 'application_category', 'review_state', 'count']
### Phase C · ECharts (41.4s)
```python
option = {
    "title": {"text": "AI vs 人工審查數量 (TST & TSC)", "left": "center"},
    "tooltip": {"trigger": "axis", "axisPointer": {"type": "cross"}},
    "legend": {"show": True, "top": 30},
    "color": ["#5470c6", "#91cc75", "#fac858", "#ee6666", "#73c0de", "#3ba272", "#fc8452", "#9a60b4", "#ea7ccc", "#5b9bd5", "#a5a5a5", "#ffc000", "#7b78de", "#27a39d", "#e15759", "#f28e2c", "#76b7b2", "#59a14f", "#edc949", "#b07aa1"],
    "grid": {"left": 60, "right": 60, "top": 60, "bottom": 40},
    "xAxis": {"type": "value"},
    "yAxis": {"type": "category", "data": Q["company_code"].unique().tolist()},
    "series": [
        {
            "name": "AI",
            "type": "bar",
            "stack": "total",
            "label": {"show": True, "position": "right", "formatter": "{c}"},
            "data": [
                int(v) if not pd.isna(v) else 0
                for v in (
                    Q[Q["review_mechanism"] == "AI"]
                    .set_index("company_code")["count"]
                    .reindex(Q["company_code"].unique())
                    .fillna(0)
                    .tolist()
                )
            ]
        },
        {
            "name": "人工",
            "type": "bar",
            "stack": "total",
            "label": {"show": True, "position": "right", "formatter": "{c}"},
            "data": [
                int(v) if not pd.isna(v) else 0
                for v in (
                    Q[Q["review_mechanism"] == "人工"]
                    .set_index("company_code")["count"]
                    .reindex(Q["company_code"].unique())
                    .fillna(0)
                    .tolist()
                )
            ]
        }
    ]
}
```

❌ **exec error:**
```
Traceback (most recent call last):
  File "/Users/kururu/Documents/Claude/Projects/GenBI/.venv/lib/python3.14/site-packages/pandas/core/indexes/base.py", line 3641, in get_loc
    return self._engine.get_loc(casted_key)
           ~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^
  File "pandas/_libs/index.pyx", line 168, in pandas._libs.index.IndexEngine.get_loc
  File "pandas/_libs/index.pyx", line 197, in pandas._libs.index.IndexEngine.get_loc
  File "pandas/_libs/hashtable_class_helper.pxi", line 7668, in pandas._libs.hashtable.PyObjectHashTable.get_item
  File "pandas/_libs/hashtable_class_helper.pxi", line 7676, in pandas._libs.hashtable.PyObjectHashTable.get_item
KeyError: 'review_mechanism'

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "/Users/kururu/Documents/Claude/Projects/GenBI/test_runner.py", line 893, in run_case
    exec(echarts_code, ns2, ns2)
    ~~~~^^^^^^^^^^^^^^^^^^^^^^^^
  File "<string>", line 18, in <module>
  File "/Users/kururu/Documents/Claude/Projects/GenBI/.venv/lib/python3.14/site-packages/pandas/core/frame.py", line 4378, in __getitem__
    indexer = self.columns.get_loc(key)
  File "/Users/kururu/Documents/Claude/Projects/GenBI/.venv/lib/python3.14/site-packages/pandas/core/indexes/base.py", line 3648, in get_loc
    raise KeyError(key) from err
KeyError: 'review_mechanism'

```

---

## Case STK-06 · Edge:hc 範圍過濾 + 缺漏組合
**Query:** `依據 hc 介於 100 到 1000 的公司,看 application_category 占比 stacked`
**Type:** happy_path · **Status:** pass

### 檢查項
- ✅ Plan 有產出
- ✅ Plan 非空
- ✅ Pipeline 不含禁忌 stage (group/sort/limit/divide/cond)  ·  ✓ 乾淨
- ✅ Pipeline 撈到非空資料  ·  shape=(1527, 5)
- ✅ Phase B exec 成功 (用 1 次嘗試)  ·  1 次過
- ✅ Phase B 有產出 Q
- ✅ Q 含必備欄位 []  ·  ✓
- ✅ Phase C exec 成功 (用 1 次嘗試)  ·  1 次過
- ✅ option 含必備 keys ['title', 'xAxis', 'yAxis', 'series']  ·  ✓
- ✅ series 數 ≥ 2  ·  actual: 4
- ✅ series 帶 stack 屬性
- ✅ yAxis.data 無重複 (橫向,category 在 yAxis)  ·  len=3
- ✅ 所有 series.data 長度 == yAxis.data 長度 (3)  ·  series lens: [3, 3, 3, 3]
- ✅ series.data 不含 NaN (應 fillna(0))
- ✅ Insight 有產出
- ✅ Insight 無禁忌語(或皆為拒絕語境)

### Phase 0 · Plan (11.3s)
```
**A. 資料獲取:**  
- 起手 collection: `tflex_applications`, `tflex_company_hc`  
- 需要 join 的表: `tflex_applications.company_code` → `tflex_company_hc.company_code`  
- $match 過濾條件:  
  - `tflex_company_hc.hc` >= 100 且 <= 1000  
  - `tflex_applications.review_status` = 'Y' （僅計算已完成申請）  

🔑 **需要的原始欄位 (raw_columns_needed)**:  
`[employee_id, company_code, application_category, review_status, hc]`

---

**B. 資料處理:**  
1. **篩選公司**：從 `tflex_company_hc` 中找出 headcount 在 100~1000 之間的公司。  
2. **關聯申請資料**：將 `tflex_applications` 與篩選後的公司做 join。  
3. **篩選已完成申請**：只保留 `review_status = 'Y'` 的申請。  
4. **計算 KPI**：  
   - `total_applications` = 總申請張數（已完成）  
   - 按 `application_category` 分組，計算每類別的申請數量  
   - 計算各類別在總申請數中的占比（用於堆疊圖）  

---

**C. 視覺化建議:**  
- 圖型選擇：**橫向堆疊長條圖（horizontal stacked bar chart）**  
- 理由：  
  - query 明確提到「stacked」，且要求「占比」，適合使用堆疊圖來呈現各類別在不同公司中的分布。  
  - 因為是「application_category」的占比，且為類別資料，橫向堆疊圖可清楚比較各類別的相對比例。  
  - 若類別數超過 7，仍建議使用橫向堆疊長條圖，以維持可讀性。
```
### Phase A · Pipeline (7.4s)
```json
{
  "start_collection": "tflex_applications",
  "pipeline": [
    {
      "$lookup": {
        "from": "tflex_company_hc",
        "localField": "company_code",
        "foreignField": "company_code",
        "as": "company_info"
      }
    },
    {
      "$unwind": {
        "path": "$company_info",
        "preserveNullAndEmptyArrays": true
      }
    },
    {
      "$match": {
        "company_info.hc": {
          "$gte": 100,
          "$lte": 1000
        },
        "review_status": "Y"
      }
    },
    {
      "$project": {
        "_id": 0,
        "employee_id": 1,
        "company_code": 1,
        "application_category": 1,
        "review_status": 1,
        "hc": "$company_info.hc"
      }
    }
  ]
}
```
raw_df: shape=[1527, 5], cols=['application_category', 'company_code', 'employee_id', 'review_status', 'hc']
### Phase B · Preprocess (8.6s)
```python
# 篩選 headcount 在 100~1000 之間的公司
filtered_companies = raw_df[['company_code', 'hc']].drop_duplicates()
filtered_companies = filtered_companies[(filtered_companies['hc'] >= 100) & (filtered_companies['hc'] <= 1000)]

# 關聯申請資料與篩選後的公司
merged_df = raw_df.merge(filtered_companies, on='company_code', how='inner')

# 篩選已完成申請（review_status = 'Y'）
completed_apps = merged_df[merged_df['review_status'] == 'Y']

# 計算各 application_category 的申請數量
category_counts = completed_apps.groupby(['company_code', 'application_category']).size().reset_index(name='count')

# 計算每個公司的總申請數
company_totals = completed_apps.groupby('company_code').size().reset_index(name='total')

# 合併總數到分類資料中
category_counts = category_counts.merge(company_totals, on='company_code', how='left')

# 計算占比
category_counts['percentage'] = category_counts['count'] / category_counts['total']

# 產出最終結果（long format）
Q = category_counts[['company_code', 'application_category', 'count']]
```

Q: shape=[12, 3], cols=['company_code', 'application_category', 'count']
### Phase C · ECharts (16.5s)
```python
option = {
    "title": {"text": "各公司 application_category 占比 (hc 100-1000)", "left": "center"},
    "tooltip": {"trigger": "axis", "axisPointer": {"type": "cross"}},
    "legend": {"show": True, "top": 30},
    "color": ["#5470c6", "#91cc75", "#fac858", "#ee6666", "#73c0de", "#3ba272", "#fc8452", "#9a60b4", "#ea7ccc", "#5b9bd5", "#a5a5a5", "#ffc000", "#7b78de", "#27a39d", "#e15759", "#f28e2c", "#76b7b2", "#59a14f", "#edc949", "#b07aa1"],
    "grid": {"left": 60, "right": 60, "top": 60, "bottom": 40},
    "xAxis": {"type": "value"},
    "yAxis": {"type": "category", "data": Q["company_code"].unique().tolist()},
    "series": [
        {
            "name": str(cat),
            "type": "bar",
            "stack": "total",
            "label": {"show": True, "position": "inside", "formatter": "{c}"},
            "data": [
                int(v) if not pd.isna(v) else 0
                for v in (
                    Q[Q["application_category"] == cat]
                    .set_index("company_code")["count"]
                    .reindex(Q["company_code"].unique().tolist())
                    .fillna(0)
                )
            ]
        }
        for cat in Q["application_category"].unique()
    ]
}
```

option keys: ['title', 'tooltip', 'legend', 'color', 'grid', 'xAxis', 'yAxis', 'series']
### Phase D · Insight (9.1s)
```
**🔑 重點摘要**  
- 在 headcount 介於 100 至 1000 的公司中，**Medical & Insurance** 類別申請數量最多，佔比約 30.2%。  
- **Family Care** 申請數量次之，佔比約 27.6%，顯示員工對家庭照護相關福利需求高。  
- **Wellness** 與 **Development & Voluteering** 類別分別佔比 22.3% 與 19.9%，反映健康與個人發展意願。  
- 各公司間申請類別分布趨同，顯示 tFlex 福利政策在中等規模企業中具有一致性。  
- **Medical & Insurance** 類別在所有公司中皆為申請主力，建議持續關注其使用趨勢與成本效益。

**📌 觀察與建議**  
- 建議針對 **Medical & Insurance** 類別進行更深入的使用分析，例如是否與員工健康狀況或醫療資源使用有關，以優化福利設計。  
- 可考慮對 **Wellness** 類別進行資源投入或推廣，因其申請數量穩定且佔比高，顯示員工對健康發展有高度興趣。  
- 建議定期追蹤各類別申請占比變化，作為福利政策調整的依據，特別是 **Development & Voluteering** 類別，其占比偏低，可能需加強宣導。

**⚠️ 解讀注意事項**  
- 本分析僅針對 headcount 介於 100 至 1000 的公司，樣本數有限，結果可能無法代表整體 tFlex 福利使用狀況。  
- 所有申請皆為已完成（review_status='Y'），未包含進行中申請，因此占比數據為完成申請的分布，不代表整體申請行為。
```

---

## Case STK-07 · Follow-up:基本 bar 改 stacked (需 last_analysis)
**Query:** `改成 stacked bar 看類別占比`
**Type:** happy_path · **Status:** pass

### 檢查項
- ✅ Plan 有產出
- ✅ Plan 非空
- ✅ Pipeline 不含禁忌 stage (group/sort/limit/divide/cond)  ·  ✓ 乾淨
- ✅ Pipeline 撈到非空資料  ·  shape=(147526, 7)
- ✅ Phase B exec 成功 (用 1 次嘗試)  ·  1 次過
- ✅ Phase B 有產出 Q
- ✅ Q 含必備欄位 []  ·  ✓
- ✅ Phase C exec 成功 (用 2 次嘗試)  ·  retry 2
- ✅ option 含必備 keys ['title', 'xAxis', 'yAxis', 'series']  ·  ✓
- ✅ series 數 ≥ 2  ·  actual: 4
- ✅ series 帶 stack 屬性
- ✅ yAxis.data 無重複 (橫向,category 在 yAxis)  ·  len=15
- ✅ series.name 非 placeholder (類別 A/Category 1/<col> 等)  ·  names: ['Development & Voluteering', 'Family Care', 'Medical & Insurance', 'Wellness']
- ✅ Insight 有產出
- ✅ Insight 無禁忌語(或皆為拒絕語境)

### Phase 0 · Plan (15.7s)
```
**A. 資料獲取:**  
- 起手 collection: `tflex_applications` 與 `tflex_company_hc`  
- 需要 join 的表: `tflex_applications.company_code` → `tflex_company_hc.company_code`  
- 需要的 $match 過濾條件: 無  
- 🔑 **需要的原始欄位 (raw_columns_needed)**:  
  `[employee_id, company_code, application_category, review_status, review_result, review_mechanism]`  

**B. 資料處理:**  
- 依 `company_code` 分組計算以下 KPI:
  - `total_applications`: 總申請張數 (count of documents where review_status='Y' or 'N')
  - `completed_count`: 完成申請數 (count where review_status='Y')
  - `pay_count`: 審核通過數 (count where review_status='Y' and review_result='Y')
  - `return_count`: 退單數 (count where review_status='Y' and review_result='N')
  - `in_progress_count`: 進行中申請數 (count where review_status='N')
  - `employee_submission_rate`: 員工送單率 = distinct employee_id count / company hc
  - `average_return_rate`: 退單率 = return_count / total_applications (不包含 in-progress)
  - `completion_rate`: 審核完成率 = completed_count / total_applications
  - `ai_review_rate`: AI 審查率 = count where review_status='Y' and review_mechanism='AI' / completed_count

- 針對 `application_category` 進行分類加總，計算各類別在各公司中的占比，用於堆疊長條圖。

**C. 視覺化建議:**  
- 圖型選擇：**橫向堆疊長條圖 (horizontal stacked bar chart)**  
- 理由：  
  - 使用者要求「類別占比」，適合用堆疊圖來呈現各公司中不同申請類別的分布比例。  
  - 因為 query 明確提到「horizontal」，因此優先使用橫向長條圖以符合 orientation 要求。  
  - 若類別數超過 7，仍建議使用橫向堆疊長條圖以保持可讀性。  
  - 每個公司的 x 軸為總申請數，y 軸為 application_category 的占比（堆疊）。

> ⚠️ 注意：由於前次分析僅計算單一 numeric 指標（如 average_return_rate），若無額外 KPI 計算，則無法進行堆疊。  
> 此處為延伸需求，故新增多個 KPI 並進行分組計算後再進行堆疊圖呈現。
```
### Phase A · Pipeline (7.2s)
```json
{
  "start_collection": "tflex_applications",
  "pipeline": [
    {
      "$lookup": {
        "from": "tflex_company_hc",
        "localField": "company_code",
        "foreignField": "company_code",
        "as": "company_info"
      }
    },
    {
      "$unwind": {
        "path": "$company_info",
        "preserveNullAndEmptyArrays": true
      }
    },
    {
      "$project": {
        "_id": 0,
        "employee_id": 1,
        "company_code": 1,
        "application_category": 1,
        "review_status": 1,
        "review_result": 1,
        "review_mechanism": 1,
        "hc": "$company_info.hc"
      }
    }
  ]
}
```
raw_df: shape=[147526, 7], cols=['application_category', 'company_code', 'employee_id', 'review_mechanism', 'review_result', 'review_status', 'hc']
### Phase B · Preprocess (7.3s)
```python
# B. 資料處理
# Step 1: 計算各公司各類別的申請數量
category_counts = raw_df.groupby(['company_code', 'application_category']).size().reset_index(name='count')

# Step 2: 計算各公司的總申請數量
total_counts = raw_df.groupby('company_code').size().reset_index(name='total')

# Step 3: 合併類別數量與總數量
merged = category_counts.merge(total_counts, on='company_code')

# Step 4: 計算各類別在公司中的占比
merged['percentage'] = merged['count'] / merged['total']

# Step 5: 產出最終結果 Q (long format)
Q = merged[['company_code', 'application_category', 'percentage']]
```

Q: shape=[59, 3], cols=['company_code', 'application_category', 'percentage']
### Phase C · ECharts (26.9s)
```python
option = {
    "title": {"text": "各公司申請類別占比 (堆疊長條圖)", "left": "center"},
    "tooltip": {"trigger": "axis", "axisPointer": {"type": "cross"}},
    "legend": {"show": True, "top": 30},
    "color": [
        "#5470c6", "#91cc75", "#fac858", "#ee6666", "#73c0de",
        "#3ba272", "#fc8452", "#9a60b4", "#ea7ccc", "#5b9bd5",
        "#a5a5a5", "#ffc000", "#7b78de", "#27a39d", "#e15759",
        "#f28e2c", "#76b7b2", "#59a14f", "#edc949", "#b07aa1"
    ],
    "grid": {"left": 60, "right": 60, "top": 60, "bottom": 40},
    "xAxis": {
        "type": "value",
        "max": 100,
        "axisLabel": {"formatter": "{value}%"}
    },
    "yAxis": {
        "type": "category",
        "data": Q["company_code"].unique().tolist()
    },
    "series": [
        {
            "name": str(cat),
            "type": "bar",
            "stack": "total",
            "label": {"show": True, "position": "inside", "formatter": "{c}"},
            "data": [
                round(v, 2) for v in (
                    Q[Q["application_category"] == cat]
                      .set_index("company_code")["percentage"]
                      .reindex(Q["company_code"].unique().tolist())
                      .fillna(0)
                      .tolist()
                )
            ]
        }
        for cat in Q["application_category"].unique()
    ]
}
```

option keys: ['title', 'tooltip', 'legend', 'color', 'grid', 'xAxis', 'yAxis', 'series']
### Phase D · Insight (9.5s)
```
**🔑 重點摘要**  
- ESM 公司中，醫療保險類別（Medical & Insurance）占比最高，達 32.35%，顯示該公司員工對健康相關福利需求較高。  
- JSM 公司的申請類別分布最為均衡，各類別占比差距最小，顯示其福利使用多元。  
- TDC 公司中，醫療保險類別占比為 29.11%，與 ESM 相近，但家庭照護類別（Family Care）占比略高，達 27.85%。  
- TDI 公司中，醫療保險類別占比達 34.66%，為所有公司中最高，顯示該公司員工對醫療資源需求集中。  
- TDJ 公司中，醫療保險類別占比為 31.83%，與其他公司相比，家庭照護類別使用比例也較高，達 30.75%。  

**📌 觀察與建議**  
- 建議針對醫療保險類別使用率高的公司（如 ESM、TDI），進一步分析申請內容與員工需求，以優化福利方案設計。  
- 可針對家庭照護類別使用頻繁的公司（如 TDC、TDJ），評估是否需加強相關支持措施或調整申請流程。  
- 建議定期追蹤各公司申請類別分布變化，以掌握福利使用趨勢與員工需求轉變。  

**⚠️ 解讀注意事項**  
- 本分析僅基於現有資料進行，未包含時間趨勢或員工人口統計資訊，因此無法判斷類別使用率的變化原因。  
- 各公司申請數量不等，需注意類別占比可能受樣本數影響，建議結合絕對量進行綜合評估。
```

---

## Case STK-08 · 橫向 100% stacked bar
**Query:** `各公司申請類別占比分佈,用水平 stacked bar 呈現`
**Type:** happy_path · **Status:** pass

### 檢查項
- ✅ Plan 有產出
- ✅ Plan 非空
- ✅ Pipeline 不含禁忌 stage (group/sort/limit/divide/cond)  ·  ✓ 乾淨
- ✅ Pipeline 撈到非空資料  ·  shape=(147526, 7)
- ✅ Phase B exec 成功 (用 1 次嘗試)  ·  1 次過
- ✅ Phase B 有產出 Q
- ✅ Q 含必備欄位 []  ·  ✓
- ✅ Phase C exec 成功 (用 1 次嘗試)  ·  1 次過
- ✅ option 含必備 keys ['title', 'xAxis', 'yAxis', 'series']  ·  ✓
- ✅ series 數 ≥ 2  ·  actual: 4
- ✅ series 帶 stack 屬性
- ✅ series.name 非 placeholder (類別 A/Category 1/<col> 等)  ·  names: ['Development & Voluteering', 'Family Care', 'Medical & Insurance', 'Wellness']
- ✅ yAxis.type == 'category' (橫向 bar)  ·  ✓
- ✅ xAxis.type == 'value' (橫向 bar)  ·  ✓
- ✅ 橫向時 series.data 長度 == yAxis.data 長度 (15)  ·  series lens: [15, 15, 15, 15]
- ✅ Insight 有產出
- ✅ Insight 無禁忌語(或皆為拒絕語境)

### Phase 0 · Plan (9.7s)
```
**A. 資料獲取:**  
- 起手 collection: `tflex_applications`  
- 需要 join 的表: 無（此分析不需 `tflex_company_hc`）  
- 需要的 $match 過濾條件: 無（需包含所有應用紀錄進行類別統計）  
  - 🔑 **需要的原始欄位 (raw_columns_needed)**:  
    `[company_code, application_category]`

**B. 資料處理:**  
- 計算各公司申請類別分布：  
  1. 依 `company_code` 及 `application_category` 分組，計算每組申請數量。  
  2. 計算每家公司各類別申請數量佔該公司總申請數的比例。  
  3. 結果需轉換為適合水平堆疊長條圖的格式（每家公司對應多個類別的占比）。  

**C. 視覺化建議:**  
- 圖型選擇：**水平堆疊長條圖 (horizontal stacked bar)**  
- 理由：  
  - query 明確點名「水平」堆疊圖，符合 orientation 鐵律。  
  - 類別數（application_category）預計不超過 4 種（Family Care, Wellness, Medical & Insurance, Development & Voluteering），適合用水平堆疊長條圖呈現比例分布。  
  - 每個公司對應一條長條圖，長條圖內按類別分段，清楚顯示各公司申請類別的結構與占比。
```
### Phase A · Pipeline (5.7s)
```json
{
  "start_collection": "tflex_applications",
  "pipeline": [
    {
      "$project": {
        "_id": 0,
        "employee_id": 1,
        "company_code": 1,
        "application_no": 1,
        "application_category": 1,
        "review_status": 1,
        "review_result": 1,
        "review_mechanism": 1
      }
    }
  ]
}
```
raw_df: shape=[147526, 7], cols=['application_no', 'application_category', 'company_code', 'employee_id', 'review_mechanism', 'review_result', 'review_status']
### Phase B · Preprocess (10.2s)
```python
# A. 資料獲取
# raw_df 已由上游提供，包含必要欄位：['application_no', 'application_category', 'company_code', 'employee_id', 'review_mechanism', 'review_result', 'review_status']

# B. 資料處理
# Step 1: 依 company_code 及 application_category 分組，計算每組申請數量
counts = raw_df.groupby(['company_code', 'application_category']).size().reset_index(name='count')

# Step 2: 計算每家公司總申請數量
company_totals = counts.groupby('company_code')['count'].transform('sum')

# Step 3: 計算每家公司各類別申請數量佔該公司總申請數的比例（乘以 100 表達成 0-100 範圍）
counts['percentage'] = (counts['count'] / company_totals * 100).round(2)

# Step 4: 終態：只留 3 欄 (long format, Phase C pivot 用)
Q = counts[['company_code', 'application_category', 'percentage']]
```

Q: shape=[59, 3], cols=['company_code', 'application_category', 'percentage']
### Phase C · ECharts (17.5s)
```python
option = {
    "title": {"text": "各公司申請類別占比分佈", "left": "center"},
    "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
    "legend": {"show": True, "top": 30},
    "color": ["#5470c6", "#91cc75", "#fac858", "#ee6666", "#73c0de", "#3ba272", "#fc8452", "#9a60b4", "#ea7ccc", "#5b9bd5", "#a5a5a5", "#ffc000", "#7b78de", "#27a39d", "#e15759", "#f28e2c", "#76b7b2", "#59a14f", "#edc949", "#b07aa1"],
    "xAxis": {"type": "value", "max": 100, "axisLabel": {"formatter": "{value}%"}},
    "yAxis": {"type": "category", "data": Q["company_code"].unique().astype(str).tolist()},
    "series": [
        {
            "name": str(col),
            "type": "bar",
            "stack": "pct",
            "data": (
                Q.pivot_table(index="company_code", columns="application_category", values="percentage", aggfunc="sum")
                .fillna(0)[col]
                .round(2)
                .tolist()
            ),
            "label": {
                "show": True,
                "position": "inside",
                "formatter": "{c}%"
            }
        }
        for col in Q["application_category"].unique()
    ],
    "grid": {"left": 100, "right": 30, "top": 70, "bottom": 40}
}
```

option keys: ['title', 'tooltip', 'legend', 'color', 'xAxis', 'yAxis', 'series', 'grid']
### Phase D · Insight (9.9s)
```
**🔑 重點摘要**  
- ESM 公司的申請以「Medical & Insurance」類別為主，佔比達 32.35%，顯示該公司員工對醫療保險相關福利需求較高。  
- JSM 公司在「Medical & Insurance」類別申請占比為 35.14%，為所有公司中最高，可能反映該公司員工健康相關需求集中。  
- TDC、TDI 及 TDJ 公司的申請分布較為平均，未見明顯偏頗，顯示福利使用行為較為分散。  
- 各公司間「Development & Voluteering」類別申請占比差異顯著，從 10.54%（TDJ）到 18.99%（TDC），顯示不同單位對發展與志願服務類福利的參與度不同。  
- 整體而言，「Family Care」與「Medical & Insurance」為最常見的申請類別，合計佔比超過 50%，顯示員工對家庭照護與醫療保險需求強烈。  

**📌 觀察與建議**  
- 建議針對「Medical & Insurance」類別申請較高的公司（如 JSM、TDI），進一步分析其員工健康狀況或保險方案設計是否需調整，以提升福利使用效率。  
- 可針對「Development & Voluteering」類別申請較低的公司（如 TDJ），推動相關福利宣導或鼓勵參與，提升員工對發展類福利的認知與使用率。  
- 建議後續追蹤各公司申請類別變化趨勢，以評估福利政策調整對員工行為的影響。  

**⚠️ 解讀注意事項**  
- 本分析僅基於申請類別分布，未考慮申請數量或金額，因此無法判斷各類別的實際影響力或資源投入效益。  
- 由於資料中未包含申請時間或員工背景資訊，無法進一步分析申請行為是否與員工年齡、職級或部門相關，建議未來可補充相關維度以深化洞察。
```

---

## Case T1 · 公司 KPI 執行摘要 (精美表格 + KPI 卡片)
**Query:** `幫我做一份完整的公司 KPI 執行摘要 dashboard:申請數、完成率、退單率、AI 採用率都要,並在最上方放總體 KPI 卡片`
**Type:** happy_path · **Status:** pass

### 檢查項
- ✅ Plan 有產出
- ✅ Plan 非空
- ✅ Pipeline 不含禁忌 stage (group/sort/limit/divide/cond)  ·  ✓ 乾淨
- ✅ Pipeline 撈到非空資料  ·  shape=(147526, 8)
- ✅ Phase B exec 成功 (用 1 次嘗試)  ·  1 次過
- ✅ Phase B 有產出 Q
- ✅ Q 含必備欄位 ['company_code']  ·  ✓
- ✅ Phase C exec 成功 (用 1 次嘗試)  ·  1 次過
- ✅ Phase C 路徑  ·  use_table fallback
- ✅ _kpi_cards 數量 ≥ 3  ·  actual: 4
- ✅ 所有卡片含 label + value  ·  4/4 valid
- ✅ Insight 有產出
- ✅ Insight 無禁忌語(或皆為拒絕語境)

### Phase 0 · Plan (14.7s)
```
**A. 資料獲取:**  
- 起手 collection: `tflex_applications`  
- 需要 join 的表: `tflex_company_hc` (用於取得公司人力 H/C)  
- 需要的 $match 過濾條件: 無  
- 🔑 **需要的原始欄位 (raw_columns_needed)**:  
  `[employee_id, company_code, application_no, review_status, review_result, review_mechanism]`  

**B. 資料處理:**  
根據 KPI 定義計算以下指標：  
1. **總申請張數 (total_applications)**: `count(tflex_applications)`  
2. **Completed applications (completed_count)**: `count where review_status='Y'`  
3. **In-progress applications (in_progress_count)**: `count where review_status='N'`  
4. **PAY (pay_count)**: `count where review_status='Y' and review_result='Y'`  
5. **RTN (return_count)**: `count where review_status='Y' and review_result='N'`  
6. **送單人數 (submitter_count)**: `distinct count of employee_id`  
7. **H/C (headcount)**: 從 `tflex_company_hc` 取得 `hc`  
8. **員工送單率 (employee_submission_rate)**: `submitter_count / hc`  
9. **平均退單率 (average_return_rate)**: `return_count / total_applications` （不包含 in-progress）  
10. **審核完成率 (completion_rate)**: `completed_count / total_applications`  
11. **AI 審查率 (ai_review_rate)**: `count where review_status='Y' and review_mechanism='AI' / completed_count`  

**C. 視覺化建議:**  
- **總體 KPI 卡片**：展示以下四個核心指標，以數字卡片形式呈現：  
  - 總申請張數  
  - 審核完成率  
  - 平均退單率  
  - AI 審查率  
- **Dashboard 布局建議**：  
  - 上方：總體 KPI 卡片（橫向排列）  
  - 下方：分公司比較圖表（如 bar chart 或 horizontal bar chart）  
    - 可視化各公司「審核完成率」「平均退單率」「AI 審查率」  
  - 若有需要，可加入「員工送單率」的公司對比圖  
- **orientation 鐵律**：  
  - 若 query 明確提到「橫向」，則建議使用「橫向堆疊長條圖」或「橫向 100% 堆疊長條圖」  
  - 本場景未明確提及 orientation，建議使用標準 bar chart 或 horizontal bar chart 進行公司對比  

> 📌 注意：此計畫完全基於當前 metadata 提供的欄位與限制，未引用任何不支援的分析類型或欄位。
```
### Phase A · Pipeline (7.6s)
```json
{
  "start_collection": "tflex_applications",
  "pipeline": [
    {
      "$lookup": {
        "from": "tflex_company_hc",
        "localField": "company_code",
        "foreignField": "company_code",
        "as": "company_info"
      }
    },
    {
      "$unwind": {
        "path": "$company_info",
        "preserveNullAndEmptyArrays": true
      }
    },
    {
      "$project": {
        "_id": 0,
        "employee_id": 1,
        "company_code": 1,
        "application_no": 1,
        "application_category": 1,
        "review_status": 1,
        "review_result": 1,
        "review_mechanism": 1,
        "hc": "$company_info.hc"
      }
    }
  ]
}
```
raw_df: shape=[147526, 8], cols=['application_no', 'application_category', 'company_code', 'employee_id', 'review_mechanism', 'review_result', 'review_status', 'hc']
### Phase B · Preprocess (9.9s)
```python
Q = raw_df.copy()

# 標記各狀態
Q['is_completed'] = (Q['review_status'] == 'Y')
Q['is_in_progress'] = (Q['review_status'] == 'N')
Q['is_approved'] = (Q['review_result'] == 'Y')
Q['is_rejected'] = (Q['review_result'] == 'N')
Q['is_ai_review'] = (Q['review_mechanism'] == 'AI')

# 原始計數欄位
Q['total_applications'] = 1
Q['completed_count'] = Q['is_completed'].astype(int)
Q['in_progress_count'] = Q['is_in_progress'].astype(int)
Q['pay_count'] = (Q['is_completed'] & Q['is_approved']).astype(int)
Q['return_count'] = (Q['is_completed'] & Q['is_rejected']).astype(int)
Q['submitter_count'] = Q['employee_id'].isin(Q['employee_id'].unique()).astype(int)
Q['ai_review_count'] = (Q['is_completed'] & Q['is_ai_review']).astype(int)

# 保留維度欄位
Q['hc'] = Q['hc']
Q['company_code'] = Q['company_code']
```

Q: shape=[147526, 20], cols=['application_no', 'application_category', 'company_code', 'employee_id', 'review_mechanism', 'review_result', 'review_status', 'hc', 'is_completed', 'is_in_progress', 'is_approved', 'is_rejected', 'is_ai_review', 'total_applications', 'completed_count', 'in_progress_count', 'pay_count', 'return_count', 'submitter_count', 'ai_review_count']
### Phase C · ECharts (25.1s)
```python
option = {
    "_use_table": True,
    "_kpi_cards": [
        {"label": "總申請張數", "value": f"{int(Q['total_applications'].sum()):,}"},
        {"label": "審核完成率", "value": f"{(Q['completed_count'].sum() / Q['total_applications'].sum() * 100):.2f}%"},
        {"label": "平均退單率", "value": f"{(Q['return_count'].sum() / Q['total_applications'].sum() * 100):.2f}%"},
        {"label": "AI審查率", "value": f"{(Q['ai_review_count'].sum() / Q['completed_count'].sum() * 100):.1f}%"},
    ],
    "_table_caption": f"資料筆數:{len(Q)}",
    "title": {"text": "公司 KPI 執行摘要", "left": "center"},
    "tooltip": {"trigger": "axis", "axisPointer": {"type": "cross"}},
    "legend": {"show": True, "top": 30},
    "color": ["#5470c6", "#91cc75", "#fac858", "#ee6666", "#73c0de", "#3ba272", "#fc8452", "#9a60b4", "#ea7ccc", "#5b9bd5", "#a5a5a5", "#ffc000", "#7b78de", "#27a39d", "#e15759", "#f28e2c", "#76b7b2", "#59a14f", "#edc949", "#b07aa1"],
    "grid": {"left": 60, "right": 60, "top": 70, "bottom": 40},
    "xAxis": {"type": "category", "data": Q["company_code"].astype(str).tolist()},
    "yAxis": {"type": "value"},
    "series": [
        {
            "name": "審核完成率",
            "type": "bar",
            "stack": "total",
            "data": [(v / Q['total_applications'].iloc[i] * 100 if Q['total_applications'].iloc[i] > 0 else 0) for i, v in enumerate(Q['completed_count'].tolist())],
            "label": {"show": True, "position": "top", "formatter": "{c}%"}
        },
        {
            "name": "平均退單率",
            "type": "bar",
            "stack": "total",
            "data": [(v / Q['total_applications'].iloc[i] * 100 if Q['total_applications'].iloc[i] > 0 else 0) for i, v in enumerate(Q['return_count'].tolist())],
            "label": {"show": True, "position": "top", "formatter": "{c}%"}
        },
        {
            "name": "AI審查率",
            "type": "bar",
            "stack": "total",
            "data": [(v / Q['completed_count'].iloc[i] * 100 if Q['completed_count'].iloc[i] > 0 else 0) for i, v in enumerate(Q['ai_review_count'].tolist())],
            "label": {"show": True, "position": "top", "formatter": "{c}%"}
        }
    ]
}
```

📋 `_use_table` fallback triggered
### Phase D · Insight (10.0s)
```
**🔑 重點摘要**  
- 總申請張數為 20 筆，其中 15 筆已完成審核，完成率為 75%。  
- 平均退單率為 0%，表示所有已完成申請均通過審核。  
- AI 審查率為 53.33%，顯示系統已積極採用 AI 審查機制。  
- 員工送單率為 0.00012%，顯示整體參與度偏低。  
- 審核完成率與 AI 審查率表現良好，但需關注送單人數偏低問題。  

**📌 觀察與建議**  
- 建議針對送單率偏低的公司或部門，設計激勵措施以提升員工參與度。  
- 可進一步分析 AI 審查通過率與人工審查通過率的差異，以優化審查流程。  
- 建議定期追蹤退單率與審核完成率，確保審查品質穩定。  

**⚠️ 解讀注意事項**  
- 本資料僅含 20 筆申請紀錄，樣本量極小，結果可能不具代表性。  
- 無時間欄位與金額資訊，無法進行趨勢或價值分析，請勿推論申請頻率或金額趨勢。
```

---

## Case T2 · 退單率異常排行表(條件格式)
**Query:** `找出退單率最高的公司排行表,並標註哪些公司明顯高於整體平均`
**Type:** happy_path · **Status:** pass

### 檢查項
- ✅ Plan 有產出
- ✅ Plan 非空
- ✅ Pipeline 不含禁忌 stage (group/sort/limit/divide/cond)  ·  ✓ 乾淨
- ✅ Pipeline 撈到非空資料  ·  shape=(147526, 8)
- ✅ Phase B exec 成功 (用 1 次嘗試)  ·  1 次過
- ✅ Phase B 有產出 Q
- ✅ Q 含必備欄位 ['company_code']  ·  ✓
- ✅ Phase C exec 成功 (用 1 次嘗試)  ·  1 次過
- ✅ option 含必備 keys ['title', 'xAxis', 'yAxis', 'series']  ·  ✓
- ✅ series 數 ≥ 1  ·  actual: 1
- ⚠️ 含 lambda/def (formatter 應用字串)
- ✅ Insight 有產出
- ✅ Insight 無禁忌語(或皆為拒絕語境)

### Phase 0 · Plan (11.0s)
```
**A. 資料獲取:**  
- 起手 collection: `tflex_applications`  
- 需要 join 的表: `tflex_company_hc` (用於取得公司人力 H/C，計算員工送單率)  
- 需要的 $match 過濾條件: 無（需包含所有應用記錄）  
- 🔑 **需要的原始欄位 (raw_columns_needed)**:  
  `[employee_id, company_code, review_status, review_result]`  

**B. 資料處理:**  
1. **計算各公司退單率 (return_rate)**  
   - 篩選 `review_status='Y'` 的記錄（已完成申請）  
   - 計算 `return_count` = count where `review_result='N'`  
   - 計算 `total_applications` = count of `review_status='Y'`  
   - `return_rate = return_count / total_applications`  

2. **計算整體平均退單率 (overall_avg_return_rate)**  
   - 總退單數 / 總完成申請數  

3. **標註明顯高於整體平均的公司**  
   - 比較每家公司 `return_rate` 與 `overall_avg_return_rate`  
   - 若 `return_rate > overall_avg_return_rate`，則標註為「高於平均」  

**C. 視覺化建議:**  
- 圖型選擇：**橫向長條圖 (horizontal bar chart)**  
- 理由：  
  - 公司數量通常不超過 15 家，適合用橫向長條圖呈現排行  
  - 可清楚顯示各公司退單率與整體平均的比較  
  - 若需標註「高於平均」，可使用不同顏色或標記線來區分  
  - 若需進一步分析，可搭配表格呈現數值細節  

> ⚠️ 注意：此查詢未提及時間軸、金額、部門等不支援維度，因此不觸發拒絕條件。
```
### Phase A · Pipeline (7.1s)
```json
{
  "start_collection": "tflex_applications",
  "pipeline": [
    {
      "$lookup": {
        "from": "tflex_company_hc",
        "localField": "company_code",
        "foreignField": "company_code",
        "as": "company_info"
      }
    },
    {
      "$unwind": {
        "path": "$company_info",
        "preserveNullAndEmptyArrays": true
      }
    },
    {
      "$project": {
        "_id": 0,
        "employee_id": 1,
        "company_code": 1,
        "application_no": 1,
        "application_category": 1,
        "review_status": 1,
        "review_result": 1,
        "review_mechanism": 1,
        "hc": "$company_info.hc"
      }
    }
  ]
}
```
raw_df: shape=[147526, 8], cols=['application_no', 'application_category', 'company_code', 'employee_id', 'review_mechanism', 'review_result', 'review_status', 'hc']
### Phase B · Preprocess (9.5s)
```python
# A. 資料獲取
# raw_df 已由上游準備好，包含所需欄位

# B. 資料處理
# 1. 計算各公司退單率
agg = raw_df.groupby('company_code').agg(
    return_count=('review_result', lambda x: (x == 'N').sum()),
    total_applications=('review_status', 'size'),
).reset_index()

# 篩選 review_status='Y' 的記錄來計算退單率
completed_df = raw_df[raw_df['review_status'] == 'Y']
return_agg = completed_df.groupby('company_code').agg(
    return_count=('review_result', lambda x: (x == 'N').sum()),
    total_applications=('review_status', 'size'),
).reset_index()

return_agg['return_rate'] = return_agg['return_count'] / return_agg['total_applications']

# 2. 計算整體平均退單率
overall_return_count = return_agg['return_count'].sum()
overall_total_applications = return_agg['total_applications'].sum()
overall_avg_return_rate = overall_return_count / overall_total_applications

# 3. 標註明顯高於整體平均的公司
return_agg['flag'] = return_agg['return_rate'] > overall_avg_return_rate

# 組合最終結果
Q = return_agg[['company_code', 'return_rate', 'return_count', 'total_applications', 'flag']].copy()
```

Q: shape=[15, 5], cols=['company_code', 'return_rate', 'return_count', 'total_applications', 'flag']
### Phase C · ECharts (18.8s)
```python
# Step 1: 計算各公司退單率與整體平均
# 假設 Q 已經是 Phase B 處理後的結果，包含以下欄位：
# ['company_code', 'return_rate', 'return_count', 'total_applications', 'flag']

# Step 2: 計算整體平均退單率
overall_avg_return_rate = (Q['return_count'].sum() / Q['total_applications'].sum())

# Step 3: 準備橫向長條圖資料
x_data = Q['company_code'].astype(str).tolist()
y_data = (Q['return_rate'] * 100).round(2).tolist()

# Step 4: 標記高於平均的公司
flag_data = []
for idx, rate in enumerate(Q['return_rate']):
    if rate > overall_avg_return_rate:
        flag_data.append("高於平均")
    else:
        flag_data.append("低於或等於平均")

# Step 5: 建立 option
option = {
    "title": {"text": "退單率最高的公司排行表", "left": "center"},
    "tooltip": {"trigger": "axis", "axisPointer": {"type": "cross"}},
    "color": ["#5470c6", "#91cc75", "#fac858", "#ee6666", "#73c0de", "#3ba272", "#fc8452", "#9a60b4", "#ea7ccc", "#5b9bd5", "#a5a5a5", "#ffc000", "#7b78de", "#27a39d", "#e15759", "#f28e2c", "#76b7b2", "#59a14f", "#edc949", "#b07aa1"],
    "xAxis": {"type": "value"},
    "yAxis": {"type": "category", "data": x_data},
    "series": [
        {
            "name": "退單率",
            "type": "bar",
            "data": y_data,
            "label": {"show": True, "position": "right", "formatter": "{c}%"},
            "itemStyle": {
                "color": lambda x: "#ee6666" if flag_data[x] == "高於平均" else "#5470c6"
            }
        }
    ],
    "grid": {"left": 60, "right": 60, "top": 60, "bottom": 40}
}
```

option keys: ['title', 'tooltip', 'color', 'xAxis', 'yAxis', 'series', 'grid']
### Phase D · Insight (8.9s)
```
**🔑 重點摘要**  
- 退單率最高的公司為 TSN（5.71%）、TWT（5.18%）與 TSC（4.37%），明顯高於整體平均。  
- 整體平均退單率為 2.68%，顯示部分公司申請品質存在顯著落差。  
- ESM、TSC、TSN 等公司退單數量較高，需進一步檢視申請流程或政策執行一致性。  
- TST 申請量龐大（119,029 筆），但退單率僅 3.60%，顯示其審核機制相對穩定。  
- JSM 退單率 4.11%，為高於平均公司之一，需關注其申請內容或審核標準。

**📌 觀察與建議**  
- 建議針對退單率高於平均的公司（如 TSN、TSC、TWT）進行個案審查，釐清退單原因並優化申請流程。  
- 可針對高退單率公司進行內部審核機制的回饋與培訓，提升整體審核一致性。  
- 建立定期監控機制，追蹤高退單率公司是否改善，並設定改善目標。

**⚠️ 解讀注意事項**  
- 本分析僅基於完成申請（review_status='Y'）計算退單率，未包含進行中申請，避免因樣本不完整導致誤判。  
- 部分公司申請量極低（如 TSK 只有 3 筆），其退單率可能因小樣本產生偏差，需特別留意。
```

---

## Case T3 · 申請量 TOP 5 vs BOTTOM 5 對比
**Query:** `申請量最大跟最少的各 5 家公司,做一張對比表呈現`
**Type:** happy_path · **Status:** pass

### 檢查項
- ✅ Plan 有產出
- ✅ Plan 非空
- ✅ Pipeline 不含禁忌 stage (group/sort/limit/divide/cond)  ·  ✓ 乾淨
- ✅ Pipeline 撈到非空資料  ·  shape=(147526, 4)
- ✅ Phase B exec 成功 (用 1 次嘗試)  ·  1 次過
- ✅ Phase B 有產出 Q
- ✅ Q 含必備欄位 ['company_code']  ·  ✓
- ✅ Phase C exec 成功 (用 1 次嘗試)  ·  1 次過
- ✅ option 含必備 keys []  ·  ✓
- ✅ series 數 ≥ 1  ·  actual: 2
- ✅ Insight 有產出
- ✅ Insight 無禁忌語(或皆為拒絕語境)

### Phase 0 · Plan (10.5s)
```
**A. 資料獲取:**  
- 起手 collection: `tflex_applications`  
- 需要 join 的表: `tflex_company_hc` (用於取得公司人力資本 headcount)  
- 需要的 $match 過濾條件: 無（全量資料分析）  
- 🔑 **需要的原始欄位 (raw_columns_needed)**:  
  `[company_code, review_status]`  

**B. 資料處理:**  
1. **計算每家公司總申請張數 (total_applications)**:  
   - 依 `company_code` 分組，計算 `tflex_applications` 中每家公司的文件數量。  
2. **排序並選出申請量前 5 名與後 5 名公司**。  
3. **加入公司人力資本 (hc)**:  
   - 透過 `company_code` join `tflex_company_hc`，取得每家公司 hc。  
4. **計算員工送單率 (employee_submission_rate)**:  
   - `distinct employee_id count / company hc`  
   - 此處需額外計算 `submitter_count`（每家公司送單的員工數）  
5. **整理成對比表**：  
   - 包含欄位：`company_code`, `total_applications`, `hc`, `employee_submission_rate`  

**C. 視覺化建議:**  
- 圖型選擇：**水平堆疊長條圖 (horizontal stacked bar)**  
- 理由：  
  - 申請量前後 5 名公司需橫向比較，清晰呈現數值差異。  
  - 由於 query 明確點名「對比表」，且類別數 ≤ 10，水平排列更易讀。  
  - 若需進一步細分，可加入 `employee_submission_rate` 作為第二組 bar。
```
### Phase A · Pipeline (6.5s)
```json
{
  "start_collection": "tflex_applications",
  "pipeline": [
    {
      "$lookup": {
        "from": "tflex_company_hc",
        "localField": "company_code",
        "foreignField": "company_code",
        "as": "company_info"
      }
    },
    {
      "$unwind": {
        "path": "$company_info",
        "preserveNullAndEmptyArrays": true
      }
    },
    {
      "$project": {
        "_id": 0,
        "employee_id": 1,
        "company_code": 1,
        "review_status": 1,
        "hc": "$company_info.hc"
      }
    }
  ]
}
```
raw_df: shape=[147526, 4], cols=['company_code', 'employee_id', 'review_status', 'hc']
### Phase B · Preprocess (10.1s)
```python
# A. 資料獲取
# 起手 collection: tflex_applications
# 需要 join 的表: tflex_company_hc (用於取得公司人力資本 headcount)
# 需要的原始欄位 (raw_columns_needed): [company_code, review_status]

# B. 資料處理
# 1. 計算每家公司總申請張數 (total_applications)
# 2. 排序並選出申請量前 5 名與後 5 名公司
# 3. 加入公司人力資本 (hc)
# 4. 計算員工送單率 (employee_submission_rate)
# 5. 整理成對比表

# Step 1: 計算每家公司總申請張數與送單人數
agg1 = raw_df.groupby('company_code').agg(
    total_applications=('review_status', 'size'),
    submitter_count=('employee_id', 'nunique'),
    hc=('hc', 'first')
).reset_index()

# Step 2: 計算員工送單率
agg1['employee_submission_rate'] = agg1['submitter_count'] / agg1['hc']

# Step 3: 排序並選出前 5 名與後 5 名公司
top5 = agg1.nlargest(5, 'total_applications')
bottom5 = agg1.nsmallest(5, 'total_applications')

# Step 4: 合併前後 5 名公司資料
Q = pd.concat([top5, bottom5], ignore_index=True)

# 確保欄位順序一致
q_columns = ['company_code', 'total_applications', 'hc', 'employee_submission_rate']
Q = Q[q_columns]
```

Q: shape=[10, 4], cols=['company_code', 'total_applications', 'hc', 'employee_submission_rate']
### Phase C · ECharts (15.9s)
```python
option = {
    "title": {"text": "申請量前後5名公司對比", "left": "center"},
    "tooltip": {"trigger": "axis", "axisPointer": {"type": "cross"}},
    "legend": {"show": True, "top": 30},
    "color": ["#5470c6", "#91cc75", "#fac858", "#ee6666", "#73c0de", "#3ba272", "#fc8452", "#9a60b4", "#ea7ccc", "#5b9bd5", "#a5a5a5", "#ffc000", "#7b78de", "#27a39d", "#e15759", "#f28e2c", "#76b7b2", "#59a14f", "#edc949", "#b07aa1"],
    "grid": {"left": 60, "right": 60, "top": 70, "bottom": 40},
    "xAxis": {"type": "category", "data": Q['company_code'].astype(str).tolist()},
    "yAxis": {"type": "value"},
    "series": [
        {
            "name": "total_applications",
            "type": "bar",
            "stack": "total",
            "data": [int(v) for v in Q['total_applications'].tolist()],
            "label": {"show": True, "position": "right", "formatter": "{c}"},
        },
        {
            "name": "hc",
            "type": "bar",
            "stack": "total",
            "data": [int(v) for v in Q['hc'].tolist()],
            "label": {"show": True, "position": "right", "formatter": "{c}"},
        },
    ]
}
```

option keys: ['title', 'tooltip', 'legend', 'color', 'grid', 'xAxis', 'yAxis', 'series']
### Phase D · Insight (10.3s)
```
**🔑 重點摘要**  
- 申請量前五名公司為 TST (128,922 筆)、TSN (4,224 筆)、TSC (4,184 筆)、JSM (3,876 筆)、TSA (2,699 筆)。  
- 申請量後五名公司為 TSK (7 筆)、TDC (79 筆)、TSJ (81 筆)、ESM (102 筆)、TSE (106 筆)。  
- TST 公司申請量佔整體近 90%，顯示其業務規模或員工參與度顯著高於其他公司。  
- 員工送單率最高的公司為 TSK (100%)、TSC (98.8%)、TSN (97.5%)，反映高參與度。  
- 申請量最少的公司 (如 TSK) 其人力資本極低，需謹慎解讀其送單率。

**📌 觀察與建議**  
- 建議針對申請量極低的公司（如 TSK、TDC）進一步追查是否因系統使用率低或員工參與度不足，並設計激勵措施提升申請率。  
- 可針對申請量前五大公司（如 TST、TSN）進行申請類型分析，了解其申請偏好與需求特徵，作為政策優化依據。  
- 建議建立「申請量與人力資本比值」的監控指標，以識別異常公司（如 TSK）是否為人力極少但申請量高，或反之。

**⚠️ 解讀注意事項**  
- 申請量最低的公司（如 TSK）僅有 2 人，樣本極小，其員工送單率（100%）可能因極少人數而失真，需謹慎看待。  
- 本分析未包含時間維度，無法判斷申請量差異是否為短期波動或長期趨勢，建議後續補充時間序列資料進行深入觀察。
```

---
