# GenBI Prompt 瘦身 + RAG 動態注入 + 自動迭代規格書

> v0.1 draft — Design proposal for v0.16+(對齊 M6 milestone)
> Architecture / Engineering Spec
> 2026-05-23

## 版本更新摘要

本文件提出 GenBI 第三大架構改造:**動態 prompt assembly via RAG**。從現在的「全域 monolithic prompt」走向「critical-rule hard-code + dynamic-context RAG retrieve」。

3 個核心閉環:

1. **Prompt 瘦身**:把 6-12K 的 phase prompt 壓到 ~3K,token / latency 雙降。
2. **RAG 動態注入**:per-query 從多個向量 index 抽 schema / few-shot / anti-pattern / chart recipe,只塞 relevant chunk 進 prompt。
3. **自動迭代**:把既有 self-learning loop 擴展從「只能 patch prompt 字串」 → 「可以新增 RAG doc(粒度更細、回滾更易)」+ Champion / Challenger A/B framework + 自動 promote。

預期效益:
- **per-query latency:25s → 14s**(qwen3-coder:30b @ 8K ctx,實測 estimation)
- **prompt token:8K → 3K(60% 省)**
- **整體 pass rate:預期 +5-10%**(few-shot retrieve 給 LLM 對標的成功例子)
- **prompt iteration cycle:從週級(human review patch)→ 日級(auto promote A/B)**

---

## 1. 背景與目標

### 1.1 GenBI v0.11-v0.15 prompt 現況

GenBI 既有 prompt 機制(v0.5/v0.6 modular + v0.3 repo + v0.8 self-learning)已優化過:

- **Phase C** 從 v0.5 monolithic 24K → modular 11 intent block + universal header,降到 6-12K per call
- **Phase B** 從 v0.6 拆 6 intent block,~3-6K per call
- **Prompt repo** v0.3+ DB-backed,可線上編輯
- **Self-learning loop** v0.8-0.9 從 trace 自動產 prompt_rule_candidate

但仍有結構性問題:

| 痛點 | 現況 | 後果 |
|---|---|---|
| Domain knowledge 全注入 | `build_domain_knowledge(metadata)` 把全部 schema / KPI / 限制塞進 prompt | Phase 0/A/D 每 query 多吃 2-3K 不相關 token |
| Anti-pattern cheatsheet 固定 | `PANDAS_ANTIPATTERN_CHEATSHEET` 60+ 行 always-on | 每 query 多吃 ~1K token,實際多數 query 不踩雷 |
| Few-shot examples 寫死 | `build_echarts_few_shot` 從 metadata `recommended_charts` 拼,domain-agnostic 但 query-agnostic | LLM 看不到「跟你 query 類似的成功 case」|
| Prompt iteration 粒度大 | self-learning candidate 只能 patch prompt 字串 | 改錯 1 個 rule = 全 query 受影響,難 A/B |
| Critical rule vs dynamic context 混在一起 | 同 prompt 內既有「JSON-only」又有 schema 描述 | LLM 注意力被分散 |

### 1.2 目標

3 條互補目標:

1. **瘦身**:把 phase prompt 從 6-12K 壓到 3K,**token 省 60% + LLM latency 省 40%**(基於本 spec §4.4 cost model)
2. **RAG 動態**:per-query 抽 relevant 5-10 chunks 注入,讓「LLM 只看跟 query 相關的 context」
3. **自動迭代**:擴展 self-learning loop 從「prompt patch only」→ 「prompt + RAG doc」,粒度細、回滾易、A/B 自動

### 1.3 不在範圍

- 替換 LLM provider(仍 Ollama / vLLM OpenAI-compat)
- 改 task_trace schema(沿用)
- 改 5-phase pipeline 形狀(同樣 Pre-0 → 0 → A → B → C → D)
- 影響 schema-driven 主路徑 baseline(凍結條款守住)

---

## 2. 問題定義

### 2.1 為什麼 prompt 一直變大

GenBI 的 prompt 一直加東西,因為:
- **每次 LLM 出包就加 rule**(self-learning loop 機制),累積到 rule 千行不誇張
- **每個 domain 加 metadata 就加 schema 字段**,15 個 collection × 8 個欄位 = 120 字段全注入
- **新加 chart intent 就加 block**,Phase C 已 11 block

LLM context window 看起來大(8K-128K),但實務上:
- 大 prompt → first-token latency 線性增加
- 大 prompt → LLM 注意力分散,critical rule 容易被忽略
- 大 prompt → token cost 線性增加(production 走 vLLM / OpenAI 時)

### 2.2 為什麼 RAG 是對的解法

不是所有「動態 context」都該寫死進 prompt。比較:

| 內容類別 | 該 hard-code 嗎? | 理由 |
|---|---|---|
| `JSON-only output` rule | ✅ Hard-code | 漏掉直接炸 |
| `禁 import 任何套件` | ✅ Hard-code | Safety,不能 retrieve fail |
| `q_columns` 鎖死 | ✅ Hard-code | Critical,變數名鎖到必須一致 |
| Schema field description(`employee_id` 是 ID 欄位)| ❌ RAG | Per-query 只需要 query 提到的 5-10 個欄位 |
| KPI definition(`average_return_rate = sum(return)/sum(payable)`)| ❌ RAG | 同上 |
| `Q['col'].first()` 是 anti-pattern | ❌ RAG | 大多數 query 不會踩這個雷 |
| 「分佈直方圖該用 bar + markLine」chart recipe | ❌ RAG | 只跟 histogram intent 相關 |

**核心洞察**:hard-code 的是「**always-on safety / format**」,RAG 的是「**only-needed-when-relevant context**」。

### 2.3 RAG 引入的新風險

