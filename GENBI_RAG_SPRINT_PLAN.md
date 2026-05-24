# GenBI RAG Prompt System — Sprint Plan

> 對應 spec:`GENBI_RAG_PROMPT_DESIGN.md`(v0.1 draft)
> 對應 milestone:M6.1 → M6.6
> 對齊既有 GenBI 命名:v0.16.0 = M6.1 ship,v0.16.6 = M6 完工
> 2026-05-23

## 0. 總攬

**規模**:7 週 / 1 人(80% 投入)
**Sprint 長度**:2 週
**Sprint 數**:**4 個 sprint**(共 8 週,含 buffer)
**人力假設**:Solo dev,跟既有 GenBI 同人接手

| Sprint | Week | Milestone covered | Ship 版本 |
|---|---|---|---|
| **Sprint 1** | W1-W2 | M6.1 RAG infra + M6.2 Phase 0/A/D RAG | v0.16.0 / v0.16.1 |
| **Sprint 2** | W3-W4 | M6.4 A/B framework(緊跟 spec §20 建議)| v0.16.2 |
| **Sprint 3** | W5-W6 | M6.3 Few-shot + Anti-pattern + Chart-recipe | v0.16.3 / v0.16.4 |
| **Sprint 4** | W7-W8 | M6.5 Self-learning extension + M6.6 hardening + buffer | v0.16.5 / v0.16.6 |

每 sprint 結尾:**baseline regression + 245+ test 跑通 + commit/tag/push**。

---

## Sprint 1 · M6.1 + M6.2 · Foundation + Schema/KPI RAG

**目標**:RAG infra 就緒、Schema/KPI RAG 在 Phase 0/A/D 跑通,**token 省 40% 主效益 day-14 拿到**。

**Ship**:v0.16.0(M6.1 infra)+ v0.16.1(M6.2 wire)

### Week 1 · M6.1 RAG Infra

#### Day 1-2:Dependency + 基礎模組

- [ ] `pip install sentence-transformers chromadb` + 加進 `requirements.txt`
- [ ] 新建 `embedding_pipeline.py`
  - [ ] `get_embedding_model()` singleton(streamlit `@st.cache_resource` 友善)
  - [ ] `embed_texts(list[str]) -> np.ndarray`
  - [ ] env flag `GENBI_EMBEDDING_MODEL`(default `all-MiniLM-L6-v2`)
- [ ] 新建 `rag_index_repository.py`
  - [ ] `RAGIndexRepository` class,wraps `chromadb.Client(path=...)` embedded mode
  - [ ] `add_doc(index_name, doc_id, content, embedding, metadata)`
  - [ ] `search(index_name, query_embedding, top_k, filter)` → list of `RAGSearchResult`
  - [ ] `delete_doc / list_docs / clear_index`
- [ ] Unit test:embedding deterministic + Chroma round-trip + filter

**Acceptance**:`pytest tests/unit/test_embedding_pipeline.py tests/unit/test_rag_index_repository.py -v` 20+ tests pass。

#### Day 3-4:Index build script + nightly job

- [ ] `scripts/build_rag_indices.py`
  - [ ] CLI flags:`--full-rebuild / --index <name> / --domain <domain>`
  - [ ] 從 `domain_metadata` collection 抽 schema doc → schema_index
  - [ ] 從 `domain_metadata.kpi_definitions` 抽 KPI doc → kpi_index
  - [ ] 進度條 + 統計 print
- [ ] `rag_index_versions` MongoDB collection schema(`_id / index_name / version / embedding_model / doc_count / status / created_at`)
- [ ] Nightly cron snippet 加進 `scripts/run_learning_jobs.py`(或獨立 cron)

**Acceptance**:`python3 scripts/build_rag_indices.py --full-rebuild` 跑完跨 tflex / ecommerce / healthcare 三 domain,印出每 index 的 doc count。

#### Day 5:Retrieval orchestrator(MVP slot)

