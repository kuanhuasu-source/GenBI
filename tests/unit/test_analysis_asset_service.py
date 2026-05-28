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


@pytest.fixture
def setup_steps(setup_dataset, tmp_path):
    """Build a 2-step M5 chain on top of setup_dataset.

    Step 1: extract_data → step parquet on disk.
    Step 2: aggregate    → step parquet on disk.

    Returns (repo, dataset_id, session_id, step1_id, step2_id).
    """
    repo, did, sid = setup_dataset
    df = pd.DataFrame({
        "dept": ["Eng", "Sales", "Eng"],
        "salary": [1000, 2000, 1500],
    })
    # Write the step parquets directly into the per-dataset derived dir.
    derived_dir = tmp_path / did / "derived"
    derived_dir.mkdir(parents=True, exist_ok=True)
    p1 = derived_dir / "step_001.parquet"
    p2 = derived_dir / "step_002.parquet"
    df.to_parquet(p1, index=False)
    df.groupby("dept").sum().reset_index().to_parquet(p2, index=False)

    repo.save_analysis_step({
        "step_id": "step_001", "session_id": sid, "dataset_id": did,
        "metadata_version": 1, "step_no": 1,
        "action_type": "extract_data",
        "user_query": "all rows",
        "input_tables": ["employee"],
        "output_table": "step_001",
        "params": {"input_table": "employee"},
        "output_schema": [
            {"name": "dept", "dtype": "object"},
            {"name": "salary", "dtype": "int64"},
        ],
        "row_count": 3,
        "status": "completed",
        "storage": {"format": "parquet", "path": str(p1)},
    })
    repo.save_analysis_step({
        "step_id": "step_002", "session_id": sid, "dataset_id": did,
        "metadata_version": 1, "step_no": 2,
        "action_type": "aggregate",
        "user_query": "total salary by dept",
        "input_tables": ["step_001"],
        "output_table": "step_002",
        "params": {"input_table": "step_001", "group_by": ["dept"],
                   "aggregations": [
                       {"column": "salary", "function": "sum",
                        "alias": "total"},
                   ]},
        "output_schema": [
            {"name": "dept", "dtype": "object"},
            {"name": "total", "dtype": "int64"},
        ],
        "row_count": 2,
        "status": "completed",
        "storage": {"format": "parquet", "path": str(p2)},
    })
    return repo, did, sid, "step_001", "step_002"


@pytest.mark.requires_mongo
class TestSaveDerivedTable:
    """v0.18 M6 (Assets 2.0): save a completed M5 step as a reusable asset."""

    def _svc(self, repo):
        from metadata_correction_service import MetadataCorrectionService
        return AnalysisAssetService(repo, MetadataCorrectionService(repo))

    def test_basic_save(self, mongo_db, setup_steps):
        repo, did, sid, s1, _s2 = setup_steps
        svc = self._svc(repo)
        aid = svc.save_derived_table(
            session_id=sid, step_id=s1,
            name="employees_extract", description="extracted rows",
            user="alice",
        )
        assert aid.startswith("asset_") or aid.startswith("saved_") \
               or "derived" in aid.lower() or aid.startswith("asset")
        doc = repo.get_asset(aid)
        assert doc is not None
        assert doc["asset_type"] == "saved_derived_table"
        assert doc["name"] == "employees_extract"
        assert doc["dataset_id"] == did
        assert doc["metadata_version"] == 1
        # Spec rule 27: source_step_ids must bind to the originating step.
        assert doc["source_step_ids"] == [s1]
        # asset_payload echoes the step's schema + row count.
        assert doc["asset_payload"]["row_count"] == 3
        assert doc["asset_payload"]["action_type"] == "extract_data"
        # storage references the step's parquet path (not copied).
        assert doc["storage"]["format"] == "parquet"

    def test_uses_step_storage_path_directly(self, mongo_db, setup_steps):
        # The asset's storage path equals the step's storage path
        # (no copy in MVP — documented design decision).
        repo, did, sid, s1, _ = setup_steps
        svc = self._svc(repo)
        aid = svc.save_derived_table(
            session_id=sid, step_id=s1, name="x", user="alice",
        )
        asset = repo.get_asset(aid)
        step = repo.get_analysis_step(s1)
        assert asset["storage"]["path"] == step["storage"]["path"]

    def test_drift_check_works_on_new_asset(self, mongo_db, setup_steps):
        # After saving, drift check returns is_stale=False (no new
        # metadata version since save).
        repo, did, sid, s1, _ = setup_steps
        svc = self._svc(repo)
        aid = svc.save_derived_table(
            session_id=sid, step_id=s1, name="x", user="alice",
        )
        drift = svc.metadata_drift_check(aid)
        assert drift["is_stale"] is False
        assert drift["warning"] is None
        assert drift["asset_version"] == drift["active_version"]

    def test_drift_detected_after_metadata_bump(self, mongo_db, setup_steps):
        repo, did, sid, s1, _ = setup_steps
        svc = self._svc(repo)
        aid = svc.save_derived_table(
            session_id=sid, step_id=s1, name="x", user="alice",
        )
        # Simulate metadata change: write a new version + activate.
        repo.save_metadata_version(
            dataset_id=did,
            metadata={"dataset_id": did, "source_type": "upload",
                       "collections": {"sheet1": {"fields": {}}},
                       "kpi_definitions": {}, "data_limitations": {}},
            confirmation_status="confirmed", confirmed_by="bob",
            activate=True,
        )
        drift = svc.metadata_drift_check(aid)
        assert drift["is_stale"] is True
        assert drift["warning"] is not None
        assert drift["asset_version"] < drift["active_version"]

    def test_save_missing_step_raises(self, mongo_db, setup_steps):
        repo, did, sid, _, _ = setup_steps
        svc = self._svc(repo)
        with pytest.raises(ValueError, match="step `nope` not found"):
            svc.save_derived_table(
                session_id=sid, step_id="nope", name="x",
            )

    def test_save_failed_step_rejected(self, mongo_db, setup_steps):
        # Inject a failed step and try to save it.
        repo, did, sid, _, _ = setup_steps
        repo.save_analysis_step({
            "step_id": "step_fail", "session_id": sid,
            "dataset_id": did, "metadata_version": 1,
            "step_no": 99, "action_type": "add_column",
            "status": "failed",
            "error_message": "bogus formula",
        })
        svc = self._svc(repo)
        with pytest.raises(ValueError, match="completed"):
            svc.save_derived_table(
                session_id=sid, step_id="step_fail", name="x",
            )

    def test_save_visualize_step_rejected(self, mongo_db, setup_steps):
        # Visualize steps have no materialized data → can't be saved as
        # derived table. (User should save_chart() for them.)
        repo, did, sid, _, _ = setup_steps
        repo.save_analysis_step({
            "step_id": "step_viz", "session_id": sid,
            "dataset_id": did, "metadata_version": 1,
            "step_no": 100, "action_type": "visualize",
            "status": "completed",
            "chart_spec": {"chart_type": "bar"},
        })
        svc = self._svc(repo)
        with pytest.raises(ValueError, match="visualize"):
            svc.save_derived_table(
                session_id=sid, step_id="step_viz", name="x",
            )

    def test_save_cross_session_step_rejected(self, mongo_db, setup_steps):
        # Step belongs to a different session — must refuse to prevent
        # mis-linking lineage.
        repo, did, sid, s1, _ = setup_steps
        sid2 = repo.create_session(did, metadata_version=1, user="alice")
        svc = self._svc(repo)
        with pytest.raises(ValueError, match="does not belong"):
            svc.save_derived_table(
                session_id=sid2, step_id=s1, name="x",
            )


