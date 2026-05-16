"""
learning/resolution_detector.py — Week 5 D1 (v0.8.10)

掃 `task_traces`:找「同 query_hash 先 failed 後來 completed 且時差 < 30 天」的
配對,**自動產生 regression test_case** 寫進 `test_cases` collection。

對齊 GenBI_v1.3_Self_Learning_MVP_Implementation_Spec.md §17 + §17.5。

# 設計重點
- **MVP 不檢查 prompt 版本變更**(目前 trace 沒記錄 prompt version,
  spec §17.5 條件 4 暫時用「沒既有 regression case」近似)
- **Idempotent**:同 query_hash 已建過 auto-regression case 就 skip
- **case_id 用 `AUTO-NNNNN`**(避開 manual case 的 `01/02/STK-XX/Txx`)
- **type='regression'**(新加 type,test_runner 可選擇性 include)
- **type-driven defaults**:expected_q_cols_all / echarts_required_keys 從
  resolved trace 的 Phase B / Phase C 實際輸出 capture
"""

from __future__ import annotations

import hashlib
import logging
import sys
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import config

logger = logging.getLogger(__name__)


# ============================================================
# Constants
# ============================================================
_RESOLUTION_WINDOW_DAYS = 30          # spec §17.5 條件 3
_AUTO_CASE_PREFIX = "AUTO"
_DEFAULT_ECHARTS_KEYS = ["title", "xAxis", "yAxis", "series"]


# ============================================================
# Helpers
# ============================================================
def _query_hash(query: str) -> str:
    """sha256(normalized query) — 跟 observation_extractor 用同樣 hash 函式。"""
    return hashlib.sha256((query or "").strip().encode("utf-8")).hexdigest()


def _next_auto_case_id(coll) -> str:
    """生成下一個 AUTO-NNNNN case_id(從現有 AUTO- 數量 +1)。"""
    try:
        n = coll.count_documents({"case_id": {"$regex": f"^{_AUTO_CASE_PREFIX}-"}})
    except Exception:
        n = 0
    return f"{_AUTO_CASE_PREFIX}-{n + 1:05d}"


def _build_regression_case(
    failed_trace: dict, resolved_trace: dict, case_id: str,
) -> dict:
    """從 (failed, resolved) trace pair 組合一個 regression test_case。

    expected_q_cols_all / echarts_required_keys 從 resolved trace 的實際輸出 capture
    (因為「成功跑通」就是 ground truth)。
    """
    query = resolved_trace.get("query", "")
    domain = resolved_trace.get("domain", "tflex")
    now = datetime.now(timezone.utc)

    # 從 resolved trace 抽 Q.cols(若存在 trace.steps 內的 preprocess 階段)
    q_cols: list[str] = []
    for step in (resolved_trace.get("steps") or []):
        meta = step.get("meta") or {}
        if "Q_cols" in meta:
            q_cols = list(meta["Q_cols"])
            break

    # 至少要求 query 內看起來像 dimension 的字眼進到 cols
    expected_cols: list[str] = []
    if q_cols:
        # 簡單啟發式:把所有 Q.cols 都放進 expected,讓 test runner 至少驗 schema
        expected_cols = q_cols[:5]

    return {
        "case_id": case_id,
        "domain": domain,
        "type": "regression",          # 新 type,跟 happy_path / refusal 分開
        "name": f"Auto-regression from resolved failure",
        "query": query,
        "expected_chart": "any",       # 自動生成的不指定具體 chart type
        "expected_q_cols_all": expected_cols,
        "echarts_required_keys": _DEFAULT_ECHARTS_KEYS,
        "is_active": True,

        # auto-resolution meta — 給 admin 看歷史 / 反查 trace
        "source": "auto_resolution",
        "auto_meta": {
            "failed_trace_id": failed_trace.get("trace_id"),
            "resolved_trace_id": resolved_trace.get("trace_id"),
            "failed_at": failed_trace.get("started_at"),
            "resolved_at": resolved_trace.get("started_at"),
            "elapsed_days": _days_between(
                failed_trace.get("started_at"),
                resolved_trace.get("started_at"),
            ),
            "query_hash": _query_hash(query),
        },
        "created_at": now,
        "updated_at": now,
    }


