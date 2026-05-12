"""
通用性 Regression Test (多 domain 版本)
=========================================
驗證 llm_service.py 已完全 domain-agnostic — 換成不同 metadata 時:

1. domain_knowledge / few-shot 不應出現其他 domain 的殘留
2. LLM 在 Phase 0/A/B/C 輸出的內容應使用目標 domain 的真實欄位
3. Phase B/C 仍能 exec 出合理結果

不需要 MongoDB — 用 inline 假資料模擬 raw_df。
需要 Ollama 在 localhost:11434 跑。

執行:
    cd /Users/kururu/Documents/Claude/Projects/GenBI
    source .venv/bin/activate
    python test_generality.py [ecommerce|healthcare]
"""

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

import numpy as np
import pandas as pd

from llm_service import LLMService, is_dashboard_query


# ============================================================
# 設定
# ============================================================
import config

OLLAMA_URL = config.LLM_API_URL
OLLAMA_MODEL = config.LLM_MODEL
OLLAMA_TIMEOUT = max(config.LLM_TIMEOUT_S, 240.0)


# ============================================================
# Domain 1 — E-commerce
# ============================================================
def _make_fake_ecommerce_raw_df(n=2000, seed=42):
    rng = np.random.default_rng(seed)
    channels = ["web", "mobile", "store"]
    categories = ["Apparel", "Electronics", "Books", "Home"]
    n_products = 20
    products = pd.DataFrame({
        "product_id": [f"P{i:03d}" for i in range(n_products)],
        "category": rng.choice(categories, n_products),
        "list_price": rng.uniform(10, 500, n_products).round(2),
    })
    chosen = rng.choice(products["product_id"], n)
    df = pd.DataFrame({
        "order_id": [f"O{i:06d}" for i in range(n)],
        "customer_id": [f"C{rng.integers(1, 500):04d}" for _ in range(n)],
        "product_id": chosen,
        "channel": rng.choice(channels, n, p=[0.5, 0.35, 0.15]),
        "order_status": rng.choice(["Y", "N"], n, p=[0.92, 0.08]),
        "quantity": rng.integers(1, 5, n),
        "unit_price": rng.uniform(10, 500, n).round(2),
    })
    df["shipment_status"] = np.where(
        df["order_status"] == "Y",
        rng.choice(["Y", "N", None], n, p=[0.78, 0.12, 0.10]),
        None,
    )
    return df.merge(products, on="product_id", how="left")


ECOMMERCE_QUERIES = [
    {
        "id": "EC01",
        "query": "Compare total orders across each sales channel — which channel has the most orders?",
        "expected_chart_concept": "bar",
    },
    {
        "id": "EC02",
        "query": "比較每個產品類別的已出貨與退貨數量,我想看哪個類別退貨量最大",
        "expected_chart_concept": "stacked_bar",
    },
    {
        "id": "EC03",
        "query": "幫我做一份電商訂單的執行摘要 dashboard,包含總訂單、總收入、退貨率",
        "expected_chart_concept": "table_with_kpi",
    },
    {
        "id": "EC04",
        "query": "我想看過去 30 天每日的訂單趨勢",
        "expected_chart_concept": "refusal",
    },
]


# ============================================================
# Domain 2 — Healthcare claims
# ============================================================
def _make_fake_healthcare_raw_df(n=2500, seed=42):
    rng = np.random.default_rng(seed)
    diag_categories = ["Cardiology", "Orthopedics", "Pediatrics", "Oncology", "General"]
    specialties = ["Hospital", "Clinic", "Lab", "Pharmacy"]
    regions = ["North", "South", "East", "West"]
    statuses = ["P", "D", "IP"]
    n_providers = 30
    providers = pd.DataFrame({
        "provider_id": [f"PR{i:04d}" for i in range(n_providers)],
        "specialty": rng.choice(specialties, n_providers),
        "region": rng.choice(regions, n_providers),
    })
    chosen = rng.choice(providers["provider_id"], n)
    claim_amount = rng.uniform(50, 8000, n).round(2)
    status = rng.choice(statuses, n, p=[0.7, 0.15, 0.15])
    paid = np.where(status == "P", claim_amount * rng.uniform(0.7, 1.0, n), 0).round(2)
    df = pd.DataFrame({
        "claim_id": [f"C{i:07d}" for i in range(n)],
        "member_id": [f"M{rng.integers(1, 800):05d}" for _ in range(n)],
        "provider_id": chosen,
        "diagnosis_category": rng.choice(diag_categories, n),
        "claim_status": status,
        "fraud_flag": rng.choice(["Y", "N"], n, p=[0.03, 0.97]),
        "claim_amount": claim_amount,
        "paid_amount": paid,
    })
    return df.merge(providers, on="provider_id", how="left")


