# STACKED_BAR_TEST.md — 堆疊長條圖專屬測試集

> 用於迭代修復 stacked bar 場景的反覆失敗。每個 case 列出查詢、期望結構、檢查項。
> **跑法:**Sidebar 按「🆕 開始新分析」清脈絡 → 貼 query → 對照檢查項。
> **目的:**找出 LLM 在 stacked bar 失敗的所有模式,逐一強化 prompt。

---

## 🧠 共通檢查項(所有 case 都要看)

每個 case 跑完後,展開 Phase C「🎨 檢視 ECharts 繪圖腳本」expander,檢查:

| 項目 | 檢查方法 |
|---|---|
| **C-1 · series 是 list comprehension** | code 含 `for X in Y` 而非手寫 3-4 個 series 物件 |
| **C-2 · series.name 非 literal 字串** | name 用 `str(key)` 之類,不是 `"類別 A"`、`"State 1"` |
| **C-3 · filter 用 for 變數** | `Q[Q['col'] == key]` 而非 `Q[Q['col'] == "某字串字面值"]` |
| **C-4 · xAxis 是 unique values** | `Q['col'].unique().tolist()` 或預先計算的 list |
| **C-5 · 所有 series 同名 stack** | 都帶 `"stack": "<相同字串>"` |
| **C-6 · 渲染後柱有正確高度** | 圖實際長出來,不是空白 |
| **C-7 · legend 真實值** | 顯示 metadata 中真實的類別/狀態名,不是 placeholder |

---

## 📋 Case STK-01 · 基本 100% 占比(per company × category)

**🎯 Query:**
```
畫一張 stacked bar:依據 company_code(TST、TSN、TSC),每條 bar 中呈現 application_category 的佔比
```

**期望結構:**
- **xAxis:** `["TST", "TSN", "TSC"]`(3 個)
- **series:** 4 個(對應 4 個 application_category)
- **每個 series 的 data:** 長度 3(對應 3 公司)
- **每柱加總:** 100%(per-company normalized)
- **yAxis max:** 100,formatter `{value}%`

**驗證:**
- C-1 至 C-7 全部通過
- TST 那柱應該是 4 種類別堆疊,加總高度到 100%(因為 normalize)
- legend 顯示 `Family Care`, `Wellness`, `Medical & Insurance`, `Development & Voluteering`

---

## 📋 Case STK-02 · Transposed 方向(per category × company)

**🎯 Query:**
```
依據 application_category 畫 stacked bar,每條 bar 中呈現 TST、TSN、TSC 的占比
```

**期望結構:**
- **xAxis:** 4 個 category 名稱
- **series:** 3 個(TST / TSN / TSC)
- **每個 series 的 data:** 長度 4(對應 4 個 category)
- **每柱加總:** 100%(per-category normalized,即「該類別中三公司占比」)

**驗證:**
- STK-01 反向版本
- 若 STK-01 通過但 STK-02 失敗 → LLM 維度識別有問題,需強化 rule 5.54

---

## 📋 Case STK-03 · 互斥狀態 stacked(raw count,非 normalized)

**🎯 Query:**
```
各公司的 PAY 與 RTN 申請數量比較,用 stacked bar 呈現
```

**期望結構:**
- **xAxis:** 15 家公司(全部,或排序後 top N)
- **series:** 2 個(PAY、RTN)
- **每柱加總:** raw count(每家公司不同高度,不 normalize)
- **yAxis max:** 不鎖 100,讓 ECharts 自動

**驗證:**
- TST 那柱應最高(總申請最多)
- 兩 series 都帶 `stack`
- legend 顯示 `PAY` / `RTN` 或語意等價詞

---

## 📋 Case STK-04 · 三狀態 stacked(per-group normalized)

**🎯 Query:**
```
各申請類別下,核准(完成且 result=Y)/退件(完成且 result=N)/進行中 三狀態的占比分佈,用 100% stacked bar
```

