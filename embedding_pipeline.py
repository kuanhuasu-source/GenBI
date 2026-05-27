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

# v0.16.1+:已知 embedding model 的 dim 對照表
# 換 model 時若沒明示 dim,fallback 查這表
_KNOWN_MODEL_DIMS: dict[str, int] = {
    # sentence-transformers
    "sentence-transformers/all-MiniLM-L6-v2": 384,
    "all-MiniLM-L6-v2": 384,
    # bge family(Ollama / vLLM 經常 serve 的)
    "bge-m3": 1024,
    "bge-m3:latest": 1024,
    "bge-large-en-v1.5": 1024,
    "BAAI/bge-m3": 1024,
    "BAAI/bge-large-en-v1.5": 1024,
    "BAAI/bge-base-zh": 768,
}


def _infer_dim(model_name: str) -> int:
    """從 model_name 推 vector dim。未知模型 fallback 到 384(warn)。"""
    # 完整 match
    if model_name in _KNOWN_MODEL_DIMS:
        return _KNOWN_MODEL_DIMS[model_name]
    # 簡名 match(去掉 path / tag)
    short = model_name.split("/")[-1].split(":")[0]
    if short in _KNOWN_MODEL_DIMS:
        return _KNOWN_MODEL_DIMS[short]
    logger.warning(
        f"Unknown embedding model {model_name!r} — dim defaults to {DEFAULT_DIM}. "
        f"If wrong dim, set explicit `dim=` or extend _KNOWN_MODEL_DIMS."
    )
    return DEFAULT_DIM


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
# v0.16.1+:HTTP-based embedder(Ollama / vLLM / OpenAI 通用)
# ============================================================
def make_http_embedder(
    api_url: str,
    model: str,
    api_key: str = "ollama",
    timeout_s: float = 30.0,
    batch_size: int = 64,
) -> Callable[[list[str]], np.ndarray]:
    """產一個 HTTP-based embedder,透過 OpenAI-compatible
    `POST /v1/embeddings` 拿 vector。

    這個介面同時相容:
    - **Ollama**(dev):`ollama pull bge-m3` 後跑 `ollama serve`,
       endpoint = `http://localhost:11434/v1/embeddings`
    - **vLLM**(production):啟動 `--task embed --served-model-name bge-m3`,
       endpoint = `http://<host>:8000/v1/embeddings`
    - **OpenAI**(cloud):endpoint = `https://api.openai.com/v1/embeddings`

    Args:
        api_url:完整 `/v1/embeddings` endpoint URL
        model:server 端的 model 名稱(Ollama tag 或 vLLM served-model-name)
        api_key:bearer token(Ollama 可任意值,vLLM 看部署設定)
        timeout_s:單次 HTTP timeout
        batch_size:同時送幾筆 text(太大會超 server context;太小 latency 高)

    Returns:
        callable(texts: list[str]) -> np.ndarray of shape (len(texts), dim)
        Embedding 已 L2-normalize(對齊 InMemoryBackend cosine 計算假設)
    """
    # Lazy import — openai package 已在 requirements.txt(LLM client 也用)
    from openai import OpenAI

    client = OpenAI(
        base_url=api_url.replace("/embeddings", ""),  # OpenAI client 要 base
        api_key=api_key,
        timeout=timeout_s,
    )

    def _http_embed(texts: list[str]) -> np.ndarray:
        if not texts:
            # dim 不知道(沒打過 server),回 (0, 0) — caller 該過濾掉空 list
            return np.zeros((0, 0), dtype=np.float32)

        all_vecs: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            try:
                resp = client.embeddings.create(model=model, input=batch)
            except Exception as e:
                raise RuntimeError(
                    f"HTTP embedder failed(model={model}, url={api_url}): {e}"
                ) from e
            # resp.data 是 list[Embedding],各有 .embedding(list[float])+ .index
            # 按 index 排序保證跟 input 對齊(server 通常已對齊但保險)
            ordered = sorted(resp.data, key=lambda d: d.index)
            for d in ordered:
                all_vecs.append(d.embedding)

        arr = np.asarray(all_vecs, dtype=np.float32)
        # L2 normalize — bge-m3 / OpenAI 多半已 normalize,但保險再做
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms < 1e-9] = 1.0    # 避免 div by 0
        return arr / norms

    return _http_embed


