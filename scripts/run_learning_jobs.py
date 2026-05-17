"""
scripts/run_learning_jobs.py — v0.9.2 (Spec §16.5)

Nightly cron orchestrator:一次跑完所有 self-learning batch job,
最後印 dashboard snapshot。

對齊 GenBI_v1.3_Self_Learning_MVP_Implementation_Spec.md §16.5
Scheduler Execution Model。

# 推薦部署
```cron
# 每天凌晨 2 點跑
0 2 * * * /usr/bin/python /path/to/GenBI/scripts/run_learning_jobs.py >> /var/log/genbi_learning.log 2>&1
```

或用 systemd timer / Windows Task Scheduler。

# 跑哪些 jobs(順序有 dependency)

1. **observation_extraction**  從新 failed traces 抽 observation
2. **verification**             對 candidate observation 跑 verifier
3. **consolidation**            verified obs → candidate instinct
4. **contradiction_scan**       verified obs vs active instinct 找矛盾
5. **confidence_decay**         dormant instinct 衰減 confidence
6. **resolution_detection**     resolved failure → auto regression test_case
7. **candidate_generation**     active instinct → prompt_rule_candidate
8. **dashboard_snapshot**       印 metric 快照(也適合給監控吃)

# Flags
  --dry-run             所有 job 改 dry-run 模式
  --skip <job_name>     跳過指定 job(可多次)
  --extraction-limit N  observation extraction 最多處理 N 個 trace
  --verifier-limit N    verifier 最多處理 N 個 obs
  --window-days N       failure / resolution window(default 7 / 30)
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

import config

logger = logging.getLogger(__name__)


# ============================================================
# Job registry — name → (module, function_name, description, kwargs builder)
# ============================================================
# 每個 job 要回傳一個 stats dict;失敗時 raise(orchestrator catch)
JOB_ORDER = (
    "observation_extraction",
    "verification",
    "consolidation",
    "contradiction_scan",
    "confidence_decay",
    "resolution_detection",
    "candidate_generation",
)


def _run_observation_extraction(db, llm, args) -> dict:
    from learning.observation_extractor import run_observation_extraction
    return run_observation_extraction(
        db, llm,
        since_days=args.window_days,
        limit=args.extraction_limit,
        dry_run=args.dry_run, verbose=True,
    )


def _run_verification(db, llm, args) -> dict:
    from learning.verifier import run_verification
    return run_verification(
        db, llm,
        limit=args.verifier_limit,
        dry_run=args.dry_run, verbose=True,
    )


def _run_consolidation(db, llm, args) -> dict:
    from learning.instinct_consolidator import consolidate_instincts
    return consolidate_instincts(
        db, domain=args.domain or "tflex",
        dry_run=args.dry_run, verbose=True,
    )


def _run_contradiction_scan(db, llm, args) -> dict:
    from learning.instinct_consolidator import detect_contradictions
    return detect_contradictions(
        db, dry_run=args.dry_run, verbose=True,
    )


def _run_confidence_decay(db, llm, args) -> dict:
    from learning.instinct_consolidator import apply_confidence_decay
    return apply_confidence_decay(
        db, dormancy_days=args.decay_dormancy_days,
        dry_run=args.dry_run, verbose=True,
    )


def _run_resolution_detection(db, llm, args) -> dict:
    from learning.resolution_detector import detect_resolutions
    return detect_resolutions(
        db, window_days=args.resolution_window_days,
        domain=args.domain,
        dry_run=args.dry_run, verbose=True,
    )


def _run_candidate_generation(db, llm, args) -> dict:
    from learning.candidate_generator import generate_candidates
    return generate_candidates(
        db, dry_run=args.dry_run, verbose=True,
    )


JOB_RUNNERS = {
    "observation_extraction": _run_observation_extraction,
    "verification":            _run_verification,
    "consolidation":           _run_consolidation,
    "contradiction_scan":      _run_contradiction_scan,
    "confidence_decay":        _run_confidence_decay,
    "resolution_detection":    _run_resolution_detection,
    "candidate_generation":    _run_candidate_generation,
}


# ============================================================
# Dashboard snapshot at end
# ============================================================
def _print_dashboard_snapshot(db, window_days: int) -> None:
    try:
        from learning.dashboard_metrics import full_snapshot
        snap = full_snapshot(db, window_days=window_days)
    except Exception as e:
        print(f"❌ dashboard snapshot failed: {e}")
        return

    print()
    print("═" * 70)
    print(f"  📊 Dashboard Snapshot · {snap['generated_at']}")
    print("═" * 70)
    op = snap.get("operational", {})
    q  = snap.get("quality", {})
    im = snap.get("impact", {})
    print(f"  Observations:  total={op.get('observations_total')}, "
          f"verified={op.get('observations_verified')}, "
          f"candidate={op.get('observations_candidate')}, "
          f"rejected={op.get('observations_rejected')}")
    print(f"  Instincts:     total={op.get('instincts_total')}, "
          f"active={op.get('instincts_active')}, "
          f"candidate={op.get('instincts_candidate')}, "
          f"deprecated={op.get('instincts_deprecated')}")
    print(f"  Quality:       retry_rate={q.get('retry_rate')}, "
          f"fallback_rate={q.get('fallback_rate')}, "
          f"baseline_pass={q.get('latest_baseline_pass_rate')}")
    print(f"  Candidates:    pending={im.get('candidates_pending')}, "
          f"approved={im.get('candidates_approved')}, "
          f"rejected={im.get('candidates_rejected')}")
    nr = snap.get("needs_review") or []
    if nr:
        print(f"  ⚠️  Needs review: {len(nr)} contradiction(s)")
        for n in nr[:3]:
            print(f"    - obs={n.get('linked_observation_id')} "
                  f"vs instinct={n.get('linked_instinct_id')}")
    print()


# ============================================================
# Main
# ============================================================
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Nightly self-learning jobs orchestrator"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="所有 job dry-run(不寫 DB)")
    parser.add_argument("--skip", action="append", default=[],
                        choices=list(JOB_ORDER),
                        help="跳過某個 job(可指定多次)")
    parser.add_argument("--only", default=None,
                        help="只跑指定 job(逗號分隔,override --skip)")
    parser.add_argument("--domain", default=None,
                        help="filter to specific domain(default: all)")
    parser.add_argument("--window-days", type=int, default=7,
                        help="failure / quality window (default 7)")
    parser.add_argument("--extraction-limit", type=int, default=20,
                        help="observation extraction 上限(default 20,spec §22 daily cap=50)")
    parser.add_argument("--verifier-limit", type=int, default=30,
                        help="verifier 上限(default 30)")
    parser.add_argument("--decay-dormancy-days", type=int, default=90,
                        help="confidence decay 觸發天數(default 90)")
    parser.add_argument("--resolution-window-days", type=int, default=30,
                        help="resolution detection window (default 30)")
    parser.add_argument("--skip-snapshot", action="store_true",
                        help="跳過最後的 dashboard snapshot")
    args = parser.parse_args()

    if args.only:
        wanted = {x.strip() for x in args.only.split(",") if x.strip()}
        skip_set = set(JOB_ORDER) - wanted
    else:
        skip_set = set(args.skip)

    print("═" * 70)
    print(f"  GenBI Self-Learning · Nightly Jobs Orchestrator")
    print(f"  Started: {datetime.now(timezone.utc).isoformat()}")
    if args.dry_run:
        print(f"  🟡 DRY RUN — 所有 job 不寫 DB")
    if skip_set:
        print(f"  ⏭️  Skipping: {sorted(skip_set)}")
    print("═" * 70)

    # ── 連線 ──
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

    # LLM:extraction / verification 需要;其他不需要
    llm = None
    if not ({"observation_extraction", "verification"} <= skip_set):
        try:
            from llm_service import LLMService
            llm = LLMService(**config.llm_service_kwargs())
            print(f"✅ LLMService initialized: model={config.LLM_MODEL}")
        except Exception as e:
            print(f"❌ LLMService 初始化失敗(extraction/verification will skip):{e}",
                  file=sys.stderr)
            skip_set.update({"observation_extraction", "verification"})

    # ── 跑每個 job ──
    summary: list[dict] = []
    overall_t0 = time.time()
    for job_name in JOB_ORDER:
        if job_name in skip_set:
            print(f"\n⏭️  skip {job_name}")
            summary.append({"job": job_name, "status": "skipped"})
            continue

        print(f"\n{'━' * 70}")
        print(f"🚀 [{job_name}]")
        print(f"{'━' * 70}")
        t0 = time.time()
        try:
            runner = JOB_RUNNERS[job_name]
            stats = runner(db, llm, args)
            elapsed = round(time.time() - t0, 1)
            summary.append({
                "job": job_name, "status": "ok",
                "elapsed_s": elapsed, "stats": stats,
            })
            print(f"\n✅ {job_name} done in {elapsed}s")
        except Exception as e:
            elapsed = round(time.time() - t0, 1)
            tb = traceback.format_exc(limit=3)
            print(f"\n❌ {job_name} failed: {type(e).__name__}: {e}")
            print(tb)
            summary.append({
                "job": job_name, "status": "error",
                "elapsed_s": elapsed, "error": str(e),
            })
            # 繼續跑下一個 job(orchestrator 不因一個 job 失敗就 abort)

    # ── final summary ──
    total_elapsed = round(time.time() - overall_t0, 1)
    print()
    print("═" * 70)
    print(f"  📋 Run Summary · total {total_elapsed}s")
    print("═" * 70)
    for s in summary:
        flag = {"ok": "✅", "skipped": "⏭️ ", "error": "❌"}.get(s["status"], "?")
        elapsed_str = f"{s.get('elapsed_s', 0):>6.1f}s" if "elapsed_s" in s else "      -"
        print(f"  {flag} {s['job']:<24s} {elapsed_str}  {s['status']}")
    print("═" * 70)

    # ── dashboard snapshot ──
    if not args.skip_snapshot:
        _print_dashboard_snapshot(db, window_days=args.window_days)

    # 結束碼:任何 job error → 1(讓 cron 知道)
    has_error = any(s["status"] == "error" for s in summary)
    return 1 if has_error else 0


if __name__ == "__main__":
    sys.exit(main())
