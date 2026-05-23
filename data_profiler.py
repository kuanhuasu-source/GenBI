"""
data_profiler.py — v0.12.0+

Physical schema + Data Quality profiler — 把 DataFrame 解成可讓 LLM /
semantic profiler / Review UI 使用的 column-level metadata。

# 為什麼 profiler 在 LLM 之前跑(spec §3.1)
LLM 不應直接看 raw file 猜資料,應該先有一份 machine-readable profile,
LLM 基於 profile 推 semantic role / KPI / data limitations。Profiler 純規則,
零 LLM call,毫秒級延遲。

# 產出規格(spec §9.1)
每個 column 必含:
  - name, physical_type, normalized_name(== name,parser 已 normalize 過)
  - null_count, null_pct
  - distinct_count, distinct_pct
  - sample_values(top-N + random-N,去重後合併)
  - 數值類 extra: min, max, mean, median, std, p05, p25, p75, p95
  - 字串類 extra: top_values(value → count,前 5)
  - warnings: list 文字標籤

# Data Quality warnings(spec §9.3)
- `all_null`              全空欄(null_pct = 100%)
- `all_same`              全部值相同(distinct_count = 1)
- `high_null`             null_pct > 30%
- `high_cardinality`      distinct / row > 0.9(疑似 ID 欄)
- `low_cardinality`       distinct = 2-3(可能是 boolean / status)
- `right_skewed`          數值欄 mean >> median(p95 / median > 3)
- `suspect_id`            name 含 'id' / '_no' / '_code' 結尾 + high_cardinality
- `suspect_total_row`     最後一列疑似 TOTAL / 合計列(future enhancement)
- `mixed_type`            object dtype 但同欄混 string + number
- `whitespace_in_values`  string 欄前後有 leading / trailing whitespace
"""

from __future__ import annotations

import logging
import math
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


# ============================================================
# Threshold 常量(可在 caller 端覆寫,但 MVP 不開放 config 化)
# ============================================================
HIGH_NULL_THRESHOLD_PCT = 30.0       # null_pct > 30% → high_null warning
HIGH_CARDINALITY_RATIO = 0.9         # distinct / row > 0.9 → 疑似 ID
LOW_CARDINALITY_MAX = 3              # distinct ≤ 3 → 可能是 status / boolean
SKEW_RATIO_THRESHOLD = 3.0           # p95 / median > 3 → right_skewed
SAMPLE_TOP_N = 5                     # 取前 5 個最常出現
SAMPLE_RANDOM_N = 3                  # 額外隨機 3 個
TOP_VALUES_LIMIT = 5                 # top_values 留 5 個


# ============================================================
# 物理 type 偵測
# ============================================================
def detect_physical_type(series: pd.Series) -> str:
    """回傳 physical_type 字串:
       'integer' | 'number' | 'string' | 'boolean' | 'datetime' | 'unknown'
    """
    if pd.api.types.is_bool_dtype(series):
        return "boolean"
    if pd.api.types.is_integer_dtype(series):
        return "integer"
    if pd.api.types.is_float_dtype(series):
        return "number"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "datetime"
    if pd.api.types.is_string_dtype(series) or series.dtype == object:
        return "string"
    return "unknown"


# ============================================================
# Single-column profile
# ============================================================
def _sample_values(series: pd.Series) -> list[Any]:
    """產生 sample_values list — top-N most common + random-N。"""
    clean = series.dropna()
    if clean.empty:
        return []
    seen: list[Any] = []
    # Top N
    try:
        top = clean.value_counts().head(SAMPLE_TOP_N).index.tolist()
        for v in top:
            if v not in seen:
                seen.append(_to_native(v))
    except TypeError:
        # unhashable values, skip top-N
        pass
    # Random N(已有的不重複)
    try:
        n_sample = min(SAMPLE_RANDOM_N, len(clean))
        random_sample = clean.sample(n=n_sample, random_state=42).tolist()
        for v in random_sample:
            if v not in seen and len(seen) < (SAMPLE_TOP_N + SAMPLE_RANDOM_N):
                seen.append(_to_native(v))
    except (ValueError, TypeError):
        pass
    return seen


def _to_native(v):
    """numpy / pandas scalar → Python native(給 BSON serializer 用)。"""
    if v is None:
        return None
    if isinstance(v, (str, int, float, bool)):
        if isinstance(v, float) and not math.isfinite(v):
            return None
        return v
    if hasattr(v, "item"):  # numpy scalar
        try:
            return v.item()
        except Exception:
            return str(v)
    if isinstance(v, pd.Timestamp):
        return v.isoformat()
    return str(v)


