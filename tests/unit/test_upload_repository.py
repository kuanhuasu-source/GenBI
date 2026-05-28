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

    def test_extra_fields_persist_through_create(self, mongo_db):
        # Spec §5.2 — upload_tables doc includes sheet_name / table_role /
        # grain / primary_key. create_table inserts the dict as-is, so extra
        # fields must round-trip. Regression guard: a future "strict schema"
        # change shouldn't silently drop these.
        repo = UploadRepository(mongo_db)
        repo.create_table({
            "dataset_id": "ds-1", "table_id": "tbl_employee",
            "table_name": "employee", "sheet_name": "Employee",
            "row_count": 10, "column_count": 3,
            "storage": {"format": "parquet", "path": "p"},
            "table_role": "dimension", "grain": "one row per employee",
            "primary_key": ["employee_id"],
        })
        t = repo.get_table("ds-1", "tbl_employee")
        assert t["sheet_name"] == "Employee"
        assert t["table_role"] == "dimension"
        assert t["grain"] == "one row per employee"
        assert t["primary_key"] == ["employee_id"]


@pytest.mark.requires_mongo
class TestUpdateTableProfileFields:
    """Spec §5.2 fields set after profiling (profile_multi_table → repo)."""

    def _seed_table(self, repo, dataset_id="ds-1", table_id="tbl_employee"):
        repo.create_table({
            "dataset_id": dataset_id, "table_id": table_id,
            "table_name": "employee",
            "row_count": 10, "column_count": 3,
            "storage": {"format": "parquet", "path": "p"},
        })

    def test_sets_all_fields(self, mongo_db):
        repo = UploadRepository(mongo_db)
        self._seed_table(repo)
        ok = repo.update_table_profile_fields(
            "ds-1", "tbl_employee",
            sheet_name="Employee",
            table_role="dimension",
            grain="one row per employee",
            primary_key=["employee_id"],
            profile_version=1,
        )
        assert ok is True
        t = repo.get_table("ds-1", "tbl_employee")
        assert t["sheet_name"] == "Employee"
        assert t["table_role"] == "dimension"
        assert t["grain"] == "one row per employee"
        assert t["primary_key"] == ["employee_id"]
        assert t["profile_version"] == 1
        assert "updated_at" in t   # timestamp written

    def test_partial_update_preserves_other_fields(self, mongo_db):
        # If caller only updates table_role, the existing grain/PK must stay.
        repo = UploadRepository(mongo_db)
        self._seed_table(repo)
        repo.update_table_profile_fields(
            "ds-1", "tbl_employee",
            table_role="dimension",
            grain="one row per employee",
            primary_key=["employee_id"],
        )
        # Second call: only flip table_role to "fact" — other fields stay.
        ok = repo.update_table_profile_fields(
            "ds-1", "tbl_employee", table_role="fact",
        )
        assert ok is True
        t = repo.get_table("ds-1", "tbl_employee")
        assert t["table_role"] == "fact"
        assert t["grain"] == "one row per employee"   # untouched
        assert t["primary_key"] == ["employee_id"]    # untouched

    def test_unrelated_fields_untouched(self, mongo_db):
        # row_count / column_count / storage / table_name (set at create_table
        # time) must survive a profile-fields update.
        repo = UploadRepository(mongo_db)
        self._seed_table(repo)
        repo.update_table_profile_fields(
            "ds-1", "tbl_employee", table_role="dimension",
        )
        t = repo.get_table("ds-1", "tbl_employee")
        assert t["row_count"] == 10
        assert t["column_count"] == 3
        assert t["table_name"] == "employee"
        assert t["storage"]["path"] == "p"

    def test_missing_table_returns_false(self, mongo_db):
        repo = UploadRepository(mongo_db)
        ok = repo.update_table_profile_fields(
            "ds-nonexistent", "tbl_nothing", table_role="fact",
        )
        assert ok is False

    def test_no_fields_returns_false(self, mongo_db):
        # Empty update — short-circuit, don't even hit Mongo.
        repo = UploadRepository(mongo_db)
        self._seed_table(repo)
        ok = repo.update_table_profile_fields("ds-1", "tbl_employee")
        assert ok is False

    def test_can_clear_with_empty_list(self, mongo_db):
        # Per docstring: pass [] to explicitly clear a primary_key.
        # None is "leave alone", [] is "no PK now".
        repo = UploadRepository(mongo_db)
        self._seed_table(repo)
        repo.update_table_profile_fields(
            "ds-1", "tbl_employee", primary_key=["employee_id"],
        )
        repo.update_table_profile_fields(
            "ds-1", "tbl_employee", primary_key=[],
        )
        t = repo.get_table("ds-1", "tbl_employee")
        assert t["primary_key"] == []

    def test_composite_primary_key_persists(self, mongo_db):
        # Bridge tables have composite PKs — verify multi-element list
        # round-trips correctly.
        repo = UploadRepository(mongo_db)
        self._seed_table(repo, table_id="tbl_emp_proj")
        repo.update_table_profile_fields(
            "ds-1", "tbl_emp_proj",
            table_role="bridge",
            primary_key=["employee_id", "project_id"],
        )
        t = repo.get_table("ds-1", "tbl_emp_proj")
        assert t["primary_key"] == ["employee_id", "project_id"]
        assert t["table_role"] == "bridge"


