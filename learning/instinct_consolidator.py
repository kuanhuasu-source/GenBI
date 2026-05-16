"""
learning/instinct_consolidator.py — Week 4 D1+D2 (v0.8.5)

兩個獨立但相關的 self-learning maintenance jobs:

  1. **consolidate_instincts**:把 ≥3 個語意相似的 verified observation 聚合
     成 1 條 candidate instinct,寫進 `learning_instincts`。
     對齊 spec §14。

  2. **detect_contradictions**:掃 verified observation vs active instinct,
     找出潛在矛盾。命中時 auto-degrade(`contradiction_count++`、conf -= 0.05、
     <0.60 → deprecated),同時寫 `learning_jobs` notification 讓人類審。
     對齊 spec §15 + §15.5。

# 設計重點
- **兩 job 都 idempotent**:多跑不會重複建 instinct(用 supporting_observation_ids
  signature 去重)、多跑 contradiction 不會重複扣分(已記錄的 obs_id 不再降)
- **MVP 不依賴 embedding model**:Jaccard token 相似度替代 cosine,實務夠用
- **auto-degrade 寫 notification**:human review 後可在 dashboard 手動 revert,
  避免 false positive 把好 instinct 害死
"""

from __future__ import annotations

import logging
import re
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
from learning.confidence import _jaccard, _tokenize

logger = logging.getLogger(__name__)


# ============================================================
# Constants
# ============================================================
_MIN_OBSERVATIONS_PER_CLUSTER = 3       # spec §14.3
_MIN_AVG_CONFIDENCE = 0.80              # spec §14.3
_CLUSTER_SIMILARITY_THRESHOLD = 0.45    # Jaccard:rec 相似才算同 cluster
_CONTRADICTION_SIMILARITY_THRESHOLD = 0.5   # 大致同 topic
_CONTRADICTION_CONF_PENALTY = 0.05      # spec §15.2
_CONTRADICTION_DEPRECATE_AT = 0.60      # spec §15.3

# Negation 啟發式:出現在一邊 cause/rec 而不在另一邊 → 視為「立場相反」
# 英文走 token-level(避免 "donut" 誤觸 "do not");中文走 substring(_tokenize 不切中文)
_NEGATION_TOKENS_EN = {
    "not", "no", "never", "avoid", "forbid", "disallow", "forbidden", "dont",
}
_NEGATION_SUBSTRINGS_ZH = (
    "不要", "不可", "不准", "不能", "禁止", "避免", "勿", "別",
    # "不" 單字太常見(「不」可能在「不過」「不一定」中,語意未必是否定),先不加
)


# ============================================================
# Helpers
# ============================================================
def _next_instinct_id(coll, prefix: str = "INST") -> str:
    """從現有 instinct count 推下一個 instinct_id(避免撞 seed 的 INST-SEED-NNN)。"""
    try:
        n = coll.count_documents({"instinct_id": {"$regex": f"^{prefix}-AUTO-"}})
    except Exception:
        n = 0
    return f"{prefix}-AUTO-{n + 1:05d}"


def _has_negation(text: str) -> bool:
    """text 是否含 negation 詞(用於 contradiction 啟發式)。"""
    if not isinstance(text, str):
        return False
    toks = _tokenize(text)
    if toks & _NEGATION_TOKENS_EN:
        return True
    # 中文 substring 比對(_tokenize 不切 CJK)
    for sub in _NEGATION_SUBSTRINGS_ZH:
        if sub in text:
            return True
    return False


def _cluster_obs_by_similarity(
    observations: list[dict], *, threshold: float = _CLUSTER_SIMILARITY_THRESHOLD,
) -> list[list[dict]]:
    """
    簡單貪婪 cluster:對每個 obs,找一個 Jaccard ≥ threshold 的 cluster 加進去,
    沒有就開新 cluster。

    用 recommendation 的 token 集做 Jaccard。
    """
    clusters: list[list[dict]] = []
    cluster_token_sets: list[set[str]] = []

    for obs in observations:
        toks = _tokenize(obs.get("recommendation", ""))
        if not toks:
            continue
        placed = False
        for i, cl_toks in enumerate(cluster_token_sets):
            if _jaccard(toks, cl_toks) >= threshold:
                clusters[i].append(obs)
                # cluster 的 representative token set 用 union(讓 cluster 越長越「廣」)
                cluster_token_sets[i] = cl_toks | toks
                placed = True
                break
        if not placed:
            clusters.append([obs])
            cluster_token_sets.append(toks)

    return clusters


