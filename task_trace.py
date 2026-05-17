"""
v0.7.0 — Task Trace Recorder

對每次 user query 完整記錄:
  - 每個 phase / function 的 elapsed time
  - 每個 LLM call 的完整 messages(system + user)+ response + tokens
  - intent routing 結果(chart intent / preprocess intent)

寫進 MongoDB `task_traces` collection,在 Streamlit `pages/05_task_traces.py`
可逐步驟展開檢視。

設計目標:
  - 不侵入既有 phase 邏輯,LLMService 透過 `self.trace` 自動 hook
  - 即使 MongoDB 不可用也不會 crash(silent no-op)
  - 序列化失敗時降級 best-effort,不阻塞 user query
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from contextlib import contextmanager
from typing import Any


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


class TaskTrace:
    """單一 query 的完整執行 trace。

    使用方式:
        trace = TaskTrace(db=mongo_db, domain="tflex", query=user_query)
        llm_service.trace = trace  # LLM call 自動記錄
        with trace.step("phase_0_plan", kind="llm_call"):
            plan = llm_service.generate_plan(...)
        trace.finalize("completed")
    """

    def __init__(self, db: Any = None, domain: str = "",
                  query: str = "", collection_name: str = "task_traces"):
        self.trace_id = str(uuid.uuid4())
        self.db = db
        self.collection_name = collection_name
        self.doc: dict = {
            "trace_id": self.trace_id,
            "domain": domain,
            "query": query,
            "started_at": _now_utc(),
            "completed_at": None,
            "total_wall_s": None,
            "status": "running",
            "intent_chart": None,
            "intent_preprocess": None,
            "steps": [],
            "summary": {
                "total_llm_calls": 0,
                "total_prompt_tokens": 0,
                "total_completion_tokens": 0,
                "total_function_steps": 0,
            },
            "error": None,
        }
        self._task_start = time.time()
        self._current_step_stack: list[dict] = []

    # ────────────────────────────────────────────────────────────
    # Step context manager
    # ────────────────────────────────────────────────────────────
    @contextmanager
    def step(self, phase: str, kind: str = "function",
              meta: dict | None = None):
        """記錄一個 phase / function step。

        Args:
            phase: phase 名稱(e.g. 'phase_0_plan', 'phase_b_preprocess',
                    'sanitize_pipeline', 'rescue_empty_echarts')
            kind: 'function' | 'llm_call' | 'post_process'
            meta: 額外的 metadata(e.g. {'intent': 'pie'})
        """
        step_doc = {
            "step_id": len(self.doc["steps"]),
            "phase": phase,
            "kind": kind,
            "started_at": _now_utc(),
            "elapsed_s": None,
            "meta": dict(meta or {}),
            "error": None,
        }
        self._current_step_stack.append(step_doc)
        t0 = time.time()
        try:
            yield step_doc
        except Exception as e:
            step_doc["error"] = f"{type(e).__name__}: {str(e)[:500]}"
            raise
        finally:
            step_doc["elapsed_s"] = round(time.time() - t0, 3)
            self._current_step_stack.pop()
            self.doc["steps"].append(step_doc)
            if kind == "function":
                self.doc["summary"]["total_function_steps"] += 1

    # ────────────────────────────────────────────────────────────
    # LLM call recording(LLMService._call_llm 內部呼叫)
    # ────────────────────────────────────────────────────────────
    def record_llm_call(self, phase: str, model: str,
                         messages: list, response: str,
                         prompt_tokens: int | None,
                         completion_tokens: int | None,
                         total_tokens: int | None,
                         elapsed_s: float,
                         intent: str | None = None,
                         error: str | None = None) -> None:
        """記錄一次完整的 LLM call(messages + response + tokens)。

        若目前在 step context 內(_current_step_stack 非空),把 LLM call
        資訊塞進該 step;否則新建一個 step。
        """
        llm_payload = {
            "model": model,
            "messages": _safe_serialize_messages(messages),
            "response": _safe_truncate(response, max_len=200_000),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "intent": intent,
        }
        if self._current_step_stack:
            # Attach LLM call payload to current open step
            current = self._current_step_stack[-1]
            current["kind"] = "llm_call"  # promote kind
            current["llm_call"] = llm_payload
            if error:
                current["error"] = error
        else:
            # No open step → make a standalone llm_call step
            step_doc = {
                "step_id": len(self.doc["steps"]),
                "phase": phase,
                "kind": "llm_call",
                "started_at": _now_utc(),
                "elapsed_s": round(elapsed_s, 3),
                "llm_call": llm_payload,
                "meta": {},
                "error": error,
            }
            self.doc["steps"].append(step_doc)

        # Aggregate summary counters
        self.doc["summary"]["total_llm_calls"] += 1
        self.doc["summary"]["total_prompt_tokens"] += prompt_tokens or 0
        self.doc["summary"]["total_completion_tokens"] += completion_tokens or 0

    # ────────────────────────────────────────────────────────────
    # Intent hints(由 detector 呼叫一次)
    # ────────────────────────────────────────────────────────────
    def set_chart_intent(self, intent: str) -> None:
        self.doc["intent_chart"] = intent

    def set_preprocess_intent(self, intent: str) -> None:
        self.doc["intent_preprocess"] = intent

    # ────────────────────────────────────────────────────────────
    # 結束 + 寫 DB
    # ────────────────────────────────────────────────────────────
    def finalize(self, status: str = "completed",
                  error: str | None = None) -> str:
        """收尾並寫入 DB(若 db 可用)。回傳 trace_id。"""
        self.doc["completed_at"] = _now_utc()
        self.doc["total_wall_s"] = round(time.time() - self._task_start, 3)
        self.doc["status"] = status
        if error:
            self.doc["error"] = str(error)[:1000]
        if self.db is not None:
            try:
                self.db[self.collection_name].insert_one(_safe_doc(self.doc))
            except Exception as e:
                # silent — trace 失敗不該影響 user query
                import logging
                logging.getLogger(__name__).warning(
                    f"TaskTrace 寫入 DB 失敗(silent fallback): {e}"
                )
        return self.trace_id


# ────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────
def _safe_truncate(s: Any, max_len: int = 200_000) -> str:
    """字串截斷,避免單一 trace 過大撐爆 DB。"""
    if not isinstance(s, str):
        try:
            s = str(s)
        except Exception:
            return ""
    if len(s) <= max_len:
        return s
    return s[:max_len] + f"\n\n... (truncated, total {len(s):,} chars)"


def _safe_serialize_messages(messages: list) -> list:
    """把 OpenAI-style messages list 轉成 MongoDB-safe dict list。"""
    out = []
    if not isinstance(messages, list):
        return out
    for msg in messages:
        if isinstance(msg, dict):
            out.append({
                "role": str(msg.get("role", "unknown")),
                "content": _safe_truncate(msg.get("content", ""), 200_000),
            })
        else:
            out.append({"role": "unknown", "content": _safe_truncate(msg)})
    return out


def _safe_doc(doc: dict) -> dict:
    """處理 dict 內可能存在的 numpy / pandas 型別,避免 MongoDB 寫入失敗。

    v0.11.0.1:不能再用 json.dumps(default=str),那會把 datetime 轉成字串,
    MongoDB 存進去就不是 BSON date,所有時間窗口查詢全失敗(failure_filter 永遠
    miss)。改用 recursive sanitizer:對 numpy / pandas / set 等型別做轉換,
    但 datetime 保留原樣交給 pymongo bson encoder。
    """
    return _sanitize(doc)


def _sanitize(obj):
    """Recursively sanitize a value to be MongoDB-safe while preserving datetime."""
    # datetime / date: keep as-is(pymongo bson 會自然處理)
    if isinstance(obj, datetime):
        return obj
    # primitives that pymongo handles natively
    if isinstance(obj, (str, int, float, bool, type(None))):
        return obj
    # dict: recurse
    if isinstance(obj, dict):
        return {str(k): _sanitize(v) for k, v in obj.items()}
    # list / tuple / set: recurse
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [_sanitize(v) for v in obj]
    # numpy scalars → Python native
    try:
        import numpy as _np
        if isinstance(obj, _np.integer):
            return int(obj)
        if isinstance(obj, _np.floating):
            return float(obj)
        if isinstance(obj, _np.bool_):
            return bool(obj)
        if isinstance(obj, _np.ndarray):
            return _sanitize(obj.tolist())
    except ImportError:
        pass
    # pandas Timestamp → datetime
    try:
        import pandas as _pd
        if isinstance(obj, _pd.Timestamp):
            return obj.to_pydatetime()
        if hasattr(obj, "to_dict"):  # DataFrame / Series
            try:
                return _sanitize(obj.to_dict())
            except Exception:
                pass
    except ImportError:
        pass
    # fallback: stringify(不會打到 datetime,因為前面已 return)
    try:
        return str(obj)
    except Exception:
        return None


# ────────────────────────────────────────────────────────────
# Repository helper(用於 admin page 讀 trace)
# ────────────────────────────────────────────────────────────
class TaskTraceRepository:
    """讀寫 task_traces collection 的薄包裝。"""

    def __init__(self, db, collection_name: str = "task_traces"):
        self.db = db
        self.collection_name = collection_name

    def list_recent(self, limit: int = 20, domain: str = "") -> list[dict]:
        if self.db is None:
            return []
        query = {}
        if domain:
            query["domain"] = domain
        cursor = (self.db[self.collection_name]
                   .find(query, {
                       "trace_id": 1, "domain": 1, "query": 1,
                       "started_at": 1, "total_wall_s": 1, "status": 1,
                       "intent_chart": 1, "intent_preprocess": 1,
                       "summary": 1,
                   })
                   .sort("started_at", -1)
                   .limit(limit))
        return list(cursor)

    def get_by_id(self, trace_id: str) -> dict | None:
        if self.db is None:
            return None
        return self.db[self.collection_name].find_one({"trace_id": trace_id})

    def delete(self, trace_id: str) -> bool:
        if self.db is None:
            return False
        result = self.db[self.collection_name].delete_one({"trace_id": trace_id})
        return result.deleted_count > 0

    def purge_older_than(self, days: int) -> int:
        """刪除 N 天前的 trace,回傳刪除筆數。"""
        if self.db is None:
            return 0
        from datetime import timedelta
        cutoff = _now_utc() - timedelta(days=days)
        result = self.db[self.collection_name].delete_many(
            {"started_at": {"$lt": cutoff}}
        )
        return result.deleted_count
