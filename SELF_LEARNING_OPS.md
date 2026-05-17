# GenBI Self-Learning 維運手冊

> 對象:維運人員 / SRE / 模型守門人
>
> 目的:不需懂 implementation 細節,也能日常監控、故障排除、安全啟停整個 self-learning loop。
>
> 對應實作 spec:`GenBI_v1.3_Self_Learning_MVP_Implementation_Spec.md`(深入細節看那份)

---

## 1. 它在做什麼?(一句話版)

**從失敗的 query 自動萃取模式,經過獨立驗證 + 群集 + 信心評分,把高品質的修補建議轉成 prompt rule candidate,給人類審核後注入生產 prompt。**

> 簡單比喻:每天晚上,系統會回顧今天「客人點菜失敗的紀錄」(failed task_trace),總結「為什麼失敗」(observation),由另一個獨立的 LLM 評審「這個總結對不對」(verifier),把相似的失敗歸成一類(instinct),累積到夠多證據時轉成「給廚師的新規則建議」(prompt rule candidate)。所有 candidate 要過 4 道閘門才上線。

---

## 2. 高層架構

```
┌─────────────────────────────────────────────────────────────────────┐
│  task_traces  ◄── 來源:app.py / test_runner.py 每次 query 留下      │
│                  含完整 LLM messages、retry、status、error、phase   │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼  failure_filter:status in (failed,
                               │  refused) 或 step error 的 trace
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│ [1] observation_extraction  ── LLM 從 trace 抽 5+1 field 觀察       │
│       輸出 → learning_observations (status=candidate)               │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────▼──────────────────────────────────────┐
│ [2] verification  ── 另一個 LLM 獨立評審,4-component confidence    │
│       evidence × specificity × consistency × novelty                │
│       輸出 → verifier_results, 更新 observation.status (verified/rejected) │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────▼──────────────────────────────────────┐
│ [3] consolidation  ── Jaccard cluster verified observations         │
│       同 cluster 證據累積 → instinct(confidence 加權平均)          │
│       輸出 → learning_instincts (status=candidate / active)         │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────▼──────────────────────────────────────┐
│ [4] contradiction_scan  ── 找新 obs 跟既有 active instinct 的衝突   │
│       例:obs 說「禁止 X」但 instinct 說「鼓勵 X」                  │
│       輸出 → 自動 degrade 信心較弱方                                 │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────▼──────────────────────────────────────┐
│ [5] confidence_decay  ── 90 天沒新證據的 instinct 信心衰減          │
│       避免老舊 instinct 永遠 active                                  │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────▼──────────────────────────────────────┐
│ [6] resolution_detection  ── 同 query_hash 從 failed → completed   │
│       自動產生 regression test_case(避免修好的問題又退化)         │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────▼──────────────────────────────────────┐
│ [7] candidate_generation  ── confidence ≥ 0.85 且 evidence ≥ 3 的   │
│       active instinct,生成 prompt_rule_candidate                    │
│       輸出 → learning_candidates(status=pending,等人類審)         │
│       規則必須過 4 道 gate:no_regression / pass / latency / cost   │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
                     人類 reviewer 在 Admin UI
                     決定 approve / reject
                               │
                               ▼
                  approved → 注入 prompt_templates,下次 query 生效
```

---

## 3. 資料表(MongoDB collection)

| Collection | 內容 | 寫者 | 讀者 |
|---|---|---|---|
| `task_traces` | 每次 query 完整紀錄(messages、retry、status、phase output、error) | app.py, test_runner.py | failure_filter, resolution_detector |
| `learning_observations` | LLM 抽出的 5+1 field 觀察 | observation_extractor | verifier, consolidator |
| `verifier_results` | 獨立 verifier 對每個 observation 的評分(agree/disagree + 4-comp confidence) | verifier | consolidator(讀 verified 的) |
| `learning_instincts` | Cluster 後的 pattern,含 status, confidence, evidence_count | consolidator, contradiction_scan, decay | candidate_generator |
| `learning_candidates` | 待人類審的 prompt rule candidate | candidate_generator | Admin UI |
| `learning_audit_log` | 所有 status 變動 / 人類操作 / job run 結果 audit | 全部 module | Admin UI |

---

## 4. 日常維運操作

### 4.1 nightly cron(推薦)

加入 crontab(每天凌晨 2 點跑,避開上班高峰):

```cron
0 2 * * * cd /path/to/GenBI && /path/to/.venv/bin/python scripts/run_learning_jobs.py >> /var/log/genbi_learning.log 2>&1
```

> 跑時長:依 trace 量而定。20-50 個 failed trace 通常 5-15 分鐘。1000+ trace 可能要 1 小時以上,建議分批(用 `--extraction-limit` flag)。

### 4.2 手動觸發(週末驗證、上線前 spot check)

```bash
# Dry-run:全鏈條跑完但不寫 DB(只看會處理幾個 trace)
python scripts/run_learning_jobs.py --dry-run

# 真實跑(會打 LLM、寫 DB)
python scripts/run_learning_jobs.py

# 跳特定 job(例如 verification 暫時關掉)
python scripts/run_learning_jobs.py --skip verification

# 限縮處理量(快速驗證,只抽 5 個)
python scripts/run_learning_jobs.py --extraction-limit 5 --verifier-limit 5
```