def profile_column(series: pd.Series, column_name: str) -> dict[str, Any]:
    """產出單一 column 的 profile dict。"""
    n_total = len(series)
    n_null = int(series.isna().sum())
    null_pct = round((n_null / n_total) * 100, 2) if n_total else 0.0

    physical_type = detect_physical_type(series)
    warnings: list[str] = []

    # distinct
    try:
        distinct_count = int(series.nunique(dropna=True))
    except TypeError:
        # unhashable values (例:list / dict in cell)
        distinct_count = -1  # 標示無法計算
        warnings.append("unhashable_values")
    distinct_pct = (round((distinct_count / n_total) * 100, 2)
                    if distinct_count >= 0 and n_total else 0.0)

    profile: dict[str, Any] = {
        "name": column_name,
        "physical_type": physical_type,
        "null_count": n_null,
        "null_pct": null_pct,
        "distinct_count": distinct_count if distinct_count >= 0 else None,
        "distinct_pct": distinct_pct,
    }

    # Sample values
    profile["sample_values"] = _sample_values(series)

    # 數值類 stats
    if physical_type in ("integer", "number"):
        clean = series.dropna()
        if len(clean) > 0:
            try:
                desc = clean.describe(percentiles=[0.05, 0.25, 0.5, 0.75, 0.95])
                profile.update({
                    "min": _to_native(desc.get("min")),
                    "max": _to_native(desc.get("max")),
                    "mean": _to_native(round(float(desc.get("mean")), 4)),
                    "median": _to_native(desc.get("50%")),
                    "std": _to_native(round(float(desc.get("std", 0) or 0), 4)),
                    "p05": _to_native(desc.get("5%")),
                    "p25": _to_native(desc.get("25%")),
                    "p75": _to_native(desc.get("75%")),
                    "p95": _to_native(desc.get("95%")),
                })
                # right_skewed 偵測
                med = float(desc.get("50%") or 0)
                p95 = float(desc.get("95%") or 0)
                if med > 0 and p95 / med > SKEW_RATIO_THRESHOLD:
                    warnings.append("right_skewed")
            except Exception as e:
                logger.warning(f"numeric stats fail for `{column_name}`: {e}")

    # 字串類 top values
    if physical_type == "string":
        clean = series.dropna()
        if len(clean) > 0:
            try:
                vc = clean.value_counts().head(TOP_VALUES_LIMIT)
                profile["top_values"] = [
                    {"value": _to_native(idx), "count": int(cnt)}
                    for idx, cnt in vc.items()
                ]
            except TypeError:
                pass
            # whitespace 偵測
            try:
                has_ws = clean.astype(str).apply(
                    lambda x: x != x.strip()
                ).any()
                if has_ws:
                    warnings.append("whitespace_in_values")
            except Exception:
                pass
            # mixed_type 偵測(object dtype 內混型別)
            sample = clean.head(100)
            types_seen = set()
            for v in sample:
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    types_seen.add("number")
                elif isinstance(v, bool):
                    types_seen.add("boolean")
                else:
                    types_seen.add("string")
                if len(types_seen) > 1:
                    warnings.append("mixed_type")
                    break

    # Cardinality warnings
    if distinct_count == 0 or null_pct >= 100:
        warnings.append("all_null")
    elif distinct_count == 1:
        warnings.append("all_same")
    elif distinct_count >= 2 and distinct_count <= LOW_CARDINALITY_MAX:
        warnings.append("low_cardinality")
    if distinct_count >= 0 and n_total > 10:
        if (distinct_count / n_total) >= HIGH_CARDINALITY_RATIO:
            warnings.append("high_cardinality")
            # suspect_id:high_cardinality + 名字像 ID
            name_lower = column_name.lower()
            id_hints = ("id", "_no", "_code", "_key", "_uuid", "編號", "代碼")
            if any(h in name_lower for h in id_hints):
                warnings.append("suspect_id")

    if null_pct > HIGH_NULL_THRESHOLD_PCT:
        warnings.append("high_null")

    profile["warnings"] = warnings

    # M4b+: PII 偵測 — 結果存 pii_info 子欄位
    try:
        from pii_detector import detect_pii_in_column
        profile["pii_info"] = detect_pii_in_column(
            column_name=column_name,
            sample_values=profile.get("sample_values", []),
            physical_type=physical_type,
        )
    except Exception as _pe:
        # pii detector 失敗不該擋 profiling
        logger.warning(f"pii_detector failed for `{column_name}`: {_pe}")
        profile["pii_info"] = {
            "is_pii": False, "pii_type": None,
            "confidence": 0.0, "reason": "",
        }

    return profile


# ============================================================
# Table-level profile
# ============================================================
def profile_table(df: pd.DataFrame, table_id: str) -> dict[str, Any]:
    """產出整張 table 的 profile dict(spec §8.3 schema)。"""
    columns_profile = [
        profile_column(df[col], str(col)) for col in df.columns
    ]
    return {
        "table_id": table_id,
        "row_count": int(df.shape[0]),
        "column_count": int(df.shape[1]),
        "columns": columns_profile,
    }


def profile_dataset(tables: list[tuple[str, pd.DataFrame]]) -> dict[str, Any]:
    """產出整 dataset profile(支援未來 multi-sheet,MVP 通常只有 1 table)。

    Args:
        tables: [(table_id, dataframe), ...]
    """
    return {
        "tables": [profile_table(df, tid) for tid, df in tables],
    }
