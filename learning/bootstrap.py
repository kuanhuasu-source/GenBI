"""
learning/bootstrap.py — Week 1 · Self-learning MVP

Hybrid warm-start:把 GenBI v0.3.x–v0.7.x 累積的 13 條 historical hotfix
seed 進 `learning_instincts` collection。讓 self-learning 系統第一天就有
基準 instincts 可參考 / contradict / consolidate against。

Idempotent:多次執行不會重複新增(用 `instinct_id` upsert)。

執行方式:
    # Via admin CLI
    python -m learning.bootstrap

    # 或在 Streamlit/admin page 內呼叫
    from learning.bootstrap import seed_all
    seed_all(db)

每條 seed 預設:
    source = "historical_seed"
    confidence = 0.95
    evidence_count = 10
    status = "active"

對應 spec §8.1(GenBI_v1.3_Self_Learning_MVP_Implementation_Spec.md)。
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import config


# ============================================================
# 13 條 Historical Seed Instincts(對齊 GenBI v0.3.x–v0.7.x hotfix)
# ============================================================

HISTORICAL_SEEDS: list[dict[str, Any]] = [
    # ─────────────────────────────────────────────
    # Phase A — MongoDB Pipeline (3 條)
    # ─────────────────────────────────────────────
    {
        "instinct_id": "INST-SEED-001",
        "name": "strip_derived_expressions",
        "rule": "Phase A $project/$addFields/$set 內含派生表達式($cond/$divide/"
                "$multiply/$add/$subtract 等)的欄位必須被自動 strip,讓 Phase B "
                "用 pandas 重算。Phase A 只負責撈資料,不做計算。",
        "phase": "phase_a",
        "error_class": "unsupported_operator",
        "tags": ["phase_a", "mongodb", "unsupported_operator", "structural_defense"],
        "implementation": "sanitize_pipeline() in llm_service.py",
        "version_source": "v0.4.1",
    },
    {
        "instinct_id": "INST-SEED-002",
        "name": "defensive_json_extraction",
        "rule": "若 LLM 回應夾雜 preamble / markdown fence / code block wrapper,"
                "用 balanced-brace parser 提取第一個合法 JSON object。不要對 "
                "model behavior 做假設,純文字 parsing 救回。",
        "phase": "phase_a",
        "error_class": "json_parse",
        "tags": ["phase_a", "json_parse", "structural_defense", "robustness"],
        "implementation": "extract_json_block() in llm_service.py",
        "version_source": "v0.3.6",
    },
    {
        "instinct_id": "INST-SEED-003",
        "name": "phase_a_retry_with_error_feedback",
        "rule": "Phase A JSON parse 失敗時,把錯誤訊息 + 嚴格 'JSON only' 指令塞回 "
                "user message retry。最多 3 attempts。",
        "phase": "phase_a",
        "error_class": "json_parse",
        "tags": ["phase_a", "json_parse", "retry", "robustness"],
        "implementation": "generate_pipeline() retry loop in llm_service.py",
        "version_source": "v0.3.6",
    },

    # ─────────────────────────────────────────────
    # Phase B — Pandas Preprocess (3 條)
    # ─────────────────────────────────────────────
    {
        "instinct_id": "INST-SEED-004",
        "name": "series_to_dataframe_safety_net",
        "rule": "若 Phase B 產出 Q 為 pandas.Series(LLM 漏 reset_index()),自動 "
                "to_frame().reset_index() 轉回 DataFrame,避免下游 KeyError。",
        "phase": "phase_b",
        "error_class": "type_mismatch",
        "tags": ["phase_b", "type_mismatch", "structural_defense"],
        "implementation": "Series safety net in app.py / test_runner.py",
        "version_source": "v0.3.6",
    },
    {
        "instinct_id": "INST-SEED-005",
        "name": "forbid_import_in_phase_b",
        "rule": "Phase B 禁止 import 任何套件(matplotlib / plotly / seaborn 等);"
                "pd / np 已備好。LLM 看到「畫出 / stacked」query 不該幻覺 import "
                "plot library。Phase B 只資料處理,不畫圖。",
        "phase": "phase_b",
        "error_class": "import_forbidden",
        "tags": ["phase_b", "import_forbidden", "prompt_rule"],
        "implementation": "_PHASE_B_HEADER_TEMPLATE_V6 rule 1 (embedded_prompts.py)",
        "version_source": "v0.7.1",
    },
    {
        "instinct_id": "INST-SEED-006",
        "name": "forbid_phase_b_replay_raw_df",
        "rule": "Q 是 Phase B 終態,不要再從 raw_df 級欄位重算(會 KeyError 因 "
                "Phase A 已 $project 過)。Phase B 用 raw_df 的 schema 直接做 "
                "groupby/agg,不要嘗試重新派生 boolean flag。",
        "phase": "phase_b",
        "error_class": "column_missing",
        "tags": ["phase_b", "column_missing", "prompt_rule"],
        "implementation": "_PHASE_B_HEADER_TEMPLATE_V6 rule 3.1 (embedded_prompts.py)",
        "version_source": "v0.7.1",
    },

    # ─────────────────────────────────────────────
    # Phase C — ECharts (5 條)
    # ─────────────────────────────────────────────
    {
        "instinct_id": "INST-SEED-007",
        "name": "coerce_numpy_to_native",
        "rule": "ECharts option dict 內所有 numpy.int64 / numpy.float64 / NaN / "
                "pandas.Timestamp / pandas.Timedelta 強制 cast 成 Python native "
                "(int / float / str / None),避免 BidiComponent serializer 把 "
                "numpy.int64 序列化為 JS null → Object.keys(null) 炸。",
        "phase": "phase_c",
        "error_class": "numpy_serialization",
        "tags": ["phase_c", "numpy_serialization", "structural_defense"],
        "implementation": "coerce_option_native_types() in llm_service.py",
        "version_source": "v0.4.6",
    },
    {
        "instinct_id": "INST-SEED-008",
        "name": "rescue_empty_echarts",
        "rule": "若 LLM 產空殼 option(xAxis.data=[] / series=[] / 所有 series.data "
                "都空),從 Q 自動 pivot 補回 series。支援 long format(2+ dims + "
                "1+ numeric)與 wide format(1 dim + N numerics)兩條路徑。",
        "phase": "phase_c",
        "error_class": "empty_shell",
        "tags": ["phase_c", "empty_shell", "structural_defense"],
        "implementation": "rescue_empty_echarts() in llm_service.py",
        "version_source": "v0.4.7",
    },
    {
        "instinct_id": "INST-SEED-009",
        "name": "rescue_in_except_path",
        "rule": "Phase C exec 失敗(KeyError 等)時,仍檢查 namespace['option'] 是否 "
                "為 dict;若是,從半殘空殼 state 也嘗試 rescue。救得回就跳出 retry,"
                "視為成功(toast 提示「從半殘空殼救回」)。",
        "phase": "phase_c",
        "error_class": "empty_shell",
        "tags": ["phase_c", "empty_shell", "structural_defense", "retry"],
        "implementation": "app.py + test_runner.py except path",
        "version_source": "v0.4.7",
    },
    {
        "instinct_id": "INST-SEED-010",
        "name": "dual_axis_force_route",
        "rule": "若 query 同時含「絕對量(件數/數量)」+「比率(率/比例/%)」+ "
                "「比較性副詞(比較/同時看到/vs)」,必走 dual-axis bar+line"
                "(yAxisIndex 0 = bar 左軸,yAxisIndex 1 = line 右軸)。**不要** "
                "走 _use_table / _kpi_cards(會丟掉跨實體的差異)。",
        "phase": "phase_c",
        "error_class": "wrong_chart_routing",
        "tags": ["phase_c", "line_dual", "prompt_rule", "routing"],
        "implementation": "_PHASE_C_BLOCK_LINE_DUAL (embedded_prompts.py) + "
                          "_detect_chart_intent() in llm_service.py",
        "version_source": "v0.4.2",
    },
    {
        "instinct_id": "INST-SEED-011",
        "name": "forbid_empty_shell_dynamic_fill",
        "rule": "Phase C 禁止「先宣告空殼 option(xAxis.data=[]、series=[])再 "
                "dynamic fill」anti-pattern。常因 LLM 接著重做 Phase B 的事而 "
                "KeyError 炸。改一次 option literal 寫完。",
        "phase": "phase_c",
        "error_class": "empty_shell",
        "tags": ["phase_c", "empty_shell", "prompt_rule", "anti_pattern"],
        "implementation": "_PHASE_C_HEADER_TEMPLATE rule 3.1 (embedded_prompts.py)",
        "version_source": "v0.4.7",
    },

    # ─────────────────────────────────────────────
    # Phase 0 — Plan (1 條)
    # ─────────────────────────────────────────────
    {
        "instinct_id": "INST-SEED-012",
        "name": "chart_word_not_refuse_trigger",
        "rule": "「圓餅圖 / pie / bar / heatmap / scatter / stacked」等圖型詞 "
                "**完全不參與** refuse 判斷。它們只是呈現方式,跟資料維度無關。"
                "判斷拒絕只看 query 要算什麼 KPI,不看要怎麼呈現。",
        "phase": "phase_0",
        "error_class": "false_positive_refusal",
        "tags": ["phase_0", "refusal", "prompt_rule", "false_positive"],
        "implementation": "_PHASE_0_PLAN_TEMPLATE Step 1 (embedded_prompts.py)",
        "version_source": "v0.4.3",
    },

    # ─────────────────────────────────────────────
    # Cross-Phase / Meta (1 條)
    # ─────────────────────────────────────────────
    {
        "instinct_id": "INST-SEED-013",
        "name": "prompt_invariants_enforcement",
        "rule": "對每個 phase prompt(含所有 intent 變體)用 sentinel-based "
                "invariants 檢查 critical rules 不缺;重構時若漏接 critical rule "
                "(例:v0.6.0 漏接 Phase B「禁止 import」),自動偵測並 fail CI。",
        "phase": "meta",
        "error_class": "regression_protection",
        "tags": ["meta", "regression_protection", "structural_defense", "ci"],
        "implementation": "scripts/check_prompt_invariants.py",
        "version_source": "v0.7.2",
    },
]


# ============================================================
# Bootstrap function
# ============================================================
def _build_seed_doc(seed: dict, now: datetime) -> dict:
    """把 raw seed 轉成 learning_instincts collection 格式。"""
    return {
        "instinct_id": seed["instinct_id"],
        "name": seed["name"],
        "rule": seed["rule"],
        "scope": "project",
        "domain": "tflex",  # 預設 tflex,future 跨 domain 由 consolidation 升 "global"
        "phase": seed["phase"],
        "error_class": seed["error_class"],
        "tags": seed["tags"],

        # Bootstrap metadata
        "source": "historical_seed",
        "version_source": seed["version_source"],
        "implementation": seed["implementation"],

        # Standard fields
        "confidence": 0.95,
        "evidence_count": 10,
        "contradiction_count": 0,
        "supporting_observation_ids": [],
        "status": "active",
        "created_at": now,
        "updated_at": now,
    }


def seed_all(db, *, dry_run: bool = False, verbose: bool = True) -> dict:
    """
    Idempotent upsert:把 13 條 historical seed 寫進 learning_instincts。

    Args:
        db: pymongo Database instance
        dry_run: 只印出會做什麼,不真的寫 DB
        verbose: 印出 per-seed 結果

    Returns:
        {"inserted": N, "updated": N, "skipped": N, "total": 13}
    """
    if db is None:
        raise ValueError("db is required (pass pymongo Database instance)")

    coll = db["learning_instincts"]
    now = datetime.now(timezone.utc)
    stats = {"inserted": 0, "updated": 0, "skipped": 0, "total": len(HISTORICAL_SEEDS)}

    for seed in HISTORICAL_SEEDS:
        doc = _build_seed_doc(seed, now)
        existing = coll.find_one({"instinct_id": doc["instinct_id"]})

        if dry_run:
            action = "would update" if existing else "would insert"
            if verbose:
                print(f"  [{action}] {doc['instinct_id']} · {doc['name']}")
            stats["inserted" if not existing else "updated"] += 1
            continue

        if existing:
            # Preserve evidence_count/contradiction_count if已累積過(讓 seed 不
            # 覆蓋 production 已 evolve 的 stats)
            if existing.get("source") == "historical_seed":
                # 純 seed 沒被改過 → 覆蓋(刷新 rule 內容,以防 rule 文字微調)
                coll.replace_one({"_id": existing["_id"]}, {**doc, "_id": existing["_id"]})
                stats["updated"] += 1
                if verbose:
                    print(f"  ✏️  updated  {doc['instinct_id']} · {doc['name']}")
            else:
                # Production 已 promote / 改過 → skip,避免覆蓋人工調整
                stats["skipped"] += 1
                if verbose:
                    print(f"  ⏭️  skipped  {doc['instinct_id']} · "
                          f"{doc['name']}(已被 production 修改,不覆蓋)")
        else:
            coll.insert_one(doc)
            stats["inserted"] += 1
            if verbose:
                print(f"  ✅ inserted {doc['instinct_id']} · {doc['name']}")

    return stats


def _ensure_indexes(db, *, verbose: bool = True) -> None:
    """確保 learning_instincts 必要 index 存在(idempotent)。"""
    coll = db["learning_instincts"]
    coll.create_index("instinct_id", unique=True)
    coll.create_index("status")
    coll.create_index("domain")
    coll.create_index("phase")
    coll.create_index([("tags", 1)])
    if verbose:
        print("  ✅ indexes ensured on learning_instincts")


# ============================================================
# CLI entry
# ============================================================
def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="Seed historical hotfix rules into learning_instincts"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would happen without writing DB")
    parser.add_argument("--skip-indexes", action="store_true",
                        help="Skip index creation")
    args = parser.parse_args()

    print("═" * 70)
    print("  GenBI Self-Learning · Historical Hotfix Bootstrap")
    print("═" * 70)
    print()

    try:
        from pymongo import MongoClient
        client = MongoClient(
            config.MONGO_URI,
            serverSelectionTimeoutMS=config.MONGO_SERVER_SELECTION_TIMEOUT_MS,
        )
        client.admin.command("ping")
        db = client[config.MONGO_DB]
        print(f"  Connected: {config.MONGO_DB}")
    except Exception as e:
        print(f"  ❌ MongoDB 連線失敗:{e}")
        return 1

    if not args.skip_indexes and not args.dry_run:
        print("\nEnsuring indexes...")
        _ensure_indexes(db)

    mode = " (DRY RUN)" if args.dry_run else ""
    print(f"\nSeeding {len(HISTORICAL_SEEDS)} historical instincts{mode}...\n")
    stats = seed_all(db, dry_run=args.dry_run, verbose=True)

    print()
    print("─" * 70)
    print(f"  Inserted: {stats['inserted']}")
    print(f"  Updated:  {stats['updated']}")
    print(f"  Skipped:  {stats['skipped']}")
    print(f"  Total:    {stats['total']}")
    print("─" * 70)
    print()

    if stats["inserted"] + stats["updated"] == stats["total"]:
        print("✅ Bootstrap complete.")
    else:
        print("⚠️  Some seeds were skipped (preserved production overrides).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
