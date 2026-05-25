"""tests/unit/test_anti_pattern_seed.py — M6.3 Sprint 3 Day 12.

驗證 anti_pattern_seed.ANTI_PATTERNS 結構 + build_anti_pattern_index 整合。
"""

from __future__ import annotations

import pytest

from anti_pattern_seed import ANTI_PATTERNS, count_by_phase, get_anti_patterns
from embedding_pipeline import EmbeddingPipeline, make_deterministic_fake_embedder
from rag_index_repository import RAGIndexRepository, make_inmemory_factory


# ============================================================
# Catalog structure
# ============================================================
class TestCatalogStructure:
    def test_has_entries(self):
        assert len(ANTI_PATTERNS) >= 10, "seed too small"

    def test_all_required_fields(self):
        for ap in ANTI_PATTERNS:
            assert "id" in ap, f"missing id: {ap}"
            assert "applies_to_phase" in ap, f"missing phase: {ap}"
            assert "content" in ap, f"missing content: {ap}"
            assert "tags" in ap, f"missing tags: {ap}"
            assert isinstance(ap["tags"], list), f"tags not list: {ap}"

    def test_id_unique(self):
        ids = [ap["id"] for ap in ANTI_PATTERNS]
        assert len(ids) == len(set(ids)), "duplicate IDs"

    def test_phase_values_valid(self):
        valid_phases = {"phase_a", "phase_b", "phase_c"}
        for ap in ANTI_PATTERNS:
            assert ap["applies_to_phase"] in valid_phases, \
                f"invalid phase: {ap['applies_to_phase']}"

    def test_content_non_empty(self):
        for ap in ANTI_PATTERNS:
            assert ap["content"].strip(), f"empty content: {ap['id']}"
            assert len(ap["content"]) >= 30, f"too short: {ap['id']}"

    def test_severity_tag_present(self):
        valid_severity = {"fatal", "high", "med"}
        for ap in ANTI_PATTERNS:
            tags = set(ap["tags"])
            severity = tags & valid_severity
            assert len(severity) == 1, \
                f"{ap['id']}: need exactly 1 severity tag, got {severity}"

    def test_coverage_all_three_phases(self):
        """確認 Phase A/B/C 都各有 anti-patterns 進 index。"""
        counts = count_by_phase()
        assert counts.get("phase_a", 0) >= 3
        assert counts.get("phase_b", 0) >= 3
        assert counts.get("phase_c", 0) >= 3


# ============================================================
# get_anti_patterns helper
# ============================================================
class TestGetAntiPatterns:
    def test_no_filter_returns_all(self):
        assert len(get_anti_patterns()) == len(ANTI_PATTERNS)

    def test_phase_a_filter(self):
        out = get_anti_patterns("phase_a")
        assert len(out) >= 3
        assert all(p["applies_to_phase"] == "phase_a" for p in out)

    def test_unknown_phase_returns_empty(self):
        assert get_anti_patterns("phase_x_unknown") == []


