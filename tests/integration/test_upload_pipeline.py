"""tests/integration/test_upload_pipeline.py — e2e upload + parse + profile + metadata (M4b)."""

from __future__ import annotations

import pytest


# ============================================================
# Test 1:乾淨 CSV 完整 pipeline 跑通
# ============================================================
@pytest.mark.integration
@pytest.mark.requires_mongo
def test_e2e_clean_csv_upload_to_metadata(
    mongo_db, golden_data_dir, tmp_path,
):
    """spec §16.2 #1:upload 乾淨 CSV → parse → profile → metadata v1 draft。"""
    from upload_repository import UploadRepository
    from upload_service import UploadService

    repo = UploadRepository(mongo_db)
    repo.ensure_indexes()
    service = UploadService(upload_repo=repo, uploads_root=tmp_path)

    csv_bytes = (golden_data_dir / "projects_clean.csv").read_bytes()
    dataset_id = service.handle_upload(
        file_obj=csv_bytes,
        filename="projects_clean.csv",
        owner="alice",
    )

    # 1. Dataset 寫入
    dataset = repo.get_dataset(dataset_id)
    assert dataset is not None
    assert dataset["status"] == "profiled"
    assert dataset["file"]["file_type"] == "csv"

    # 2. Table profile
    tables = repo.list_tables(dataset_id)
    assert len(tables) == 1
    assert tables[0]["row_count"] == 15
    assert tables[0]["column_count"] == 6

    # 3. Profile 寫入
    profile = repo.get_latest_profile(dataset_id)
    assert profile["profile_version"] == 1
    assert len(profile["tables"][0]["columns"]) == 6

    # 4. Metadata v1 draft 已產
    active = repo.get_active_metadata(dataset_id)
    assert active is not None
    assert active["version"] == 1
    assert active["confirmation_status"] == "draft"
    md = active["metadata"]
    assert md["source_type"] == "upload"
    # 該偵測出 project_id 為 identifier
    pid = md["collections"]["sheet1"]["fields"]["project_id"]
    assert pid["semantic_role"] == "identifier"


# ============================================================
# Test 2:PII dataset 上傳,PII 欄該被 mark
# ============================================================
@pytest.mark.integration
@pytest.mark.requires_mongo
def test_e2e_pii_dataset_marks_pii_fields(
    mongo_db, golden_data_dir, tmp_path,
):
    """spec §14.3:upload 含 PII 的 dataset → email/phone/employee_id 該 mark pii。"""
    from upload_repository import UploadRepository
    from upload_service import UploadService

    repo = UploadRepository(mongo_db)
    repo.ensure_indexes()
    service = UploadService(upload_repo=repo, uploads_root=tmp_path)

    csv_bytes = (golden_data_dir / "employee_pii.csv").read_bytes()
    dataset_id = service.handle_upload(
        file_obj=csv_bytes,
        filename="employee_pii.csv",
        owner="alice",
    )

    active = repo.get_active_metadata(dataset_id)
    fields = active["metadata"]["collections"]["sheet1"]["fields"]
    # PII 欄位 semantic_role 該 = 'pii'
    assert fields["email"]["semantic_role"] == "pii"
    assert fields["phone"]["semantic_role"] == "pii"
    assert fields["full_name"]["semantic_role"] == "pii"
    assert fields["employee_id"]["semantic_role"] == "pii"
    # salary / department 不該 mark
    assert fields["salary"]["semantic_role"] != "pii"
    assert fields["department"]["semantic_role"] != "pii"


# ============================================================
# Test 3:沒日期欄,trend 不支援
# ============================================================
@pytest.mark.integration
@pytest.mark.requires_mongo
def test_e2e_no_date_column_blocks_trend(
    mongo_db, golden_data_dir, tmp_path,
):
    """spec §16.2 #4:沒日期欄,data_limitations 該反映"""
    from upload_repository import UploadRepository
    from upload_service import UploadService

    repo = UploadRepository(mongo_db)
    repo.ensure_indexes()
    service = UploadService(upload_repo=repo, uploads_root=tmp_path)

    csv_bytes = (golden_data_dir / "projects_no_date.csv").read_bytes()
    dataset_id = service.handle_upload(
        file_obj=csv_bytes,
        filename="projects_no_date.csv",
        owner="alice",
    )

    active = repo.get_active_metadata(dataset_id)
    lim = active["metadata"]["data_limitations"]
    # missing_dimensions 該含 date
    assert any("date" in m.lower() for m in lim["missing_dimensions"])
    # not_supported_analysis 該含 trend
    assert any("trend" in n.lower() or "趨勢" in n
                for n in lim["not_supported_analysis"])