### 4.3 個別 job 單獨測試

每個 module 都可獨立 import + call:

```python
from pymongo import MongoClient
import config
from llm_service import LLMService
from learning.failure_filter import get_failed_traces
from learning.observation_extractor import run_observation_extraction

db = MongoClient(config.MONGO_URI)[config.MONGO_DB]
llm = LLMService(...)  # 同 test_runner

# 只看 candidate trace,不寫
traces = get_failed_traces(db, since_days=7, limit=10)
for t in traces:
    print(t['trace_id'][:8], t['status'], t['query'][:60])

# 只跑 extraction,verbose 看細節
stats = run_observation_extraction(db, llm, since_days=7, limit=3,
                                     dry_run=False, verbose=True)
print(stats)
```

### 4.4 Bootstrap 種子(只在新 DB 第一次)

`learning/bootstrap.py` 內建 13 個歷史 hotfix 種子(來自 v0.7-v0.8 設計階段的已知 pattern)。新 DB 建議跑一次,給 consolidator 一些「冷啟動」材料:

```bash
python -m learning.bootstrap --apply       # 真正寫
python -m learning.bootstrap               # dry-run 預覽
```

⚠️ **不要重複跑**。每筆種子有固定 dedupe_key,理論上有去重,但語意上「重新 seed」會把人類 reject 過的種子復活,造成 confusion。

---

## 5. 監控 — Admin UI(pages/06_learning_review.py)

啟動 Streamlit:
```bash
streamlit run app.py
```
左側 sidebar 切到 **「Learning Review」** 頁。4 個區塊:

### 5.1 Operational(營運面)
- **Jobs run last 7d**:7 個 job 各跑幾次 / 成功率 / 平均耗時
- **Trace pipeline funnel**:task_traces → failure_filter 抓出 → extracted → verified → consolidated 各層數字
- **LLM 成本**:近 7 天 self-learning 吃多少 token(會直接影響月帳單)

### 5.2 Quality(品質面)
- **Verifier agree rate**:理想 70-90%。太高(>95%)→ verifier 太鬆;太低(<50%)→ extractor 太雜
- **Instinct retention rate**:cluster 後留住多少 obs(理想 30-60%,太低代表 obs 太發散)
- **Contradiction rate**:近 30 天觸發 contradiction_scan 的比例(>20% 要警覺)

### 5.3 Impact(成效)
- **Baseline pass rate over time**:應該慢慢上升(self-learning 真的有效的 ground truth)
- **Resolved query count**:同 query 從 failed → completed 的數量
- **Candidate approval rate**:人類 approve 比例(<30% → candidate 品質不夠 / >80% → 沒在 review)

### 5.4 Detail(候選清單)
- Pending candidates:含 rule 內容、來源 instinct、4-gate 通過狀況
- 點 Approve / Reject 直接寫回 DB + audit log

---

## 6. 故障排除

### 6.1 「pipeline 跑完每個 job 都顯示 Found 0」

**症狀**:dry-run 或實跑後 7 個 job 全部處理 0 筆。

**可能原因 & 對策**:

| 原因 | 對策 |
|---|---|
| task_traces 全是 status=completed(沒失敗料) | 跑 baseline 或上線一陣子累積失敗 trace。或手動 inject `needs_review=True` 標記要分析的 trace |
| **started_at 被存成字串**(v0.11.0.1 修過的 bug) | 跑 `python scripts/backfill_task_traces_datetime.py --apply` 把字串轉 datetime |
| 時間窗口太短(`since_days`) | 加大窗口:`--window-days 30` |
| failure_filter 過濾條件太嚴 | 看 `scripts/inspect_traces.py` 確認 trace 真的存在,再對照 filter 條件 |

### 6.2 「verifier 全部 reject」

**症狀**:跑完 verification 後 `learning_observations` 全部 status=rejected。

**可能原因**:
- extractor 抽得太空泛(content 沒寫具體錯誤、沒寫怎麼修)→ 看 prompt template 是否有變動
- verifier 的 confidence threshold 太高(預設 0.6,在 verifier.py 內)→ 可調但要小心放行雜訊
- LLM 模型本身狀態飄移(換模型後沒重 calibrate)→ 跑 bench_model.py 看模型行為

### 6.3 「candidate 一直是 pending,沒被 review」

不是 bug,是流程設計。Candidate 要人類審核才能上線(防止 self-learning 跑歪)。

**動作**:
- Admin UI 上的 reviewer 該定期(建議週週看)
- 或設組織 SLA:7 天內沒 review 的自動 escalate

### 6.4 「明明跑了 bootstrap,instinct 還是 0」

`bootstrap` 寫的是 `learning_observations`,不是 `learning_instincts`。要等 nightly run 跑完 consolidation 才會出現 instinct。

或手動觸發:
```python
from learning.instinct_consolidator import run_consolidation
run_consolidation(db, verbose=True)
```

### 6.5 「失敗 trace 數量爆量,LLM 帳單壓力大」

