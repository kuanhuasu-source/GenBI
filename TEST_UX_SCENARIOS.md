# TEST_UX_SCENARIOS.md — GenBI UX 整合測試計畫

> 對應 v0.2.0(含 Pre-Phase 0 Intent Router + Follow-up Detection + out_of_scope)。
> 目的:**驗證從第一次見面到深度迭代分析的完整 user journey**,
> 涵蓋 6 種 intent + 5 階段 pipeline + 接續分析 + 離題防禦。
>
> 用法:`streamlit run app.py`,按 Scenario 順序貼問題,對照「期待行為」與「驗證點」。

---

## 🎬 Scenario 1 · 冷啟動 (新使用者第一次見面)

**前提:** 開啟系統、chat 為空。
**目的:** 驗證 Welcome Panel + Intent Router 對 meta queries 的瞬時回應(**全部 0 LLM call**)。

| # | Query | 期待 Intent | 驗證重點 |
|---|---|---|---|
| 1.0 | (不輸入,看畫面) | — | 上方出現 **Welcome Panel**(3 個探索按鈕 + 6 個從 metadata 來的範例問題) |
| 1.1 | `hi` | greeting | 簡短歡迎 + 4 個建議下一步;**< 1 秒** |
| 1.2 | `你會做什麼?` | intro | 產品介紹 + 4 個能力 bullet + 5 個 sample questions;**< 1 秒** |
| 1.3 | `你有什麼資料?` | data_overview | 列出 tflex_applications + tflex_company_hc schema + 11 個 KPI + 限制;**< 1 秒** |
| 1.4 | `我可以怎麼開始?` | guidance | 分類引導(比較/概覽/分佈)+ 範例;**< 1 秒** |
| 1.5 | `Hello, what can you do?` | intro | 英文觸發也走 intro;**< 1 秒** |

---

## 🔍 Scenario 2 · 探索資料能力 (data_check 多變化)

**前提:** 已過 Scenario 1。
**目的:** 驗證「你有 X 嗎?」的 subject 萃取與 metadata 搜尋。

| # | Query | 期待 | 驗證重點 |
|---|---|---|---|
| 2.1 | `你有時間資料嗎?` | data_check (negative) | 找不到 → 引用 `data_limitations` 中「No application date」 |
| 2.2 | `有沒有 review_status?` | data_check (positive) | 找到 `tflex_applications.review_status` 並列出描述 |
| 2.3 | `有公司資料嗎?` | data_check (positive) | 找到 `company_code` 等公司相關欄位 |
| 2.4 | `有金額資料嗎?` | data_check (negative) | 找不到 → 引用「No payment amount」限制 |
| 2.5 | `do you have employee data?` | data_check (positive) | 英文觸發也行,找到 `employee_id` |

---

## 📊 Scenario 3 · 標準分析路徑 (覆蓋圖表型態)

**前提:** 不一定要連續,每個都是「fresh analysis」。
**目的:** 驗證 Phase 0→A→B→C→D 完整流程 + 各圖表型態。

| # | Query | 預期圖表 | 驗證重點 |
|---|---|---|---|
| 3.1 | `比較各公司的退單率與申請數` | 雙軸 bar+line | 左軸件數、右軸退單率% |
| 3.2 | `各公司 PAY 與 RTN 對比,看誰退件最多` | stacked bar | series 帶 `stack` 屬性、TST 那柱最高 |
| 3.3 | `Top 5 退件最多的公司` | sorted bar | 5 列 by 退件數降序;**Phase A 不可出現 `$sort`/`$limit`** |
| 3.4 | `畫熱力圖看公司 × 類別申請分佈` | heatmap | 含 visualMap、x/y 都是 category |
| 3.5 | `AI 審查率與退單率有相關嗎?畫散點圖` | scatter | series.type=scatter,每點是一家公司 |
| 3.6 | `全公司 KPI 一覽 dashboard:申請數、退單率、AI 率` | table + KPI cards | `_use_table=True` + 3-4 張 metric cards + 比率欄是 ProgressColumn |
| 3.7 | `各公司審核完成率排序` | sorted % bar | y 軸 0-100%,完成率欄是百分比 |

---

## 🔗 Scenario 4 · 接續修改迭代

**前提:** 跑完 Scenario 3.1(雙軸圖)、`last_analysis` 已有脈絡。
**目的:** 驗證 Follow-up Detection 注入前次脈絡。