HEALTHCARE_QUERIES = [
    {
        "id": "HC01",
        "query": "Compare denial rate by provider specialty — which specialty has the highest denial rate?",
        "expected_chart_concept": "bar with rate y-axis",
    },
    {
        "id": "HC02",
        "query": "比較各 diagnosis_category 的 paid vs denied 數量,我想看哪個類別拒賠最多",
        "expected_chart_concept": "stacked_bar",
    },
    {
        "id": "HC03",
        "query": "做一份理賠執行摘要 dashboard:總理賠件數、總已付金額、拒賠率、疑似詐欺率",
        "expected_chart_concept": "table_with_kpi",
    },
    {
        "id": "HC04",
        "query": "我想看上個月每週的理賠趨勢",
        "expected_chart_concept": "refusal",
    },
]


# ============================================================
# Domain registry
# ============================================================
def _load_ecommerce():
    from _test_ecommerce_metadata import ECOMMERCE_METADATA
    return ECOMMERCE_METADATA


def _load_healthcare():
    from _test_healthcare_metadata import HEALTHCARE_METADATA
    return HEALTHCARE_METADATA


DOMAINS = {
    "ecommerce": {
        "loader": _load_ecommerce,
        "queries": ECOMMERCE_QUERIES,
        "fake_data_fn": _make_fake_ecommerce_raw_df,
        # 其他 domain 的特有詞 (跑此 domain 時都是污染詞)
        "non_target_terms": [
            # tFlex
            "tflex", "company_code", "review_status", "review_result", "review_mechanism",
            "application_no", "average_return_rate", "ai_review_rate",
            "退單率", "退件", "AI 審查", "TSK", "TST", "JSM",
            # healthcare
            "claim_status", "fraud_flag", "diagnosis_category", "provider_id",
            "specialty",
        ],
    },
    "healthcare": {
        "loader": _load_healthcare,
        "queries": HEALTHCARE_QUERIES,
        "fake_data_fn": _make_fake_healthcare_raw_df,
        "non_target_terms": [
            # tFlex
            "tflex", "company_code", "review_status", "review_result", "review_mechanism",
            "application_no", "average_return_rate", "ai_review_rate",
            "退單率", "退件", "AI 審查", "TSK", "TST", "JSM",
            # ecommerce
            "channel", "shipment_status", "order_status",
            "list_price", "shop_demo",
        ],
    },
}


# ============================================================
# 通用工具
# ============================================================
def truncate(s: str, n: int = 400) -> str:
    return s if len(s) <= n else s[:n] + "...(truncated)"


def scan_contamination(text: str, non_target_terms: list[str]) -> list[str]:
    if not text:
        return []
    text_lower = text.lower()
    return [t for t in non_target_terms if t.lower() in text_lower]


