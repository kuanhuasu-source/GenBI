"""
phase_b_validator.py — v0.10.5 (Level 2 強化 · Phase B 版)

Phase B 「exec OK 但 Q 內容錯」semantic validator。

設計動機:
  既有 retry 只有 exception 才觸發,但 Phase B 也有 silent failure:
    - Q 是空的(filter 過度,exec 沒爆)
    - Q 數值欄全 0(aggregation 失敗)
    - Q 有重複的 (cat, series) pair(long format 會讓 Phase C pivot 爆 / 圖出錯)
    - Q 有 TOTAL/合計 row(dashboard mode 規則違反)
    - 比較類 query 但 Q 只有 1 row(filter 漏其他實體)

  既有 retry 只能 catch exception,這類「exec ok 但 Q 內容錯」的 silent failure
  會被當「成功」往 Phase C 送,Phase C 再爆但提示是針對 Phase C 的,改不到根因。

解法:Phase B exec OK 後跑這個 validator,若回 non-empty issues,當 retry trigger,
      把 issues 餵進 LLM 當「semantic error feedback」做 Phase B 下一輪 attempt。

# 對外 API
    validate_phase_b_output(Q, query="", dashboard_mode=False) -> list[str]
        Returns: list of issue strings, each 含 [CHECK_NAME] 前綴 + 中文解釋 + 修正提示
        Empty list = OK
        Non-empty = 應該 retry,把這個 list 透過 format_issues_as_retry_hint() 包裝
                     成 previous_error 傳回 generate_preprocess_code

# Checks (5 個,可獨立 unit test)
    A. _check_q_not_empty        — Q 不能是空 DataFrame
    B. _check_q_numeric_not_all_zero — 至少一個數值欄要有非 0 值
    C. _check_q_no_total_row     — dashboard mode 不該有 TOTAL/合計/全部 row
    D. _check_q_long_format_dedupe — 長格式 Q 的 (cat, series) pair 必須 unique
    E. _check_q_comparison_has_multi_rows — 比較類 query 但 Q 只 1 row
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


# ============================================================
# Helpers
# ============================================================
_TOTAL_LABELS = {
    "TOTAL", "total", "Total",
    "合計", "總計", "全部", "全公司", "全體", "ALL", "All", "all",
}

_COMPARISON_HINTS = (
    "比較", "比較看", "對比", "各", "前", "Top", "TOP", "前幾", "排名",
    "差異", "vs", "VS", "排行",
)


def _is_dataframe(Q: Any) -> bool:
    try:
        import pandas as pd
        return isinstance(Q, pd.DataFrame)
    except Exception:
        return False


def _numeric_columns(Q) -> list[str]:
    """回傳 Q 中的數值欄位名。"""
    if not _is_dataframe(Q):
        return []
    try:
        return [c for c in Q.columns if str(Q[c].dtype).startswith(("int", "float"))]
    except Exception:
        return []


def _categorical_columns(Q) -> list[str]:
    """回傳 Q 中的非數值欄位名(string / object / category)。"""
    if not _is_dataframe(Q):
        return []
    try:
        return [
            c for c in Q.columns
            if not str(Q[c].dtype).startswith(("int", "float"))
        ]
    except Exception:
        return []


# ============================================================
# Checks
# ============================================================
def _check_q_not_empty(Q) -> list[str]:
    """Q 不能是空 DataFrame(0 row 或 0 col)。"""
    issues: list[str] = []
    if not _is_dataframe(Q):
        return issues  # 不是 DF 就交給其他 check 處理
    try:
        if Q.empty or len(Q) == 0:
            issues.append(
                "[Q_EMPTY] Phase B 產出的 Q 是空 DataFrame(0 row)。"
                "可能 raw_df filter 過嚴 / merge key 對不上 / agg 結果空。"
                "請檢查:(1) filter 條件是否打字錯?(2) 必要欄位是否存在於 raw_df?"
                "(3) 若用 left/right join,是否該用 outer?"
            )
        elif len(Q.columns) == 0:
            issues.append(
                "[Q_EMPTY] Q 有 row 但沒有欄位(columns 空)。"
                "通常是 agg/select 後欄位被全 drop。請保留至少一個分類欄 + 一個數值欄。"
            )
    except Exception as e:
        logger.debug("Q empty check 例外: %s", e)
    return issues


def _check_q_numeric_not_all_zero(Q) -> list[str]:
    """Q 至少要有一個數值欄、且該數值欄不能全 0/全 NaN。"""
    issues: list[str] = []
    if not _is_dataframe(Q) or Q.empty:
        return issues
    num_cols = _numeric_columns(Q)
    if not num_cols:
        issues.append(
            "[Q_NO_NUMERIC] Q 沒有任何數值欄位。"
            "Phase B 通常應 aggregate 出至少一個 metric(count/sum/avg 等)。"
            "請檢查是否漏了 agg() 步驟,或 dtype 被誤判為 object。"
        )
        return issues
    try:
        all_zero_cols = []
        all_nan_cols = []
        for c in num_cols:
            col = Q[c]
            if col.isna().all():
                all_nan_cols.append(c)
                continue
            # 用 fillna(0) 避免 NaN 干擾 zero 判斷
            if (col.fillna(0) == 0).all():
                all_zero_cols.append(c)
        if all_nan_cols:
            issues.append(
                f"[Q_ALL_NAN] 數值欄 {all_nan_cols} 全是 NaN。"
                "通常是 merge key 對不上、或欄位名稱拼錯導致 KeyError 被吞掉。"
                "請印出 raw_df.columns 對照欄位名確認。"
            )
        if all_zero_cols:
            issues.append(
                f"[Q_ALL_ZERO] 數值欄 {all_zero_cols} 全是 0。"
                "可能 filter 過嚴、bool flag 條件寫錯(例 == True 但欄位是 1/0)、"
                "或 agg 對象選錯欄。請檢查 filter 條件與 flag 欄位的實際值範圍。"
            )
    except Exception as e:
        logger.debug("Q numeric check 例外: %s", e)
    return issues


def _check_q_no_total_row(Q, dashboard_mode: bool = False) -> list[str]:
    """dashboard mode 下 Q 不該有 TOTAL / 合計 / 全公司 等彙總列。"""
    issues: list[str] = []
    if not dashboard_mode or not _is_dataframe(Q) or Q.empty:
        return issues
    cat_cols = _categorical_columns(Q)
    if not cat_cols:
        return issues
    try:
        leaked = []
        for c in cat_cols:
            try:
                vals = set(str(v).strip() for v in Q[c].dropna().unique())
            except Exception:
                continue
            hit = vals & _TOTAL_LABELS
            if hit:
                leaked.append(f"{c}={sorted(hit)}")
        if leaked:
            issues.append(
                f"[Q_TOTAL_LEAK] dashboard mode 下 Q 出現彙總列:{leaked}。"
                "dashboard mode 禁止 TOTAL/合計/全公司 等 row(KPI 卡會重複加總)。"
                "請在 aggregate 後用 `Q = Q[~Q[cat_col].isin(['TOTAL','合計','全公司'])]` "
                "或在 filter 階段就排除。"
            )
    except Exception as e:
        logger.debug("Q total leak check 例外: %s", e)
    return issues


def _check_q_long_format_dedupe(Q) -> list[str]:
    """長格式 Q 的 (cat, series) pair 必須 unique。

    啟發式判斷:
      - 若 Q 有 >= 2 個 categorical col + 1 個 numeric col(典型 long format)
      - 檢查前兩個 cat col 組合是否 unique
      - 重複代表 Phase B 漏 groupby,Phase C pivot 會出錯
    """
    issues: list[str] = []
    if not _is_dataframe(Q) or Q.empty:
        return issues
    cat_cols = _categorical_columns(Q)
    num_cols = _numeric_columns(Q)
    # 必須有 >= 2 cat + >= 1 num 才像 long format
    if len(cat_cols) < 2 or not num_cols:
        return issues
    try:
        c1, c2 = cat_cols[0], cat_cols[1]
        dupe = Q.duplicated(subset=[c1, c2]).sum()
        if dupe > 0:
            issues.append(
                f"[Q_LONG_FORMAT_DUPE] Q 疑似 long format 但 ({c1}, {c2}) 組合"
                f"有 {int(dupe)} 個重複。"
                "Phase C 把 long 轉 ECharts series 時會把同 (cat, series) 多筆畫成"
                "多個點(視覺異常)。請在 Phase B 用 "
                f"`Q = Q.groupby(['{c1}', '{c2}']).agg({{'{num_cols[0]}': 'sum'}}).reset_index()` "
                "彙總,確保組合唯一。"
            )
    except Exception as e:
        logger.debug("Q long-format dedupe check 例外: %s", e)
    return issues


def _check_q_comparison_has_multi_rows(Q, query: str = "") -> list[str]:
    """query 暗示「比較/各/前 N」但 Q 只有 1 row → filter 把要比的對象都 filter 掉了。"""
    issues: list[str] = []
    if not _is_dataframe(Q) or Q.empty or not query:
        return issues
    if not any(h in query for h in _COMPARISON_HINTS):
        return issues
    try:
        if len(Q) == 1:
            issues.append(
                "[Q_SINGLE_ROW_FOR_COMPARISON] query 含比較/排名/各 等字眼,"
                "但 Q 只有 1 row,無法做比較圖。"
                "請檢查 filter 是否把多個比較對象(例如各公司、各部門)誤刪。"
                "通常 Phase A 的 $match 範圍太窄,或 groupby 鍵選錯。"
            )
    except Exception as e:
        logger.debug("Q comparison check 例外: %s", e)
    return issues


# ============================================================
# 公開 API
# ============================================================
def validate_phase_b_output(
    Q,
    query: str = "",
    dashboard_mode: bool = False,
) -> list[str]:
    """跑 5 個 check,回 issues list(空 list = OK)。

    Args:
        Q: Phase B exec 後產出的 DataFrame
        query: 原始 user query(用於 comparison 偵測)
        dashboard_mode: 是否為 dashboard query(影響 TOTAL leak check)

    Returns:
        list[str]: 每個 issue 一個字串,含 [CHECK_NAME] 前綴 + 中文說明 + 修正提示
    """
    issues: list[str] = []
    issues += _check_q_not_empty(Q)
    # 若 Q 已經是空,後面的 check 就 skip(避免雜訊)
    if any("[Q_EMPTY]" in i for i in issues):
        return issues
    issues += _check_q_numeric_not_all_zero(Q)
    issues += _check_q_no_total_row(Q, dashboard_mode=dashboard_mode)
    issues += _check_q_long_format_dedupe(Q)
    issues += _check_q_comparison_has_multi_rows(Q, query=query)
    return issues


def format_issues_as_retry_hint(issues: list[str]) -> str:
    """把 issues 格式化成 `previous_error` 字串給 generate_preprocess_code。"""
    if not issues:
        return ""
    lines = [
        "Semantic validation failed — Phase B exec OK 但 Q 內容語意錯。"
        "請修正以下 issue 重生 Phase B code:",
        "",
    ]
    for i, iss in enumerate(issues, 1):
        lines.append(f"  {i}. {iss}")
    return "\n".join(lines)


# ============================================================
# Smoke test
# ============================================================
if __name__ == "__main__":
    import pandas as pd

    # A: empty
    print("A empty:", _check_q_not_empty(pd.DataFrame()))

    # B: all zero
    df_zero = pd.DataFrame({"company": ["A", "B"], "count": [0, 0]})
    print("B all_zero:", _check_q_numeric_not_all_zero(df_zero))

    # C: TOTAL leak in dashboard mode
    df_total = pd.DataFrame({"company": ["A", "B", "TOTAL"], "count": [1, 2, 3]})
    print("C total_leak:", _check_q_no_total_row(df_total, dashboard_mode=True))
    print("C total_leak (non-dashboard skip):",
          _check_q_no_total_row(df_total, dashboard_mode=False))

    # D: long format dupe
    df_dupe = pd.DataFrame({
        "company": ["A", "A", "A", "B"],
        "type": ["PAY", "PAY", "RTN", "PAY"],
        "count": [10, 20, 5, 30],
    })
    print("D long_dupe:", _check_q_long_format_dedupe(df_dupe))

    # E: comparison but single row
    df_single = pd.DataFrame({"company": ["A"], "count": [10]})
    print("E single_for_cmp:",
          _check_q_comparison_has_multi_rows(df_single, query="各公司的銷售比較"))

    # OK case
    df_ok = pd.DataFrame({"company": ["A", "B", "C"], "count": [10, 20, 30]})
    print("OK:", validate_phase_b_output(df_ok, query="各公司銷售", dashboard_mode=True))
