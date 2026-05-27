# Sprint 4 · bge-m3 HTTP backend experiment — Result & Rollback

**Status:** Code shipped(opt-in via `GENBI_EMBEDDING_BACKEND=http`). Production default rolls back to **all-MiniLM-L6-v2 (Sprint 2 champion)**.

**Date:** 2026-05-26

---

## TL;DR

Switched embedder from local sentence-transformers (all-MiniLM-L6-v2, 384d) to HTTP-served bge-m3 (1024d via Ollama). Two A/B runs:

| Run | Embedder | min_score | Pass | $/success |
|---|---|---:|---:|---:|
| RAG OFF baseline | — | — | 22/26 (85%) | $0.0050 |
| **Sprint 2 champion ⭐** | MiniLM 384d | 0.20 | **25/26 (96%)** | **$0.0045** |
| bge-m3 attempt 1 | bge-m3 1024d | 0.20 | 17/26 (65%) | $0.0063 |
| bge-m3 attempt 2 (tuned) | bge-m3 1024d | 0.50 | 22/26 (85%) | $0.0051 |

bge-m3 at any tested threshold either flooded the prompt (0.20) or scored at baseline (0.50). Best case: RAG-neutral. **Did not clear spec §11.2 promotion bar (≥+8%).**

**Action:** Keep MiniLM as production default. bge-m3 HTTP backend code stays (working, tested), available as opt-in for future re-attempts.

## Root cause diagnosis(via `scripts/inspect_rag_retrieval.py`)

bge-m3 has **cosine inflation** on small heterogeneous Chinese-domain indices:

| Query | schema top | kpi top | few_shot top | chart_recipe top | anti_pattern top |
|---|---:|---:|---:|---:|---:|
| Q1 tflex Chinese query | 0.51 | 0.57 | **0.70** | 0.53 | 0.46 |
| Q2 tflex Chinese query | 0.52 | 0.58 | 0.53 | 0.48 | 0.53 |
| **Q3 English off-domain garbage** | **0.43** | **0.44** | **0.50** | **0.48** | **0.46** |

On-domain top - off-domain top ≈ **0.05-0.10** (1.2× ratio).
For comparison, all-MiniLM on-domain top - off-domain top ≈ **0.30** (4× ratio).

**Why bge-m3 compresses cosines:**

1. **Multilingual training** (110 languages) — encoder finds "vague semantic relationships" between unrelated content
2. **Small index size** (10-30 docs/index) — sparse neighborhood pushes everything toward "moderately similar"
3. **Mixed-language content** (Chinese + English mixed in docs) — bge-m3's strong cross-lingual matching means even English garbage queries hit Chinese docs

Threshold sweep on tflex query:

```
min_score | schema | kpi | few_shot
----------|--------|-----|--------
0.20-0.40 |   5    |  5  |    5     ← flood, indistinguishable from off-domain
0.50      |   1    |  5  |    5     ← schema cliff at 0.50, kpi/few_shot still saturated
```

There's no clean threshold that separates Q1/Q2 from Q3 across all 5 indices. Per-index thresholds might help marginally; the proper fix is cross-encoder re-rank (spec §9.3 Phase 3).

## Why Sprint 2 MiniLM worked but bge-m3 didn't

It's counter-intuitive — MiniLM is a smaller, English-primary model than bge-m3.
But the index is the asymmetric factor:

- **25 schema docs / 17 kpi docs** is tiny.
- For tiny indices, **sharper discrimination > raw semantic power**.
- MiniLM's English bias HELPS here — Chinese tflex queries vs Chinese docs hit moderate similarity (0.35-0.40); off-domain English queries vs Chinese docs hit very low similarity (0.05-0.10). The gap matters.
- bge-m3's multilingual strength HURTS — it finds "weak but consistent" similarity for everything, compressing the distribution.

## Conditions to re-evaluate bge-m3

Open a follow-up sprint to revisit bge-m3 when **at least one** is true:

1. **Index size scales ≥10×** — 250+ schema docs and 170+ kpi docs. More docs → sparser similarity neighborhood → bge-m3's discrimination should improve naturally.
2. **Cross-encoder re-rank implemented** (spec §9.3 Phase 3) — dense retrieval + cross-encoder re-rank should compensate for cosine inflation. `BAAI/bge-reranker-base` or `BAAI/bge-reranker-large` candidates.
3. **Per-index threshold tuning** + MMR (Maximum Marginal Relevance) diversification. Substantial work but principled.

Until then, MiniLM Sprint 2 is the production answer.

## Code state(opt-in bge-m3 preserved)

| File | State |
|---|---|
| `embedding_pipeline.py` | `make_http_embedder()` (working) + `get_embedding_pipeline()` env routing |
| `retrieval_orchestrator.py` | `min_score=0.20` (MiniLM-tuned, default)|
| `DEPLOYMENT.md` | §6.1 documents both backends; bge-m3 section now annotated with this caveat |
| Production `.env` | `GENBI_EMBEDDING_BACKEND=local` (or unset — same effect) |
| Tests | 489 passing — HTTP embedder unit tests cover bge-m3 path |

To re-attempt bge-m3 manually:

```bash
# .env
GENBI_EMBEDDING_BACKEND=http
GENBI_EMBEDDING_API_URL=http://localhost:11434/v1/embeddings
GENBI_EMBEDDING_MODEL=bge-m3

# 在 retrieval_orchestrator.py 改回 min_score=0.50(per slot)
# 重建 indices(1024-dim)
rm -rf rag_indices
python scripts/build_rag_indices.py --full-rebuild --domain tflex
```

## What this taught us

Per CLAUDE.md rule 9 (test intent, not behavior) + rule 12 (失敗要大聲):

- **Embedding model choice is NOT independent of corpus characteristics.** Bigger model ≠ better for tiny indices.
- **Discrimination gap > absolute score.** A model where Q1=0.4 / off-domain=0.05 wins over Q1=0.7 / off-domain=0.5.
- **Test the canary.** The off-domain English query was the smoking-gun diagnostic. Without it we'd be tuning min_score forever, never seeing the underlying issue.
- **Threshold tuning has a ceiling.** When cosines are inflation-compressed, no scalar threshold can recover discrimination.

## Rollback steps for the user (your Mac)

```bash
cd /Users/kururu/Documents/Claude/Projects/GenBI

# 1. .env:remove or comment out bge-m3 HTTP lines, leaving:
# GENBI_RAG_ENABLED=true
# GENBI_RAG_PHASE_BC=false
# (no GENBI_EMBEDDING_BACKEND line = defaults to local)

# 2. Rebuild indices back to MiniLM(384-dim)
rm -rf rag_indices
python scripts/build_rag_indices.py --full-rebuild --domain tflex

# 3. Verify inspector shows MiniLM and reasonable scores
python scripts/inspect_rag_retrieval.py --query "各公司今年申請審核狀態統計" --skip-sweep | head -25
# Expected header: Embedder: sentence-transformers/all-MiniLM-L6-v2(dim=384)

# 4. Confirm Sprint 2 reproduces(target 25/26)
mkdir -p runs/
python test_runner.py --domain tflex --rag-on 2>&1 | tee runs/rag_on_minilm_restore_$(date +%Y%m%d_%H%M).log
```

Target: re-land at 25/26 (Sprint 2 champion). If you do, ship as v0.16.1 (the bge-m3 work + this rollback + the dotenv autoload fix).
