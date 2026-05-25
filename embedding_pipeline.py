"""
embedding_pipeline.py — v0.16.0+ (M6.1 Sprint 1 Day 1-2)

統一 embedding 介面 — 把 query / doc 文字轉成 vector。對齊 spec §9.5 + §24.3。

# 設計重點

- **Singleton 模式**(production):全 streamlit session 共用一個 model instance,
  避免每次 query 都 cold-load(load 一次 3-5s 太貴)
- **Dependency injection**:`embed_func` 參數可注入 fake embedder 給 test 用,
  不需要實機裝 sentence-transformers / torch
- **Lazy import**:sentence-transformers 在 `_load_real_model()` 才 import,
  test mode 跑 unit test 不需要 700MB torch
- **Air-gap friendly**(spec §24.2.2):env `HF_HUB_OFFLINE=1` 時不打 HuggingFace
- **Backward compatible**:`GENBI_RAG_ENABLED=false` 時整個 module 不該被 load

# 用法

```python
# Production:用 default model
from embedding_pipeline import get_embedding_pipeline
ep = get_embedding_pipeline()
vec = ep.embed_one("query 文字")        # 1-D ndarray (384,)
vecs = ep.embed(["q1", "q2", "q3"])     # 2-D ndarray (3, 384)

# Test:inject fake embedder
fake = lambda texts: np.random.randn(len(texts), 384)
ep = EmbeddingPipeline(embed_func=fake)
```
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any, Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)


# ============================================================
# 預設模型(spec §9.6)
# ============================================================
DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_DIM = 384


# ============================================================
# Lazy loader for real model
# ============================================================
def _load_real_model(model_name: str):
    """真實載入 sentence-transformers model。

    Lazy import 避免 test mode 不需要這 700MB 依賴。
    Air-gap mode(env `HF_HUB_OFFLINE=1`)由 huggingface 自身處理。
    """
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as e:
        raise ImportError(
            "sentence-transformers not installed. Run "
            "`pip install sentence-transformers` to enable RAG."
        ) from e
    logger.info(f"Loading embedding model: {model_name}")
    return SentenceTransformer(model_name)


# ============================================================
# Deterministic fake embedder(testing only)
# ============================================================
def make_deterministic_fake_embedder(
    dim: int = DEFAULT_DIM,
) -> Callable[[list[str]], np.ndarray]:
    """產一個 fake embedder,給 unit test 用。

    每個 text 透過 hash 映射成固定 seed,再 np.random 出穩定向量 —
    這樣 test 在不同機器跑會 deterministic,且相同 text 對應相同 vector
    (similarity 才合理)。
    """
    def _fake_embed(texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), dim), dtype=np.float32)
        for i, t in enumerate(texts):
            seed = int(hashlib.md5(t.encode("utf-8")).hexdigest()[:8], 16)
            rng = np.random.default_rng(seed)
            vec = rng.standard_normal(dim).astype(np.float32)
            # Normalize 成 unit vector — 才能用 cosine similarity 比
            vec /= (np.linalg.norm(vec) + 1e-9)
            out[i] = vec
        return out
    return _fake_embed


# ============================================================
# 主類別
# ============================================================
class EmbeddingPipeline:
    """統一 embedding 介面 — production 用 sentence-transformers,test 用 fake。"""

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        dim: int = DEFAULT_DIM,
        embed_func: Optional[Callable[[list[str]], np.ndarray]] = None,
    ):
        """
        Args:
            model_name: 模型名稱,production 用(default `all-MiniLM-L6-v2`)
            dim: 向量維度(本參數該跟 model_name 對齊;預設 384)
            embed_func: 如果提供,直接用此 callable 取代真實 model。
                測試時可傳入 `make_deterministic_fake_embedder()`。
        """
        self.model_name = model_name
        self.dim = dim
        self._embed_func = embed_func
        self._model = None   # lazy load

    # ============================================================
    # 公開 API
    # ============================================================
    def embed(self, texts: list[str]) -> np.ndarray:
        """把一組 text 編成 vector matrix。

        Returns:
            np.ndarray, shape (len(texts), dim)
        """
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        # 1. 用注入的 embed_func(testing path)
        if self._embed_func is not None:
            result = self._embed_func(texts)
            return np.asarray(result, dtype=np.float32)
        # 2. 走真實 model(production path)
        return self._embed_with_real_model(texts)

    def embed_one(self, text: str) -> np.ndarray:
        """單筆 embed,回 1-D ndarray (dim,)。"""
        return self.embed([text])[0]

    def cosine_similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        """cosine similarity helper(給 InMemoryBackend 用)。
        a / b 該已經 normalize,但保險再 norm 一次。"""
        a = np.asarray(a, dtype=np.float32)
        b = np.asarray(b, dtype=np.float32)
        na = np.linalg.norm(a)
        nb = np.linalg.norm(b)
        if na < 1e-9 or nb < 1e-9:
            return 0.0
        return float(np.dot(a, b) / (na * nb))

    # ============================================================
    # Internal
    # ============================================================
    def _embed_with_real_model(self, texts: list[str]) -> np.ndarray:
        if self._model is None:
            self._model = _load_real_model(self.model_name)
        # sentence-transformers encode 直接吐 np.ndarray
        vecs = self._model.encode(
            texts,
            convert_to_numpy=True,
            normalize_embeddings=True,   # unit vectors,便於 cosine similarity
        )
        return vecs.astype(np.float32)


# ============================================================
# Singleton(production 用,streamlit @cache_resource 友善)
# ============================================================
_pipeline_singleton: Optional[EmbeddingPipeline] = None


def get_embedding_pipeline(
    model_name: Optional[str] = None,
    embed_func: Optional[Callable] = None,
) -> EmbeddingPipeline:
    """取得 singleton EmbeddingPipeline。

    第一次呼叫時建立 instance,之後 cached 回傳同一個。

    Args:
        model_name: 第一次呼叫時可指定;之後忽略(已 singleton)
        embed_func: 同上。若想 reset singleton 用 `reset_embedding_pipeline()`
    """
    global _pipeline_singleton
    if _pipeline_singleton is None:
        name = model_name or os.getenv("GENBI_EMBEDDING_MODEL", DEFAULT_MODEL)
        _pipeline_singleton = EmbeddingPipeline(
            model_name=name,
            embed_func=embed_func,
        )
    return _pipeline_singleton


def reset_embedding_pipeline() -> None:
    """重置 singleton(主要 test 用)。"""
    global _pipeline_singleton
    _pipeline_singleton = None
