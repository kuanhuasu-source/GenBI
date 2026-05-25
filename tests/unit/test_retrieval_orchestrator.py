"""tests/unit/test_retrieval_orchestrator.py — unit tests for retrieval_orchestrator (M6.1 Sprint 1 Day 5).

走 InMemoryBackend + fake embedder,零依賴。
"""

from __future__ import annotations

import pytest

from embedding_pipeline import EmbeddingPipeline, make_deterministic_fake_embedder
from rag_index_repository import RAGIndexRepository, make_inmemory_factory
from retrieval_orchestrator import (
    DEFAULT_PHASE_POLICY,
    DEFAULT_SLOT_CONFIGS,
    KNOWN_SLOTS,
    SLOT_ANTI_PATTERN,
    SLOT_CHART_RECIPE,
    SLOT_FEW_SHOT,
    SLOT_KPI,
    SLOT_SCHEMA,
    RetrievalOrchestrator,
    SlotConfig,
)


# ============================================================
# Fixtures
# ============================================================
@pytest.fixture
def ep():
    return EmbeddingPipeline(embed_func=make_deterministic_fake_embedder())


@pytest.fixture
def repo():
    return RAGIndexRepository(backend_factory=make_inmemory_factory())


@pytest.fixture
def orch(repo, ep):
    return RetrievalOrchestrator(rag_repo=repo, embedding_pipeline=ep)


def _seed(repo, ep, index_name, docs):
    """docs = [(doc_id, content, metadata)]"""
    for doc_id, content, md in docs:
        repo.add_doc(index_name, doc_id, content, ep.embed_one(content),
                     metadata=md)


# ============================================================
# Constants / config integrity
# ============================================================
class TestConstants:
    def test_known_slots_count(self):
        assert len(KNOWN_SLOTS) == 5

    def test_all_slots_have_config(self):
        for slot in KNOWN_SLOTS:
            assert slot in DEFAULT_SLOT_CONFIGS

    def test_phase_policy_uses_known_slots(self):
        """每個 phase policy 的 slot 都該在 KNOWN_SLOTS 內。"""
        for phase, slots in DEFAULT_PHASE_POLICY.items():
            for s in slots:
                assert s in KNOWN_SLOTS, f"phase={phase} bad slot={s}"

    def test_phase_0_plan_slots(self):
        """spec §9.2:Phase 0 抽 schema + kpi + few_shot"""
        assert DEFAULT_PHASE_POLICY["phase_0_plan"] == (
            SLOT_SCHEMA, SLOT_KPI, SLOT_FEW_SHOT,
        )

    def test_phase_c_chart_slots(self):
        """spec §9.2:Phase C 抽 chart_recipe + anti_pattern"""
        assert DEFAULT_PHASE_POLICY["phase_c_chart"] == (
            SLOT_CHART_RECIPE, SLOT_ANTI_PATTERN,
        )

    def test_max_chars_align_with_spec(self):
        """spec §9.5 token budget"""
        assert DEFAULT_SLOT_CONFIGS[SLOT_SCHEMA].max_chars == 1200
        assert DEFAULT_SLOT_CONFIGS[SLOT_KPI].max_chars == 600
        assert DEFAULT_SLOT_CONFIGS[SLOT_FEW_SHOT].max_chars == 1500
        assert DEFAULT_SLOT_CONFIGS[SLOT_ANTI_PATTERN].max_chars == 800
        assert DEFAULT_SLOT_CONFIGS[SLOT_CHART_RECIPE].max_chars == 2000


# ============================================================
# Init guards
# ============================================================
class TestInit:
    def test_requires_repo(self, ep):
        with pytest.raises(ValueError, match="rag_repo"):
            RetrievalOrchestrator(rag_repo=None, embedding_pipeline=ep)

    def test_requires_ep(self, repo):
        with pytest.raises(ValueError, match="embedding_pipeline"):
            RetrievalOrchestrator(rag_repo=repo, embedding_pipeline=None)

    def test_default_configs(self, orch):
        assert orch.slot_configs is DEFAULT_SLOT_CONFIGS
        assert orch.phase_policy is DEFAULT_PHASE_POLICY


