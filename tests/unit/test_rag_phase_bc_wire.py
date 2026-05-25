"""tests/unit/test_rag_phase_bc_wire.py — M6.3 Sprint 3 Day 16-17.

Phase B/C RAG wire freeze-clause + slot injection tests.
對齊 spec §10.2:RAG off → byte-equal v0.15。RAG on → slots 注入 prompt。
"""

from __future__ import annotations

import pytest

from embedded_prompts import (
    compose_phase_b_prompt_modular,
    compose_phase_c_prompt_modular,
)


_FAKE_COLS = "[FAKE_COLS_INFO]"
_FAKE_DK = "[FAKE_DOMAIN_KNOWLEDGE]"


# ============================================================
# Phase B freeze-clause
# ============================================================
class TestPhaseBFreezeClause:
    def test_no_rag_kwargs_no_rag_headers(self):
        out = compose_phase_b_prompt_modular(
            intent="simple_groupby",
            cols_info=_FAKE_COLS,
            domain_knowledge=_FAKE_DK,
        )
        assert "動態 context · 注意這些雷 (RAG)" not in out
        assert "動態 context · 過往成功案例 (RAG)" not in out
        assert _FAKE_DK in out

    def test_empty_string_rag_kwargs_no_rag_headers(self):
        """明示 rag_*='' 該等同未傳。"""
        out_omit = compose_phase_b_prompt_modular(
            intent="simple_groupby",
            cols_info=_FAKE_COLS,
            domain_knowledge=_FAKE_DK,
        )
        out_empty = compose_phase_b_prompt_modular(
            intent="simple_groupby",
            cols_info=_FAKE_COLS,
            domain_knowledge=_FAKE_DK,
            rag_anti_pattern="",
            rag_few_shot="",
        )
        assert out_omit == out_empty

    def test_dk_to_static_section_byte_precise(self):
        """domain_knowledge 後該直接接「實作守則」,RAG off 時無多餘字。"""
        out = compose_phase_b_prompt_modular(
            intent="simple_groupby",
            cols_info=_FAKE_COLS,
            domain_knowledge=_FAKE_DK,
        )
        dk_idx = out.find(_FAKE_DK)
        next_marker = "### 實作守則 (CRITICAL RULES — universal):"
        next_idx = out.find(next_marker)
        assert dk_idx >= 0 and next_idx > dk_idx
        between = out[dk_idx + len(_FAKE_DK):next_idx]
        assert "(RAG)" not in between

    def test_no_jinja_artifacts(self):
        out = compose_phase_b_prompt_modular(
            intent="simple_groupby",
            cols_info=_FAKE_COLS,
            domain_knowledge=_FAKE_DK,
        )
        assert "{%" not in out
        assert "{{" not in out
        assert "endif" not in out


# ============================================================
# Phase B slot injection
# ============================================================
class TestPhaseBSlotInjection:
    def test_anti_pattern_slot(self):
        out = compose_phase_b_prompt_modular(
            intent="simple_groupby",
            cols_info=_FAKE_COLS,
            domain_knowledge=_FAKE_DK,
            rag_anti_pattern="❌ Q 不該包含 TOTAL row",
        )
        assert "注意這些雷 (RAG)" in out
        assert "TOTAL row" in out

    def test_few_shot_slot(self):
        out = compose_phase_b_prompt_modular(
            intent="simple_groupby",
            cols_info=_FAKE_COLS,
            domain_knowledge=_FAKE_DK,
            rag_few_shot="Query: 各公司申請數\nPipeline: ...",
        )
        assert "過往成功案例 (RAG)" in out
        assert "各公司申請數" in out

    def test_both_slots_in_order(self):
        out = compose_phase_b_prompt_modular(
            intent="simple_groupby",
            cols_info=_FAKE_COLS,
            domain_knowledge=_FAKE_DK,
            rag_anti_pattern="[ANTI]",
            rag_few_shot="[FEWSHOT]",
        )
        anti_idx = out.find("[ANTI]")
        few_idx = out.find("[FEWSHOT]")
        assert anti_idx > 0
        assert few_idx > anti_idx, "anti_pattern 該在 few_shot 前(spec §10.1)"

    def test_partial_only_anti_pattern(self):
        out = compose_phase_b_prompt_modular(
            intent="simple_groupby",
            cols_info=_FAKE_COLS,
            domain_knowledge=_FAKE_DK,
            rag_anti_pattern="[ONLY_ANTI]",
            rag_few_shot="",
        )
        assert "[ONLY_ANTI]" in out
        assert "注意這些雷 (RAG)" in out
        assert "過往成功案例 (RAG)" not in out


