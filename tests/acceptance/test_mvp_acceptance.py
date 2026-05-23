"""tests/acceptance/test_mvp_acceptance.py — Upload Workspace MVP acceptance criteria (M4c).

Implements spec §16.4 + §16.5 — 10 條 acceptance criteria 自動驗證。
Manual-only criteria(UI 行為、debug panel 顯示)列在 ACCEPTANCE.md。

# 自動可測 acceptance(本檔)
1. 可以上傳 CSV / Excel single sheet
2. 可以產生 physical profile + semantic metadata
3. 可以儲存 metadata version
4. 可以從 confirmed metadata 拿 source_type='upload' 旗標
5. 具備基本安全限制(file size / forbidden builtins / row limit)
6. Saved Chart 必須保存 query / metadata_version / Q schema / chart option / 產生 code
7. Saved Metric 必須寫回 dynamic metadata 的 kpi_definitions
8. Asset 必須綁定 dataset_id 與 metadata_version,以確保可追溯
9. PII 欄位該被 mark(spec §14.3)
10. Phase A/B 走 safe_exec sandbox(spec §14.2)

# 不可自動測,留 ACCEPTANCE.md manual check
- UI 在 metadata 未確認時顯示 warning badge(§10.7 #6)
- Debug panel 顯示每 phase output(§15)
- Rerun asset 能從 Saved Assets 重開(§16.5 #6)
- 圖表渲染 / fallback 表格顯示
"""

from __future__ import annotations

import pandas as pd
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.requires_mongo]


# ============================================================
# Acceptance #1:Upload CSV
# ============================================================
def test_acceptance_1_upload_csv(mongo_db, golden_data_dir, tmp_path):
    """spec §16.4 #1:可以上傳 CSV"""
    from upload_repository import UploadRepository
    from upload_service import UploadService

    repo = UploadRepository(mongo_db)
    repo.ensure_indexes()
    service = UploadService(upload_repo=repo, uploads_root=tmp_path)

    dataset_id = service.handle_upload(
        file_obj=(golden_data_dir / "projects_clean.csv").read_bytes(),
        filename="projects_clean.csv",
        owner="alice",
    )
    assert dataset_id.startswith("upload_")
    assert repo.get_dataset(dataset_id)["status"] == "profiled"


# ============================================================
# Acceptance #2:Physical profile + semantic metadata
# ============================================================
def test_acceptance_2_profile_and_semantic(mongo_db, golden_data_dir, tmp_path):
    """spec §16.4 #2:profile + metadata 都該產出"""
    from upload_repository import UploadRepository
    from upload_service import UploadService

    repo = UploadRepository(mongo_db)
    repo.ensure_indexes()
    service = UploadService(upload_repo=repo, uploads_root=tmp_path)

    did = service.handle_upload(
        (golden_data_dir / "projects_clean.csv").read_bytes(),
        "projects_clean.csv", "alice",
    )
    # Physical profile
    profile = repo.get_latest_profile(did)
    assert profile is not None
    assert all(
        "physical_type" in c for c in profile["tables"][0]["columns"]
    )
    # Semantic metadata
    md = repo.get_active_metadata(did)["metadata"]
    coll = md["collections"]["sheet1"]
    assert all(
        "semantic_role" in f for f in coll["fields"].values()
    )


# ============================================================
# Acceptance #3:Metadata version 該被 save
# ============================================================
def test_acceptance_3_metadata_versioning(mongo_db, golden_data_dir, tmp_path):
    """spec §16.4 #4:可以儲存 metadata version"""
    from upload_repository import UploadRepository
    from upload_service import UploadService
    from metadata_correction_service import MetadataCorrectionService

    repo = UploadRepository(mongo_db)
    repo.ensure_indexes()
    service = UploadService(upload_repo=repo, uploads_root=tmp_path)
    did = service.handle_upload(
        (golden_data_dir / "projects_clean.csv").read_bytes(),
        "x.csv", "alice",
    )
    # v1 draft
    assert repo.get_active_metadata(did)["version"] == 1
    # apply correction → v2
    MetadataCorrectionService(repo).apply_corrections(
        did,
        [{"target": "sheet1.leadtime.unit", "old_value": "",
           "new_value": "days", "reason": "test"}],
        user="alice",
    )
    versions = repo.list_metadata_versions(did)
    assert len(versions) == 2
    assert versions[0]["version"] == 2   # newest first
    assert versions[0]["is_active"] is True


