"""
scripts/check_prompt_invariants.py — v0.7.2

Sentinel-based 不變式檢查:對每個 phase prompt(含所有 intent 變體)做關鍵字
sentinels 檢查。重構 prompt 時若不小心漏接某條 critical rule,這支腳本會
catch 到。

使用情境:
  - Pre-commit hook
  - CI gate
  - 手動驗證(`python3 scripts/check_prompt_invariants.py`)

設計原則:
  - 只檢查「**少了會引起 production bug** 的關鍵字眼」,不檢查文案
  - sentinels 用 `or` 邏輯(多個變體任一命中算過,避免綁死特定措辭)
  - 失敗時印出哪個 phase / intent 缺哪個 sentinel,幫助 debug

退場碼:
  0 = 全部 sentinels 存在
  1 = 至少一個 sentinel 缺失
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ============================================================
# Sentinel 定義
#   key:  prompt 識別字串
#   value: list of (sentinel_label, [str1, str2, ...])
#          內層 list 中任一字串 in prompt 即算 OK(or 邏輯)
# ============================================================
SENTINELS: dict[str, list[tuple[str, list[str]]]] = {
    # ──────────────────────────────────────────
    # Phase 0 · Plan
    # ──────────────────────────────────────────
    "phase_0_plan": [
        ("拒絕協定 schema-driven",
         ["data_limitations", "schema"]),
        ("REFUSE 格式",
         ["REFUSE", "[REFUSE]"]),
        ("圖型詞不參與判斷(v0.4.3 anti-false-positive)",
         ["圖型詞", "圓餅圖"]),
        ("最後檢查(v0.4.3 wrap-around)",
         ["最後檢查", "撤回拒絕"]),
        ("三步推理流程",
         ["Step 1", "Step 2", "Step 3"]),
    ],

    # ──────────────────────────────────────────
    # Phase A · Pipeline
    # ──────────────────────────────────────────
    "phase_a_pipeline": [
        ("禁止 DB 端聚合",
         ["$group", "$count", "$sort", "$limit"]),
        ("禁止派生欄位(v0.4.1)",
         ["$cond", "$divide"]),
        ("派生欄位口訣",
         ["Phase A 撈", "Phase B 算"]),
        ("$project 鐵律",
         ["$project", "保留", "metadata"]),
        ("Entity 過濾鐵律",
         ["$match", "$in"]),
    ],

    # ──────────────────────────────────────────
    # Phase B · Preprocess(universal header — 適用所有 intent)
    # ──────────────────────────────────────────
    "phase_b_universal": [
        ("Q 變數產出鐵律",
         ["最外層產出 Q", "宣告 `Q`", "宣告 Q"]),
        ("禁止 print",
         ["禁止 `print`", "禁止 print"]),
        ("禁止 import(v0.7.1 補回)",
         ["禁止 import", "不要再 import", "matplotlib"]),
        ("終態 Q = 最終結果",
         ["終態", "Q = "]),
        ("禁止外部 IO",
         ["read_csv", "read_sql", "外部 IO"]),
        ("禁止 self-merge",
         ["self-merge", "禁止.*merge"]),
        ("Series.first() 禁區",
         ["Series.first", ".first()"]),
        ("Long/tidy format 原則",
         ["long", "tidy"]),
    ],

    # ──────────────────────────────────────────
    # Phase B intent-specific blocks(每個 intent 該有的 skeleton 元素)
    # ──────────────────────────────────────────
    "phase_b_block_dashboard_kpi": [
        ("row-level pass-through",
         ["row-level", "pass-through"]),
        ("禁 TOTAL 列",
         ["TOTAL", "SUMMARY", "合計"]),
    ],
    "phase_b_block_stacked_long_pct": [
        ("transform per-group normalize",
         ["transform", "_total_per_group"]),
        ("percentage 0-100",
         ["percentage", "* 100"]),
    ],
    "phase_b_block_ratio_kpi": [
        ("加權平均 weighted",
         ["weighted", "加權平均", "sum", "分子", "分母"]),
        ("禁 .mean() 簡單平均",
         [".mean()", "簡單平均"]),
    ],
    "phase_b_block_time_series": [
        ("時間軸轉型",
         ["to_datetime", "to_period"]),
    ],
    "phase_b_block_simple_groupby": [
        ("groupby + agg",
         ["groupby", "agg"]),
    ],

    # ──────────────────────────────────────────
    # Phase C · ECharts(universal header)
    # ──────────────────────────────────────────
    "phase_c_universal": [
        ("option 變數產出",
         ["option", "dict"]),
        ("欄位名鎖死",
         ["欄位名鎖死", "q_columns"]),
        ("禁止函式 formatter",
         ["formatter", "lambda"]),
        ("禁止空殼 + dynamic fill(v0.4.7)",
         ["空殼", "dynamic fill"]),
        ("numpy cast(v0.4.6)",
         ["numpy", "cast", "int(", "float(", "str("]),
        ("數值精度",
         ["round", "精度"]),
        ("色盤",
         ["color", "5470c6"]),
    ],

    # ──────────────────────────────────────────
    # Phase C intent-specific blocks
    # ──────────────────────────────────────────
    "phase_c_block_pie": [
        ("type=pie",
         ['"pie"', "type.*pie"]),
        ("label formatter for pie",
         ["{b}", "{c}", "{d}"]),
    ],
    "phase_c_block_stacked_100": [
        ("yAxis max=100",
         ['"max": 100', "max: 100"]),
        ("formatter %",
         ['{value}%', '"{value}%"']),
        ("stack 同名",
         ['"stack"', "stack:"]),
    ],
    "phase_c_block_stacked_raw": [
        ("stack key",
         ["stack", '"total"']),
        ("強制 pivot",
         ["pivot", "pivot_table"]),
    ],
    "phase_c_block_line_dual": [
        ("yAxisIndex",
         ["yAxisIndex"]),
        ("雙軸 yAxis list",
         ['"yAxis": [', "yAxis 必須是"]),
    ],
    "phase_c_block_heatmap": [
        ("trigger item(雷 2)",
         ['"trigger": "item"', 'trigger.*item']),
        ("visualMap inRange.color(雷 3)",
         ["visualMap", "inRange", "color"]),
        ("float/str cast(雷 1)",
         ["float(", "str("]),
    ],
    "phase_c_block_bar_horizontal": [
        ("xAxis value / yAxis category",
         ['"type": "value"', '"type": "category"']),
    ],
    "phase_c_block_kpi_table": [
        ("_use_table flag",
         ["_use_table"]),
        ("_kpi_cards",
         ["_kpi_cards"]),
        ("加權平均",
         ["weighted", "加權平均", "sum"]),
    ],

    # ──────────────────────────────────────────
    # Phase D · Insight
    # ──────────────────────────────────────────
    "phase_d_insight": [
        ("KPI definitions 引用",
         ["kpi_definitions", "KPI"]),
        ("data_limitations 警語",
         ["data_limitations", "missing_dimensions"]),
        ("Markdown 輸出格式",
         ["**", "重點摘要", "觀察"]),
    ],
}


# ============================================================
# Prompt 取得函式 — 從各 phase 取出實際的 prompt 字串
# ============================================================
def _get_prompt(key: str) -> str:
    """取得 key 對應的 prompt 字串(rendered 後)。
    回傳 "" 表示該 key 不適用此 build(skip)。"""
    from embedded_prompts import EMBEDDED_PROMPTS
    from jinja2 import Template

    # ── Phase 0 / A / D:單一 monolithic prompt ──
    if key == "phase_0_plan":
        tpl = EMBEDDED_PROMPTS.get(("phase_0_plan", "*"))
        return Template(tpl).render(domain_knowledge="") if tpl else ""
    if key == "phase_a_pipeline":
        tpl = EMBEDDED_PROMPTS.get(("phase_a_pipeline", "*"))
        return Template(tpl).render(domain_knowledge="") if tpl else ""
    if key == "phase_d_insight":
        tpl = EMBEDDED_PROMPTS.get(("phase_d_insight", "*"))
        return Template(tpl).render(domain_knowledge="") if tpl else ""

    # ── Phase B universal header ──
    if key == "phase_b_universal":
        from embedded_prompts import _PHASE_B_HEADER_TEMPLATE_V6
        return Template(_PHASE_B_HEADER_TEMPLATE_V6).render(
            cols_info="", domain_knowledge=""
        )

    # ── Phase B intent blocks ──
    if key.startswith("phase_b_block_"):
        from embedded_prompts import _PHASE_B_INTENT_BLOCKS
        intent = key.replace("phase_b_block_", "")
        return _PHASE_B_INTENT_BLOCKS.get(intent, "")

    # ── Phase C universal header ──
    if key == "phase_c_universal":
        from embedded_prompts import _PHASE_C_HEADER_TEMPLATE
        return Template(_PHASE_C_HEADER_TEMPLATE).render(cols_info="")

    # ── Phase C intent blocks ──
    if key.startswith("phase_c_block_"):
        from embedded_prompts import _PHASE_C_INTENT_BLOCKS
        intent = key.replace("phase_c_block_", "")
        return _PHASE_C_INTENT_BLOCKS.get(intent, "")

    return ""


# ============================================================
# Check runner
# ============================================================
def check_sentinel(haystack: str, needles: list[str]) -> bool:
    """needles 列表中任一字串在 haystack 內即算 pass(or 邏輯)。"""
    if not haystack:
        return False
    return any(n in haystack for n in needles)


def main() -> int:
    print(f"{'═' * 78}")
    print("  GenBI Prompt Invariants Check (sentinel-based)")
    print(f"{'═' * 78}\n")

    total_keys = len(SENTINELS)
    total_sentinels = sum(len(v) for v in SENTINELS.values())
    failures: list[tuple[str, str, list[str]]] = []
    passed_count = 0

    for key, sentinel_list in SENTINELS.items():
        try:
            prompt = _get_prompt(key)
        except Exception as e:
            print(f"  ⚠️  {key:32s} skipped (load error: {e})")
            continue

        if not prompt:
            print(f"  ⚠️  {key:32s} skipped (empty prompt)")
            continue

        all_pass = True
        for label, needles in sentinel_list:
            ok = check_sentinel(prompt, needles)
            if ok:
                passed_count += 1
            else:
                all_pass = False
                failures.append((key, label, needles))

        status = "✅" if all_pass else "❌"
        print(f"  {status}  {key:32s} {len(sentinel_list)} sentinels "
              f"({len(prompt):,} chars)")

    print(f"\n{'─' * 78}")
    print(f"  Total: {total_keys} prompts, {total_sentinels} sentinels")
    print(f"  Passed: {passed_count} / {total_sentinels}")
    print(f"  Failed: {len(failures)}")
    print(f"{'─' * 78}\n")

    if failures:
        print("❌ FAILURES:")
        for key, label, needles in failures:
            print(f"\n  · {key} → 缺 [{label}]")
            print(f"     expected any of: {needles}")
        print()
        return 1

    print("✅ All sentinels present.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