@pytest.mark.requires_mongo
class TestSaveTemplateFromSteps:
    """v0.18 M6 (Assets 2.0): replayable multi-step analysis template."""

    def _svc(self, repo):
        from metadata_correction_service import MetadataCorrectionService
        return AnalysisAssetService(repo, MetadataCorrectionService(repo))

    def test_basic_save_2_step_chain(self, mongo_db, setup_steps):
        repo, did, sid, s1, s2 = setup_steps
        svc = self._svc(repo)
        aid = svc.save_template_from_steps(
            session_id=sid, step_ids=[s1, s2],
            name="extract_then_agg",
            description="employees → sum salary per dept",
            user="alice",
        )
        doc = repo.get_asset(aid)
        assert doc["asset_type"] == "analysis_template"
        assert doc["dataset_id"] == did
        assert doc["metadata_version"] == 1
        assert doc["source_step_ids"] == [s1, s2]   # spec rule 27
        payload = doc["asset_payload"]
        assert payload["n_steps"] == 2
        # Replay payload preserves enough to re-execute later.
        assert payload["steps"][0]["action_type"] == "extract_data"
        assert payload["steps"][1]["action_type"] == "aggregate"
        # Aggregation params preserved (replay needs them).
        agg_params = payload["steps"][1]["params"]
        assert agg_params["group_by"] == ["dept"]
        assert agg_params["aggregations"][0]["function"] == "sum"

    def test_steps_sorted_by_step_no_in_payload(self, mongo_db, setup_steps):
        # Caller passes step_ids in arbitrary order — replay must use
        # original step_no order.
        repo, did, sid, s1, s2 = setup_steps
        svc = self._svc(repo)
        aid = svc.save_template_from_steps(
            session_id=sid, step_ids=[s2, s1],   # reversed!
            name="x",
        )
        steps = repo.get_asset(aid)["asset_payload"]["steps"]
        assert [s["step_no"] for s in steps] == [1, 2]

    def test_empty_step_ids_rejected(self, mongo_db, setup_steps):
        repo, _, sid, _, _ = setup_steps
        svc = self._svc(repo)
        with pytest.raises(ValueError, match="step_ids empty"):
            svc.save_template_from_steps(
                session_id=sid, step_ids=[], name="x",
            )

    def test_failed_step_in_chain_rejected(self, mongo_db, setup_steps):
        repo, did, sid, s1, _ = setup_steps
        repo.save_analysis_step({
            "step_id": "step_fail", "session_id": sid,
            "dataset_id": did, "metadata_version": 1,
            "step_no": 50, "action_type": "add_column",
            "status": "failed",
            "error_message": "bad formula",
        })
        svc = self._svc(repo)
        with pytest.raises(ValueError, match="status `failed`"):
            svc.save_template_from_steps(
                session_id=sid, step_ids=[s1, "step_fail"], name="x",
            )

    def test_unknown_step_id_rejected(self, mongo_db, setup_steps):
        repo, _, sid, s1, _ = setup_steps
        svc = self._svc(repo)
        with pytest.raises(ValueError, match="not found"):
            svc.save_template_from_steps(
                session_id=sid, step_ids=[s1, "step_nope"], name="x",
            )


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
