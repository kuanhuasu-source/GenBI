#!/usr/bin/env python3
"""bench_model.py — 量單一 Ollama 模型在 GenBI 4 個 phase 的 per-call 耗時。

Usage:
    python bench_model.py qwen3-coder-next:q4_K_M --profile default
    python bench_model.py qwen36-a3b-12k          --profile reasoning_distilled
    python bench_model.py qwen3-coder:30b         --profile default   # 老 baseline 對照

What it does:
    1. Override env(HRDA_MODEL_NAME / HRDA_MODEL_PROFILE)
    2. 對 3 個代表性 query 各跑 Phase 0/A/B/C
    3. 列 per-phase min / median / max / total
    4. 不打 MongoDB / 不寫 trace,純量耗時

不需要 raw_df,Phase B 用 fake column list 餵進去測 latency 即可
(我們關心的是 LLM 推論時間,不是程式正確性)。
"""

from __future__ import annotations
import argparse
import os
import sys
import time


# 3 個 query 涵蓋 simple / dashboard / stacked,刻意挑會走不同 prompt branch 的
BENCH_QUERIES = [
    "各公司的申請件數比較",                    # simple bar
    "tFlex 全公司儀表板",                      # dashboard with KPI
    "畫橫向 100% 堆疊圖看各公司審核狀態組成",  # stacked horizontal
]


def fmt(t: float) -> str:
    return f"{t:6.1f}s"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("model", help="Ollama model name (例:qwen3-coder:30b)")
    p.add_argument("--profile", default="default",
                   help="HRDA_MODEL_PROFILE (default|reasoning_distilled)")
    p.add_argument("--queries", type=int, default=3,
                   help="跑幾個 query(預設 3,最多 3)")
    p.add_argument("--skip-warmup", action="store_true",
                   help="不跑第 0 次 warmup call(預設會跑一個 throwaway 暖機)")
    args = p.parse_args()

    # 必須在 import config 之前 override env
    os.environ["HRDA_MODEL_NAME"] = args.model
    os.environ["HRDA_MODEL_PROFILE"] = args.profile

    from config import llm_service_kwargs, print_summary, MODEL_PROFILE_NAME
    from llm_service import LLMService

    print_summary()
    print()

    llm = LLMService(**llm_service_kwargs())

    # ── Warmup(讓 cold-load 不要污染第一個 query 的數據)──
    if not args.skip_warmup:
        print("🔥 Warmup(1 個 throwaway plan call,耗時不計)...")
        t0 = time.time()
        try:
            llm.generate_plan("warmup")
        except Exception as e:
            print(f"   warmup 出錯(不影響):{e}")
        print(f"   warmup 完成({time.time() - t0:.1f}s)\n")

    queries = BENCH_QUERIES[: args.queries]
    times = {"plan": [], "pipeline": [], "preprocess": [], "echarts": []}

    for i, q in enumerate(queries, 1):
        print(f"━━━ Query {i}/{len(queries)}: {q[:46]} ━━━")

        # Phase 0: Plan
        t0 = time.time()
        plan_resp = llm.generate_plan(q)
        t = time.time() - t0
        plan_text = plan_resp.get("message", "") if isinstance(plan_resp, dict) else ""
        print(f"  Phase 0 (plan)       : {fmt(t)}")
        times["plan"].append(t)

        # Phase A: Pipeline
        t0 = time.time()
        try:
            llm.generate_pipeline(q, plan_text=plan_text)
            t = time.time() - t0
            print(f"  Phase A (pipeline)   : {fmt(t)}")
            times["pipeline"].append(t)
        except Exception as e:
            print(f"  Phase A (pipeline)   : FAILED ({type(e).__name__}: {e})")

        # Phase B: Preprocess(用 fake cols,只量 LLM 耗時)
        t0 = time.time()
        fake_cols = ["company_name", "apply_date", "review_status", "applicant_id"]
        fake_sample = (
            "| company_name | review_status | apply_date |\n"
            "|---|---|---|\n"
            "| TSMC | completed | 2024-01-15 |\n"
            "| UMC  | pending   | 2024-02-03 |"
        )
        try:
            llm.generate_preprocess_code(q, plan_text, fake_cols, raw_df_sample=fake_sample)
            t = time.time() - t0
            print(f"  Phase B (preprocess) : {fmt(t)}")
            times["preprocess"].append(t)
        except Exception as e:
            print(f"  Phase B (preprocess) : FAILED ({type(e).__name__}: {e})")

        # Phase C: ECharts
        t0 = time.time()
        try:
            llm.generate_echarts_option(q, plan_text, q_columns=["company_name", "count"])
            t = time.time() - t0
            print(f"  Phase C (echarts)    : {fmt(t)}")
            times["echarts"].append(t)
        except Exception as e:
            print(f"  Phase C (echarts)    : FAILED ({type(e).__name__}: {e})")

        print()

    # ── Summary table ──
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  Model   : {args.model}")
    print(f"  Profile : {MODEL_PROFILE_NAME}")
    print(f"  Queries : {len(queries)}")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  {'Phase':<22s}{'min':>8s}{'median':>8s}{'max':>8s}{'total':>9s}")
    grand_total = 0.0
    for ph, ts in times.items():
        if not ts:
            print(f"  {ph:<22s}{'(no data)':>33s}")
            continue
        ts_sorted = sorted(ts)
        med = ts_sorted[len(ts_sorted) // 2]
        total = sum(ts)
        grand_total += total
        print(f"  {ph:<22s}{min(ts):>7.1f}s{med:>7.1f}s{max(ts):>7.1f}s{total:>8.1f}s")
    print("─" * 56)
    print(f"  {'PER-QUERY TOTAL':<22s}"
          f"{'':>24s}{grand_total / max(len(queries), 1):>8.1f}s")
    print(f"  {'GRAND TOTAL':<22s}"
          f"{'':>24s}{grand_total:>8.1f}s")
    print()

    # ── Token cost(累積整段)──
    summary = llm.get_call_summary()
    if summary["calls"]:
        print(f"  📊 LLM calls   : {summary['calls']}")
        print(f"  📊 total tokens: {summary['total_tokens']:,} "
              f"(prompt={summary['prompt_tokens']:,} / completion={summary['completion_tokens']:,})")
        print(f"  📊 tokens/sec  : "
              f"{summary['total_tokens'] / max(summary['total_elapsed_s'], 0.01):.1f}")


if __name__ == "__main__":
    main()