def _build_instinct_doc(cluster: list[dict], *, instinct_id: str,
                          domain: str = "tflex") -> dict:
    """
    把一個 obs cluster 組成 instinct doc。

    canonical rule:取 cluster 內 confidence 最高的 obs 的 recommendation
    當代表(MVP fallback — 不呼叫 LLM 二次合成,降本)。
    cluster 內全部 obs id 都會記在 supporting_observation_ids。
    """
    # 排序:confidence DESC,挑代表
    def _conf(o):
        return o.get("verifier_confidence", 0.0) or 0.0

    sorted_cluster = sorted(cluster, key=_conf, reverse=True)
    repr_obs = sorted_cluster[0]

    confidences = [_conf(o) for o in cluster]
    avg_conf = sum(confidences) / len(confidences) if confidences else 0.0

    # 合併 tags(去重保留順序)
    seen = set()
    merged_tags: list[str] = []
    for o in cluster:
        for t in (o.get("tags") or []):
            if t and t not in seen:
                seen.add(t)
                merged_tags.append(t)

    now = datetime.now(timezone.utc)
    return {
        "instinct_id": instinct_id,
        "name": f"consolidated_{instinct_id.lower().replace('-', '_')}",
        "rule": repr_obs.get("recommendation", "")[:1000],
        "scope": "project",
        "domain": domain,
        "phase": repr_obs.get("phase") or "meta",
        "error_class": None,
        "tags": merged_tags[:10],

        "source": "consolidated",
        "version_source": "v0.8.5",
        "implementation": None,

        "confidence": round(avg_conf, 4),
        "evidence_count": len(cluster),
        "contradiction_count": 0,
        "supporting_observation_ids": [o.get("observation_id") for o in cluster
                                         if o.get("observation_id")],

        # status='candidate' — 等人類審後在 dashboard 改 active
        "status": "candidate",

        "created_at": now,
        "updated_at": now,
    }


def _signature(supporting_ids: list[str]) -> str:
    """idempotent key:同樣的 obs 組合不要重複建 instinct。"""
    return ",".join(sorted(set(supporting_ids)))