| # | Query | 期待 | 驗證重點 |
|---|---|---|---|
| 4.1 | `改成 stacked bar` | follow-up + 換圖 | 顯示 **🔗 偵測為延續性分析** 提示;新圖為 stacked bar |
| 4.2 | `也加上完成率` | follow-up + 增 KPI | 第三個 series 加入 |
| 4.3 | `只看 TST 跟 TSC` | follow-up + 收窄 | Q 變 2 列 |
| 4.4 | `排序看看` | follow-up + 排序 | x 軸順序變化 |
| 4.5 | `剛剛那張圖換成熱力圖` | follow-up + 換圖 | 用「剛剛」+「換成」雙觸發 |
| 4.6 | `我想看 AI 審查率分佈` | **新分析(不是 follow-up)** | **不顯示** 🔗 提示;clean fresh pipeline |
| 4.7 | (Sidebar 按「🆕 開始新分析」) | 重置 | `last_analysis` 清空,下次 query 一定是新分析 |

---

## 🛑 Scenario 5 · 資料限制拒絕(Schema-Driven Refusal)

**前提:** 不需要前後文。
**目的:** 驗證 Phase 0 從 metadata 推理拒絕,**不是靠寫死關鍵字**。

| # | Query | 期待 | 驗證重點 |
|---|---|---|---|
| 5.1 | `過去三個月每週的申請趨勢` | `[REFUSE]` | 引用「No application date」/「Time trend analysis 不支援」 |
| 5.2 | `平均申請金額是多少?` | `[REFUSE]` | 引用「No payment amount」 |
| 5.3 | `各部門退單率比較` | `[REFUSE]` | 引用「No employee department」 |
| 5.4 | `不同年齡層的申請偏好` | `[REFUSE]` | 引用 demographics 限制 |
| 5.5 | `審核平均花多久?` | `[REFUSE]` | 引用「No review completion timestamp」 |

**通用驗證:** 拒絕訊息結構應為「⚠️ 資料不足 + 引用具體 missing 欄位 + 建議改問」。

---

## 🧭 Scenario 6 · 完全離題(Layer 1 out_of_scope)

**目的:** 驗證 0 LLM call 的離題防禦。

| # | Query | 期待 | 驗證重點 |
|---|---|---|---|
| 6.1 | `今天天氣如何?` | out_of_scope | **0 LLM call < 1 秒** + 引導訊息 + 範例問題 |
| 6.2 | `台積電股價多少?` | out_of_scope | 同上 |
| 6.3 | `幫我翻譯這段話成英文` | out_of_scope | 同上 |
| 6.4 | `1+1 等於多少?` | out_of_scope | 同上 |
| 6.5 | `推薦我一首歌` | out_of_scope | 同上 |
| 6.6 | `你今天工作得怎麼樣?` | out_of_scope | 純社交不在範圍 |

**通用驗證:** 訊息結構應為「🧭 你問的不在 [dataset_name] 範圍內 + 引述使用者輸入 + 3 個探索建議 + 範例問題」。

---

## ⚠️ Scenario 7 · 複合需求(壓力測試)

**目的:** 測試 LLM 對多重約束的處理能力。

| # | Query | 期待 | 驗證重點 |
|---|---|---|---|
| 7.1 | `在 Family Care 類別中,各公司的核准與退件占比分佈,用 100% stacked bar,y 軸顯示百分比` | 100% stacked bar | per-category 加總 = 100%、y 軸 0-100% 範圍 |
| 7.2 | `申請 3 件以上的員工分布在哪些公司,水平 bar,數值標在柱子上` | sorted horizontal bar | 兩階段聚合 + xAxis=value/yAxis=category + 數值 label |
| 7.3 | `比較 AI 跟人工審查的退件率,看哪個 mechanism 比較好` | grouped or bar | dimension 從公司切換成 review_mechanism |
| 7.4 | `小公司(hc<500)的人均申請數,用 bar 表示` | bar | 數值門檻過濾 + 人均計算 + 數字 round 2 位 |

---

## 🎯 Scenario 8 · 完整 User Journey(系統最壓力測試)

**這是最重要的一輪測試** — 模擬真實使用者完整旅程,測試各 layer 間的互動。

