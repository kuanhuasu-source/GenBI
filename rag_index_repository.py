"""
rag_index_repository.py — v0.16.0+ (M6.1 Sprint 1 Day 1-2)

Vector index repository with backend abstraction。對齊 spec §6 + §8。

# Backend abstraction

`VectorIndexBackend` ABC 定義介面,3 個 implementation:
- `InMemoryBackend`:純 numpy + dict,零依賴,給 test / air-gap fallback 用
- `ChromaBackend`:production 用,embedded mode(無 server,純 file)
- `(future) QdrantBackend`:Phase 3 scale 用

# 5 個 logical index(spec §8)

```python
repo = RAGIndexRepository(
    backend_factory=lambda name: InMemoryBackend()  # test
    # or:
    # backend_factory=lambda name: ChromaBackend(path="./rag_indices", collection=name)
)
repo.add_doc("schema_index", doc_id="...", content="...", embedding=vec, metadata={"domain": "tflex"})
results = repo.search("schema_index", query_embedding=vec, top_k=5, filter={"domain": "tflex"})
```

# Doc schema(per-index 略有不同,共通欄位)

```python
{
    "doc_id": str,
    "content": str,                # 給 LLM prompt 用的文字
    "embedding": list[float],      # vector(dim 跟 embedding_model 一致)
    "metadata": {
        "domain": str,             # filter 用
        "embedding_model": str,    # 升級 model 時辨識舊版
        "created_at": str,
        ...                        # per-index 特有欄位
    },
}
```
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)


# ============================================================
# Constants(對齊 spec §8)
# ============================================================
KNOWN_INDEX_NAMES = (
    "schema_index",
    "kpi_index",
    "few_shot_index",
    "anti_pattern_index",
    "chart_recipe_index",
)


# ============================================================
# Result dataclass
# ============================================================
@dataclass
class SearchResult:
    """單筆 search 結果。"""
    doc_id: str
    content: str
    score: float                       # similarity(higher = closer)
    metadata: dict[str, Any] = field(default_factory=dict)


# ============================================================
# Backend abstraction
# ============================================================
class VectorIndexBackend(ABC):
    """單一 index 的後端介面。"""

    @abstractmethod
    def add(
        self, doc_id: str, content: str,
        embedding: np.ndarray, metadata: dict[str, Any] | None = None,
    ) -> None: ...

    @abstractmethod
    def search(
        self, query_embedding: np.ndarray, top_k: int = 5,
        filter: dict[str, Any] | None = None,
        min_score: float = -1.1,
    ) -> list[SearchResult]: ...

    @abstractmethod
    def delete(self, doc_id: str) -> bool: ...

    @abstractmethod
    def get(self, doc_id: str) -> Optional[SearchResult]: ...

    @abstractmethod
    def list_docs(self, limit: int = 100) -> list[SearchResult]: ...

    @abstractmethod
    def count(self) -> int: ...

    @abstractmethod
    def clear(self) -> None: ...


# ============================================================
# InMemoryBackend(test / air-gap fallback)
# ============================================================
class InMemoryBackend(VectorIndexBackend):
    """純 numpy + dict 實作,零依賴。

    - 適合 unit test
    - 適合 < 10K docs 的小 dataset
    - production 應該用 ChromaBackend
    """

    def __init__(self, name: str = "inmemory"):
        self.name = name
        # doc_id → (content, embedding, metadata)
        self._docs: dict[str, tuple[str, np.ndarray, dict]] = {}

    def add(self, doc_id, content, embedding, metadata=None):
        if doc_id in self._docs:
            # Upsert behavior(overwrite)
            logger.debug(f"InMemoryBackend.add: upsert doc {doc_id}")
        self._docs[doc_id] = (
            content,
            np.asarray(embedding, dtype=np.float32),
            dict(metadata or {}),
        )

    def search(self, query_embedding, top_k=5, filter=None, min_score=-1.1):
        # min_score=-1.1 預設 = "全收"(cosine 範圍 [-1,1]),caller 可調更嚴
        if not self._docs:
            return []
        q = np.asarray(query_embedding, dtype=np.float32)
        q_norm = float(np.linalg.norm(q))
        if q_norm < 1e-9:
            return []

        results: list[SearchResult] = []
        for doc_id, (content, emb, md) in self._docs.items():
            # Filter
            if filter and not _matches_filter(md, filter):
                continue
            # Cosine similarity(假設 vectors 已 normalize,但保險再 norm 一次)
            e_norm = float(np.linalg.norm(emb))
            if e_norm < 1e-9:
                continue
            score = float(np.dot(q, emb) / (q_norm * e_norm))
            if score < min_score:
                continue
            results.append(SearchResult(
                doc_id=doc_id, content=content,
                score=round(score, 6),
                metadata=dict(md),
            ))
        # Top-K by score desc
        results.sort(key=lambda r: -r.score)
        return results[:top_k]

    def delete(self, doc_id):
        return self._docs.pop(doc_id, None) is not None

    def get(self, doc_id):
        d = self._docs.get(doc_id)
        if d is None:
            return None
        content, emb, md = d
        return SearchResult(
            doc_id=doc_id, content=content, score=1.0,
            metadata=dict(md),
        )

    def list_docs(self, limit=100):
        out = []
        for doc_id, (content, _, md) in list(self._docs.items())[:limit]:
            out.append(SearchResult(
                doc_id=doc_id, content=content, score=1.0,
                metadata=dict(md),
            ))
        return out

    def count(self):
        return len(self._docs)

    def clear(self):
        self._docs.clear()


# ============================================================
# ChromaBackend(production)
# ============================================================
class ChromaBackend(VectorIndexBackend):
    """Chroma embedded mode backend。

    - 純 file-based,無 server process
    - data 寫在 `<persist_directory>/chroma.db`
    - production 用
    """

    def __init__(
        self,
        collection_name: str,
        persist_directory: str | Path,
    ):
        try:
            import chromadb
        except ImportError as e:
            raise ImportError(
                "chromadb not installed. Run `pip install chromadb` to "
                "use ChromaBackend. Test path can use InMemoryBackend instead."
            ) from e

        self.collection_name = collection_name
        self.persist_directory = str(persist_directory)
        Path(self.persist_directory).mkdir(parents=True, exist_ok=True)

        self._client = chromadb.PersistentClient(path=self.persist_directory)
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},   # use cosine similarity
        )

    def add(self, doc_id, content, embedding, metadata=None):
        # Chroma upsert
        self._collection.upsert(
            ids=[doc_id],
            documents=[content],
            embeddings=[np.asarray(embedding).tolist()],
            metadatas=[dict(metadata or {})] if metadata else None,
        )

    def search(self, query_embedding, top_k=5, filter=None, min_score=-1.1):
        # Chroma 用 distance(越小越近,cosine distance = 1 - similarity)
        kwargs: dict[str, Any] = {
            "query_embeddings": [np.asarray(query_embedding).tolist()],
            "n_results": top_k,
        }
        if filter:
            kwargs["where"] = filter   # Chroma filter syntax

        try:
            raw = self._collection.query(**kwargs)
        except Exception as e:
            logger.warning(f"ChromaBackend.search error: {e}")
            return []

        # Parse Chroma response
        ids = raw.get("ids", [[]])[0] if raw.get("ids") else []
        docs = raw.get("documents", [[]])[0] if raw.get("documents") else []
        dists = raw.get("distances", [[]])[0] if raw.get("distances") else []
        mds = raw.get("metadatas", [[]])[0] if raw.get("metadatas") else []
        results = []
        for i, doc_id in enumerate(ids):
            distance = float(dists[i]) if i < len(dists) else 1.0
            score = 1.0 - distance   # cosine distance → similarity
            if score < min_score:
                continue
            results.append(SearchResult(
                doc_id=doc_id,
                content=docs[i] if i < len(docs) else "",
                score=round(score, 6),
                metadata=dict(mds[i]) if i < len(mds) and mds[i] else {},
            ))
        return results

    def delete(self, doc_id):
        try:
            self._collection.delete(ids=[doc_id])
            return True
        except Exception:
            return False

    def get(self, doc_id):
        try:
            raw = self._collection.get(ids=[doc_id], include=["documents", "metadatas"])
        except Exception:
            return None
        ids = raw.get("ids", [])
        if not ids:
            return None
        return SearchResult(
            doc_id=ids[0],
            content=raw.get("documents", [""])[0] or "",
            score=1.0,
            metadata=dict(raw.get("metadatas", [{}])[0] or {}),
        )

    def list_docs(self, limit=100):
        try:
            raw = self._collection.get(limit=limit, include=["documents", "metadatas"])
        except Exception:
            return []
        results = []
        ids = raw.get("ids", [])
        docs = raw.get("documents", [])
        mds = raw.get("metadatas", [])
        for i, doc_id in enumerate(ids):
            results.append(SearchResult(
                doc_id=doc_id,
                content=docs[i] if i < len(docs) else "",
                score=1.0,
                metadata=dict(mds[i]) if i < len(mds) and mds[i] else {},
            ))
        return results

    def count(self):
        try:
            return self._collection.count()
        except Exception:
            return 0

    def clear(self):
        # 刪整個 collection 再重建
        try:
            self._client.delete_collection(self.collection_name)
        except Exception:
            pass
        self._collection = self._client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"},
        )


# ============================================================
# Filter helper(InMemoryBackend 用,模擬 Chroma where syntax 子集)
# ============================================================
def _matches_filter(metadata: dict, filter: dict) -> bool:
    """簡化版 filter — 只支援 exact match 跟 $in。"""
    for k, v in filter.items():
        actual = metadata.get(k)
        if isinstance(v, dict) and "$in" in v:
            if actual not in v["$in"]:
                return False
        else:
            if actual != v:
                return False
    return True


# ============================================================
# Repository(把多個 index 集中管)
# ============================================================
class RAGIndexRepository:
    """管 5 個 index(schema / kpi / few_shot / anti_pattern / chart_recipe)。

    Backend 透過 factory 注入 — production 給 Chroma factory,test 給 InMemory factory。
    """

    def __init__(
        self,
        backend_factory: Callable[[str], VectorIndexBackend],
        index_names: tuple[str, ...] = KNOWN_INDEX_NAMES,
    ):
        """
        Args:
            backend_factory:`def factory(index_name) -> VectorIndexBackend`
                每個 index_name 都 invoke 一次拿到對應 backend
            index_names:該 repo 管理的 index list
        """
        self.index_names = index_names
        self._backends: dict[str, VectorIndexBackend] = {
            name: backend_factory(name) for name in index_names
        }

    def _backend(self, index_name: str) -> VectorIndexBackend:
        if index_name not in self._backends:
            raise KeyError(
                f"Unknown index `{index_name}`. Available: {sorted(self._backends.keys())}"
            )
        return self._backends[index_name]

    # ============================================================
    # Per-index API(thin wrappers)
    # ============================================================
    def add_doc(
        self, index_name: str, doc_id: str, content: str,
        embedding: np.ndarray, metadata: dict | None = None,
    ) -> None:
        self._backend(index_name).add(doc_id, content, embedding, metadata)

    def search(
        self, index_name: str, query_embedding: np.ndarray, top_k: int = 5,
        filter: dict | None = None, min_score: float = -1.1,
    ) -> list[SearchResult]:
        return self._backend(index_name).search(
            query_embedding, top_k=top_k, filter=filter, min_score=min_score,
        )

    def delete_doc(self, index_name: str, doc_id: str) -> bool:
        return self._backend(index_name).delete(doc_id)

    def get_doc(self, index_name: str, doc_id: str) -> Optional[SearchResult]:
        return self._backend(index_name).get(doc_id)

    def list_docs(self, index_name: str, limit: int = 100) -> list[SearchResult]:
        return self._backend(index_name).list_docs(limit)

    def count(self, index_name: str) -> int:
        return self._backend(index_name).count()

    def clear(self, index_name: str) -> None:
        self._backend(index_name).clear()

    def count_all(self) -> dict[str, int]:
        return {name: self._backend(name).count() for name in self.index_names}


# ============================================================
# Factory functions(便利)
# ============================================================
def make_inmemory_factory() -> Callable[[str], VectorIndexBackend]:
    """Test / air-gap fallback factory。"""
    return lambda name: InMemoryBackend(name=name)


def make_chroma_factory(persist_directory: str | Path) -> Callable[[str], VectorIndexBackend]:
    """Production factory。所有 index 共用同一 persist_directory(分 collection 隔離)。"""
    return lambda name: ChromaBackend(
        collection_name=name, persist_directory=persist_directory,
    )