1. **Retrieval 漏掉 critical schema** → LLM hallucinate 不存在的欄位
2. **Few-shot retrieve 給錯例子** → LLM 學壞
3. **Embedding 模型版本不一致** → index stale
4. **Index 過時** → instinct outdated 仍被注入

每個都在 §15 給對策。

---

## 3. 核心設計原則

### 3.1 Hard-code first, RAG second

任何「**漏掉就會炸**」的東西必須 hard-code。RAG 只服務「**多塞一點更好,沒抽到也能 work**」的內容。

### 3.2 Per-slot retrieval

不要單一全 index,要 5 個 specialized index(schema / KPI / few-shot / anti-pattern / chart-recipe)。每個 slot 有自己的:
- Embedding 內容(欄位 description vs KPI formula vs chart code,語意不同)
- Top-K(schema 拿 5,KPI 拿 3,few-shot 拿 2,anti-pattern 拿 3)
- Filter(per-domain / per-intent / per-source-type)

### 3.3 Self-learning loop 擴展,非取代

既有 v0.8 self-learning 架構(observation → instinct → candidate → regression gate → review)**不打掉**,只加新 candidate type:
- `prompt_template_patch`(現有)
- `schema_index_doc`(新)
- `few_shot_doc`(新)
- `anti_pattern_doc`(新)
- `chart_recipe_doc`(新)

Regression gate / human review pipeline 不變。

### 3.4 Champion / Challenger A/B by default

任何 RAG index update 都先進 challenger,流量上限 20%,連續 N 天通過 regression gate 才 auto-promote 成 champion。

### 3.5 RAG-off 行為 = baseline

`GENBI_RAG_ENABLED=false`(env default)= 既有 schema-driven byte-equal 行為。**baseline regression test 強制驗 RAG-off path**。

---

## 4. 功能範圍

### 4.1 M6.1 MVP 範圍(必須)

1. **`rag_index_repository.py`** — 4 個向量 index(schema / few-shot / anti-pattern / chart-recipe)
2. **Embedding pipeline** — local `sentence-transformers/all-MiniLM-L6-v2`(384-dim,300MB,CPU 快)
3. **Vector DB 後端** — embedded Chroma(無 server,純 file-based)
4. **`retrieval_orchestrator.py`** — parallel retrieve + re-rank + slot 注入
5. **Phase 0 prompt RAG-aware** — `{{ rag_schema }}` slot wired
6. **`GENBI_RAG_ENABLED` env flag** — default off
7. **Index build / rebuild script** — `scripts/build_rag_indices.py`

### 4.2 M6.2 範圍

1. Phase A / Phase D prompt 也加 RAG slot
2. Few-shot index source = `task_traces` 內 status='completed' + verified
3. Anti-pattern index source = `learning_instincts` active + confidence > 0.85

### 4.3 M6.3 範圍

1. Chart recipe index — 對 Phase C intent block 抽出可 RAG 的範例
2. Phase B / Phase C 加 RAG slot
3. Per-query intent 動態 chart recipe retrieval

### 4.4 M6.4 範圍(A/B framework)

1. `task_trace.prompt_variant: 'champion' | 'challenger'` 欄位
2. `rag_index_versions` collection — index version + champion/challenger pointer
3. Traffic split logic(% based)
4. Nightly stats job + regression gate per-index-type
5. Auto-promote / auto-rollback logic

### 4.5 M6.5 範圍(self-learning extension)

1. `candidate_generator.py` 加 4 種新 candidate type
2. `regression_gate.py` per-candidate-type metric(few-shot vs prompt patch 評分維度不同)
3. `pages/06_learning_review.py` 新增 tab 分 type 顯示
4. Observation → RAG-target 分類

### 4.6 Phase 3 範圍(未來)

1. Production vector DB(Qdrant / Weaviate)
2. Multi-tenancy index(per-org RAG)
3. Embedding 模型 fine-tune
4. Cross-lingual retrieval(中/英 query 互查)

---

## 5. 系統架構

### 5.1 高階架構

```
使用者 query
   │
   ▼
[Pre-Phase 0 · Intent Router]   (零 LLM call,沿用)
   │
   ▼
[RAG Orchestrator(M6.1+)]
   │ 並行 retrieve 5 個 index
   │ ├─ schema_index.top_k(query_emb, k=5)
   │ ├─ kpi_index.top_k(query_emb, k=3)
   │ ├─ few_shot_index.top_k(query_emb, k=2, filter=success)
   │ ├─ anti_pattern_index.top_k(query_emb, k=3)
   │ └─ chart_recipe_index.top_k(query_emb + intent, k=1)
   │ → re-rank + dedup
   │
   ▼
[Slot 注入到 prompt template]
   Phase 0 prompt 含 {{ rag_schema }}, {{ rag_kpi }}, {{ rag_few_shot }}
   Phase A prompt 含 {{ rag_schema }}, {{ rag_anti_pattern }}, {{ rag_few_shot }}
   Phase B prompt 含 {{ rag_anti_pattern }}, {{ rag_few_shot }}
   Phase C prompt 含 {{ rag_chart_recipe }}, {{ rag_anti_pattern }}
   Phase D prompt 含 {{ rag_kpi }}
   │
   ▼
[既有 5-phase pipeline]   (Phase 0/A/B/C/D 不動)
   │
   ▼
[Post-trace · A/B variant tag]
   trace.prompt_variant = 'champion' | 'challenger'
   trace.rag_chunks_used = [{index, doc_id, score}, ...]
   │
   ▼
[Nightly evolution job(M6.4+)]
   ├─ extract successful traces → few_shot_index challenger
   ├─ extract verified instincts → anti_pattern_index challenger
   ├─ regression_gate per-index
   └─ auto promote / rollback challenger
```

### 5.2 與既有架構整合

