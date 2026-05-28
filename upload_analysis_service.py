"""
upload_analysis_service.py — v0.12.0+ (M3)

Upload Workspace 的對話分析 orchestrator — 對應 schema-driven `app.py` 主 pipeline,
但 Phase A 走 Pandas filter,並接 `analysis_sessions` 做 session-level 對話狀態。

# 5-phase pipeline(對應 app.py 行 755-1408)

```
caller (pages/08_data_analysis.py Section 10) 給 query
   │
   ▼
[Pre-Phase 0]  classify_intent_for_query
   ├─ meta intent → generate_meta_response, return early
   ├─ follow-up → 從 session.last_analysis 取脈絡
   └─ analysis ↓
   │
   ▼
[Phase 0]  LLMService.generate_plan(metadata 含 source_type='upload')
           → 走 `phase_0_plan_upload` prompt(描述 A 段為 Pandas filter)
           → 偵測 [REFUSE] 短路
   │
   ▼
[Phase A · Upload-specific]
   ├─ Load parquet → source_df
   ├─ LLMService.generate_pandas_extraction(...) → Pandas code
   ├─ exec in restricted namespace(只給 pd / np / source_df)
   ├─ phase_a_validator.validate_phase_a_output → 若 fail 進 retry
   └─ 取 raw_df,3 attempts
   │
   ▼
[Phase B]  reuse 既有 generate_preprocess_code(無改動)
[Phase C]  reuse 既有 generate_echarts_option / generate_plot_code
[Phase D]  reuse 既有 generate_insight
   │
   ▼
寫進 session.messages + 更新 session.last_analysis
finalize task_trace(source_type='upload')
回傳完整 result dict 給 UI
```

# 凍結驗證
- 此模組是新增,既有 caller 用不到
- Phase B/C/D reuse 既有 LLMService 方法(零改動)
- Phase 0/A 走 `source_type='upload'` branch(M2 + M3 內部已驗證 byte-equal)
- TaskTrace 加 source_type='upload' 旗標,跟 schema-driven trace 區分
"""

from __future__ import annotations

import json
import logging
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Optional

import pandas as pd
import numpy as np

import file_parser
import phase_a_validator
import phase_b_validator
import phase_c_validator
from safe_exec import safe_exec_pandas, check_dataframe_limits
from llm_service import (
    is_dashboard_query,
    sanitize_pipeline,
    rescue_empty_echarts,
    ensure_default_styling,
    coerce_option_native_types,
    _detect_chart_intent,
    _detect_preprocess_intent,
)
from task_trace import TaskTrace
from upload_repository import UploadRepository

logger = logging.getLogger(__name__)


# ============================================================
# Sandbox safety helper
# ============================================================
# v0.17.0 · Progressive phase callback
# Event taxonomy (see UI_REFACTOR_PLAN Sprint A2):
#   phase_id: 'phase_0_plan' | 'phase_a_pipeline' | 'phase_b_preprocess'
#             | 'phase_c_chart' | 'phase_d_insight'
#   event:    'start' | 'complete' | 'error' | 'skipped'
PhaseCallback = Callable[[str, str, dict], None]


def _safe_emit_phase(
    on_phase: Optional[PhaseCallback],
    phase_id: str,
    event: str,
    payload: dict,
) -> None:
    """Best-effort callback invocation。on_phase 為 None 時 no-op,
    callback raise 時 log warning 但不阻斷 pipeline。
    """
    if on_phase is None:
        return
    try:
        on_phase(phase_id, event, payload)
    except Exception as e:
        logger.warning(
            f"on_phase callback failed for {phase_id}/{event}: {e}"
        )


