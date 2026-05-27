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


# ============================================================
# v0.16.1+:dim auto-inference + HTTP backend
# ============================================================
class TestInferDim:
    def test_minilm_default(self):
        from embedding_pipeline import _infer_dim
        assert _infer_dim("sentence-transformers/all-MiniLM-L6-v2") == 384
        assert _infer_dim("all-MiniLM-L6-v2") == 384

    def test_bge_m3_1024(self):
        from embedding_pipeline import _infer_dim
        assert _infer_dim("bge-m3") == 1024
        assert _infer_dim("bge-m3:latest") == 1024          # Ollama tag
        assert _infer_dim("BAAI/bge-m3") == 1024            # HF style

    def test_bge_base_zh_768(self):
        from embedding_pipeline import _infer_dim
        assert _infer_dim("BAAI/bge-base-zh") == 768

    def test_unknown_falls_back_to_default(self):
        from embedding_pipeline import _infer_dim
        assert _infer_dim("not-a-real-model") == DEFAULT_DIM


class TestEmbeddingPipelineAutoDim:
    def test_bge_m3_no_dim_kwarg(self):
        """v0.16.1+:不傳 dim 時該從 model_name 自動推。"""
        ep = EmbeddingPipeline(model_name="bge-m3")
        assert ep.dim == 1024

    def test_explicit_dim_overrides_inference(self):
        ep = EmbeddingPipeline(model_name="bge-m3", dim=2048)
        assert ep.dim == 2048


class TestHTTPEmbedder:
    """v0.16.1+:OpenAI-compatible HTTP embedder(Ollama / vLLM 通用)。

    Mock OpenAI client,不真打 HTTP。
    """
    def test_returns_normalized_vectors(self):
        from unittest.mock import MagicMock, patch
        from embedding_pipeline import make_http_embedder

        class FakeData:
            def __init__(self, idx, emb):
                self.index = idx
                self.embedding = emb

        mock_client = MagicMock()
        fake_resp = MagicMock()
        fake_resp.data = [
            FakeData(0, [3.0, 4.0, 0.0, 0.0]),   # norm=5
            FakeData(1, [0.0, 0.0, 1.0, 0.0]),   # already unit
        ]
        mock_client.embeddings.create.return_value = fake_resp

        with patch("openai.OpenAI", return_value=mock_client):
            embed_fn = make_http_embedder(
                api_url="http://localhost:11434/v1/embeddings",
                model="bge-m3",
            )
            vecs = embed_fn(["text1", "text2"])

        assert vecs.shape == (2, 4)
        # L2 normalize 過 → norm 該 ~1
        assert abs(np.linalg.norm(vecs[0]) - 1.0) < 1e-5
        assert abs(np.linalg.norm(vecs[1]) - 1.0) < 1e-5
        np.testing.assert_allclose(vecs[0], [0.6, 0.8, 0.0, 0.0], atol=1e-5)

    def test_empty_input(self):
        from unittest.mock import patch, MagicMock
        from embedding_pipeline import make_http_embedder
        with patch("openai.OpenAI", return_value=MagicMock()):
            embed_fn = make_http_embedder(
                api_url="http://x/v1/embeddings", model="bge-m3",
            )
            vecs = embed_fn([])
        assert vecs.shape == (0, 0)

    def test_batching_respects_batch_size(self):
        from unittest.mock import MagicMock, patch
        from embedding_pipeline import make_http_embedder

        class FakeData:
            def __init__(self, idx, emb):
                self.index = idx
                self.embedding = emb

        mock_client = MagicMock()
        def _create(model, input):
            fake = MagicMock()
            fake.data = [FakeData(i, [1.0, 0.0, 0.0])
                          for i in range(len(input))]
            return fake
        mock_client.embeddings.create.side_effect = _create

        with patch("openai.OpenAI", return_value=mock_client):
            embed_fn = make_http_embedder(
                api_url="http://x/v1/embeddings", model="m", batch_size=2,
            )
            vecs = embed_fn(["a", "b", "c", "d", "e"])

        assert vecs.shape == (5, 3)
        # 5 input / batch=2 → 3 calls(2+2+1)
        assert mock_client.embeddings.create.call_count == 3

    def test_server_error_raises_with_context(self):
        from unittest.mock import MagicMock, patch
        from embedding_pipeline import make_http_embedder

        mock_client = MagicMock()
        mock_client.embeddings.create.side_effect = ConnectionError("boom")
        with patch("openai.OpenAI", return_value=mock_client):
            embed_fn = make_http_embedder(
                api_url="http://x/v1/embeddings", model="bge-m3",
            )
            with pytest.raises(RuntimeError, match="bge-m3"):
                embed_fn(["hello"])


class TestGetEmbeddingPipelineBackendRouting:
    """v0.16.1+:GENBI_EMBEDDING_BACKEND env 路由。"""

    def test_default_local_backend(self, monkeypatch):
        monkeypatch.delenv("GENBI_EMBEDDING_BACKEND", raising=False)
        monkeypatch.delenv("GENBI_EMBEDDING_MODEL", raising=False)
        reset_embedding_pipeline()
        ep = get_embedding_pipeline()
        assert ep.model_name == DEFAULT_MODEL
        # default → local sentence-transformers,_embed_func 該 None
        assert ep._embed_func is None
        reset_embedding_pipeline()

    def test_http_backend_creates_http_embedder(self, monkeypatch):
        from unittest.mock import patch, MagicMock
        monkeypatch.setenv("GENBI_EMBEDDING_BACKEND", "http")
        monkeypatch.setenv("GENBI_EMBEDDING_MODEL", "bge-m3")
        monkeypatch.setenv(
            "GENBI_EMBEDDING_API_URL",
            "http://localhost:11434/v1/embeddings",
        )
        reset_embedding_pipeline()
        with patch("openai.OpenAI", return_value=MagicMock()):
            ep = get_embedding_pipeline()
        assert ep.model_name == "bge-m3"
        assert ep.dim == 1024     # bge-m3 該自動推
        # _embed_func 該是 http embedder callable
        assert ep._embed_func is not None
        reset_embedding_pipeline()

    def test_explicit_embed_func_overrides_env(self, monkeypatch):
        # 即使 env 設 http,caller 傳 embed_func 該贏(test path)
        monkeypatch.setenv("GENBI_EMBEDDING_BACKEND", "http")
        reset_embedding_pipeline()
        fake = make_deterministic_fake_embedder()
        ep = get_embedding_pipeline(embed_func=fake)
        assert ep._embed_func is fake
        reset_embedding_pipeline()