- [ ] 新建 `retrieval_orchestrator.py`
  - [ ] `RetrievalOrchestrator(__init__)` 收 4-5 個 RAGIndexRepository
  - [ ] `retrieve_for_phase(phase, query, domain, intent, rag_enabled=True) -> dict`
  - [ ] per-phase policy table(Phase 0 抽 schema+kpi+few_shot,etc — 對應 spec §11.2)
  - [ ] `_inject_slots` token budget 截斷(per slot max char,spec §11.5)
- [ ] Unit test:fake index + 確認 slot 注入正確

**Acceptance**:`tests/unit/test_retrieval_orchestrator.py` 15 tests pass。

### Week 2 · M6.2 Phase 0/A/D RAG wire

#### Day 6-7:Prompt template 改 RAG-aware

- [ ] `embedded_prompts.py` 加 RAG-aware 變體:
  - [ ] `_inline_phase_0_plan_prompt_rag`(STATIC_HEADER + slot + STATIC_FOOTER)
  - [ ] `_inline_phase_a_pipeline_prompt_rag`
  - [ ] `_inline_phase_d_insight_prompt_rag`
- [ ] `llm_service.py` 對應 `_render_*` 加 RAG branch:
  ```python
  if rag_enabled and self.rag_orchestrator:
      rag_context = self.rag_orchestrator.retrieve_for_phase(...)
      return self._inline_phase_0_plan_prompt_rag(**rag_context)
  return self._inline_phase_0_plan_prompt()   # legacy fallback
  ```
- [ ] `LLMService.__init__` 加 `rag_orchestrator: Optional[...]` 參數(default None)
- [ ] `config.py` 加 `GENBI_RAG_ENABLED` env(default False)

**Acceptance**:`GENBI_RAG_ENABLED=false` 跑 baseline byte-equal v0.15(凍結驗證)。

#### Day 8-9:End-to-end 驗證

- [ ] `app.py` `_build_llm_service_for_domain` 加 RAG orchestrator
- [ ] Test_runner.py 加 `--rag-on / --rag-off` flag
- [ ] 跑 5-10 個典型 schema-driven query 對比:
  - [ ] RAG-off prompt token vs RAG-on prompt token
  - [ ] RAG-on retrieval latency
  - [ ] Pass rate(短 sample,5-10 queries)
- [ ] 印 token saved % + latency saved %

**Acceptance**:**Token 省 ≥ 30%(實測)** + RAG-off baseline byte-equal。

#### Day 10:Documentation + ship

- [ ] AI_CONTEXT.md 加 §23 RAG infra 章節
- [ ] CHANGELOG entries
- [ ] Commit & push & tag v0.16.0 + v0.16.1

### Sprint 1 Demo target

> Streamlit 跑 tflex 既有 query「比較各 company 的 hc」,RAG-on 模式下 Phase 0 prompt size 從 8K → 3K,latency 從 25s → 14s,結果 byte-equal RAG-off。Demo 給 stakeholder 看 token 帳單省一半。

### Sprint 1 Risks + Mitigation

| Risk | Mitigation |
|---|---|
| sentence-transformers cold load 太慢 | Day 1 就驗 `@st.cache_resource` singleton mode |
| Chroma 跟 MongoDB co-host RAM 爆 | Day 3 監控 mem,必要時 chunk index build |
| Phase 0 prompt 改了 baseline 變動 | Day 6 改前先 lock v0.15 baseline doc,改後 diff 比 byte-equal |
| RAG-on first attempt LLM 行為怪 | Day 8 留 day 9 緩衝排查 prompt template syntax issue |

---

## Sprint 2 · M6.4 · A/B Framework

**目標**:RAG 上之後 LLM 行為穩定性的**安全網**,**緊跟 Sprint 1** 而非延後到 M6.5。

**Ship**:v0.16.2

### Week 3 · Variant tracking + Experiment lifecycle

#### Day 11-12:`task_trace` schema extend

- [ ] `task_trace.py` 加欄位:
  - [ ] `prompt_variant: dict[str, str]` per-index
  - [ ] `rag_chunks_used: list[dict]`
  - [ ] `rag_total_latency_ms: float`
- [ ] Migration:既有 trace 沒這欄位 → safe default
- [ ] 確認 self-learning loop(failure_filter 等)對新欄位 backward compat

**Acceptance**:既有 task_trace test 全綠 + 新欄位有寫到。