# ============================================================
# Acceptance #4:source_type 旗標
# ============================================================
def test_acceptance_4_source_type_upload(mongo_db, golden_data_dir, tmp_path):
    """spec §6.3 + §11.2:upload metadata 必須帶 source_type='upload',
    LLMService 才能切 Phase 0 plan prompt 到 upload 版"""
    from upload_repository import UploadRepository
    from upload_service import UploadService
    from metadata_correction_service import MetadataCorrectionService
    from metadata_provider import UploadMetadataProvider

    repo = UploadRepository(mongo_db)
    repo.ensure_indexes()
    service = UploadService(upload_repo=repo, uploads_root=tmp_path)
    did = service.handle_upload(
        (golden_data_dir / "projects_clean.csv").read_bytes(),
        "x.csv", "alice",
    )
    MetadataCorrectionService(repo).confirm_metadata(did, user="alice")

    provider = UploadMetadataProvider(repo, require_confirmed=True)
    md = provider.get_metadata(did)
    assert md["source_type"] == "upload"


# ============================================================
# Acceptance #5:Safety limits
# ============================================================
def test_acceptance_5_file_size_limit():
    """spec §14.1:檔案 ≤ 100MB"""
    from file_parser import MAX_FILE_SIZE_BYTES, ALLOWED_EXTENSIONS
    assert MAX_FILE_SIZE_BYTES == 100 * 1024 * 1024
    assert ".csv" in ALLOWED_EXTENSIONS
    assert ".xlsx" in ALLOWED_EXTENSIONS
    # 危險副檔名不該在內
    for bad in (".exe", ".sh", ".py", ".js"):
        assert bad not in ALLOWED_EXTENSIONS


def test_acceptance_5_forbidden_builtins():
    """spec §14.2:Phase A/B exec 必須禁止 open / exec / eval / __import__"""
    from safe_exec import _SAFE_BUILTINS
    for forbidden in ("open", "exec", "eval", "compile",
                        "__import__", "globals", "locals"):
        assert forbidden not in _SAFE_BUILTINS, \
            f"{forbidden} 不該在 safe builtins"


def test_acceptance_5_phase_a_blocks_open(mongo_db, golden_data_dir, tmp_path):
    """spec §14.2:LLM 寫 open(...) 該被 safe_exec 攔下"""
    from safe_exec import safe_exec_pandas
    result = safe_exec_pandas(
        code="raw_df = open('/etc/passwd').read()",
        inputs={"source_df": pd.DataFrame({"x": [1]})},
        expected_output_var="raw_df",
    )
    assert result.success is False


# ============================================================
# Acceptance #6:Saved Chart 必含 query / md_version / Q schema /
#                              chart option / 產生 code
# ============================================================
def test_acceptance_6_saved_chart_lineage(
    mongo_db, golden_data_dir, tmp_path,
):
    """spec §12A.7 #2"""
    from upload_repository import UploadRepository
    from upload_service import UploadService
    from metadata_correction_service import MetadataCorrectionService
    from analysis_asset_service import AnalysisAssetService

    repo = UploadRepository(mongo_db)
    repo.ensure_indexes()
    service = UploadService(upload_repo=repo, uploads_root=tmp_path)
    did = service.handle_upload(
        (golden_data_dir / "projects_clean.csv").read_bytes(),
        "x.csv", "alice",
    )
    MetadataCorrectionService(repo).confirm_metadata(did, user="alice")
    sid = repo.create_session(did, metadata_version=2, user="alice")
    repo.append_message(sid, role="user", content="畫圖")

    fake_result = {
        "status": "completed", "trace_id": "t",
        "plan_text": "p", "phase_a_code": "a", "phase_b_code": "b",
        "phase_c_code": "c",
        "Q_info": {"n_rows": 5, "columns": ["a", "b"]},
        "raw_df_info": {"n_rows": 100, "columns": ["x"]},
        "Q": pd.DataFrame({"a": [1], "b": [2]}),
        "chart_option": {"series": [{"type": "bar"}]},
        "chart_fig": None, "use_table_fallback": False,
        "insight": "i", "is_followup": False, "error": None,
    }
    svc = AnalysisAssetService(repo, MetadataCorrectionService(repo))
    aid = svc.save_chart(did, sid, fake_result, "C", user="alice")
    doc = repo.get_asset(aid)

    # 必含 query
    assert doc["source_query"] == "畫圖"
    # 必含 metadata_version
    assert doc["metadata_version"] == 2
    # 必含 Q schema
    assert doc["asset_payload"]["q_columns"] == ["a", "b"]
    # 必含 chart_option
    assert doc["asset_payload"]["chart_option"] == {"series": [{"type": "bar"}]}
    # 必含 phase code
    assert doc["lineage"]["phase_a_code"] == "a"
    assert doc["lineage"]["phase_b_code"] == "b"
    assert doc["lineage"]["phase_c_code"] == "c"


