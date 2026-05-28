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
DEFAULT_ANALYSIS_SESSIONS_COLLECTION = "analysis_sessions"
DEFAULT_ANALYSIS_ASSETS_COLLECTION = "analysis_assets"
# v0.18 M2:relationship candidates per spec §5.3
DEFAULT_RELATIONSHIP_CANDIDATES_COLLECTION = "upload_relationship_candidates"
# v0.18 M5:interactive analysis steps per spec §5.4
DEFAULT_ANALYSIS_STEPS_COLLECTION = "analysis_steps"


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
        analysis_sessions_collection: str = DEFAULT_ANALYSIS_SESSIONS_COLLECTION,
        analysis_assets_collection: str = DEFAULT_ANALYSIS_ASSETS_COLLECTION,
        relationship_candidates_collection: str = DEFAULT_RELATIONSHIP_CANDIDATES_COLLECTION,
        analysis_steps_collection: str = DEFAULT_ANALYSIS_STEPS_COLLECTION,
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
        # M3+: analysis sessions(chat 對話 + Phase 0/A/B/C/D outputs)
        self._analysis_sessions = mongo_db[analysis_sessions_collection]
        # M3A+: analysis assets(Saved Chart / Saved Metric / Analysis Template)
        self._analysis_assets = mongo_db[analysis_assets_collection]
        # v0.18 M2:upload_relationship_candidates per spec §5.3
        self._relationship_candidates = mongo_db[relationship_candidates_collection]
        # v0.18 M5:analysis_steps per spec §5.4
        self._analysis_steps = mongo_db[analysis_steps_collection]

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
        # M3+: analysis_sessions
        self._analysis_sessions.create_index(
            [("dataset_id", 1), ("updated_at", -1)],
        )
        # M3A+: analysis_assets
        self._analysis_assets.create_index(
            [("dataset_id", 1), ("asset_type", 1), ("created_at", -1)],
        )
        self._analysis_assets.create_index(
            [("dataset_id", 1), ("is_active", 1)],
        )
        # v0.18 M2: upload_relationship_candidates
        # Unique (dataset_id, metadata_version, relationship_id) so we can
        # safely upsert when a profile re-runs.
        self._relationship_candidates.create_index(
            [("dataset_id", 1), ("metadata_version", 1),
             ("relationship_id", 1)],
            unique=True,
        )
        self._relationship_candidates.create_index(
            [("dataset_id", 1), ("status", 1)],
        )
        # v0.18 M5: analysis_steps
        # Unique (session_id, step_no) so steps within a session are ordered
        # and a duplicate step_no on the same session is an error.
        self._analysis_steps.create_index(
            [("session_id", 1), ("step_no", 1)],
            unique=True,
        )
        self._analysis_steps.create_index(
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
        """硬刪除 dataset 及相關 table / profile 記錄(filesystem 由 caller 清)。

        v0.18 M2:cascade also clears upload_relationship_candidates rows.
        v0.18 M5:cascade also clears analysis_steps rows.
        """
        self._tables.delete_many({"dataset_id": dataset_id})
        self._profiles.delete_many({"dataset_id": dataset_id})
        self._relationship_candidates.delete_many({"dataset_id": dataset_id})
        self._analysis_steps.delete_many({"dataset_id": dataset_id})
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

    def update_table_profile_fields(
        self,
        dataset_id: str,
        table_id: str,
        *,
        sheet_name: Optional[str] = None,
        table_role: Optional[str] = None,
        grain: Optional[str] = None,
        primary_key: Optional[list[str]] = None,
        profile_version: Optional[int] = None,
    ) -> bool:
        """Set profile-derived fields on an existing upload_tables doc.

        These fields are not knowable at parse time — they come out of
        `multi_table_profiler.profile_multi_table()` after the profile
        step. Called by `upload_service` once profiling completes.

        Spec §5.2 `upload_tables` schema:
            sheet_name    — original Excel sheet name (informational)
            table_role    — "fact" | "dimension" | "bridge" | "unknown"
            grain         — human-readable, e.g. "one row per employee"
            primary_key   — list[str] of column names forming the PK

        Args:
            dataset_id, table_id: lookup keys (unique together).
            sheet_name / table_role / grain / primary_key / profile_version:
                Only fields you pass non-None get written. Pass an empty
                list / empty string to explicitly clear a value.

        Returns:
            True if a doc matched and any field changed; False otherwise.
        """
        set_doc: dict[str, Any] = {}
        for k, v in [
            ("sheet_name", sheet_name),
            ("table_role", table_role),
            ("grain", grain),
            ("primary_key", primary_key),
            ("profile_version", profile_version),
        ]:
            if v is not None:
                set_doc[k] = v
        if not set_doc:
            return False
        set_doc["updated_at"] = _dt.datetime.now(_dt.timezone.utc)
        result = self._tables.update_one(
            {"dataset_id": dataset_id, "table_id": table_id},
            {"$set": set_doc},
        )
        return result.modified_count > 0

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

    # ============================================================
    # analysis_sessions CRUD(M3+)
    # ============================================================
    def create_session(
        self,
        dataset_id: str,
        metadata_version: int,
        user: str = "anonymous",
    ) -> str:
        """為某 dataset 建一個新分析 session。

        Args:
            dataset_id: 上傳 dataset
            metadata_version: 進入分析時 active 的 metadata version(綁定 lineage)
            user: session owner

        Returns:
            session_id (str, format `sess_YYYYMMDDHHMMSS_<6hex>`)
        """
        import secrets
        now = _dt.datetime.now(_dt.timezone.utc)
        ts = now.strftime("%Y%m%d%H%M%S")
        session_id = f"sess_{ts}_{secrets.token_hex(3)}"
        doc = {
            "_id": session_id,
            "dataset_id": dataset_id,
            "metadata_version": metadata_version,
            "owner": user,
            "messages": [],          # 對話歷史(user / assistant 交替)
            "last_analysis": None,   # 給 follow-up 接續用
            "created_at": now,
            "updated_at": now,
        }
        self._analysis_sessions.insert_one(doc)
        return session_id

    def get_session(self, session_id: str) -> Optional[dict]:
        return self._analysis_sessions.find_one({"_id": session_id})

    def list_sessions(
        self,
        dataset_id: str,
        owner: Optional[str] = None,
        limit: int = 20,
    ) -> list[dict]:
        """列出某 dataset 下的最近 sessions(新到舊)。"""
        query: dict[str, Any] = {"dataset_id": dataset_id}
        if owner:
            query["owner"] = owner
        return list(
            self._analysis_sessions.find(query)
            .sort("updated_at", -1)
            .limit(limit)
        )

    def append_message(
        self,
        session_id: str,
        role: str,
        content: str,
        **extra,
    ) -> bool:
        """加一則 message 到 session(role='user' or 'assistant')。

        `extra` 任何 key/value(例:phase_outputs / chart_intent / trace_id)
        都會跟 role/content 一起存進該 message。
        """
        msg = {
            "role": role,
            "content": content,
            "created_at": _dt.datetime.now(_dt.timezone.utc),
            **extra,
        }
        result = self._analysis_sessions.update_one(
            {"_id": session_id},
            {
                "$push": {"messages": msg},
                "$set": {"updated_at": _dt.datetime.now(_dt.timezone.utc)},
            },
        )
        return result.modified_count > 0

    def update_last_analysis(
        self,
        session_id: str,
        last_analysis: dict,
    ) -> bool:
        """更新 session.last_analysis(給 follow-up 偵測接續分析用)。"""
        result = self._analysis_sessions.update_one(
            {"_id": session_id},
            {"$set": {
                "last_analysis": last_analysis,
                "updated_at": _dt.datetime.now(_dt.timezone.utc),
            }},
        )
        return result.modified_count > 0

    def delete_session(self, session_id: str) -> bool:
        return self._analysis_sessions.delete_one(
            {"_id": session_id}).deleted_count > 0

    # ============================================================
    # analysis_assets CRUD(M3A+)
    # ============================================================
    def create_asset(self, asset_doc: dict) -> str:
        """寫一筆 analysis_asset 文件。caller 必須提供 _id(用 generate_asset_id)。

        Required keys:
          _id, asset_type, dataset_id, metadata_version, name,
          source_query, asset_payload, lineage, created_by
        """
        required = {"_id", "asset_type", "dataset_id", "metadata_version",
                    "name", "source_query", "asset_payload", "lineage",
                    "created_by"}
        missing = required - set(asset_doc.keys())
        if missing:
            raise ValueError(f"create_asset: 缺欄位 {missing}")
        now = _dt.datetime.now(_dt.timezone.utc)
        asset_doc.setdefault("description", "")
        asset_doc.setdefault("is_active", True)
        asset_doc["created_at"] = now
        asset_doc["updated_at"] = now
        self._analysis_assets.insert_one(asset_doc)
        return asset_doc["_id"]

    def get_asset(self, asset_id: str) -> Optional[dict]:
        return self._analysis_assets.find_one({"_id": asset_id})

    def list_assets(
        self,
        dataset_id: Optional[str] = None,
        asset_type: Optional[str] = None,
        owner: Optional[str] = None,
        include_inactive: bool = False,
        limit: int = 100,
    ) -> list[dict]:
        """列 assets(新到舊)。dataset_id 可選,沒帶就跨 dataset。"""
        query: dict[str, Any] = {}
        if dataset_id:
            query["dataset_id"] = dataset_id
        if asset_type:
            query["asset_type"] = asset_type
        if owner:
            query["created_by"] = owner
        if not include_inactive:
            query["is_active"] = True
        return list(
            self._analysis_assets.find(query)
            .sort("created_at", -1)
            .limit(limit)
        )

    def rename_asset(self, asset_id: str, new_name: str,
                       description: Optional[str] = None) -> bool:
        update_doc: dict[str, Any] = {
            "name": new_name,
            "updated_at": _dt.datetime.now(_dt.timezone.utc),
        }
        if description is not None:
            update_doc["description"] = description
        result = self._analysis_assets.update_one(
            {"_id": asset_id}, {"$set": update_doc},
        )
        return result.modified_count > 0

    def soft_delete_asset(self, asset_id: str) -> bool:
        """軟刪 — is_active=False(保留 audit trail)。"""
        result = self._analysis_assets.update_one(
            {"_id": asset_id},
            {"$set": {
                "is_active": False,
                "updated_at": _dt.datetime.now(_dt.timezone.utc),
            }},
        )
        return result.modified_count > 0

    def hard_delete_asset(self, asset_id: str) -> bool:
        """真刪 — 從 DB 移除(危險,僅 admin 用途)。"""
        return self._analysis_assets.delete_one(
            {"_id": asset_id}).deleted_count > 0

    # ============================================================
    # upload_relationship_candidates CRUD (v0.18 M2 · spec §5.3)
    # ============================================================
    def save_relationship_candidates(
        self,
        dataset_id: str,
        candidates: list[dict],
        metadata_version: int,
    ) -> int:
        """Bulk upsert relationship candidates per spec §5.3.

        Idempotent via the unique (dataset_id, metadata_version,
        relationship_id) index — calling this twice with the same args
        replaces existing rows in place rather than creating duplicates.

        Args:
            dataset_id: dataset to attach candidates to.
            candidates: list of relationship dicts (one per spec §5.3
                schema, as produced by relationship_profiler.detect_relationships).
                Each must have `relationship_id` (caller provides — the
                profiler generates one deterministically).
            metadata_version: pins these candidates to a profile snapshot
                so re-profiling doesn't silently clobber confirmed status.

        Returns:
            Number of candidates written (insert or update).
        """
        if not candidates:
            return 0
        now = _dt.datetime.now(_dt.timezone.utc)
        n_written = 0
        for c in candidates:
            if "relationship_id" not in c:
                raise ValueError(
                    "save_relationship_candidates: candidate missing "
                    "`relationship_id`"
                )
            doc = {
                **c,
                "dataset_id": dataset_id,
                "metadata_version": metadata_version,
                "updated_at": now,
            }
            doc.setdefault("status", "candidate")
            doc.setdefault("created_at", now)
            self._relationship_candidates.update_one(
                {
                    "dataset_id": dataset_id,
                    "metadata_version": metadata_version,
                    "relationship_id": c["relationship_id"],
                },
                {"$set": doc},
                upsert=True,
            )
            n_written += 1
        return n_written

    def list_relationship_candidates(
        self,
        dataset_id: str,
        *,
        metadata_version: Optional[int] = None,
        status: Optional[str] = None,
    ) -> list[dict]:
        """List relationship candidates for a dataset.

        Args:
            dataset_id: required.
            metadata_version: if given, filter to that profile version;
                otherwise returns the latest metadata_version's rows.
            status: optional filter — "candidate" / "confirmed" /
                "rejected" / "edited".

        Returns:
            Sorted by confidence desc.
        """
        query: dict[str, Any] = {"dataset_id": dataset_id}
        if metadata_version is None:
            # Pick the max metadata_version present and filter to it.
            latest = (
                self._relationship_candidates.find({"dataset_id": dataset_id})
                .sort("metadata_version", -1)
                .limit(1)
            )
            latest_list = list(latest)
            if not latest_list:
                return []
            query["metadata_version"] = latest_list[0]["metadata_version"]
        else:
            query["metadata_version"] = metadata_version
        if status is not None:
            query["status"] = status
        return list(
            self._relationship_candidates.find(query)
            .sort("confidence", -1)
        )

    def update_relationship_status(
        self,
        dataset_id: str,
        relationship_id: str,
        *,
        status: Optional[str] = None,
        relationship_type: Optional[str] = None,
        default_join_type: Optional[str] = None,
        from_field: Optional[str] = None,
        to_field: Optional[str] = None,
        metadata_version: Optional[int] = None,
        user: str = "system",
    ) -> bool:
        """Update a candidate's status / type / join settings (Review UI).

        Spec §9.1 Relationship Review actions (Confirm / Reject / Edit
        join key / Edit relationship type / Edit default join type) all
        funnel through this method.

        Args:
            dataset_id, relationship_id: lookup keys.
            status: candidate | confirmed | rejected | edited.
            relationship_type: optional override (one_to_one / many_to_one /
                one_to_many / many_to_many_candidate).
            default_join_type: optional override (left / inner / right).
            from_field / to_field: optional override (user picked a different
                join column).
            metadata_version: if given, restricts the update to that
                version's row. Default updates the latest version's row.
            user: who made the change (audit; written to `confirmed_by` if
                status is `confirmed`, `rejected_by` if `rejected`, ...).

        Returns:
            True if a doc matched and was updated; False otherwise.
        """
        set_doc: dict[str, Any] = {}
        if status is not None:
            set_doc["status"] = status
            now = _dt.datetime.now(_dt.timezone.utc)
            if status == "confirmed":
                set_doc["confirmed_by"] = user
                set_doc["confirmed_at"] = now
            elif status == "rejected":
                set_doc["rejected_by"] = user
                set_doc["rejected_at"] = now
            elif status == "edited":
                set_doc["edited_by"] = user
                set_doc["edited_at"] = now
        for k, v in [
            ("relationship_type", relationship_type),
            ("default_join_type", default_join_type),
            ("from_field", from_field),
            ("to_field", to_field),
        ]:
            if v is not None:
                set_doc[k] = v
        if not set_doc:
            return False
        set_doc["updated_at"] = _dt.datetime.now(_dt.timezone.utc)

        query: dict[str, Any] = {
            "dataset_id": dataset_id,
            "relationship_id": relationship_id,
        }
        if metadata_version is not None:
            query["metadata_version"] = metadata_version
        else:
            # Update the latest metadata_version's row only — avoids
            # mass-updating historical snapshots.
            latest = (
                self._relationship_candidates.find(
                    {"dataset_id": dataset_id,
                     "relationship_id": relationship_id}
                )
                .sort("metadata_version", -1)
                .limit(1)
            )
            latest_list = list(latest)
            if not latest_list:
                return False
            query["metadata_version"] = latest_list[0]["metadata_version"]

        result = self._relationship_candidates.update_one(
            query, {"$set": set_doc},
        )
        return result.modified_count > 0

    # ============================================================
    # analysis_steps CRUD (v0.18 M5 · spec §5.4)
    # ============================================================
    def save_analysis_step(self, step_doc: dict) -> str:
        """Insert a new analysis_step row.

        Args:
            step_doc: must include `step_id`, `session_id`, `dataset_id`,
                `step_no`, `action_type`. Other fields per spec §5.4 are
                optional (output_table, generated_code, generated_sql,
                row_count, status, input_tables, output_schema).

        Returns:
            step_id (str).

        Raises:
            ValueError on missing required field.
            pymongo DuplicateKeyError if (session_id, step_no) already exists.
        """
        required = {"step_id", "session_id", "dataset_id",
                    "step_no", "action_type"}
        missing = required - set(step_doc.keys())
        if missing:
            raise ValueError(
                f"save_analysis_step: required keys missing: {missing}"
            )
        now = _dt.datetime.now(_dt.timezone.utc)
        step_doc.setdefault("created_at", now)
        step_doc["updated_at"] = now
        step_doc.setdefault("status", "completed")
        self._analysis_steps.insert_one(step_doc)
        return step_doc["step_id"]

    def get_analysis_step(self, step_id: str) -> Optional[dict]:
        return self._analysis_steps.find_one({"step_id": step_id})

    def list_analysis_steps(
        self,
        session_id: str,
        *,
        status: Optional[str] = None,
    ) -> list[dict]:
        """List steps in a session, ordered by step_no ascending."""
        query: dict[str, Any] = {"session_id": session_id}
        if status is not None:
            query["status"] = status
        return list(
            self._analysis_steps.find(query).sort("step_no", 1)
        )

    def next_step_no(self, session_id: str) -> int:
        """Return the step_no to use for the next step in this session.

        Walks step_no asc and returns 1 + max(step_no), or 1 if none.
        """
        latest = (
            self._analysis_steps.find({"session_id": session_id})
            .sort("step_no", -1)
            .limit(1)
        )
        latest_list = list(latest)
        if not latest_list:
            return 1
        return int(latest_list[0]["step_no"]) + 1


def generate_asset_id(asset_type: str = "asset") -> str:
    """產生新的 asset_id — 格式:`<prefix>_<ts>_<6hex>`。

    Args:
        asset_type: 'saved_chart' | 'saved_metric' | 'analysis_template'
                    用於 asset_id 前綴(便於肉眼辨識)。

    Returns:
        例 'chart_20260522135530_a8c3f2' / 'metric_...' / 'tmpl_...'
    """
    import secrets
    prefix_map = {
        "saved_chart": "chart",
        "saved_metric": "metric",
        "analysis_template": "tmpl",
    }
    prefix = prefix_map.get(asset_type, "asset")
    ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"{prefix}_{ts}_{secrets.token_hex(3)}"


def generate_dataset_id() -> str:
    """產生新的 dataset_id — 格式:`upload_YYYYMMDDHHMMSS_<6hex>`。

    timestamp 確保按建立時間排序,6 hex 避免同秒衝突。
    """
    import secrets
    ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d%H%M%S")
    suffix = secrets.token_hex(3)  # 6 hex chars
    return f"upload_{ts}_{suffix}"