# ============================================================
# Phase C freeze-clause
# ============================================================
class TestPhaseCFreezeClause:
    def test_no_rag_kwargs_no_rag_headers(self):
        out = compose_phase_c_prompt_modular(
            intent="bar_basic",
            cols_info=_FAKE_COLS,
        )
        assert "動態 context · 推薦圖表 recipe (RAG)" not in out
        assert "動態 context · 注意這些雷 (RAG)" not in out

    def test_empty_string_rag_kwargs_equiv(self):
        out_omit = compose_phase_c_prompt_modular(
            intent="bar_basic", cols_info=_FAKE_COLS,
        )
        out_empty = compose_phase_c_prompt_modular(
            intent="bar_basic", cols_info=_FAKE_COLS,
            rag_chart_recipe="", rag_anti_pattern="",
        )
        assert out_omit == out_empty

    def test_cols_to_static_section_byte_precise(self):
        """cols_info 後該直接接「### 任務說明」,RAG off 時無多餘字。"""
        out = compose_phase_c_prompt_modular(
            intent="bar_basic", cols_info=_FAKE_COLS,
        )
        cols_idx = out.find(_FAKE_COLS)
        next_marker = "### 任務說明"
        next_idx = out.find(next_marker)
        assert cols_idx >= 0 and next_idx > cols_idx
        between = out[cols_idx + len(_FAKE_COLS):next_idx]
        assert "(RAG)" not in between

    def test_no_jinja_artifacts(self):
        out = compose_phase_c_prompt_modular(
            intent="bar_basic", cols_info=_FAKE_COLS,
        )
        assert "{%" not in out
        assert "{{" not in out
        assert "endif" not in out


# ============================================================
# Phase C slot injection
# ============================================================
class TestPhaseCSlotInjection:
    def test_chart_recipe_slot(self):
        out = compose_phase_c_prompt_modular(
            intent="bar_basic", cols_info=_FAKE_COLS,
            rag_chart_recipe="company_total: chart_type=bar, x=company_code, y=count",
        )
        assert "推薦圖表 recipe (RAG)" in out
        assert "company_total" in out
        assert "chart_type=bar" in out

    def test_anti_pattern_slot(self):
        out = compose_phase_c_prompt_modular(
            intent="bar_basic", cols_info=_FAKE_COLS,
            rag_anti_pattern="❌ 不要用 3D pie",
        )
        assert "注意這些雷 (RAG)" in out
        assert "3D pie" in out

    def test_both_slots_in_order(self):
        """spec §9.2:Phase C 抽 chart_recipe + anti_pattern,順序這樣。"""
        out = compose_phase_c_prompt_modular(
            intent="bar_basic", cols_info=_FAKE_COLS,
            rag_chart_recipe="[RECIPE]",
            rag_anti_pattern="[ANTI]",
        )
        recipe_idx = out.find("[RECIPE]")
        anti_idx = out.find("[ANTI]")
        assert recipe_idx > 0
        assert anti_idx > recipe_idx, "chart_recipe 該在 anti_pattern 前"


