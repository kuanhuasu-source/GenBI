"""tests/unit/test_chart_recipe_index.py — M6.3 Sprint 3 Day 15.

驗證 build_chart_recipe_index:從 domain_metadata.charting_guidance 抽
recommended_charts(per-intent)+ chart_rules(general)。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from embedding_pipeline import EmbeddingPipeline, make_deterministic_fake_embedder
from rag_index_repository import RAGIndexRepository, make_inmemory_factory
from scripts.build_rag_indices import build_chart_recipe_index


# ============================================================
# Fixtures
# ============================================================
@pytest.fixture
def ep():
    return EmbeddingPipeline(embed_func=make_deterministic_fake_embedder())


@pytest.fixture
def repo():
    return RAGIndexRepository(backend_factory=make_inmemory_factory())


def _empty_mongo():
    """Mongo where domain_metadata cursor is empty → fallback to embedded."""
    mongo = MagicMock()
    mongo.__getitem__.return_value.find.return_value = iter([])
    return mongo


# ============================================================
# Integration with embedded tflex
# ============================================================
class TestBuildFromEmbedded:
    def test_builds_from_tflex_fallback(self, repo, ep):
        """No DB tflex doc → embedded fallback → tflex charting_guidance picks up."""
        n = build_chart_recipe_index(repo, _empty_mongo(), ep, domain="tflex")
        # tflex has 6 recommended_charts + 4 chart_rules = 10
        assert n >= 8, f"expected 8+ docs, got {n}"
        assert repo.count("chart_recipe_index") == n

    def test_tflex_has_both_source_types(self, repo, ep):
        build_chart_recipe_index(repo, _empty_mongo(), ep, domain="tflex")
        docs = repo.list_docs("chart_recipe_index", limit=50)
        source_types = {d.metadata.get("source_type") for d in docs}
        assert "recommended_chart" in source_types
        assert "chart_rule" in source_types

    def test_recommended_chart_has_intent(self, repo, ep):
        """recommended_charts 該帶 intent metadata(filter 用)。"""
        build_chart_recipe_index(repo, _empty_mongo(), ep, domain="tflex")
        docs = repo.list_docs("chart_recipe_index", limit=50)
        rc_docs = [d for d in docs
                   if d.metadata.get("source_type") == "recommended_chart"]
        for d in rc_docs:
            assert d.metadata.get("intent"), f"no intent: {d.doc_id}"

    def test_chart_rule_no_intent(self, repo, ep):
        """chart_rules 不該帶 intent(跨 intent 共用)。"""
        build_chart_recipe_index(repo, _empty_mongo(), ep, domain="tflex")
        docs = repo.list_docs("chart_recipe_index", limit=50)
        rule_docs = [d for d in docs
                     if d.metadata.get("source_type") == "chart_rule"]
        assert rule_docs, "no chart_rules indexed"
        for d in rule_docs:
            assert d.metadata.get("intent") is None or \
                   "intent" not in d.metadata


# ============================================================
# With custom mocked metadata
# ============================================================
class TestBuildFromMockedMetadata:
    def _make_mongo_with_metadata(self, md: dict, domain: str = "test_d"):
        mongo = MagicMock()
        doc = {"domain": domain, "is_active": True, "metadata": md}
        mongo.__getitem__.return_value.find.return_value = iter([doc])
        return mongo

    def test_recommended_chart_content_format(self, repo, ep):
        md = {
            "charting_guidance": {
                "recommended_charts": {
                    "company_total": {
                        "chart_type": "bar",
                        "x": "company_code",
                        "y": "count",
                    }
                }
            }
        }
        mongo = self._make_mongo_with_metadata(md, "x_dom")
        n = build_chart_recipe_index(repo, mongo, ep, domain="x_dom")
        assert n == 1
        docs = repo.list_docs("chart_recipe_index", limit=10)
        c = docs[0].content
        assert "company_total" in c
        assert "chart_type=bar" in c
        assert "x=company_code" in c
        assert "y=count" in c
        assert docs[0].metadata["intent"] == "bar"

    def test_recommended_chart_extras_included(self, repo, ep):
        """series / stack / group_by 等 extra fields 該進 content。"""
        md = {
            "charting_guidance": {
                "recommended_charts": {
                    "stacked": {
                        "chart_type": "stacked_bar",
                        "x": "cat",
                        "y": "count",
                        "series": "status",
                        "stack": "100%",
                    }
                }
            }
        }
        mongo = self._make_mongo_with_metadata(md)
        build_chart_recipe_index(repo, mongo, ep, domain="test_d")
        c = repo.list_docs("chart_recipe_index", limit=5)[0].content
        assert "series=status" in c
        assert "stack=100%" in c

    def test_chart_rules_indexed(self, repo, ep):
        md = {
            "charting_guidance": {
                "chart_rules": [
                    "Avoid 3D pie charts.",
                    "Use percentage for ratio.",
                ]
            }
        }
        mongo = self._make_mongo_with_metadata(md)
        n = build_chart_recipe_index(repo, mongo, ep, domain="test_d")
        assert n == 2

    def test_no_charting_guidance(self, repo, ep):
        md = {"collections": {"x": {"fields": {}}}}
        mongo = self._make_mongo_with_metadata(md)
        n = build_chart_recipe_index(repo, mongo, ep, domain="test_d")
        assert n == 0

    def test_empty_rules_list(self, repo, ep):
        md = {"charting_guidance": {"chart_rules": []}}
        mongo = self._make_mongo_with_metadata(md)
        n = build_chart_recipe_index(repo, mongo, ep, domain="test_d")
        assert n == 0

    def test_skips_non_string_rules(self, repo, ep):
        md = {
            "charting_guidance": {
                "chart_rules": [
                    "valid rule",
                    {"nested": "dict"},   # 該 skip
                    "",                     # 該 skip(empty)
                    "another valid",
                ]
            }
        }
        mongo = self._make_mongo_with_metadata(md)
        n = build_chart_recipe_index(repo, mongo, ep, domain="test_d")
        assert n == 2

    def test_dry_run(self, repo, ep):
        md = {
            "charting_guidance": {
                "recommended_charts": {"x": {"chart_type": "bar", "x": "a", "y": "b"}},
                "chart_rules": ["rule"],
            }
        }
        mongo = self._make_mongo_with_metadata(md)
        n = build_chart_recipe_index(
            repo, mongo, ep, domain="test_d", dry_run=True,
        )
        assert n == 2
        assert repo.count("chart_recipe_index") == 0

    def test_clears_before_rebuild(self, repo, ep):
        md = {"charting_guidance": {"chart_rules": ["r1"]}}
        mongo = self._make_mongo_with_metadata(md)
        build_chart_recipe_index(repo, mongo, ep, domain="test_d")
        assert repo.count("chart_recipe_index") == 1
        # Rebuild with diff data
        md2 = {"charting_guidance": {"chart_rules": ["r2", "r3"]}}
        build_chart_recipe_index(
            repo, self._make_mongo_with_metadata(md2), ep, domain="test_d",
        )
        assert repo.count("chart_recipe_index") == 2