# ============================================================
# Public:consolidate verified observations into candidate instincts
# ============================================================
def consolidate_instincts(
    db,
    *,
    min_observations: int = _MIN_OBSERVATIONS_PER_CLUSTER,
    min_avg_confidence: float = _MIN_AVG_CONFIDENCE,
    similarity_threshold: float = _CLUSTER_SIMILARITY_THRESHOLD,
    domain: str = "tflex",
    dry_run: bool = False,
    verbose: bool = True,
) -> dict:
    """
    把 verified observations 聚合成 candidate instincts。

    Algorithm:
      1. 撈所有 status='verified' 的 observation
      2. 依 phase 分組(不同 phase 一定不聚)
      3. 每組內依 recommendation Jaccard 跑貪婪 cluster
      4. cluster size >= min_observations 且 avg confidence >= min_avg_confidence
         → 建 instinct(status='candidate')
      5. 若同樣 supporting_observation_ids signature 已有 instinct,skip

    Returns stats dict。
    """
    if db is None:
        raise ValueError("db is required")

    stats = {
        "run_id": str(uuid.uuid4()),
        "verified_observations": 0,
        "clusters_total": 0,
        "clusters_qualifying": 0,
        "instincts_created": 0,
        "instincts_skipped_dup": 0,
        "errors": 0,
        "created_instinct_ids": [],
    }
    job_started = datetime.now(timezone.utc)

    coll_obs = db["learning_observations"]
    coll_inst = db["learning_instincts"]
    coll_jobs = db["learning_jobs"]

    # 1. fetch verified
    try:
        verified = list(coll_obs.find({"status": "verified"}).limit(2000))
    except Exception as e:
        logger.error(f"failed to fetch verified observations: {e}")
        stats["errors"] += 1
        return stats
    stats["verified_observations"] = len(verified)

    if verbose:
        print(f"📥 Found {len(verified)} verified observation(s)")

    # 2+3. group by phase, cluster
    by_phase: dict[str, list[dict]] = {}
    for obs in verified:
        phase = obs.get("phase") or "meta"
        by_phase.setdefault(phase, []).append(obs)

    for phase, obs_list in by_phase.items():
        clusters = _cluster_obs_by_similarity(
            obs_list, threshold=similarity_threshold
        )
        stats["clusters_total"] += len(clusters)
        if verbose and obs_list:
            print(f"  phase={phase}: {len(obs_list)} obs → {len(clusters)} cluster(s)")

        for cluster in clusters:
            if len(cluster) < min_observations:
                continue
            confidences = [
                (o.get("verifier_confidence") or 0.0) for o in cluster
            ]
            avg_c = sum(confidences) / len(confidences) if confidences else 0.0
            if avg_c < min_avg_confidence:
                continue

            stats["clusters_qualifying"] += 1
            ids = [o.get("observation_id") for o in cluster if o.get("observation_id")]
            sig = _signature(ids)

            # idempotent:已建過(找含完全相同 supporting_observation_ids 的 instinct)
            try:
                exists = coll_inst.find_one({
                    "supporting_observation_ids": {"$all": ids,
                                                    "$size": len(set(ids))},
                    "source": "consolidated",
                })
            except Exception:
                exists = None
            if exists:
                stats["instincts_skipped_dup"] += 1
                if verbose:
                    print(f"  ⏭️  skip(已存在 instinct {exists.get('instinct_id')} "
                          f"含完全相同的 obs 組)")
                continue

            instinct_id = _next_instinct_id(coll_inst)
            doc = _build_instinct_doc(cluster, instinct_id=instinct_id,
                                        domain=domain)
            if dry_run:
                if verbose:
                    print(f"  [dry-run] would create {instinct_id} "
                          f"(cluster size={len(cluster)}, "
                          f"avg conf={avg_c:.2f}, phase={phase})")
                    print(f"     rule head: {doc['rule'][:120]}")
                stats["instincts_created"] += 1
                stats["created_instinct_ids"].append(instinct_id)
                continue

            try:
                coll_inst.insert_one(doc)
                stats["instincts_created"] += 1
                stats["created_instinct_ids"].append(instinct_id)
                if verbose:
                    print(f"  ✅ created {instinct_id} "
                          f"(cluster={len(cluster)}, avg conf={avg_c:.2f}, "
                          f"phase={phase})")
                    print(f"     rule head: {doc['rule'][:120]}")
            except Exception as e:
                stats["errors"] += 1
                if verbose:
                    print(f"  ❌ insert failed: {type(e).__name__}: {str(e)[:200]}")

    # learning_jobs record
    if not dry_run:
        try:
            coll_jobs.insert_one({
                "job_id": f"JOB-CON-{int(time.time())}",
                "job_type": "consolidation",
                "status": "completed",
                "started_at": job_started,
                "completed_at": datetime.now(timezone.utc),
                "input_count": stats["verified_observations"],
                "output_count": stats["instincts_created"],
                "skipped_dup_count": stats["instincts_skipped_dup"],
                "error_count": stats["errors"],
                "run_id": stats["run_id"],
                "params": {
                    "min_observations": min_observations,
                    "min_avg_confidence": min_avg_confidence,
                    "similarity_threshold": similarity_threshold,
                    "domain": domain,
                },
            })
        except Exception as e:
            logger.warning(f"failed to write learning_jobs record: {e}")

    return stats


# ============================================================
# Public:detect contradictions(verified obs vs active instincts)
# ============================================================
def _is_contradicting(obs: dict, inst: dict) -> tuple[bool, str]:
    """
    啟發式 contradiction detection。

    必要條件(全部成立才算 contradicting):
      1. 同 phase
      2. tags 有交集
      3. Jaccard(obs.cause+rec, inst.rule) >= _CONTRADICTION_SIMILARITY_THRESHOLD
      4. negation 詞 presence 不同(一邊有「禁止/不」,另一邊沒有 — 立場相反)

    Returns:
        (is_contradicting, reason_str)
    """
    if obs.get("phase") != inst.get("phase"):
        return False, ""

    obs_tags = set(obs.get("tags") or [])
    inst_tags = set(inst.get("tags") or [])
    if not (obs_tags & inst_tags):
        return False, ""

    obs_text = (obs.get("cause", "") + " " + obs.get("recommendation", ""))
    inst_text = inst.get("rule", "")
    obs_tok = _tokenize(obs_text)
    inst_tok = _tokenize(inst_text)
    sim = _jaccard(obs_tok, inst_tok)
    if sim < _CONTRADICTION_SIMILARITY_THRESHOLD:
        return False, ""

    obs_neg = _has_negation(obs_text)
    inst_neg = _has_negation(inst_text)
    if obs_neg == inst_neg:
        return False, ""  # 兩邊都有或都沒有 negation → 不算立場相反

    return True, (
        f"similarity={sim:.2f}; obs_neg={obs_neg} vs inst_neg={inst_neg}; "
        f"tag_overlap={list(obs_tags & inst_tags)[:3]}"
    )