**緊急止血**:cron 加 `--extraction-limit 50` 限縮每次處理量。

**根本解法**:
- 先把 baseline 失敗修一輪(prompt 改進、新 chart type 支援)
- 大規模失敗多半代表 prompt regression / 模型問題,不該丟給 self-learning loop 自動修

---

## 7. 注意事項

### 7.1 LLM 成本控管

Self-learning 每個 job 都打 LLM,**有成本**。粗估:
- extraction:每個 trace ~1500 prompt + 500 completion ≈ ~1 cent (Claude Haiku) / ~0.5 cent (gpt-4o-mini)
- verification:同上
- 100 個 trace/天 ≈ ~$2-4/天 LLM 成本

對策:
- 用便宜的本地 ollama(已 setup,免錢但慢)
- 或限縮 `--extraction-limit`
- 或 `--skip verification`(verification 是最貴的 step,先犧牲它換速度)

### 7.2 LLM Stochasticity 影響

同樣 prompt 跑兩次,extractor 可能抽出**不一樣的 observation**。這是設計上預期的(`dedupe_key` 會去重 byte-identical 的,但語意相似但字串不同的會分開記)。

不要過度 react:看 `consolidator` 跑完後的 instinct 才是穩定信號。

### 7.3 Candidate 上線後的回滾

如果某 candidate approved 後發現有 regression:

```python
from learning.candidate_generator import deactivate_candidate
deactivate_candidate(db, candidate_id="...", reason="regression on case STK-XX")
```

`deactivate` 會:
1. 把 candidate.status 改 `deactivated`
2. 從 prompt_templates 移除對應 rule(或者切換到備份版)
3. 寫 audit_log

⚠️ 上線前的 4-gate 是預防,**不能取代** post-launch 監控。每次 approve 後盯 7 天 baseline pass rate。

### 7.4 災備 / Backup

每週 backup learning_* 7 個 collection:

```bash
mongodump --db tflex_demo --collection learning_observations --out /backup/$(date +%Y%m%d)/
mongodump --db tflex_demo --collection learning_instincts --out /backup/$(date +%Y%m%d)/
mongodump --db tflex_demo --collection learning_candidates --out /backup/$(date +%Y%m%d)/
mongodump --db tflex_demo --collection verifier_results --out /backup/$(date +%Y%m%d)/
mongodump --db tflex_demo --collection learning_audit_log --out /backup/$(date +%Y%m%d)/
mongodump --db tflex_demo --collection task_traces --out /backup/$(date +%Y%m%d)/
```

task_traces 是最大宗,可以另外設 7-30 天 retention(老 trace 清掉省空間;但保留至少 30 天,因為 resolution_detection 預設看 30 天窗口)。

### 7.5 不要做的事

- ❌ **不要把 candidate 自動 approve**。整套設計核心是人類 gate,自動 approve 等於把 prompt 隨意修改交給 LLM。
- ❌ **不要在 task_traces 上加自定義欄位**。會打破 schema 假設,讓 filter 邏輯失準。要加新欄位走 spec 修改流程。
- ❌ **不要直接 update learning_observations / learning_instincts 內容**。要走 audit_log 標記的流程(改 status / reason),不然失去回溯能力。
- ❌ **不要在生產 db 直接跑 dry-run 之外的實驗**。先在 staging 跑、看 audit_log。

---

## 8. 緊急停機

關掉 nightly cron:
```bash
crontab -e   # 註解掉 GenBI 那行
```

關掉 app 內 trace 寫入(避免 task_traces 繼續累積):
```bash
# .env 加一行(若有實作 toggle;或直接拔 task_trace.py 的 insert_one 那段)
GENBI_TASK_TRACE_DISABLED=true
```

> 目前 trace 寫入沒有 env switch,如果要熱關閉只能臨時 monkey-patch 或回滾 commit。建議下版本(v0.11.x+)補上 env toggle。

---

## 9. 版本歷史(self-learning 相關)

| 版本 | 內容 |
|---|---|
| v0.8.0-0.8.2 | bootstrap / failure_filter / observation_extractor |
| v0.8.4 | verifier + 4-component confidence |
| v0.8.5 | instinct_consolidator + contradiction_scan |
| v0.8.10 | resolution_detector + candidate_generator + regression_gate |
| v0.9.0 | Admin UI(pages/06_learning_review.py) + dashboard_metrics |
| v0.9.2 | confidence_decay + run_learning_jobs.py orchestrator |
| v0.11.0 | test_runner.py 接 TaskTrace,baseline run 也餵料 |
| v0.11.0.1 | hotfix · 修 task_trace datetime 序列化 bug(failure_filter 終於能 match) |

完整變動細節看 `CHANGELOG.md`。

---

## 10. 聯絡 & 升級路徑

- 系統設計疑問 → 看 `GenBI_v1.3_Self_Learning_MVP_Implementation_Spec.md`
- 程式 / module 細節 → 看 `learning/` 各檔頂部 docstring
- 維運操作 → 本文件
- 緊急 issue → 開 GitHub Issue + 附 trace_id + audit_log 對應行
