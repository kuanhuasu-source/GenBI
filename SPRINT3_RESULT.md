# Sprint 3 / M6.3 · Result & Decision

**Status:** Sprint 2 (Phase 0/A/D RAG) remains champion. Sprint 3 (Phase B/C wire) infra shipped but gated off by default.

**Date:** 2026-05-25

---

## TL;DR

Sprint 2 → Sprint 3 progression hoped to extend the +11.5pp pass-rate lift to Phase B/C. Two independent A/B runs (v3 and v4) consistently landed at **23/26 (88%)**, **−2 cases vs Sprint 2 champion (25/26, 96%)**. After targeted hot-fix (anti_pattern phase filter) failed to recover, we accepted Sprint 3 as net negative and gated Phase B/C RAG behind a separate env flag.

## A/B numbers

| Run | Mode | Pass | Calls | Per-call prompt | Total tokens | Cost / success |
|---|---|---:|---:|---:|---:|---:|
| baseline | OFF | 22/26 (85%) | 138 | 3,932 | 589,915 | $0.0050 |
| **Sprint 2 v2** | **ON (P0/A/D)** | **25/26 (96%)** | 140 | 3,940 | 601,299 | **$0.0045** ← champion |
| Sprint 3 v3 | ON + Phase B/C wire (raw) | 23/26 (88%) | 134 | 4,138 | 601,110 | $0.0048 |
| Sprint 3 v4 | ON + phase-filter fix | 23/26 (88%) | 135 | 4,150 | 607,491 | $0.0049 |

Decision rule (sprint plan §4.3): challenger must beat champion by ≥+1 case at p<0.10 to promote. Sprint 3 went the *opposite direction*. Two runs at -2 cases each — unlikely pure noise.

## Root-cause diagnosis (via `scripts/inspect_rag_retrieval.py`)

Three issues identified; one fixed surgically, two left as known-issues to address later.

### Fixed in v4: anti_pattern cross-phase leak (M6.3 hot-fix)

**Symptom:** Phase C's prompt was getting Phase B-specific anti-patterns (e.g., "Q contains TOTAL row" — irrelevant to ECharts code generation).

**Root cause:** `SLOT_ANTI_PATTERN.filter_keys = ()` — no phase filter on retrieval.

**Fix:** Added `applies_to_phase` to filter_keys. LLMService Phase A/B/C renderers now pass `extra_filters={"applies_to_phase": "phase_X"}`. Verified by `test_anti_pattern_phase_filter`.

This fix is correct (inspector confirms post-fix Phase B/C get phase-appropriate anti-patterns), but **didn't recover the 2 lost cases.**

### ✅ ADDRESSED in Sprint 4 / M6.5: few_shot_index semantic mismatch

**Original symptom:** Query "員工 H/C 圓餅圖" (pie chart) gets few-shot examples about "stacked bar" and "heatmap" — wrong chart type.

**Root cause:** `few_shot_index` was seeded from prior test_runs (mostly RAG-OFF era).

**Fix (Sprint 4 / M6.5):** Added `rag_on_only=True` default in `build_few_shot_index`.
Mongo query now includes `{"rag_enabled": True}` — only RAG-on era successful
runs feed the index. Bootstrap mode (no RAG-on runs yet)用
`--include-rag-off-runs` flag。對齊 spec §9.3 recency decay 原則(只信
「同 prompt regime」的成功)。

**To verify Phase B/C re-enable readiness:** rebuild indices after accumulating
≥5 RAG-on test_runs,then `python scripts/inspect_rag_retrieval.py` 看 few_shot
是否語意對齊。

### Still open: anti_pattern dilution

**Symptom:** Even with correct phase-filtered anti_pattern, Phase B prompt gets warnings ("Q has TOTAL row", "ratio status partial cover") that don't apply to most queries.

**Root cause:** Phase B/C prompts already have *static* validator-derived rules embedded in them. Adding RAG-retrieved versions of the same rules creates redundancy — the LLM's attention is split.

**Why deferred:** Possible fix is to *deduplicate* RAG anti_pattern content against the static prompt block. Requires content diff logic. Not trivial.

## What shipped in Sprint 3 (preserved, off by default)

| Component | Status | Notes |
|---|---|---|
| `anti_pattern_seed.py` | ✅ shipped | 17 hand-curated anti-patterns from 3 validators |
| `build_anti_pattern_index` | ✅ shipped | seeds + future learning_instincts integration |
| `build_few_shot_index` | ✅ shipped | reads from test_runs.case_results pass cases |
| `build_chart_recipe_index` | ✅ shipped | reads from domain_metadata.charting_guidance |
| Phase B/C Jinja `{%- if rag_X %}` guards | ✅ shipped | empty strings → byte-equal v0.15 |
| `compose_phase_b/c_prompt_modular` RAG kwargs | ✅ shipped | accepts rag_*, defaults "" |
| **`GENBI_RAG_PHASE_BC` env flag (default false)** | ✅ shipped | the gate |
| Tests | ✅ 470 passing | +57 from Sprint 3 work |

**Net: Sprint 3 infra is production-ready, just not the default.**

## Production config recommendation

```bash
# .env(production / default Mac dev)
GENBI_RAG_ENABLED=true          # Phase 0/A/D RAG — the +11.5pp lift
GENBI_RAG_PHASE_BC=false        # Phase B/C — opt-in only, expect regression
GENBI_EMBEDDING_MODEL=...       # see SPRINT2_RUN_GUIDE.md §7 for air-gap setup
```

To re-test Phase B/C RAG in the future (after content improvements):
```bash
GENBI_RAG_PHASE_BC=true python test_runner.py --domain tflex --rag-on
```

## Conditions to re-enable Phase B/C RAG

Per CLAUDE.md Rule 9 (test intent, not behavior), don't re-enable until:

1. ✅ **few_shot_index curated from RAG-ON era runs** — done in Sprint 4 / M6.5 (`rag_on_only=True` default)
2. ⏳ **chart_recipe_index expanded** — tflex needs pie, heatmap, scatter recipes (currently only bar/stacked). Metadata-side work, no code change needed.
3. ⏳ **anti_pattern dedup** — RAG-retrieved rules don't overlap with static prompt block. Concrete criterion: skip RAG anti_pattern if its `id` is referenced in static prompt text.
4. **A/B run shows ≥+1 case** vs Sprint 2 champion at p<0.10 — clear champion-takedown criteria

After 5+ RAG-on test runs accumulate, the M6.5 fix should naturally improve few_shot quality. Re-test Phase B/C with `GENBI_RAG_PHASE_BC=true` then.

## Sprint plan position

- ✅ **Sprint 1** (M6.1 + M6.2) — RAG infra + Phase 0/A/D wire, byte-equal RAG-off
- ✅ **Sprint 2** (M6.4) — A/B framework + tflex schema/KPI indices, +11.5pp lift
- ⚠️ **Sprint 3** (M6.3) — Phase B/C wire shipped but disabled; lessons captured
- ⏭️ **Sprint 4** (M6.5 + M6.6) — self-learning loop + on-premise air-gap install (next)

The Sprint 4 self-learning loop will naturally address the #1 still-open issue (few_shot curation) by sourcing few-shot from validated production runs.