# ============================================================
# LLMService Phase B/C 路徑 _retrieve_rag_slots integration
# ============================================================
class TestLLMServiceBCWiring:
    """直接驗 LLMService._retrieve_rag_slots 在 Phase B/C 的 extra_filters 行為。"""

    def _build_llm(self, **kw):
        from unittest.mock import MagicMock
        from llm_service import LLMService
        llm = LLMService.__new__(LLMService)
        llm.retrieval_orchestrator = kw.get("orchestrator")
        llm.rag_enabled = kw.get("rag_enabled", False) and \
            llm.retrieval_orchestrator is not None
        # Phase B/C tests default to enabled; gating tests override to False
        llm.rag_phase_bc_enabled = kw.get("rag_phase_bc_enabled", True)
        llm._last_query = kw.get("last_query", "")
        llm.domain = kw.get("domain", "tflex")
        return llm

    def test_phase_b_no_extra_filter(self):
        from unittest.mock import MagicMock
        orch = MagicMock()
        orch.retrieve_for_phase.return_value = {"rag_anti_pattern": "x"}
        llm = self._build_llm(orchestrator=orch, rag_enabled=True,
                              last_query="q")
        result = llm._retrieve_rag_slots("phase_b_preprocess")
        assert result == {"rag_anti_pattern": "x"}
        orch.retrieve_for_phase.assert_called_once_with(
            phase="phase_b_preprocess",
            query="q",
            domain="tflex",
            rag_enabled=True,
            extra_filters=None,
        )

    def test_phase_b_gated_off_by_default(self):
        """v0.16.0+ M6.3 Sprint 3:rag_phase_bc_enabled=False(default)→ Phase B 不抽 RAG。"""
        from unittest.mock import MagicMock
        orch = MagicMock()
        orch.retrieve_for_phase.return_value = {"rag_anti_pattern": "x"}
        llm = self._build_llm(orchestrator=orch, rag_enabled=True,
                              last_query="q")
        llm.rag_phase_bc_enabled = False   # 預設值
        result = llm._retrieve_rag_slots("phase_b_preprocess")
        assert result == {}
        orch.retrieve_for_phase.assert_not_called()

    def test_phase_c_gated_off_by_default(self):
        from unittest.mock import MagicMock
        orch = MagicMock()
        orch.retrieve_for_phase.return_value = {"rag_chart_recipe": "x"}
        llm = self._build_llm(orchestrator=orch, rag_enabled=True,
                              last_query="q")
        llm.rag_phase_bc_enabled = False
        result = llm._retrieve_rag_slots(
            "phase_c_chart", extra_filters={"intent": "bar"},
        )
        assert result == {}
        orch.retrieve_for_phase.assert_not_called()

    def test_phase_b_with_explicit_enable(self):
        """rag_phase_bc_enabled=True → Phase B 該照常抽 RAG。"""
        from unittest.mock import MagicMock
        orch = MagicMock()
        orch.retrieve_for_phase.return_value = {"rag_anti_pattern": "x"}
        llm = self._build_llm(orchestrator=orch, rag_enabled=True,
                              last_query="q")
        llm.rag_phase_bc_enabled = True
        result = llm._retrieve_rag_slots("phase_b_preprocess")
        assert result == {"rag_anti_pattern": "x"}

    def test_phase_0_unaffected_by_bc_gate(self):
        """Phase 0/A/D 不受 rag_phase_bc_enabled 影響。"""
        from unittest.mock import MagicMock
        orch = MagicMock()
        orch.retrieve_for_phase.return_value = {"rag_schema": "x"}
        llm = self._build_llm(orchestrator=orch, rag_enabled=True,
                              last_query="q")
        llm.rag_phase_bc_enabled = False   # B/C 關
        # Phase 0 該照樣跑
        result = llm._retrieve_rag_slots("phase_0_plan")
        assert result == {"rag_schema": "x"}

    def test_phase_c_with_intent_filter(self):
        """Phase C 該帶 intent filter 過濾 chart_recipe。"""
        from unittest.mock import MagicMock
        orch = MagicMock()
        orch.retrieve_for_phase.return_value = {"rag_chart_recipe": "..."}
        llm = self._build_llm(orchestrator=orch, rag_enabled=True,
                              last_query="顯示 pie")
        result = llm._retrieve_rag_slots(
            "phase_c_chart",
            extra_filters={"intent": "pie"},
        )
        assert result == {"rag_chart_recipe": "..."}
        orch.retrieve_for_phase.assert_called_once_with(
            phase="phase_c_chart",
            query="顯示 pie",
            domain="tflex",
            rag_enabled=True,
            extra_filters={"intent": "pie"},
        )
