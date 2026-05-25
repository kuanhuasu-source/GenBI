"""
rag_index_versions_repository.py — v0.16.0+ (M6.1 Sprint 1 Day 3)

`rag_index_versions` MongoDB collection CRUD — 追蹤每 vector index 的
version / champion / challenger / promotion log。對齊 spec §7.1。

# Schema

```python
{
    "_id": ObjectId,
    "index_name": "schema_index" | "kpi_index" | ...,
    "version": int,
    "embedding_model": str,
    "embedding_dim": int,
    "doc_count": int,
    "status": "champion" | "challenger" | "deprecated",
    "promoted_at": ISODate | None,
    "promoted_by": "auto" | "alice",
    "promotion_reason": str | None,
    "rollback_history": list,
    "metrics": dict,           # 例 {"pass_rate": 0.84, "avg_latency_ms": 142}
    "created_at": ISODate,
    "updated_at": ISODate,
}
```

# 用法

```python
from rag_index_versions_repository import RAGIndexVersionsRepository

repo = RAGIndexVersionsRepository(mongo_db)
repo.ensure_indexes()

# 註冊新 version(default 進 challenger)
v_id = repo.create_version(
    index_name="schema_index",
    embedding_model="all-MiniLM-L6-v2",
    embedding_dim=384,
    doc_count=120,
    status="challenger",
)

# Promote to champion
repo.promote(v_id, by="auto", reason="pass rate +8% p<0.05")

# Query 當前 champion
champion = repo.get_champion("schema_index")
```
"""

from __future__ import annotations

import datetime as _dt
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


DEFAULT_COLLECTION = "rag_index_versions"


class RAGIndexVersionsRepository:
    """`rag_index_versions` MongoDB collection 的 CRUD 層。"""

    def __init__(self, mongo_db, collection: str = DEFAULT_COLLECTION):
        if mongo_db is None:
            raise ValueError(
                "RAGIndexVersionsRepository requires MongoDB connection."
            )
        self.db = mongo_db
        self._coll = mongo_db[collection]

    def ensure_indexes(self) -> None:
        self._coll.create_index([("index_name", 1), ("version", -1)])
        self._coll.create_index([("index_name", 1), ("status", 1)])

    # ============================================================
    # Create / read
    # ============================================================
    def create_version(
        self,
        index_name: str,
        embedding_model: str,
        embedding_dim: int,
        doc_count: int = 0,
        status: str = "challenger",
        metrics: dict | None = None,
        notes: str = "",
    ) -> Any:
        """寫一個新 version doc。Returns inserted_id。"""
        latest = self._latest_version_num(index_name)
        next_v = latest + 1 if latest else 1
        now = _dt.datetime.now(_dt.timezone.utc)
        doc = {
            "index_name": index_name,
            "version": next_v,
            "embedding_model": embedding_model,
            "embedding_dim": embedding_dim,
            "doc_count": doc_count,
            "status": status,
            "promoted_at": None,
            "promoted_by": None,
            "promotion_reason": None,
            "rollback_history": [],
            "metrics": dict(metrics or {}),
            "notes": notes,
            "created_at": now,
            "updated_at": now,
        }
        result = self._coll.insert_one(doc)
        return result.inserted_id

    def _latest_version_num(self, index_name: str) -> int:
        latest = (
            self._coll.find({"index_name": index_name})
            .sort("version", -1).limit(1)
        )
        ls = list(latest)
        return ls[0]["version"] if ls else 0

    def get_version(self, index_name: str, version: int) -> Optional[dict]:
        return self._coll.find_one({"index_name": index_name,
                                     "version": version})

    def get_champion(self, index_name: str) -> Optional[dict]:
        return self._coll.find_one({"index_name": index_name,
                                     "status": "champion"})

    def get_challenger(self, index_name: str) -> Optional[dict]:
        return self._coll.find_one({"index_name": index_name,
                                     "status": "challenger"})

    def list_versions(self, index_name: str | None = None,
                       include_deprecated: bool = False) -> list[dict]:
        q: dict[str, Any] = {}
        if index_name:
            q["index_name"] = index_name
        if not include_deprecated:
            q["status"] = {"$ne": "deprecated"}
        return list(self._coll.find(q).sort([
            ("index_name", 1), ("version", -1),
        ]))

    # ============================================================
    # Promote / Rollback
    # ============================================================
    def promote(
        self,
        version_doc_id: Any,
        by: str = "auto",
        reason: str = "",
        metrics: dict | None = None,
    ) -> bool:
        """把指定 version 升為 champion,既有 champion 降 deprecated。

        Returns:
            True if successful
        """
        target = self._coll.find_one({"_id": version_doc_id})
        if not target:
            return False
        index_name = target["index_name"]

        # 既有 champion → deprecated
        old_champion = self.get_champion(index_name)
        if old_champion and old_champion["_id"] != version_doc_id:
            self._coll.update_one(
                {"_id": old_champion["_id"]},
                {"$set": {
                    "status": "deprecated",
                    "updated_at": _dt.datetime.now(_dt.timezone.utc),
                }},
            )

        # Target → champion
        update_doc: dict[str, Any] = {
            "status": "champion",
            "promoted_at": _dt.datetime.now(_dt.timezone.utc),
            "promoted_by": by,
            "promotion_reason": reason,
            "updated_at": _dt.datetime.now(_dt.timezone.utc),
        }
        if metrics is not None:
            update_doc["metrics"] = metrics
        result = self._coll.update_one(
            {"_id": version_doc_id}, {"$set": update_doc},
        )
        return result.modified_count > 0

    def rollback_champion(
        self, index_name: str, to_version: int,
        by: str = "auto", reason: str = "",
    ) -> bool:
        """Rollback champion 到指定舊 version。

        Returns:
            True if successful
        """
        target = self.get_version(index_name, to_version)
        if not target:
            return False
        # 既有 champion → deprecated + rollback_history
        current = self.get_champion(index_name)
        if current and current["_id"] != target["_id"]:
            self._coll.update_one(
                {"_id": current["_id"]},
                {
                    "$set": {
                        "status": "deprecated",
                        "updated_at": _dt.datetime.now(_dt.timezone.utc),
                    },
                    "$push": {"rollback_history": {
                        "at": _dt.datetime.now(_dt.timezone.utc),
                        "by": by,
                        "reason": reason,
                        "from_version": current["version"],
                        "to_version": to_version,
                    }},
                },
            )
        # Target → champion
        return self.promote(target["_id"], by=by,
                              reason=f"Rollback: {reason}")

    def deprecate(self, version_doc_id: Any) -> bool:
        result = self._coll.update_one(
            {"_id": version_doc_id},
            {"$set": {
                "status": "deprecated",
                "updated_at": _dt.datetime.now(_dt.timezone.utc),
            }},
        )
        return result.modified_count > 0

    # ============================================================
    # Metrics
    # ============================================================
    def update_metrics(self, version_doc_id: Any, metrics: dict) -> bool:
        result = self._coll.update_one(
            {"_id": version_doc_id},
            {"$set": {
                "metrics": metrics,
                "updated_at": _dt.datetime.now(_dt.timezone.utc),
            }},
        )
        return result.modified_count > 0
