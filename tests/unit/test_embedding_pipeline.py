"""tests/unit/test_embedding_pipeline.py — unit tests for embedding_pipeline.py (M6.1)."""

from __future__ import annotations

import numpy as np
import pytest

from embedding_pipeline import (
    DEFAULT_DIM,
    DEFAULT_MODEL,
    EmbeddingPipeline,
    get_embedding_pipeline,
    make_deterministic_fake_embedder,
    reset_embedding_pipeline,
)


# ============================================================
# Deterministic fake embedder
# ============================================================
class TestDeterministicFakeEmbedder:
    def test_same_text_same_vector(self):
        fake = make_deterministic_fake_embedder(dim=384)
        v1 = fake(["hello"])
        v2 = fake(["hello"])
        np.testing.assert_array_equal(v1, v2)

    def test_different_text_different_vector(self):
        fake = make_deterministic_fake_embedder()
        v1 = fake(["hello"])
        v2 = fake(["world"])
        # 不該相等(99.99% 機率)
        assert not np.array_equal(v1, v2)

    def test_returns_unit_vectors(self):
        fake = make_deterministic_fake_embedder(dim=384)
        vecs = fake(["a", "b", "c"])
        for v in vecs:
            norm = np.linalg.norm(v)
            assert abs(norm - 1.0) < 1e-5, f"vector not unit: norm={norm}"

    def test_shape(self):
        fake = make_deterministic_fake_embedder(dim=384)
        vecs = fake(["x", "y", "z"])
        assert vecs.shape == (3, 384)

    def test_custom_dim(self):
        fake = make_deterministic_fake_embedder(dim=128)
        vecs = fake(["x"])
        assert vecs.shape == (1, 128)


# ============================================================
# EmbeddingPipeline with injected fake
# ============================================================
class TestEmbeddingPipeline:
    def test_embed_with_fake(self):
        fake = make_deterministic_fake_embedder()
        ep = EmbeddingPipeline(embed_func=fake)
        vecs = ep.embed(["q1", "q2"])
        assert vecs.shape == (2, DEFAULT_DIM)

    def test_embed_one(self):
        fake = make_deterministic_fake_embedder()
        ep = EmbeddingPipeline(embed_func=fake)
        v = ep.embed_one("query")
        assert v.shape == (DEFAULT_DIM,)

    def test_empty_input(self):
        fake = make_deterministic_fake_embedder()
        ep = EmbeddingPipeline(embed_func=fake)
        vecs = ep.embed([])
        assert vecs.shape == (0, DEFAULT_DIM)

    def test_dtype_float32(self):
        fake = make_deterministic_fake_embedder()
        ep = EmbeddingPipeline(embed_func=fake)
        vecs = ep.embed(["x"])
        assert vecs.dtype == np.float32

    def test_no_real_model_loaded_when_using_fake(self):
        fake = make_deterministic_fake_embedder()
        ep = EmbeddingPipeline(embed_func=fake)
        ep.embed(["x"])
        # _model 該保持 None(沒去碰真 sentence-transformers)
        assert ep._model is None


# ============================================================
# Cosine similarity helper
# ============================================================
class TestCosineSimilarity:
    def test_same_vector_returns_1(self):
        ep = EmbeddingPipeline(embed_func=make_deterministic_fake_embedder())
        v = np.array([1.0, 0.0, 0.0])
        assert abs(ep.cosine_similarity(v, v) - 1.0) < 1e-6

    def test_orthogonal_returns_0(self):
        ep = EmbeddingPipeline(embed_func=make_deterministic_fake_embedder())
        a = np.array([1.0, 0.0])
        b = np.array([0.0, 1.0])
        assert abs(ep.cosine_similarity(a, b)) < 1e-6

    def test_opposite_returns_minus_1(self):
        ep = EmbeddingPipeline(embed_func=make_deterministic_fake_embedder())
        a = np.array([1.0, 0.0])
        b = np.array([-1.0, 0.0])
        assert abs(ep.cosine_similarity(a, b) + 1.0) < 1e-6

    def test_zero_vector_returns_0(self):
        ep = EmbeddingPipeline(embed_func=make_deterministic_fake_embedder())
        a = np.array([0.0, 0.0])
        b = np.array([1.0, 0.0])
        assert ep.cosine_similarity(a, b) == 0.0


# ============================================================
# Singleton behavior
# ============================================================
class TestSingleton:
    def test_returns_same_instance(self):
        reset_embedding_pipeline()
        fake = make_deterministic_fake_embedder()
        ep1 = get_embedding_pipeline(embed_func=fake)
        ep2 = get_embedding_pipeline()
        assert ep1 is ep2

    def test_reset_releases_singleton(self):
        reset_embedding_pipeline()
        fake = make_deterministic_fake_embedder()
        ep1 = get_embedding_pipeline(embed_func=fake)
        reset_embedding_pipeline()
        ep2 = get_embedding_pipeline(embed_func=fake)
        assert ep1 is not ep2


# ============================================================
# 確認預設 default
# ============================================================
def test_default_model_name():
    assert DEFAULT_MODEL == "sentence-transformers/all-MiniLM-L6-v2"


def test_default_dim():
    assert DEFAULT_DIM == 384


# ============================================================
# Real model import path(不真跑,只測 lazy import behavior)
# ============================================================
class TestLazyImport:
    def test_real_model_not_loaded_until_called(self):
        """確認 EmbeddingPipeline init 不 load real model — embed_func=None 且未 embed 不會試載"""
        ep = EmbeddingPipeline()   # 沒給 embed_func
        # 直到 .embed() 才會嘗試 _load_real_model
        assert ep._model is None
        assert ep._embed_func is None
