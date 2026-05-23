"""tests/unit/test_upload_metadata_generator.py — unit tests for upload_metadata_generator.py (M4a)."""

from __future__ import annotations

import pandas as pd
import pytest

from data_profiler import profile_table
from semantic_profiler import profile_columns_semantic
from upload_metadata_generator import (
    build_metadata,
    infer_data_limitations,
    infer_grain,
    suggest_charting_guidance,
    suggest_kpis,
    suggest_sample_questions,
    summarize_confidence,
)


# ============================================================
# Helper:從 CSV 一次跑完 physical + semantic
# ============================================================
def _run_profile(df: pd.DataFrame) -> tuple[list[dict], list[dict]]:
    col_profs = profile_table(df, "sheet1")["columns"]
    sem_results = profile_columns_semantic(col_profs, use_llm=False)
    return col_profs, sem_results


def _enriched_results(col_profs, sem_results):
    """build_metadata 內 enrich 那段:把 name 注入 semantic results。"""
    out = []
    for p, s in zip(col_profs, sem_results):
        merged = dict(s)
        merged["name"] = p["name"]
        merged["warnings"] = p.get("warnings", [])
        out.append(merged)
    return out


# ============================================================
# infer_grain
# ============================================================
class TestInferGrain:
    def test_clean_dataset_with_pk(self, golden_data_dir):
        df = pd.read_csv(golden_data_dir / "projects_clean.csv")
        col_profs, sem_results = _run_profile(df)
        enriched = _enriched_results(col_profs, sem_results)
        grain = infer_grain(enriched, col_profs, row_count=15)
        # project_id 是 strict PK(15 unique = 15 rows)
        assert grain["primary_key"] == "project_id"
        assert grain["confidence"] >= 0.9
        assert "project_id" in grain["grain_text"]

    def test_no_identifier(self):
        # 沒 identifier 欄位 → 該 fallback
        df = pd.DataFrame({"category": ["A", "B", "C"] * 5})
        col_profs, sem_results = _run_profile(df)
        enriched = _enriched_results(col_profs, sem_results)
        grain = infer_grain(enriched, col_profs, row_count=15)
        # 可能有也可能沒有,但不該 crash
        assert "grain_text" in grain


# ============================================================
# infer_data_limitations
# ============================================================
class TestInferDataLimitations:
    def test_no_date_column(self, golden_data_dir):
        df = pd.read_csv(golden_data_dir / "projects_no_date.csv")
        col_profs, sem_results = _run_profile(df)
        enriched = _enriched_results(col_profs, sem_results)
        lim = infer_data_limitations(enriched)
        assert any("date" in m.lower() for m in lim["missing_dimensions"])
        assert any("trend" in n.lower() or "趨勢" in n
                    for n in lim["not_supported_analysis"])

    def test_with_date_no_limitation(self, golden_data_dir):
        df = pd.read_csv(golden_data_dir / "projects_with_date.csv",
                          parse_dates=["start_date", "end_date"])
        col_profs, sem_results = _run_profile(df)
        enriched = _enriched_results(col_profs, sem_results)
        lim = infer_data_limitations(enriched)
        # 有 date col 不該標 "no date column"
        assert not any("no confirmed date" in m.lower()
                        for m in lim["missing_dimensions"])

    def test_no_amount_flagged(self, golden_data_dir):
        df = pd.read_csv(golden_data_dir / "projects_no_date.csv")
        col_profs, sem_results = _run_profile(df)
        enriched = _enriched_results(col_profs, sem_results)
        lim = infer_data_limitations(enriched)
        # 沒 amount col → 至少不該炸,可能標也可能不標
        assert isinstance(lim, dict)


# ============================================================
# suggest_kpis
# ============================================================
class TestSuggestKpis:
    def test_count_kpi(self):
        results = [{
            "name": "order_count",
            "role": "measure_count",
            "unit": "count",
        }]
        kpis = suggest_kpis(results)
        assert "total_order_count" in kpis
        assert "sum" in kpis["total_order_count"]["formula"]

    def test_amount_kpi(self):
        results = [{"name": "amount", "role": "measure_amount", "unit": ""}]
        kpis = suggest_kpis(results)
        assert "total_amount" in kpis
        assert "avg_amount" in kpis

    def test_duration_kpi(self):
        results = [{"name": "leadtime", "role": "measure_duration", "unit": "days"}]
        kpis = suggest_kpis(results)
        assert "avg_leadtime" in kpis
        assert "p95_leadtime" in kpis
        assert "median_leadtime" in kpis
        # P95 important_note 該有 unit
        assert "days" in kpis["p95_leadtime"]["important_note"]

    def test_percentage_kpi(self):
        results = [{"name": "rate", "role": "measure_percentage", "unit": "ratio"}]
        kpis = suggest_kpis(results)
        # 比率類:只有 avg,不能 sum
        assert "avg_rate" in kpis
        assert "total_rate" not in kpis


