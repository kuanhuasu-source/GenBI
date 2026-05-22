"""
upload_repository.py — v0.12.0+

Upload Workspace 的 MongoDB CRUD 層 — 管理 3 個 collection:
  - `uploaded_datasets`     dataset 主檔(owner / file metadata / status)
  - `upload_tables`         dataset 內每張 table / sheet 的描述(row/col count / parquet path)
  - `upload_profiles`       physical data profile(per-column stats / warnings)

# 設計重點

- **不接 embedded fallback**:upload 路徑天生需要 MongoDB(沒 DB 沒地方存使用者資料)。
  DB 連不上時 caller 應該 raise,讓 UI 顯示「請啟動 MongoDB」。
- **沿用既有 collection 命名前綴**:`upload_*` 跟既有 `tflex_*` / `prompt_*` 並排,
  做 backup / dump 時容易過濾。
- **不寫 cache**:profile / table 都是 immutable snapshot(每次 upload 一份新的),
  跟 prompt template 不同,不需要 TTL cache。

# Schema 樣本(完整 spec 見 GenBI_Upload_Workspace_System_Extension_Spec §8.1-§8.3)

```python
# uploaded_datasets
{
    "_id": "upload_20260522135530_a8c3f2",  # 也是 dataset_id
    "dataset_name": "project_leadtime.csv",
    "owner": "alan",
    "source_type": "file_upload",
    "file": {
        "original_filename": "project_leadtime.csv",
        "stored_path": "uploads/upload_20260522135530_a8c3f2/source.csv",
        "file_type": "csv",
        "file_size_bytes": 123456,
        "sha256": "...",
    },
    "status": "uploaded" | "parsing" | "parsed" | "profiled" | "error",
    "active_metadata_version": None,  # Milestone 2 之後才有值
    "error_message": None,
    "created_at": ISODate,
    "updated_at": ISODate,
}

# upload_tables
{
    "_id": ObjectId,
    "dataset_id": "upload_20260522135530_a8c3f2",
    "table_id": "sheet1",
    "table_name": "Sheet1",
    "row_count": 120,
    "column_count": 8,
    "grain_guess": None,  # M2 semantic profiler 才填
    "storage": {
        "format": "parquet",
        "path": "uploads/upload_20260522135530_a8c3f2/sheet1.parquet",
    },
    "created_at": ISODate,
}

# upload_profiles
{
    "_id": ObjectId,
    "dataset_id": "upload_20260522135530_a8c3f2",
    "profile_version": 1,
    "tables": [
        {
            "table_id": "sheet1",
            "row_count": 120,
            "columns": [
                {"name": "project_id", "physical_type": "string", ...},
                {"name": "leadtime", "physical_type": "number", "min": 8, ...},
            ],
        }
    ],
    "created_at": ISODate,
}
```
"""

from __future__ import annotations

import datetime as _dt
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


# Collection 名稱(可在 caller 覆寫,例如多環境共用 DB 時加前綴)
DEFAULT_DATASETS_COLLECTION = "uploaded_datasets"
DEFAULT_TABLES_COLLECTION = "upload_tables"
DEFAULT_PROFILES_COLLECTION = "upload_profiles"
DEFAULT_METADATA_VERSIONS_COLLECTION = "upload_metadata_versions"
DEFAULT_USER_CORRECTIONS_COLLECTION = "upload_user_corrections"