| 步驟 | Query | 期待行為 | 關鍵驗證 |
|---|---|---|---|
| A | `hi` | greeting | 簡短歡迎 |
| B | `你會做什麼?` | intro | 完整能力 |
| C | `你有什麼資料?` | data_overview | 完整 schema |
| D | `比較各公司退單率與申請數` | analysis | 雙軸圖,**寫入 last_analysis** |
| E | `改成 stacked bar` | follow-up | 🔗 提示出現,沿用 Q 換圖 |
| F | `也看 AI 採用率` | follow-up | 加新 series |
| G | **`今天天氣如何?`** | out_of_scope ⭐ | **關鍵**:有 last_analysis 但仍正確判斷為離題(不該誤觸 follow-up) |
| H | `你有時間資料嗎?` | data_check ⭐ | **關鍵**:meta query 不被當 follow-up |
| I | `重新看一下類別分布` | analysis(新) | 不該是 follow-up;會更新 last_analysis |
| J | (按「🆕 開始新分析」) | 清除 | last_analysis = None |
| K | `Top 5 退件公司` | analysis(fresh) | 沒有 🔗 提示(因 last_analysis 清空) |
| L | `今天 TST 表現如何?` | analysis | 含「今天」也含「TST」→ vocab match → 走 analysis;Phase 0 可能 refuse 因「今天」隱含時間 |

**這一輪要看的是:**
- Intent Router 正確 short-circuit meta query
- Follow-up Detection 正確識別接續 vs 新查詢
- out_of_scope 即使有 last_analysis 仍可觸發
- 中途 meta query 不污染 last_analysis
- 「🆕 開始新分析」能正確重置

---

## 🪝 Scenario 9 · 邊界與防禦

| # | Query | 期待 | 驗證重點 |
|---|---|---|---|
| 9.1 | (空白輸入或只有空格) | 不送出 | Streamlit 應該阻擋空 submit |
| 9.2 | `?` | 太短 → 預設 analysis → Phase 0 可能拒絕 | 不該 crash |
| 9.3 | `分析` | 太模糊 → 走 analysis → Phase 0 可能要求具體化 | 系統 graceful |
| 9.4 | `123456` | 純數字 → out_of_scope | 0 LLM, 引導訊息 |
| 9.5 | `; DROP TABLE applications;` | 不該執行 | 字串純文字,當作 query 處理(MongoDB pipeline 不會 eval 字串) |

---

## 📊 驗證輔助:Cost / Latency 期待值

| 場景類型 | 預期 LLM calls | 預期 wall time | 預期 tokens |
|---|---|---|---|
| Meta query (greeting/intro/...) | **0** | < 1 秒 | 0 |
| out_of_scope | **0** | < 1 秒 | 0 |
| Refusal (Phase 0 [REFUSE]) | **1** | 3-8 秒 | ~1K-1.5K |
| 標準分析(順利一次過) | **5** | 25-40 秒 | ~7K-10K |
| 分析含 Phase B 1 次 retry | **6** | 30-50 秒 | ~10K-13K |
| 分析含 Phase C fallback (3 次 retry) | **7-8** | 40-60 秒 | ~12K-16K |
| Follow-up(接續) | **5-6** | 25-45 秒 | ~9K-12K(因為 prompt 多了前次脈絡) |

---

## 🚦 整體 Pass 判定

執行完 Scenarios 1-8 後,若以下都成立則視為 v0.2.0 達標:

- [ ] Scenario 1 全部 ≤ 1 秒(meta queries 都是 0 LLM)
- [ ] Scenario 3 至少 5/7 圖表正確渲染(允許 1-2 個 fallback 表格)
- [ ] Scenario 4 每個 follow-up 都看到 🔗 提示,且沿用前次脈絡
- [ ] Scenario 5 全部正確拒絕並引用具體限制
- [ ] Scenario 6 全部 0 LLM call 攔下
- [ ] Scenario 8 G 步(離題在有 last_analysis 時)正確識別
- [ ] 整段 User Journey 不會 crash 或顯示 Python traceback 給使用者

---

## 📝 測試紀錄表(自行填寫)

| Scenario | Pass 數 / 總數 | 觀察 / 異常 |
|---|---|---|
| 1 · 冷啟動 | / 6 | |
| 2 · 探索資料 | / 5 | |
| 3 · 標準分析 | / 7 | |
| 4 · 接續修改 | / 7 | |
| 5 · 資料限制拒絕 | / 5 | |
| 6 · 完全離題 | / 6 | |
| 7 · 複合需求 | / 4 | |
| 8 · 完整 Journey | / 12 | |
| 9 · 邊界與防禦 | / 5 | |
| **合計** | **/ 57** | |
