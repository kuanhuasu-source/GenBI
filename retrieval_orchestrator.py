"""
retrieval_orchestrator.py — v0.16.0+ (M6.1 Sprint 1 Day 5)

Per-phase RAG retrieval orchestrator。對齊 spec §9 (GENBI_RAG_PROMPT_DESIGN.md)。

# 職責

1. 收 RAGIndexRepository(5 個 logical index 共用)+ EmbeddingPipeline
2. 根據 phase 走 per-phase policy(spec §9.2):決定該抽哪些 index
3. 對每個 slot 跑 search → truncate per token budget(spec §9.5)
4. Return `dict[slot_name -> str]`,給 prompt template 直接渲染

# 用法

```python
from retrieval_orchestrator import RetrievalOrchestrator
from rag_index_repository import RAGIndexRepository, make_inmemory_factory
from embedding_pipeline import get_embedding_pipeline

repo = RAGIndexRepository(backend_factory=make_inmemory_factory())
ep = get_embedding_pipeline()
orch = RetrievalOrchestrator(rag_repo=repo, embedding_pipeline=ep)

# Phase 0 plan:抽 schema + kpi + few_shot
slots = orch.retrieve_for_phase(
    phase="phase_0_plan",
    query="顯示各公司今年申請審核狀態統計",
    domain="tflex",
)
# slots == {"rag_schema": "...", "rag_kpi": "...", "rag_few_shot": "..."}

# RAG disabled → 全空
slots = orch.retrieve_for_phase(phase="phase_0_plan", query="…", rag_enabled=False)
# slots == {}
```

# Per-phase policy(spec §9.2)

| Phase             | Indices                              |
|-------------------|--------------------------------------|
| phase_0_plan      | schema, kpi, few_shot                |
| phase_a_pipeline  | schema, anti_pattern, few_shot       |
| phase_b_preprocess| anti_pattern, few_shot               |
| phase_c_chart     | chart_recipe, anti_pattern           |
| phase_d_insight   | kpi                                  |

# Token budget per slot(spec §9.5)

| Slot              | Max chars |
|-------------------|----------:|
| rag_schema        | 1200      |
| rag_kpi           |  600      |
| rag_few_shot      | 1500      |
| rag_anti_pattern  |  800      |
| rag_chart_recipe  | 2000      |

Truncate 是「整 doc 累加直到超 budget」,不切單 doc 中間。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ============================================================
# Slot constants(對齊 spec §9.5 + §10.1)
# ============================================================
SLOT_SCHEMA = "rag_schema"
SLOT_KPI = "rag_kpi"
SLOT_FEW_SHOT = "rag_few_shot"
SLOT_ANTI_PATTERN = "rag_anti_pattern"
SLOT_CHART_RECIPE = "rag_chart_recipe"

KNOWN_SLOTS = (
    SLOT_SCHEMA, SLOT_KPI, SLOT_FEW_SHOT,
    SLOT_ANTI_PATTERN, SLOT_CHART_RECIPE,
)


# ============================================================
# Per-slot config
# ============================================================
@dataclass(frozen=True)
class SlotConfig:
    """單 slot 的 retrieve config。"""
    index_name: str
    top_k: int = 5
    max_chars: int = 1200
    min_score: float = 0.3              # spec §6:過低不注入
    # filter keys:從 caller context 取哪些 key 過濾(eg ["domain"])
    filter_keys: tuple[str, ...] = ()


# ============================================================
# Default slot configs(spec §9.5)
# v0.16.0+ M6.4 tuning:min_score=0.20 對齊 all-MiniLM-L6-v2(Sprint 2 champion)
#
# ⚠️ Threshold 跟 embedding model 強耦合:
#   - **all-MiniLM-L6-v2(384d,production champion)**:min_score=0.20。
#     Top cosines 落在 0.35-0.50。0.20 留 2-3 docs/slot 通過(剛好填 prompt budget)。
#   - **bge-m3(1024d,opt-in HTTP backend)**:建議 min_score=0.50。
#     bge-m3 cosine 分布壓縮(off-domain 0.43 / on-domain 0.51-0.70),
#     用 0.20 會 flood,用 0.50 又只達 RAG-neutral(0pp lift vs OFF baseline)。
#     bge-m3 的「正解」是加 cross-encoder re-rank(spec §9.3 Phase 3),這版未做。
# 換 embedder 時應同步調 min_score — 若有 per-backend config 需求未來再加。
# ============================================================
DEFAULT_SLOT_CONFIGS: dict[str, SlotConfig] = {
    SLOT_SCHEMA: SlotConfig(
        index_name="schema_index", top_k=5, max_chars=1200,
        min_score=0.20, filter_keys=("domain",),
    ),
    SLOT_KPI: SlotConfig(
        index_name="kpi_index", top_k=3, max_chars=600,
        min_score=0.20, filter_keys=("domain",),
    ),
    SLOT_FEW_SHOT: SlotConfig(
        index_name="few_shot_index", top_k=3, max_chars=1500,
        min_score=0.20, filter_keys=("domain",),
    ),
    SLOT_ANTI_PATTERN: SlotConfig(
        index_name="anti_pattern_index", top_k=3, max_chars=800,
        min_score=0.20,
        # v0.16.0+ M6.3 fix:依 applies_to_phase 過濾,Phase A/B/C 各自取自己的
        # 跨 domain 共用(沒 domain filter),但跨 phase 不共用(Phase B 不該被
        # Phase A 或 Phase C 的 anti-pattern 帶風向)。
        filter_keys=("applies_to_phase",),
    ),
    SLOT_CHART_RECIPE: SlotConfig(
        index_name="chart_recipe_index", top_k=3, max_chars=2000,
        min_score=0.20, filter_keys=("intent",),
    ),
}


# ============================================================
# Per-phase policy(spec §9.2)
# ============================================================
DEFAULT_PHASE_POLICY: dict[str, tuple[str, ...]] = {
    "phase_0_plan": (SLOT_SCHEMA, SLOT_KPI, SLOT_FEW_SHOT),
    "phase_a_pipeline": (SLOT_SCHEMA, SLOT_ANTI_PATTERN, SLOT_FEW_SHOT),
    "phase_b_preprocess": (SLOT_ANTI_PATTERN, SLOT_FEW_SHOT),
    "phase_c_chart": (SLOT_CHART_RECIPE, SLOT_ANTI_PATTERN),
    "phase_d_insight": (SLOT_KPI,),
}


# ============================================================
# Orchestrator
# ============================================================
class RetrievalOrchestrator:
    """Per-phase RAG retriever。"""

    def __init__(
        self,
        rag_repo,
        embedding_pipeline,
        slot_configs: dict[str, SlotConfig] | None = None,
        phase_policy: dict[str, tuple[str, ...]] | None = None,
    ):
        if rag_repo is None:
            raise ValueError("rag_repo required")
        if embedding_pipeline is None:
            raise ValueError("embedding_pipeline required")
        self.repo = rag_repo
        self.ep = embedding_pipeline
        self.slot_configs = slot_configs or DEFAULT_SLOT_CONFIGS
        self.phase_policy = phase_policy or DEFAULT_PHASE_POLICY

    # ============================================================
    # Main entry
    # ============================================================
    def retrieve_for_phase(
        self,
        phase: str,
        query: str,
        domain: str | None = None,
        intent: str | None = None,
        rag_enabled: bool = True,
        extra_filters: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        """根據 phase 抽 RAG slot,回 `dict[slot_name -> rendered_text]`。

        Args:
            phase:phase id(對齊 DEFAULT_PHASE_POLICY keys)
            query:user 問題 / phase context(會 embed 做 query vec)
            domain:filter 用(若 slot config 有 "domain" filter_key)
            intent:filter 用(eg phase_c chart_recipe by intent)
            rag_enabled:若 False → 直接回 {} (slot 全空,prompt 走 fallback)
            extra_filters:per-call extra filter,合併進 slot filter

        Returns:
            `{slot_name: rendered_text}`,只含有 hit 的 slot。
            無 hit 的 slot 不放(prompt template 該 `{% if %}` 守護)。
        """
        if not rag_enabled:
            return {}
        slots_for_phase = self.phase_policy.get(phase)
        if not slots_for_phase:
            logger.debug("retrieve_for_phase: no policy for phase=%s", phase)
            return {}
        if not query or not query.strip():
            logger.debug("retrieve_for_phase: empty query, return {}")
            return {}

        # Embed query 一次,5 個 slot 共用
        query_vec = self.ep.embed_one(query)

        ctx = {"domain": domain, "intent": intent}
        if extra_filters:
            ctx.update(extra_filters)

        out: dict[str, str] = {}
        for slot_name in slots_for_phase:
            cfg = self.slot_configs.get(slot_name)
            if cfg is None:
                logger.warning(
                    "retrieve_for_phase: no config for slot=%s", slot_name,
                )
                continue
            rendered = self._retrieve_one_slot(query_vec, cfg, ctx)
            if rendered:
                out[slot_name] = rendered
        return out

    # ============================================================
    # Per-slot retrieval
    # ============================================================
    def _retrieve_one_slot(
        self, query_vec, cfg: SlotConfig, ctx: dict[str, Any],
    ) -> str:
        """單 slot retrieve + truncate。回空字串若無 hit。"""
        filter_ = self._build_filter(cfg.filter_keys, ctx)
        hits = self.repo.search(
            cfg.index_name, query_vec,
            top_k=cfg.top_k, filter=filter_, min_score=cfg.min_score,
        )
        if not hits:
            return ""
        return self._truncate_slot(hits, cfg.max_chars)

    @staticmethod
    def _build_filter(
        filter_keys: tuple[str, ...], ctx: dict[str, Any],
    ) -> dict[str, Any] | None:
        """從 ctx 抽 filter_keys 對應 value 組成 filter dict。None value skip。"""
        if not filter_keys:
            return None
        f: dict[str, Any] = {}
        for k in filter_keys:
            v = ctx.get(k)
            if v is not None:
                f[k] = v
        return f if f else None

    @staticmethod
    def _truncate_slot(hits, max_chars: int) -> str:
        """把 hits 的 content 串成單 string,累加直到超 budget 停。

        以 doc 為單位(不切單 doc 中間)。doc 間用 "\n\n" 分隔。
        """
        out_parts: list[str] = []
        used = 0
        for h in hits:
            content = (h.content or "").strip()
            if not content:
                continue
            sep_cost = 2 if out_parts else 0   # "\n\n" 兩字元
            if used + sep_cost + len(content) > max_chars:
                # 整 doc 放不下 → 停(spec §9.5)
                break
            out_parts.append(content)
            used += sep_cost + len(content)
        return "\n\n".join(out_parts)

    # ============================================================
    # Introspection helpers
    # ============================================================
    def get_phase_slots(self, phase: str) -> tuple[str, ...]:
        """Return slot names for given phase(用 debug / introspection)。"""
        return self.phase_policy.get(phase, ())

    def get_slot_config(self, slot_name: str) -> Optional[SlotConfig]:
        return self.slot_configs.get(slot_name)
