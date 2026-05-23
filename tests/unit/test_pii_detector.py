"""tests/unit/test_pii_detector.py — unit tests for pii_detector.py (M4b)."""

from __future__ import annotations

import pandas as pd
import pytest

from pii_detector import detect_pii_in_column, summarize_pii_in_dataset


# ============================================================
# Email
# ============================================================
class TestEmail:
    def test_pattern_hit(self):
        result = detect_pii_in_column(
            column_name="contact",
            sample_values=["alice@example.com", "bob@test.org", "carol@x.co"],
            physical_type="string",
        )
        assert result["is_pii"] is True
        assert result["pii_type"] == "email"
        assert result["confidence"] >= 0.7

    def test_name_hit_without_pattern(self):
        # 欄名含 email 但 sample 還沒拿到 — 仍判 PII(低 confidence)
        result = detect_pii_in_column(
            column_name="email_address",
            sample_values=["pending"],
            physical_type="string",
        )
        assert result["is_pii"] is True
        assert result["pii_type"] == "email"

    def test_no_email(self):
        result = detect_pii_in_column(
            column_name="category",
            sample_values=["A", "B", "C"],
            physical_type="string",
        )
        assert result["is_pii"] is False


# ============================================================
# Phone
# ============================================================
class TestPhone:
    def test_tw_mobile(self):
        result = detect_pii_in_column(
            column_name="phone",
            sample_values=["0912-345-678", "0987-654-321", "0922-111-222"],
            physical_type="string",
        )
        assert result["is_pii"] is True
        assert result["pii_type"] == "phone"

    def test_international(self):
        result = detect_pii_in_column(
            column_name="contact_phone",
            sample_values=["+1-555-123-4567", "+886 912 345 678"],
            physical_type="string",
        )
        assert result["is_pii"] is True
        assert result["pii_type"] == "phone"

    def test_date_not_misjudged_as_phone(self):
        # 日期格式不該被當電話(7+ digits 規則 + 沒 phone name hint)
        result = detect_pii_in_column(
            column_name="order_date",
            sample_values=["2025-01-15", "2025-02-20", "2025-03-25"],
            physical_type="string",
        )
        assert result["is_pii"] is False


# ============================================================
# National ID
# ============================================================
class TestNationalID:
    def test_tw_id_format(self):
        result = detect_pii_in_column(
            column_name="member_no",
            sample_values=["A123456789", "B234567890", "C345678901"],
            physical_type="string",
        )
        assert result["is_pii"] is True
        assert result["pii_type"] == "national_id"

    def test_cn_id_format(self):
        # 18 chars total(17 digit + 1 check digit/letter)
        result = detect_pii_in_column(
            column_name="id",
            sample_values=["11010519900101123X", "320102198812091234"],
            physical_type="string",
        )
        assert result["is_pii"] is True
        assert result["pii_type"] == "national_id"

    def test_name_hint(self):
        result = detect_pii_in_column(
            column_name="national_id",
            sample_values=["xxxx"],
            physical_type="string",
        )
        assert result["is_pii"] is True
        assert result["pii_type"] == "national_id"


# ============================================================
# Employee ID
# ============================================================
class TestEmployeeID:
    def test_name_hit(self):
        result = detect_pii_in_column(
            column_name="employee_id",
            sample_values=["E001", "E002", "E003"],
            physical_type="string",
        )
        assert result["is_pii"] is True
        assert result["pii_type"] == "employee_id"

    def test_chinese_name(self):
        result = detect_pii_in_column(
            column_name="員工編號",
            sample_values=["A001", "A002"],
            physical_type="string",
        )
        assert result["is_pii"] is True


# ============================================================
# Name-like
# ============================================================
class TestNameLike:
    def test_full_name(self):
        result = detect_pii_in_column(
            column_name="full_name",
            sample_values=["Alice Chen", "Bob Wang"],
            physical_type="string",
        )
        assert result["is_pii"] is True
        assert result["pii_type"] == "name_like"

    def test_chinese_name(self):
        result = detect_pii_in_column(
            column_name="姓名",
            sample_values=["陳小明", "王大華"],
            physical_type="string",
        )
        assert result["is_pii"] is True


# ============================================================
# Address
# ============================================================
class TestAddress:
    def test_address_keyword(self):
        result = detect_pii_in_column(
            column_name="mailing_address",
            sample_values=["123 Main St"],
            physical_type="string",
        )
        assert result["is_pii"] is True
        assert result["pii_type"] == "address"

    def test_chinese_address(self):
        result = detect_pii_in_column(
            column_name="住址",
            sample_values=["xxx"],
            physical_type="string",
        )
        assert result["is_pii"] is True