#### Day 13-14:`ab_framework.py`

- [ ] `ab_framework.split_traffic(session_id, index_name) -> 'champion' | 'challenger'`
  - hash-based 一致性(同 session 同 split)
  - 設定來自 `ab_experiments` collection
- [ ] `ab_experiments` collection schema(對齊 spec §7.2)
- [ ] `start_experiment / pause / resume` API
- [ ] Unit test:large N query 後 split % 落在 ±2% 容差

**Acceptance**:`tests/unit/test_ab_framework.py` 12 tests pass。

#### Day 15:Retrieval orchestrator 接 A/B

- [ ] `retrieve_for_phase` 對每 index 查 ab_experiments → 走 champion or challenger version
- [ ] `rag_index_repository` 支援 multi-version 並存(版本 suffix:`schema_index_v2`, `schema_index_v3`)
- [ ] task_trace 記 per-index variant

**Acceptance**:跑 100 query,trace `prompt_variant` 欄位 80:20 落點正確。

### Week 4 · Auto-promotion + Admin UI

#### Day 16-17:`rag_promotion_service.py`

- [ ] `evaluate_experiment(experiment_id) -> {action, metrics, p_value}`
  - per-index 不同 metric(spec §13.4):
    - schema: hallucinated column rate
    - kpi: KPI formula error rate
    - 其他暫用 overall pass rate
  - statistical significance(scipy `chi2_contingency` for pass rate, `ttest_ind` for latency)
- [ ] `auto_promote / auto_rollback` logic
- [ ] `rag_promotion_log` collection write

**Acceptance**:`tests/unit/test_rag_promotion_service.py` 15 tests pass。

#### Day 18-19:Admin UI

- [ ] `pages/09_rag_indices.py`
  - [ ] 列 5 個 index 的 champion / challenger version
  - [ ] Browse doc(content / embedding stats / metadata)
  - [ ] 手動 promote / rollback button
  - [ ] 即時 metric trend(實作:基於 task_traces aggregate)
- [ ] Nightly job `evaluate_all_experiments`
- [ ] Cron snippet:`0 3 * * * python3 scripts/evaluate_rag_experiments.py`

**Acceptance**:human 在 admin UI 能手動跑完一輪 experiment lifecycle(start → run → eval → promote)。

#### Day 20:Integration test + ship

- [ ] Integration test:end-to-end A/B(start experiment → run 100 query → eval → promote)
- [ ] Commit & push & tag v0.16.2

### Sprint 2 Demo

> 開 1 個 experiment:schema_index champion v1 vs challenger v2(challenger 多 5 個 doc)。跑 100 個 query,traffic 80:20,nightly job 自動算 metric,印出 promote / rollback / continue 決定。

### Sprint 2 Risks

| Risk | Mitigation |
|---|---|
| 統計顯著性 small sample size 算不準 | 設 min sample 100 traces 才 evaluate;否則 continue |
| Rollback 後既有 trace 還引用舊 index | rag_index_repository 保留 deprecated version 30 天 |
| Per-index metric 對齊難 | Day 16 先 pick 2 個 index 做 deep metric(schema + few_shot),其他用 overall pass rate proxy |

---

## Sprint 3 · M6.3 · Few-shot + Anti-pattern + Chart-recipe

**目標**:Phase B/C 也走 RAG,從 trace + instinct 自動 build index。

**Ship**:v0.16.3(few-shot)+ v0.16.4(anti-pattern + chart-recipe)

### Week 5 · Few-shot index

#### Day 21-22:Build from task_traces

- [ ] `scripts/build_few_shot_index.py`
  - 過濾 `status=completed + verified=True + confidence > 0.85`
  - per phase 抽 query + phase output excerpt
  - PII filter on content(M4b PII detector reuse)
- [ ] Few-shot doc schema(對齊 spec §7.4.2)
- [ ] Initial build:用既有 26 case baseline trace + Upload Workspace test traces 當 seed

**Acceptance**:few_shot_index 至少 50 doc,query 「比較各 company 的 hc」 retrieve top-2 都是相關 case。

#### Day 23-24:Phase A/B prompt wire

