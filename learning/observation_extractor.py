"""
learning/observation_extractor.py — Week 2 D1+D2 (v0.8.2)

把一個 failed task_trace 用 LLM 抽成 structured observation,寫進
`learning_observations` collection,並用 dedupe_key 防重。

對齊 GenBI_v1.3_Self_Learning_MVP_Implementation_Spec.md §10–§11。

# 設計重點
- **5 個 required field** + tags(context / action / result / cause /
  recommendation)。
- **Strict JSON output**:用 `extract_json_block` 跑 balanced-brace
  parser,容忍 markdown fence 與 preamble(沿用 v0.3.6 風格)。
- **Rejection rules**(§10.3):
    1. 任一 required field 缺
    2. recommendation 太空泛
    3. cause 無 trace evidence(目前只做啟發式檢查)
    4. duplicate dedupe_key
- **Dedupe key**:`sha256(phase || cause || recommendation)`,
  collection 上有 unique index(migration 005 已建)。
- **Idempotent**:同 trace 多次跑會被 dedupe_key 擋掉,不會重複進。
- **Cost control**:default limit=5,可由 caller 收緊。

# 使用方式
```bash
# CLI(撈最近 7 天 failed traces,最多抽 5 筆)
python -m learning.observation_extractor --days 7 --limit 5

# Dry run
python -m learning.observation_extractor --days 7 --limit 5 --dry-run
```

```python
# Programmatic(由 admin / scheduler 呼叫)
from learning.observation_extractor import run_observation_extraction
stats = run_observation_extraction(db, llm_service, since_days=7, limit=5)
```
"""

from __future__ import annotations

import hashlib
import json
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
from learning.failure_filter import get_failed_traces, get_trace_by_id

logger = logging.getLogger(__name__)


# ============================================================
# Constants — 對齊 spec §11 + §11.5 Controlled Tag Vocabulary
# ============================================================

REQUIRED_FIELDS = ("context", "action", "result", "cause", "recommendation")

# Tag controlled vocab(供 LLM 參考,不做 strict 過濾;太硬可能扼殺新 pattern)
_TAG_PHASE = {"phase_a", "phase_b", "phase_c", "phase_d", "phase_0", "meta"}
_TAG_CHART_TYPE = {"pie", "bar", "stacked_raw", "stacked_100", "line",
                   "line_dual", "heatmap", "scatter", "kpi_table"}
_TAG_ERROR_CLASS = {"column_missing", "numpy_serialization", "empty_shell",
                    "duplicate_conversion", "import_forbidden", "json_parse",
                    "divide_by_zero", "unsupported_operator",
                    "false_positive_refusal", "wrong_chart_routing",
                    "type_mismatch", "regression_protection"}

# 「太空泛」recommendation 的啟發式黑名單(全小寫比對)。
# 若 recommendation 整段去除空白 ≤ 30 char 且包含其中之一,reject。
_GENERIC_PHRASES = (
    "improve the prompt",
    "improve phase",
    "fix the bug",
    "fix the issue",
    "make it work",
    "do better",
    "be more careful",
    "handle errors",
    "add validation",
    "review the code",
    "retry on failure",
)

# Generic recommendation 還有一個下限:長度 < 25 chars 視為過短
_MIN_RECOMMENDATION_CHARS = 25

# LLM 抽取的 system prompt(stable, embedded — 不走 prompt_repository)
_EXTRACTION_SYSTEM_PROMPT = """\
You are an execution analyst for the GenBI agentic pipeline.

Your job: analyze ONE failed task trace and extract ONE concrete,
actionable observation that captures the root cause and a specific fix.

# Required JSON schema(EXACTLY these 6 keys, nothing else)
{
  "context": "<task being attempted, e.g. phase + intent + chart_type>",
  "action": "<what the system actually did — concrete code/op snippet>",
  "result": "<what happened — error class + symptom>",
  "cause": "<root cause, directly inferable from the trace>",
  "recommendation": "<specific testable rule, names a column/operator/threshold>",
  "tags": ["phase_a"|"phase_b"|"phase_c"|"phase_d", ...up to 5 tags]
}

# Strict rules
1. **Cause** must be backed by something visible in the trace
   (error message / code snippet / LLM message / step name).
   Don't speculate beyond the evidence.
2. **Recommendation** must be specific:
   - name a column / operator / function / threshold
   - phraseable as "If X, do Y" or "Add rule: ..."
   - actionable by a prompt patch or validator (NOT "improve the prompt")
3. **Tags** are short snake_case strings. Prefer the controlled vocab
   (phase_a/b/c/d, pie/bar/stacked_100/line_dual/heatmap/scatter,
   column_missing/numpy_serialization/empty_shell/duplicate_conversion/
   import_forbidden/json_parse/unsupported_operator).
4. Return **STRICT JSON only**. No preamble, no markdown fence,
   no trailing prose.
"""