```
─────────────────────────────────────────────
新增模組(M6.x):
─────────────────────────────────────────────
rag_index_repository.py        ← 4 個 vector index CRUD + 統一介面
retrieval_orchestrator.py      ← parallel retrieve + re-rank + slot fill
embedding_pipeline.py          ← 統一 embedding(local / OpenAI 切換)
ab_framework.py                ← traffic split + variant tracking
rag_promotion_service.py       ← champion / challenger promotion logic

scripts/build_rag_indices.py   ← nightly rebuild from sources

─────────────────────────────────────────────
擴充既有模組:
─────────────────────────────────────────────
llm_service.py                 ← _render_*_prompt 加 RAG orchestrator hook
prompt_repository.py           ← 加 retrieval_strategy: dict field
task_trace.py                  ← 加 prompt_variant / rag_chunks_used 欄位
learning/observation_extractor ← 加 RAG-target classification
learning/candidate_generator   ← 產 4 種新 candidate type
learning/regression_gate       ← per-type metric
pages/06_learning_review.py    ← 新增 RAG candidate tab

─────────────────────────────────────────────
新增 MongoDB collection:
─────────────────────────────────────────────
rag_index_versions             ← index 版本 + champion/challenger
ab_experiments                  ← A/B 實驗紀錄
rag_promotion_log               ← promote / rollback 歷史
```

### 5.3 Vector DB 選擇

**M6.1 MVP**:**embedded Chroma**
- 無 server,純 file-based
- 跟 SQLite 一樣 in-process,GenBI 一起跑
- 適合 single-node dev + 中小規模 production
- 缺點:無 horizontal scaling

**Phase 3 production**:**Qdrant** 或 **pgvector**
- Qdrant:獨立 server,scale 好,Rust 寫的,latency 低
- pgvector:Postgres extension,若 production 已有 PG 就直接用
- 換 backend 透過 `rag_index_repository` 抽象層切

---

## 6. 模組設計

| 模組 | 職責 | 對齊 spec section |
|---|---|---|
| `rag_index_repository.py` | 4 個向量 index 的 CRUD + 統一介面(`add_doc / search / delete / rebuild`) | §9 |
| `embedding_pipeline.py` | 統一 embedding(local sentence-transformer / OpenAI),embedding_version 管理 | §9.5 |
| `retrieval_orchestrator.py` | parallel retrieve + cross-index re-rank + dedup + slot 注入 prompt | §11 |
| `ab_framework.py` | traffic split(% based) + variant tracking + experiment lifecycle | §13 |
| `rag_promotion_service.py` | per-index champion / challenger / auto-promote logic | §13.4 |
| `scripts/build_rag_indices.py` | nightly cron rebuild(from `task_traces` / `learning_instincts` / `domain_metadata`) | §14 |
| `pages/09_rag_indices.py` | Streamlit admin UI:browse index docs / view promotion log / manual promote | §16 |

---

## 7. 資料模型

### 7.1 `rag_index_versions`(M6.1+)

```python
{
    "_id": ObjectId,
    "index_name": "schema_index" | "kpi_index" | "few_shot_index" |
                  "anti_pattern_index" | "chart_recipe_index",
    "version": 3,
    "embedding_model": "all-MiniLM-L6-v2",
    "embedding_dim": 384,
    "doc_count": 1247,
    "status": "champion" | "challenger" | "deprecated",
    "promoted_at": ISODate | None,
    "promoted_by": "auto" | "alice",
    "promotion_reason": "challenger pass rate +8% vs champion p<0.01",
    "rollback_history": [...],
    "metrics": {
        "avg_query_latency_ms": 142,
        "p95_query_latency_ms": 280,
        "pass_rate": 0.84,
    },
    "created_at": ISODate,
    "updated_at": ISODate,
}
```

### 7.2 `ab_experiments`(M6.4+)

```python
{
    "_id": ObjectId,
    "experiment_id": "exp_20260601_schema_v3_vs_v2",
    "index_name": "schema_index",
    "champion_version": 2,
    "challenger_version": 3,
    "traffic_split": {"champion": 0.8, "challenger": 0.2},
    "started_at": ISODate,
    "ended_at": ISODate | None,
    "status": "running" | "promoted" | "rolled_back",
    "metrics": {
        "champion_traces": 3217,
        "challenger_traces": 812,
        "champion_pass_rate": 0.82,
        "challenger_pass_rate": 0.89,
        "p_value": 0.003,
    },
}
```

### 7.3 `rag_promotion_log`(M6.4+)

```python
{
    "_id": ObjectId,
    "index_name": "few_shot_index",
    "action": "promote" | "rollback",
    "from_version": 2,
    "to_version": 3,
    "trigger": "auto" | "manual",
    "trigger_by": "nightly_job" | "alice",
    "reason": "...",
    "metrics_snapshot": {...},
    "at": ISODate,
}
```

### 7.4 Vector index doc(per index 不同)

#### 7.4.1 `schema_index` doc

```python
{
    "doc_id": "schema_tflex_applications_review_status",
    "content": "review_status (string): 申請審核狀態。allowed: Y, N, R, X (Y=已通過...)。",
    "embedding": [0.123, -0.045, ...],   # 384 dim
    "embedding_model": "all-MiniLM-L6-v2",
    "embedding_version": 1,
    "domain": "tflex",
    "table": "tflex_applications",
    "field_name": "review_status",
    "semantic_role": "categorical_status",
    "metadata_version_source": 12,
    "created_at": ISODate,
}
```

#### 7.4.2 `few_shot_index` doc

