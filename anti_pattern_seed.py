"""
anti_pattern_seed.py — v0.16.0+ (M6.3 Sprint 3 Day 12)

Hand-curated anti-pattern catalog for `anti_pattern_index` 的 seed。

# 為什麼用 seed 而非全自動 mining?

理想狀況:`learning_instincts` collection 跑滿 production traces,自動萃出
real-world anti-pattern。但 GenBI v0.16 起步階段:
- learning pipeline 尚未啟動
- 但 phase_a/b/c_validator.py 已經有「驗證後 LLM 該避免」的明確規則
- 把這些 hardcoded rules 抽出來當第 0 版 seed,即立刻有 high-quality content

# 結構

每筆 doc = `{"id": str, "applies_to_phase": str, "content": str, "tags": list}`:
- `id`:文件 stable id(eg "A_FORBIDDEN_IMPORT")
- `applies_to_phase`:`phase_a` / `phase_b` / `phase_c`(filter 用)
- `content`:給 LLM prompt 用的文字 — 描述 anti-pattern + 為什麼 + 該怎麼做
- `tags`:`severity`(`fatal`/`high`/`med`)+ category(`io`/`dataframe`/...)

# 後續整合 learning_instincts

當 `learning_instincts` collection 開始累積:
1. `build_anti_pattern_index` 先讀 seed
2. 再讀 learning_instincts(filter `status=verified`)
3. Embedding model 統一,兩源混合 retrieve

短期內 seed 是 only source。Sprint 3 不依賴 learning pipeline。
"""

from __future__ import annotations

