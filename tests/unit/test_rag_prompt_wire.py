"""tests/unit/test_rag_prompt_wire.py — M6.2 Sprint 1 Day 6-10 unit tests.

驗證 RAG wire-up 的兩個核心保證:

1. **Byte-equal**:RAG off / orchestrator=None / 無 rag_<slot> kwargs
   → render() output 跟 LLMService 內 inline f-string fallback 一模一樣
   (v0.15 freeze clause)

2. **Slot injection**:RAG on + orchestrator 回 slot text
   → rendered prompt 內含 slot 文字 + 對應 RAG section header

不跑真 LLM call,只測 prompt template rendering 路徑。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from embedded_prompts import EMBEDDED_PROMPTS
from prompt_repository import PromptRepository


# ============================================================
# Helpers
# ============================================================
@pytest.fixture
def repo():
    return PromptRepository(
        mongo_db=None,
        embedded_fallback=EMBEDDED_PROMPTS,
        enabled=False,   # 強制走 embedded
    )


_FAKE_DK = "[FAKE_DOMAIN_KNOWLEDGE_BLOCK]"


# ============================================================
# Byte-equal:no RAG kwargs → 等同空字串 defaults
# ============================================================
class TestByteEqualNoRAG:
    """確認 v0.16.0 模板修改後 + render() 自動補空 defaults,
    既有 caller(不傳 rag_*)輸出 byte-equal 過去版本(無 RAG block)。"""

    def test_phase_0_no_rag_kwargs(self, repo):
        out = repo.render("phase_0_plan", domain_knowledge=_FAKE_DK)
        # RAG section headers 不該出現
        assert "動態 context · 相關欄位 (RAG)" not in out
        assert "動態 context · 相關 KPI (RAG)" not in out
        assert "動態 context · 過往成功案例 (RAG)" not in out
        # 既有 schema-driven section 一定要在
        assert _FAKE_DK in out
        assert "拒絕協定" in out

    def test_phase_a_no_rag_kwargs(self, repo):
        out = repo.render("phase_a_pipeline", domain_knowledge=_FAKE_DK)
        assert "動態 context · 相關欄位 (RAG)" not in out
        assert "動態 context · 注意這些雷 (RAG)" not in out
        assert "動態 context · 過往成功案例 (RAG)" not in out
        assert _FAKE_DK in out
        assert "實作守則 (CRITICAL RULES)" in out

    def test_phase_d_no_rag_kwargs(self, repo):
        out = repo.render("phase_d_insight", domain_knowledge=_FAKE_DK)
        assert "動態 context · 相關 KPI (RAG)" not in out
        assert _FAKE_DK in out
        assert "通用分析原則" in out

    def test_explicit_empty_string_same_as_omitted(self, repo):
        """明示 rag_*='' vs 不傳該完全一樣。"""
        out_omit = repo.render("phase_0_plan", domain_knowledge=_FAKE_DK)
        out_empty = repo.render(
            "phase_0_plan", domain_knowledge=_FAKE_DK,
            rag_schema="", rag_kpi="", rag_few_shot="",
        )
        assert out_omit == out_empty

    def test_dk_immediately_followed_by_static_section(self, repo):
        """RAG off 時,domain_knowledge 後該直接接靜態 section,
        中間不該有 RAG header 或多餘 RAG-related 文字。"""
        out = repo.render("phase_0_plan", domain_knowledge=_FAKE_DK)
        # _FAKE_DK 與 拒絕協定 之間的 substring 不該含 "(RAG)"
        dk_idx = out.find(_FAKE_DK)
        refuse_idx = out.find("拒絕協定")
        assert dk_idx >= 0 and refuse_idx > dk_idx
        between = out[dk_idx + len(_FAKE_DK):refuse_idx]
        assert "(RAG)" not in between


# ============================================================
# Slot injection:有 rag_<slot> kwargs → slot 文字進 prompt
# ============================================================
class TestSlotInjection:
    def test_phase_0_schema_slot(self, repo):
        out = repo.render(
            "phase_0_plan", domain_knowledge=_FAKE_DK,
            rag_schema="company_code: 公司代碼",
        )
        assert "動態 context · 相關欄位 (RAG)" in out
        assert "company_code: 公司代碼" in out

    def test_phase_0_all_three_slots(self, repo):
        out = repo.render(
            "phase_0_plan", domain_knowledge=_FAKE_DK,
            rag_schema="[SCHEMA_TEXT]",
            rag_kpi="[KPI_TEXT]",
            rag_few_shot="[FEW_SHOT_TEXT]",
        )
        assert "[SCHEMA_TEXT]" in out
        assert "[KPI_TEXT]" in out
        assert "[FEW_SHOT_TEXT]" in out
        # 3 個 section header 都該在
        assert "相關欄位 (RAG)" in out
        assert "相關 KPI (RAG)" in out
        assert "過往成功案例 (RAG)" in out
        # Order:schema → kpi → few_shot
        idx_s = out.find("[SCHEMA_TEXT]")
        idx_k = out.find("[KPI_TEXT]")
        idx_f = out.find("[FEW_SHOT_TEXT]")
        assert idx_s < idx_k < idx_f

    def test_phase_a_anti_pattern_slot(self, repo):
        out = repo.render(
            "phase_a_pipeline", domain_knowledge=_FAKE_DK,
            rag_anti_pattern="❌ 不要漏 $project review_result",
        )
        assert "注意這些雷 (RAG)" in out
        assert "不要漏 $project review_result" in out

    def test_phase_d_kpi_slot(self, repo):
        out = repo.render(
            "phase_d_insight", domain_knowledge=_FAKE_DK,
            rag_kpi="approval_rate = Y/(Y+N) without R/X",
        )
        assert "相關 KPI (RAG)" in out
        assert "approval_rate = Y/(Y+N)" in out

    def test_partial_slot_only_one_section(self, repo):
        """只給 rag_kpi,schema/few_shot section 不該出現。"""
        out = repo.render(
            "phase_0_plan", domain_knowledge=_FAKE_DK,
            rag_kpi="[ONLY_KPI]",
        )
        assert "[ONLY_KPI]" in out
        assert "相關 KPI (RAG)" in out
        assert "相關欄位 (RAG)" not in out
        assert "過往成功案例 (RAG)" not in out


# ============================================================
# LLMService wiring:_retrieve_rag_slots 路由
# ============================================================
class TestLLMServiceRetrieveRAGSlots:
    """直接驗 LLMService._retrieve_rag_slots 在各情境的行為。
    不真連 LLM,只看它對 orchestrator 怎麼呼叫 / 回什麼。"""

    def _build_llm(self, **kwargs):
        """Bypass __init__ 的 LLM client 連線,只 mock 必要 state。"""
        from llm_service import LLMService
        # 建一個 minimal instance,跳過 OpenAI client 初始化
        llm = LLMService.__new__(LLMService)
        llm.retrieval_orchestrator = kwargs.get("orchestrator")
        llm.rag_enabled = kwargs.get("rag_enabled", False) and \
            llm.retrieval_orchestrator is not None
        llm.rag_phase_bc_enabled = kwargs.get("rag_phase_bc_enabled", True)
        llm._last_query = kwargs.get("last_query", "")
        llm.domain = kwargs.get("domain", "tflex")
        return llm

    def test_rag_disabled_returns_empty(self):
        orch = MagicMock()
        orch.retrieve_for_phase.return_value = {"rag_schema": "x"}
        llm = self._build_llm(
            orchestrator=orch, rag_enabled=False, last_query="q",
        )
        result = llm._retrieve_rag_slots("phase_0_plan")
        assert result == {}
        # orchestrator 不該被呼叫
        orch.retrieve_for_phase.assert_not_called()

    def test_no_orchestrator_returns_empty(self):
        llm = self._build_llm(
            orchestrator=None, rag_enabled=True, last_query="q",
        )
        # rag_enabled 該被 init 邏輯自動降為 False(orchestrator=None)
        assert llm.rag_enabled is False
        result = llm._retrieve_rag_slots("phase_0_plan")
        assert result == {}

    def test_empty_query_returns_empty(self):
        orch = MagicMock()
        llm = self._build_llm(
            orchestrator=orch, rag_enabled=True, last_query="",
        )
        result = llm._retrieve_rag_slots("phase_0_plan")
        assert result == {}
        orch.retrieve_for_phase.assert_not_called()

    def test_rag_enabled_calls_orchestrator(self):
        orch = MagicMock()
        orch.retrieve_for_phase.return_value = {
            "rag_schema": "[SCHEMA]", "rag_kpi": "[KPI]",
        }
        llm = self._build_llm(
            orchestrator=orch, rag_enabled=True,
            last_query="show me leadtime", domain="tflex",
        )
        result = llm._retrieve_rag_slots("phase_0_plan")
        assert result == {"rag_schema": "[SCHEMA]", "rag_kpi": "[KPI]"}
        orch.retrieve_for_phase.assert_called_once_with(
            phase="phase_0_plan",
            query="show me leadtime",
            domain="tflex",
            rag_enabled=True,
            extra_filters=None,
        )

    def test_orchestrator_exception_returns_empty(self):
        """orchestrator 炸 → swallow → 回 {}(prompt 走 fallback,不該整個壞掉)。"""
        orch = MagicMock()
        orch.retrieve_for_phase.side_effect = RuntimeError("vector store dead")
        llm = self._build_llm(
            orchestrator=orch, rag_enabled=True, last_query="q",
        )
        result = llm._retrieve_rag_slots("phase_0_plan")
        assert result == {}


# ============================================================
# Config:GENBI_RAG_ENABLED env
# ============================================================
class TestConfigFlag:
    def test_default_false(self, monkeypatch):
        monkeypatch.delenv("GENBI_RAG_ENABLED", raising=False)
        # 重 import config 拿到 fresh 值
        import importlib
        import config
        importlib.reload(config)
        assert config.RAG_ENABLED is False

    def test_env_true(self, monkeypatch):
        monkeypatch.setenv("GENBI_RAG_ENABLED", "true")
        import importlib
        import config
        importlib.reload(config)
        assert config.RAG_ENABLED is True

    def test_env_false_explicit(self, monkeypatch):
        monkeypatch.setenv("GENBI_RAG_ENABLED", "false")
        import importlib
        import config
        importlib.reload(config)
        assert config.RAG_ENABLED is False


# ============================================================
# Freeze-clause:RAG-off render output 該 byte-equal pre-RAG 行為
# ============================================================
# 用 Jinja2 whitespace control(`{%- if %}` / `{%- endif %}`),slot 為空時
# 整個 if-block 該 collapse 成零字元。驗證方式:render 後 DK 與下個靜態
# section 之間的「中間文字」該完全等於 pre-edit 的 separator(精確的
# 雙 newline `\n\n`,不帶任何 RAG 相關文字 / 空白污染)。
class TestFreezeClauseSeparator:
    """spec §10.2 freeze-clause:RAG off → DK 後直接接靜態 section,
    `\\n\\n` separator 必須 byte-precise 保留。"""

    def test_phase_0_separator_byte_precise(self, repo):
        dk = "[FAKE_DK_FOR_SEP_TEST]"
        out = repo.render("phase_0_plan", domain_knowledge=dk)
        dk_idx = out.find(dk)
        refuse_marker = "### 🚨 拒絕協定"
        refuse_idx = out.find(refuse_marker)
        assert dk_idx >= 0 and refuse_idx > dk_idx
        between = out[dk_idx + len(dk):refuse_idx]
        # 必須剛好是 "\n\n",不多不少
        assert between == "\n\n", (
            f"Phase 0 separator changed by RAG edit. "
            f"Expected '\\n\\n', got {between!r}"
        )

    def test_phase_a_separator_byte_precise(self, repo):
        dk = "[FAKE_DK_PHASE_A_SEP]"
        out = repo.render("phase_a_pipeline", domain_knowledge=dk)
        dk_idx = out.find(dk)
        next_marker = "### 實作守則 (CRITICAL RULES):"
        next_idx = out.find(next_marker)
        assert dk_idx >= 0 and next_idx > dk_idx
        between = out[dk_idx + len(dk):next_idx]
        assert between == "\n\n", (
            f"Phase A separator changed. Expected '\\n\\n', got {between!r}"
        )

    def test_phase_d_separator_byte_precise(self, repo):
        dk = "[FAKE_DK_PHASE_D_SEP]"
        out = repo.render("phase_d_insight", domain_knowledge=dk)
        dk_idx = out.find(dk)
        next_marker = "### 通用分析原則:"
        next_idx = out.find(next_marker)
        assert dk_idx >= 0 and next_idx > dk_idx
        between = out[dk_idx + len(dk):next_idx]
        assert between == "\n\n", (
            f"Phase D separator changed. Expected '\\n\\n', got {between!r}"
        )

    def test_no_jinja_artifacts_in_rag_off_output(self, repo):
        """RAG off rendered output 不該漏 Jinja2 語法殘留。"""
        for key in ("phase_0_plan", "phase_a_pipeline", "phase_d_insight"):
            out = repo.render(key, domain_knowledge="dk")
            assert "{%" not in out, f"{key}: Jinja2 tag leaked: {out!r}"
            assert "{{" not in out, f"{key}: Jinja2 var leaked: {out!r}"
            assert "endif" not in out, f"{key}: literal 'endif' leaked"


# ============================================================
# Render() auto-injects RAG defaults — caller backward compat
# ============================================================
class TestRenderAutoDefaults:
    def test_render_works_without_any_rag_kwargs(self, repo):
        """既有 caller(只傳 domain_knowledge)該照常 work,不該 raise UndefinedError。"""
        # 應該不 raise
        out = repo.render("phase_0_plan", domain_knowledge="x")
        assert "x" in out

    def test_caller_provided_overrides_default(self, repo):
        out = repo.render(
            "phase_0_plan", domain_knowledge="x",
            rag_schema="caller_value",
        )
        assert "caller_value" in out

    def test_partial_caller_kwargs_others_get_default(self, repo):
        """只給 rag_schema,rag_kpi/rag_few_shot 該自動為空。"""
        out = repo.render(
            "phase_0_plan", domain_knowledge="x",
            rag_schema="S",
        )
        assert "S" in out
        # rag_kpi/few_shot 該沒對應 header(空字串 → guard skip)
        assert "相關 KPI (RAG)" not in out
        assert "過往成功案例 (RAG)" not in out