def _build_phase_a_namespace(
    source_df: pd.DataFrame,
    source_dfs: dict[str, pd.DataFrame] | None = None,
) -> dict:
    """為 Phase A exec 建構 restricted namespace。

    暴露 `pd` / `np` / `source_df`(legacy single-table) / `source_dfs`
    (v0.18 M4 Tier B · multi-table dict 由 table_id 索引)。**故意省略**
    open/os/subprocess/__builtins__ 等;LLM 真的寫 import xxx 也會在
    phase_a_validator 被攔。

    Args:
        source_df: 首張 table 的 DataFrame(向下相容,單表場景仍可用)
        source_dfs: optional {table_id: DataFrame} dict(多表場景)
    """
    ns = {
        "pd": pd,
        "np": np,
        "source_df": source_df,
        # __builtins__ 仍會被 Python 注入,但 validator 已擋 import / IO 關鍵字
    }
    if source_dfs:
        ns["source_dfs"] = source_dfs
    return ns


# ============================================================
# 主 service
# ============================================================
class UploadAnalysisService:
    """Upload Workspace 的 5-phase orchestrator。

    每個 dataset 可有多個 analysis_session;每個 session 內可 chat 多輪。
    """

    def __init__(
        self,
        mongo_db,
        upload_repo: UploadRepository,
        llm_service,
        uploads_root: Path | None = None,
    ):
        """
        Args:
            mongo_db: pymongo Database(必須能寫,用於 task_traces)
            upload_repo: UploadRepository instance
            llm_service: 已 bound 到 upload metadata 的 LLMService
                (caller 端用 UploadMetadataProvider.get_metadata 拿 metadata 後 build)
            uploads_root: project_root/uploads(parquet 讀取用)
        """
        self.db = mongo_db
        self.repo = upload_repo
        self.llm = llm_service
        self.uploads_root = Path(uploads_root) if uploads_root else None

    # ============================================================
    # Session 管理
    # ============================================================
    def start_session(
        self,
        dataset_id: str,
        metadata_version: int,
        user: str = "anonymous",
    ) -> str:
        return self.repo.create_session(
            dataset_id=dataset_id,
            metadata_version=metadata_version,
            user=user,
        )

    # ============================================================
    # 主入口:處理一個 query
    # ============================================================
    def handle_query(
        self,
        session_id: str,
        query: str,
        chart_engine: str = "ECharts",
        enable_insight: bool = True,
        on_phase: Optional[PhaseCallback] = None,
    ) -> dict:
        """端對端跑 query — 寫進 session,寫 task_trace,回完整 result dict。

        Args:
            session_id: analysis_sessions._id
            query: 使用者輸入字串
            chart_engine: 'ECharts' or 'Plotly'
            enable_insight: True 才跑 Phase D
            on_phase: v0.17 新增 · Optional progressive callback。
                Signature: `(phase_id: str, event: str, payload: dict) -> None`
                phase_id: 'phase_0_plan' | 'phase_a_pipeline' |
                          'phase_b_preprocess' | 'phase_c_chart' | 'phase_d_insight'
                event:    'start' | 'complete' | 'error' | 'skipped'
                None(default)→ 不發 callback,行為 byte-equal v0.16。
                Callback raise 不會阻斷 pipeline(會 log warning)。

        Returns:
            result dict:
            {
              "status": "completed" | "refused" | "meta" | "failed",
              "intent": "...",                # Pre-Phase 0 classify 結果
              "trace_id": "...",
              "plan_text": str,
              "phase_a_code": str | None,
              "raw_df_info": {n_rows, columns} | None,
              "phase_b_code": str | None,
              "Q_info": {n_rows, columns} | None,
              "Q": pd.DataFrame | None,
              "phase_c_code": str | None,
              "chart_option": dict | None,
              "chart_fig": object | None,
              "use_table_fallback": bool,
              "insight": str | None,
              "error": str | None,
              "messages_appended": [user_msg_dict, assistant_msg_dict],
            }
        """
        session = self.repo.get_session(session_id)
        if not session:
            return self._error_result(f"Session `{session_id}` 不存在")
        dataset_id = session["dataset_id"]

        # ── 紀錄 user message ──
        self.repo.append_message(session_id, role="user", content=query)

        # ── 拿 metadata + 起 trace ──
        active_meta_doc = self.repo.get_active_metadata(dataset_id)
        if not active_meta_doc:
            return self._error_result(
                f"Dataset `{dataset_id}` 沒 active metadata"
            )
        metadata_version = active_meta_doc["version"]

        trace = TaskTrace(
            db=self.db,
            domain=dataset_id,
            query=query,
            source_type="upload",
        )
        # task_trace 寫進去這些 meta
        trace.doc["dataset_id"] = dataset_id
        trace.doc["metadata_version"] = metadata_version
        trace.doc["session_id"] = session_id
        self.llm.trace = trace

        try:
            return self._handle_query_inner(
                session_id=session_id,
                dataset_id=dataset_id,
                query=query,
                chart_engine=chart_engine,
                enable_insight=enable_insight,
                trace=trace,
                session=session,
                on_phase=on_phase,
            )
        finally:
            self.llm.trace = None  # 一定 detach

    # ============================================================
    # 內部主流程
    # ============================================================
    def _handle_query_inner(
        self,
        session_id: str,
        dataset_id: str,
        query: str,
        chart_engine: str,
        enable_insight: bool,
        trace: TaskTrace,
        session: dict,
        on_phase: Optional[PhaseCallback] = None,
    ) -> dict:
        last_analysis = session.get("last_analysis")

        # ────────────────────────────────────────
        # Pre-Phase 0:intent router
        # ────────────────────────────────────────
        intent_result = self.llm.classify_intent_for_query(
            query, last_analysis=last_analysis,
        )
        intent = intent_result.get("intent", "analysis")
        is_followup = intent_result.get("is_followup", False)

        if intent != "analysis":
            meta_md = self.llm.generate_meta_response(
                intent,
                subject=intent_result.get("subject", ""),
                query=query,
            )
            self.repo.append_message(
                session_id, role="assistant",
                content=meta_md, meta_intent=intent,
            )
            trace.finalize(status="completed")
            return {
                "status": "meta",
                "intent": intent,
                "trace_id": trace.trace_id,
                "meta_response": meta_md,
                "messages_appended": 2,
            }

        followup_context = last_analysis if is_followup else None

        # ────────────────────────────────────────
        # Phase 0: Plan
        # ────────────────────────────────────────
        plan_text = ""
        _t0_phase0 = time.time()
        _safe_emit_phase(on_phase, "phase_0_plan", "start", {"query": query})
        try:
            with trace.step("phase_0_plan", kind="llm_call"):
                plan_res = self.llm.generate_plan(
                    query, followup_context=followup_context,
                )
            if plan_res["status"] == "error":
                _safe_emit_phase(
                    on_phase, "phase_0_plan", "error",
                    {"phase": "phase_0_plan", "error": plan_res["message"],
                     "traceback": ""},
                )
                trace.finalize(status="failed", error=plan_res["message"])
                return self._error_result(plan_res["message"], trace=trace)
            plan_text = plan_res["message"]
        except Exception as e:
            _safe_emit_phase(
                on_phase, "phase_0_plan", "error",
                {"phase": "phase_0_plan", "error": str(e),
                 "traceback": traceback.format_exc()},
            )
            trace.finalize(status="failed", error=str(e))
            return self._error_result(f"Phase 0 失敗:{e}", trace=trace)

        # Refusal 短路
        is_refusal = (
            plan_text.strip()[:400].startswith("[REFUSE]")
            or "[REFUSE]" in plan_text[:400]
            or any(kw in plan_text[:400] for kw in (
                "無法執行", "無法分析", "無法計算", "資料限制觸犯",
            ))
        )
        # v0.17:Phase 0 完成事件(含 is_refusal 旗標,讓 UI 決定是否往下顯示)
        _safe_emit_phase(
            on_phase, "phase_0_plan", "complete",
            {
                "plan_text": plan_text,
                "elapsed_s": time.time() - _t0_phase0,
                "is_refusal": is_refusal,
            },
        )
        if is_refusal:
            clean_msg = plan_text.replace("[REFUSE]", "").strip()
            self.repo.append_message(
                session_id, role="assistant",
                content=f"⚠️ 資料不足\n\n{clean_msg}",
                refusal=True, plan_text=plan_text,
            )
            trace.finalize(status="refused")
            return {
                "status": "refused",
                "intent": intent,
                "trace_id": trace.trace_id,
                "plan_text": plan_text,
                "refusal_message": clean_msg,
                "messages_appended": 2,
            }

        # v0.18+ [META] 短路 — 結構性問題已在 plan_text 完整回答,
        # 不必走 Phase A/B/C/D。同 [REFUSE] 的 marker 機制,
        # 由 Phase 0 prompt 教 LLM 標記。省去 ~50s 不必要的 Phase exec。
        is_meta = (
            plan_text.strip()[:400].startswith("[META]")
            or "[META]" in plan_text[:400]
        )
        if is_meta:
            clean_plan = plan_text.replace("[META]", "").strip()
            self.repo.append_message(
                session_id, role="assistant",
                content=clean_plan,
                meta_structural=True,
                plan_text=plan_text,
            )
            trace.finalize(status="completed")
            return {
                "status": "meta_structural",
                "intent": intent,
                "trace_id": trace.trace_id,
                "plan_text": plan_text,
                "messages_appended": 2,
            }

        # ────────────────────────────────────────
        # Phase A: Pandas filter
        # ────────────────────────────────────────
        _t0_phase_a = time.time()
        _safe_emit_phase(on_phase, "phase_a_pipeline", "start", {})

        # 載入 source_df + source_dfs(v0.18 M4 Tier B · multi-table)
        try:
            tables = self.repo.list_tables(dataset_id)
            if not tables:
                raise ValueError(f"Dataset `{dataset_id}` 沒 table")
            # Load every table into source_dfs dict (keyed by table_id)
            source_dfs: dict[str, pd.DataFrame] = {}
            for t in tables:
                source_dfs[t["table_id"]] = file_parser.load_parquet(
                    t["storage"]["path"]
                )
            # Single-table backward compat: source_df = first table.
            # Existing Phase A code that writes `raw_df = source_df[...]`
            # continues to work; new multi-table code uses source_dfs[id].
            table = tables[0]
            source_df = source_dfs[table["table_id"]]
            source_columns = list(source_df.columns)
            # tables_info exposes per-table column lists to the Phase A
            # prompt so the LLM knows which columns live on which table.
            tables_info = {
                tid: list(df.columns) for tid, df in source_dfs.items()
            }
            # v0.18 M4 Tier B follow-up · tables_meta carries the
            # metadata-level facts (primary_key / grain / description) that
            # aren't visible from a raw DataFrame at runtime. Lets the LLM
            # populate "primary_key" column for META questions instead of
            # defaulting to None.
            _md_collections = (self.llm.task_metadata or {}).get(
                "collections"
            ) or {}
            tables_meta = {
                tid: {
                    "primary_key": coll.get("primary_key"),
                    "grain": coll.get("grain"),
                    "description": coll.get("description"),
                    "table_role": coll.get("table_role"),
                }
                for tid, coll in _md_collections.items()
            }
        except Exception as e:
            _safe_emit_phase(
                on_phase, "phase_a_pipeline", "error",
                {"phase": "phase_a_pipeline", "error": str(e),
                 "traceback": traceback.format_exc()},
            )
            trace.finalize(status="failed", error=str(e))
            return self._error_result(f"載入 parquet 失敗:{e}", trace=trace)

        # 產 raw_df sample 給 LLM 看
        try:
            source_sample_md = source_df.head(3).to_markdown(index=False)
        except Exception:
            source_sample_md = source_df.head(3).to_string(index=False)

        # v0.14.2+: 檢查 source_df 沒爆 row/col limit(MVP 100K rows)
        ok, limit_err = check_dataframe_limits(source_df, max_rows=100_000)
        if not ok:
            _safe_emit_phase(
                on_phase, "phase_a_pipeline", "error",
                {"phase": "phase_a_pipeline", "error": limit_err,
                 "traceback": ""},
            )
            trace.finalize(status="failed", error=limit_err)
            return self._error_result(limit_err, trace=trace)

        phase_a_code = None
        phase_a_err = None
        raw_df: pd.DataFrame | None = None

        for attempt in range(3):
            try:
                with trace.step(f"phase_a_pandas_extraction_attempt_{attempt + 1}",
                                kind="llm_call"):
                    phase_a_code = self.llm.generate_pandas_extraction(
                        query=query,
                        plan_text=plan_text,
                        source_columns=source_columns,
                        source_df_sample=source_sample_md,
                        previous_code=phase_a_code if attempt > 0 else "",
                        previous_error=phase_a_err if attempt > 0 else "",
                        tables_info=tables_info if len(tables_info) > 1 else None,
                    )

                # v0.14.2+: 走 safe_exec sandbox(restricted builtins + timeout +
                # output validation),取代裸 exec
                # v0.18 M4 Tier B: 多表時 source_dfs + tables_meta 也注入 sandbox
                _exec_inputs = {"source_df": source_df}
                if len(tables_info) > 1:
                    _exec_inputs["source_dfs"] = source_dfs
                    _exec_inputs["tables_meta"] = tables_meta
                with trace.step(f"phase_a_exec_attempt_{attempt + 1}"):
                    exec_result = safe_exec_pandas(
                        code=phase_a_code,
                        inputs=_exec_inputs,
                        expected_output_var="raw_df",
                        timeout_s=30.0,
                        max_rows=100_000,
                    )
                if not exec_result.success:
                    # safe_exec 失敗 → 轉成 exception 走 retry loop
                    raise RuntimeError(
                        f"[{exec_result.error_type}] {exec_result.error}"
                    )

                # Validator(static check on code + dynamic check on ns)
                _validator_ns = {
                    "raw_df": exec_result.output,
                    "source_df": source_df,
                }
                issues = phase_a_validator.validate_phase_a_output(
                    code=phase_a_code,
                    exec_namespace=_validator_ns,
                    source_columns=source_columns,
                )
                if issues and attempt < 2:
                    phase_a_err = phase_a_validator.format_issues_as_retry_hint(issues)
                    phase_a_err += "\n\n" + phase_a_validator.PANDAS_FILTER_ANTIPATTERN_CHEATSHEET
                    continue
                elif issues:
                    logger.warning(f"Phase A validator 3 次都失敗:{issues}")

                raw_df = exec_result.output
                break
            except Exception:
                phase_a_err = traceback.format_exc()
                if attempt >= 2:
                    _safe_emit_phase(
                        on_phase, "phase_a_pipeline", "error",
                        {"phase": "phase_a_pipeline",
                         "error": "Phase A 連續 3 次失敗",
                         "traceback": phase_a_err},
                    )
                    trace.finalize(status="failed", error=phase_a_err)
                    return self._error_result(
                        f"Phase A 連續 3 次失敗,最後錯誤:\n{phase_a_err[:300]}",
                        trace=trace, phase_a_code=phase_a_code,
                    )

        if raw_df is None:
            _safe_emit_phase(
                on_phase, "phase_a_pipeline", "error",
                {"phase": "phase_a_pipeline", "error": "raw_df 未取得",
                 "traceback": ""},
            )
            trace.finalize(status="failed", error="raw_df 未取得")
            return self._error_result("Phase A 完成但 raw_df 為空", trace=trace,
                                       phase_a_code=phase_a_code)

        # v0.17:Phase A 完成事件
        _safe_emit_phase(
            on_phase, "phase_a_pipeline", "complete",
            {
                "code": phase_a_code,
                "raw_df_info": {
                    "n_rows": int(len(raw_df)),
                    "columns": list(raw_df.columns),
                },
                "elapsed_s": time.time() - _t0_phase_a,
            },
        )

        # ────────────────────────────────────────
        # Phase B: Pandas processing(reuse 既有 generate_preprocess_code)
        # v0.14.2+: 走 safe_exec(同 Phase A)
        # ────────────────────────────────────────
        _t0_phase_b = time.time()
        _safe_emit_phase(on_phase, "phase_b_preprocess", "start", {})

        # v0.18 M4 Tier B · Phase C 也暴露 source_df + source_dfs(多表時),
        # 與 Phase A/B 一致避免 NameError。雖然 Phase C prompt 應只用 Q,
        # 但 LLM 若意外引用 source data 會被這層 catch 住。
        workflow_ns: dict = {
            "pd": pd, "np": np, "raw_df": raw_df,
            "source_df": source_df,
        }
        if len(tables_info) > 1:
            workflow_ns["source_dfs"] = source_dfs
            workflow_ns["tables_meta"] = tables_meta
        dashboard_mode = is_dashboard_query(query)
        try:
            raw_sample_md = raw_df.head(3).to_markdown(index=False)
        except Exception:
            raw_sample_md = raw_df.head(3).to_string(index=False)

        phase_b_code = None
        phase_b_err = None
        Q: pd.DataFrame | None = None

        for attempt in range(3):
            try:
                with trace.step(f"phase_b_preprocess_attempt_{attempt + 1}",
                                kind="llm_call"):
                    phase_b_code = self.llm.generate_preprocess_code(
                        query=query,
                        plan_text=plan_text,
                        available_columns=list(raw_df.columns),
                        raw_df_sample=raw_sample_md,
                        dashboard_hint=dashboard_mode,
                        previous_code=phase_b_code if attempt > 0 else "",
                        previous_error=phase_b_err if attempt > 0 else "",
                        tables_info=tables_info if len(tables_info) > 1 else None,
                    )

                # v0.18 M4 Tier B: expose source_df + source_dfs (multi-table)
                # in Phase B namespace too. Phase A's contract is filter/select;
                # Phase B's is preprocess (groupby/agg/derive). For multi-table
                # META questions or supplementary lookups, Phase B may need to
                # reference other sheets — without these in the sandbox the
                # LLM hits NameError.
                _b_inputs = {"raw_df": raw_df, "source_df": source_df}
                if len(tables_info) > 1:
                    _b_inputs["source_dfs"] = source_dfs
                    _b_inputs["tables_meta"] = tables_meta
                with trace.step(f"phase_b_exec_attempt_{attempt + 1}"):
                    exec_result = safe_exec_pandas(
                        code=phase_b_code,
                        inputs=_b_inputs,
                        expected_output_var="Q",
                        timeout_s=60.0,    # Phase B 比 A 重(groupby/agg)
                        max_rows=100_000,
                    )
                if not exec_result.success:
                    raise RuntimeError(
                        f"[{exec_result.error_type}] {exec_result.error}"
                    )
                Q = exec_result.output
                # safe_exec 已自動 Series → DataFrame,workflow_ns 同步
                workflow_ns["Q"] = Q

                # Phase B semantic validator
                issues = phase_b_validator.validate_phase_b_output(
                    Q, query=query, dashboard_mode=dashboard_mode,
                )
                if issues and attempt < 2:
                    phase_b_err = phase_b_validator.format_issues_as_retry_hint(issues)
                    continue
                break
            except Exception:
                phase_b_err = traceback.format_exc()
                if attempt >= 2:
                    _safe_emit_phase(
                        on_phase, "phase_b_preprocess", "error",
                        {"phase": "phase_b_preprocess",
                         "error": "Phase B 連續 3 次失敗",
                         "traceback": phase_b_err},
                    )
                    trace.finalize(status="failed", error=phase_b_err)
                    return self._error_result(
                        f"Phase B 連續 3 次失敗:\n{phase_b_err[:300]}",
                        trace=trace,
                        plan_text=plan_text, phase_a_code=phase_a_code,
                        phase_b_code=phase_b_code,
                    )

        if Q is None or (hasattr(Q, "empty") and Q.empty):
            _safe_emit_phase(
                on_phase, "phase_b_preprocess", "error",
                {"phase": "phase_b_preprocess", "error": "Phase B Q 為空",
                 "traceback": ""},
            )
            trace.finalize(status="failed", error="Phase B Q 為空")
            return self._error_result(
                "Phase B 完成但 Q 為空", trace=trace,
                plan_text=plan_text, phase_a_code=phase_a_code,
                phase_b_code=phase_b_code,
            )

        # v0.17:Phase B 完成事件
        try:
            _q_preview_md = Q.head(5).to_markdown(index=False)
        except Exception:
            _q_preview_md = Q.head(5).to_string(index=False)
        _safe_emit_phase(
            on_phase, "phase_b_preprocess", "complete",
            {
                "code": phase_b_code,
                "Q_info": {
                    "n_rows": int(len(Q)),
                    "columns": list(Q.columns),
                },
                "Q_preview_md": _q_preview_md,
                "elapsed_s": time.time() - _t0_phase_b,
            },
        )

        # ────────────────────────────────────────
        # Phase C: Visualization
        # ────────────────────────────────────────
        _t0_phase_c = time.time()
        _safe_emit_phase(on_phase, "phase_c_chart", "start", {})

        phase_c_code = None
        phase_c_err = None
        final_option: dict | None = None
        final_fig = None
        use_table_fallback = False

        for attempt in range(3):
            try:
                with trace.step(f"phase_c_attempt_{attempt + 1}",
                                kind="llm_call"):
                    if chart_engine == "ECharts":
                        phase_c_code = self.llm.generate_echarts_option(
                            query=query,
                            plan_text=plan_text,
                            q_columns=list(Q.columns),
                            previous_code=phase_c_code if attempt > 0 else "",
                            previous_error=phase_c_err if attempt > 0 else "",
                        )
                    else:
                        phase_c_code = self.llm.generate_plot_code(
                            query=query,
                            plan_text=plan_text,
                            q_columns=list(Q.columns),
                            previous_code=phase_c_code if attempt > 0 else "",
                            previous_error=phase_c_err if attempt > 0 else "",
                        )

                with trace.step(f"phase_c_exec_attempt_{attempt + 1}"):
                    exec(phase_c_code, workflow_ns, workflow_ns)

                if chart_engine == "ECharts":
                    opt = workflow_ns.get("option")
                    if not isinstance(opt, dict):
                        raise ValueError("Phase C 未產 option dict")
                    opt, _ = rescue_empty_echarts(opt, Q)
                    opt, _ = ensure_default_styling(opt, query)
                    opt = coerce_option_native_types(opt)
                    use_table_fallback = bool(opt.get("_use_table"))
                    if not use_table_fallback and "series" not in opt:
                        raise ValueError("ECharts option 缺 series")

                    # Phase C semantic validator
                    if not use_table_fallback:
                        intent_for_val = _detect_chart_intent(query)
                        issues = phase_c_validator.validate_phase_c_output(
                            opt, Q, query=query, intent=intent_for_val,
                        )
                        if issues and attempt < 2:
                            phase_c_err = phase_c_validator.format_issues_as_retry_hint(issues)
                            continue
                    final_option = opt
                else:
                    final_fig = workflow_ns.get("fig")
                    if not final_fig:
                        raise ValueError("Phase C 未產 fig")
                break
            except Exception:
                phase_c_err = traceback.format_exc()
                if attempt >= 2:
                    # Phase C 軟失敗 → 降級表格 fallback
                    use_table_fallback = True
                    final_option = {"_use_table": True, "_phase_c_fallback": True}
                    final_fig = None

        # v0.17:Phase C 完成事件(含 fallback flag)
        _safe_emit_phase(
            on_phase, "phase_c_chart", "complete",
            {
                "code": phase_c_code,
                "chart_option": final_option,
                "use_table_fallback": use_table_fallback,
                "elapsed_s": time.time() - _t0_phase_c,
            },
        )

        # ────────────────────────────────────────
        # Phase D: Insight(optional)
        # ────────────────────────────────────────
        insight_text = None
        if enable_insight:
            _t0_phase_d = time.time()
            _safe_emit_phase(on_phase, "phase_d_insight", "start", {})
            try:
                q_preview_md = Q.head(30).to_markdown(index=False)
            except Exception:
                q_preview_md = Q.head(30).to_string(index=False)
            try:
                with trace.step("phase_d_insight", kind="llm_call"):
                    insight_res = self.llm.generate_insight(
                        query, plan_text, q_preview_md,
                    )
                if insight_res["status"] == "success":
                    insight_text = insight_res["message"]
                _safe_emit_phase(
                    on_phase, "phase_d_insight", "complete",
                    {
                        "insight": insight_text or "",
                        "elapsed_s": time.time() - _t0_phase_d,
                    },
                )
            except Exception as e:
                logger.warning(f"Phase D 失敗(不阻塞):{e}")
                # Phase D 失敗不阻塞主流程,但要通知 UI
                _safe_emit_phase(
                    on_phase, "phase_d_insight", "error",
                    {"phase": "phase_d_insight", "error": str(e),
                     "traceback": traceback.format_exc()},
                )
        else:
            _safe_emit_phase(
                on_phase, "phase_d_insight", "skipped",
                {"reason": "enable_insight=False"},
            )

        # ────────────────────────────────────────
        # 寫 session.messages + last_analysis
        # ────────────────────────────────────────
        self.repo.append_message(
            session_id, role="assistant",
            content="分析已完成,如上方資料、圖表與洞察所示。",
            plan_text=plan_text,
            phase_a_code=phase_a_code,
            phase_b_code=phase_b_code,
            phase_c_code=phase_c_code,
            chart_engine=chart_engine,
            use_table_fallback=use_table_fallback,
            insight=insight_text,
            trace_id=trace.trace_id,
        )
        self.repo.update_last_analysis(session_id, {
            "query": query,
            "plan_summary": plan_text[:400],
            "Q_cols": list(Q.columns),
            "chart_engine": chart_engine,
            "was_followup": is_followup,
        })

        trace.finalize(status="completed")

        return {
            "status": "completed",
            "intent": intent,
            "trace_id": trace.trace_id,
            "plan_text": plan_text,
            "phase_a_code": phase_a_code,
            "raw_df_info": {
                "n_rows": int(len(raw_df)),
                "columns": list(raw_df.columns),
            },
            "phase_b_code": phase_b_code,
            "Q_info": {
                "n_rows": int(len(Q)),
                "columns": list(Q.columns),
            },
            "Q": Q,
            "phase_c_code": phase_c_code,
            "chart_option": final_option,
            "chart_fig": final_fig,
            "use_table_fallback": use_table_fallback,
            "insight": insight_text,
            "is_followup": is_followup,
            "error": None,
        }

    # ============================================================
    # Helpers
    # ============================================================
    @staticmethod
    def _error_result(
        msg: str,
        trace: TaskTrace | None = None,
        **partial,
    ) -> dict:
        return {
            "status": "failed",
            "trace_id": trace.trace_id if trace else None,
            "error": msg,
            **partial,
        }
