"""tests/unit/test_metadata_correction_service.py — unit tests for metadata_correction_service.py (M4a)."""

from __future__ import annotations

import pytest

from metadata_correction_service import (
    MetadataCorrectionService,
    _apply_correction_to_dict,
)


# ============================================================
# Fixture:乾淨 metadata template
# ============================================================
@pytest.fixture
def sample_metadata():
    return {
        "dataset_id": "upload_test_001",
        "source_type": "upload",
        "business_context": {
            "business_description": "test desc",
            "main_business_questions": ["q1", "q2"],
        },
        "collections": {
            "sheet1": {
                "primary_key": "project_id",
                "grain": "每列代表一個 project",
                "fields": {
                    "project_id": {
                        "type": "string",
                        "semantic_role": "identifier",
                        "description": "Project ID",
                        "unit": "",
                        "default_aggregation": "no_agg",
                        "is_dimension": False,
                        "is_measure": False,
                        "is_identifier": True,
                        "user_confirmed": False,
                    },
                    "leadtime": {
                        "type": "number",
                        "semantic_role": "unknown",
                        "description": "Lead time",
                        "unit": "",
                        "default_aggregation": "no_agg",
                        "is_dimension": False,
                        "is_measure": False,
                        "is_identifier": False,
                        "user_confirmed": False,
                    },
                },
            },
        },
        "kpi_definitions": {
            "avg_leadtime": {
                "name": "Avg leadtime",
                "formula": "mean(leadtime)",
                "user_confirmed": False,
            },
        },
        "data_limitations": {
            "missing_dimensions": ["No date column"],
            "not_supported_analysis": [],
        },
    }


# ============================================================
# Path-based mutation
# ============================================================
class TestApplyCorrectionToDict:
    def test_field_attr_change(self, sample_metadata):
        ok = _apply_correction_to_dict(
            sample_metadata, "sheet1.leadtime.unit", "days",
        )
        assert ok
        assert sample_metadata["collections"]["sheet1"]["fields"]["leadtime"]["unit"] == "days"
        # User confirmed 該自動 set
        assert sample_metadata["collections"]["sheet1"]["fields"]["leadtime"]["user_confirmed"] is True

    def test_semantic_role_change_cascade(self, sample_metadata):
        """改 semantic_role,is_measure/is_identifier/default_aggregation 應連動"""
        ok = _apply_correction_to_dict(
            sample_metadata, "sheet1.leadtime.semantic_role", "measure_duration",
        )
        assert ok
        field = sample_metadata["collections"]["sheet1"]["fields"]["leadtime"]
        assert field["semantic_role"] == "measure_duration"
        assert field["is_measure"] is True
        assert field["default_aggregation"] == "avg"

    def test_grain_change(self, sample_metadata):
        ok = _apply_correction_to_dict(
            sample_metadata, "grain.sheet1", "每列代表一筆申請",
        )
        assert ok
        assert sample_metadata["collections"]["sheet1"]["grain"] == "每列代表一筆申請"

    def test_primary_key_change(self, sample_metadata):
        ok = _apply_correction_to_dict(
            sample_metadata, "primary_key.sheet1", "leadtime",
        )
        assert ok
        assert sample_metadata["collections"]["sheet1"]["primary_key"] == "leadtime"

    def test_primary_key_to_none(self, sample_metadata):
        ok = _apply_correction_to_dict(
            sample_metadata, "primary_key.sheet1", None,
        )
        assert ok
        assert sample_metadata["collections"]["sheet1"]["primary_key"] is None

    def test_data_limitations_missing(self, sample_metadata):
        new_list = ["No date column", "No amount column"]
        ok = _apply_correction_to_dict(
            sample_metadata, "data_limitations.missing_dimensions", new_list,
        )
        assert ok
        assert sample_metadata["data_limitations"]["missing_dimensions"] == new_list

    def test_business_description(self, sample_metadata):
        ok = _apply_correction_to_dict(
            sample_metadata, "business_description", "new desc",
        )
        assert ok
        assert sample_metadata["business_context"]["business_description"] == "new desc"

    def test_main_business_questions(self, sample_metadata):
        new_qs = ["Q new 1", "Q new 2", "Q new 3"]
        ok = _apply_correction_to_dict(
            sample_metadata, "main_business_questions", new_qs,
        )
        assert ok
        assert sample_metadata["business_context"]["main_business_questions"] == new_qs

    def test_kpi_attr(self, sample_metadata):
        ok = _apply_correction_to_dict(
            sample_metadata, "kpi.avg_leadtime.user_confirmed", True,
        )
        assert ok
        assert sample_metadata["kpi_definitions"]["avg_leadtime"]["user_confirmed"] is True


