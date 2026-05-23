"""tests/unit/test_upload_repository.py — unit tests for upload_repository.py (M4a)."""

from __future__ import annotations

import pytest

from upload_repository import (
    UploadRepository,
    generate_asset_id,
    generate_dataset_id,
)


# ============================================================
# ID generators(不需要 MongoDB)
# ============================================================
class TestIdGenerators:
    def test_dataset_id_format(self):
        did = generate_dataset_id()
        assert did.startswith("upload_")
        # upload_YYYYMMDDHHMMSS_<6hex>
        parts = did.split("_")
        assert len(parts) == 3
        assert len(parts[1]) == 14   # timestamp
        assert len(parts[2]) == 6    # hex suffix

    def test_dataset_id_unique(self):
        ids = {generate_dataset_id() for _ in range(50)}
        assert len(ids) == 50   # 全部不重複(secrets.token_hex 保證)

    def test_asset_id_prefix(self):
        assert generate_asset_id("saved_chart").startswith("chart_")
        assert generate_asset_id("saved_metric").startswith("metric_")
        assert generate_asset_id("analysis_template").startswith("tmpl_")
        assert generate_asset_id("unknown_type").startswith("asset_")


# ============================================================
# Constructor 強制 MongoDB
# ============================================================
class TestRepoConstructor:
    def test_requires_mongo(self):
        with pytest.raises(ValueError, match="MongoDB"):
            UploadRepository(None)


# ============================================================
# CRUD with mongomock
# ============================================================
@pytest.mark.requires_mongo
class TestDatasetCRUD:
    def test_create_get(self, mongo_db):
        repo = UploadRepository(mongo_db)
        repo.ensure_indexes()
        did = repo.create_dataset({
            "_id": "upload_test_x",
            "dataset_name": "x.csv",
            "owner": "alice",
            "source_type": "file_upload",
            "file": {"original_filename": "x.csv"},
            "status": "uploaded",
        })
        assert did == "upload_test_x"
        # Get
        doc = repo.get_dataset("upload_test_x")
        assert doc is not None
        assert doc["dataset_name"] == "x.csv"
        assert "created_at" in doc

    def test_create_missing_id_raises(self, mongo_db):
        repo = UploadRepository(mongo_db)
        with pytest.raises(ValueError, match="_id"):
            repo.create_dataset({"dataset_name": "x.csv"})

    def test_list_by_owner(self, mongo_db):
        repo = UploadRepository(mongo_db)
        for i in range(3):
            repo.create_dataset({
                "_id": f"upload_test_alice_{i}", "dataset_name": f"a{i}.csv",
                "owner": "alice", "source_type": "file_upload",
                "file": {}, "status": "uploaded",
            })
        repo.create_dataset({
            "_id": "upload_test_bob", "dataset_name": "b.csv",
            "owner": "bob", "source_type": "file_upload",
            "file": {}, "status": "uploaded",
        })
        alice_only = repo.list_datasets(owner="alice")
        assert len(alice_only) == 3
        all_ds = repo.list_datasets()
        assert len(all_ds) == 4

    def test_update_status(self, mongo_db):
        repo = UploadRepository(mongo_db)
        repo.create_dataset({
            "_id": "upload_test_u", "dataset_name": "u.csv",
            "owner": "alice", "source_type": "file_upload",
            "file": {}, "status": "uploaded",
        })
        ok = repo.update_status("upload_test_u", "profiled",
                                  active_metadata_version=2)
        assert ok
        doc = repo.get_dataset("upload_test_u")
        assert doc["status"] == "profiled"
        assert doc["active_metadata_version"] == 2

    def test_delete_cascade(self, mongo_db):
        """刪 dataset 該連 tables / profiles 一起刪"""
        repo = UploadRepository(mongo_db)
        repo.create_dataset({
            "_id": "upload_test_d", "dataset_name": "d.csv",
            "owner": "alice", "source_type": "file_upload",
            "file": {}, "status": "uploaded",
        })
        repo.create_table({
            "dataset_id": "upload_test_d", "table_id": "sheet1",
            "table_name": "Sheet1", "row_count": 10, "column_count": 3,
            "storage": {"format": "parquet", "path": "fake"},
        })
        repo.save_profile("upload_test_d", {"tables": [{"table_id": "sheet1"}]})
        # Delete
        ok = repo.delete_dataset("upload_test_d")
        assert ok
        assert repo.get_dataset("upload_test_d") is None
        assert repo.list_tables("upload_test_d") == []
        assert repo.get_latest_profile("upload_test_d") is None