```python
{
    "doc_id": "fewshot_trace_abc123_phase0",
    "content": "Q: 比較各 company 的 hc\nPlan: ...\nQ.columns: [company_code, hc]\nChart: bar",
    "embedding": [...],
    "source_trace_id": "abc-123",
    "source_query": "比較各 company 的 hc",
    "phase": "plan" | "preprocess" | "echarts",
    "domain": "tflex",
    "intent": "bar_horizontal",
    "success": True,
    "confidence": 0.92,
    "verification_status": "verified",
    "created_at": ISODate,
}
```

#### 7.4.3 `anti_pattern_index` doc

```python
{
    "doc_id": "antipattern_instinct_5",
    "content": "Phase B 不要對 Q 用 raw_df 級欄位 filter。Q 是 agg 後的 final。",
    "embedding": [...],
    "source_instinct_id": "instinct-5",
    "phase_target": ["preprocess", "echarts"],
    "trigger_pattern": "KeyError on raw_status / review_result / review_mechanism",
    "confidence": 0.93,
    "evidence_count": 14,
    "created_at": ISODate,
}
```

#### 7.4.4 `chart_recipe_index` doc

```python
{
    "doc_id": "recipe_histogram_with_markers",
    "content": "Histogram with markLine for avg/median/P95:\n```python\noption = {...}\n```",
    "embedding": [...],   # 對 query 通常用「分佈/histogram + markLine」字串 embed
    "intent": "histogram",
    "q_columns_required": ["bin_label", "bin_midpoint", "count"],
    "echarts_features": ["bar", "markLine"],
    "source": "embedded_prompts.PHASE_C_BLOCK_HISTOGRAM",
    "created_at": ISODate,
}
```

---

## 8. 向量 Index 規格(per-index)

### 8.1 schema_index

**Source**:`domain_metadata` collection + `upload_metadata_versions`(confirmed)。

**Build**:對每個 (`domain`, `table`, `field`) 三元組,把 `field.description` + `field.allowed_values` + sample values 串成 content,embedding。

**Update trigger**:metadata 新 version → 重 embed 該 domain 的所有 field doc(舊版 deprecate)。

**Top-K policy**:default k=5,filter by `domain`。最低 score threshold 0.3(過低不注入)。

### 8.2 kpi_index

**Source**:`domain_metadata.kpi_definitions` + `upload_metadata_versions.kpi_definitions`。

**Build**:對每個 KPI,content = `kpi.name + kpi.formula + kpi.important_note`,embedding。

**Top-K**:k=3,filter by `domain`。

### 8.3 few_shot_index

**Source**:`task_traces`(status=completed + verified=True + confidence > 0.85)。

**Build**:per phase,content = `query + phase_output_excerpt`(plan / phase A code 前 200 字 / phase C 簡化版 option)。

**Update trigger**:nightly job 撈最新 N 天 traces。

**Top-K**:k=2,filter by `phase + domain + intent`。

### 8.4 anti_pattern_index

**Source**:`learning_instincts`(active=True + confidence > 0.85)。

**Build**:content = `instinct.context + instinct.recommendation`,embedding。

**Top-K**:k=3,filter by `phase_target`(plan/A/B/C)。

### 8.5 chart_recipe_index

**Source**:`embedded_prompts.py` 內 `_PHASE_C_INTENT_BLOCKS` + user-saved Saved Chart 的 chart_option。

**Build**:per intent,content = `intent + chart_option simplified template`。

**Top-K**:k=1(只取最相關 1 個)。

### 8.6 Embedding 模型

**M6.1 MVP**:`sentence-transformers/all-MiniLM-L6-v2`
- 384 dim
- 300MB,CPU 跑得快(~50ms/query)
- 中英文支援(雙語)
- 已被廣泛驗證

**升級候選(Phase 3)**:`BAAI/bge-m3` 或 `nomic-embed-text-v1.5`,效果好但模型大 1-2GB。

**embedding_version 管理**:doc 內存 `embedding_model + embedding_version`,模型升級時舊版 doc auto-deprecate + nightly re-embed。

---

## 9. Retrieval Orchestrator 規格

### 9.1 介面

```python
from retrieval_orchestrator import RetrievalOrchestrator

orch = RetrievalOrchestrator(
    schema_index=..., kpi_index=..., few_shot_index=...,
    anti_pattern_index=..., chart_recipe_index=...,
    embedding_pipeline=...,
)

rag_context = orch.retrieve_for_phase(
    phase="phase_0_plan",
    query="比較各 company 的 hc",
    domain="tflex",
    intent=None,
    rag_enabled=True,
)
# rag_context = {
#     "rag_schema": "...",        # top-5 schema chunks
#     "rag_kpi": "...",           # top-3 kpi chunks
#     "rag_few_shot": "...",      # top-2 few-shot examples
#     "rag_anti_pattern": "",     # phase 0 不用
#     "rag_chart_recipe": "",     # phase 0 不用
#     "rag_chunks_used": [...],   # 給 task_trace 記用
# }

# 注入 prompt template
filled_prompt = template.render(**rag_context, **other_vars)
```

### 9.2 Per-phase retrieval policy

| Phase | 該抽哪些 index |
|---|---|
| Phase 0 plan | schema, kpi, few_shot |
| Phase A pipeline / pandas | schema, anti_pattern, few_shot |
| Phase B preprocess | anti_pattern, few_shot |
| Phase C echarts | chart_recipe, anti_pattern |
| Phase D insight | kpi |

### 9.3 Re-rank 策略

預設:single-index score(distance)+ recency boost(per index 不同 decay):
- few_shot:30 天內 1.0,30-90 天 0.8,>90 天 0.5
- anti_pattern:no decay(instinct verified 後永久有效)
- schema:no decay(metadata 改才會 invalidate)

進階(Phase 3):cross-encoder re-rank 用 BGE-reranker。

### 9.4 並行 retrieval

