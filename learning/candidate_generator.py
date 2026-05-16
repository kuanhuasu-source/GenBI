"""
learning/candidate_generator.py — Week 5 D2 (v0.8.10)

把 active instinct 升成 `prompt_rule_candidate`(寫進 `prompt_rule_candidates`
collection),等人類審後 merge 進 prompt template。

對齊 GenBI_v1.3_Self_Learning_MVP_Implementation_Spec.md §18 + §7.5。

# 升 candidate 條件(spec §18)
- instinct.status = 'active'
- instinct.confidence >= 0.85
- instinct.evidence_count >= 3

# target_component 推導(從 instinct.phase)
| phase     | target_component  |
| ---       | ---               |
| phase_0   | phase_0_plan      |
| phase_a   | phase_a_pipeline  |
| phase_b   | phase_b_preprocess|
| phase_c   | phase_c_echarts   |
| phase_d   | phase_d_insight   |
| meta      | meta              |

# Idempotent
同 instinct_id 已有 candidate(status in candidate/testing/approved)→ skip。
"""

from __future__ import annotations

import logging
import sys
import time
import uuid
from datetime import datetime, timezone
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
_MIN_CONFIDENCE = 0.85       # spec §18
_MIN_EVIDENCE = 3            # spec §18
_CANDIDATE_PREFIX = "PRC"    # spec §7.5 example

_PHASE_TO_COMPONENT = {
    "phase_0": "phase_0_plan",
    "phase_a": "phase_a_pipeline",
    "phase_b": "phase_b_preprocess",
    "phase_c": "phase_c_echarts",
    "phase_d": "phase_d_insight",
    "meta":    "meta",
}


# ============================================================
# Helpers
# ============================================================
def _next_candidate_id(coll) -> str:
    try:
        n = coll.count_documents({"candidate_id": {"$regex": f"^{_CANDIDATE_PREFIX}-"}})
    except Exception:
        n = 0
    return f"{_CANDIDATE_PREFIX}-{n + 1:06d}"


def _component_for(phase: str | None) -> str:
    if not phase:
        return "meta"
    return _PHASE_TO_COMPONENT.get(phase, "meta")


def _build_candidate_doc(instinct: dict, candidate_id: str) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "candidate_id": candidate_id,
        "instinct_id": instinct.get("instinct_id"),
        "target_component": _component_for(instinct.get("phase")),

        # proposed_rule:MVP 直接複製 instinct.rule(spec 沒強制要 LLM 二次潤飾)
        "proposed_rule": (instinct.get("rule") or "")[:2000],

        # 帶上 instinct 的 evidence / confidence — gate 判斷依據
        "evidence_count": instinct.get("evidence_count", 0),
        "confidence": instinct.get("confidence", 0.0),

        # 鏈接回 instinct 與其 supporting observations(可追溯)
        "supporting_observation_ids": list(
            instinct.get("supporting_observation_ids") or []
        ),

        "status": "candidate",     # spec §7.5:candidate / testing / approved / rejected
        "created_at": now,
        "updated_at": now,

        # 額外元資料,讓 dashboard 看
        "source_instinct_phase": instinct.get("phase"),
        "source_instinct_tags": list(instinct.get("tags") or []),
        "source_instinct_name": instinct.get("name"),
    }