@pytest.mark.requires_mongo
class TestRelationshipCandidatesCRUD:
    """v0.18 M2 · spec §5.3 upload_relationship_candidates"""

    def _sample(self, rid="rel_orders_customers_customer_id"):
        return {
            "relationship_id": rid,
            "from_table": "orders", "from_field": "customer_id",
            "to_table": "customers", "to_field": "customer_id",
            "relationship_type": "many_to_one",
            "default_join_type": "left",
            "confidence": 0.91,
            "confidence_tier": "high",
            "evidence": {
                "name_similarity": 1.0, "type_compatible": True,
                "from_to_overlap_ratio": 0.98, "to_unique_ratio": 0.997,
                "sample_match_count": 50,
            },
            "status": "candidate",
        }

    def test_save_bulk_writes_n(self, mongo_db):
        repo = UploadRepository(mongo_db)
        repo.ensure_indexes()
        n = repo.save_relationship_candidates(
            "ds-1",
            [self._sample(), self._sample("rel_a_b_x"), self._sample("rel_c_d_y")],
            metadata_version=1,
        )
        assert n == 3
        rows = repo.list_relationship_candidates("ds-1")
        assert len(rows) == 3

    def test_save_empty_returns_zero(self, mongo_db):
        repo = UploadRepository(mongo_db)
        assert repo.save_relationship_candidates("ds-1", [], 1) == 0

    def test_save_idempotent_via_relationship_id(self, mongo_db):
        repo = UploadRepository(mongo_db)
        repo.ensure_indexes()
        repo.save_relationship_candidates(
            "ds-1", [self._sample()], metadata_version=1,
        )
        # Re-running with same args must not produce duplicates — only update.
        repo.save_relationship_candidates(
            "ds-1", [self._sample()], metadata_version=1,
        )
        rows = repo.list_relationship_candidates("ds-1")
        assert len(rows) == 1

    def test_save_different_metadata_version_creates_new_row(self, mongo_db):
        # When metadata is re-profiled (new version), candidates are NOT
        # overwritten on the old version — both rows coexist for history.
        repo = UploadRepository(mongo_db)
        repo.ensure_indexes()
        repo.save_relationship_candidates(
            "ds-1", [self._sample()], metadata_version=1,
        )
        repo.save_relationship_candidates(
            "ds-1", [self._sample()], metadata_version=2,
        )
        all_rows = list(
            mongo_db["upload_relationship_candidates"]
            .find({"dataset_id": "ds-1"})
        )
        assert len(all_rows) == 2
        versions = {r["metadata_version"] for r in all_rows}
        assert versions == {1, 2}

    def test_save_requires_relationship_id(self, mongo_db):
        repo = UploadRepository(mongo_db)
        bad = self._sample()
        del bad["relationship_id"]
        with pytest.raises(ValueError, match="relationship_id"):
            repo.save_relationship_candidates(
                "ds-1", [bad], metadata_version=1,
            )

    def test_list_defaults_to_latest_version(self, mongo_db):
        repo = UploadRepository(mongo_db)
        repo.ensure_indexes()
        # v1 has 2 rels; v2 has 1 (smaller set after user editing)
        repo.save_relationship_candidates(
            "ds-1", [self._sample("rel_a"), self._sample("rel_b")],
            metadata_version=1,
        )
        repo.save_relationship_candidates(
            "ds-1", [self._sample("rel_a")], metadata_version=2,
        )
        # Default call returns v2 (latest) only.
        rows = repo.list_relationship_candidates("ds-1")
        assert len(rows) == 1
        assert all(r["metadata_version"] == 2 for r in rows)

    def test_list_filter_by_status(self, mongo_db):
        repo = UploadRepository(mongo_db)
        repo.ensure_indexes()
        r1 = self._sample("rel_a")
        r2 = {**self._sample("rel_b"), "status": "confirmed"}
        r3 = {**self._sample("rel_c"), "status": "rejected"}
        repo.save_relationship_candidates(
            "ds-1", [r1, r2, r3], metadata_version=1,
        )
        candidate_rows = repo.list_relationship_candidates(
            "ds-1", status="candidate",
        )
        assert len(candidate_rows) == 1
        confirmed_rows = repo.list_relationship_candidates(
            "ds-1", status="confirmed",
        )
        assert len(confirmed_rows) == 1
        assert confirmed_rows[0]["relationship_id"] == "rel_b"

    def test_list_sorted_by_confidence_desc(self, mongo_db):
        repo = UploadRepository(mongo_db)
        repo.ensure_indexes()
        rels = [
            {**self._sample("rel_low"), "confidence": 0.55},
            {**self._sample("rel_high"), "confidence": 0.95},
            {**self._sample("rel_mid"), "confidence": 0.75},
        ]
        repo.save_relationship_candidates("ds-1", rels, metadata_version=1)
        rows = repo.list_relationship_candidates("ds-1")
        confidences = [r["confidence"] for r in rows]
        assert confidences == sorted(confidences, reverse=True)

    def test_update_status_confirmed_writes_audit(self, mongo_db):
        repo = UploadRepository(mongo_db)
        repo.ensure_indexes()
        repo.save_relationship_candidates(
            "ds-1", [self._sample("rel_x")], metadata_version=1,
        )
        ok = repo.update_relationship_status(
            "ds-1", "rel_x", status="confirmed", user="alice",
        )
        assert ok is True
        row = repo.list_relationship_candidates("ds-1")[0]
        assert row["status"] == "confirmed"
        assert row["confirmed_by"] == "alice"
        assert "confirmed_at" in row

    def test_update_status_rejected_writes_audit(self, mongo_db):
        repo = UploadRepository(mongo_db)
        repo.ensure_indexes()
        repo.save_relationship_candidates(
            "ds-1", [self._sample("rel_x")], metadata_version=1,
        )
        repo.update_relationship_status(
            "ds-1", "rel_x", status="rejected", user="bob",
        )
        row = repo.list_relationship_candidates("ds-1")[0]
        assert row["status"] == "rejected"
        assert row["rejected_by"] == "bob"

    def test_update_join_key_and_type(self, mongo_db):
        # User edited a candidate: changed from_field + relationship_type.
        repo = UploadRepository(mongo_db)
        repo.ensure_indexes()
        repo.save_relationship_candidates(
            "ds-1", [self._sample("rel_x")], metadata_version=1,
        )
        ok = repo.update_relationship_status(
            "ds-1", "rel_x",
            status="edited",
            from_field="customer_uid",
            relationship_type="one_to_many",
            default_join_type="inner",
            user="carol",
        )
        assert ok is True
        row = repo.list_relationship_candidates("ds-1")[0]
        assert row["from_field"] == "customer_uid"
        assert row["relationship_type"] == "one_to_many"
        assert row["default_join_type"] == "inner"
        assert row["status"] == "edited"

    def test_update_missing_relationship_returns_false(self, mongo_db):
        repo = UploadRepository(mongo_db)
        assert repo.update_relationship_status(
            "ds-1", "rel_nonexistent", status="confirmed",
        ) is False

    def test_update_no_fields_returns_false(self, mongo_db):
        repo = UploadRepository(mongo_db)
        repo.save_relationship_candidates(
            "ds-1", [self._sample("rel_x")], metadata_version=1,
        )
        assert repo.update_relationship_status(
            "ds-1", "rel_x",
        ) is False

    def test_cascade_delete_with_dataset(self, mongo_db):
        repo = UploadRepository(mongo_db)
        repo.ensure_indexes()
        repo.create_dataset({
            "_id": "ds-cascade", "dataset_name": "x",
            "owner": "alice", "source_type": "file_upload",
            "file": {}, "status": "uploaded",
        })
        repo.save_relationship_candidates(
            "ds-cascade", [self._sample("rel_x")], metadata_version=1,
        )
        repo.delete_dataset("ds-cascade")
        assert repo.list_relationship_candidates("ds-cascade") == []