# ============================================================
# rag_enabled flag
# ============================================================
class TestRagEnabledFlag:
    def test_disabled_returns_empty(self, orch):
        out = orch.retrieve_for_phase(
            phase="phase_0_plan", query="anything", rag_enabled=False,
        )
        assert out == {}

    def test_disabled_with_data_still_empty(self, orch, repo, ep):
        _seed(repo, ep, "schema_index", [
            ("s-1", "company_code field", {"domain": "tflex"}),
        ])
        out = orch.retrieve_for_phase(
            phase="phase_0_plan", query="company", domain="tflex",
            rag_enabled=False,
        )
        assert out == {}


# ============================================================
# Empty / unknown inputs
# ============================================================
class TestEdgeCases:
    def test_unknown_phase_returns_empty(self, orch):
        out = orch.retrieve_for_phase(phase="phase_X_unknown", query="q")
        assert out == {}

    def test_empty_query_returns_empty(self, orch):
        out = orch.retrieve_for_phase(phase="phase_0_plan", query="")
        assert out == {}

    def test_whitespace_query_returns_empty(self, orch):
        out = orch.retrieve_for_phase(phase="phase_0_plan", query="   ")
        assert out == {}

    def test_no_hits_returns_no_slot(self, orch):
        """所有 index 空 → 沒 hit → 沒 slot 進 result。"""
        out = orch.retrieve_for_phase(
            phase="phase_0_plan", query="anything", domain="tflex",
        )
        # phase_0 該抽 3 個 slot,但所有 index 是空的 → out 該為空 dict
        assert out == {}


# ============================================================
# Per-phase routing
# ============================================================
class TestPerPhaseRouting:
    def test_phase_0_routes_to_schema_kpi_few_shot(self, orch, repo, ep):
        _seed(repo, ep, "schema_index", [
            ("s-1", "schema content for tflex", {"domain": "tflex"}),
        ])
        _seed(repo, ep, "kpi_index", [
            ("k-1", "kpi content for tflex", {"domain": "tflex"}),
        ])
        _seed(repo, ep, "few_shot_index", [
            ("f-1", "few shot for tflex", {"domain": "tflex"}),
        ])
        # 不該抽的 index:加 data 但驗 result 沒對應 slot
        _seed(repo, ep, "anti_pattern_index", [
            ("a-1", "anti pattern", {}),
        ])
        # Query 匹配 schema content(fake embedder identical → cosine=1)
        out = orch.retrieve_for_phase(
            phase="phase_0_plan",
            query="schema content for tflex",
            domain="tflex",
        )
        assert SLOT_SCHEMA in out
        # anti_pattern 該沒(phase_0 沒抽它)
        assert SLOT_ANTI_PATTERN not in out
        assert SLOT_CHART_RECIPE not in out

    def test_phase_c_routes_to_chart_recipe_anti_pattern(self, orch, repo, ep):
        _seed(repo, ep, "chart_recipe_index", [
            ("c-1", "bar chart recipe", {"intent": "bar"}),
        ])
        _seed(repo, ep, "anti_pattern_index", [
            ("a-1", "do not use 3D pie", {}),
        ])
        _seed(repo, ep, "schema_index", [
            ("s-1", "should not appear", {"domain": "tflex"}),
        ])
        out = orch.retrieve_for_phase(
            phase="phase_c_chart",
            query="bar chart recipe",
            intent="bar",
        )
        assert SLOT_CHART_RECIPE in out
        # schema 不該出現(phase_c 沒抽它)
        assert SLOT_SCHEMA not in out

    def test_phase_d_routes_to_kpi_only(self, orch, repo, ep):
        _seed(repo, ep, "kpi_index", [
            ("k-1", "kpi insight on leadtime", {"domain": "tflex"}),
        ])
        _seed(repo, ep, "few_shot_index", [
            ("f-1", "should not appear", {"domain": "tflex"}),
        ])
        out = orch.retrieve_for_phase(
            phase="phase_d_insight",
            query="kpi insight on leadtime",
            domain="tflex",
        )
        assert SLOT_KPI in out
        assert SLOT_FEW_SHOT not in out
        assert SLOT_SCHEMA not in out