# ============================================================
# Public:批次掃 instincts → 升 candidates
# ============================================================
def generate_candidates(
    db,
    *,
    min_confidence: float = _MIN_CONFIDENCE,
    min_evidence: int = _MIN_EVIDENCE,
    limit: int = 100,
    dry_run: bool = False,
    verbose: bool = True,
) -> dict:
    """
    把符合條件的 active instinct 升成 prompt_rule_candidate。

    Algorithm(spec §18):
      For each active instinct:
        if confidence >= min_confidence
        and evidence_count >= min_evidence
        and 沒既有 candidate
            → 寫 prompt_rule_candidate

    Returns stats dict 含:scanned_instincts, qualifying, candidates_created,
                           skipped_dup, errors
    """
    if db is None:
        raise ValueError("db is required")

    stats = {
        "run_id": str(uuid.uuid4()),
        "scanned_instincts": 0,
        "qualifying": 0,
        "candidates_created": 0,
        "skipped_dup": 0,
        "errors": 0,
        "created_candidate_ids": [],
    }
    job_started = datetime.now(timezone.utc)

    instincts_coll = db["learning_instincts"]
    candidates_coll = db["prompt_rule_candidates"]
    jobs_coll = db["learning_jobs"]

    # 1. 撈所有 active instinct
    try:
        instincts = list(
            instincts_coll
            .find({"status": "active"})
            .sort([("confidence", -1), ("evidence_count", -1)])
            .limit(2000)
        )
    except Exception as e:
        logger.error(f"failed to fetch instincts: {e}")
        stats["errors"] += 1
        return stats

    stats["scanned_instincts"] = len(instincts)
    if verbose:
        print(f"📥 {len(instincts)} active instincts (min_conf={min_confidence}, "
              f"min_evidence={min_evidence})")

    created_so_far = 0
    for inst in instincts:
        if created_so_far >= limit:
            break

        conf = inst.get("confidence", 0.0) or 0.0
        ev = inst.get("evidence_count", 0) or 0
        if conf < min_confidence or ev < min_evidence:
            continue

        stats["qualifying"] += 1
        instinct_id = inst.get("instinct_id")
        if not instinct_id:
            stats["errors"] += 1
            continue

        # 2. 既有 candidate 檢查(idempotent)
        try:
            existing = candidates_coll.find_one({
                "instinct_id": instinct_id,
                "status": {"$in": ["candidate", "testing", "approved"]},
            })
        except Exception:
            existing = None
        if existing:
            stats["skipped_dup"] += 1
            if verbose:
                print(f"  ⏭️  {instinct_id} 已有 candidate "
                      f"{existing.get('candidate_id')} (status={existing.get('status')})")
            continue

        # 3. 建 candidate
        candidate_id = _next_candidate_id(candidates_coll)
        doc = _build_candidate_doc(inst, candidate_id)

        if dry_run:
            stats["candidates_created"] += 1
            stats["created_candidate_ids"].append(candidate_id)
            created_so_far += 1
            if verbose:
                print(f"  [dry-run] would create {candidate_id} "
                      f"← instinct {instinct_id} (conf={conf:.2f}, "
                      f"evidence={ev}, target={doc['target_component']})")
            continue

        try:
            candidates_coll.insert_one(doc)
            stats["candidates_created"] += 1
            stats["created_candidate_ids"].append(candidate_id)
            created_so_far += 1
            if verbose:
                print(f"  ✅ {candidate_id} ← {instinct_id} "
                      f"(conf={conf:.2f}, evidence={ev}, "
                      f"target={doc['target_component']})")
                print(f"     rule head: {doc['proposed_rule'][:100]}")
        except Exception as e:
            stats["errors"] += 1
            if verbose:
                print(f"  ❌ {candidate_id} insert failed: {type(e).__name__}: "
                      f"{str(e)[:200]}")

    # 4. learning_jobs record
    if not dry_run:
        try:
            jobs_coll.insert_one({
                "job_id": f"JOB-CAND-{int(time.time())}",
                "job_type": "candidate_generation",
                "status": "completed",
                "started_at": job_started,
                "completed_at": datetime.now(timezone.utc),
                "input_count": stats["scanned_instincts"],
                "output_count": stats["candidates_created"],
                "qualifying": stats["qualifying"],
                "skipped_dup": stats["skipped_dup"],
                "errors": stats["errors"],
                "run_id": stats["run_id"],
                "params": {
                    "min_confidence": min_confidence,
                    "min_evidence": min_evidence,
                    "limit": limit,
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
        description="Generate prompt_rule_candidates from qualifying active instincts"
    )
    parser.add_argument("--min-confidence", type=float, default=_MIN_CONFIDENCE,
                        help=f"Min instinct confidence (default {_MIN_CONFIDENCE})")
    parser.add_argument("--min-evidence", type=int, default=_MIN_EVIDENCE,
                        help=f"Min evidence_count (default {_MIN_EVIDENCE})")
    parser.add_argument("--limit", type=int, default=50,
                        help="Max candidates to create")
    parser.add_argument("--dry-run", action="store_true",
                        help="Don't write to DB")
    args = parser.parse_args()

    print("═" * 70)
    print("  GenBI Self-Learning · Prompt Rule Candidate Generator")
    print(f"  Spec: §18 + §7.5")
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
    print(f"\n🚀 Generating candidates (min_conf={args.min_confidence}, "
          f"min_evidence={args.min_evidence}, limit={args.limit}){mode}\n")

    stats = generate_candidates(
        db,
        min_confidence=args.min_confidence,
        min_evidence=args.min_evidence,
        limit=args.limit,
        dry_run=args.dry_run,
        verbose=True,
    )

    print()
    print("─" * 70)
    print(f"  Scanned instincts:    {stats['scanned_instincts']}")
    print(f"  Qualifying:           {stats['qualifying']}")
    print(f"  Candidates created:   {stats['candidates_created']}")
    print(f"  Skipped (dup):        {stats['skipped_dup']}")
    print(f"  Errors:               {stats['errors']}")
    print("─" * 70)

    if not args.dry_run and stats["candidates_created"] > 0:
        print(f"\n📊 Verify:")
        print(f"   mongo {config.MONGO_DB}")
        print(f"   db.prompt_rule_candidates.find({{status:\"candidate\"}}).pretty()")

    return 0


if __name__ == "__main__":
    sys.exit(main())
