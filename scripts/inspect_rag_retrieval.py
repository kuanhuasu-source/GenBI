"""scripts/inspect_rag_retrieval.py — diagnose RAG retrieval(無 LLM call)。

回答 3 個問題:
1. 5 個 vector index 各有多少 docs?content 長啥樣?
2. 對代表性 tflex query,orchestrator 各 phase 抽到啥?score 多高?有沒過 min_score?
3. RAG-on 跑 LLM 時,slot 是 inject 真內容還是空字串(silent RAG)?

用法:
    python scripts/inspect_rag_retrieval.py
    python scripts/inspect_rag_retrieval.py --query "顯示各公司今年申請狀態"
    python scripts/inspect_rag_retrieval.py --persist-dir ./rag_indices --domain tflex
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# v0.16.1+ FIX:import config FIRST 觸發 .env 載入,讓 GENBI_EMBEDDING_BACKEND
# 等環境變數被 get_embedding_pipeline() 看到。沒這個 inspector 會 silently
# 走 local MiniLM 預設(雖然 .env 設了 http+bge-m3 也沒用)。
import config  # noqa: F401  (side-effect:dotenv load)

from embedding_pipeline import get_embedding_pipeline
from rag_index_repository import (
    KNOWN_INDEX_NAMES,
    RAGIndexRepository,
    make_chroma_factory,
)
from retrieval_orchestrator import (
    DEFAULT_PHASE_POLICY,
    DEFAULT_SLOT_CONFIGS,
    RetrievalOrchestrator,
)


# ============================================================
# 代表性 tflex queries(從 test_runner 的常見 case 抽樣)
# ============================================================
DEFAULT_QUERIES = [
    "各公司今年申請審核狀態統計",
    "員工人數最多的前 10 個部門",
    "申請處理天數平均值",
    "顯示員工 H/C 圓餅圖",
    "過去三個月的申請趨勢",   # 預期 REFUSE — 無時間維度
    "review_status 每個狀態值的分佈",
]


def _short(s: str, n: int = 80) -> str:
    s = s.replace("\n", " ")
    return s[:n] + ("..." if len(s) > n else "")


# ============================================================
# Part 1:索引 inventory
# ============================================================
def report_inventory(repo: RAGIndexRepository) -> dict[str, int]:
    print("\n" + "=" * 60)
    print("PART 1:Index Inventory")
    print("=" * 60)
    counts = {}
    for name in KNOWN_INDEX_NAMES:
        try:
            n = repo.count(name)
        except Exception as e:
            print(f"  {name:25s} ❌ {e}")
            counts[name] = -1
            continue
        counts[name] = n
        flag = "✅" if n > 0 else "⚠️ 空"
        print(f"  {name:25s} {flag}  {n:4d} docs")

        # 抽 sample 顯示 content
        if n > 0:
            try:
                samples = repo.list_docs(name, limit=3)
                for s in samples:
                    md = s.metadata or {}
                    domain = md.get("domain", "?")
                    print(f"    · [{domain}] {_short(s.content, 90)}")
            except Exception as e:
                print(f"    (list_docs 失敗:{e})")
    return counts


# ============================================================
# Part 2:Per-query retrieve trace
# ============================================================
def report_retrieve(
    orch: RetrievalOrchestrator, queries: list[str], domain: str,
) -> None:
    print("\n" + "=" * 60)
    print("PART 2:Retrieve Trace(per phase per query)")
    print("=" * 60)

    for q in queries:
        print(f"\n──── query:{q}")
        # 直接 embed 一次拿 query vec
        qv = orch.ep.embed_one(q)

        # 跑每個 phase(看每個 phase 抽到什麼)
        for phase in DEFAULT_PHASE_POLICY.keys():
            slots = DEFAULT_PHASE_POLICY[phase]
            phase_label = phase
            slot_summary = []
            empty_count = 0
            for slot_name in slots:
                cfg = DEFAULT_SLOT_CONFIGS[slot_name]
                # 跑底層 search 看 raw scores(orchestrator 會 truncate,
                # 這邊直接調 repo 拿原始)
                filter_ = None
                if "domain" in cfg.filter_keys:
                    filter_ = {"domain": domain}
                try:
                    hits = orch.repo.search(
                        cfg.index_name, qv, top_k=cfg.top_k,
                        filter=filter_,
                        min_score=-1.1,   # 暫不擋,看真實 score 分佈
                    )
                except Exception as e:
                    slot_summary.append(f"{slot_name}❌{e}")
                    continue
                # 算多少過 cfg.min_score(預設 0.3)
                pass_min = sum(1 for h in hits if h.score >= cfg.min_score)
                if hits:
                    top = hits[0]
                    slot_summary.append(
                        f"{slot_name}({len(hits)}hit, top={top.score:.2f}, "
                        f"≥{cfg.min_score}={pass_min})"
                    )
                else:
                    slot_summary.append(f"{slot_name}(0hit)")
                    empty_count += 1
            print(f"  [{phase_label}]")
            for s in slot_summary:
                print(f"    {s}")

        # 然後跑 orchestrator(會套 min_score)看實際會 inject 啥
        # v0.16.0+ M6.3:Phase 0/B/C 各跑一次,看哪些 slot 真有內容進 prompt
        for phase_to_inject in (
            "phase_0_plan",
            "phase_b_preprocess",
            "phase_c_chart",
        ):
            actual = orch.retrieve_for_phase(
                phase=phase_to_inject, query=q, domain=domain,
                rag_enabled=True,
            )
            if actual:
                print(f"  ✅ INJECT @ {phase_to_inject}:{list(actual.keys())}")
                for k, v in actual.items():
                    # 印前 200 char,看到實際內容比看到 list 有用
                    print(f"     {k} →")
                    for line in v.split("\n")[:3]:
                        print(f"        {_short(line, 110)}")
            else:
                print(f"  ⚠️  INJECT @ {phase_to_inject}:NOTHING")


# ============================================================
# Part 3:Threshold sensitivity sweep
# ============================================================
def report_threshold_sweep(
    orch: RetrievalOrchestrator, query: str, domain: str,
) -> None:
    """掃不同 min_score 看會抽到多少 doc — 給 tuning 用。"""
    print("\n" + "=" * 60)
    print(f"PART 3:Threshold sweep(query={query!r}, domain={domain})")
    print("=" * 60)
    qv = orch.ep.embed_one(query)
    thresholds = [0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50]
    print(f"\n  min_score | schema_hit | kpi_hit | few_shot_hit")
    print(f"  ----------|------------|---------|-------------")
    for t in thresholds:
        out = {}
        for slot_name, idx_name in [
            ("schema", "schema_index"),
            ("kpi", "kpi_index"),
            ("few_shot", "few_shot_index"),
        ]:
            try:
                hits = orch.repo.search(
                    idx_name, qv, top_k=5,
                    filter={"domain": domain} if slot_name != "anti_pattern" else None,
                    min_score=t,
                )
                out[slot_name] = len(hits)
            except Exception:
                out[slot_name] = -1
        print(f"  {t:.2f}      | {out['schema']:>10d} | {out['kpi']:>7d} | "
              f"{out['few_shot']:>12d}")

    print(f"\n  建議:取「schema/kpi 至少抽 2-3 個」的最大 threshold,")
    print(f"        通常落在 0.10-0.20 之間。實際 cosine 在小 index 上偏低。")


def main() -> int:
    parser = argparse.ArgumentParser(description="RAG retrieval diagnostic")
    parser.add_argument("--persist-dir", default="./rag_indices")
    parser.add_argument("--domain", default="tflex")
    parser.add_argument("--query", default=None,
                         help="只跑單一 query(預設跑內建 6 條)")
    parser.add_argument("--skip-sweep", action="store_true",
                         help="跳過 Part 3 threshold sweep")
    args = parser.parse_args()

    print(f"RAG persist dir:{args.persist_dir}")
    print(f"Domain         :{args.domain}")

    # ── 初始化 ──
    try:
        repo = RAGIndexRepository(
            backend_factory=make_chroma_factory(args.persist_dir),
        )
        ep = get_embedding_pipeline()
        print(f"Embedder       :{ep.model_name}(dim={ep.dim})")
    except Exception as e:
        print(f"❌ Init failed:{e}")
        return 1

    orch = RetrievalOrchestrator(rag_repo=repo, embedding_pipeline=ep)

    # ── Part 1 ──
    counts = report_inventory(repo)
    if all(c <= 0 for c in counts.values()):
        print("\n❌ 所有 index 都是空的 — 先跑 scripts/build_rag_indices.py --full-rebuild")
        return 1

    # ── Part 2 ──
    queries = [args.query] if args.query else DEFAULT_QUERIES
    report_retrieve(orch, queries, args.domain)

    # ── Part 3 ──
    if not args.skip_sweep:
        sample_q = args.query or DEFAULT_QUERIES[0]
        report_threshold_sweep(orch, sample_q, args.domain)

    # ── Summary ──
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print("如果 PART 2 大多 phase 都顯示 'inject NOTHING':")
    print("  → RAG silent。下一步 Step 3:降 min_score(retrieval_orchestrator.py)")
    print("如果 PART 2 抽到一堆 noise(無關內容):")
    print("  → RAG noisy。要重建 index 或調 content builder")
    print("如果 PART 2 抽得很準:")
    print("  → 不是 RAG 問題,是 prompt 結合 RAG 後 LLM 反而被 confused")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
