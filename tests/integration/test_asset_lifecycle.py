"""tests/integration/test_asset_lifecycle.py — Save Chart/Metric/Template + rerun (M4b)."""

from __future__ import annotations

import pandas as pd
import pytest


@pytest.fixture
def setup_confirmed_dataset(mongo_db, golden_data_dir, tmp_path):
    """Upload + confirm dataset,回 (repo, dataset_id, session_id)。"""
    from upload_repository import UploadRepository
    from upload_service import UploadService
    from metadata_correction_service import MetadataCorrectionService

    repo = UploadRepository(mongo_db)
    repo.ensure_indexes()
    service = UploadService(upload_repo=repo, uploads_root=tmp_path)
    csv_bytes = (golden_data_dir / "projects_clean.csv").read_bytes()
    dataset_id = service.handle_upload(
        file_obj=csv_bytes, filename="x.csv", owner="alice",
    )
    # Confirm metadata → v2
    MetadataCorrectionService(repo).confirm_metadata(
        dataset_id=dataset_id, user="alice",
    )
    # 開 session
    sid = repo.create_session(dataset_id, metadata_version=2, user="alice")
    repo.append_message(sid, role="user", content="比較各 category 的 leadtime")
    return repo, dataset_id, sid


@pytest.fixture
def fake_completed_result():
    return {
        "status": "completed",
        "intent": "analysis",
        "trace_id": "trace-abc",
        "plan_text": "## A. ...\n## B. ...\n## C. ...",
        "phase_a_code": "raw_df = source_df.copy()",
        "phase_b_code": "Q = raw_df.groupby('category').agg(avg_leadtime=('leadtime','mean'))",
        "phase_c_code": "option = {...}",
        "raw_df_info": {"n_rows": 15, "columns": ["category", "leadtime"]},
        "Q_info": {"n_rows": 5, "columns": ["category", "avg_leadtime"]},
        "Q": pd.DataFrame({
            "category": ["Web", "Mobile", "Infra", "Backend", "Web"],
            "avg_leadtime": [45, 80, 170, 110, 50],
        }),
        "chart_option": {"series": [{"type": "bar"}]},
        "chart_fig": None,
        "use_table_fallback": False,
        "insight": "Insight text",
        "is_followup": False,
        "error": None,
    }


# ============================================================
# Test 7:Save Chart 完整 lifecycle
# ============================================================
@pytest.mark.integration
@pytest.mark.requires_mongo
def test_e2e_save_chart_and_reopen(
    mongo_db, setup_confirmed_dataset, fake_completed_result,
):
    """spec §16.2 #11:Save Chart → 重新 list / get / rename / delete。"""
    from analysis_asset_service import AnalysisAssetService
    from metadata_correction_service import MetadataCorrectionService

    repo, did, sid = setup_confirmed_dataset
    svc = AnalysisAssetService(repo, MetadataCorrectionService(repo))

    # Save
    aid = svc.save_chart(
        dataset_id=did, session_id=sid,
        analysis_result=fake_completed_result,
        name="Category leadtime", description="for weekly review",
        user="alice",
    )
    assert aid.startswith("chart_")

    # List
    assets = svc.list(dataset_id=did, asset_type="saved_chart")
    assert len(assets) == 1
    assert assets[0]["name"] == "Category leadtime"

    # Get
    doc = svc.get(aid)
    assert doc["lineage"]["phase_a_code"] == "raw_df = source_df.copy()"

    # Rename
    svc.rename(aid, "Weekly review", description="updated")
    assert svc.get(aid)["name"] == "Weekly review"

    # Delete(soft)
    svc.delete(aid)
    assert svc.list(dataset_id=did, asset_type="saved_chart") == []
    # Include inactive
    inactive = svc.list(dataset_id=did, asset_type="saved_chart",
                          include_inactive=True)
    assert len(inactive) == 1


# ============================================================
# Test 8:Save Metric 寫回 metadata + 後續 query 可引用
# ============================================================
@pytest.mark.integration
@pytest.mark.requires_mongo
def test_e2e_save_metric_writes_back_metadata(
    mongo_db, setup_confirmed_dataset, fake_completed_result,
):
    """spec §16.2 #12 + §10.7 #6:Save Metric → kpi_definitions 該新增。"""
    from analysis_asset_service import AnalysisAssetService
    from metadata_correction_service import MetadataCorrectionService

    repo, did, sid = setup_confirmed_dataset
    svc = AnalysisAssetService(repo, MetadataCorrectionService(repo))

    # Save metric → v3 metadata 該生成
    aid = svc.save_metric(
        dataset_id=did, session_id=sid,
        analysis_result=fake_completed_result,
        kpi_key="avg_leadtime",
        name="平均 Lead Time",
        formula="mean(leadtime)",
        important_note="unit=days",
        user="alice",
    )
    assert aid.startswith("metric_")

    # Active metadata 該變 v3 + kpi_definitions 該含新 KPI
    active = repo.get_active_metadata(did)
    assert active["version"] == 3   # v1 draft, v2 confirmed, v3 save metric
    kpis = active["metadata"]["kpi_definitions"]
    assert "avg_leadtime" in kpis
    assert kpis["avg_leadtime"]["formula"] == "mean(leadtime)"
    assert kpis["avg_leadtime"]["user_confirmed"] is True
    # important_note 該帶 unit
    assert "days" in kpis["avg_leadtime"]["important_note"]