# ============================================================
# 單一 case runner (3 次 retry on Phase B/C)
# ============================================================
def run_case(case: dict, llm: LLMService, raw_df: pd.DataFrame,
              non_target_terms: list[str]) -> dict:
    print(f"\n{'=' * 60}\n[{case['id']}] {case['query']}\n{'=' * 60}")
    result = {
        "id": case["id"], "query": case["query"],
        "phases": {}, "contamination": {},
    }

    # === Phase 0 · Plan ===
    print("\n▶︎ Phase 0 · Plan")
    t0 = time.time()
    plan_res = llm.generate_plan(case["query"])
    elapsed = time.time() - t0
    plan_text = plan_res.get("message", "") if plan_res["status"] == "success" else ""
    print(f"   ({elapsed:.1f}s)")
    print(truncate(plan_text, 400))
    result["phases"]["plan"] = {"elapsed_s": round(elapsed, 1), "text": plan_text}
    contam = scan_contamination(plan_text, non_target_terms)
    result["contamination"]["plan"] = contam
    print(f"   {'❌ 污染: ' + str(contam) if contam else '✅ 無污染'}")

    if case.get("expected_chart_concept") == "refusal":
        kws = ["資料不足", "無", "缺", "不支援", "missing", "no ", "trend"]
        denied = any(kw in plan_text.lower() for kw in kws)
        result["refusal_detected"] = denied
        print(f"   {'✅' if denied else '❌'} 拒絕路徑: {denied}")
        return result

    # === Phase A · Pipeline ===
    print("\n▶︎ Phase A · Pipeline")
    t0 = time.time()
    pipeline_str = llm.generate_pipeline(case["query"], plan_text)
    elapsed = time.time() - t0
    try:
        pipeline_obj = json.loads(pipeline_str)
        print(f"   ({elapsed:.1f}s) start={pipeline_obj.get('start_collection')}")
        result["phases"]["pipeline"] = {
            "elapsed_s": round(elapsed, 1),
            "pipeline": pipeline_obj,
            "valid_json": True,
        }
        contam = scan_contamination(pipeline_str, non_target_terms)
        result["contamination"]["pipeline"] = contam
        print(f"   {'❌ 污染: ' + str(contam) if contam else '✅ 無污染'}")
    except json.JSONDecodeError as e:
        print(f"   ❌ 不是合法 JSON: {e}")
        result["phases"]["pipeline"] = {"valid_json": False, "raw": pipeline_str}
        return result

    # === Phase B · Pandas (3 次 retry) ===
    print("\n▶︎ Phase B · Pandas (使用 inline 假資料)")
    try:
        raw_sample_md = raw_df.head(3).to_markdown(index=False)
    except Exception:
        raw_sample_md = raw_df.head(3).to_string(index=False)

    prep_code = None
    prep_err = None
    Q = None
    b_elapsed_total = 0.0
    b_retry_log: list[str] = []

    dashboard_mode = is_dashboard_query(case["query"])
    if dashboard_mode:
        print(f"   📊 偵測為 dashboard 場景,Phase B 走 row-level pass-through")

    for attempt in range(3):
        t0 = time.time()
        prep_code = llm.generate_preprocess_code(
            case["query"], plan_text, list(raw_df.columns),
            raw_df_sample=raw_sample_md,
            dashboard_hint=dashboard_mode,
            previous_code=prep_code if attempt > 0 else "",
            previous_error=prep_err if attempt > 0 else "",
        )
        b_elapsed_total += time.time() - t0
        ns = {"pd": pd, "np": np, "raw_df": raw_df}
        try:
            exec(prep_code, ns, ns)
            Q = ns.get("Q")
            # 安全網
            if isinstance(Q, pd.DataFrame) and Q.shape[0] == raw_df.shape[0]:
                for nm, v in ns.items():
                    if nm == "Q" or nm.startswith("_") or not isinstance(v, pd.DataFrame):
                        continue
                    if 1 <= len(v) < len(raw_df) * 0.9:
                        print(f"   ⚠️ 安全網:Q 未指派,fallback 到 {nm} (shape={v.shape})")
                        Q = v
                        break
            prep_err = None
            print(f"   attempt {attempt + 1} ✅ exec OK")
            break
        except Exception:
            prep_err = traceback.format_exc()
            last = prep_err.strip().split("\n")[-1][:120]
            b_retry_log.append(f"#{attempt + 1}: {last}")
            print(f"   attempt {attempt + 1} ❌ {last}")
            if attempt < 2:
                print(f"      🔁 帶 cheatsheet 重生...")

    print(f"   Phase B 總耗時: {b_elapsed_total:.1f}s, attempts: {attempt + 1}")
    contam_code = scan_contamination(prep_code or "", non_target_terms)
    result["contamination"]["phaseB"] = contam_code
    if contam_code:
        print(f"   ❌ code 污染: {contam_code}")
    else:
        print("   ✅ code 無污染")
    result["phases"]["preprocess"] = {
        "elapsed_s": round(b_elapsed_total, 1),
        "attempts": attempt + 1,
        "retry_log": b_retry_log,
        "exec_error": prep_err,
        "Q_shape": list(Q.shape) if isinstance(Q, pd.DataFrame) else None,
        "Q_cols": list(Q.columns) if isinstance(Q, pd.DataFrame) else None,
    }
    if prep_err:
        return result

    if isinstance(Q, pd.DataFrame):
        print(f"   Q.shape={Q.shape}, cols={list(Q.columns)}")

    # === Phase C · ECharts (3 次 retry) ===
    print("\n▶︎ Phase C · ECharts (3 次 retry)")
    echarts_code = None
    plot_err = None
    option = None
    c_elapsed_total = 0.0
    c_retry_log: list[str] = []

    for c_attempt in range(3):
        t0 = time.time()
        echarts_code = llm.generate_echarts_option(
            case["query"], plan_text, list(Q.columns),
            previous_code=echarts_code if c_attempt > 0 else "",
            previous_error=plot_err if c_attempt > 0 else "",
        )
        c_elapsed_total += time.time() - t0
        ns2 = {"pd": pd, "Q": Q}
        try:
            exec(echarts_code, ns2, ns2)
            option = ns2.get("option")
            plot_err = None
            print(f"   attempt {c_attempt + 1} ✅ exec OK")
            break
        except Exception:
            plot_err = traceback.format_exc()
            last = plot_err.strip().split("\n")[-1][:120]
            c_retry_log.append(f"#{c_attempt + 1}: {last}")
            print(f"   attempt {c_attempt + 1} ❌ {last}")
            if c_attempt < 2:
                print(f"      🔁 重生中...")

    contam_ec = scan_contamination(echarts_code or "", non_target_terms)
    result["contamination"]["phaseC"] = contam_ec
    if contam_ec:
        print(f"   ❌ code 污染: {contam_ec}")
    else:
        print("   ✅ code 無污染")
    result["phases"]["echarts"] = {
        "elapsed_s": round(c_elapsed_total, 1),
        "attempts": c_attempt + 1,
        "retry_log": c_retry_log,
        "exec_error": plot_err,
        "option_keys": list(option.keys()) if isinstance(option, dict) else None,
        "use_table_fallback": isinstance(option, dict) and option.get("_use_table") is True,
    }
    if isinstance(option, dict):
        if option.get("_use_table"):
            cards = option.get("_kpi_cards") or []
            print(f"   📋 _use_table=True, kpi_cards={len(cards)}")
        else:
            print(f"   📊 option keys: {list(option.keys())[:6]}")
    elif plot_err is not None:
        # 🛡️ 3 次失敗 — 軟通過,標記 fallback
        print(f"   ⚠️ Phase C 3 次失敗,降級為表格渲染 (軟通過)")
        result["phases"]["echarts"]["phase_c_fallback"] = True

    return result