# ============================================================
# Anti-pattern catalog
# ============================================================
ANTI_PATTERNS: list[dict] = [
    # ─── Phase A · Pandas Filter ───────────────────────────────────────
    {
        "id": "A_FORBIDDEN_IMPORT",
        "applies_to_phase": "phase_a",
        "content": (
            "❌ Phase A 禁止 `import` 任何套件(包含 `import pandas as pd`、"
            "`from xxx import yyy`、`__import__(...)`)。\n"
            "原因:sandbox 已備好 `pd` / `np` / `source_df` 在 namespace,"
            "import 會 NameError(沙箱阻擋)。\n"
            "✅ 直接用:`raw_df = source_df[source_df['col']=='X']`"
        ),
        "tags": ["fatal", "io", "phase_a"],
    },
    {
        "id": "A_FORBIDDEN_IO",
        "applies_to_phase": "phase_a",
        "content": (
            "❌ Phase A 禁止 `open(...)` / `pd.read_csv(...)` / `os.path...` / "
            "`requests.get(...)` / `subprocess...`(任何外部 IO)。\n"
            "原因:資料外洩風險 + Phase A 只能讀 `source_df`,不可讀任何其他資料源。\n"
            "✅ 全部從 `source_df`:`source_df.loc[...]` / `source_df.query(\"...\")`"
        ),
        "tags": ["fatal", "io", "phase_a"],
    },
    {
        "id": "A_HALLUCINATED_COLUMN",
        "applies_to_phase": "phase_a",
        "content": (
            "❌ Phase A 引用了不存在的欄位(eg `source_df['不存在的欄位']`)。\n"
            "原因:`source_columns` 已明列實際欄位,LLM 不該憑想像猜欄位名。\n"
            "✅ 只引用 `source_columns` 中真實存在的欄位。若需求中提到「期間」"
            "但 schema 沒有日期欄位 — 走 Phase 0 REFUSE。"
        ),
        "tags": ["high", "dataframe", "phase_a"],
    },
    {
        "id": "A_DERIVED_NEW_COLUMN",
        "applies_to_phase": "phase_a",
        "content": (
            "❌ Phase A 派生新欄位(eg `raw_df['ratio'] = source_df['a']/source_df['b']`)。\n"
            "原因:派生欄位是 Phase B 的工作。Phase A 只負責「從 source_df 取列子集」。\n"
            "✅ Phase A 保持 row filter 純粹;Phase B 才在 raw_df 上跑 `.assign(...)`。"
        ),
        "tags": ["med", "dataframe", "phase_a"],
    },
    {
        "id": "A_DOING_PHASE_B_WORK",
        "applies_to_phase": "phase_a",
        "content": (
            "❌ Phase A 直接 `raw_df = source_df.groupby(...).agg(...)` / "
            "`.merge(...)` / `.pivot(...)`。\n"
            "原因:聚合 / 合表 / pivot 都是 Phase B 的工作。Phase A 只做 row filter。\n"
            "✅ 例:`raw_df = source_df[source_df['cat'].isin(['A', 'B'])]`"
        ),
        "tags": ["med", "dataframe", "phase_a"],
    },
    {
        "id": "A_NO_RAW_DF",
        "applies_to_phase": "phase_a",
        "content": (
            "❌ Phase A code exec 後 namespace 沒有 `raw_df` 變數(忘記寫 `raw_df = ...`)。\n"
            "原因:下游 Phase B 找不到 raw_df,直接 NameError。\n"
            "✅ 最後一行必寫 `raw_df = <filtered DataFrame>`(即使是 `source_df.copy()`)。"
        ),
        "tags": ["fatal", "dataframe", "phase_a"],
    },

    # ─── Phase B · Preprocess ──────────────────────────────────────────
    {
        "id": "B_Q_EMPTY",
        "applies_to_phase": "phase_b",
        "content": (
            "❌ Phase B 算完 Q 是空的 DataFrame(filter 過度 / inner join 沒交集)。\n"
            "原因:exec 沒爆但 Q 沒 row,Phase C 畫空圖。\n"
            "✅ 檢查 filter 條件是否過嚴;考慮放寬或改 outer join。"
        ),
        "tags": ["high", "dataframe", "phase_b"],
    },
    {
        "id": "B_Q_ALL_ZERO",
        "applies_to_phase": "phase_b",
        "content": (
            "❌ Phase B Q 的所有數值欄都是 0(aggregation 失敗 / 欄位類型錯)。\n"
            "原因:常見錯誤是用 `.sum()` 在 categorical column 上,或 dtype 是 str 沒轉。\n"
            "✅ 用 `astype(float)` / `pd.to_numeric(..., errors='coerce')` 轉前先確認欄位類型。"
        ),
        "tags": ["high", "dataframe", "phase_b"],
    },
    {
        "id": "B_Q_TOTAL_ROW",
        "applies_to_phase": "phase_b",
        "content": (
            "❌ Phase B(dashboard mode)Q 包含 'TOTAL' / '合計' / '全公司' 等 summary row。\n"
            "原因:Phase C 把 summary row 當一般類別畫,圖嚴重失真。\n"
            "✅ 算完後 drop summary row;summary 在 dashboard KPI 卡片另外呈現,不混入主圖。"
        ),
        "tags": ["med", "dataframe", "phase_b"],
    },
    {
        "id": "B_Q_LONG_FORMAT_DEDUPE",
        "applies_to_phase": "phase_b",
        "content": (
            "❌ Phase B 長格式 Q 出現重複 (category, series) pair。\n"
            "原因:Phase C `.pivot()` 遇重複 pair 直接 ValueError(`Index contains duplicates`)。\n"
            "✅ 確保 groupby 欄位涵蓋所有 dimension;若漏 col 算出多筆,加進 groupby 或 sum 起來。"
        ),
        "tags": ["high", "dataframe", "phase_b"],
    },
    {
        "id": "B_Q_COMPARISON_SINGLE_ROW",
        "applies_to_phase": "phase_b",
        "content": (
            "❌ 比較類 query(含「比較」「各」「Top」「vs」「排名」)但 Phase B 算出 Q 只有 1 row。\n"
            "原因:filter 把其他類別都篩掉了,LLM 看不見比較對象。\n"
            "✅ 回去 plan 看 raw_columns_needed 是否漏列關鍵 dimension;放寬 filter。"
        ),
        "tags": ["high", "dataframe", "phase_b"],
    },
    {
        "id": "B_STATUS_PARTIAL_COVER",
        "applies_to_phase": "phase_b",
        "content": (
            "❌ Phase B 算 ratio/比率 類 KPI 時,只看部分 status 值。例如 "
            "approval_rate 只看 Y / N 但漏算 R(retract)/X(rejected)等其他 status。\n"
            "原因:分母不完整,結果失真(全 0 或全 100%)。\n"
            "✅ kpi_definitions 公式如果有「state 列表」,B 段必須涵蓋所有列出的 state。"
        ),
        "tags": ["high", "kpi", "phase_b"],
    },

    # ─── Phase C · ECharts ─────────────────────────────────────────────
    {
        "id": "C_NO_HISTOGRAM_TYPE",
        "applies_to_phase": "phase_c",
        "content": (
            "❌ ECharts 沒有 `type: 'histogram'` series type。\n"
            "原因:用了會直接 BidiComponent Error,圖不出來。\n"
            "✅ 用 bar series + 自行 bucket Q(在 Phase B groupby buckets)就是 histogram。"
        ),
        "tags": ["fatal", "echarts", "phase_c"],
    },
    {
        "id": "C_PIE_OVER_7_SLICES",
        "applies_to_phase": "phase_c",
        "content": (
            "❌ Pie chart 但類別數 > 7,可讀性極差。\n"
            "原因:小片大量無法區分,圖例擠成一團。\n"
            "✅ 改 horizontal bar(類別多時最佳),或合併 small slices into 'Others'。"
        ),
        "tags": ["med", "echarts", "phase_c"],
    },
    {
        "id": "C_STACKED_MISSING_STATES",
        "applies_to_phase": "phase_c",
        "content": (
            "❌ Stacked bar/100% chart,Phase B 算出的 state 種類少於 ECharts series 數。\n"
            "原因:series 對到空資料,圖示有 series 但 stack 不完整。\n"
            "✅ 用 Phase B Q 實際出現的 state value 動態生 series,別 hardcode。"
        ),
        "tags": ["med", "echarts", "phase_c"],
    },
    {
        "id": "C_HORIZONTAL_AS_VERTICAL",
        "applies_to_phase": "phase_c",
        "content": (
            "❌ Query 明說「橫向 / 水平 / horizontal」但 ECharts 出 vertical bar。\n"
            "原因:Phase 0 plan 寫成「堆疊長條圖」(掉 orientation)→ Phase C router 走 vertical。\n"
            "✅ Plan 該寫「橫向 100% 堆疊長條圖(horizontal 100% stacked bar)」"
            "讓 router 偵測得到。"
        ),
        "tags": ["med", "echarts", "phase_c"],
    },
    {
        "id": "C_HEATMAP_WRONG_DATA_SHAPE",
        "applies_to_phase": "phase_c",
        "content": (
            "❌ Heatmap data 用 wide format(Phase B pivot 出來)但 ECharts heatmap 要 long [[x_idx, y_idx, value]] 三元組。\n"
            "原因:format mismatch 直接 series.data invalid,圖空白。\n"
            "✅ Heatmap 要從 Phase B long format `Q` 轉為 `[x_idx, y_idx, val]` list。"
        ),
        "tags": ["high", "echarts", "phase_c"],
    },
]


# ============================================================
# 開發工具
# ============================================================
def get_anti_patterns(phase: str | None = None) -> list[dict]:
    """回該 phase 的 anti-patterns。phase=None → 全部回。"""
    if phase is None:
        return list(ANTI_PATTERNS)
    return [p for p in ANTI_PATTERNS if p.get("applies_to_phase") == phase]


def count_by_phase() -> dict[str, int]:
    out: dict[str, int] = {}
    for p in ANTI_PATTERNS:
        ph = p.get("applies_to_phase", "?")
        out[ph] = out.get(ph, 0) + 1
    return out


if __name__ == "__main__":
    print(f"Total anti-patterns: {len(ANTI_PATTERNS)}")
    for ph, n in sorted(count_by_phase().items()):
        print(f"  {ph}: {n}")