# ============================================================
# Filter routing(domain / intent)
# ============================================================
class TestFilters:
    def test_domain_filter_isolates_results(self, orch, repo, ep):
        """domain=tflex query 不該 hit ecommerce 的 schema doc。"""
        _seed(repo, ep, "schema_index", [
            ("t-1", "common term", {"domain": "tflex"}),
            ("e-1", "common term", {"domain": "ecommerce"}),
        ])
        out = orch.retrieve_for_phase(
            phase="phase_0_plan",
            query="common term",
            domain="tflex",
        )
        assert SLOT_SCHEMA in out
        # 該只含 tflex 的(content 同,但 metadata 不同)
        # 因為 inmemory 同 content 同 embedding 都 cosine=1,
        # 兩 doc score 同 → 但 filter 該擋掉 ecommerce
        assert "common term" in out[SLOT_SCHEMA]
        # 確認只 1 個 doc(不該有重複)
        assert out[SLOT_SCHEMA].count("common term") == 1

    def test_no_domain_no_filter(self, orch, repo, ep):
        """anti_pattern slot 沒 domain filter,跨 domain 共用。"""
        _seed(repo, ep, "anti_pattern_index", [
            ("a-1", "global anti pattern", {}),
        ])
        out = orch.retrieve_for_phase(
            phase="phase_b_preprocess",
            query="global anti pattern",
        )
        assert SLOT_ANTI_PATTERN in out

    def test_anti_pattern_phase_filter(self, orch, repo, ep):
        """v0.16.0+ M6.3 fix:Phase B 不該抽到 phase_a 或 phase_c 的 anti-pattern。"""
        _seed(repo, ep, "anti_pattern_index", [
            ("a-1", "phase_a anti", {"applies_to_phase": "phase_a"}),
            ("b-1", "phase_b anti", {"applies_to_phase": "phase_b"}),
            ("c-1", "phase_c anti", {"applies_to_phase": "phase_c"}),
        ])
        out = orch.retrieve_for_phase(
            phase="phase_b_preprocess",
            query="phase_b anti",   # identical match for fake embedder
            extra_filters={"applies_to_phase": "phase_b"},
        )
        assert SLOT_ANTI_PATTERN in out
        rendered = out[SLOT_ANTI_PATTERN]
        # 該只含 phase_b 那筆,phase_a / phase_c 該被 filter 掉
        assert "phase_b anti" in rendered
        assert "phase_a anti" not in rendered
        assert "phase_c anti" not in rendered

    def test_intent_filter_for_chart_recipe(self, orch, repo, ep):
        _seed(repo, ep, "chart_recipe_index", [
            ("c-bar", "bar chart recipe text", {"intent": "bar"}),
            ("c-pie", "pie chart recipe text", {"intent": "pie"}),
        ])
        out = orch.retrieve_for_phase(
            phase="phase_c_chart",
            query="bar chart recipe text",
            intent="bar",
        )
        assert SLOT_CHART_RECIPE in out
        # 只該抽 bar 那個
        assert "bar chart recipe text" in out[SLOT_CHART_RECIPE]
        assert "pie chart recipe text" not in out[SLOT_CHART_RECIPE]