5 個 index 同時跑(`asyncio.gather` 或 `concurrent.futures`),避免 serial 等待。Total retrieve latency p95 < 200ms。

### 9.5 Token budget per slot

每個 slot 有上限,過長 truncate:

| Slot | Max chars |
|---|---:|
| `rag_schema` | 1200(~300 token) |
| `rag_kpi` | 600 |
| `rag_few_shot` | 1500(~375 token) |
| `rag_anti_pattern` | 800 |
| `rag_chart_recipe` | 2000 |

Total RAG context cap: **~6K char ≈ 1500 token**(留給 hard-coded prompt 1500 token,合計 3K)。

---

## 10. Prompt 重構規格

### 10.1 新 prompt 結構

每個 phase 的 prompt template 拆 3 段:

```jinja2
{{ STATIC_HEADER }}             ← Hard-coded critical rules + format spec(不變)

### 動態 context(RAG)
{% if rag_schema %}**相關欄位**:
{{ rag_schema }}{% endif %}

{% if rag_kpi %}**相關 KPI**:
{{ rag_kpi }}{% endif %}

{% if rag_few_shot %}**過往成功案例**:
{{ rag_few_shot }}{% endif %}

{% if rag_anti_pattern %}**注意這些雷**:
{{ rag_anti_pattern }}{% endif %}

{{ STATIC_FOOTER }}             ← Hard-coded output rules / examples shell
```

### 10.2 Backward compat

`prompt_repository.PromptRepository.render(prompt_key, **vars)` 加 RAG-aware mode:
- 若 `vars` 含 `rag_<slot>` keys → 使用
- 若沒 → slot 渲染為空字串(等同 RAG-off,完整 prompt 走 fallback static content)

當 `GENBI_RAG_ENABLED=false` → orchestrator 不 invoke,prompt slot 全空。實質 prompt 退回 v0.15 行為(byte-equal validation)。

### 10.3 Migration plan

對每 phase prompt 漸進改:

| Step | Action |
|---|---|
| 1 | 把 prompt 內「always-on」段標為 `STATIC_HEADER` / `STATIC_FOOTER` |
| 2 | 把「per-query 才需要」段抽出來,evaluate 是否 RAG-friendly |
| 3 | RAG-friendly 段改成 `{{ rag_<slot> }}` placeholder |
| 4 | RAG-off mode:slot 渲染為「全 schema / 全 KPI」(等同 v0.15 行為)|
| 5 | RAG-on mode:slot 渲染為 top-K retrieve 結果 |

---

## 11. A/B Framework 規格

### 11.1 Traffic split

對每個 RAG index 獨立做 A/B:
- 預設 traffic split champion:challenger = 80:20
- query 進來,hash(`session_id + index_name`) % 100 → 0-79 走 champion,80-99 走 challenger
- task_trace 記 `prompt_variant: {index_name: 'champion' | 'challenger'}` per-index

### 11.2 Experiment lifecycle

```
[Create]    rag_promotion_service.create_experiment(
                index_name='few_shot_index',
                champion_version=2, challenger_version=3,
                traffic_split={'challenger': 0.2},
            )
   ↓
[Run]       experiment 跑 N 天(default 7)
   ↓
[Evaluate]  nightly job 對比 metrics:
              - challenger pass rate vs champion pass rate
              - challenger latency vs champion latency
              - challenger token usage vs champion token usage
              - statistical significance(chi-square / t-test)
   ↓
[Decide]    regression_gate per-index:
              ✅ Promote 條件:challenger pass rate ≥ champion + 統計顯著(p<0.05)+
                              latency 沒倒退 >10%
              ❌ Rollback 條件:challenger pass rate < champion 顯著(p<0.05)or
                              latency 倒退 >20%
              ⏸ Continue 條件:no significant diff yet → 延長 N 天
```

### 11.3 Manual override

Admin UI(`pages/09_rag_indices.py`)允許:
- 手動 promote(skip A/B)
- 手動 rollback(rollback 到任一歷史 version)
- 手動調 traffic split %
- 暫停 experiment

### 11.4 Per-index-type regression metric

不同 index update 該看不同 metric:

| Index | Primary metric | Secondary |
|---|---|---|
| schema_index | hallucinated column rate(LLM 寫了不存在的欄位)| LLM JSON parse rate |
| kpi_index | KPI formula error rate(LLM 用錯 KPI 公式)| - |
| few_shot_index | overall pass rate | latency |
| anti_pattern_index | retry rate(intent 是降 LLM retry 次數) | - |
| chart_recipe_index | Phase C exec error rate | Phase C fallback rate |

---

## 12. Prompt Iteration Loop

### 12.1 完整流程

```
[Day 0]   user query → task_trace 寫 status=completed/failed
   │
[Day 1]   nightly job:
          ├─ failure_filter → 撈最近失敗 trace(已有)
          ├─ observation_extractor → 抽 observation(已有)
          ├─ verifier → independent verify(已有)
          └─ instinct_consolidator → 升 instinct(已有)
   │
[Day 2]   candidate_generator(M6.5 擴充)→ 4 種 candidate type:
          ├─ A. prompt_template_patch(現有)
          ├─ B. schema_index_doc(新)
          ├─ C. few_shot_doc(新)
          ├─ D. anti_pattern_doc(新)
          └─ E. chart_recipe_doc(新)
   │
[Day 3]   regression_gate per-candidate-type 自動判可不可上 challenger:
          - A 走 baseline test_runner 比對
          - B/C/D/E 走 RAG A/B framework 跑 N 天 metric
   │
[Day 3-10]  experiment 跑(80:20 traffic split)
   │
[Day 11]  rag_promotion_service 自動評估:
          - 顯著好 → auto promote
          - 顯著差 → auto rollback
          - no diff → 延 7 天 or close
   │
[ongoing] pages/06_learning_review.py / 09_rag_indices.py
          人類隨時可介入(manual promote / rollback / 看 metric trend)
```

