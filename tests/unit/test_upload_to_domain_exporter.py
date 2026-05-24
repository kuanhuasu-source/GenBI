"""tests/unit/test_upload_to_domain_exporter.py — unit tests for upload_to_domain_exporter.py (M5.4)."""

from __future__ import annotations

import copy
import pytest

from upload_to_domain_exporter import (
    UploadToDomainExporter,
    _normalize_domain_id,
    convert_upload_metadata_to_domain,
)


# ============================================================
# _normalize_domain_id
# ============================================================
class TestNormalizeDomainId:
    @pytest.mark.parametrize("raw,expected", [
        ("projects_clean.csv", "projects_clean"),
        ("Project Leadtime.xlsx", "project_leadtime"),
        ("sales_amount.parquet", "sales_amount"),
        ("   weird-name!!  ", "weird_name"),
        ("中文檔名.csv", "uploaded_domain"),   # 純中文無 ascii → fallback
        ("", "uploaded_domain"),
    ])
    def test_normalizes(self, raw, expected):
        assert _normalize_domain_id(raw) == expected


# ============================================================
# convert_upload_metadata_to_domain
# ============================================================
@pytest.fixture
def sample_upload_metadata():
    return {
        "dataset_id": "upload_xxx",
        "dataset_name": "projects.csv",
        "source_type": "upload",
        "business_context": {
            "business_description": "test",
            "main_business_questions": ["q1"],
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
                        "rule_hits": ["id_name:project_id"],
                        "llm_used": False,
                    },
                    "leadtime": {
                        "type": "number",
                        "semantic_role": "measure_duration",
                        "description": "Lead time",
                        "rule_hits": ["duration_name:leadtime"],
                    },
                },
            },
        },
        "kpi_definitions": {
            "avg_leadtime": {"name": "Avg leadtime", "formula": "mean(leadtime)"},
        },
        "data_limitations": {"missing_dimensions": ["No date"]},
        "charting_guidance": {},
    }


class TestConvertMetadata:
    def test_removes_source_type(self, sample_upload_metadata):
        out = convert_upload_metadata_to_domain(
            sample_upload_metadata, target_domain_id="projects_q1",
        )
        assert "source_type" not in out

    def test_sets_dataset_id(self, sample_upload_metadata):
        out = convert_upload_metadata_to_domain(
            sample_upload_metadata, target_domain_id="projects_q1",
        )
        assert out["dataset_id"] == "projects_q1"

    def test_renames_collection(self, sample_upload_metadata):
        out = convert_upload_metadata_to_domain(
            sample_upload_metadata,
            target_domain_id="x",
            target_collection_name="projects",
        )
        assert "projects" in out["collections"]
        assert "sheet1" not in out["collections"]
        # 內容仍保留
        assert "project_id" in out["collections"]["projects"]["fields"]

    def test_strips_upload_specific_field_keys(self, sample_upload_metadata):
        out = convert_upload_metadata_to_domain(
            sample_upload_metadata, target_domain_id="x",
        )
        for f in out["collections"]["sheet1"]["fields"].values():
            assert "rule_hits" not in f
            assert "llm_used" not in f

    def test_fills_recommended_mongodb(self, sample_upload_metadata):
        # 原本沒這 key
        sample_upload_metadata.pop("recommended_mongodb", None)
        out = convert_upload_metadata_to_domain(
            sample_upload_metadata, target_domain_id="x",
        )
        assert "recommended_mongodb" in out
        assert out["recommended_mongodb"]["database"].startswith("graduated_")

    def test_input_not_mutated(self, sample_upload_metadata):
        before = copy.deepcopy(sample_upload_metadata)
        convert_upload_metadata_to_domain(
            sample_upload_metadata, target_domain_id="x",
        )
        # 原 dict 不該被改
        assert sample_upload_metadata == before


# ============================================================
# Graduate flow(用 mongomock)
# ============================================================
@pytest.mark.requires_mongo
class TestGraduateFlow:
    def test_graduate_confirmed_dataset(self, mongo_db, sample_upload_metadata):
        from upload_repository import UploadRepository
        from prompt_repository import PromptRepository

        upload_repo = UploadRepository(mongo_db)
        upload_repo.ensure_indexes()
        # 建 upload dataset + confirmed metadata
        upload_repo.create_dataset({
            "_id": "upload_xxx", "dataset_name": "projects.csv",
            "owner": "alice", "source_type": "file_upload",
            "file": {}, "status": "profiled",
        })
        upload_repo.save_metadata_version(
            dataset_id="upload_xxx",
            metadata=sample_upload_metadata,
            confirmation_status="confirmed",
            confirmed_by="alice",
            activate=True,
        )

        # Prompt repo:明確 enabled=True 走 mongomock(不靠 config PROMPT_REPO_ENABLED env)
        prompt_repo = PromptRepository(
            mongo_db=mongo_db,
            embedded_fallback={},
            enabled=True,
        )

        exporter = UploadToDomainExporter(prompt_repo, upload_repo)
        result = exporter.graduate(
            dataset_id="upload_xxx",
            target_domain_id="projects_q1",
            target_collection_name="projects",
            user="alice",
            notes="Test graduate",
        )
        assert result["status"] == "graduated"
        assert result["domain_id"] == "projects_q1"
        # 已寫入 domain_metadata,prompt_repo 該能拿到
        # cache TTL 可能還在,先 invalidate
        prompt_repo.invalidate_all()
        graduated = prompt_repo.get_metadata("projects_q1")
        assert graduated["dataset_id"] == "projects_q1"
        assert "source_type" not in graduated   # 已 strip
        # Collection 該 rename 成 "projects"
        assert "projects" in graduated["collections"]

    def test_refuse_unconfirmed(self, mongo_db, sample_upload_metadata):
        from upload_repository import UploadRepository
        from prompt_repository import PromptRepository

        upload_repo = UploadRepository(mongo_db)
        upload_repo.ensure_indexes()
        upload_repo.create_dataset({
            "_id": "upload_xxx", "dataset_name": "x.csv",
            "owner": "a", "source_type": "file_upload",
            "file": {}, "status": "profiled",
        })
        # draft 狀態
        upload_repo.save_metadata_version(
            dataset_id="upload_xxx",
            metadata=sample_upload_metadata,
            confirmation_status="draft",
            activate=True,
        )
        prompt_repo = PromptRepository(
            mongo_db=mongo_db, embedded_fallback={}, enabled=True,
        )
        exporter = UploadToDomainExporter(prompt_repo, upload_repo)
        with pytest.raises(ValueError, match="confirmed"):
            exporter.graduate(dataset_id="upload_xxx", user="alice")

    def test_auto_derive_domain_id(self, mongo_db, sample_upload_metadata):
        from upload_repository import UploadRepository
        from prompt_repository import PromptRepository

        upload_repo = UploadRepository(mongo_db)
        upload_repo.create_dataset({
            "_id": "upload_xxx", "dataset_name": "Sales Q1 Report.xlsx",
            "owner": "a", "source_type": "file_upload",
            "file": {}, "status": "profiled",
        })
        upload_repo.save_metadata_version(
            dataset_id="upload_xxx",
            metadata=sample_upload_metadata,
            confirmation_status="confirmed", confirmed_by="alice",
            activate=True,
        )
        prompt_repo = PromptRepository(
            mongo_db=mongo_db, embedded_fallback={}, enabled=True,
        )
        exporter = UploadToDomainExporter(prompt_repo, upload_repo)
        # 不指定 target_domain_id → 自動從 dataset_name 推
        result = exporter.graduate(dataset_id="upload_xxx", user="a")
        assert result["domain_id"] == "sales_q1_report"
