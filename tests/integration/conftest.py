"""tests/integration/conftest.py — fixtures for end-to-end integration tests (M4b)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pandas as pd
import pytest


# ============================================================
# Fake LLMService — 不打真 LLM,根據 phase 回固定 response
# ============================================================
class FakeLLMService:
    """Mock LLMService 給 integration test 用。

    Caller 可在 build time 透過 `responses` 注入每 phase 的 fake output。
    若沒注入,給合理的 default(讓 e2e flow 跑得通,不關心內容對錯)。
    """

    def __init__(self, responses: dict[str, Any] | None = None,
                 task_metadata: dict | None = None):
        self.task_metadata = task_metadata or {}
        self.domain = "fake"
        self.model_profile = {}
        self.disable_thinking = False
        self.trace = None
        self.call_log: list[dict] = []
        self.responses = responses or {}

    # ── Intent router(沿用真實 logic)──
    def classify_intent_for_query(self, query: str, last_analysis=None):
        # 簡化:都 return analysis,test 可以 override 透過 responses
        if "classify_intent" in self.responses:
            return self.responses["classify_intent"]
        return {"intent": "analysis", "subject": "", "is_followup": False}

    def generate_meta_response(self, intent: str, subject: str = "",
                                  query: str = "") -> str:
        return self.responses.get("meta_response",
                                    f"Mock meta response for {intent}")

    # ── 5 phase ──
    def generate_plan(self, query: str, followup_context=None):
        plan = self.responses.get(
            "plan",
            "## A. 資料獲取\n從 source_df filter\n\n"
            "## B. 資料處理\n計算 Q\n\n"
            "## C. 視覺化\nBar chart",
        )
        return {"status": "success", "message": plan}

    def generate_pandas_extraction(self, query, plan_text="",
                                     source_columns=None, source_df_sample="",
                                     previous_code="", previous_error=""):
        return self.responses.get(
            "pandas_extraction",
            "raw_df = source_df.copy()",
        )

    def generate_preprocess_code(self, query, plan_text="",
                                   available_columns=None, raw_df_sample="",
                                   dashboard_hint=False,
                                   previous_code="", previous_error=""):
        return self.responses.get(
            "preprocess_code",
            "Q = raw_df.copy()",
        )

    def generate_echarts_option(self, query, plan_text="", q_columns=None,
                                  previous_code="", previous_error=""):
        return self.responses.get(
            "echarts_option",
            'option = {"title": {"text": "Mock"}, '
            '"xAxis": {"type": "category", "data": ["a", "b"]}, '
            '"yAxis": {"type": "value"}, '
            '"series": [{"type": "bar", "data": [1, 2]}]}',
        )

    def generate_plot_code(self, query, plan_text="", q_columns=None,
                            previous_code="", previous_error=""):
        return self.responses.get(
            "plot_code",
            "import plotly.graph_objects as go\n"
            "fig = go.Figure(data=[go.Bar(x=['a','b'], y=[1,2])])",
        )

    def generate_insight(self, query, plan_text="", q_preview_md=""):
        return {
            "status": "success",
            "message": self.responses.get("insight", "Mock insight text"),
        }


# ============================================================
# Common fixtures
# ============================================================
@pytest.fixture
def fake_llm():
    return FakeLLMService()


@pytest.fixture
def fake_llm_factory():
    """每個 test 可以 build 自己 customized response 的 fake LLM。"""
    def _factory(**responses):
        return FakeLLMService(responses=responses)
    return _factory


@pytest.fixture
def project_root():
    return Path(__file__).resolve().parent.parent.parent


@pytest.fixture
def golden_data_dir(project_root):
    return project_root / "tests" / "golden_data"