# ============================================================
# Acceptance #7:Saved Metric 必寫回 kpi_definitions
# ============================================================
def test_acceptance_7_saved_metric_writeback(
    mongo_db, golden_data_dir, tmp_path,
):
    """spec §12A.7 #5"""
    from upload_repository import UploadRepository
    from upload_service import UploadService
    from metadata_correction_service import MetadataCorrectionService
    from analysis_asset_service import AnalysisAssetService

    repo = UploadRepository(mongo_db)
    repo.ensure_indexes()
    service = UploadService(upload_repo=repo, uploads_root=tmp_path)
    did = service.handle_upload(
        (golden_data_dir / "projects_clean.csv").read_bytes(),
        "x.csv", "alice",
    )
    MetadataCorrectionService(repo).confirm_metadata(did, user="alice")
    sid = repo.create_session(did, metadata_version=2, user="alice")
    repo.append_message(sid, role="user", content="算平均")

    fake_result = {
        "status": "completed", "trace_id": "t", "plan_text": "p",
        "phase_a_code": "a", "phase_b_code": "b", "phase_c_code": "c",
        "Q_info": {"n_rows": 1, "columns": ["x"]},
        "Q": pd.DataFrame({"x": [42]}),
        "chart_option": None, "chart_fig": None,
        "use_table_fallback": False, "insight": "",
        "is_followup": False, "error": None,
    }
    svc = AnalysisAssetService(repo, MetadataCorrectionService(repo))
    svc.save_metric(
        did, sid, fake_result,
        kpi_key="my_avg", name="My Avg", formula="mean(x)",
        important_note="unit=units", user="alice",
    )
    # kpi_definitions 該含 my_avg
    active = repo.get_active_metadata(did)
    kpis = active["metadata"]["kpi_definitions"]
    assert "my_avg" in kpis
    assert kpis["my_avg"]["formula"] == "mean(x)"


# ============================================================
# Acceptance #8:Asset 綁 dataset_id + metadata_version
# ============================================================
def test_acceptance_8_asset_lineage_binding(
    mongo_db, golden_data_dir, tmp_path,
):
    """spec §12A.7 #8"""
    from upload_repository import UploadRepository
    from upload_service import UploadService
    from metadata_correction_service import MetadataCorrectionService
    from analysis_asset_service import AnalysisAssetService

    repo = UploadRepository(mongo_db)
    repo.ensure_indexes()
    service = UploadService(upload_repo=repo, uploads_root=tmp_path)
    did = service.handle_upload(
        (golden_data_dir / "projects_clean.csv").read_bytes(),
        "x.csv", "alice",
    )
    MetadataCorrectionService(repo).confirm_metadata(did, user="alice")
    sid = repo.create_session(did, metadata_version=2, user="alice")
    repo.append_message(sid, role="user", content="q")

    fake_result = {
        "status": "completed", "trace_id": "t", "plan_text": "p",
        "phase_a_code": "", "phase_b_code": "", "phase_c_code": "",
        "Q_info": {"n_rows": 0, "columns": []}, "Q": pd.DataFrame(),
        "chart_option": None, "chart_fig": None,
        "use_table_fallback": False, "insight": "",
        "is_followup": False, "error": None,
    }
    svc = AnalysisAssetService(repo, MetadataCorrectionService(repo))
    aid = svc.save_chart(did, sid, fake_result, "x", user="alice")
    doc = repo.get_asset(aid)
    # 雙綁定必須有
    assert doc["dataset_id"] == did
    assert doc["metadata_version"] >= 1


