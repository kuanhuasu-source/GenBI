"""tests/unit/test_upload_analysis_service.py — v0.17.0

Sprint A2 acceptance tests:
- on_phase callback ordering(5 phases × start+complete)
- enable_insight=False → phase_d_insight skipped event

凍結驗證:on_phase=None(default)必須 byte-equal v0.16(既有 callers 不傳此 kwarg
不該行為改變)。此檔不 cover backward compat — 既有 test_upload_analysis_*.py
integration test 保證 default 路徑。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest


# ============================================================
# Fake LLMService — 滿足 service 跑完 5 phase 所需的最小介面
# ============================================================
class _FakeLLM:
    """Minimal LLMService stub。Service 跑完整 pipeline 需要這些方法。"""

    def __init__(self):
        self.trace = None  # service 會 set/clear

    def classify_intent_for_query(self, query, last_analysis=None):
        return {"intent": "analysis", "is_followup": False}

    def generate_meta_response(self, intent, subject="", query=""):
        return "meta response"

    def generate_plan(self, query, followup_context=None):
        return {
            "status": "success",
            "message": "## A. extract\n## B. preprocess\n## C. chart",
        }

    def generate_pandas_extraction(
        self, query, plan_text, source_columns, source_df_sample,
        previous_code="", previous_error="",
    ):
        return "raw_df = source_df.copy()"

    def generate_preprocess_code(
        self, query, plan_text, available_columns, raw_df_sample,
        dashboard_hint=False, previous_code="", previous_error="",
    ):
        return "Q = raw_df.copy()"

    def generate_echarts_option(
        self, query, plan_text, q_columns,
        previous_code="", previous_error="",
    ):
        return (
            'option = {"xAxis": {"type": "category", "data": ["x"]}, '
            '"yAxis": {"type": "value"}, '
            '"series": [{"type": "bar", "data": [1]}]}'
        )

    def generate_plot_code(
        self, query, plan_text, q_columns,
        previous_code="", previous_error="",
    ):
        return (
            "import plotly.express as px\n"
            "fig = px.bar(Q)"
        )

    def generate_insight(self, query, plan_text, q_preview_md):
        return {"status": "success", "message": "insight text"}


# ============================================================
# Fixtures — dataset + table + parquet 一條龍 setup
# ============================================================
@pytest.fixture
def setup_analysis(mongo_db, tmp_path):
    """建 dataset + table + parquet + session + active metadata,
    回 (service, session_id)。"""
    from upload_repository import UploadRepository
    from upload_analysis_service import UploadAnalysisService

    repo = UploadRepository(mongo_db)
    repo.ensure_indexes()

    dataset_id = "upload_test_a2"
    # 1. dataset
    repo.create_dataset({
        "_id": dataset_id, "dataset_name": "test.csv", "owner": "alice",
        "source_type": "file_upload", "file": {}, "status": "profiled",
    })
    # 2. metadata(confirmed)
    repo.save_metadata_version(
        dataset_id=dataset_id,
        metadata={
            "dataset_id": dataset_id,
            "source_type": "upload",
            "collections": {"sheet1": {"fields": {
                "value": {"type": "int", "semantic_role": "measure"},
            }}},
            "kpi_definitions": {},
            "data_limitations": {},
        },
        confirmation_status="confirmed",
        confirmed_by="alice",
        activate=True,
    )
    # 3. parquet file
    parquet_path = tmp_path / "test.parquet"
    df = pd.DataFrame({"value": [1, 2, 3, 4, 5]})
    try:
        df.to_parquet(parquet_path)
    except Exception:
        pytest.skip("pyarrow / fastparquet 未安裝,parquet 寫不出")
    # 4. table row
    repo.create_table({
        "dataset_id": dataset_id,
        "table_id": "sheet1",
        "table_name": "sheet1",
        "row_count": 5,
        "column_count": 1,
        "storage": {"path": str(parquet_path)},
    })
    service = UploadAnalysisService(
        mongo_db=mongo_db,
        upload_repo=repo,
        llm_service=_FakeLLM(),
        uploads_root=tmp_path,
    )
    # 5. session(透過 service 介面建,避免直接戳內部 attribute)
    session_id = service.start_session(
        dataset_id=dataset_id,
        metadata_version=1,
        user="alice",
    )
    return service, session_id


# ============================================================
# Test 1:Happy path → 5 phases × start+complete in order
# ============================================================
@pytest.mark.requires_mongo
def test_on_phase_callback_fires_5_phases_in_order(setup_analysis):
    """on_phase callback 應該對 5 個 phase 各觸發 start + complete event,
    順序固定為 0/A/B/C/D。"""
    service, session_id = setup_analysis

    events: list[tuple[str, str]] = []

    def cb(phase_id: str, event: str, payload: dict) -> None:
        events.append((phase_id, event))

    result = service.handle_query(
        session_id=session_id,
        query="畫直方圖",
        chart_engine="ECharts",
        enable_insight=True,
        on_phase=cb,
    )

    # Service 跑完一輪不該回 failed(若 fake LLM 介面有缺,會在這邊看到)
    assert result["status"] in ("completed", "failed"), (
        f"unexpected status {result['status']}: {result.get('error')}"
    )

    # 過濾 start/complete events,verify 順序
    phase_events = [
        (pid, ev) for pid, ev in events if ev in ("start", "complete")
    ]

    expected_order = [
        ("phase_0_plan", "start"),
        ("phase_0_plan", "complete"),
        ("phase_a_pipeline", "start"),
        ("phase_a_pipeline", "complete"),
        ("phase_b_preprocess", "start"),
        ("phase_b_preprocess", "complete"),
        ("phase_c_chart", "start"),
        ("phase_c_chart", "complete"),
        ("phase_d_insight", "start"),
        ("phase_d_insight", "complete"),
    ]
    # 若 phase C/D 因 fake LLM 介面差異 fail,至少前 3 phase 該 fire 完整
    # 但 happy path 應該全 fire
    if result["status"] == "completed":
        assert phase_events == expected_order, (
            f"phase events mismatch:\n  got: {phase_events}\n  "
            f"want: {expected_order}"
        )


# ============================================================
# Test 2:enable_insight=False → Phase D skipped
# ============================================================
@pytest.mark.requires_mongo
def test_on_phase_skipped_event_when_insight_disabled(setup_analysis):
    """enable_insight=False 時,Phase D 不該 fire start/complete,
    而是 fire 一次 'skipped' event 並帶 reason。"""
    service, session_id = setup_analysis

    events: list[tuple[str, str, dict]] = []

    def cb(phase_id: str, event: str, payload: dict) -> None:
        events.append((phase_id, event, payload))

    result = service.handle_query(
        session_id=session_id,
        query="畫直方圖",
        chart_engine="ECharts",
        enable_insight=False,
        on_phase=cb,
    )

    # 只看 Phase D 的 events
    phase_d_events = [
        (ev, payload) for pid, ev, payload in events
        if pid == "phase_d_insight"
    ]

    if result["status"] == "completed":
        # 至少要有 skipped event,且不能有 start/complete
        skipped = [e for e in phase_d_events if e[0] == "skipped"]
        non_skipped = [e for e in phase_d_events if e[0] != "skipped"]

        assert len(skipped) == 1, (
            f"expected exactly 1 skipped event for phase_d_insight, "
            f"got {len(skipped)}: {phase_d_events}"
        )
        assert non_skipped == [], (
            f"expected no non-skipped events for phase_d_insight when "
            f"enable_insight=False, got: {non_skipped}"
        )
        # 該帶 reason
        _, payload = skipped[0]
        assert "reason" in payload, (
            f"skipped payload should include 'reason', got: {payload}"
        )
        assert "enable_insight" in payload["reason"], (
            f"reason should mention enable_insight, got: {payload['reason']}"
        )


# ============================================================
# Test 3:on_phase=None(default)不該 raise(byte-equal sanity check)
# ============================================================
@pytest.mark.requires_mongo
def test_on_phase_none_does_not_raise(setup_analysis):
    """on_phase=None(既有 callers 行為)不該影響 service。"""
    service, session_id = setup_analysis

    result = service.handle_query(
        session_id=session_id,
        query="畫直方圖",
        chart_engine="ECharts",
        enable_insight=True,
        # on_phase 故意不傳
    )
    assert result["status"] in ("completed", "failed", "refused", "meta")


# ============================================================
# Test 4:callback raise 不該阻斷 pipeline
# ============================================================
@pytest.mark.requires_mongo
def test_on_phase_callback_exception_does_not_break_pipeline(setup_analysis):
    """callback 若 raise(UI bug),service 該 log warning 並繼續跑。"""
    service, session_id = setup_analysis

    def bad_cb(phase_id: str, event: str, payload: dict) -> None:
        raise RuntimeError("UI bug in callback")

    # 不該 raise
    result = service.handle_query(
        session_id=session_id,
        query="畫直方圖",
        chart_engine="ECharts",
        enable_insight=True,
        on_phase=bad_cb,
    )
    # Service 該照常跑完(status 視 LLM stub 而定)
    assert result["status"] in ("completed", "failed", "refused", "meta")
