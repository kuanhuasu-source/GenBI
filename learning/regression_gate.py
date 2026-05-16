"""
learning/regression_gate.py — Week 5 D3 (v0.8.10)

比較 baseline test_run 與 candidate test_run,gate 判斷是否可以 promote
`prompt_rule_candidate.status` candidate / testing → approved。

對齊 GenBI_v1.3_Self_Learning_MVP_Implementation_Spec.md §19 + §19.5。

# Gate 條件(spec §19)
1. **No critical regressions** — case 沒 pass→fail
2. **Pass rate not lower** — candidate.passed >= baseline.passed
3. **Latency increase < 10%** — (candidate.total_wall_s - baseline.total_wall_s) / baseline < 0.10
4. **Cost increase < 15%** — (candidate.total_tokens - baseline.total_tokens) / baseline < 0.15

# 設計重點
- Pure logic — 不寫 DB(讓 caller 決定怎麼 promote)
- 接受 baseline_run / candidate_run dicts 直接比較,也提供 helper 從 DB
  抓 baseline + 最新 candidate run
- 條件 1 用 case-by-case status diff(precise);條件 2-4 用 summary 比例
- 詳細回 verdict dict,含每條 gate 的 pass/fail + 原因
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import config

logger = logging.getLogger(__name__)


# ============================================================
# Thresholds(spec §19 + §19.5)
# ============================================================
_LATENCY_INCREASE_THRESHOLD = 0.10    # 10%
_COST_INCREASE_THRESHOLD = 0.15       # 15%
_PASSING_STATUSES = ("pass", "refusal_detected")


# ============================================================
# Core comparison logic
# ============================================================
def _passing_count(case_results: list[dict]) -> int:
    return sum(
        1 for c in (case_results or [])
        if c.get("status") in _PASSING_STATUSES
    )


def _case_status_map(case_results: list[dict]) -> dict[str, str]:
    return {c.get("id", "?"): c.get("status", "?") for c in (case_results or [])}


def _detect_critical_regressions(
    baseline_cases: list[dict],
    candidate_cases: list[dict],
) -> list[dict]:
    """
    抓 case-level regression:baseline 通過但 candidate 沒通過。
    回 list of {case_id, baseline_status, candidate_status}。
    """
    base_map = _case_status_map(baseline_cases)
    cand_map = _case_status_map(candidate_cases)
    regressions: list[dict] = []
    for cid, base_st in base_map.items():
        cand_st = cand_map.get(cid, "missing")
        if base_st in _PASSING_STATUSES and cand_st not in _PASSING_STATUSES:
            regressions.append({
                "case_id": cid,
                "baseline_status": base_st,
                "candidate_status": cand_st,
            })
    return regressions


def _safe_ratio_delta(old: float, new: float) -> float | None:
    """(new - old) / old;old <= 0 回 None(避免 div by zero)。"""
    if old is None or new is None:
        return None
    try:
        old_f, new_f = float(old), float(new)
    except Exception:
        return None
    if old_f <= 0:
        return None
    return (new_f - old_f) / old_f


# ============================================================
# 對外:compare_runs(輸入兩個 test_run dict)
# ============================================================
def compare_runs(
    baseline_run: dict,
    candidate_run: dict,
    *,
    latency_threshold: float = _LATENCY_INCREASE_THRESHOLD,
    cost_threshold: float = _COST_INCREASE_THRESHOLD,
) -> dict:
    """
    比較兩個 test_run dict,回 verdict。

    Args:
        baseline_run: 含 summary + case_results
        candidate_run: 同上(套用 candidate 之後的 run)
        latency_threshold: latency 增加比例上限(default 0.10)
        cost_threshold: cost 增加比例上限(default 0.15)

    Returns:
        verdict dict:
          {
            "verdict": "pass" | "fail",
            "gate_1_no_critical_regression": bool,
            "gate_2_pass_rate_not_lower": bool,
            "gate_3_latency_ok": bool,
            "gate_4_cost_ok": bool,
            "critical_regressions": [...],
            "metrics": {baseline_passed, candidate_passed,
                        latency_delta, cost_delta, ...},
            "reasons": [...],
          }
    """
    base_summary = baseline_run.get("summary", {}) or {}
    cand_summary = candidate_run.get("summary", {}) or {}
    base_cases = baseline_run.get("case_results", []) or []
    cand_cases = candidate_run.get("case_results", []) or []

    # Gate 1: critical regressions
    regressions = _detect_critical_regressions(base_cases, cand_cases)
    gate1 = (len(regressions) == 0)

    # Gate 2: pass rate not lower
    base_passed = _passing_count(base_cases) or base_summary.get("passed", 0)
    cand_passed = _passing_count(cand_cases) or cand_summary.get("passed", 0)
    gate2 = (cand_passed >= base_passed)

    # Gate 3: latency increase < 10%
    base_wall = base_summary.get("total_wall_s") or baseline_run.get("total_wall_s")
    cand_wall = cand_summary.get("total_wall_s") or candidate_run.get("total_wall_s")
    latency_delta = _safe_ratio_delta(base_wall, cand_wall)
    gate3 = (latency_delta is None or latency_delta < latency_threshold)

    # Gate 4: cost increase < 15%
    base_tok = base_summary.get("total_tokens")
    cand_tok = cand_summary.get("total_tokens")
    cost_delta = _safe_ratio_delta(base_tok, cand_tok)
    gate4 = (cost_delta is None or cost_delta < cost_threshold)

    all_pass = gate1 and gate2 and gate3 and gate4

    reasons: list[str] = []
    if not gate1:
        reasons.append(
            f"critical regression(s) in {len(regressions)} case(s): "
            + ", ".join(r["case_id"] for r in regressions[:5])
        )
    if not gate2:
        reasons.append(f"pass count dropped: {base_passed} → {cand_passed}")
    if not gate3:
        reasons.append(
            f"latency increase {latency_delta:.1%} >= "
            f"threshold {latency_threshold:.0%}"
        )
    if not gate4:
        reasons.append(
            f"cost increase {cost_delta:.1%} >= "
            f"threshold {cost_threshold:.0%}"
        )

    return {
        "verdict": "pass" if all_pass else "fail",
        "gate_1_no_critical_regression": gate1,
        "gate_2_pass_rate_not_lower": gate2,
        "gate_3_latency_ok": gate3,
        "gate_4_cost_ok": gate4,
        "critical_regressions": regressions,
        "metrics": {
            "baseline_passed": base_passed,
            "candidate_passed": cand_passed,
            "baseline_wall_s": base_wall,
            "candidate_wall_s": cand_wall,
            "latency_delta": latency_delta,
            "baseline_tokens": base_tok,
            "candidate_tokens": cand_tok,
            "cost_delta": cost_delta,
        },
        "reasons": reasons,
        "thresholds": {
            "latency": latency_threshold,
            "cost": cost_threshold,
        },
    }


# ============================================================
# DB helpers
# ============================================================
def _fetch_baseline_run(db, domain: str | None) -> dict | None:
    """從 test_runs 拿 is_baseline=True 的 run(若有 domain,filter)。"""
    if db is None:
        return None
    coll = db[getattr(config, "TEST_RUNS_COLLECTION", "test_runs")]
    query: dict = {"is_baseline": True}
    if domain:
        query["domain"] = domain
    try:
        return coll.find_one(query, sort=[("started_at", -1)])
    except Exception as e:
        logger.warning(f"failed to fetch baseline: {e}")
        return None


def _fetch_run_by_id(db, run_id: str) -> dict | None:
    if db is None:
        return None
    coll = db[getattr(config, "TEST_RUNS_COLLECTION", "test_runs")]
    try:
        return coll.find_one({"run_id": run_id})
    except Exception as e:
        logger.warning(f"failed to fetch run {run_id}: {e}")
        return None


# ============================================================
# 對外:run_gate(從 DB 比對一個 candidate run)
# ============================================================
def run_gate(
    db,
    *,
    candidate_run_id: str,
    candidate_id: str | None = None,
    domain: str | None = None,
    latency_threshold: float = _LATENCY_INCREASE_THRESHOLD,
    cost_threshold: float = _COST_INCREASE_THRESHOLD,
    dry_run: bool = False,
    verbose: bool = True,
) -> dict:
    """
    從 DB 抓 baseline + 指定 candidate run,跑 gate,並(非 dry-run 時)
    更新 prompt_rule_candidate.status。

    Returns:
        {
          "verdict": "pass"|"fail"|"error",
          "details": {compare_runs 完整 verdict},
          "candidate_id": str | None,
          "promoted": bool,
        }
    """
    if db is None:
        return {"verdict": "error", "details": {"error": "no_db"}}

    baseline = _fetch_baseline_run(db, domain)
    if not baseline:
        return {"verdict": "error",
                "details": {"error": "no_baseline_for_domain", "domain": domain}}

    candidate = _fetch_run_by_id(db, candidate_run_id)
    if not candidate:
        return {"verdict": "error",
                "details": {"error": "candidate_run_not_found",
                            "run_id": candidate_run_id}}

    details = compare_runs(
        baseline, candidate,
        latency_threshold=latency_threshold,
        cost_threshold=cost_threshold,
    )
    verdict = details["verdict"]
    promoted = False

    if verbose:
        print(f"\n📊 Gate verdict: {verdict.upper()}")
        m = details["metrics"]
        print(f"   baseline run_id:  {baseline.get('run_id')}")
        print(f"   candidate run_id: {candidate.get('run_id')}")
        print(f"   passed:    {m['baseline_passed']} → {m['candidate_passed']}")
        if m["latency_delta"] is not None:
            print(f"   wall_s:    {m['baseline_wall_s']} → "
                  f"{m['candidate_wall_s']}  ({m['latency_delta']:+.1%})")
        if m["cost_delta"] is not None:
            print(f"   tokens:    {m['baseline_tokens']} → "
                  f"{m['candidate_tokens']}  ({m['cost_delta']:+.1%})")
        for r in details["reasons"]:
            print(f"   ⚠️  {r}")
        for reg in details["critical_regressions"][:5]:
            print(f"   ❌ regression {reg['case_id']}: "
                  f"{reg['baseline_status']} → {reg['candidate_status']}")

    # 若 caller 給 candidate_id,且 verdict=pass,更新 status → approved
    if not dry_run and candidate_id and verdict == "pass":
        try:
            cands_coll = db["prompt_rule_candidates"]
            r = cands_coll.update_one(
                {"candidate_id": candidate_id},
                {"$set": {
                    "status": "approved",
                    "gate_verdict": details,
                    "gate_run_id": candidate_run_id,
                    "approved_at": datetime.now(timezone.utc),
                }},
            )
            promoted = (r.modified_count > 0)
            if verbose:
                print(f"   ✅ {candidate_id} promoted to approved" if promoted
                      else f"   ⚠️  {candidate_id} update no-op (id 不存在?)")
        except Exception as e:
            if verbose:
                print(f"   ❌ promote failed: {e}")
    elif not dry_run and candidate_id and verdict == "fail":
        try:
            cands_coll = db["prompt_rule_candidates"]
            cands_coll.update_one(
                {"candidate_id": candidate_id},
                {"$set": {
                    "status": "rejected",
                    "gate_verdict": details,
                    "gate_run_id": candidate_run_id,
                    "rejected_at": datetime.now(timezone.utc),
                }},
            )
            if verbose:
                print(f"   ⏭️  {candidate_id} marked rejected")
        except Exception as e:
            if verbose:
                print(f"   ❌ reject update failed: {e}")

    return {
        "verdict": verdict,
        "details": details,
        "candidate_id": candidate_id,
        "promoted": promoted,
    }


# ============================================================
# CLI
# ============================================================
def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="Regression gate:compare baseline vs candidate test_run"
    )
    parser.add_argument("--candidate-run-id", required=True,
                        help="test_run.run_id of the candidate run")
    parser.add_argument("--candidate-id", default=None,
                        help="prompt_rule_candidates.candidate_id (optional, "
                             "used to update status after gate)")
    parser.add_argument("--domain", default=None,
                        help="Domain filter for baseline lookup")
    parser.add_argument("--latency-threshold", type=float,
                        default=_LATENCY_INCREASE_THRESHOLD,
                        help=f"Max latency increase (default {_LATENCY_INCREASE_THRESHOLD})")
    parser.add_argument("--cost-threshold", type=float,
                        default=_COST_INCREASE_THRESHOLD,
                        help=f"Max cost increase (default {_COST_INCREASE_THRESHOLD})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Don't update candidate.status")
    args = parser.parse_args()

    print("═" * 70)
    print("  GenBI Self-Learning · Regression Gate")
    print(f"  Spec: §19 + §19.5")
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
    print(f"\n🚀 Running gate{mode}")
    print(f"   candidate_run_id: {args.candidate_run_id}")
    print(f"   candidate_id:     {args.candidate_id or '(not provided)'}")
    print(f"   domain:           {args.domain or 'any'}")
    print(f"   thresholds:       latency<{args.latency_threshold:.0%}, "
          f"cost<{args.cost_threshold:.0%}")

    result = run_gate(
        db,
        candidate_run_id=args.candidate_run_id,
        candidate_id=args.candidate_id,
        domain=args.domain,
        latency_threshold=args.latency_threshold,
        cost_threshold=args.cost_threshold,
        dry_run=args.dry_run,
        verbose=True,
    )

    print()
    print("─" * 70)
    print(f"  Final verdict: {result['verdict'].upper()}"
           + (" (promoted)" if result.get("promoted") else ""))
    print("─" * 70)

    return 0 if result["verdict"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