def detect_contradictions(
    db,
    *,
    dry_run: bool = False,
    verbose: bool = True,
) -> dict:
    """
    掃 verified observations vs active instincts,找潛在矛盾並 auto-degrade。

    Idempotent:每個 instinct 記 `applied_contradiction_obs_ids` 集合,
    已扣過的 obs_id 不再扣第二次。

    每命中一條 contradiction:
      1. instinct.contradiction_count++
      2. instinct.confidence -= 0.05
      3. instinct.applied_contradiction_obs_ids 加進去
      4. 若 confidence < 0.60 → status='deprecated'
      5. 寫一筆 learning_jobs notification(job_type='contradiction_review',
         status='needs_review')

    Returns stats dict。
    """
    if db is None:
        raise ValueError("db is required")

    stats = {
        "run_id": str(uuid.uuid4()),
        "verified_observations": 0,
        "active_instincts": 0,
        "contradictions_detected": 0,
        "instincts_degraded": 0,
        "instincts_deprecated": 0,
        "notifications_written": 0,
        "errors": 0,
    }
    job_started = datetime.now(timezone.utc)

    coll_obs = db["learning_observations"]
    coll_inst = db["learning_instincts"]
    coll_jobs = db["learning_jobs"]

    try:
        verified = list(coll_obs.find({"status": "verified"}).limit(2000))
        active = list(coll_inst.find({"status": "active"}).limit(500))
    except Exception as e:
        logger.error(f"failed to fetch obs/instincts: {e}")
        stats["errors"] += 1
        return stats

    stats["verified_observations"] = len(verified)
    stats["active_instincts"] = len(active)

    if verbose:
        print(f"📥 {len(verified)} verified obs × {len(active)} active instincts")

    for obs in verified:
        obs_id = obs.get("observation_id")
        if not obs_id:
            continue
        for inst in active:
            inst_id = inst.get("instinct_id")
            if not inst_id:
                continue
            applied = set(inst.get("applied_contradiction_obs_ids") or [])
            if obs_id in applied:
                continue  # 這條 obs 已對這 instinct 扣過分,skip

            is_contra, reason = _is_contradicting(obs, inst)
            if not is_contra:
                continue

            stats["contradictions_detected"] += 1
            new_conf = max(0.0, (inst.get("confidence") or 0.0)
                            - _CONTRADICTION_CONF_PENALTY)
            new_status = ("deprecated" if new_conf < _CONTRADICTION_DEPRECATE_AT
                          else inst.get("status"))

            if verbose:
                print(f"  ⚠️  contradiction: obs={obs_id} vs instinct={inst_id}")
                print(f"     {reason}")
                print(f"     conf {inst.get('confidence'):.2f} → {new_conf:.2f}"
                      f"{' (DEPRECATED)' if new_status == 'deprecated' else ''}")

            if not dry_run:
                # update instinct
                try:
                    update_doc = {
                        "$inc": {"contradiction_count": 1},
                        "$set": {
                            "confidence": round(new_conf, 4),
                            "status": new_status,
                            "updated_at": datetime.now(timezone.utc),
                        },
                        "$addToSet": {"applied_contradiction_obs_ids": obs_id},
                    }
                    coll_inst.update_one({"instinct_id": inst_id}, update_doc)
                    stats["instincts_degraded"] += 1
                    if new_status == "deprecated":
                        stats["instincts_deprecated"] += 1
                    # update in-memory active list too, so subsequent obs see new state
                    inst["confidence"] = new_conf
                    inst["status"] = new_status
                    inst.setdefault("applied_contradiction_obs_ids", []).append(obs_id)
                except Exception as e:
                    stats["errors"] += 1
                    logger.warning(f"failed to degrade instinct {inst_id}: {e}")

                # notification
                try:
                    coll_jobs.insert_one({
                        "job_id": f"NOTIF-CONTRA-{int(time.time()*1000)}-"
                                   f"{stats['notifications_written']:03d}",
                        "job_type": "contradiction_review",
                        "status": "needs_review",
                        "started_at": datetime.now(timezone.utc),
                        "completed_at": None,
                        "input_count": 1,
                        "output_count": 0,
                        "linked_observation_id": obs_id,
                        "linked_instinct_id": inst_id,
                        "instinct_status_after": new_status,
                        "confidence_after": new_conf,
                        "reason": reason,
                    })
                    stats["notifications_written"] += 1
                except Exception as e:
                    logger.warning(f"failed to write notification: {e}")

    # final job log
    if not dry_run:
        try:
            coll_jobs.insert_one({
                "job_id": f"JOB-CONTRA-{int(time.time())}",
                "job_type": "contradiction_scan",
                "status": "completed",
                "started_at": job_started,
                "completed_at": datetime.now(timezone.utc),
                "input_count": stats["verified_observations"]
                                * stats["active_instincts"],
                "output_count": stats["contradictions_detected"],
                "instincts_degraded": stats["instincts_degraded"],
                "instincts_deprecated": stats["instincts_deprecated"],
                "notifications_written": stats["notifications_written"],
                "error_count": stats["errors"],
                "run_id": stats["run_id"],
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
        description="Consolidate verified observations into instinct candidates "
                    "+ detect contradictions"
    )
    parser.add_argument("--min-observations", type=int, default=_MIN_OBSERVATIONS_PER_CLUSTER,
                        help=f"Min cluster size to qualify (default {_MIN_OBSERVATIONS_PER_CLUSTER})")
    parser.add_argument("--min-avg-confidence", type=float, default=_MIN_AVG_CONFIDENCE,
                        help=f"Min avg confidence (default {_MIN_AVG_CONFIDENCE})")
    parser.add_argument("--domain", default="tflex",
                        help="Domain tag for new instincts")
    parser.add_argument("--skip-consolidation", action="store_true",
                        help="Only run contradiction detection")
    parser.add_argument("--skip-contradiction", action="store_true",
                        help="Only run consolidation")
    parser.add_argument("--dry-run", action="store_true",
                        help="Don't write to DB")
    args = parser.parse_args()

    print("═" * 70)
    print("  GenBI Self-Learning · Instinct Consolidator + Contradiction Scan")
    print(f"  Spec: §14 (consolidation) + §15 (contradiction)")
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

    if not args.skip_consolidation:
        print(f"\n🚀 Running consolidation{mode}")
        print(f"   min_observations={args.min_observations}, "
              f"min_avg_confidence={args.min_avg_confidence}, "
              f"domain={args.domain}")
        c_stats = consolidate_instincts(
            db,
            min_observations=args.min_observations,
            min_avg_confidence=args.min_avg_confidence,
            domain=args.domain,
            dry_run=args.dry_run,
            verbose=True,
        )
        print()
        print("─" * 70)
        print(f"  Verified observations: {c_stats['verified_observations']}")
        print(f"  Clusters total:        {c_stats['clusters_total']}")
        print(f"  Clusters qualifying:   {c_stats['clusters_qualifying']}")
        print(f"  Instincts created:     {c_stats['instincts_created']}")
        print(f"  Skipped (dup):         {c_stats['instincts_skipped_dup']}")
        print(f"  Errors:                {c_stats['errors']}")
        print("─" * 70)

    if not args.skip_contradiction:
        print(f"\n🚀 Running contradiction detection{mode}")
        d_stats = detect_contradictions(db, dry_run=args.dry_run, verbose=True)
        print()
        print("─" * 70)
        print(f"  Verified observations: {d_stats['verified_observations']}")
        print(f"  Active instincts:      {d_stats['active_instincts']}")
        print(f"  Contradictions found:  {d_stats['contradictions_detected']}")
        print(f"  Instincts degraded:    {d_stats['instincts_degraded']}")
        print(f"  Instincts deprecated:  {d_stats['instincts_deprecated']}")
        print(f"  Notifications written: {d_stats['notifications_written']}")
        print(f"  Errors:                {d_stats['errors']}")
        print("─" * 70)

    if not args.dry_run:
        print(f"\n📊 Inspect:")
        print(f"   mongo {config.MONGO_DB}")
        print(f"   db.learning_instincts.find({{source:\"consolidated\"}}).pretty()")
        print(f"   db.learning_jobs.find({{job_type:\"contradiction_review\","
              f" status:\"needs_review\"}}).pretty()")

    return 0


if __name__ == "__main__":
    sys.exit(main())
