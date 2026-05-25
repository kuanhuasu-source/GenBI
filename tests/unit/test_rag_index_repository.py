"""tests/unit/test_rag_index_repository.py — unit tests for rag_index_repository.py (M6.1).

整套用 InMemoryBackend 跑(零依賴)。
ChromaBackend 走 production 路徑,M6.1 unit test 不測(integration 階段才驗)。
"""

from __future__ import annotations

import numpy as np
import pytest

from embedding_pipeline import EmbeddingPipeline, make_deterministic_fake_embedder
from rag_index_repository import (
    KNOWN_INDEX_NAMES,
    InMemoryBackend,
    RAGIndexRepository,
    SearchResult,
    _matches_filter,
    make_chroma_factory,
    make_inmemory_factory,
)


# ============================================================
# Fixture:fake embedder for deterministic tests
# ============================================================
@pytest.fixture
def ep():
    return EmbeddingPipeline(embed_func=make_deterministic_fake_embedder())


@pytest.fixture
def repo():
    return RAGIndexRepository(backend_factory=make_inmemory_factory())


# ============================================================
# InMemoryBackend basic CRUD
# ============================================================
class TestInMemoryBackendCRUD:
    def test_add_and_get(self, ep):
        backend = InMemoryBackend()
        v = ep.embed_one("hello world")
        backend.add("doc-1", "hello world", v, metadata={"domain": "tflex"})
        result = backend.get("doc-1")
        assert result is not None
        assert result.doc_id == "doc-1"
        assert result.content == "hello world"
        assert result.metadata["domain"] == "tflex"

    def test_count(self, ep):
        backend = InMemoryBackend()
        assert backend.count() == 0
        for i in range(5):
            backend.add(f"d-{i}", f"content {i}", ep.embed_one(f"text {i}"))
        assert backend.count() == 5

    def test_delete(self, ep):
        backend = InMemoryBackend()
        backend.add("x", "content", ep.embed_one("text"))
        assert backend.delete("x") is True
        assert backend.delete("x") is False   # 刪不存在
        assert backend.get("x") is None

    def test_upsert_behavior(self, ep):
        backend = InMemoryBackend()
        backend.add("d", "v1", ep.embed_one("v1"))
        backend.add("d", "v2", ep.embed_one("v2"))   # 覆蓋
        assert backend.count() == 1
        assert backend.get("d").content == "v2"

    def test_clear(self, ep):
        backend = InMemoryBackend()
        for i in range(3):
            backend.add(f"d-{i}", f"c-{i}", ep.embed_one(f"t-{i}"))
        backend.clear()
        assert backend.count() == 0

    def test_list_docs(self, ep):
        backend = InMemoryBackend()
        for i in range(5):
            backend.add(f"d-{i}", f"c-{i}", ep.embed_one(f"t-{i}"))
        docs = backend.list_docs(limit=3)
        assert len(docs) == 3