# ============================================================
# Helpers — trace digest
# ============================================================
def _build_trace_digest(full_trace: dict, *, max_chars: int = 8000) -> str:
    """
    把整個 trace doc 摘要成 LLM 看得懂的 text digest。

    包含:query、status、intent、trace.error、每個 step 的 phase+kind+
    error(若有)+ LLM call 的 prompt/response 摘錄。

    控制長度:單筆 trace 可能 100KB+,我們只塞重點(error step 完整、
    其他 step 只列表頭),total cap ~8000 chars 控成本。
    """
    if not full_trace:
        return "(empty trace)"

    lines: list[str] = []
    lines.append(f"trace_id: {full_trace.get('trace_id')}")
    lines.append(f"domain:   {full_trace.get('domain', '?')}")
    lines.append(f"query:    {full_trace.get('query', '')[:300]}")
    lines.append(f"status:   {full_trace.get('status')}")
    lines.append(f"intent_chart:      {full_trace.get('intent_chart')}")
    lines.append(f"intent_preprocess: {full_trace.get('intent_preprocess')}")
    if full_trace.get("error"):
        lines.append(f"trace.error: {str(full_trace['error'])[:600]}")
    lines.append("")

    steps = full_trace.get("steps") or []
    lines.append(f"--- {len(steps)} steps ---")
    for i, step in enumerate(steps):
        phase = step.get("phase", "?")
        kind = step.get("kind", "?")
        elapsed = step.get("elapsed_s")
        err = step.get("error")
        header = f"[{i}] phase={phase} kind={kind} elapsed={elapsed}s"
        if err:
            header += f"  ERROR: {str(err)[:300]}"
        lines.append(header)

        # 對 error step + 最後 1 個 step 多塞 LLM call payload(若有)
        if err or i == len(steps) - 1:
            llm_call = step.get("llm_call")
            if llm_call:
                msgs = llm_call.get("messages") or []
                # 只塞最後一則 user message 與 response 摘錄
                last_user = next(
                    (m for m in reversed(msgs)
                     if isinstance(m, dict) and m.get("role") == "user"),
                    None
                )
                if last_user:
                    content = (last_user.get("content") or "")[:400]
                    lines.append(f"    user_msg_tail: {content}")
                resp = (llm_call.get("response") or "")[:500]
                if resp:
                    lines.append(f"    llm_response: {resp}")

    digest = "\n".join(lines)
    if len(digest) > max_chars:
        digest = digest[:max_chars] + f"\n\n... (truncated, total {len(digest)} chars)"
    return digest


# ============================================================
# LLM 抽取
# ============================================================
def _call_llm_for_observation(llm_service, digest: str) -> str:
    """
    呼叫 LLM 抽 observation。沿用 LLMService._call_llm,所以也會自動
    被 task_trace recorder hook 到(若有設)。
    """
    messages = [
        {"role": "system", "content": _EXTRACTION_SYSTEM_PROMPT},
        {"role": "user", "content": f"# Failed trace digest\n\n{digest}\n\n"
                                       "Return strict JSON only."},
    ]
    # temperature 略升保留分析角度;但仍偏低避免幻覺
    return llm_service._call_llm(
        messages,
        temperature=0.2,
        max_tokens=1024,
        phase="observation_extraction",
    )


def _parse_observation_response(raw: str) -> dict:
    """
    從 LLM 回應抽 JSON。沿用 v0.3.6 的 balanced-brace parser。

    Returns parsed dict 或 raise ValueError。
    """
    from llm_service import extract_json_block
    block = extract_json_block(raw or "")
    if not block or not block.strip().startswith("{"):
        raise ValueError(f"no JSON block found in LLM response: "
                         f"{(raw or '')[:200]!r}")
    try:
        return json.loads(block)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON parse failed: {e}; block head: {block[:200]!r}")