class UploadRepository:
    """Upload Workspace 的 CRUD 層 — 必須接 MongoDB(無 embedded fallback)。

    使用方式:
        from upload_repository import UploadRepository
        repo = UploadRepository(mongo_db)
        repo.ensure_indexes()  # idempotent
        dataset_id = repo.create_dataset({...})
        repo.update_status(dataset_id, "parsed")
        ...
    """

    def __init__(
        self,
        mongo_db,
        datasets_collection: str = DEFAULT_DATASETS_COLLECTION,
        tables_collection: str = DEFAULT_TABLES_COLLECTION,
        profiles_collection: str = DEFAULT_PROFILES_COLLECTION,
        metadata_versions_collection: str = DEFAULT_METADATA_VERSIONS_COLLECTION,
        user_corrections_collection: str = DEFAULT_USER_CORRECTIONS_COLLECTION,
    ):
        """
        Args:
            mongo_db: pymongo Database instance(必須能寫,不接受 None)
            其他 collection 名:可由 caller 覆寫(多環境共用 DB 時加前綴)。
        """
        if mongo_db is None:
            raise ValueError(
                "UploadRepository requires a MongoDB connection — "
                "upload-driven path 沒 DB 沒地方存資料"
            )
        self.db = mongo_db
        self._datasets = mongo_db[datasets_collection]
        self._tables = mongo_db[tables_collection]
        self._profiles = mongo_db[profiles_collection]
        # M2+: metadata version + corrections
        self._metadata_versions = mongo_db[metadata_versions_collection]
        self._user_corrections = mongo_db[user_corrections_collection]

    # ============================================================
    # Index management
    # ============================================================
    def ensure_indexes(self) -> None:
        """建立必要 index(idempotent — 重複呼叫安全)。"""
        # uploaded_datasets
        self._datasets.create_index([("owner", 1), ("created_at", -1)])
        self._datasets.create_index([("status", 1)])
        # upload_tables
        self._tables.create_index(
            [("dataset_id", 1), ("table_id", 1)], unique=True,
        )
        # upload_profiles
        self._profiles.create_index(
            [("dataset_id", 1), ("profile_version", -1)],
        )
        # M2+: metadata_versions
        self._metadata_versions.create_index(
            [("dataset_id", 1), ("version", -1)],
        )
        self._metadata_versions.create_index(
            [("dataset_id", 1), ("is_active", 1)],
        )
        # M2+: user_corrections
        self._user_corrections.create_index(
            [("dataset_id", 1), ("created_at", -1)],
        )

    # ============================================================
    # uploaded_datasets CRUD
    # ============================================================
    def create_dataset(self, dataset_doc: dict) -> str:
        """寫入新的 uploaded_datasets 文件。

        Args:
            dataset_doc: 必須含 `_id`(=dataset_id)、`dataset_name`、`owner`、
                `source_type`、`file`、`status`,其他欄位 optional。

        Returns:
            dataset_id (str)
        """
        if "_id" not in dataset_doc:
            raise ValueError("create_dataset: dataset_doc 必須帶 `_id`")
        now = _dt.datetime.now(_dt.timezone.utc)
        dataset_doc.setdefault("created_at", now)
        dataset_doc["updated_at"] = now
        self._datasets.insert_one(dataset_doc)
        return dataset_doc["_id"]

    def get_dataset(self, dataset_id: str) -> Optional[dict]:
        return self._datasets.find_one({"_id": dataset_id})

    def list_datasets(
        self,
        owner: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        """列出 dataset,按 created_at 倒序。"""
        query: dict[str, Any] = {}
        if owner:
            query["owner"] = owner
        if status:
            query["status"] = status
        cursor = (
            self._datasets.find(query)
            .sort("created_at", -1)
            .limit(limit)
        )
        return list(cursor)

    def update_status(
        self,
        dataset_id: str,
        status: str,
        error_message: Optional[str] = None,
        **extra_fields,
    ) -> bool:
        """改 dataset.status,同時可帶其他欄位更新(例如 active_metadata_version)。"""
        update_doc: dict[str, Any] = {
            "status": status,
            "updated_at": _dt.datetime.now(_dt.timezone.utc),
        }
        if error_message is not None:
            update_doc["error_message"] = error_message
        update_doc.update(extra_fields)
        result = self._datasets.update_one(
            {"_id": dataset_id}, {"$set": update_doc},
        )
        return result.modified_count > 0

    def delete_dataset(self, dataset_id: str) -> bool:
        """硬刪除 dataset 及相關 table / profile 記錄(filesystem 由 caller 清)。"""
        self._tables.delete_many({"dataset_id": dataset_id})
        self._profiles.delete_many({"dataset_id": dataset_id})
        result = self._datasets.delete_one({"_id": dataset_id})
        return result.deleted_count > 0

    # ============================================================
    # upload_tables CRUD
    # ============================================================
    def create_table(self, table_doc: dict) -> str:
        """寫入新的 upload_tables 文件。

        Args:
            table_doc: 必須含 `dataset_id`、`table_id`、`table_name`、
                `row_count`、`column_count`、`storage`。

        Returns:
            table_id (str)
        """
        required = {"dataset_id", "table_id", "table_name", "row_count",
                    "column_count", "storage"}
        missing = required - set(table_doc.keys())
        if missing:
            raise ValueError(f"create_table: 缺欄位 {missing}")
        table_doc.setdefault("created_at", _dt.datetime.now(_dt.timezone.utc))
        self._tables.insert_one(table_doc)
        return table_doc["table_id"]

    def list_tables(self, dataset_id: str) -> list[dict]:
        return list(
            self._tables.find({"dataset_id": dataset_id})
            .sort("table_id", 1)
        )

    def get_table(self, dataset_id: str, table_id: str) -> Optional[dict]:
        return self._tables.find_one(
            {"dataset_id": dataset_id, "table_id": table_id},
        )

    # ============================================================
    # upload_profiles CRUD
    # ============================================================
    def save_profile(
        self,
        dataset_id: str,
        profile: dict,
    ) -> int:
        """寫入新 profile,自動 increment profile_version。

        Args:
            profile: 必須含 `tables` 子欄位,format 見 spec §8.3。

        Returns:
            新的 profile_version (1-based int)
        """
        latest = (
            self._profiles.find({"dataset_id": dataset_id})
            .sort("profile_version", -1)
            .limit(1)
        )
        latest_list = list(latest)
        next_version = (latest_list[0]["profile_version"] + 1
                        if latest_list else 1)

        doc = {
            "dataset_id": dataset_id,
            "profile_version": next_version,
            "tables": profile.get("tables", []),
            "created_at": _dt.datetime.now(_dt.timezone.utc),
        }
        self._profiles.insert_one(doc)
        return next_version

    def get_latest_profile(self, dataset_id: str) -> Optional[dict]:
        result = (
            self._profiles.find({"dataset_id": dataset_id})
            .sort("profile_version", -1)
            .limit(1)
        )
        result_list = list(result)
        return result_list[0] if result_list else None

    def list_profile_versions(self, dataset_id: str) -> list[dict]:
        """列出歷次 profile(從新到舊)。"""
        return list(
            self._profiles.find({"dataset_id": dataset_id})
            .sort("profile_version", -1)
        )

    # ============================================================
    # upload_metadata_versions CRUD(M2+)
    # ============================================================
    def save_metadata_version(
        self,
        dataset_id: str,
        metadata: dict,
        confirmation_status: str = "draft",
        confirmed_by: Optional[str] = None,
        notes: str = "",
        activate: bool = True,
    ) -> int:
        """寫入新 metadata version,自動 increment version。

        Args:
            metadata: 完整 GenBI-compatible metadata dict
                (含 dataset_id / source_type / business_context / collections
                 / kpi_definitions / data_limitations 等)
            confirmation_status: 'draft' | 'confirmed' | 'needs_review'
            confirmed_by: 確認者(`confirmation_status='confirmed'` 才有意義)
            notes: 此版改了什麼
            activate: True 時將此版 mark `is_active=True`,
                並把同 dataset_id 既有 active 版 mark False

        Returns:
            新的 version number (1-based)
        """
        latest = (
            self._metadata_versions.find({"dataset_id": dataset_id})
            .sort("version", -1).limit(1)
        )
        latest_list = list(latest)
        next_version = (latest_list[0]["version"] + 1
                        if latest_list else 1)

        now = _dt.datetime.now(_dt.timezone.utc)
        doc = {
            "dataset_id": dataset_id,
            "version": next_version,
            "is_active": bool(activate),
            "confirmation_status": confirmation_status,
            "confirmed_by": confirmed_by,
            "confirmed_at": now if confirmation_status == "confirmed" else None,
            "metadata": metadata,
            "notes": notes,
            "created_at": now,
        }
        # 若 activate=True,先把舊版 deactivate
        if activate:
            self._metadata_versions.update_many(
                {"dataset_id": dataset_id, "is_active": True},
                {"$set": {"is_active": False}},
            )
        self._metadata_versions.insert_one(doc)

        # 同步寫入 uploaded_datasets.active_metadata_version
        if activate:
            self.update_status(
                dataset_id,
                status="profiled",  # 維持 profiled 狀態(M3 改成 confirmed)
                active_metadata_version=next_version,
            )
        return next_version

    def get_active_metadata(self, dataset_id: str) -> Optional[dict]:
        """讀當前 active 的 metadata version 文件(完整 doc,非僅 metadata 子欄)。"""
        return self._metadata_versions.find_one({
            "dataset_id": dataset_id,
            "is_active": True,
        })

    def get_metadata_version(
        self,
        dataset_id: str,
        version: int,
    ) -> Optional[dict]:
        return self._metadata_versions.find_one({
            "dataset_id": dataset_id,
            "version": version,
        })

    def list_metadata_versions(self, dataset_id: str) -> list[dict]:
        """列出 dataset 的所有 metadata version(新到舊)。"""
        return list(
            self._metadata_versions.find({"dataset_id": dataset_id})
            .sort("version", -1)
        )

    def activate_metadata_version(self, dataset_id: str, version: int) -> bool:
        """切換 active version(舊版 deactivate,目標版 activate)。"""
        target = self.get_metadata_version(dataset_id, version)
        if not target:
            return False
        self._metadata_versions.update_many(
            {"dataset_id": dataset_id, "is_active": True},
            {"$set": {"is_active": False}},
        )
        result = self._metadata_versions.update_one(
            {"dataset_id": dataset_id, "version": version},
            {"$set": {"is_active": True}},
        )
        if result.modified_count > 0:
            self.update_status(
                dataset_id, status="profiled",
                active_metadata_version=version,
            )
            return True
        return False

    # ============================================================
    # upload_user_corrections CRUD(M2+)
    # ============================================================
    def save_corrections(
        self,
        dataset_id: str,
        metadata_version_before: int,
        metadata_version_after: int,
        corrections: list[dict],
        user: str,
    ) -> str:
        """寫使用者修正紀錄(audit log)。

        Args:
            corrections: list of {target, old_value, new_value, reason}

        Returns:
            插入的 doc 的 _id 字串
        """
        doc = {
            "dataset_id": dataset_id,
            "metadata_version_before": metadata_version_before,
            "metadata_version_after": metadata_version_after,
            "corrections": corrections,
            "created_by": user,
            "created_at": _dt.datetime.now(_dt.timezone.utc),
        }
        result = self._user_corrections.insert_one(doc)
        return str(result.inserted_id)

    def list_corrections(self, dataset_id: str) -> list[dict]:
        """列出 dataset 的修正歷史(新到舊)。"""
        return list(
            self._user_corrections.find({"dataset_id": dataset_id})
            .sort("created_at", -1)
        )


def generate_dataset_id() -> str:
    """產生新的 dataset_id — 格式:`upload_YYYYMMDDHHMMSS_<6hex>`。

    timestamp 確保按建立時間排序,6 hex 避免同秒衝突。
    """
    import secrets
    ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d%H%M%S")
    suffix = secrets.token_hex(3)  # 6 hex chars
    return f"upload_{ts}_{suffix}"