**期望結構:**
- **xAxis:** 4 個 application_category
- **series:** 3 個(approved / returned / in_progress)
- **每柱加總:** 100%(每類別內三狀態 normalize)
- **yAxis max:** 100,formatter `{value}%`

**驗證:**
- 3 個 series 都帶 stack
- legend 顯示三狀態真實名(可能英文或中文,要看 LLM 怎麼命名)

---

## 📋 Case STK-05 · 含 filter 的 stacked

**🎯 Query:**
```
只看 TST、TSC 兩家,各類別中 AI 審查 vs 人工審查 的數量 stacked
```

**期望結構:**
- **Phase A** 應有 `$match: {company_code: {$in: ["TST", "TSC"]}}`
- **xAxis:** 4 個類別
- **series:** 2 個(AI / Human)
- **每柱:** TST + TSC 兩家加總的 raw count
- 或:6 個位置(2 公司 × 應該… 看 LLM 怎麼理解)— 此 case 故意設計成有歧義,看 LLM 如何處理

**驗證:**
- Filter 是否正確套用?
- 維度選擇是否合理(類別當 xAxis,審查機制當 series)?

---

## 📋 Case STK-06 · Edge case · 缺漏組合

**🎯 Query:**
```
依據 hc 介於 100 到 1000 的公司,看各類別占比 stacked
```

**期望結構:**
- 中型公司(可能 TSU/TDI/TDJ/TWT 等,看 hc 範圍)
- 4 個類別 series
- **重點:** 若有公司沒某個類別的申請,pivot 後該位置應該是 0,**不能 NaN 或 crash**

**驗證:**
- `.fillna(0)` 是否有用?
- 每柱加總仍為 100%

---

## 📋 Case STK-07 · Follow-up 改 stacked

**前置:** 先跑 `各公司的申請數量 bar` 建立 last_analysis。

**🎯 Follow-up Query:**
```
改成 stacked bar 看類別占比
```

**期望結構:**
- 偵測為 follow-up(顯示 🔗 提示)
- 沿用前次的 dimension(公司),但加上 application_category 為 series
- 100% normalize

**驗證:**
- 是否真的接續(看 Phase 0 plan 是否有引用前次脈絡)
- 是否避免 hardcode placeholder

---

## 🧪 跑完後的 root-cause 對照表

| 觀察到的 bug | 對應的 prompt 規則需強化 |
|---|---|
| series 用 hardcode "類別 A/B/C" | rule 5.53(series 動態產出) |
| xAxis 顯示重複公司 (TSC, TSC, TSC...) | rule 5.55(long-format xAxis 用 unique) |
| 維度 transposed | rule 5.54(維度 vs series 中文語意) |
| 軸超 100% 或 raw count + `{value}%` formatter | rule 5.3 / 5.6(100% stacked 配方) |
| 每柱沒到 100%(非 normalized) | Phase B rule 9.5(per-group normalize) |
| 圖完全空白 | filter literal 字串問題 → rule 5.53 |
| stack 屬性缺失 | rule 5.5(stack 觸發判斷) |
| follow-up 失去前次 Q 結構 | Phase 0 follow-up preamble |

---

## 📊 評分標準

每跑完一個 case,記錄:
- ✅ 全部 7 個共通檢查項通過 → **Full Pass**
- ⚠️ 6/7 通過 → **Minor Issue**(可放著,記筆記)
- ❌ < 6/7 通過 → **Failed**,需 root-cause + 強化 prompt

**目標:** 7 個 case 中至少 5 個 Full Pass、最多 1 個 Failed。

---

## 📝 迭代流程

1. 跑單個 case
2. 失敗 → 對照「root-cause 對照表」找該強化哪條規則
3. 修 prompt → smoke test
4. 重跑同個 case 驗證
5. 全 case 跑一遍確認沒退步
6. 進下一個 case

**不要** 一次跑多個 case 後一起修 — 排錯會混亂。