# ============================================================
# Validation — 對齊 spec §10.3
# ============================================================
def _validate_observation(obs: dict) -> tuple[bool, str]:
    """
    檢查 observation 是否值得收。回傳 (ok, rejection_reason)。

    Rejection rules(§10.3):
      - 任一 required field 缺 or 空
      - recommendation 太空泛(< 25 chars 或含 generic 字眼)
      - cause 太短(< 15 chars,通常代表「LLM made a mistake」這種敷衍)
    """
    if not isinstance(obs, dict):
        return False, "not_a_dict"

    # 1. Required fields
    for f in REQUIRED_FIELDS:
        val = obs.get(f)
        if not isinstance(val, str) or not val.strip():
            return False, f"missing_field:{f}"

    # 2. Cause 證據檢查:過短視為敷衍
    cause = obs["cause"].strip()
    if len(cause) < 15:
        return False, "cause_too_short"

    # 3. Recommendation 具體性
    rec = obs["recommendation"].strip()
    if len(rec) < _MIN_RECOMMENDATION_CHARS:
        return False, "recommendation_too_short"

    rec_lower = rec.lower()
    for ph in _GENERIC_PHRASES:
        if ph in rec_lower and len(rec) < 80:
            # 短 + 含 generic 字眼 → reject;長一點的可能還是有料
            return False, f"recommendation_generic:{ph}"

    return True, ""


def _normalize_tags(raw_tags: Any) -> list[str]:
    """把 LLM 給的 tags 正規化成 list[str](最多 5 個)。"""
    if not isinstance(raw_tags, list):
        return []
    out = []
    for t in raw_tags:
        if isinstance(t, str) and t.strip():
            out.append(t.strip().lower())
        if len(out) >= 5:
            break
    return out


# ============================================================
# Dedupe + ID
# ============================================================
def _compute_dedupe_key(phase: str, cause: str, recommendation: str) -> str:
    """
    sha256(phase || cause || recommendation)。

    cause / recommendation 先 normalize(strip + lower + collapse whitespace)
    避免大小寫 / 多空白造成的假新,但保留語意差異。
    """
    def _norm(s: str) -> str:
        return " ".join((s or "").lower().split())
    payload = f"{(phase or '').lower()}||{_norm(cause)}||{_norm(recommendation)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _compute_query_hash(query: str) -> str:
    return hashlib.sha256((query or "").strip().encode("utf-8")).hexdigest()


def _next_observation_id(coll) -> str:
    """生成下一個 OBS-NNNNNN id(從現有筆數 +1)。"""
    try:
        n = coll.count_documents({})
    except Exception:
        n = 0
    # 加 6 位 zero-pad;若已超過 999999,再多一位也 OK
    return f"OBS-{n + 1:06d}"


# ============================================================
# 對外:單一 trace → observation dict
# ============================================================
def extract_observation(full_trace: dict, llm_service,
                         *, run_id: str | None = None) -> dict:
    """
    把一個 task_trace doc 跑 LLM 抽出 structured observation。

    Args:
        full_trace: 完整 trace doc(含 messages),由 get_trace_by_id 拿到
        llm_service: LLMService instance
        run_id: 本次 extraction batch 的 id(可選,讓 caller 串)

    Returns:
        observation dict — 含:
          observation_id (None 若 dry/未寫 DB),source_trace_id,
          query_hash, phase, context/action/result/cause/recommendation,
          tags, status='candidate', dedupe_key, created_at,
          run_id, extraction_error
        若 LLM 失敗或 reject,status='rejected' + rejection_reason。
    """
    base = {
        "observation_id": None,
        "run_id": run_id or str(uuid.uuid4()),
        "source_trace_id": full_trace.get("trace_id"),
        "query_hash": _compute_query_hash(full_trace.get("query", "")),
        "phase": None,
        "context": "",
        "action": "",
        "result": "",
        "cause": "",
        "recommendation": "",
        "tags": [],
        "status": "candidate",
        "dedupe_key": None,
        "created_at": datetime.now(timezone.utc),
        "rejection_reason": None,
        "extraction_error": None,
    }

    if not full_trace:
        base["status"] = "rejected"
        base["rejection_reason"] = "empty_trace"
        return base

    # ── 1. 組 digest + 呼叫 LLM ──
    digest = _build_trace_digest(full_trace)
    try:
        raw = _call_llm_for_observation(llm_service, digest)
    except Exception as e:
        base["status"] = "rejected"
        base["extraction_error"] = f"llm_call_failed: {str(e)[:300]}"
        return base

    # ── 2. parse JSON ──
    try:
        obs = _parse_observation_response(raw)
    except Exception as e:
        base["status"] = "rejected"
        base["extraction_error"] = f"json_parse_failed: {str(e)[:300]}"
        return base

    # ── 3. validate ──
    ok, reason = _validate_observation(obs)
    if not ok:
        base.update({
            "context": str(obs.get("context", ""))[:1000],
            "action": str(obs.get("action", ""))[:1000],
            "result": str(obs.get("result", ""))[:1000],
            "cause": str(obs.get("cause", ""))[:1000],
            "recommendation": str(obs.get("recommendation", ""))[:1000],
            "tags": _normalize_tags(obs.get("tags")),
            "status": "rejected",
            "rejection_reason": reason,
        })
        return base

    # ── 4. 推斷 phase(優先 tag,fallback step 內第一個 error 的 phase)──
    tags = _normalize_tags(obs.get("tags"))
    phase = next((t for t in tags if t in _TAG_PHASE), None)
    if not phase:
        # 退而求其次:從 trace step 找第一個 error 的 phase
        for step in (full_trace.get("steps") or []):
            if step.get("error"):
                p = step.get("phase", "")
                # 抽 phase prefix(e.g. "phase_b_preprocess" → "phase_b")
                for known in _TAG_PHASE:
                    if known and p.startswith(known):
                        phase = known
                        break
                if phase:
                    break
    if not phase:
        phase = "meta"

    # ── 5. dedupe key ──
    dedupe_key = _compute_dedupe_key(phase, obs["cause"], obs["recommendation"])

    base.update({
        "phase": phase,
        "context": str(obs["context"]).strip()[:1000],
        "action": str(obs["action"]).strip()[:1000],
        "result": str(obs["result"]).strip()[:1000],
        "cause": str(obs["cause"]).strip()[:1000],
        "recommendation": str(obs["recommendation"]).strip()[:1000],
        "tags": tags,
        "dedupe_key": dedupe_key,
        "status": "candidate",
    })
    return base