- [ ] `embedded_prompts.py` 加 `{{ rag_few_shot }}` slot 到:
  - `_inline_phase_a_pipeline_prompt_rag`
  - `_inline_phase_b_preprocess_prompt_rag`
- [ ] Phase A/B retrieve 走 `intent` filter(避免 line chart 抽到 bar chart 例子)
- [ ] Test:RAG-on Phase A/B 跑 5-10 個 query,看 prompt 結構正確

**Acceptance**:Phase A/B RAG-on 跑 baseline,pass rate 不降(尤其先看是否 still byte-equal-ish)。

#### Day 25:Sprint 3 mid checkpoint

- [ ] Commit & push & tag v0.16.3

### Week 6 · Anti-pattern + Chart-recipe

#### Day 26-27:Anti-pattern index

- [ ] `scripts/build_anti_pattern_index.py`
  - source = `learning_instincts.find(active=True, confidence > 0.85)`
  - content = instinct context + recommendation
- [ ] Phase B/C prompt 加 `{{ rag_anti_pattern }}` slot
- [ ] **觸發式注入**:retrieve score < threshold(default 0.5)不注入,避免不相關干擾

**Acceptance**:跑 borderline query(STK-05 之類有 history pattern),anti_pattern slot 該注入相關 hint。

#### Day 28-29:Chart-recipe index

- [ ] `scripts/build_chart_recipe_index.py`
  - source = `embedded_prompts._PHASE_C_INTENT_BLOCKS`
  - 加 user-saved Saved Chart `chart_option`(M3A 已有)
- [ ] Phase C prompt 加 `{{ rag_chart_recipe }}` slot
- [ ] Per-intent retrieval(filter by intent)

**Acceptance**:histogram query → retrieve histogram recipe;stacked query → retrieve stacked recipe。

#### Day 30:Sprint 3 ship

- [ ] Full baseline run RAG-on,確認 pass rate ≥ RAG-off
- [ ] Commit & push & tag v0.16.4

### Sprint 3 Demo

> 跑「畫 hc 分佈直方圖」query:RAG retrieve 到 histogram chart_recipe + 加「ECharts 沒有 histogram type」anti-pattern hint。Phase C 直接寫對(bar + markLine),不再進 retry。

### Sprint 3 Risks

| Risk | Mitigation |
|---|---|
| Few-shot seed 量不夠(<50)→ retrieve 不準 | Day 21 並行寫 fixture 加 30 個 synthetic case |
| Anti-pattern 不該注入時硬注入(noise)→ LLM confused | 觸發 score threshold 0.5+;debug panel 顯示 "trigger" / "skipped" |
| Chart-recipe doc content 太長(完整 code 1.5K char)| 抽 simplified template(structure only),完整 code 在 prompt static footer |

---

## Sprint 4 · M6.5 + M6.6 · Self-learning Extension + Hardening

**目標**:閉環自動化(失敗 trace → instinct → RAG doc candidate → A/B → auto promote)+ production-ready。

**Ship**:v0.16.5(M6.5)+ v0.16.6(M6 完工)

### Week 7 · M6.5 Self-learning extension

#### Day 31-32:`candidate_generator.py` 擴展

- [ ] 加 4 種新 candidate type:`schema_index_doc / few_shot_doc / anti_pattern_doc / chart_recipe_doc`
- [ ] 從 verified instinct 分類產對應 candidate:
  - 高 confidence + 觸發 pattern 明確 → `anti_pattern_doc`
  - 成功 trace 跟現有 few_shot 顯著不同 → `few_shot_doc`
  - 新 schema field description → `schema_index_doc`
  - 新 chart variant → `chart_recipe_doc`
- [ ] `prompt_rule_candidates` collection 加 `candidate_type` field

**Acceptance**:跑既有 `python -m learning.candidate_generator`,產出至少各 1 個 4 種 candidate type。

#### Day 33-34:`regression_gate.py` per-type

- [ ] per-candidate-type evaluate logic:
  - `schema_index_doc`:hallucinated rate before/after
  - `few_shot_doc`:overall pass rate before/after
  - `anti_pattern_doc`:retry rate before/after
  - `chart_recipe_doc`:Phase C exec error rate before/after
  - `prompt_template_patch`(現有):test_runner baseline diff