def _days_between(start: Any, end: Any) -> float | None:
    """安全算 datetime 差(回小數天)。任一無效回 None。"""
    try:
        if not isinstance(start, datetime) or not isinstance(end, datetime):
            return None
        return round((end - start).total_seconds() / 86400.0, 2)
    except Exception:
        return None


# ============================================================
# Public:detect_resolutions(把 resolved failure 寫成 regression test_case)
# ============================================================
def detect_resolutions(
    db,
    *,
    window_days: int = _RESOLUTION_WINDOW_DAYS,
    limit: int = 100,
    domain: str | None = None,
    dry_run: bool = False,
    verbose: bool = True,
) -> dict:
    """
    掃 task_traces 找 resolved failures,寫 regression test_case。

    Algorithm(spec §17.5):
      For each query_hash:
        if failed run exists
        and later completed run exists
        and 時差 < window_days
        and 沒既有 regression case
            → 寫 regression test_case

    Args:
        db: pymongo Database
        window_days: 視為 resolution 的時間窗(default 30)
        limit: 最多處理幾個 query_hash(cost control)
        domain: 若指定,只看該 domain 的 trace
        dry_run: 跑 logic 但不寫 DB
        verbose: 印 per-pair 結果

    Returns:
        stats dict 含:scanned_traces, resolved_pairs, cases_created,
                       cases_skipped_dup, errors
    """
    if db is None:
        raise ValueError("db is required")

    stats = {
        "run_id": str(uuid.uuid4()),
        "scanned_traces": 0,
        "unique_query_hashes": 0,
        "resolved_pairs": 0,
        "cases_created": 0,
        "cases_skipped_dup": 0,
        "errors": 0,
        "created_case_ids": [],
    }
    job_started = datetime.now(timezone.utc)

    traces_coll_name = getattr(config, "TASK_TRACES_COLLECTION", "task_traces")
    cases_coll_name = getattr(config, "TEST_CASES_COLLECTION", "test_cases")
    jobs_coll_name = "learning_jobs"

    traces_coll = db[traces_coll_name]
    cases_coll = db[cases_coll_name]
    jobs_coll = db[jobs_coll_name]

    # 1. 拿最近 window_days 內的 traces(failed/refused/completed 都要)
    since = datetime.now(timezone.utc) - timedelta(days=window_days)
    query = {"started_at": {"$gte": since}}
    if domain:
        query["domain"] = domain
    try:
        traces = list(traces_coll.find(query).sort("started_at", 1).limit(5000))
    except Exception as e:
        logger.error(f"failed to query task_traces: {e}")
        stats["errors"] += 1
        return stats
    stats["scanned_traces"] = len(traces)
    if verbose:
        print(f"📥 Scanning {len(traces)} traces (window={window_days}d, "
              f"domain={domain or 'all'})")

    # 2. 按 query_hash 分組
    by_hash: dict[str, list[dict]] = {}
    for t in traces:
        qh = _query_hash(t.get("query", ""))
        if not qh:
            continue
        by_hash.setdefault(qh, []).append(t)
    stats["unique_query_hashes"] = len(by_hash)

    if verbose:
        print(f"   → {len(by_hash)} unique query_hash")

    # 3. 對每個 query_hash 找 (failed_before, completed_after) 配對
    pairs_found = 0
    for qh, ts in by_hash.items():
        if pairs_found >= limit:
            break
        # 找最早的 failed/refused
        fails = [t for t in ts if t.get("status") in ("failed", "refused")]
        completes = [t for t in ts if t.get("status") == "completed"]
        if not fails or not completes:
            continue

        # 取最早 failed 跟 之後最早的 completed
        earliest_fail = min(fails, key=lambda x: x.get("started_at") or datetime.min)
        later_completes = [
            c for c in completes
            if (c.get("started_at") or datetime.min)
                > (earliest_fail.get("started_at") or datetime.min)
        ]
        if not later_completes:
            continue
        earliest_resolved = min(
            later_completes, key=lambda x: x.get("started_at") or datetime.min
        )

        # 檢查時間窗
        days = _days_between(
            earliest_fail.get("started_at"),
            earliest_resolved.get("started_at"),
        )
        if days is None or days > window_days:
            continue

        pairs_found += 1
        stats["resolved_pairs"] += 1

        # 4. 檢查既有 regression case(idempotent — 用 query_hash 在 auto_meta 內)
        try:
            existing = cases_coll.find_one({
                "source": "auto_resolution",
                "auto_meta.query_hash": qh,
            })
        except Exception:
            existing = None

        if existing:
            stats["cases_skipped_dup"] += 1
            if verbose:
                print(f"  ⏭️  query_hash={qh[:12]}…  已有 regression case "
                      f"{existing.get('case_id')}")
            continue

        # 5. 建 regression case
        case_id = _next_auto_case_id(cases_coll)
        doc = _build_regression_case(earliest_fail, earliest_resolved, case_id)

        if dry_run:
            stats["cases_created"] += 1
            stats["created_case_ids"].append(case_id)
            if verbose:
                print(f"  [dry-run] would create {case_id} "
                      f"(resolved in {days}d): {doc['query'][:50]}…")
            continue

        try:
            cases_coll.insert_one(doc)
            stats["cases_created"] += 1
            stats["created_case_ids"].append(case_id)
            if verbose:
                print(f"  ✅ {case_id} created (resolved in {days}d, "
                      f"domain={doc['domain']})")
                print(f"     query: {doc['query'][:70]}…")
        except Exception as e:
            stats["errors"] += 1
            if verbose:
                print(f"  ❌ insert failed: {type(e).__name__}: {str(e)[:200]}")

    # 6. 寫 learning_jobs record
    if not dry_run:
        try:
            jobs_coll.insert_one({
                "job_id": f"JOB-RESOL-{int(time.time())}",
                "job_type": "resolution_detection",
                "status": "completed",
                "started_at": job_started,
                "completed_at": datetime.now(timezone.utc),
                "input_count": stats["scanned_traces"],
                "output_count": stats["cases_created"],
                "resolved_pairs": stats["resolved_pairs"],
                "skipped_dup": stats["cases_skipped_dup"],
                "errors": stats["errors"],
                "run_id": stats["run_id"],
                "params": {
                    "window_days": window_days,
                    "limit": limit,
                    "domain": domain,
                },
            })
        except Exception as e:
            logger.warning(f"failed to write learning_jobs record: {e}")

    return stats