class TestApplyCorrectionInvalidPaths:
    def test_unknown_table(self, sample_metadata):
        ok = _apply_correction_to_dict(
            sample_metadata, "fake_table.col.attr", "value",
        )
        assert ok is False

    def test_unknown_column(self, sample_metadata):
        ok = _apply_correction_to_dict(
            sample_metadata, "sheet1.fake_col.unit", "days",
        )
        assert ok is False

    def test_unknown_kpi(self, sample_metadata):
        ok = _apply_correction_to_dict(
            sample_metadata, "kpi.fake_kpi.user_confirmed", True,
        )
        assert ok is False

    def test_malformed_path(self, sample_metadata):
        ok = _apply_correction_to_dict(sample_metadata, "", "x")
        assert ok is False


# ============================================================
# MetadataCorrectionService(用 mongomock)
# ============================================================
@pytest.mark.requires_mongo
class TestCorrectionServiceWithMongo:
    def _setup_active(self, mongo_db, sample_metadata):
        """先把 sample metadata 寫進 mongomock 當 active v1。"""
        from upload_repository import UploadRepository
        repo = UploadRepository(mongo_db)
        repo.ensure_indexes()
        # 建 dataset
        repo.create_dataset({
            "_id": "upload_test_001",
            "dataset_name": "test",
            "owner": "alice",
            "source_type": "file_upload",
            "file": {},
            "status": "profiled",
            "active_metadata_version": None,
            "error_message": None,
        })
        # 寫 v1
        repo.save_metadata_version(
            dataset_id="upload_test_001",
            metadata=sample_metadata,
            confirmation_status="draft",
            activate=True,
        )
        return repo

    def test_apply_corrections_creates_new_version(self, mongo_db, sample_metadata):
        repo = self._setup_active(mongo_db, sample_metadata)
        service = MetadataCorrectionService(repo)
        result = service.apply_corrections(
            dataset_id="upload_test_001",
            corrections=[{
                "target": "sheet1.leadtime.unit",
                "old_value": "",
                "new_value": "days",
                "reason": "test",
            }],
            user="alice",
        )
        assert result["applied"] == 1
        assert result["skipped"] == 0
        assert result["version"] == 2  # v1 → v2
        # 新版 active
        new_active = repo.get_active_metadata("upload_test_001")
        assert new_active["version"] == 2
        assert new_active["metadata"]["collections"]["sheet1"]["fields"]["leadtime"]["unit"] == "days"

    def test_apply_partial_skip(self, mongo_db, sample_metadata):
        """若有 invalid path,該 skip 但不擋其他正確的"""
        repo = self._setup_active(mongo_db, sample_metadata)
        service = MetadataCorrectionService(repo)
        result = service.apply_corrections(
            dataset_id="upload_test_001",
            corrections=[
                {"target": "sheet1.leadtime.unit", "old_value": "", "new_value": "days",
                 "reason": "ok"},
                {"target": "fake.bad.path", "old_value": "", "new_value": "x",
                 "reason": "bad"},
            ],
            user="alice",
        )
        assert result["applied"] == 1
        assert result["skipped"] == 1
        assert "fake.bad.path" in result["skipped_targets"]

    def test_confirm_metadata(self, mongo_db, sample_metadata):
        repo = self._setup_active(mongo_db, sample_metadata)
        service = MetadataCorrectionService(repo)
        result = service.confirm_metadata(
            dataset_id="upload_test_001",
            user="alice",
            notes="LGTM",
        )
        assert result["version"] == 2
        # 新版 status='confirmed'
        new_active = repo.get_active_metadata("upload_test_001")
        assert new_active["confirmation_status"] == "confirmed"
        assert new_active["confirmed_by"] == "alice"

    def test_confirm_already_confirmed(self, mongo_db, sample_metadata):
        repo = self._setup_active(mongo_db, sample_metadata)
        # Confirm 一次
        service = MetadataCorrectionService(repo)
        service.confirm_metadata(dataset_id="upload_test_001", user="alice")
        # 再 confirm:應該 short-circuit 不出新版
        result = service.confirm_metadata(dataset_id="upload_test_001", user="alice")
        assert result.get("already_confirmed") is True
