"""tests/unit/test_analysis_asset_service.py — unit tests for analysis_asset_service.py (M4a)."""

from __future__ import annotations

import pandas as pd
import pytest

from analysis_asset_service import AnalysisAssetService


# ============================================================
# Pure logic(不需要 MongoDB)
# ============================================================
class TestValidateCompleted:
    def test_accepts_completed(self):
        svc = AnalysisAssetService(upload_repo=None, correction_service=None)
        svc._validate_completed({"status": "completed"})  # no raise

    @pytest.mark.parametrize("bad_status", ["failed", "refused", "meta", "running", "", None])
    def test_rejects_other(self, bad_status):
        svc = AnalysisAssetService(upload_repo=None, correction_service=None)
        with pytest.raises(ValueError, match="completed"):
            svc._validate_completed({"status": bad_status})

    def test_rejects_non_dict(self):
        svc = AnalysisAssetService(upload_repo=None, correction_service=None)
        with pytest.raises(ValueError, match="dict"):
            svc._validate_completed("not a dict")


# ============================================================
# End-to-end with mongomock
# ============================================================
@pytest.fixture
def fake_completed_result():
    """模擬一個成功分析的 result dict。"""
    return {
        "status": "completed",
        "intent": "analysis",
        "trace_id": "trace-abc-123",
        "plan_text": "## A. ...\n## B. ...\n## C. ...",
        "phase_a_code": "raw_df = source_df.copy()",
        "raw_df_info": {"n_rows": 100, "columns": ["a", "b"]},
        "phase_b_code": "Q = raw_df.groupby('a').size().reset_index(name='count')",
        "Q_info": {"n_rows": 10, "columns": ["a", "count"]},
        "Q": pd.DataFrame({"a": ["x", "y", "z"], "count": [1, 2, 3]}),
        "phase_c_code": 'option = {"series": [...]}',
        "chart_option": {"series": [{"type": "bar"}]},
        "chart_fig": None,
        "use_table_fallback": False,
        "insight": "Insight text",
        "is_followup": False,
        "error": None,
    }


@pytest.fixture
def setup_dataset(mongo_db):
    """建一個 dataset + active metadata,回 (repo, dataset_id, session_id)。"""
    from upload_repository import UploadRepository
    repo = UploadRepository(mongo_db)
    repo.ensure_indexes()
    dataset_id = "upload_test_aas"
    repo.create_dataset({
        "_id": dataset_id, "dataset_name": "test.csv", "owner": "alice",
        "source_type": "file_upload", "file": {}, "status": "profiled",
    })
    repo.save_metadata_version(
        dataset_id=dataset_id,
        metadata={
            "dataset_id": dataset_id,
            "source_type": "upload",
            "collections": {"sheet1": {"fields": {}}},
            "kpi_definitions": {},
            "data_limitations": {},
        },
        confirmation_status="confirmed",
        confirmed_by="alice",
        activate=True,
    )
    sid = repo.create_session(dataset_id, metadata_version=1, user="alice")
    repo.append_message(sid, role="user", content="比較各 a 的 count")
    return repo, dataset_id, sid


@pytest.mark.requires_mongo
class TestSaveChart:
    def test_basic_save(self, mongo_db, setup_dataset, fake_completed_result):
        repo, did, sid = setup_dataset
        from metadata_correction_service import MetadataCorrectionService
        svc = AnalysisAssetService(repo, MetadataCorrectionService(repo))
        aid = svc.save_chart(
            dataset_id=did, session_id=sid,
            analysis_result=fake_completed_result,
            name="Test chart", description="desc",
            user="alice",
        )
        assert aid.startswith("chart_")
        doc = repo.get_asset(aid)
        assert doc["name"] == "Test chart"
        assert doc["asset_type"] == "saved_chart"
        assert doc["metadata_version"] == 1
        # Lineage 完整
        assert "phase_a_code" in doc["lineage"]
        assert doc["lineage"]["phase_a_code"] == "raw_df = source_df.copy()"
        # source_query 從 session messages 抽
        assert doc["source_query"] == "比較各 a 的 count"

    def test_save_failed_result_rejected(self, mongo_db, setup_dataset):
        repo, did, sid = setup_dataset
        from metadata_correction_service import MetadataCorrectionService
        svc = AnalysisAssetService(repo, MetadataCorrectionService(repo))
        with pytest.raises(ValueError, match="completed"):
            svc.save_chart(
                dataset_id=did, session_id=sid,
                analysis_result={"status": "failed"},
                name="x", user="alice",
            )