# ============================================================
# Integration:build_anti_pattern_index
# ============================================================
class TestBuildAntiPatternIndex:
    @pytest.fixture
    def setup(self):
        from scripts.build_rag_indices import build_anti_pattern_index
        ep = EmbeddingPipeline(embed_func=make_deterministic_fake_embedder())
        repo = RAGIndexRepository(backend_factory=make_inmemory_factory())
        # Mock mongo_db that always returns empty for learning_instincts
        from unittest.mock import MagicMock
        mongo = MagicMock()
        mongo.__getitem__.return_value.find.return_value = iter([])
        return build_anti_pattern_index, repo, ep, mongo

    def test_builds_seed_docs(self, setup):
        build, repo, ep, mongo = setup
        n = build(repo, mongo, ep)
        assert n == len(ANTI_PATTERNS), \
            f"expected {len(ANTI_PATTERNS)} docs, got {n}"
        assert repo.count("anti_pattern_index") == n

    def test_doc_has_phase_metadata(self, setup):
        build, repo, ep, mongo = setup
        build(repo, mongo, ep)
        docs = repo.list_docs("anti_pattern_index", limit=100)
        phases = set()
        for d in docs:
            phases.add(d.metadata.get("applies_to_phase"))
        assert phases == {"phase_a", "phase_b", "phase_c"}

    def test_source_type_marked_seed(self, setup):
        build, repo, ep, mongo = setup
        build(repo, mongo, ep)
        docs = repo.list_docs("anti_pattern_index", limit=100)
        # 全部該標 source_type='seed'(learning_instincts mock 為空)
        for d in docs:
            assert d.metadata.get("source_type") == "seed"

    def test_clears_before_rebuild(self, setup):
        build, repo, ep, mongo = setup
        build(repo, mongo, ep)
        first_count = repo.count("anti_pattern_index")
        # 重 build 該 clear + repopulate(count 不變)
        build(repo, mongo, ep)
        assert repo.count("anti_pattern_index") == first_count

    def test_dry_run_does_not_write(self, setup):
        build, repo, ep, mongo = setup
        n = build(repo, mongo, ep, dry_run=True)
        assert n == len(ANTI_PATTERNS)
        # repo 該還是空
        assert repo.count("anti_pattern_index") == 0

    def test_learning_instincts_integration(self):
        """v0.16.0+ M6.5:狀態 = 'active' 的 learning_instincts 進 index。"""
        from scripts.build_rag_indices import build_anti_pattern_index
        from unittest.mock import MagicMock
        ep = EmbeddingPipeline(embed_func=make_deterministic_fake_embedder())
        repo = RAGIndexRepository(backend_factory=make_inmemory_factory())
        # 用 'active' status — GenBI 學習 pipeline 的 promoted-into-rules 狀態
        mongo = MagicMock()
        fake_instincts = [
            {"instinct_id": "INST001", "status": "active", "phase": "phase_b",
             "rule": "always cast date columns before .dt accessor"},
            {"instinct_id": "INST002", "status": "active", "phase": "phase_c",
             "rule": "do not use 3d pie chart on any dataset"},
        ]
        mongo.__getitem__.return_value.find.return_value = iter(fake_instincts)
        n = build_anti_pattern_index(repo, mongo, ep)
        assert n == len(ANTI_PATTERNS) + 2

        # 確認 learning 的 2 個進去了
        docs = repo.list_docs("anti_pattern_index", limit=100)
        learn_docs = [d for d in docs
                      if d.metadata.get("source_type") == "learning_instinct"]
        assert len(learn_docs) == 2
        learn_ids = {d.metadata.get("instinct_id") for d in learn_docs}
        assert learn_ids == {"INST001", "INST002"}

    def test_learning_status_query_filter(self):
        """v0.16.0+ M6.5:Mongo query 該過濾 status='active'(不該抓 candidate/deprecated)。"""
        from scripts.build_rag_indices import build_anti_pattern_index
        from unittest.mock import MagicMock
        ep = EmbeddingPipeline(embed_func=make_deterministic_fake_embedder())
        repo = RAGIndexRepository(backend_factory=make_inmemory_factory())
        mongo = MagicMock()
        mongo.__getitem__.return_value.find.return_value = iter([])
        build_anti_pattern_index(repo, mongo, ep)
        # 找 learning_instincts.find() 的 call,確認 query 是 {status: 'active'}
        find_call = mongo.__getitem__.return_value.find.call_args
        assert find_call[0][0] == {"status": "active"}

    def test_learning_phase_normalization(self):
        """v0.16.0+ M6.5:learning_instincts.phase 該被 normalize 到 'phase_X' 格式。"""
        from scripts.build_rag_indices import build_anti_pattern_index
        from unittest.mock import MagicMock
        ep = EmbeddingPipeline(embed_func=make_deterministic_fake_embedder())
        repo = RAGIndexRepository(backend_factory=make_inmemory_factory())
        mongo = MagicMock()
        # 各種 phase 格式 — 該都 normalize 到 phase_a/b/c
        fake_instincts = [
            {"instinct_id": "I1", "status": "active", "phase": "a",
             "rule": "rule for a"},
            {"instinct_id": "I2", "status": "active", "phase": "phaseB",
             "rule": "rule for b"},
            {"instinct_id": "I3", "status": "active", "phase": "C",
             "rule": "rule for c"},
            {"instinct_id": "I4", "status": "active", "phase": "phase_a",
             "rule": "rule for a2"},
        ]
        mongo.__getitem__.return_value.find.return_value = iter(fake_instincts)
        build_anti_pattern_index(repo, mongo, ep)
        docs = repo.list_docs("anti_pattern_index", limit=100)
        learn_docs = [d for d in docs
                      if d.metadata.get("source_type") == "learning_instinct"]
        phases = sorted(d.metadata.get("applies_to_phase") for d in learn_docs)
        assert phases == ["phase_a", "phase_a", "phase_b", "phase_c"]