# ============================================================
# InMemoryBackend search
# ============================================================
class TestInMemoryBackendSearch:
    def test_search_returns_top_k(self, ep):
        backend = InMemoryBackend()
        for i in range(10):
            backend.add(f"d-{i}", f"doc {i}", ep.embed_one(f"doc {i}"))
        q = ep.embed_one("doc 5")
        results = backend.search(q, top_k=3)
        assert len(results) == 3
        # Top-1 該是 doc 5 自己(同樣 text 該 cosine=1)
        assert results[0].doc_id == "d-5"
        # Score 該 ≈ 1.0
        assert abs(results[0].score - 1.0) < 1e-5

    def test_search_empty_index(self, ep):
        backend = InMemoryBackend()
        results = backend.search(ep.embed_one("query"))
        assert results == []

    def test_search_with_filter(self, ep):
        backend = InMemoryBackend()
        backend.add("a", "x", ep.embed_one("x"), metadata={"domain": "tflex"})
        backend.add("b", "y", ep.embed_one("y"), metadata={"domain": "ecommerce"})
        backend.add("c", "z", ep.embed_one("z"), metadata={"domain": "tflex"})
        results = backend.search(
            ep.embed_one("anything"), top_k=10,
            filter={"domain": "tflex"},
        )
        assert len(results) == 2
        assert all(r.metadata["domain"] == "tflex" for r in results)

    def test_search_with_filter_in_operator(self, ep):
        backend = InMemoryBackend()
        backend.add("a", "x", ep.embed_one("x"), metadata={"intent": "bar"})
        backend.add("b", "y", ep.embed_one("y"), metadata={"intent": "pie"})
        backend.add("c", "z", ep.embed_one("z"), metadata={"intent": "scatter"})
        results = backend.search(
            ep.embed_one("anything"), top_k=10,
            filter={"intent": {"$in": ["bar", "pie"]}},
        )
        assert len(results) == 2

    def test_min_score_filter(self, ep):
        backend = InMemoryBackend()
        backend.add("a", "totally different", ep.embed_one("totally different"))
        backend.add("b", "another text", ep.embed_one("another text"))
        # min_score=0.99 → 沒有 doc 同 query 應該過不了 threshold
        # (除非 query 文字跟某 doc 完全一致)
        q = ep.embed_one("a query unrelated to any doc here")
        results = backend.search(q, top_k=10, min_score=0.99)
        assert results == []   # 沒 doc 跟 query 完全 identical

    def test_zero_vector_returns_empty(self, ep):
        backend = InMemoryBackend()
        backend.add("d", "c", ep.embed_one("t"))
        zero = np.zeros(384, dtype=np.float32)
        results = backend.search(zero, top_k=5)
        assert results == []

    def test_results_sorted_by_score_desc(self, ep):
        backend = InMemoryBackend()
        for i in range(10):
            backend.add(f"d-{i}", f"text {i}", ep.embed_one(f"text {i}"))
        results = backend.search(ep.embed_one("text 5"), top_k=5)
        # Score 該降冪
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)


# ============================================================
# _matches_filter helper
# ============================================================
class TestMatchesFilter:
    def test_exact_match(self):
        assert _matches_filter({"x": "a"}, {"x": "a"}) is True
        assert _matches_filter({"x": "a"}, {"x": "b"}) is False

    def test_missing_key(self):
        assert _matches_filter({}, {"x": "a"}) is False

    def test_multi_key_all_match(self):
        md = {"a": 1, "b": 2}
        assert _matches_filter(md, {"a": 1, "b": 2}) is True
        assert _matches_filter(md, {"a": 1, "b": 3}) is False

    def test_in_operator(self):
        assert _matches_filter({"x": "a"}, {"x": {"$in": ["a", "b"]}}) is True
        assert _matches_filter({"x": "c"}, {"x": {"$in": ["a", "b"]}}) is False


# ============================================================
# RAGIndexRepository(multi-index 管理)
# ============================================================
class TestRAGIndexRepository:
    def test_creates_all_known_indices(self, repo):
        counts = repo.count_all()
        # 5 個 known indices
        assert set(counts.keys()) == set(KNOWN_INDEX_NAMES)
        # 全空
        for c in counts.values():
            assert c == 0

    def test_add_and_search_specific_index(self, repo, ep):
        v = ep.embed_one("test schema doc")
        repo.add_doc(
            "schema_index", "schema-1", "test schema doc", v,
            metadata={"domain": "tflex"},
        )
        results = repo.search("schema_index", v, top_k=1)
        assert len(results) == 1
        assert results[0].doc_id == "schema-1"

    def test_unknown_index_raises(self, repo, ep):
        with pytest.raises(KeyError, match="Unknown index"):
            repo.add_doc("bad_index", "x", "y", ep.embed_one("z"))

    def test_isolation_between_indices(self, repo, ep):
        """每個 index 該獨立,不該互相污染"""
        repo.add_doc("schema_index", "s-1", "schema doc", ep.embed_one("schema"))
        repo.add_doc("kpi_index", "k-1", "kpi doc", ep.embed_one("kpi"))
        assert repo.count("schema_index") == 1
        assert repo.count("kpi_index") == 1
        assert repo.count("few_shot_index") == 0

    def test_delete_doc(self, repo, ep):
        repo.add_doc("schema_index", "d", "c", ep.embed_one("t"))
        assert repo.delete_doc("schema_index", "d") is True
        assert repo.get_doc("schema_index", "d") is None

    def test_list_docs(self, repo, ep):
        for i in range(3):
            repo.add_doc(
                "schema_index", f"d-{i}", f"c-{i}",
                ep.embed_one(f"t-{i}"),
            )
        docs = repo.list_docs("schema_index", limit=10)
        assert len(docs) == 3

    def test_clear(self, repo, ep):
        for i in range(5):
            repo.add_doc("schema_index", f"d-{i}", f"c-{i}",
                          ep.embed_one(f"t-{i}"))
        repo.clear("schema_index")
        assert repo.count("schema_index") == 0