# ============================================================
# Negative cases
# ============================================================
class TestNonPII:
    @pytest.mark.parametrize("name,samples", [
        ("category", ["Apparel", "Electronics", "Books"]),
        ("revenue", [100, 200, 300]),
        ("status", ["Completed", "InProgress"]),
        ("region", ["TW", "US", "JP"]),
        ("product_id", ["PROD-001", "PROD-002"]),   # id 但不是 employee_id
    ])
    def test_not_pii(self, name, samples):
        result = detect_pii_in_column(
            column_name=name,
            sample_values=samples,
            physical_type="string",
        )
        assert result["is_pii"] is False, \
            f"{name} 被誤判為 PII: {result}"


# ============================================================
# Dataset summary
# ============================================================
class TestSummarize:
    def test_aggregate(self):
        col_profiles = [
            {"name": "email", "pii_info": {
                "is_pii": True, "pii_type": "email",
                "confidence": 0.95, "reason": "x"}},
            {"name": "phone", "pii_info": {
                "is_pii": True, "pii_type": "phone",
                "confidence": 0.85, "reason": "y"}},
            {"name": "category", "pii_info": {
                "is_pii": False, "pii_type": None,
                "confidence": 0.0, "reason": ""}},
        ]
        s = summarize_pii_in_dataset(col_profiles)
        assert s["has_pii"] is True
        assert len(s["pii_columns"]) == 2
        assert s["pii_count_by_type"] == {"email": 1, "phone": 1}

    def test_no_pii(self):
        col_profiles = [
            {"name": "x", "pii_info": {
                "is_pii": False, "pii_type": None,
                "confidence": 0.0, "reason": ""}},
        ]
        s = summarize_pii_in_dataset(col_profiles)
        assert s["has_pii"] is False
        assert s["pii_columns"] == []


# ============================================================
# Integration with data_profiler — profile_column 應該注入 pii_info
# ============================================================
class TestDataProfilerIntegration:
    def test_profile_column_includes_pii_info(self):
        from data_profiler import profile_column

        s = pd.Series(["alice@x.com", "bob@y.com", "carol@z.com"])
        prof = profile_column(s, "email")
        assert "pii_info" in prof
        assert prof["pii_info"]["is_pii"] is True
        assert prof["pii_info"]["pii_type"] == "email"

    def test_non_pii_column(self):
        from data_profiler import profile_column

        s = pd.Series([1, 2, 3, 4, 5])
        prof = profile_column(s, "score")
        assert "pii_info" in prof
        assert prof["pii_info"]["is_pii"] is False

    def test_golden_employee_pii_csv(self, golden_data_dir):
        """spec golden dataset employee_pii.csv 應該偵測出 3 個 PII 欄"""
        from data_profiler import profile_table

        df = pd.read_csv(golden_data_dir / "employee_pii.csv")
        prof = profile_table(df, "sheet1")
        # 偵測 PII columns
        pii_cols = [c["name"] for c in prof["columns"]
                     if c.get("pii_info", {}).get("is_pii")]
        # employee_id / full_name / email / phone 都該命中
        assert "employee_id" in pii_cols
        assert "full_name" in pii_cols
        assert "email" in pii_cols
        assert "phone" in pii_cols
        # department / salary 不該被誤判
        assert "department" not in pii_cols
        assert "salary" not in pii_cols


# ============================================================
# Integration with upload_metadata_generator — PII 應蓋掉 semantic_role
# ============================================================
class TestMetadataGeneratorIntegration:
    def test_pii_role_override(self, golden_data_dir):
        from data_profiler import profile_table
        from semantic_profiler import profile_columns_semantic
        from upload_metadata_generator import build_metadata

        df = pd.read_csv(golden_data_dir / "employee_pii.csv")
        col_profs = profile_table(df, "sheet1")["columns"]
        sem_results = profile_columns_semantic(col_profs, use_llm=False)
        md = build_metadata(
            dataset_doc={"_id": "test", "dataset_name": "emp.csv"},
            table_doc={"dataset_id": "test", "table_id": "sheet1",
                       "row_count": 8, "column_count": 6},
            column_profiles=col_profs,
            semantic_results=sem_results,
        )
        fields = md["collections"]["sheet1"]["fields"]
        # email / phone / full_name 都該 mark semantic_role='pii'
        assert fields["email"]["semantic_role"] == "pii"
        assert fields["phone"]["semantic_role"] == "pii"
        assert fields["full_name"]["semantic_role"] == "pii"
        # salary / department 不該 mark
        assert fields["salary"]["semantic_role"] != "pii"
        assert fields["department"]["semantic_role"] != "pii"