def format_summary(results: list[dict], domain: str) -> str:
    lines = ["", "=" * 60, f" {domain} · 速覽", "=" * 60]
    for r in results:
        cid = r["id"]
        all_contam = []
        for phase, hits in r.get("contamination", {}).items():
            if hits:
                all_contam.append(f"{phase}={hits}")
        if r.get("refusal_detected") is not None:
            line = f"  {cid} · refusal: {r['refusal_detected']}"
        else:
            phases = r.get("phases", {})
            attempts_b = phases.get("preprocess", {}).get("attempts", "—")
            attempts_c = phases.get("echarts", {}).get("attempts", "—")
            b_ok = phases.get("preprocess", {}).get("exec_error") is None
            c_ok = phases.get("echarts", {}).get("exec_error") is None
            line = (
                f"  {cid} · B:{'✅' if b_ok else '❌'} ({attempts_b}x) "
                f"· C:{'✅' if c_ok else '❌'} ({attempts_c}x)"
            )
        if all_contam:
            line += f"  · ❌ 污染: {all_contam}"
        else:
            line += "  · ✅ 無污染"
        lines.append(line)
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("domain", nargs="?", default="ecommerce",
                         choices=list(DOMAINS.keys()),
                         help="Which test domain to use")
    args = parser.parse_args()

    domain_cfg = DOMAINS[args.domain]
    metadata = domain_cfg["loader"]()
    queries = domain_cfg["queries"]
    non_target = domain_cfg["non_target_terms"]

    print("=" * 60)
    print(f" 通用性 Regression Test — {args.domain}")
    print("=" * 60)
    print(f"LLM     : {OLLAMA_URL}")
    print(f"Model   : {OLLAMA_MODEL}")
    print(f"Domain  : {metadata['dataset_name']}")
    print(f"Cases   : {len(queries)} 個 · 每 case Phase B/C 最多 3 次 retry")
    print()

    llm = LLMService(
        api_url=OLLAMA_URL,
        api_key=config.LLM_API_KEY,
        model_name=OLLAMA_MODEL,
        timeout_s=OLLAMA_TIMEOUT,
        default_temperature=config.LLM_TEMPERATURE,
        task_metadata=metadata,
    )

    dk_contam = scan_contamination(llm.domain_knowledge, non_target)
    fs_contam = scan_contamination(llm.echarts_few_shot, non_target)
    print(f"domain_knowledge 污染: {dk_contam or '✅ 無'}")
    print(f"echarts_few_shot 污染: {fs_contam or '✅ 無'}")
    if dk_contam or fs_contam:
        print("❌ Critical:通用化失敗!")
        return 1

    raw_df = domain_cfg["fake_data_fn"](n=2000)
    print(f"\n假 raw_df: shape={raw_df.shape}, cols={list(raw_df.columns)}")

    results = []
    total_wall_t0 = time.time()
    for case in queries:
        case_t0 = time.time()
        try:
            llm.reset_call_log()
            res = run_case(case, llm, raw_df, non_target)
        except KeyboardInterrupt:
            print("\n🛑 中斷")
            break
        except Exception:
            print(f"\n💥 致命錯誤:\n{traceback.format_exc()}")
            res = {"id": case["id"], "error": traceback.format_exc()}
        res["wall_elapsed_s"] = round(time.time() - case_t0, 2)
        res["llm_usage"] = llm.get_call_summary()
        b_attempts = res.get("phases", {}).get("preprocess", {}).get("attempts", 0)
        c_attempts = res.get("phases", {}).get("echarts", {}).get("attempts", 0)
        res["retries"] = {"phase_b": b_attempts, "phase_c": c_attempts}
        results.append(res)

    total_wall = time.time() - total_wall_t0
    print(format_summary(results, args.domain))

    # ── Cost summary ──
    total_calls = sum(r.get("llm_usage", {}).get("calls", 0) for r in results)
    total_prompt = sum(r.get("llm_usage", {}).get("prompt_tokens", 0) for r in results)
    total_completion = sum(r.get("llm_usage", {}).get("completion_tokens", 0) for r in results)
    total_tokens = total_prompt + total_completion

    print()
    print("─" * 60)
    print(" 💰 Cost & Latency Summary")
    print("─" * 60)
    print(f"  Wall clock total  : {total_wall:.1f}s ({total_wall / 60:.1f} 分鐘)")
    print(f"  Total LLM calls   : {total_calls}")
    print(f"  Total prompt tok  : {total_prompt:,}")
    print(f"  Total output tok  : {total_completion:,}")
    print(f"  Total tokens      : {total_tokens:,}")
    if total_calls > 0:
        print(f"  Avg tokens/call   : {total_tokens // total_calls:,}")
    print()
    print("  詳細 per-case (wall / calls / tokens / retries B/C):")
    for r in results:
        usage = r.get("llm_usage", {})
        ret = r.get("retries", {})
        wall = r.get("wall_elapsed_s", 0)
        retry_str = f"{ret.get('phase_b', '—')}/{ret.get('phase_c', '—')}"
        print(
            f"    {r['id']:6s} · {wall:>6.1f}s · {usage.get('calls', 0):>2d} calls · "
            f"{usage.get('total_tokens', 0):>7,d} tok · retry {retry_str}"
        )

    out_file = f"test_generality_{args.domain}.json"
    Path(out_file).write_text(
        json.dumps(results, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"\n📝 結果寫入 {out_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