# ============================================================
# Token budget truncation
# ============================================================
class TestTruncation:
    def test_truncate_to_max_chars(self, orch, repo, ep):
        """每 doc ~604 char,schema slot budget 1200 → 該 fit 1 doc;第 2 doc 加上 sep 會超 budget。"""
        big = "A" * 600
        _seed(repo, ep, "schema_index", [
            (f"d-{i}", big + f" id{i}", {"domain": "tflex"})
            for i in range(5)
        ])
        out = orch.retrieve_for_phase(
            phase="phase_0_plan",
            query=big + " id0",   # query = doc-0 (top-1)
            domain="tflex",
        )
        assert SLOT_SCHEMA in out
        # 整個 rendered text 該 <= max_chars(1200)
        assert len(out[SLOT_SCHEMA]) <= 1200
        # 604 * 2 + 2 sep = 1210 > 1200 → 只該裝 1 doc
        # Count distinct " idN" markers
        id_count = sum(
            1 for i in range(5) if f" id{i}" in out[SLOT_SCHEMA]
        )
        assert id_count == 1

    def test_truncate_skip_oversized_first_doc(self, orch, repo, ep):
        """單 doc 比 budget 大 → 整個 slot 該為空(spec §9.5:不切單 doc)。"""
        custom = SlotConfig(index_name="kpi_index", top_k=3, max_chars=50,
                            filter_keys=("domain",))
        orch_small = RetrievalOrchestrator(
            rag_repo=orch.repo, embedding_pipeline=orch.ep,
            slot_configs={**DEFAULT_SLOT_CONFIGS, SLOT_KPI: custom},
        )
        too_big = "X" * 200
        _seed(orch.repo, orch.ep, "kpi_index", [
            ("k-1", too_big, {"domain": "tflex"}),
        ])
        out = orch_small.retrieve_for_phase(
            phase="phase_d_insight", query=too_big, domain="tflex",
        )
        # 單 doc 200 char > 50 budget → 該 skip → 整 slot 為空 → 不進 out
        assert SLOT_KPI not in out

    def test_separator_between_docs(self, orch, repo, ep):
        """多 doc 之間該用 "\n\n" 分隔。"""
        _seed(repo, ep, "schema_index", [
            ("a", "doc A short", {"domain": "tflex"}),
            ("b", "doc B short", {"domain": "tflex"}),
        ])
        out = orch.retrieve_for_phase(
            phase="phase_0_plan",
            query="doc A short",
            domain="tflex",
        )
        # 至少 doc-A 一定該 hit;若兩個都進,該被 \n\n 分
        if "doc B short" in out.get(SLOT_SCHEMA, ""):
            assert "\n\n" in out[SLOT_SCHEMA]


# ============================================================
# Helpers
# ============================================================
class TestBuildFilter:
    def test_no_filter_keys(self):
        f = RetrievalOrchestrator._build_filter((), {"domain": "tflex"})
        assert f is None

    def test_some_keys_present(self):
        f = RetrievalOrchestrator._build_filter(
            ("domain", "intent"), {"domain": "tflex", "intent": None},
        )
        assert f == {"domain": "tflex"}

    def test_all_none_returns_none(self):
        f = RetrievalOrchestrator._build_filter(
            ("domain",), {"domain": None},
        )
        assert f is None


class TestTruncateSlot:
    def test_empty_hits_returns_empty(self):
        assert RetrievalOrchestrator._truncate_slot([], 100) == ""

    def test_single_hit_under_budget(self):
        class H:
            content = "hello"
        result = RetrievalOrchestrator._truncate_slot([H()], 100)
        assert result == "hello"

    def test_multiple_hits_joined(self):
        class H:
            def __init__(self, c): self.content = c
        result = RetrievalOrchestrator._truncate_slot(
            [H("aa"), H("bb"), H("cc")], 100,
        )
        assert result == "aa\n\nbb\n\ncc"

    def test_skips_empty_content(self):
        class H:
            def __init__(self, c): self.content = c
        result = RetrievalOrchestrator._truncate_slot(
            [H(""), H("real"), H("  ")], 100,
        )
        assert result == "real"


# ============================================================
# Introspection
# ============================================================
class TestIntrospection:
    def test_get_phase_slots(self, orch):
        assert orch.get_phase_slots("phase_0_plan") == (
            SLOT_SCHEMA, SLOT_KPI, SLOT_FEW_SHOT,
        )

    def test_get_phase_slots_unknown(self, orch):
        assert orch.get_phase_slots("bogus") == ()

    def test_get_slot_config(self, orch):
        cfg = orch.get_slot_config(SLOT_SCHEMA)
        assert cfg is not None
        assert cfg.index_name == "schema_index"
        assert cfg.max_chars == 1200

    def test_get_slot_config_unknown(self, orch):
        assert orch.get_slot_config("rag_unknown") is None