**Acceptance**:`tests/unit/test_regression_gate_per_type.py` 20 tests pass。

#### Day 35:Admin UI extension

- [ ] `pages/06_learning_review.py` 加 RAG candidate tab
  - 分 5 個 sub-tab(prompt_patch / schema_doc / few_shot / anti_pattern / chart_recipe)
  - 每 candidate 顯示:來源 trace_id / verified instinct / proposed doc content / regression metric

### Week 8 · M6.6 Production hardening + Doc

#### Day 36-37:Monitor + alerting

- [ ] Metrics dashboard(`pages/09_rag_indices.py` Tab 4)
  - per-index retrieve latency p50/p95/p99 trend
  - per-index hit rate
  - Champion/Challenger pass rate gap
- [ ] Alert rule:
  - p99 latency > 1s 連續 6 hr → log warning
  - Hit rate < 50% 連續 7 day → flag index 可能需重 build
  - Auto-rollback 觸發 → notify(stub:print + log)

#### Day 38-39:Embedding model upgrade path

- [ ] `embedding_pipeline.get_model(model_name)` 支援多 model 並存
- [ ] `rag_index_repository` per-doc 存 `embedding_version`
- [ ] Migration script:`scripts/reembed_index.py`(舊 model → 新 model 平滑切換)

#### Day 40:Final integration + ship

- [ ] Full baseline regression(`test_runner.py` RAG-on + RAG-off 都跑)
- [ ] Acceptance suite 加 5 個 RAG-specific test
- [ ] AI_CONTEXT.md §23 完整 RAG 章節
- [ ] Commit & push & tag **v0.16.6 · M6 完工**

### Sprint 4 Demo

> 真實閉環:從 `test_runner.py --baseline` 跑出 STK-05 failure trace → nightly observation_extractor → instinct → candidate_generator 產 anti_pattern_doc candidate → regression_gate 自動跑 evaluation → metric 通過 → auto_promote 加進 anti_pattern_index → 下次 STK-05 query 進來時 LLM 看到 hint,**人類沒參與**整個迭代

### Sprint 4 Risks

| Risk | Mitigation |
|---|---|
| Candidate generator 產 noise(無價值 candidate)→ review queue 爆 | Day 32 加 candidate dedup + min confidence threshold + cap 每天 10 個 |
| Embedding model upgrade 過程 index serve 中斷 | dual-version strategy(舊版 serve、新版 build,build 完 swap),不 hard cutover |
| 4 weeks 結束沒做完 M6.6 | hardening 可拆 v0.16.7 後續做,先 ship M6.5 主功能 |

---

## 整 sprint 完工後狀態

### Code 規模(估)

| 新增/擴充 | Module | LoC |
|---|---|---:|
| 新 | `embedding_pipeline.py` | ~100 |
| 新 | `rag_index_repository.py` | ~250 |
| 新 | `retrieval_orchestrator.py` | ~300 |
| 新 | `ab_framework.py` | ~200 |
| 新 | `rag_promotion_service.py` | ~250 |
| 新 | `scripts/build_rag_indices.py` | ~150 |
| 新 | `scripts/build_few_shot_index.py` | ~120 |
| 新 | `scripts/build_anti_pattern_index.py` | ~80 |
| 新 | `scripts/build_chart_recipe_index.py` | ~80 |
| 新 | `scripts/reembed_index.py` | ~100 |
| 新 | `scripts/evaluate_rag_experiments.py` | ~80 |
| 新 | `pages/09_rag_indices.py` | ~350 |
| 擴充 | `llm_service.py` | +100 |
| 擴充 | `embedded_prompts.py` | +500(RAG-aware variants) |
| 擴充 | `task_trace.py` | +30 |
| 擴充 | `learning/candidate_generator.py` | +150 |
| 擴充 | `learning/regression_gate.py` | +100 |
| 擴充 | `pages/06_learning_review.py` | +200 |
| 擴充 | `config.py` | +20 |
| 擴充 | `requirements.txt` | +3 (sentence-transformers / chromadb / scipy) |
| 擴充 | `AI_CONTEXT.md` | +300 |