### 12.2 從「prompt patch only」演進的好處

| 維度 | v0.8 self-learning(現況) | v0.16 + RAG iteration |
|---|---|---|
| Iteration 粒度 | 整 prompt patch(影響全 query)| Per-doc(只影響相關 query)|
| 回滾成本 | 高(改 prompt 字串,全 query 受影響)| 低(deactivate 1 個 doc)|
| A/B 風險 | 高(beam-line 全變)| 低(20% traffic + per-index)|
| Iteration 速度 | 週級(human review)| 日級(auto promote 通過 gate)|
| 學習能力 | rule-based learning(改規則)| pattern-based learning(看例子)|

### 12.3 從 task_trace 自動 promote 範例

```
Day 0: query "畫 hc 分佈直方圖" failed phase C BidiComponent error
  ↓
Day 1: observation_extractor 抽出:
       - context: histogram intent, ECharts no histogram type
       - recommendation: 用 bar + markLine
  ↓
Day 1: verifier confirm(LLM-based independent verification),verified=True
  ↓
Day 2: candidate_generator 產 2 個 candidate:
       (a) prompt_template_patch: 在 Phase C universal header 加「ECharts no histogram」rule
       (b) anti_pattern_doc: anti_pattern_index 加新 doc
  ↓
Day 3: regression_gate evaluate:
       (a) 走 test_runner 整 baseline,需要時間 + 影響全 query
       (b) 進 anti_pattern_index challenger,影響 20% traffic histogram query
       → (b) 風險低、回滾易,優先 promote
  ↓
Day 3-10: A/B run,histogram query 中 challenger 的 retry rate 從 35% 降到 12%
  ↓
Day 11: auto promote challenger → champion
```

---

## 13. 安全與治理規格

### 13.1 RAG 注入內容過濾

- few_shot_index 只允許 `verified=True + status=completed` 的 trace
- anti_pattern_index 只允許 `confidence > 0.85 + is_active=True` 的 instinct
- schema_index 只允許 `confirmation_status='confirmed'` 的 metadata version

### 13.2 PII 保護

- task_trace 寫入時不該含 PII(M4b 已有 PII detection,寫 trace 前 mask sensitive cells)
- few_shot_index doc embedding 前再過一次 PII filter(double check)

### 13.3 Index 隔離

- Per-domain index filter(schema_index 跟 kpi_index 必 filter by domain)
- few_shot / anti_pattern 跨 domain 但每筆 doc 帶 `source_domain` field,供 user audit

### 13.4 Retrieval failure fallback

若 vector DB 連不上 / retrieve timeout > 500ms:
- log warning
- 走 RAG-off path(slot 為空,prompt 用 static-only content)
- 不阻塞 user query

---

## 14. Observability / Debug 規格

### 14.1 task_trace 加欄位

```python
{
    ...既有欄位...,
    "rag_chunks_used": [
        {"index_name": "schema_index", "doc_id": "...", "score": 0.82},
        ...
    ],
    "rag_total_latency_ms": 142,
    "prompt_variant": {
        "schema_index": "champion",
        "few_shot_index": "challenger",
        ...
    },
}
```

### 14.2 Debug Panel(Streamlit)

Upload Workspace Section 12 Debug Panel 加新 tab:**🧠 RAG context**
- 顯示本次 query 從每個 index 抽到的 chunks + score
- 顯示 prompt 最終 size(static vs RAG injected)
- 顯示 prompt_variant per-index

### 14.3 Admin UI · `pages/09_rag_indices.py`

- 列 5 個 index 的 champion / challenger version
- 查 doc(可看 content / embedding stats / 來源 trace_id)
- 手動 promote / rollback
- A/B experiment 狀態 + metric trend

### 14.4 Metrics dashboard

- per-index retrieve latency p50 / p95 / p99
- per-index hit rate(retrieve 結果有 ≥1 doc score > threshold)
- per-phase pass rate trend(champion vs challenger)
- Token usage trend(per phase)

---

## 15. 測試規格

### 15.1 Unit tests

| Module | Test count |
|---|---:|
| `rag_index_repository` | 20 |
| `embedding_pipeline` | 10 |
| `retrieval_orchestrator` | 25(per-phase policy / re-rank / dedupe / token budget) |
| `ab_framework` | 15(traffic split / hash distribution / variant tracking) |
| `rag_promotion_service` | 12(promote / rollback / regression gate) |

### 15.2 Integration tests

- end-to-end query 走 RAG-on path,trace 寫對
- RAG-off path byte-equal v0.15 行為(凍結驗證)
- 5 個 index 並行 retrieve + slot 注入完整 prompt

### 15.3 Acceptance(對齊 既有 acceptance suite 風格)

- `test_acceptance_rag_off_byte_equal`:RAG_ENABLED=false 跑 baseline 跟 v0.15 完全一樣
- `test_acceptance_rag_on_token_saved`:RAG-on prompt token < RAG-off 60%
- `test_acceptance_ab_split_distribution`:大量 query 後 traffic 比例落在 ±2% 容差內
- `test_acceptance_retrieval_failure_fallback`:vector DB 斷線時自動走 RAG-off,不 crash

### 15.4 Golden RAG test set

新增 `tests/golden_rag/`:
- `query_to_expected_schema_chunks.json` — 寫 50 個 (query, expected schema field names) pair
- `query_to_expected_few_shot.json` — 寫 30 個 (query, expected trace_id) pair
- Nightly run RAG retrieval 對比,retrieval accuracy 低於 80% 就 alert

---

## 16. 開發里程碑