# ============================================================
# E2E:整 pipeline 跑(embed + add + search)
# ============================================================
class TestEndToEnd:
    def test_realistic_schema_lookup(self, repo, ep):
        """模擬 GenBI 真實場景:塞幾個欄位描述,query 回 top-K 相關。"""
        schema_docs = [
            ("schema-1", "company_code (string, identifier): 公司代碼,例 TST, TSC, TSN",
             {"domain": "tflex", "field": "company_code"}),
            ("schema-2", "review_status (string, categorical_status): 申請審核狀態,Y/N/R/X",
             {"domain": "tflex", "field": "review_status"}),
            ("schema-3", "hc (integer, measure_count): headcount,公司員工人數",
             {"domain": "tflex", "field": "hc"}),
            ("schema-4", "application_no (string, identifier): 申請編號 unique",
             {"domain": "tflex", "field": "application_no"}),
            ("schema-5", "leadtime (number, measure_duration): 申請處理天數",
             {"domain": "tflex", "field": "leadtime"}),
        ]
        for doc_id, content, md in schema_docs:
            repo.add_doc("schema_index", doc_id, content,
                          ep.embed_one(content), metadata=md)

        # Query 該抽出最相關的(fake embedder 是 hash-based 沒語意,
        # 所以這裡用 identical content 才能驗 top-1 對。production sentence-transformers
        # 才有真語意 similarity。)
        q = ep.embed_one(schema_docs[0][1])   # query = doc-1 content
        results = repo.search("schema_index", q, top_k=2,
                                filter={"domain": "tflex"})
        assert len(results) >= 1
        assert results[0].doc_id == "schema-1"
        assert abs(results[0].score - 1.0) < 1e-5   # identical → cosine=1


# ============================================================
# Factory functions
# ============================================================
class TestFactories:
    def test_inmemory_factory(self):
        factory = make_inmemory_factory()
        backend = factory("test_index")
        assert isinstance(backend, InMemoryBackend)

    def test_chroma_factory_returns_callable(self):
        """不真建 Chroma backend(會試 import chromadb),只測 factory callable。"""
        factory = make_chroma_factory("/tmp/test_rag")
        assert callable(factory)


# ============================================================
# SearchResult dataclass
# ============================================================
class TestSearchResult:
    def test_basic(self):
        r = SearchResult(doc_id="x", content="y", score=0.5)
        assert r.doc_id == "x"
        assert r.content == "y"
        assert r.score == 0.5
        assert r.metadata == {}

    def test_with_metadata(self):
        r = SearchResult(doc_id="x", content="y", score=0.5,
                          metadata={"k": "v"})
        assert r.metadata["k"] == "v"


# ============================================================
# Index names constant
# ============================================================
def test_known_index_names():
    """spec §8 定 5 個 index"""
    assert set(KNOWN_INDEX_NAMES) == {
        "schema_index", "kpi_index", "few_shot_index",
        "anti_pattern_index", "chart_recipe_index",
    }