@pytest.mark.requires_mongo
class TestSaveMetric:
    def test_writeback_creates_new_version(self, mongo_db, setup_dataset, fake_completed_result):
        """Saved Metric 應該寫回 metadata 出新版"""
        repo, did, sid = setup_dataset
        from metadata_correction_service import MetadataCorrectionService
        svc = AnalysisAssetService(repo, MetadataCorrectionService(repo))
        aid = svc.save_metric(
            dataset_id=did, session_id=sid,
            analysis_result=fake_completed_result,
            kpi_key="avg_count", name="平均計數", formula="mean(count)",
            important_note="unit=items", user="alice",
        )
        assert aid.startswith("metric_")
        # Asset 文件
        doc = repo.get_asset(aid)
        assert doc["asset_type"] == "saved_metric"
        assert doc["asset_payload"]["kpi_key"] == "avg_count"
        assert doc["asset_payload"]["formula"] == "mean(count)"
        # 新 metadata version 該生成
        active = repo.get_active_metadata(did)
        assert active["version"] == 2   # v1 是 setup,v2 是 save_metric 寫的
        # kpi_definitions 該含新 KPI
        kpi_defs = active["metadata"]["kpi_definitions"]
        assert "avg_count" in kpi_defs
        assert kpi_defs["avg_count"]["name"] == "平均計數"
        assert kpi_defs["avg_count"]["user_confirmed"] is True
        # Asset 該綁 new version
        assert doc["metadata_version"] == 2

    def test_overwrite_existing_kpi(self, mongo_db, setup_dataset, fake_completed_result):
        repo, did, sid = setup_dataset
        from metadata_correction_service import MetadataCorrectionService
        svc = AnalysisAssetService(repo, MetadataCorrectionService(repo))
        # 第一次
        svc.save_metric(
            dataset_id=did, session_id=sid,
            analysis_result=fake_completed_result,
            kpi_key="x", name="X v1", formula="f", user="a",
        )
        # 用同 key 再寫
        svc.save_metric(
            dataset_id=did, session_id=sid,
            analysis_result=fake_completed_result,
            kpi_key="x", name="X v2", formula="f2", user="a",
        )
        active = repo.get_active_metadata(did)
        # 該存最後的 v2
        assert active["metadata"]["kpi_definitions"]["x"]["name"] == "X v2"
        assert active["metadata"]["kpi_definitions"]["x"]["formula"] == "f2"


@pytest.mark.requires_mongo
class TestSaveTemplate:
    def test_basic(self, mongo_db, setup_dataset, fake_completed_result):
        repo, did, sid = setup_dataset
        from metadata_correction_service import MetadataCorrectionService
        svc = AnalysisAssetService(repo, MetadataCorrectionService(repo))
        aid = svc.save_template(
            dataset_id=did, session_id=sid,
            analysis_result=fake_completed_result,
            name="My template", description="reusable",
            user="alice",
        )
        assert aid.startswith("tmpl_")
        doc = repo.get_asset(aid)
        assert doc["asset_type"] == "analysis_template"
        # template_steps 應該存 query + plan
        steps = doc["asset_payload"]["template_steps"]
        assert "query" in steps
        assert "plan_text" in steps


@pytest.mark.requires_mongo
class TestListAndGet:
    def test_list_filters(self, mongo_db, setup_dataset, fake_completed_result):
        repo, did, sid = setup_dataset
        from metadata_correction_service import MetadataCorrectionService
        svc = AnalysisAssetService(repo, MetadataCorrectionService(repo))
        svc.save_chart(did, sid, fake_completed_result, "C1", user="alice")
        svc.save_chart(did, sid, fake_completed_result, "C2", user="alice")
        svc.save_template(did, sid, fake_completed_result, "T1", user="alice")
        # Type filter
        charts = svc.list(dataset_id=did, asset_type="saved_chart")
        assert len(charts) == 2
        tmpls = svc.list(dataset_id=did, asset_type="analysis_template")
        assert len(tmpls) == 1


@pytest.mark.requires_mongo
class TestReplayAndDriftCheck:
    def test_replay_returns_query(self, mongo_db, setup_dataset, fake_completed_result):
        repo, did, sid = setup_dataset
        from metadata_correction_service import MetadataCorrectionService
        svc = AnalysisAssetService(repo, MetadataCorrectionService(repo))
        aid = svc.save_chart(did, sid, fake_completed_result, "C", user="alice")
        q = svc.get_replay_query(aid)
        assert q == "比較各 a 的 count"

    def test_drift_check_fresh(self, mongo_db, setup_dataset, fake_completed_result):
        repo, did, sid = setup_dataset
        from metadata_correction_service import MetadataCorrectionService
        svc = AnalysisAssetService(repo, MetadataCorrectionService(repo))
        aid = svc.save_chart(did, sid, fake_completed_result, "C", user="alice")
        drift = svc.metadata_drift_check(aid)
        assert drift["asset_version"] == 1
        assert drift["active_version"] == 1
        assert drift["is_stale"] is False
        assert drift["warning"] is None

    def test_drift_check_stale(self, mongo_db, setup_dataset, fake_completed_result):
        """新 metadata version 後,舊 asset 該被 mark stale"""
        repo, did, sid = setup_dataset
        from metadata_correction_service import MetadataCorrectionService
        svc = AnalysisAssetService(repo, MetadataCorrectionService(repo))
        aid = svc.save_chart(did, sid, fake_completed_result, "C", user="alice")
        # 後續 save_metric 會出 v2 metadata
        svc.save_metric(did, sid, fake_completed_result,
                          kpi_key="k", name="K", formula="f", user="alice")
        drift = svc.metadata_drift_check(aid)
        assert drift["is_stale"] is True
        assert drift["warning"] is not None


@pytest.mark.requires_mongo
class TestRenameAndDelete:
    def test_rename(self, mongo_db, setup_dataset, fake_completed_result):
        repo, did, sid = setup_dataset
        from metadata_correction_service import MetadataCorrectionService
        svc = AnalysisAssetService(repo, MetadataCorrectionService(repo))
        aid = svc.save_chart(did, sid, fake_completed_result, "Old", user="a")
        ok = svc.rename(aid, "New", description="renamed")
        assert ok
        doc = svc.get(aid)
        assert doc["name"] == "New"

    def test_soft_delete(self, mongo_db, setup_dataset, fake_completed_result):
        repo, did, sid = setup_dataset
        from metadata_correction_service import MetadataCorrectionService
        svc = AnalysisAssetService(repo, MetadataCorrectionService(repo))
        aid = svc.save_chart(did, sid, fake_completed_result, "X", user="a")
        ok = svc.delete(aid)
        assert ok
        doc = svc.get(aid)
        # Soft delete:doc 還在但 is_active=False
        assert doc is not None
        assert doc["is_active"] is False