@pytest.mark.requires_mongo
class TestAnalysisStepsCRUD:
    """v0.18 M5 · spec §5.4 analysis_steps collection"""

    def _sample_step(self, step_id="step_001", step_no=1,
                      session_id="sess_001", action="extract_data"):
        return {
            "step_id": step_id,
            "session_id": session_id,
            "dataset_id": "ds-1",
            "metadata_version": 1,
            "step_no": step_no,
            "action_type": action,
            "user_query": "test",
            "input_tables": ["employee"],
            "output_table": "out_1",
            "row_count": 10,
        }

    def test_save_and_get_step(self, mongo_db):
        repo = UploadRepository(mongo_db)
        repo.ensure_indexes()
        sid = repo.save_analysis_step(self._sample_step())
        assert sid == "step_001"
        doc = repo.get_analysis_step("step_001")
        assert doc["session_id"] == "sess_001"
        assert doc["action_type"] == "extract_data"
        assert "created_at" in doc

    def test_save_requires_fields(self, mongo_db):
        repo = UploadRepository(mongo_db)
        bad = self._sample_step()
        del bad["step_id"]
        with pytest.raises(ValueError, match="step_id"):
            repo.save_analysis_step(bad)

    def test_list_steps_sorted_by_step_no(self, mongo_db):
        repo = UploadRepository(mongo_db)
        repo.ensure_indexes()
        for n in [3, 1, 2]:
            repo.save_analysis_step(self._sample_step(
                step_id=f"step_{n}", step_no=n,
            ))
        steps = repo.list_analysis_steps("sess_001")
        assert [s["step_no"] for s in steps] == [1, 2, 3]

    def test_duplicate_step_no_in_session_rejected(self, mongo_db):
        # Unique index on (session_id, step_no) — duplicate step_no
        # within the same session must fail.
        repo = UploadRepository(mongo_db)
        repo.ensure_indexes()
        repo.save_analysis_step(self._sample_step(
            step_id="step_a", step_no=1,
        ))
        with pytest.raises(Exception) as excinfo:
            repo.save_analysis_step(self._sample_step(
                step_id="step_b", step_no=1,
            ))
        msg = str(excinfo.value).lower()
        assert ("duplicate" in msg or "duplicatekey" in msg
                or "e11000" in msg)

    def test_list_filter_by_status(self, mongo_db):
        repo = UploadRepository(mongo_db)
        repo.ensure_indexes()
        repo.save_analysis_step(self._sample_step(
            step_id="step_ok", step_no=1,
        ))
        bad = self._sample_step(step_id="step_bad", step_no=2)
        bad["status"] = "failed"
        repo.save_analysis_step(bad)
        completed = repo.list_analysis_steps(
            "sess_001", status="completed",
        )
        assert len(completed) == 1
        assert completed[0]["step_id"] == "step_ok"

    def test_next_step_no(self, mongo_db):
        repo = UploadRepository(mongo_db)
        repo.ensure_indexes()
        # Empty session → 1
        assert repo.next_step_no("sess_001") == 1
        repo.save_analysis_step(self._sample_step(step_no=1))
        assert repo.next_step_no("sess_001") == 2
        repo.save_analysis_step(self._sample_step(
            step_id="step_2", step_no=2,
        ))
        assert repo.next_step_no("sess_001") == 3

    def test_step_isolated_by_session(self, mongo_db):
        # step_no=1 in session A and step_no=1 in session B must coexist.
        repo = UploadRepository(mongo_db)
        repo.ensure_indexes()
        repo.save_analysis_step(self._sample_step(
            step_id="a1", session_id="sess_A", step_no=1,
        ))
        repo.save_analysis_step(self._sample_step(
            step_id="b1", session_id="sess_B", step_no=1,
        ))
        assert len(repo.list_analysis_steps("sess_A")) == 1
        assert len(repo.list_analysis_steps("sess_B")) == 1

    def test_cascade_delete_with_dataset(self, mongo_db):
        repo = UploadRepository(mongo_db)
        repo.ensure_indexes()
        repo.create_dataset({
            "_id": "ds-cascade", "dataset_name": "x", "owner": "a",
            "source_type": "file_upload", "file": {}, "status": "uploaded",
        })
        repo.save_analysis_step({
            **self._sample_step(), "dataset_id": "ds-cascade",
        })
        repo.delete_dataset("ds-cascade")
        assert repo.list_analysis_steps("sess_001") == []


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
