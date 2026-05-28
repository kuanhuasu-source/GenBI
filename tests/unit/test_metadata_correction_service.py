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


@pytest.mark.requires_mongo
class TestConfirmMetadataMergesRelationships:
    """v0.18 M7: confirmed/edited relationships project into the new
    metadata version's `relationships` field. Spec §14.5 #5."""

    def _setup(self, mongo_db, sample_metadata):
        from upload_repository import UploadRepository
        repo = UploadRepository(mongo_db)
        repo.ensure_indexes()
        repo.create_dataset({
            "_id": "ds-m7", "dataset_name": "test", "owner": "alice",
            "source_type": "file_upload", "file": {},
            "status": "profiled",
        })
        repo.save_metadata_version(
            dataset_id="ds-m7", metadata=sample_metadata,
            confirmation_status="draft", activate=True,
        )
        return repo

    def _sample_rel(self, rid, status="confirmed"):
        return {
            "relationship_id": rid,
            "from_table": "orders", "from_field": "customer_id",
            "to_table": "customers", "to_field": "customer_id",
            "relationship_type": "many_to_one",
            "default_join_type": "left",
            "confidence": 0.95,
            "status": status,
        }

    def test_no_rels_field_absent(self, mongo_db, sample_metadata):
        # When there are zero confirmed rels, metadata.relationships
        # must not appear on the new version (backward compat).
        repo = self._setup(mongo_db, sample_metadata)
        svc = MetadataCorrectionService(repo)
        result = svc.confirm_metadata("ds-m7", user="alice")
        assert result["n_relationships_merged"] == 0
        new_active = repo.get_active_metadata("ds-m7")
        assert "relationships" not in new_active["metadata"]

    def test_confirmed_rel_merged(self, mongo_db, sample_metadata):
        repo = self._setup(mongo_db, sample_metadata)
        repo.save_relationship_candidates(
            "ds-m7", [self._sample_rel("rel_a")], metadata_version=1,
        )
        svc = MetadataCorrectionService(repo)
        result = svc.confirm_metadata("ds-m7", user="alice")
        assert result["n_relationships_merged"] == 1
        new_active = repo.get_active_metadata("ds-m7")
        assert "relationships" in new_active["metadata"]
        rels = new_active["metadata"]["relationships"]
        assert len(rels) == 1
        assert rels[0]["relationship_id"] == "rel_a"
        assert rels[0]["from_table"] == "orders"
        assert rels[0]["status"] == "confirmed"

    def test_edited_status_also_merged(self, mongo_db, sample_metadata):
        # `edited` rels count as user-approved (same trust level as
        # confirmed) — must also flow into the new version.
        repo = self._setup(mongo_db, sample_metadata)
        repo.save_relationship_candidates(
            "ds-m7",
            [self._sample_rel("rel_edited", status="edited")],
            metadata_version=1,
        )
        svc = MetadataCorrectionService(repo)
        result = svc.confirm_metadata("ds-m7", user="alice")
        assert result["n_relationships_merged"] == 1
        rels = repo.get_active_metadata("ds-m7")["metadata"]["relationships"]
        assert rels[0]["status"] == "edited"

    def test_candidate_status_not_merged(self, mongo_db, sample_metadata):
        # Unreviewed candidates do NOT flow in — that would defeat
        # the HITL gate.
        repo = self._setup(mongo_db, sample_metadata)
        repo.save_relationship_candidates(
            "ds-m7",
            [self._sample_rel("rel_pending", status="candidate")],
            metadata_version=1,
        )
        svc = MetadataCorrectionService(repo)
        result = svc.confirm_metadata("ds-m7", user="alice")
        assert result["n_relationships_merged"] == 0

    def test_rejected_status_not_merged(self, mongo_db, sample_metadata):
        repo = self._setup(mongo_db, sample_metadata)
        repo.save_relationship_candidates(
            "ds-m7",
            [self._sample_rel("rel_rejected", status="rejected")],
            metadata_version=1,
        )
        svc = MetadataCorrectionService(repo)
        result = svc.confirm_metadata("ds-m7", user="alice")
        assert result["n_relationships_merged"] == 0

    def test_mixed_only_approved_merged(self, mongo_db, sample_metadata):
        # 1 confirmed + 1 candidate + 1 rejected + 1 edited = 2 merged
        repo = self._setup(mongo_db, sample_metadata)
        repo.save_relationship_candidates(
            "ds-m7",
            [
                self._sample_rel("rel_c", status="confirmed"),
                self._sample_rel("rel_p", status="candidate"),
                self._sample_rel("rel_r", status="rejected"),
                self._sample_rel("rel_e", status="edited"),
            ],
            metadata_version=1,
        )
        svc = MetadataCorrectionService(repo)
        result = svc.confirm_metadata("ds-m7", user="alice")
        assert result["n_relationships_merged"] == 2
        merged_ids = {
            r["relationship_id"]
            for r in repo.get_active_metadata("ds-m7")["metadata"]["relationships"]
        }
        assert merged_ids == {"rel_c", "rel_e"}

    def test_only_executable_fields_projected(self, mongo_db, sample_metadata):
        # Evidence + confidence should NOT leak into metadata —
        # those live in the dedicated upload_relationship_candidates
        # collection. metadata only carries what's needed at query time.
        repo = self._setup(mongo_db, sample_metadata)
        rel = self._sample_rel("rel_c")
        rel["evidence"] = {"name_similarity": 1.0}
        rel["confidence"] = 0.99
        repo.save_relationship_candidates("ds-m7", [rel], metadata_version=1)
        svc = MetadataCorrectionService(repo)
        svc.confirm_metadata("ds-m7", user="alice")
        merged = repo.get_active_metadata("ds-m7")["metadata"]["relationships"][0]
        assert "evidence" not in merged
        assert "confidence" not in merged
