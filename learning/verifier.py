"""
learning/verifier.py — Week 3 D2 (v0.8.4)

Verifier agent:對 observation_extractor 抽出來的 candidate observation 做**獨立**
驗證(LLM 自審),搭配 confidence.py 算 numeric score,最後決定 accept / revise / reject。

對齊 GenBI_v1.3_Self_Learning_MVP_Implementation_Spec.md §12 + §7.2。

# Decision rule(本實作版)
- **accept**:LLM 判 "accept" 且 confidence >= 0.75
- **revise**:LLM 判 "revise" 或 (LLM accept 但 0.60 <= confidence < 0.75)
- **reject**:LLM 判 "reject" 或 confidence < 0.60 或 LLM 判 hallucinated_cause

# 設計重點
- **獨立性**:Verifier 用**另一條 LLM call**,看到的是 observation + trace digest,
  不看 extractor 的 prompt。等於跨 model run 雙重驗證(雖然同 model,但
  prompt-level 獨立)。
- **Strict JSON output**:沿用 v0.3.6 balanced-brace parser
- **DB schema**(`verifier_results`,對齊 spec §7.2):
    observation_id, decision, confidence, reasoning, issues, created_at,
    sub_scores(extra:把 confidence 4 個 sub-component 都存)
- **狀態同步**:verifier 跑完後也更新對應 observation.status
    (accept → verified;reject → rejected;revise → keep candidate + 加 issues)
"""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import config
from learning import confidence as confidence_mod
from learning.failure_filter import get_trace_by_id
from learning.observation_extractor import _build_trace_digest

logger = logging.getLogger(__name__)


# ============================================================
# Constants
# ============================================================
_ACCEPT_THRESHOLD = 0.75
_REVISE_THRESHOLD = 0.60

_VERIFIER_SYSTEM_PROMPT = """\
You are an independent verifier for a self-learning system.

You receive ONE observation (extracted by a separate analyst) plus the
original failed trace. Your job: judge whether the observation should be
trusted, revised, or rejected.

# Required JSON schema(EXACTLY these keys, nothing else)
{
  "decision": "accept" | "revise" | "reject",
  "reasoning": "<one-paragraph explanation, refer to trace evidence>",
  "issues": ["<short list of concrete problems if any>"],
  "trace_quotes_count": <integer 0..10 — how many distinct trace pieces
                         (error message / step phase / LLM message snippet)
                         actually support the cause>
}

# Decision rules
- **accept**:
    - The observation's `cause` is directly supported by the trace
      (error text, step error, LLM message)
    - The `recommendation` is specific and testable
      (names a column / operator / threshold / specific phase)
    - NOT a near-duplicate of well-known lessons
- **revise**:
    - The core insight is right but `cause` or `recommendation` is partial
      or missing a key detail
    - Trace evidence is mostly there but not fully cited
- **reject**:
    - `cause` is speculative / not actually visible in the trace
      (hallucinated)
    - `recommendation` is too generic ("improve the prompt") or untestable
    - `recommendation` rephrases something the existing instinct base
      clearly already covers

Be strict. False positives in this loop create polluted prompt patches
later.

Return STRICT JSON only — no preamble, no markdown fence, no trailing text.
"""


# ============================================================
# Helpers
# ============================================================
def _build_verifier_user_msg(observation: dict, trace_digest: str) -> str:
    """組裝 verifier user message。"""
    obs_block = json.dumps({
        "context": observation.get("context", ""),
        "action": observation.get("action", ""),
        "result": observation.get("result", ""),
        "cause": observation.get("cause", ""),
        "recommendation": observation.get("recommendation", ""),
        "tags": observation.get("tags", []),
        "phase": observation.get("phase", ""),
    }, ensure_ascii=False, indent=2)

    return (
        "# Observation to verify\n\n"
        f"{obs_block}\n\n"
        "# Original trace digest(read-only evidence)\n\n"
        f"{trace_digest}\n\n"
        "Return strict JSON only."
    )


def _parse_verifier_response(raw: str) -> dict:
    """從 LLM 回應抽 JSON。"""
    from llm_service import extract_json_block
    block = extract_json_block(raw or "")
    if not block or not block.strip().startswith("{"):
        raise ValueError(f"no JSON block in verifier response: {(raw or '')[:200]!r}")
    return json.loads(block)


def _normalize_decision(raw: Any) -> str:
    """把 LLM 回的 decision 規範到 {accept, revise, reject},無法判讀 → reject。"""
    if not isinstance(raw, str):
        return "reject"
    raw_lower = raw.strip().lower()
    if raw_lower in ("accept", "revise", "reject"):
        return raw_lower
    # 容錯:很多 LLM 會回 approved/rejected/needs_revision 之類
    if "accept" in raw_lower or "approve" in raw_lower:
        return "accept"
    if "revise" in raw_lower or "revision" in raw_lower or "needs" in raw_lower:
        return "revise"
    return "reject"