# ============================================================
# Test 9:Drift check — metadata 變了之後,舊 asset 該 stale
# ============================================================
@pytest.mark.integration
@pytest.mark.requires_mongo
def test_e2e_metadata_drift_warning(
    mongo_db, setup_confirmed_dataset, fake_completed_result,
):
    """spec §12A.7 #9:asset.metadata_version 過期該提示。"""
    from analysis_asset_service import AnalysisAssetService
    from metadata_correction_service import MetadataCorrectionService

    repo, did, sid = setup_confirmed_dataset
    svc = AnalysisAssetService(repo, MetadataCorrectionService(repo))

    # Save chart at v2
    chart_aid = svc.save_chart(
        dataset_id=did, session_id=sid,
        analysis_result=fake_completed_result,
        name="Old chart", user="alice",
    )
    # Fresh
    drift = svc.metadata_drift_check(chart_aid)
    assert drift["is_stale"] is False

    # Save metric 後 v3 → 舊 chart 該 stale
    svc.save_metric(
        dataset_id=did, session_id=sid,
        analysis_result=fake_completed_result,
        kpi_key="x", name="X", formula="f", user="alice",
    )
    drift = svc.metadata_drift_check(chart_aid)
    assert drift["is_stale"] is True
    assert drift["warning"] is not None
    assert "v2" in drift["warning"]
    assert "v3" in drift["warning"]


# ============================================================
# Test 10:Analysis Template + replay query
# ============================================================
@pytest.mark.integration
@pytest.mark.requires_mongo
def test_e2e_template_save_and_replay(
    mongo_db, setup_confirmed_dataset, fake_completed_result,
):
    """spec §16.2 + §12A.4:Save Template → replay 該回 saved query。"""
    from analysis_asset_service import AnalysisAssetService
    from metadata_correction_service import MetadataCorrectionService

    repo, did, sid = setup_confirmed_dataset
    svc = AnalysisAssetService(repo, MetadataCorrectionService(repo))

    aid = svc.save_template(
        dataset_id=did, session_id=sid,
        analysis_result=fake_completed_result,
        name="Category leadtime template", user="alice",
    )
    assert aid.startswith("tmpl_")
    # Replay 該回 session 的 user query
    q = svc.get_replay_query(aid)
    assert q == "比較各 category 的 leadtime"
    # template_steps 該保存 plan
    doc = svc.get(aid)
    steps = doc["asset_payload"]["template_steps"]
    assert "query" in steps
    assert "plan_text" in steps


# ============================================================
# Test 11:UploadMetadataProvider 對 confirmed dataset OK
# ============================================================
@pytest.mark.integration
@pytest.mark.requires_mongo
def test_e2e_upload_metadata_provider(
    mongo_db, setup_confirmed_dataset,
):
    """UploadMetadataProvider 該能拿 confirmed dataset 的 metadata,
    且回的 dict 含 source_type='upload' 旗標。"""
    from metadata_provider import UploadMetadataProvider

    repo, did, sid = setup_confirmed_dataset
    provider = UploadMetadataProvider(repo, require_confirmed=True)
    md = provider.get_metadata(did)
    assert md["source_type"] == "upload"
    assert md["dataset_id"] == did
    # list_available 該含此 dataset
    available = provider.list_available()
    assert did in available


# ============================================================
# Test 12:Provider 拒絕 unconfirmed metadata
# ============================================================
@pytest.mark.integration
@pytest.mark.requires_mongo
def test_e2e_provider_blocks_unconfirmed(
    mongo_db, golden_data_dir, tmp_path,
):
    """spec §10.7 #6:未 confirm 該擋。"""
    from metadata_provider import UploadMetadataProvider
    from upload_repository import UploadRepository
    from upload_service import UploadService

    repo = UploadRepository(mongo_db)
    repo.ensure_indexes()
    service = UploadService(upload_repo=repo, uploads_root=tmp_path)
    csv_bytes = (golden_data_dir / "projects_clean.csv").read_bytes()
    dataset_id = service.handle_upload(
        file_obj=csv_bytes, filename="x.csv", owner="alice",
    )
    # 沒 confirm → provider 該 raise
    provider = UploadMetadataProvider(repo, require_confirmed=True)
    with pytest.raises(KeyError, match="confirmed"):
        provider.get_metadata(dataset_id)
    # list_available 該不含此 dataset
    assert dataset_id not in provider.list_available()