# ============================================================
# 對外:批次跑 extraction(write to DB + dedupe)
# ============================================================
def run_observation_extraction(
    db,
    llm_service,
    *,
    since_days: int = 7,
    limit: int = 5,
    statuses: tuple = ("failed", "refused"),
    dry_run: bool = False,
    verbose: bool = True,
) -> dict:
    """
    一鍵跑 failed traces → observations 流程。

    1. 用 failure_filter 撈最近 N 天 failed trace summary
    2. 對每筆撈完整 trace
    3. 呼叫 extract_observation
    4. 用 dedupe_key 寫進 learning_observations(duplicate 直接 skip)
    5. 同時寫一筆 learning_jobs(job_type='observation_extraction')

    Args:
        db: pymongo Database
        llm_service: LLMService instance(同 production 設定)
        since_days: 看幾天
        limit: 最多處理幾筆 trace(cost control,spec §22 建議每天 ≤ 50)
        statuses: 要分析的 status filter
        dry_run: 只跑不寫 DB(observations 跟 job record 都不寫)
        verbose: 印 per-trace 結果

    Returns:
        {
          "run_id": str,
          "input_count": N,        # 撈到幾個 candidate trace
          "extracted": N,          # LLM 抽出來且通過 validation
          "rejected": N,           # validation 沒過
          "deduped": N,            # dedupe_key 撞到既有
          "errors": N,             # LLM call / JSON parse 失敗
          "observations": [...],   # 抽出來的 obs list(verbose 模式才完整)
        }
    """
    if db is None:
        raise ValueError("db is required")
    if llm_service is None:
        raise ValueError("llm_service is required")

    run_id = str(uuid.uuid4())
    job_started = datetime.now(timezone.utc)
    stats = {
        "run_id": run_id,
        "input_count": 0,
        "extracted": 0,
        "rejected": 0,
        "deduped": 0,
        "errors": 0,
        "observations": [],
    }

    # ── 1. 撈 candidate traces ──
    summaries = get_failed_traces(
        db, since_days=since_days, statuses=statuses, limit=limit
    )
    stats["input_count"] = len(summaries)

    if verbose:
        print(f"📥 Found {len(summaries)} candidate trace(s) to analyze")

    coll_obs = db["learning_observations"]
    coll_jobs = db["learning_jobs"]

    # ── 2. 對每筆 trace 跑 extraction ──
    for i, summary in enumerate(summaries, 1):
        trace_id = summary.get("trace_id")
        if not trace_id:
            continue
        full_trace = get_trace_by_id(db, trace_id)
        if not full_trace:
            stats["errors"] += 1
            if verbose:
                print(f"  [{i}] {trace_id}: ❌ trace doc missing")
            continue

        if verbose:
            print(f"  [{i}/{len(summaries)}] {trace_id[:8]}… "
                  f"({summary.get('status')}) — extracting...")

        t0 = time.time()
        obs = extract_observation(full_trace, llm_service, run_id=run_id)
        elapsed = round(time.time() - t0, 2)

        if obs["status"] == "rejected":
            if obs.get("extraction_error"):
                stats["errors"] += 1
                if verbose:
                    print(f"     ❌ {obs['extraction_error']} ({elapsed}s)")
            else:
                stats["rejected"] += 1
                if verbose:
                    print(f"     ⏭️  rejected: {obs.get('rejection_reason')} "
                          f"({elapsed}s)")
            stats["observations"].append(obs)
            continue

        # ── 3. dedupe + write ──
        if dry_run:
            if verbose:
                print(f"     [dry-run] would insert dedupe_key="
                      f"{obs['dedupe_key'][:12]}… ({elapsed}s)")
            stats["extracted"] += 1
            stats["observations"].append(obs)
            continue

        # 賦 observation_id 後再插入
        obs["observation_id"] = _next_observation_id(coll_obs)
        try:
            coll_obs.insert_one({k: v for k, v in obs.items()
                                  if k not in ("rejection_reason",
                                                "extraction_error")})
            stats["extracted"] += 1
            if verbose:
                print(f"     ✅ {obs['observation_id']} written ({elapsed}s)")
        except Exception as e:
            # DuplicateKeyError → dedupe_key 撞到既有
            err_name = type(e).__name__
            if "Duplicate" in err_name or "duplicate" in str(e).lower():
                stats["deduped"] += 1
                if verbose:
                    print(f"     ⏭️  dedupe(同 cause+recommendation 已存在)")
            else:
                stats["errors"] += 1
                if verbose:
                    print(f"     ❌ insert failed: {err_name}: {str(e)[:200]}")
        stats["observations"].append(obs)

    # ── 4. 寫 job record(讓 dashboard 看跑了什麼)──
    if not dry_run:
        try:
            coll_jobs.insert_one({
                "job_id": f"JOB-OBS-{run_id[:8]}",
                "job_type": "observation_extraction",
                "status": "completed",
                "started_at": job_started,
                "completed_at": datetime.now(timezone.utc),
                "input_count": stats["input_count"],
                "output_count": stats["extracted"],
                "rejected_count": stats["rejected"],
                "deduped_count": stats["deduped"],
                "error_count": stats["errors"],
                "run_id": run_id,
                "params": {
                    "since_days": since_days,
                    "limit": limit,
                    "statuses": list(statuses),
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
        description="Extract observations from failed task_traces"
    )
    parser.add_argument("--days", type=int, default=7,
                        help="Lookback window in days (default 7)")
    parser.add_argument("--limit", type=int, default=5,
                        help="Max traces to process (default 5, spec §22 daily cap=50)")
    parser.add_argument("--status", default="failed,refused",
                        help="Comma-separated status filter")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run LLM but do NOT write to DB")
    args = parser.parse_args()

    print("═" * 70)
    print("  GenBI Self-Learning · Observation Extraction")
    print(f"  Spec: GenBI_v1.3_Self_Learning_MVP_Implementation_Spec.md §10–§11")
    print("═" * 70)

    # ── MongoDB ──
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

    # ── LLMService ──
    try:
        from llm_service import LLMService
        llm = LLMService(**config.llm_service_kwargs())
        print(f"✅ LLMService initialized: model={config.LLM_MODEL}")
    except Exception as e:
        print(f"❌ LLMService 初始化失敗:{e}", file=sys.stderr)
        return 1

    statuses = tuple(s.strip() for s in args.status.split(","))
    mode = " (DRY RUN)" if args.dry_run else ""
    print(f"\n🚀 Running extraction (days={args.days}, limit={args.limit}, "
          f"status={statuses}){mode}\n")

    stats = run_observation_extraction(
        db, llm,
        since_days=args.days,
        limit=args.limit,
        statuses=statuses,
        dry_run=args.dry_run,
        verbose=True,
    )

    print()
    print("─" * 70)
    print(f"  run_id:      {stats['run_id']}")
    print(f"  Input:       {stats['input_count']}")
    print(f"  Extracted:   {stats['extracted']}")
    print(f"  Rejected:    {stats['rejected']}")
    print(f"  Deduped:     {stats['deduped']}")
    print(f"  Errors:      {stats['errors']}")
    print("─" * 70)

    if not args.dry_run and stats["extracted"] > 0:
        print(f"\n📊 Verify:")
        print(f"   mongo {config.MONGO_DB}")
        print(f"   db.learning_observations.find({{run_id:\"{stats['run_id']}\"}}).pretty()")

    return 0


if __name__ == "__main__":
    sys.exit(main())