| Milestone | 時程 | 主要交付 |
|---|---|---|
| **M6.1** RAG infra | 1 週 | rag_index_repository / embedding_pipeline / Chroma backend / build script |
| **M6.2** Phase 0/A/D RAG | 1 週 | schema + kpi index 上,Phase 0/A/D prompt RAG-aware |
| **M6.3** Phase B/C RAG | 1 週 | few_shot + anti_pattern + chart_recipe index,Phase B/C wire |
| **M6.4** A/B framework | 2 週 | traffic split + experiment + champion/challenger + auto promote |
| **M6.5** Self-learning extension | 1 週 | 4 種新 candidate type + per-type regression gate + UI |
| **M6.6** Production hardening | 1 週 | Qdrant migration / multi-tenancy / metrics dashboard |

**Total**:7 週 / 1 人。**M6.1+M6.2 就解鎖 token 省 40% 主效益**,即使其他 phase 延後也有 immediate ROI。

---

## 17. 開發拆票建議

### Epic R · RAG Infra(M6.1)

- R1:`rag_index_repository.py`(Chroma backend + 統一 CRUD)
- R2:`embedding_pipeline.py`(local sentence-transformer + cache)
- R3:`scripts/build_rag_indices.py`(初次 build + nightly rebuild)
- R4:`rag_index_versions` collection schema
- R5:dev-only `streamlit run pages/09_rag_indices.py` 瀏覽 UI

### Epic S · Schema/KPI RAG(M6.2)

- S1:Phase 0 prompt template 加 `{{ rag_schema }}` / `{{ rag_kpi }}` slot
- S2:`retrieval_orchestrator.retrieve_for_phase` schema/kpi path
- S3:`build_domain_knowledge_rag` — RAG-aware variant
- S4:`GENBI_RAG_ENABLED` env flag + byte-equal off validation

### Epic F · Few-shot RAG(M6.3)

- F1:`build_few_shot_index_from_traces`(從 task_traces 抽 verified=True)
- F2:Phase A/B prompt `{{ rag_few_shot }}` slot
- F3:per-phase top-K policy
- F4:PII filter on few-shot doc

### Epic AP · Anti-pattern RAG(M6.3)

- AP1:`build_anti_pattern_index_from_instincts`
- AP2:Phase B/C prompt `{{ rag_anti_pattern }}` slot
- AP3:trigger-based注入(retrieve score > threshold 才注入)
- AP4:anti_pattern_doc candidate type

### Epic C · Chart-recipe RAG(M6.3)

- C1:`build_chart_recipe_index_from_blocks`
- C2:Phase C prompt `{{ rag_chart_recipe }}` slot
- C3:per-intent retrieval policy

### Epic AB · A/B framework(M6.4)

- AB1:`task_trace.prompt_variant` 欄位 + 寫入
- AB2:`ab_framework.split_traffic`
- AB3:`ab_experiments` collection
- AB4:`nightly_evaluate_experiments` job
- AB5:`rag_promotion_service.auto_promote / rollback`
- AB6:per-index regression metric

### Epic L · Self-learning extension(M6.5)

- L1:`observation_extractor` 加 RAG-target classification
- L2:`candidate_generator` 產 4 種新 candidate type
- L3:`regression_gate` per-type metric
- L4:`pages/06_learning_review.py` 加 RAG candidate tab

### Epic D · Debug + Admin(M6.5)

- D1:`pages/09_rag_indices.py`(browse / promote / rollback)
- D2:Debug Panel 加 RAG context tab
- D3:Metrics dashboard(retrieve latency / hit rate / pass rate trend)

### Epic P · Production hardening(M6.6)

- P1:Qdrant backend(swap from Chroma)
- P2:Multi-tenant index isolation
- P3:Embedding model upgrade path
- P4:Disaster recovery(index backup / restore)

---

## 18. 風險與對策

| 風險 | 影響 | 對策 |
|---|---|---|
| Retrieve 漏掉 critical schema → KeyError | 高 | (a) critical schema 仍 hard-code(`q_columns`/`source_columns`)(b) schema retrieve 必加 query 提到的欄位名 substring 強制 inject |
| Few-shot 給錯案例 → LLM 學壞 | 中 | (a) 只用 verified=True trace (b) score threshold 0.5+ (c) anti-pattern index 同時注入「不要這樣」hint |
| 向量 DB 額外運維 | 中 | M6.1 用 embedded Chroma(無 server)— production 升 Qdrant |
| Embedding 模型更新 → index 全失效 | 中 | embedding_version per-doc;不一致 auto-skip + nightly re-embed |
| RAG 加 latency 抵消 token 節省 | 中 | local embedding < 50ms + parallel retrieve < 200ms;total wall time 仍降 |
| A/B challenger 出包 | 高 | (a) traffic 上限 20% (b) regression gate 自動 rollback (c) per-index 隔離,不會全炸 |
| Schema-driven baseline 被打 | 致命 | (a) `GENBI_RAG_ENABLED=false` default (b) test_runner 跑 false 路徑 byte-equal 驗證 (c) PR check on baseline regression |
| Index 過時(instinct outdated)| 中 | (a) doc 帶 `created_at` (b) confidence decay(spec §16 已有)(c) 定期 audit |
| RAG-on 後 LLM behavior 變得不穩 | 中 | A/B framework 直接抓到 — 不穩的 challenger 不會 promote |
| User 看到 trace 含 PII | 高 | (a) trace 寫入前 PII mask(M4b 已有)(b) few_shot embed 前再過 PII filter |

---

## 19. Definition of Done

本 spec 視為實作完成需同時滿足:

1. ✅ M6.1-M6.5 全部 milestone 收齊
2. ✅ Phase 0/A/B/C/D prompt 全部 RAG-aware(slot 機制)
3. ✅ 5 個向量 index 各自 promote / rollback workflow 跑通
4. ✅ A/B framework 端到端跑通,且至少 1 個 index 完成 1 輪 promote
5. ✅ Token 省 ≥ 40%(實測 5 個典型 query)
6. ✅ Wall time 省 ≥ 30%(同上)
7. ✅ Self-learning loop 能自動產 4 種新 candidate type
8. ✅ test_runner.py 跑 RAG-off 仍 byte-equal v0.15(凍結條款)
9. ✅ RAG-on 跑 baseline 通過 regression gate(no pass → non-pass regression)
10. ✅ AI_CONTEXT.md 加新 section 22.10(後續 LLM agent 能接手)

---

## 20. 建議優先開發順序

1. M6.1 RAG infra(沒這個其他都無法跑)
2. M6.2 Schema/KPI RAG(40% token 省 immediate)
3. M6.4 A/B framework(沒這個 iteration 無法自動化)
4. M6.3 Few-shot / Anti-pattern / Chart-recipe RAG(完整 RAG 覆蓋)
5. M6.5 Self-learning extension(自動 iteration)
6. M6.6 Production hardening(scale 時再做)

真正的產品風險不是 retrieval algorithm,而是「**RAG-on 後 LLM 行為是否仍可預測**」。因此 A/B framework 該緊跟 M6.2 後做,**不要等到全 RAG 都 wire 才上 A/B**。

---

## 21. 附錄:範例 RAG-aware prompt 結構

### Phase 0 plan(RAG-on)

```
你是專業的 AI 商業智慧助理。請以下方 Domain Knowledge 為依據規劃分析。

### Domain Knowledge(動態 retrieval)
{{ rag_schema }}            ← top-5 schema fields,僅 query 相關

### KPI 公式(動態 retrieval)
{{ rag_kpi }}               ← top-3 KPI,僅 query 相關

### 過往成功案例(動態 retrieval)
{{ rag_few_shot }}          ← top-2 traces,query 類似

### 拒絕協定(hard-coded)
若使用者問題觸犯資料限制,第一個字元就輸出 [REFUSE]。

### 任務說明(hard-coded)
請拆解為 A. 資料獲取 / B. 資料處理 / C. 視覺化 三段。
```

### Phase C echarts(RAG-on)

```
你是精通 Apache ECharts 5 的資深前端工程師,負責【C. 視覺化繪圖】。
{{ cols_info }}

### 動態 chart recipe(動態 retrieval)
{{ rag_chart_recipe }}      ← top-1 chart recipe 範例,跟 query intent 相關

### 過去踩過這些雷(動態 retrieval,觸發式)
{{ rag_anti_pattern }}      ← top-3 anti-pattern,score > threshold 才注入

### Critical FATAL rules(hard-coded)
1. 變數產出:option = {...} dict literal
2. ECharts 沒有 histogram type — 用 bar + markLine
3. q_columns 鎖死:只能用 Q.columns 內欄位
...
```

---

## 22. 附錄:範例 RAG schema doc

### schema_index doc 範例

```json
{
    "doc_id": "schema_tflex_applications_review_status",
    "content": "review_status (string, semantic_role=categorical_status): 申請審核狀態。allowed values: Y, N, R, X (Y=已通過, N=未通過, R=已退單, X=未處理)。常與 review_result, review_mechanism 一起用於合規分析。",
    "embedding": [0.0123, -0.0456, ...],
    "embedding_model": "all-MiniLM-L6-v2",
    "domain": "tflex",
    "table": "tflex_applications",
    "field_name": "review_status",
    "semantic_role": "categorical_status",
    "metadata_version_source": 12
}
```

### few_shot_index doc 範例

```json
{
    "doc_id": "fewshot_trace_abc123_plan",
    "content": "Q: 比較各 company 的 hc 排名\nPlan:\nA. tflex_company_hc 取所有公司 → company_code, hc\nB. groupby company_code, hc.first() → Q[company_code, hc]\nC. horizontal bar, x=hc, y=company_code 由高到低",
    "embedding": [0.0789, ...],
    "source_trace_id": "abc-123",
    "source_query": "比較各 company 的 hc 排名",
    "phase": "plan",
    "domain": "tflex",
    "intent": "bar_horizontal",
    "success": true,
    "confidence": 0.92,
    "verification_status": "verified"
}
```

---

## 23. 結論

本 spec 提出把 GenBI 的 prompt 從「靜態 monolithic 6-12K」走向「**hard-coded critical + RAG-injected dynamic 3K**」。3 個閉環:

1. **瘦身**:從 prompt 內抽出可動態的 chunks,**token 省 60%、latency 省 40%**
2. **動態注入**:5 個 specialized index(schema / KPI / few-shot / anti-pattern / chart-recipe)per-query retrieve
3. **自動迭代**:擴展既有 self-learning loop 從「只能改 prompt 字串」→「可以加 RAG doc」+ A/B framework + 自動 promote

關鍵設計選擇:
- **不取代既有 prompt repo / self-learning loop**,只擴展
- **`GENBI_RAG_ENABLED=false` default** 保證 baseline byte-equal
- **A/B framework 緊跟 M6.2** 而非 M6.5,讓 RAG 從第一天就有安全網
- **Per-index 獨立 promote / rollback**,iteration 粒度細、風險小

完成後 GenBI 將具備:
1. **正式資料集**(schema-driven)+ **上傳資料集**(upload-driven)+ **動態 RAG prompt**(全 phase)三條完整路徑
2. **prompt 維護成本顯著降低**(失敗 → trace → instinct → RAG doc → A/B 自動完成,不必人類 patch prompt)
3. **per-query latency 從 25s → 14s**,production ROI 立刻顯現