# ============================================================
# CLI
# ============================================================
def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="Detect resolved failures and auto-generate regression test_cases"
    )
    parser.add_argument("--days", type=int, default=_RESOLUTION_WINDOW_DAYS,
                        help=f"Resolution window in days (default {_RESOLUTION_WINDOW_DAYS})")
    parser.add_argument("--limit", type=int, default=50,
                        help="Max query_hash pairs to process")
    parser.add_argument("--domain", default=None,
                        help="Filter to specific domain (default: all)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run logic but don't write to DB")
    args = parser.parse_args()

    print("═" * 70)
    print("  GenBI Self-Learning · Resolution Detector")
    print(f"  Spec: §17 + §17.5")
    print("═" * 70)

    try:
        from pymongo import MongoClient
        client = MongoClient(
            config.MONGO_URI,
            serverSelectionTimeoutMS=config.MONGO_SERVER_SELECTION_TIMEOUT_MS,
        )
        client.admin.command("ping")
        db = client[config.MONGO_DB]
        print(f"\n✅ MongoDB connected: {config.MONGO_DB}")
    except Exception as e:
        print(f"\n❌ MongoDB 連線失敗:{e}", file=sys.stderr)
        return 1

    mode = " (DRY RUN)" if args.dry_run else ""
    print(f"\n🚀 Detecting resolutions (window={args.days}d, "
          f"limit={args.limit}, domain={args.domain or 'all'}){mode}\n")

    stats = detect_resolutions(
        db,
        window_days=args.days,
        limit=args.limit,
        domain=args.domain,
        dry_run=args.dry_run,
        verbose=True,
    )

    print()
    print("─" * 70)
    print(f"  Scanned traces:        {stats['scanned_traces']}")
    print(f"  Unique query_hash:     {stats['unique_query_hashes']}")
    print(f"  Resolved pairs found:  {stats['resolved_pairs']}")
    print(f"  Regression cases created: {stats['cases_created']}")
    print(f"  Skipped (dup):         {stats['cases_skipped_dup']}")
    print(f"  Errors:                {stats['errors']}")
    print("─" * 70)

    if not args.dry_run and stats["cases_created"] > 0:
        print(f"\n📊 Verify:")
        print(f"   mongo {config.MONGO_DB}")
        print(f"   db.test_cases.find({{source:\"auto_resolution\"}}).pretty()")

    return 0


if __name__ == "__main__":
    sys.exit(main())
