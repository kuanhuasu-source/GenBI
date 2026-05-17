"""
phase_c_validator.py — v0.10.4 (Level 2 強化)

Phase C 「exec OK 但內容錯」semantic validator。

設計動機:
  baseline 觀察到不少 Phase C 失敗是「exec succeeded but option dict 內容語意錯」
  (series 跟 xAxis 長度對不齊、100% stacked 漏 max=100、long format 沒 filter 等)。
  既有 retry 只有 exception 才觸發,這類 silent failure 滑過去。

解法:exec OK 後跑這個 validator,若回 non-empty issues list,當作 retry trigger,
      把 issues 餵進 LLM 當「semantic error feedback」做下一輪 attempt。

# 對外 API
    validate_phase_c_output(option, Q, query="", intent="") -> list[str]
        Returns: list of issue strings, each含 [CHECK_NAME] 前綴 + 中文解釋
        Empty list = OK
        Non-empty = 應該 retry,把這個 list join 成 previous_error 傳回 LLM

# Checks(5 個,可獨立 unit test)
    A. _check_axis_alignment      — series.data 長度 == category axis.data 長度
    B. _check_100pct_max          — 100% stacked 必有 value axis max=100
    C. _check_cat_dedupe          — category axis.data 無重複
    D. _check_long_format_series  — long format Q 但 series=1(漏 filter)
    E. _check_series_non_degenerate — series.data 全 0 / 全同值 silent failure
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ============================================================
# Helpers
# ============================================================
def _safe_dict(x: Any) -> dict:
    """option / xAxis / yAxis 可能是 dict、list、None — 統一回 dict 簡化下游檢查。"""
    return x if isinstance(x, dict) else {}


def _detect_axes(option: dict) -> dict:
    """偵測 chart orientation,回:
        {is_horizontal, cat_axis (dict), cat_axis_label, cat_axis_data,
         val_axis (dict), val_axis_label}
    """
    xaxis = _safe_dict(option.get("xAxis"))
    yaxis = _safe_dict(option.get("yAxis"))
    is_horizontal = (
        xaxis.get("type") == "value" and yaxis.get("type") == "category"
    )
    cat_axis = yaxis if is_horizontal else xaxis
    val_axis = xaxis if is_horizontal else yaxis
    return {
        "is_horizontal": is_horizontal,
        "cat_axis": cat_axis,
        "cat_axis_label": "yAxis" if is_horizontal else "xAxis",
        "cat_axis_data": cat_axis.get("data") if isinstance(cat_axis, dict) else None,
        "val_axis": val_axis,
        "val_axis_label": "xAxis" if is_horizontal else "yAxis",
    }


def _looks_like_100pct(Q, intent: str, option: dict) -> bool:
    """heuristic — 是否為 100% stacked 場景。"""
    if intent in ("stacked_100", "stacked_100_horizontal"):
        return True
    # Q 含 percentage / pct 欄
    if Q is not None and hasattr(Q, "columns"):
        for c in Q.columns:
            cl = str(c).lower()
            if cl in ("percentage", "pct") or cl.endswith("_pct"):
                return True
    # option 直接含 max=100 但已有就不需要 check 了 — 反過來檢測潛在 100% 才需要
    # series 任一 series.stack 同名 → 可能 stacked,結合 formatter '{value}%' 才算 100%
    series = option.get("series", []) or []
    has_stack = any(s.get("stack") for s in series if isinstance(s, dict))
    axes = _detect_axes(option)
    val_axis = axes["val_axis"] or {}
    axis_label = _safe_dict(val_axis.get("axisLabel"))
    fmt = str(axis_label.get("formatter", ""))
    if has_stack and "%" in fmt:
        return True
    return False


# ============================================================
# Check A:axis alignment
# ============================================================
def _check_axis_alignment(option: dict, Q) -> list[str]:
    axes = _detect_axes(option)
    cat_data = axes["cat_axis_data"]
    if not isinstance(cat_data, list):
        return []
    cat_len = len(cat_data)
    if cat_len == 0:
        return []
    label = axes["cat_axis_label"]
    issues = []
    for i, s in enumerate(option.get("series", []) or []):
        if not isinstance(s, dict):
            continue
        sd = s.get("data")
        if not isinstance(sd, list):
            continue
        if len(sd) != cat_len:
            issues.append(
                f"[AXIS_ALIGN] series[{i}] (name='{s.get('name','?')}') "
                f"data 長度 {len(sd)} != {label}.data 長度 {cat_len}。"
                f"修法:若 Q 是 long format,filter sub_dim 後加 "
                f"`.set_index({label}_dim).reindex({label}_data).fillna(0).tolist()` "
                f"確保長度對齊。"
            )
    return issues


# ============================================================
# Check B:100% stacked needs value axis max=100
# ============================================================
def _check_100pct_max(option: dict, Q, intent: str) -> list[str]:
    if not _looks_like_100pct(Q, intent, option):
        return []
    axes = _detect_axes(option)
    val_axis = axes["val_axis"] or {}
    val_label = axes["val_axis_label"]
    actual_max = val_axis.get("max") if isinstance(val_axis, dict) else None
    if actual_max == 100:
        return []
    return [
        f"[100PCT_MAX] 偵測到 100% stacked 場景(intent={intent or 'auto'}),"
        f"{val_label} 必須有 `\"max\": 100` 鎖住範圍 0~100%。"
        f"目前 {val_label}.max = {actual_max}。"
        f"修法:加 `\"max\": 100`,並確認 axisLabel.formatter='{{value}}%'。"
    ]


# ============================================================
# Check C:category axis dedupe
# ============================================================
def _check_cat_dedupe(option: dict) -> list[str]:
    axes = _detect_axes(option)
    cat_data = axes["cat_axis_data"]
    label = axes["cat_axis_label"]
    if not isinstance(cat_data, list) or len(cat_data) == 0:
        return []
    str_data = [str(x) for x in cat_data]
    dup_count = len(str_data) - len(set(str_data))
    if dup_count == 0:
        return []
    return [
        f"[CAT_DEDUPE] {label}.data 含 {dup_count} 個重複(總 {len(cat_data)} 個)。"
        f"前 5 項:{cat_data[:5]}。"
        f"修法:long format Q 須用 `Q['<dim_col>'].unique().tolist()` 而非 `.tolist()`。"
    ]


# ============================================================
# Check D:long format Q + only 1 series(LLM 漏 filter sub_dim)
# ============================================================
def _check_long_format_one_series(option: dict, Q) -> list[str]:
    if Q is None or not hasattr(Q, "columns") or len(Q.columns) != 3:
        return []
    try:
        import pandas as pd  # noqa: F401
        numeric_cols = []
        string_cols = []
        for c in Q.columns:
            try:
                if pd.api.types.is_numeric_dtype(Q[c]):
                    numeric_cols.append(c)
                else:
                    string_cols.append(c)
            except Exception:
                string_cols.append(c)
    except Exception:
        return []

    if len(numeric_cols) != 1 or len(string_cols) != 2:
        return []  # 不像 long format

    n_series = len(option.get("series", []) or [])
    if n_series != 1:
        return []  # 已經多 series,沒問題

    # 找最有可能的 sub_dim:unique values 2-15 個
    candidates = []
    for c in string_cols:
        try:
            u = int(Q[c].nunique())
            if 2 <= u <= 15:
                candidates.append((c, u))
        except Exception:
            pass
    if not candidates:
        return []
    best_col, best_u = max(candidates, key=lambda x: x[1])
    return [
        f"[LONG_FORMAT_1SERIES] Q 是 long format(3 欄含 dim+sub_dim+value),"
        f"sub_dim '{best_col}' 有 {best_u} 個 unique values,但 option 只有 1 個 series。"
        f"修法:對每個 sub_dim value 各做 1 個 series:\n"
        f"  ```python\n"
        f"  x_data = Q['<dim>'].unique().tolist()\n"
        f"  for v in Q['{best_col}'].unique():\n"
        f"      data = Q[Q['{best_col}']==v].set_index('<dim>')['{numeric_cols[0]}']\n"
        f"             .reindex(x_data).fillna(0).tolist()\n"
        f"      series.append({{'name': str(v), 'type': 'bar', 'data': data}})\n"
        f"  ```"
    ]


# ============================================================
# Check E:series 全 0 / 全同值 silent failure
# ============================================================
def _check_series_non_degenerate(option: dict) -> list[str]:
    issues = []
    for i, s in enumerate(option.get("series", []) or []):
        if not isinstance(s, dict):
            continue
        sd = s.get("data")
        if not isinstance(sd, list) or len(sd) == 0:
            continue
        # 抽純 numeric 值
        nums = []
        for v in sd:
            if isinstance(v, (int, float)) and not (isinstance(v, bool)):
                nums.append(v)
            elif isinstance(v, list) and len(v) >= 1 and isinstance(v[-1], (int, float)):
                # heatmap-style [x, y, val]
                nums.append(v[-1])
        if not nums:
            continue
        if all(n == 0 for n in nums):
            issues.append(
                f"[ALL_ZERO] series[{i}] (name='{s.get('name','?')}') 所有 data 都是 0。"
                f"原因通常是 Phase B 算出 column 全 0(退化公式 / filter 全濾掉 / status 欄漏撈)。"
            )
        elif len(set(nums)) == 1:
            issues.append(
                f"[ALL_SAME] series[{i}] (name='{s.get('name','?')}') 所有 data 都是 {nums[0]}。"
                f"原因:可能 Phase B groupby 維度錯,或 filter 不對。"
            )
    return issues


# ============================================================
# 對外 API
# ============================================================
def validate_phase_c_output(
    option: Any,
    Q=None,
    query: str = "",
    intent: str = "",
) -> list[str]:
    """跑 5 個 semantic check,回 issues list。

    Args:
        option: Phase C 輸出的 ECharts option dict(可能是 dict、含 _use_table 的 fallback、None)
        Q: Phase B 終態 DataFrame(可選,給 long-format / 100% 偵測用)
        query: user 原始 query(可選,supplementary context)
        intent: chart intent(`_detect_chart_intent(query)` 結果,可選)

    Returns:
        list[str] of issue strings。Empty list = OK。

    Skip 條件:
        - option 不是 dict(可能 table fallback)→ 直接回 []
        - option 含 _use_table=True → 是 table fallback,不檢查 chart-specific issue
    """
    if not isinstance(option, dict):
        return []
    if option.get("_use_table"):
        return []  # table fallback,不該被 validator 視為 chart

    all_issues: list[str] = []
    try:
        all_issues.extend(_check_axis_alignment(option, Q))
    except Exception as e:
        logger.warning(f"_check_axis_alignment crashed: {e}")
    try:
        all_issues.extend(_check_100pct_max(option, Q, intent))
    except Exception as e:
        logger.warning(f"_check_100pct_max crashed: {e}")
    try:
        all_issues.extend(_check_cat_dedupe(option))
    except Exception as e:
        logger.warning(f"_check_cat_dedupe crashed: {e}")
    try:
        all_issues.extend(_check_long_format_one_series(option, Q))
    except Exception as e:
        logger.warning(f"_check_long_format_one_series crashed: {e}")
    try:
        all_issues.extend(_check_series_non_degenerate(option))
    except Exception as e:
        logger.warning(f"_check_series_non_degenerate crashed: {e}")

    return all_issues


def format_issues_as_retry_hint(issues: list[str]) -> str:
    """把 issues 格式化成 `previous_error` 字串給 LLM。"""
    if not issues:
        return ""
    lines = [
        "Semantic validation failed — exec OK 但 option dict 內容語意錯。"
        "請修正以下 issue 重生:",
        "",
    ]
    for i, iss in enumerate(issues, 1):
        lines.append(f"  {i}. {iss}")
    return "\n".join(lines)


if __name__ == "__main__":
    # 簡單 smoke
    import pandas as pd

    # Test A: axis alignment fail
    option = {
        "xAxis": {"type": "category", "data": ["A", "B", "C"]},
        "yAxis": {"type": "value"},
        "series": [{"name": "S1", "data": [1, 2, 3, 4]}],  # 4 vs 3
    }
    print("A axis_align:", _check_axis_alignment(option, None))

    # Test B: 100pct missing max
    option = {
        "xAxis": {"type": "category", "data": ["A", "B"]},
        "yAxis": {"type": "value", "axisLabel": {"formatter": "{value}%"}},
        "series": [{"name": "S1", "stack": "x", "data": [50, 60]}],
    }
    print("B 100pct:", _check_100pct_max(option, None, "stacked_100"))

    # Test C: dedupe fail
    option = {
        "xAxis": {"type": "category", "data": ["A", "A", "B", "B"]},
        "yAxis": {"type": "value"},
        "series": [{"data": [1, 2, 3, 4]}],
    }
    print("C dedupe:", _check_cat_dedupe(option))

    # Test D: long format 1 series
    Q = pd.DataFrame({
        "company": ["A", "A", "B", "B"],
        "type": ["PAY", "RTN", "PAY", "RTN"],
        "count": [10, 2, 20, 3],
    })
    option = {
        "xAxis": {"type": "category", "data": ["A", "B"]},
        "yAxis": {"type": "value"},
        "series": [{"name": "PAY", "data": [10, 2, 20, 3]}],  # 1 series, wrong
    }
    print("D long_format:", _check_long_format_one_series(option, Q))

    # Test E: all zero
    option = {
        "xAxis": {"type": "category", "data": ["A", "B"]},
        "yAxis": {"type": "value"},
        "series": [
            {"name": "S1", "data": [0, 0]},
            {"name": "S2", "data": [5, 7]},
        ],
    }
    print("E all_zero:", _check_series_non_degenerate(option))

    # OK case
    option_ok = {
        "xAxis": {"type": "category", "data": ["A", "B", "C"]},
        "yAxis": {"type": "value", "max": 100, "axisLabel": {"formatter": "{value}%"}},
        "series": [
            {"name": "PAY", "stack": "x", "data": [60, 70, 80]},
            {"name": "RTN", "stack": "x", "data": [40, 30, 20]},
        ],
    }
    print("OK case:", validate_phase_c_output(option_ok, None, intent="stacked_100"))