# ============================================================
# 主類別
# ============================================================
class EmbeddingPipeline:
    """統一 embedding 介面 — production 用 sentence-transformers,test 用 fake。"""

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        dim: int | None = None,
        embed_func: Optional[Callable[[list[str]], np.ndarray]] = None,
    ):
        """
        Args:
            model_name: 模型名稱,production 用(default `all-MiniLM-L6-v2`)
            dim: 向量維度。None → 從 `_KNOWN_MODEL_DIMS` 查表自動推。
                明示傳 dim 會 override 推測值。
            embed_func: 如果提供,直接用此 callable 取代真實 model。
                測試時可傳入 `make_deterministic_fake_embedder()`,
                或 production 注入 `make_http_embedder(...)`。
        """
        self.model_name = model_name
        # v0.16.1+:dim 從 model_name 自動推(bge-m3 → 1024 etc.)
        self.dim = dim if dim is not None else _infer_dim(model_name)
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

    v0.16.1+ backend routing(env):
        GENBI_EMBEDDING_BACKEND=local(default)→ sentence-transformers 本地
        GENBI_EMBEDDING_BACKEND=http  → OpenAI-compatible /v1/embeddings
            需要 GENBI_EMBEDDING_API_URL + GENBI_EMBEDDING_MODEL
            (Ollama dev / vLLM prod 同一條路徑)

    Args:
        model_name: 第一次呼叫時可指定;之後忽略(已 singleton)
        embed_func: 同上。若想 reset singleton 用 `reset_embedding_pipeline()`
    """
    global _pipeline_singleton
    if _pipeline_singleton is None:
        # Caller-provided embed_func 永遠優先(test path)
        if embed_func is not None:
            name = model_name or os.getenv("GENBI_EMBEDDING_MODEL", DEFAULT_MODEL)
            _pipeline_singleton = EmbeddingPipeline(
                model_name=name, embed_func=embed_func,
            )
            return _pipeline_singleton

        backend = os.getenv("GENBI_EMBEDDING_BACKEND", "local").lower()
        name = model_name or os.getenv("GENBI_EMBEDDING_MODEL", DEFAULT_MODEL)

        if backend == "http":
            # Ollama / vLLM / OpenAI 走同一條
            api_url = os.getenv(
                "GENBI_EMBEDDING_API_URL",
                "http://localhost:11434/v1/embeddings",
            )
            api_key = os.getenv("GENBI_EMBEDDING_API_KEY", "ollama")
            try:
                batch_size = int(os.getenv("GENBI_EMBEDDING_BATCH_SIZE", "64"))
            except ValueError:
                batch_size = 64
            try:
                timeout_s = float(os.getenv("GENBI_EMBEDDING_TIMEOUT_S", "30"))
            except ValueError:
                timeout_s = 30.0
            logger.info(
                f"Embedding backend = http · url={api_url} · model={name} · "
                f"dim={_infer_dim(name)}"
            )
            http_fn = make_http_embedder(
                api_url=api_url, model=name, api_key=api_key,
                timeout_s=timeout_s, batch_size=batch_size,
            )
            _pipeline_singleton = EmbeddingPipeline(
                model_name=name, embed_func=http_fn,
            )
        else:
            # backend == "local"(default,backward compat v0.16.0)
            logger.info(
                f"Embedding backend = local · model={name} · "
                f"dim={_infer_dim(name)}"
            )
            _pipeline_singleton = EmbeddingPipeline(model_name=name)

    return _pipeline_singleton


def reset_embedding_pipeline() -> None:
    """重置 singleton(主要 test 用)。"""
    global _pipeline_singleton
    _pipeline_singleton = None