# ============================================================
# Acceptance #9:PII 該被 mark
# ============================================================
def test_acceptance_9_pii_marked(mongo_db, golden_data_dir, tmp_path):
    """spec §14.3"""
    from upload_repository import UploadRepository
    from upload_service import UploadService

    repo = UploadRepository(mongo_db)
    repo.ensure_indexes()
    service = UploadService(upload_repo=repo, uploads_root=tmp_path)
    did = service.handle_upload(
        (golden_data_dir / "employee_pii.csv").read_bytes(),
        "pii.csv", "alice",
    )
    md = repo.get_active_metadata(did)["metadata"]
    fields = md["collections"]["sheet1"]["fields"]
    pii_cols = [n for n, f in fields.items() if f["semantic_role"] == "pii"]
    # email / phone / full_name / employee_id 該都 mark
    for must_pii in ("email", "phone", "full_name", "employee_id"):
        assert must_pii in pii_cols, f"{must_pii} 該被 mark pii"


# ============================================================
# Acceptance #10:Phase A/B 走 safe_exec sandbox
# ============================================================
def test_acceptance_10_safe_exec_wired():
    """spec §14.2:upload_analysis_service 必須走 safe_exec_pandas 而非裸 exec"""
    # Source code check — 確認 service 真的 import 並用了 safe_exec
    import inspect
    import upload_analysis_service
    src = inspect.getsource(upload_analysis_service)
    assert "safe_exec_pandas" in src, \
        "upload_analysis_service.py 沒 import safe_exec_pandas"
    assert "from safe_exec import" in src
    # 應該至少呼叫 2 次(Phase A + Phase B)
    assert src.count("safe_exec_pandas(") >= 2


# ============================================================
# Acceptance #11:沒 confirm 的 metadata 進 chat 該被擋
# ============================================================
def test_acceptance_11_unconfirmed_metadata_blocks_analysis(
    mongo_db, golden_data_dir, tmp_path,
):
    """spec §10.7 #6:未確認 metadata 進入分析時應顯示警告 / 阻止"""
    from upload_repository import UploadRepository
    from upload_service import UploadService
    from metadata_provider import UploadMetadataProvider

    repo = UploadRepository(mongo_db)
    repo.ensure_indexes()
    service = UploadService(upload_repo=repo, uploads_root=tmp_path)
    did = service.handle_upload(
        (golden_data_dir / "projects_clean.csv").read_bytes(),
        "x.csv", "alice",
    )
    # 沒 confirm → provider 該 raise
    provider = UploadMetadataProvider(repo, require_confirmed=True)
    with pytest.raises(KeyError):
        provider.get_metadata(did)


# ============================================================
# Acceptance #12:Identifier 該被標 is_identifier + 不可 sum / avg
# ============================================================
def test_acceptance_12_identifier_not_sum(
    mongo_db, golden_data_dir, tmp_path,
):
    """spec §10.7 #7:identifier 不得被 sum / avg"""
    from upload_repository import UploadRepository
    from upload_service import UploadService

    repo = UploadRepository(mongo_db)
    repo.ensure_indexes()
    service = UploadService(upload_repo=repo, uploads_root=tmp_path)
    did = service.handle_upload(
        (golden_data_dir / "projects_clean.csv").read_bytes(),
        "x.csv", "alice",
    )
    md = repo.get_active_metadata(did)["metadata"]
    pid = md["collections"]["sheet1"]["fields"]["project_id"]
    assert pid["semantic_role"] == "identifier"
    assert pid["is_identifier"] is True
    assert "sum" in pid["not_recommended_use"]
    assert "average" in pid["not_recommended_use"]