@pytest.mark.requires_mongo
class TestTableCRUD:
    def test_create_unique(self, mongo_db):
        repo = UploadRepository(mongo_db)
        repo.ensure_indexes()
        repo.create_table({
            "dataset_id": "ds-1", "table_id": "sheet1",
            "table_name": "Sheet1", "row_count": 10, "column_count": 3,
            "storage": {"format": "parquet", "path": "p"},
        })
        # Unique index 該擋住 — 用 pymongo.errors 或 mongomock 都繼承的 OperationFailure
        # (pymongo 在某些 sandbox 環境 import 失敗,改用 generic Exception 擷取)
        with pytest.raises(Exception) as excinfo:
            repo.create_table({
                "dataset_id": "ds-1", "table_id": "sheet1",
                "table_name": "Sheet1 dup", "row_count": 5, "column_count": 2,
                "storage": {"format": "parquet", "path": "p2"},
            })
        # 確認是 DuplicateKeyError 類(不論來自 pymongo 或 mongomock)
        assert ("DuplicateKey" in type(excinfo.value).__name__
                or "duplicate" in str(excinfo.value).lower()
                or "E11000" in str(excinfo.value))

    def test_list_returns_sorted(self, mongo_db):
        repo = UploadRepository(mongo_db)
        repo.ensure_indexes()
        for i in [3, 1, 2]:
            repo.create_table({
                "dataset_id": "ds-1", "table_id": f"sheet{i}",
                "table_name": f"S{i}", "row_count": 10, "column_count": 3,
                "storage": {"format": "parquet", "path": "p"},
            })
        tables = repo.list_tables("ds-1")
        assert [t["table_id"] for t in tables] == ["sheet1", "sheet2", "sheet3"]


@pytest.mark.requires_mongo
class TestProfileVersioning:
    def test_auto_increment(self, mongo_db):
        repo = UploadRepository(mongo_db)
        for i in range(3):
            v = repo.save_profile("ds-1", {"tables": [{"v": i}]})
            assert v == i + 1
        # latest
        latest = repo.get_latest_profile("ds-1")
        assert latest["profile_version"] == 3

    def test_list_versions_desc(self, mongo_db):
        repo = UploadRepository(mongo_db)
        for _ in range(3):
            repo.save_profile("ds-1", {"tables": []})
        versions = repo.list_profile_versions("ds-1")
        assert [v["profile_version"] for v in versions] == [3, 2, 1]


@pytest.mark.requires_mongo
class TestMetadataVersioning:
    def test_save_activate(self, mongo_db):
        repo = UploadRepository(mongo_db)
        repo.create_dataset({
            "_id": "ds-1", "dataset_name": "x",
            "owner": "alice", "source_type": "file_upload",
            "file": {}, "status": "profiled",
        })
        v1 = repo.save_metadata_version("ds-1", {"k": "v1"}, activate=True)
        v2 = repo.save_metadata_version("ds-1", {"k": "v2"}, activate=True)
        assert v1 == 1
        assert v2 == 2
        # v2 是 active
        active = repo.get_active_metadata("ds-1")
        assert active["version"] == 2
        # v1 該被 mark inactive
        v1_doc = repo.get_metadata_version("ds-1", 1)
        assert v1_doc["is_active"] is False

    def test_confirm_status(self, mongo_db):
        repo = UploadRepository(mongo_db)
        repo.create_dataset({
            "_id": "ds-1", "dataset_name": "x", "owner": "a",
            "source_type": "file_upload", "file": {}, "status": "profiled",
        })
        v = repo.save_metadata_version(
            "ds-1", {"k": "v"},
            confirmation_status="confirmed", confirmed_by="alice",
        )
        active = repo.get_active_metadata("ds-1")
        assert active["confirmation_status"] == "confirmed"
        assert active["confirmed_by"] == "alice"

    def test_activate_old_version(self, mongo_db):
        repo = UploadRepository(mongo_db)
        repo.create_dataset({
            "_id": "ds-1", "dataset_name": "x", "owner": "a",
            "source_type": "file_upload", "file": {}, "status": "profiled",
        })
        repo.save_metadata_version("ds-1", {"k": "v1"}, activate=True)
        repo.save_metadata_version("ds-1", {"k": "v2"}, activate=True)
        # 切回 v1
        ok = repo.activate_metadata_version("ds-1", 1)
        assert ok
        active = repo.get_active_metadata("ds-1")
        assert active["version"] == 1


