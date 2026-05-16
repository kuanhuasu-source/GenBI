"""
learning/dashboard_metrics.py — Week 6 D2 (v0.9.0)

Self-Learning dashboard 的指標計算 — 全部 pure Python read-only,給:
  - Streamlit `pages/06_learning_review.py` 渲染
  - CLI ad-hoc 查
  - 未來 API 端點

對齊 spec §25 Learning Dashboard Metrics:

  Operational:
    - observations_created
    - accepted_count / rejected_count
    - active_instincts

  Quality:
    - retry_rate(task_trace 內 attempt > 1 的比例)
    - fallback_rate(phaseC_fallback_used 的比例)
    - benchmark_pass_rate(最新 baseline / test_run)

  Impact:
    - approved_candidates
    - precision_improvement(預留欄位,需要更長 history 才算)
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import config

logger = logging.getLogger(__name__)


# ============================================================
# Operational metrics
# ============================================================
def operational_metrics(db, *, window_days: int | None = None) -> dict:
    """observation / instinct 計數。

    Args:
        window_days: 若給,只算這時間窗內;None 則 all-time。
    """
    if db is None:
        return {}
    since_filter: dict = {}
    if window_days:
        since = datetime.now(timezone.utc) - timedelta(days=window_days)
        since_filter = {"created_at": {"$gte": since}}

    obs = db["learning_observations"]
    inst = db["learning_instincts"]

    def _safe_count(coll, q):
        try:
            return coll.count_documents(q)
        except Exception:
            return 0

    return {
        "observations_total":      _safe_count(obs, dict(since_filter)),
        "observations_candidate":  _safe_count(obs, {**since_filter, "status": "candidate"}),
        "observations_verified":   _safe_count(obs, {**since_filter, "status": "verified"}),
        "observations_rejected":   _safe_count(obs, {**since_filter, "status": "rejected"}),

        "instincts_total":         _safe_count(inst, {}),
        "instincts_active":        _safe_count(inst, {"status": "active"}),
        "instincts_candidate":     _safe_count(inst, {"status": "candidate"}),
        "instincts_deprecated":    _safe_count(inst, {"status": "deprecated"}),

        "instincts_consolidated":  _safe_count(
            inst, {"source": "consolidated", "status": {"$ne": "deprecated"}}
        ),
        "instincts_seed":          _safe_count(inst, {"source": "historical_seed"}),
    }


# ============================================================
# Quality metrics(從 task_traces / test_runs 算)
# ============================================================
def quality_metrics(db, *, window_days: int = 7) -> dict:
    """retry rate / fallback rate / benchmark pass rate。"""
    if db is None:
        return {}

    since = datetime.now(timezone.utc) - timedelta(days=window_days)
    traces_coll_name = getattr(config, "TASK_TRACES_COLLECTION", "task_traces")
    runs_coll_name = getattr(config, "TEST_RUNS_COLLECTION", "test_runs")

    out: dict = {
        "window_days": window_days,
        "total_traces": 0,
        "traces_with_retry": 0,
        "retry_rate": None,
        "traces_with_fallback": 0,
        "fallback_rate": None,
        "latest_baseline_pass_rate": None,
        "latest_baseline_run_id": None,
    }

    # --- task_traces ---
    try:
        traces = list(db[traces_coll_name].find({"started_at": {"$gte": since}}))
        out["total_traces"] = len(traces)

        retry_n = 0
        fallback_n = 0
        for t in traces:
            steps = t.get("steps") or []
            # retry:phase_b / phase_c retry log 非空,或 step.meta.attempts > 1
            has_retry = False
            has_fallback = False
            for s in steps:
                meta = s.get("meta") or {}
                if meta.get("attempts") and meta["attempts"] > 1:
                    has_retry = True
                if meta.get("use_table_fallback") or "fallback" in str(meta).lower():
                    has_fallback = True
            if t.get("status") == "phaseC_fallback_used":
                has_fallback = True
            if has_retry:
                retry_n += 1
            if has_fallback:
                fallback_n += 1
        out["traces_with_retry"] = retry_n
        out["traces_with_fallback"] = fallback_n
        if len(traces) > 0:
            out["retry_rate"] = round(retry_n / len(traces), 4)
            out["fallback_rate"] = round(fallback_n / len(traces), 4)
    except Exception as e:
        logger.warning(f"quality_metrics traces query failed: {e}")

    # --- test_runs latest baseline ---
    try:
        baseline = (
            db[runs_coll_name]
            .find_one({"is_baseline": True}, sort=[("started_at", -1)])
        )
        if baseline:
            summary = baseline.get("summary", {}) or {}
            total = summary.get("total_cases", 0) or 0
            passed = summary.get("passed", 0) or 0
            refused = summary.get("refusal_detected", 0) or 0
            if total > 0:
                out["latest_baseline_pass_rate"] = round((passed + refused) / total, 4)
            out["latest_baseline_run_id"] = baseline.get("run_id")
    except Exception as e:
        logger.warning(f"quality_metrics baseline query failed: {e}")

    return out


# ============================================================
# Impact metrics
# ============================================================
def impact_metrics(db) -> dict:
    """approved candidates / promotion 統計。"""
    if db is None:
        return {}

    cands = db["prompt_rule_candidates"]

    def _safe_count(q):
        try:
            return cands.count_documents(q)
        except Exception:
            return 0

    return {
        "candidates_total":     _safe_count({}),
        "candidates_pending":   _safe_count({"status": "candidate"}),
        "candidates_testing":   _safe_count({"status": "testing"}),
        "candidates_approved":  _safe_count({"status": "approved"}),
        "candidates_rejected":  _safe_count({"status": "rejected"}),
    }


# ============================================================
# Needs review (contradictions notifications)
# ============================================================
def needs_review_queue(db, *, limit: int = 20) -> list[dict]:
    """從 learning_jobs 撈 job_type='contradiction_review' + status='needs_review'。"""
    if db is None:
        return []
    try:
        return list(
            db["learning_jobs"]
            .find({
                "job_type": "contradiction_review",
                "status": "needs_review",
            })
            .sort("started_at", -1)
            .limit(limit)
        )
    except Exception as e:
        logger.warning(f"needs_review_queue failed: {e}")
        return []


# ============================================================
# 一次性 snapshot(供 page 用)
# ============================================================
def full_snapshot(db, *, window_days: int = 7) -> dict:
    """Page 渲染用的 one-shot 拉取。"""
    return {
        "generated_at": datetime.now(timezone.utc),
        "window_days": window_days,
        "operational": operational_metrics(db, window_days=None),
        "operational_window": operational_metrics(db, window_days=window_days),
        "quality": quality_metrics(db, window_days=window_days),
        "impact": impact_metrics(db),
        "needs_review": needs_review_queue(db, limit=10),
    }


# ============================================================
# CLI
# ============================================================
def main() -> int:
    import argparse
    import json
    parser = argparse.ArgumentParser(
        description="Dashboard metrics snapshot for self-learning system"
    )
    parser.add_argument("--days", type=int, default=7,
                        help="Quality metrics window (default 7)")
    parser.add_argument("--json", action="store_true",
                        help="Output JSON instead of human-readable")
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

    snap = full_snapshot(db, window_days=args.days)

    if args.json:
        print(json.dumps(snap, default=str, indent=2, ensure_ascii=False))
        return 0

    print("═" * 70)
    print(f"  Self-Learning Dashboard · {snap['generated_at']}")
    print("═" * 70)

    op = snap["operational"]
    print("\n📊 Operational (all-time):")
    print(f"  Observations:  total={op.get('observations_total')}, "
          f"candidate={op.get('observations_candidate')}, "
          f"verified={op.get('observations_verified')}, "
          f"rejected={op.get('observations_rejected')}")
    print(f"  Instincts:     total={op.get('instincts_total')}, "
          f"active={op.get('instincts_active')}, "
          f"candidate={op.get('instincts_candidate')}, "
          f"deprecated={op.get('instincts_deprecated')}")
    print(f"                 seed={op.get('instincts_seed')}, "
          f"consolidated={op.get('instincts_consolidated')}")

    q = snap["quality"]
    print(f"\n⚙️  Quality (last {q.get('window_days')}d):")
    print(f"  Total traces:  {q.get('total_traces')}")
    print(f"  Retry rate:    {q.get('retry_rate')}  "
          f"({q.get('traces_with_retry')} / {q.get('total_traces')})")
    print(f"  Fallback rate: {q.get('fallback_rate')}  "
          f"({q.get('traces_with_fallback')} / {q.get('total_traces')})")
    print(f"  Latest baseline pass rate: {q.get('latest_baseline_pass_rate')} "
          f"(run_id={q.get('latest_baseline_run_id')})")

    im = snap["impact"]
    print(f"\n🎯 Impact:")
    print(f"  Candidates:    pending={im.get('candidates_pending')}, "
          f"testing={im.get('candidates_testing')}, "
          f"approved={im.get('candidates_approved')}, "
          f"rejected={im.get('candidates_rejected')}")

    nr = snap["needs_review"]
    print(f"\n⚠️  Needs Review (top {len(nr)} contradictions):")
    for n in nr[:5]:
        print(f"  - obs={n.get('linked_observation_id')} "
              f"vs instinct={n.get('linked_instinct_id')} "
              f"(conf_after={n.get('confidence_after')})")
        print(f"      reason: {(n.get('reason') or '')[:90]}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