# ============================================================
# build_metadata 端到端
# ============================================================
class TestBuildMetadata:
    def test_e2e_clean_projects(self, golden_data_dir):
        df = pd.read_csv(golden_data_dir / "projects_clean.csv")
        col_profs, sem_results = _run_profile(df)
        md = build_metadata(
            dataset_doc={"_id": "upload_test_001", "dataset_name": "projects_clean.csv"},
            table_doc={
                "dataset_id": "upload_test_001", "table_id": "sheet1",
                "row_count": 15, "column_count": 6,
            },
            column_profiles=col_profs,
            semantic_results=sem_results,
        )
        # source_type 旗標(M3 之後 LLMService 切 Phase 0 prompt 用)
        assert md["source_type"] == "upload"
        assert md["dataset_id"] == "upload_test_001"
        # 結構完整
        assert "sheet1" in md["collections"]
        coll = md["collections"]["sheet1"]
        assert coll["primary_key"] == "project_id"
        # fields 全在
        assert "project_id" in coll["fields"]
        assert "leadtime" in coll["fields"]
        # identifier 必含 is_identifier=True
        assert coll["fields"]["project_id"]["is_identifier"] is True
        # measure 必含 is_measure=True
        # (leadtime 可能被推為 measure_duration 或 measure_count)
        assert coll["fields"]["leadtime"]["is_measure"] is True
        # KPI 建議至少有東西
        assert len(md["kpi_definitions"]) > 0
        # data_limitations 結構在
        assert "missing_dimensions" in md["data_limitations"]
        assert "not_supported_analysis" in md["data_limitations"]

    def test_e2e_sales_amount(self, golden_data_dir):
        df = pd.read_csv(golden_data_dir / "sales_amount.csv",
                          parse_dates=["sale_date"])
        col_profs, sem_results = _run_profile(df)
        md = build_metadata(
            dataset_doc={"_id": "upload_test_sales", "dataset_name": "sales_amount.csv"},
            table_doc={
                "dataset_id": "upload_test_sales", "table_id": "sheet1",
                "row_count": 15, "column_count": 5,
            },
            column_profiles=col_profs,
            semantic_results=sem_results,
        )
        # amount 該被 KPI 建議成 total_amount / avg_amount
        assert "total_amount" in md["kpi_definitions"]
        assert "avg_amount" in md["kpi_definitions"]


# ============================================================
# summarize_confidence
# ============================================================
class TestSummarizeConfidence:
    def test_high_medium_low_buckets(self):
        md = {"collections": {
            "t": {"fields": {
                "a": {"confidence": 0.95},
                "b": {"confidence": 0.86},
                "c": {"confidence": 0.70},
                "d": {"confidence": 0.55},
            }}
        }}
        s = summarize_confidence(md)
        assert s["high_confidence_fields"] == 2   # 0.95, 0.86
        assert s["medium_confidence_fields"] == 1  # 0.70
        assert s["low_confidence_fields"] == 1     # 0.55


# ============================================================
# Charting guidance
# ============================================================
class TestSuggestCharting:
    def test_dim_measure_combo(self):
        results = [
            {"name": "cat", "role": "dimension"},
            {"name": "amt", "role": "measure_amount"},
        ]
        cg = suggest_charting_guidance(results)
        assert "recommended_charts" in cg
        # 該至少有 ranking_bar
        assert "ranking_bar" in cg["recommended_charts"]


# ============================================================
# Sample questions
# ============================================================
class TestSampleQuestions:
    def test_questions_generated(self):
        results = [
            {"name": "cat", "role": "dimension"},
            {"name": "amt", "role": "measure_amount"},
        ]
        kpis = {"total_amt": {"name": "Total amt"}}
        qs = suggest_sample_questions(results, kpis)
        assert len(qs) > 0
        assert all(isinstance(q, str) for q in qs)