@pytest.mark.requires_mongo
class TestCorrectionsCRUD:
    def test_save_and_list(self, mongo_db):
        repo = UploadRepository(mongo_db)
        repo.save_corrections(
            dataset_id="ds-1", metadata_version_before=1, metadata_version_after=2,
            corrections=[{"target": "sheet1.col.unit", "old_value": "",
                          "new_value": "days", "reason": "test"}],
            user="alice",
        )
        history = repo.list_corrections("ds-1")
        assert len(history) == 1
        assert history[0]["created_by"] == "alice"


@pytest.mark.requires_mongo
class TestSessionCRUD:
    def test_create_append_update(self, mongo_db):
        repo = UploadRepository(mongo_db)
        sid = repo.create_session(
            dataset_id="ds-1", metadata_version=1, user="alice",
        )
        assert sid.startswith("sess_")
        # Append
        ok = repo.append_message(sid, role="user", content="畫圖")
        assert ok
        ok = repo.append_message(sid, role="assistant", content="完成",
                                   trace_id="abc")
        assert ok
        # Get session
        session = repo.get_session(sid)
        assert session["dataset_id"] == "ds-1"
        assert len(session["messages"]) == 2
        # Update last_analysis
        ok = repo.update_last_analysis(sid, {"chart_engine": "ECharts"})
        assert ok
        session = repo.get_session(sid)
        assert session["last_analysis"]["chart_engine"] == "ECharts"


@pytest.mark.requires_mongo
class TestAssetCRUD:
    def test_create_get(self, mongo_db):
        repo = UploadRepository(mongo_db)
        repo.ensure_indexes()
        aid = repo.create_asset({
            "_id": "chart_test_001", "asset_type": "saved_chart",
            "dataset_id": "ds-1", "metadata_version": 1,
            "name": "Test chart", "source_query": "draw something",
            "asset_payload": {}, "lineage": {}, "created_by": "alice",
        })
        assert aid == "chart_test_001"
        doc = repo.get_asset(aid)
        assert doc["name"] == "Test chart"
        assert doc["is_active"] is True

    def test_list_filters(self, mongo_db):
        repo = UploadRepository(mongo_db)
        for i in range(3):
            repo.create_asset({
                "_id": f"chart_{i}", "asset_type": "saved_chart",
                "dataset_id": "ds-1", "metadata_version": 1,
                "name": f"C{i}", "source_query": "q",
                "asset_payload": {}, "lineage": {}, "created_by": "alice",
            })
        for i in range(2):
            repo.create_asset({
                "_id": f"metric_{i}", "asset_type": "saved_metric",
                "dataset_id": "ds-1", "metadata_version": 1,
                "name": f"M{i}", "source_query": "q",
                "asset_payload": {}, "lineage": {}, "created_by": "alice",
            })
        # Filter by type
        charts = repo.list_assets(asset_type="saved_chart")
        assert len(charts) == 3
        metrics = repo.list_assets(asset_type="saved_metric")
        assert len(metrics) == 2

    def test_rename(self, mongo_db):
        repo = UploadRepository(mongo_db)
        repo.create_asset({
            "_id": "x", "asset_type": "saved_chart", "dataset_id": "ds-1",
            "metadata_version": 1, "name": "Old", "source_query": "q",
            "asset_payload": {}, "lineage": {}, "created_by": "alice",
        })
        ok = repo.rename_asset("x", "New name", description="updated")
        assert ok
        doc = repo.get_asset("x")
        assert doc["name"] == "New name"
        assert doc["description"] == "updated"

    def test_soft_delete(self, mongo_db):
        repo = UploadRepository(mongo_db)
        repo.create_asset({
            "_id": "x", "asset_type": "saved_chart", "dataset_id": "ds-1",
            "metadata_version": 1, "name": "x", "source_query": "q",
            "asset_payload": {}, "lineage": {}, "created_by": "alice",
        })
        ok = repo.soft_delete_asset("x")
        assert ok
        # Default 不顯示 inactive
        active_list = repo.list_assets()
        assert len(active_list) == 0
        # include_inactive 才看到
        all_list = repo.list_assets(include_inactive=True)
        assert len(all_list) == 1
