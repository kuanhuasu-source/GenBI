"""
learning/failure_filter.py — Week 1 D3

從 task_traces collection 撈出需要做 observation extraction 的 trace。

對齊 GenBI_v1.3_Self_Learning_MVP_Implementation_Spec.md §9:

  觸發 observation extraction 的條件(任一成立):
    - status = failed
    - status = refused        (本實作補充:Plan refuse 也值得分析)
    - any step has error      (對應 retry_count > 0 / fallback_used 的近似)
    - trace.needs_review = True  (manually flagged)

回傳精簡的 trace summary list,避免一次撈整個 trace doc(含大量 LLM
messages)塞爆記憶體。caller 視需要再用 trace_id 撈完整版。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import config


# ============================================================
# 主函式
# ============================================================
def get_failed_traces(
    db,
    *,
    since_days: int = 7,
    statuses: tuple = ("failed", "refused"),
    include_step_errors: bool = True,
    include_manual_flag: bool = True,
    limit: int = 200,
) -> list[dict]:
    """
    撈 last N days 內符合 failure filter 的 trace summary。

    Args:
        db: pymongo Database
        since_days: 看最近幾天(default 7)
        statuses: 要 include 的 status 值
        include_step_errors: 加上「任一 step 有 error」的 trace
        include_manual_flag: 加上 needs_review=True 的 trace
        limit: 最多回幾筆(避免一次撈太多)

    Returns:
        list[dict],每個 dict 含:
          - trace_id, query, domain, status, started_at, total_wall_s
          - intent_chart, intent_preprocess
          - has_step_error (bool)
          - failure_reason (str, 短描述)
        **不含完整 messages / response**(那要 caller 用 trace_id 二次撈)
    """
    if db is None:
        raise ValueError("db is required")

    collection_name = getattr(config, "TASK_TRACES_COLLECTION", "task_traces")
    coll = db[collection_name]

    # 時間窗口
    since = datetime.now(timezone.utc) - timedelta(days=since_days)

    # OR 組合查詢條件
    or_clauses: list[dict] = []
    if statuses:
        or_clauses.append({"status": {"$in": list(statuses)}})
    if include_step_errors:
        # 任一 step 有 error
        or_clauses.append({"steps": {"$elemMatch": {"error": {"$ne": None}}}})
    if include_manual_flag:
        or_clauses.append({"needs_review": True})

    if not or_clauses:
        return []  # 全部都 disable 的話就空 list

    query = {
        "started_at": {"$gte": since},
        "$or": or_clauses,
    }

    # 只撈摘要欄位,避免一次拉整 doc(可能含 100KB+ 的 messages)
    projection = {
        "_id": 0,
        "trace_id": 1,
        "query": 1,
        "domain": 1,
        "status": 1,
        "started_at": 1,
        "total_wall_s": 1,
        "intent_chart": 1,
        "intent_preprocess": 1,
        "error": 1,
        # 只撈 steps 內的 phase + error(避免拉整個 messages payload)
        "steps.step_id": 1,
        "steps.phase": 1,
        "steps.kind": 1,
        "steps.elapsed_s": 1,
        "steps.error": 1,
        "needs_review": 1,
    }

    cursor = (coll.find(query, projection)
                  .sort("started_at", -1)
                  .limit(limit))

    out = []
    for doc in cursor:
        out.append(_summarize_trace(doc))
    return out


# ============================================================
# Helper:把 raw trace doc 摘要成精簡 dict
# ============================================================
def _summarize_trace(doc: dict) -> dict:
    """從 raw trace doc 抽出 caller 需要的 summary 欄位。"""
    steps = doc.get("steps") or []
    error_steps = [s for s in steps if s.get("error")]

    # 失敗原因摘要:優先用 trace.error;其次第一個 step error
    failure_reason = doc.get("error") or ""
    if not failure_reason and error_steps:
        first_err = error_steps[0]
        failure_reason = (
            f"step #{first_err.get('step_id')} "
            f"({first_err.get('phase', '?')}): "
            f"{(first_err.get('error') or '')[:200]}"
        )

    return {
        "trace_id": doc.get("trace_id"),
        "query": doc.get("query", ""),
        "domain": doc.get("domain", ""),
        "status": doc.get("status"),
        "started_at": doc.get("started_at"),
        "total_wall_s": doc.get("total_wall_s"),
        "intent_chart": doc.get("intent_chart"),
        "intent_preprocess": doc.get("intent_preprocess"),
        "has_step_error": len(error_steps) > 0,
        "error_step_count": len(error_steps),
        "failure_reason": failure_reason[:500],  # cap 長度
        "needs_review": bool(doc.get("needs_review", False)),
    }


# ============================================================
# 另一個 helper:從 trace_id 撈完整 trace(observation extractor 會用到)
# ============================================================
def get_trace_by_id(db, trace_id: str) -> dict | None:
    """撈完整 trace doc(含所有 LLM messages + response),給下游 extractor 用。"""
    if db is None:
        return None
    collection_name = getattr(config, "TASK_TRACES_COLLECTION", "task_traces")
    return db[collection_name].find_one({"trace_id": trace_id})


# ============================================================
# CLI(預覽用)
# ============================================================
def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="Preview failed traces eligible for observation extraction"
    )
    parser.add_argument("--days", type=int, default=7,
                        help="Window in days (default 7)")
    parser.add_argument("--limit", type=int, default=20,
                        help="Max traces to show")
    parser.add_argument("--status", default="failed,refused",
                        help="Comma-separated status filter")
    args = parser.parse_args()

    try:
        from pymongo import MongoClient
        client = MongoClient(
            config.MONGO_URI,
            serverSelectionTimeoutMS=config.MONGO_SERVER_SELECTION_TIMEOUT_MS,
        )
        client.admin.command("ping")
        db = client[config.MONGO_DB]
    except Exception as e:
        print(f"❌ MongoDB 連線失敗:{e}", file=sys.stderr)
        return 1

    statuses = tuple(s.strip() for s in args.status.split(","))
    traces = get_failed_traces(
        db, since_days=args.days, statuses=statuses, limit=args.limit
    )

    print(f"\nFound {len(traces)} eligible trace(s) in last {args.days} days:\n")
    if not traces:
        print("  (empty — no failed/refused trace, or no task_traces 集合)")
        return 0

    print(f"  {'trace_id':<10s} {'status':<10s} {'domain':<10s} "
          f"{'wall(s)':>8s}  query")
    print(f"  {'-' * 10} {'-' * 10} {'-' * 10} {'-' * 8}  {'-' * 40}")
    for t in traces:
        print(
            f"  {(t.get('trace_id') or '?')[:8]:<10s} "
            f"{(t.get('status') or '?'):<10s} "
            f"{(t.get('domain') or '?'):<10s} "
            f"{(t.get('total_wall_s') or 0):>8.1f}  "
            f"{(t.get('query') or '')[:60]}"
        )
        if t.get("failure_reason"):
            print(f"     ↳ {t['failure_reason'][:100]}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