# ============================================================
# Test 4:有日期欄,trend 應該不被阻
# ============================================================
@pytest.mark.integration
@pytest.mark.requires_mongo
def test_e2e_with_date_column_allows_trend(
    mongo_db, golden_data_dir, tmp_path,
):
    from upload_repository import UploadRepository
    from upload_service import UploadService

    repo = UploadRepository(mongo_db)
    repo.ensure_indexes()
    service = UploadService(upload_repo=repo, uploads_root=tmp_path)

    csv_bytes = (golden_data_dir / "projects_with_date.csv").read_bytes()
    dataset_id = service.handle_upload(
        file_obj=csv_bytes,
        filename="projects_with_date.csv",
        owner="alice",
    )

    active = repo.get_active_metadata(dataset_id)
    lim = active["metadata"]["data_limitations"]
    # 不該標 "No date column"
    assert not any("no confirmed date" in m.lower()
                    for m in lim["missing_dimensions"])
    # 該至少有一個 datetime/date dimension 欄位
    fields = active["metadata"]["collections"]["sheet1"]["fields"]
    date_fields = [f for f in fields.values()
                    if f["semantic_role"] in ("date_dimension", "datetime_dimension")]
    assert len(date_fields) > 0


# ============================================================
# Test 5:metadata correction 後產新版,active 切到新版
# ============================================================
@pytest.mark.integration
@pytest.mark.requires_mongo
def test_e2e_correction_creates_new_version(
    mongo_db, golden_data_dir, tmp_path,
):
    """spec §16.2 #6:status code 經 user 修正後,後續分析採用修正後語意。"""
    from upload_repository import UploadRepository
    from upload_service import UploadService
    from metadata_correction_service import MetadataCorrectionService

    repo = UploadRepository(mongo_db)
    repo.ensure_indexes()
    service = UploadService(upload_repo=repo, uploads_root=tmp_path)

    csv_bytes = (golden_data_dir / "projects_clean.csv").read_bytes()
    dataset_id = service.handle_upload(
        file_obj=csv_bytes, filename="projects_clean.csv",
        owner="alice",
    )
    # v1 draft 該已存在
    active = repo.get_active_metadata(dataset_id)
    assert active["version"] == 1

    # User 改 leadtime 的 unit
    corrections_svc = MetadataCorrectionService(repo)
    result = corrections_svc.apply_corrections(
        dataset_id=dataset_id,
        corrections=[{
            "target": "sheet1.leadtime.unit",
            "old_value": "",
            "new_value": "days",
            "reason": "user confirmed",
        }],
        user="alice",
    )
    assert result["version"] == 2
    # 新 active 該是 v2
    new_active = repo.get_active_metadata(dataset_id)
    assert new_active["version"] == 2
    leadtime = new_active["metadata"]["collections"]["sheet1"]["fields"]["leadtime"]
    assert leadtime["unit"] == "days"
    assert leadtime["user_confirmed"] is True


# ============================================================
# Test 6:Confirm 流程
# ============================================================
@pytest.mark.integration
@pytest.mark.requires_mongo
def test_e2e_confirm_metadata_workflow(
    mongo_db, golden_data_dir, tmp_path,
):
    """spec §10.7 #5:Confirm 後 confirmation_status='confirmed'。"""
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

    # v1 是 draft
    assert repo.get_active_metadata(dataset_id)["confirmation_status"] == "draft"

    # Confirm
    corrections_svc = MetadataCorrectionService(repo)
    result = corrections_svc.confirm_metadata(
        dataset_id=dataset_id, user="alice", notes="LGTM",
    )
    assert result["version"] == 2

    # v2 active + confirmed
    active = repo.get_active_metadata(dataset_id)
    assert active["confirmation_status"] == "confirmed"
    assert active["confirmed_by"] == "alice"