def _final_decision(llm_decision: str, confidence: float, issues: list) -> str:
    """
    結合 LLM 判斷 + numeric confidence 給最終 decision。

    優先級:
      - LLM "reject" → reject(直接信任 LLM)
      - confidence < 0.60 → reject(分數太低,即使 LLM 說 accept 也擋)
      - LLM "revise" 或 (LLM accept 但 conf < 0.75) → revise
      - LLM "accept" 且 conf >= 0.75 → accept
    """
    if llm_decision == "reject":
        return "reject"
    if confidence < _REVISE_THRESHOLD:
        return "reject"
    if llm_decision == "revise":
        return "revise"
    # LLM accept
    if confidence >= _ACCEPT_THRESHOLD:
        return "accept"
    return "revise"


# ============================================================
# 對外:單筆 verify
# ============================================================
def verify_observation(observation: dict, trace: dict,
                        llm_service,
                        *, db: Any = None) -> dict:
    """
    對一個 candidate observation 跑 verifier。

    Args:
        observation: 從 learning_observations 撈出來的 dict
        trace: 對應的 task_trace doc
        llm_service: LLMService instance(產 verifier 判決用)
        db: pymongo Database — 給 confidence.consistency/novelty 查既有 obs/instincts

    Returns:
        verifier_result dict — 可直接插進 verifier_results collection:
          observation_id, decision (accept/revise/reject), confidence,
          reasoning, issues, sub_scores, llm_decision, llm_trace_quotes_count,
          created_at, error
    """
    base = {
        "observation_id": observation.get("observation_id"),
        "decision": "reject",
        "confidence": 0.0,
        "reasoning": "",
        "issues": [],
        "sub_scores": {},
        "llm_decision": None,
        "llm_trace_quotes_count": 0,
        "created_at": datetime.now(timezone.utc),
        "error": None,
    }

    # ── 1. 組 digest + LLM call ──
    digest = _build_trace_digest(trace) if trace else "(trace unavailable)"
    user_msg = _build_verifier_user_msg(observation, digest)

    try:
        raw = llm_service._call_llm(
            [
                {"role": "system", "content": _VERIFIER_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.1,
            max_tokens=800,
            phase="verification",
        )
    except Exception as e:
        base["error"] = f"llm_call_failed: {str(e)[:300]}"
        base["reasoning"] = "verifier LLM call failed; defaulting to reject"
        return base

    try:
        parsed = _parse_verifier_response(raw)
    except Exception as e:
        base["error"] = f"json_parse_failed: {str(e)[:300]}"
        base["reasoning"] = "verifier response not parseable; defaulting to reject"
        return base

    llm_decision = _normalize_decision(parsed.get("decision"))
    reasoning = str(parsed.get("reasoning", ""))[:1000]
    issues_raw = parsed.get("issues") or []
    issues = [str(x)[:300] for x in issues_raw if isinstance(x, (str, int, float))][:10]
    try:
        quotes_n = max(0, int(parsed.get("trace_quotes_count", 0)))
    except Exception:
        quotes_n = 0

    # ── 2. compute composite confidence ──
    conf = confidence_mod.compute_confidence(
        observation,
        trace_quotes_count=quotes_n,
        db=db,
    )

    # ── 3. final decision ──
    final = _final_decision(llm_decision, conf["confidence"], issues)

    base.update({
        "decision": final,
        "confidence": conf["confidence"],
        "reasoning": reasoning,
        "issues": issues,
        "sub_scores": {k: v for k, v in conf.items() if k != "confidence"},
        "llm_decision": llm_decision,
        "llm_trace_quotes_count": quotes_n,
    })
    return base


# ============================================================
# 對外:批次跑(撈所有 candidate observations 一次驗)
# ============================================================
def run_verification(
    db,
    llm_service,
    *,
    run_id: str | None = None,
    limit: int = 20,
    dry_run: bool = False,
    verbose: bool = True,
) -> dict:
    """
    批次驗證 learning_observations 內 status='candidate' 的 observation。

    1. 撈最多 `limit` 筆 candidate(若給 run_id,只看那批 extraction 的產出)
    2. 對每筆撈對應 trace
    3. 跑 verify_observation
    4. 寫 verifier_results + 更新 observation.status

    Args:
        db: pymongo Database
        llm_service: LLMService
        run_id: 只驗 extraction 那批的(可選)
        limit: 最多驗幾筆(cost control,§22 daily cap=50)
        dry_run: 跑 LLM 但不寫 DB
        verbose: 印 per-observation 結果

    Returns:
        {
          run_id, input_count, accepted, revised, rejected, errors,
          results (list of verifier_result dicts)
        }
    """
    if db is None:
        raise ValueError("db is required")
    if llm_service is None:
        raise ValueError("llm_service is required")

    job_started = datetime.now(timezone.utc)
    stats = {
        "run_id": run_id,
        "input_count": 0,
        "accepted": 0,
        "revised": 0,
        "rejected": 0,
        "errors": 0,
        "results": [],
    }

    coll_obs = db["learning_observations"]
    coll_ver = db["verifier_results"]
    coll_jobs = db["learning_jobs"]

    # ── 1. 撈 candidate ──
    query: dict = {"status": "candidate"}
    if run_id:
        query["run_id"] = run_id
    try:
        candidates = list(coll_obs.find(query).sort("created_at", -1).limit(limit))
    except Exception as e:
        logger.error(f"failed to query learning_observations: {e}")
        return stats

    stats["input_count"] = len(candidates)
    if verbose:
        print(f"📥 Found {len(candidates)} candidate observation(s) to verify")

    # ── 2. 對每筆跑 verify ──
    for i, obs in enumerate(candidates, 1):
        obs_id = obs.get("observation_id", "?")
        trace_id = obs.get("source_trace_id")
        if verbose:
            print(f"  [{i}/{len(candidates)}] {obs_id} — verifying...")

        trace = get_trace_by_id(db, trace_id) if trace_id else None

        t0 = time.time()
        result = verify_observation(obs, trace, llm_service, db=db)
        elapsed = round(time.time() - t0, 2)

        decision = result["decision"]
        if result.get("error"):
            stats["errors"] += 1
            if verbose:
                print(f"     ❌ {result['error']} ({elapsed}s)")
        elif decision == "accept":
            stats["accepted"] += 1
            if verbose:
                print(f"     ✅ ACCEPT  conf={result['confidence']:.2f}  ({elapsed}s)")
        elif decision == "revise":
            stats["revised"] += 1
            if verbose:
                print(f"     ✏️  REVISE  conf={result['confidence']:.2f}  ({elapsed}s)")
        else:
            stats["rejected"] += 1
            if verbose:
                print(f"     ⏭️  REJECT  conf={result['confidence']:.2f}  ({elapsed}s)")
                if result.get("issues"):
                    print(f"         issues: {result['issues'][:3]}")

        # ── 3. 寫 verifier_results + 更新 obs.status ──
        if not dry_run:
            try:
                coll_ver.insert_one({k: v for k, v in result.items()
                                       if k != "error"} | {
                    "verifier_error": result.get("error"),
                })
            except Exception as e:
                logger.warning(f"failed to insert verifier_result for {obs_id}: {e}")

            # 更新 observation.status(accept → verified,reject → rejected,revise → 維持 candidate)
            new_status = {
                "accept": "verified",
                "reject": "rejected",
                "revise": "candidate",  # 不動 status,但下游 dashboard 看得到 verifier_results
            }.get(decision, "candidate")
            try:
                coll_obs.update_one(
                    {"observation_id": obs_id},
                    {"$set": {
                        "status": new_status,
                        "verifier_confidence": result["confidence"],
                        "verifier_decision": decision,
                        "verifier_issues": result.get("issues", []),
                    }},
                )
            except Exception as e:
                logger.warning(f"failed to update observation status for {obs_id}: {e}")

        stats["results"].append(result)

    # ── 4. learning_jobs record ──
    if not dry_run:
        try:
            coll_jobs.insert_one({
                "job_id": f"JOB-VER-{int(time.time())}",
                "job_type": "verification",
                "status": "completed",
                "started_at": job_started,
                "completed_at": datetime.now(timezone.utc),
                "input_count": stats["input_count"],
                "output_count": stats["accepted"] + stats["revised"],
                "accepted_count": stats["accepted"],
                "revised_count": stats["revised"],
                "rejected_count": stats["rejected"],
                "error_count": stats["errors"],
                "linked_run_id": run_id,
                "params": {"limit": limit},
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
        description="Verify candidate observations in learning_observations"
    )
    parser.add_argument("--run-id", default=None,
                        help="Only verify observations from this extraction run_id")
    parser.add_argument("--limit", type=int, default=10,
                        help="Max observations to verify (default 10)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run LLM but don't write to DB")
    args = parser.parse_args()

    print("═" * 70)
    print("  GenBI Self-Learning · Verifier")
    print(f"  Spec: GenBI_v1.3_Self_Learning_MVP_Implementation_Spec.md §12 + §13")
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

    try:
        from llm_service import LLMService
        llm = LLMService(**config.llm_service_kwargs())
        print(f"✅ LLMService initialized: model={config.LLM_MODEL}")
    except Exception as e:
        print(f"❌ LLMService 初始化失敗:{e}", file=sys.stderr)
        return 1

    mode = " (DRY RUN)" if args.dry_run else ""
    print(f"\n🚀 Running verification (limit={args.limit}, run_id={args.run_id}){mode}\n")

    stats = run_verification(
        db, llm,
        run_id=args.run_id,
        limit=args.limit,
        dry_run=args.dry_run,
        verbose=True,
    )

    print()
    print("─" * 70)
    print(f"  Input:    {stats['input_count']}")
    print(f"  ✅ Accepted: {stats['accepted']}")
    print(f"  ✏️  Revised:  {stats['revised']}")
    print(f"  ⏭️  Rejected: {stats['rejected']}")
    print(f"  ❌ Errors:   {stats['errors']}")
    print("─" * 70)

    if not args.dry_run and stats["accepted"] > 0:
        print(f"\n📊 Verify:")
        print(f"   mongo {config.MONGO_DB}")
        print(f"   db.learning_observations.find({{status:\"verified\"}}).pretty()")
        print(f"   db.verifier_results.find().sort({{created_at:-1}}).limit(5).pretty()")

    return 0


if __name__ == "__main__":
    sys.exit(main())