**Total**:**~3400 LoC** production code + **~500 LoC** tests + **~500 LoC** docs

### Test coverage 預期

- Sprint 1:+30 unit + 5 integration → 360 total
- Sprint 2:+25 unit + 3 integration → 388 total
- Sprint 3:+20 unit + 5 integration → 413 total
- Sprint 4:+15 unit + 5 acceptance → 433 total

### 預期 production metric

| 指標 | v0.15(baseline) | v0.16 完工 |
|---|---|---|
| Prompt token per query(avg) | ~8K | ~3K(−60%) |
| Per-query wall time(p50)| ~25s | ~14s(−44%) |
| Pass rate(test_runner baseline) | ~80% | ~85%(few-shot 加 borderline 例子) |
| Retry rate(per phase) | ~25% | ~17%(−32%,anti_pattern hint 提前防雷) |
| 人類 prompt patch cycle(week) | ~1/week | ~1/month(自動 candidate 大幅減)|

---

## 跨 sprint 依賴

```
Sprint 1 (M6.1+M6.2)
   ↓ infra ready
Sprint 2 (M6.4 A/B framework)
   ↓ 安全網 ready
Sprint 3 (M6.3 完整 RAG coverage)
   ↓ 全 phase RAG-aware
Sprint 4 (M6.5+M6.6 自動化 + hardening)
   ↓
v0.16.6 ship
```

關鍵路徑:**Sprint 1 → Sprint 2**(沒 A/B 不敢動 RAG)
平行路徑:Sprint 3 跟 Sprint 2 末段可以 overlap(Day 18-20 開始 anti_pattern 探索)

---

## Sprint 0(可選 · 規劃前 buffer 1 週)

如果想先驗 ROI、不直接動 production code,可以加 **Sprint 0(pre-flight)**:

**Day 0-3** · POC + Benchmark

- [ ] 本機跑 `pip install sentence-transformers chromadb` 全套裝起來
- [ ] 對既有 26 case baseline trace 量測:
  - prompt token 分佈(Phase 0/A/B/C/D each)
  - LLM call latency 分佈
- [ ] 用 5-10 個 query 手動模擬 RAG-on prompt size(直接組 prompt 看 token 差)
- [ ] 確認**真的能省 30%+ token**,再進 Sprint 1

**Day 4-5** · Approve / Pivot

- [ ] 把 benchmark 結果寫成 1 頁 markdown(prompt token saved / latency saved estimate)
- [ ] 跟 stakeholder confirm go/no-go
- [ ] go → start Sprint 1;no-go → revisit spec

**建議**:如果 stakeholder 有不確定,Sprint 0 跑完再投入 Sprint 1-4;若確定就動,跳過 Sprint 0 直接開工。

---

## 進度追蹤 / Standup template

每天 standup 提 3 件事:

1. **昨天完成了什麼**(對應 Day X 的 [ ] item)
2. **今天要做什麼**(下一個 [ ] item)
3. **卡住嗎**(blocker / risk surfacing)

每 sprint 結尾 demo:
- 一個 specific scenario 跑通(不抽象,實機 Streamlit demo)
- 量化 metric(token / latency / pass rate)before/after
- 下個 sprint 的 risk + mitigation 更新

---

## Definition of Done(整個 M6)

對照 spec §19:

- [ ] M6.1-M6.6 全部 milestone 收齊
- [ ] Phase 0/A/B/C/D prompt 全部 RAG-aware
- [ ] 5 個向量 index 各自 promote / rollback workflow 跑通
- [ ] A/B framework 端到端跑通,至少 1 個 index 完成 1 輪 auto-promote
- [ ] Token 省 ≥ 40%(實測 5 個 query)
- [ ] Wall time 省 ≥ 30%
- [ ] Self-learning loop 自動產 4 種新 candidate type
- [ ] test_runner.py 跑 RAG-off 仍 byte-equal v0.15
- [ ] RAG-on 跑 baseline 通過 regression gate(no pass → non-pass)
- [ ] AI_CONTEXT.md 加 §23 RAG 章節
