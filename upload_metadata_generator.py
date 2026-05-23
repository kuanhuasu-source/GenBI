"""
upload_metadata_generator.py — v0.12.0+

把 physical profile + semantic profile + user corrections 組裝成
GenBI agentic workflow 可消化的 metadata dict。

# 輸出 schema(對齊既有 tflex / ecommerce metadata)

```python
{
  "dataset_id": "upload_...",
  "dataset_name": "project_leadtime.csv",
  "source_type": "upload",          # ← 關鍵旗標,LLMService 用來切 Phase A prompt
  "business_context": {
    "business_description": "...",
    "main_business_questions": [...],   # sample questions(自動建議 + user 可改)
  },
  "recommended_mongodb": {              # upload-driven 不需要,但保留 key 避免 caller 炸
    "database": "<n/a — uploaded dataset>",
    "collections": {},
    "join_key": null,
  },
  "collections": {
    "sheet1": {
      "primary_key": "...",             # 從 grain 或 high_cardinality identifier 推
      "grain": "one row per ...",
      "description": "...",
      "fields": {
        "<col>": {
          "type": "string|number|integer|boolean|datetime",
          "semantic_role": "...",
          "description": "...",
          "unit": "...",
          "default_aggregation": "sum|avg|...",
          "recommended_use": [...],
          "not_recommended_use": [...],
          "is_dimension": bool,
          "is_measure": bool,
          "is_identifier": bool,
          "confidence": float,
          "user_confirmed": bool,
          "allowed_values": [...] or None,    # 由 categorical_status top_values 補
          "warnings": [...],
        }
      },
    },
  },
  "relationships": [],                 # MVP single-table 永遠空
  "kpi_definitions": {                 # 從 measure_* 欄位自動建議
    "<kpi_key>": {
      "name": "...",
      "formula": "<pandas-style 公式>",
      "important_note": "...",
      "auto_suggested": True,
      "user_confirmed": False,
    }
  },
  "data_limitations": {
    "missing_dimensions": [...],       # 自動推:沒 date col → "No confirmed date column"
    "not_supported_analysis": [...],   # 自動推:沒 date col → "Trend analysis ..."
  },
  "charting_guidance": {               # 從 semantic profile 自動建議
    "recommended_charts": {...},
  },
}
```
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from semantic_profiler import ROLE_PROPERTIES, SEMANTIC_ROLES

logger = logging.getLogger(__name__)


# ============================================================
# Grain 推論
# ============================================================
def infer_grain(
    column_results: list[dict],
    column_profiles: list[dict],
    row_count: int,
) -> dict[str, Any]:
    """猜 dataset 的 grain — 哪個欄位最像 primary key?

    Returns:
        {
          "primary_key": "<col_name>" or None,
          "grain_text": "one row per ..." 描述,
          "confidence": float,
          "candidates": [{"name": ..., "distinct": ..., "is_identifier": bool}],
        }
    """
    candidates = []
    for prof, sem in zip(column_profiles, column_results):
        if sem["role"] != "identifier":
            continue
        d = prof.get("distinct_count") or 0
        # primary key 該滿足:distinct ≈ row_count
        if row_count > 0 and d >= row_count * 0.99:
            candidates.append({
                "name": prof["name"],
                "distinct": d,
                "confidence_in_role": sem["confidence"],
                "is_strict_pk": True,
            })
        elif sem["confidence"] >= 0.85:
            candidates.append({
                "name": prof["name"],
                "distinct": d,
                "confidence_in_role": sem["confidence"],
                "is_strict_pk": False,
            })

    if not candidates:
        return {
            "primary_key": None,
            "grain_text": "每列代表一筆紀錄(沒偵測到 primary key)",
            "confidence": 0.3,
            "candidates": [],
        }

    # 優先嚴格 PK
    strict_pks = [c for c in candidates if c["is_strict_pk"]]
    chosen = strict_pks[0] if strict_pks else candidates[0]

    return {
        "primary_key": chosen["name"],
        "grain_text": f"每列代表一個 {chosen['name']}",
        "confidence": 0.95 if chosen["is_strict_pk"] else 0.70,
        "candidates": candidates,
    }


# ============================================================
# Data limitations 推論
# ============================================================
def infer_data_limitations(column_results: list[dict]) -> dict[str, list[str]]:
    """從 semantic profile 自動推 data_limitations。

    Rule:
      - 沒 date_dimension / datetime_dimension → 不支援趨勢分析
      - 沒 measure_amount → 不支援金額分析(只是 warning,不是硬限制)
      - 全是 dimension(沒 measure_*)→ 不支援數值聚合
    """
    has_date = any(c["role"] in ("date_dimension", "datetime_dimension")
                   for c in column_results)
    has_amount = any(c["role"] == "measure_amount" for c in column_results)
    has_any_measure = any(c["role"].startswith("measure_") for c in column_results)

    missing = []
    not_supported = []

    if not has_date:
        missing.append("No confirmed date column")
        not_supported.append(
            "Trend analysis (時間序列 / 月度 / 季節性) "
            "unless user confirms a date field"
        )
    if not has_amount:
        missing.append("No confirmed amount column")
    if not has_any_measure:
        not_supported.append(
            "Numeric aggregation (sum / avg / median) — "
            "資料集無 measure 欄位"
        )
    return {
        "missing_dimensions": missing,
        "not_supported_analysis": not_supported,
    }


# ============================================================
# KPI 自動建議
# ============================================================
def suggest_kpis(column_results: list[dict]) -> dict[str, dict]:
    """從 measure_* 欄位自動建議 KPI。

    Rule:
      - measure_count → KPI: total_<col> = sum(col)
      - measure_amount → KPI: total_<col> + avg_<col>
      - measure_duration → KPI: avg_<col> + median_<col> + p95_<col>
      - measure_percentage → KPI: avg_<col>
    """
    kpis: dict[str, dict] = {}
    for sem in column_results:
        role = sem["role"]
        col = sem.get("name")  # 由 caller 注入(在 build_metadata 內處理)
        # 這個函式被 build_metadata 呼叫時會帶 'name' 進來
        if not col:
            continue
        if role == "measure_count":
            kpis[f"total_{col}"] = {
                "name": f"Total {col}",
                "formula": f"sum({col})",
                "important_note": f"{col} 為計數,可加總",
                "auto_suggested": True,
                "user_confirmed": False,
            }
        elif role == "measure_amount":
            kpis[f"total_{col}"] = {
                "name": f"Total {col}",
                "formula": f"sum({col})",
                "important_note": "金額類 KPI",
                "auto_suggested": True,
                "user_confirmed": False,
            }
            kpis[f"avg_{col}"] = {
                "name": f"Average {col}",
                "formula": f"mean({col})",
                "important_note": "金額平均",
                "auto_suggested": True,
                "user_confirmed": False,
            }
        elif role == "measure_duration":
            unit = sem.get("unit", "days")
            kpis[f"avg_{col}"] = {
                "name": f"Average {col}",
                "formula": f"mean({col})",
                "important_note": f"unit={unit}",
                "auto_suggested": True,
                "user_confirmed": False,
            }
            kpis[f"p95_{col}"] = {
                "name": f"P95 {col}",
                "formula": f"quantile({col}, 0.95)",
                "important_note": f"unit={unit},長尾分佈用 P95 比平均更穩健",
                "auto_suggested": True,
                "user_confirmed": False,
            }
            kpis[f"median_{col}"] = {
                "name": f"Median {col}",
                "formula": f"median({col})",
                "important_note": f"unit={unit}",
                "auto_suggested": True,
                "user_confirmed": False,
            }
        elif role == "measure_percentage":
            unit = sem.get("unit", "percent")
            kpis[f"avg_{col}"] = {
                "name": f"Average {col}",
                "formula": f"mean({col})",
                "important_note": f"百分比/比率,unit={unit};不可 sum",
                "auto_suggested": True,
                "user_confirmed": False,
            }
    return kpis


# ============================================================
# Sample questions 自動建議
# ============================================================
def suggest_sample_questions(
    column_results_with_names: list[dict],
    kpis: dict,
) -> list[str]:
    """從 semantic profile + KPI 自動建議 main_business_questions。"""
    questions = []
    has_kpi = bool(kpis)
    measure_cols = [
        c["name"] for c in column_results_with_names
        if c.get("role", "").startswith("measure_")
    ]
    dimension_cols = [
        c["name"] for c in column_results_with_names
        if c.get("role") in ("dimension", "categorical_status")
    ]
    date_cols = [
        c["name"] for c in column_results_with_names
        if c.get("role") in ("date_dimension", "datetime_dimension")
    ]

    # 基礎分佈 / 排名問題
    if measure_cols and dimension_cols:
        m = measure_cols[0]
        d = dimension_cols[0]
        questions.append(f"比較各 {d} 的 {m} 平均")
        questions.append(f"找出 {m} 最高的 Top 5 {d}")
    if measure_cols:
        m = measure_cols[0]
        questions.append(f"畫 {m} 的分佈圖,並標示平均、中位數、P95")
    if has_kpi:
        # 取第一個 KPI
        first_kpi = next(iter(kpis.keys()))
        questions.append(f"算出整體 {first_kpi}")
    if date_cols and measure_cols:
        questions.append(f"{measure_cols[0]} 隨 {date_cols[0]} 的趨勢")

    return questions[:5]   # 最多 5 條


# ============================================================
# Charting guidance(基礎)
# ============================================================
def suggest_charting_guidance(
    column_results_with_names: list[dict],
) -> dict[str, Any]:
    """從 semantic profile 推荐基礎圖表。MVP 簡化版,M3 之後可擴。"""
    recommended_charts: dict[str, dict] = {}

    measure_cols = [
        c for c in column_results_with_names
        if c["role"].startswith("measure_")
    ]
    dimension_cols = [
        c for c in column_results_with_names
        if c["role"] in ("dimension", "categorical_status")
    ]
    date_cols = [
        c for c in column_results_with_names
        if c["role"] in ("date_dimension", "datetime_dimension")
    ]

    # 1. 排名 bar chart(dim × measure)
    if dimension_cols and measure_cols:
        d = dimension_cols[0]
        m = measure_cols[0]
        recommended_charts["ranking_bar"] = {
            "chart_type": "bar",
            "x": d["name"],
            "y": m["name"],
            "description": f"比較各 {d['name']} 的 {m['name']}",
        }
    # 2. Histogram for duration / amount
    for c in measure_cols:
        if c["role"] in ("measure_duration", "measure_amount"):
            recommended_charts[f"histogram_{c['name']}"] = {
                "chart_type": "histogram",
                "x": c["name"],
                "description": f"{c['name']} 分佈",
            }
            break
    # 3. Trend line if has date
    if date_cols and measure_cols:
        recommended_charts["trend_line"] = {
            "chart_type": "line",
            "x": date_cols[0]["name"],
            "y": measure_cols[0]["name"],
            "description": "時間趨勢",
        }

    return {"recommended_charts": recommended_charts}


# ============================================================
# 主入口
# ============================================================
def build_metadata(
    dataset_doc: dict,
    table_doc: dict,
    column_profiles: list[dict],
    semantic_results: list[dict],
    grain_text: Optional[str] = None,
    primary_key: Optional[str] = None,
    user_business_description: Optional[str] = None,
    user_main_questions: Optional[list[str]] = None,
    user_data_limitations: Optional[dict] = None,
) -> dict[str, Any]:
    """組裝完整的 GenBI-compatible metadata dict。

    Args:
        dataset_doc: uploaded_datasets 的文件(含 _id, dataset_name)
        table_doc: upload_tables 的文件(含 table_id, row_count, column_count)
        column_profiles: data_profiler 的 columns list
        semantic_results: semantic_profiler 的 columns list(順序同上)
        grain_text: 使用者確認的 grain 文字(None 則 auto-infer)
        primary_key: 使用者確認的 primary key(None 則 auto-infer)
        user_business_description: 使用者填的 description(None 用 default)
        user_main_questions: 使用者填的 sample questions(None 用 auto-suggest)
        user_data_limitations: 使用者填的 limitations(None 用 auto-infer)

    Returns:
        完整 metadata dict
    """
    dataset_id = dataset_doc["_id"]
    dataset_name = dataset_doc.get("dataset_name") or dataset_id
    table_id = table_doc["table_id"]
    row_count = table_doc.get("row_count", 0)

    # 把 column name 注入 semantic_results,方便後續 helper 用
    enriched_results: list[dict] = []
    for prof, sem in zip(column_profiles, semantic_results):
        merged = dict(sem)
        merged["name"] = prof["name"]
        merged["warnings"] = prof.get("warnings", [])
        enriched_results.append(merged)

    # Grain
    if not grain_text:
        grain_info = infer_grain(enriched_results, column_profiles, row_count)
        grain_text = grain_info["grain_text"]
        if not primary_key:
            primary_key = grain_info["primary_key"]

    # KPI suggestions
    kpis = suggest_kpis(enriched_results)

    # Sample questions
    if user_main_questions is None:
        sample_qs = suggest_sample_questions(enriched_results, kpis)
    else:
        sample_qs = user_main_questions

    # Charting guidance
    charting = suggest_charting_guidance(enriched_results)

    # Data limitations
    if user_data_limitations is None:
        limitations = infer_data_limitations(enriched_results)
    else:
        limitations = user_data_limitations

    # Build fields dict
    fields: dict[str, dict] = {}
    for prof, sem in zip(column_profiles, enriched_results):
        col_name = prof["name"]
        role = sem.get("role", "unknown")

        # M4b+: PII override — pii_info.is_pii=True 強制蓋成 semantic_role='pii'
        # 並把 pii_info 寫進 field metadata 給 LLM prompt 用
        pii_info = prof.get("pii_info") or {}
        is_pii = bool(pii_info.get("is_pii"))
        if is_pii:
            role = "pii"
            sem["role"] = "pii"
            sem["confidence"] = max(sem.get("confidence", 0.0),
                                      float(pii_info.get("confidence", 0.0)))
            sem["reason"] = (
                f"PII detected ({pii_info.get('pii_type')}): "
                f"{pii_info.get('reason', '')}"
            )

        props = ROLE_PROPERTIES.get(role, ROLE_PROPERTIES.get("unknown"))
        # M4b+: PII role 沒在 ROLE_PROPERTIES,sentinel(LLM prompt 看到 pii 就跳過 chart label)
        if role == "pii" and (not props or "default_aggregation" not in props):
            props = {
                "default_aggregation": "no_agg",
                "recommended_use": ["label_only", "count_distinct"],
                "not_recommended_use": ["display_in_chart", "list_in_insight",
                                          "sum", "average"],
                "is_dimension": False,
                "is_measure": False,
                "is_identifier": True,   # PII 通常含 identifier 性質
            }

        # allowed_values 從 categorical_status / boolean_flag 自動補
        allowed_values = None
        if role == "categorical_status":
            top = prof.get("top_values") or []
            allowed_values = {
                str(t["value"]): {"count": t["count"]}
                for t in top
            }
        elif role == "boolean_flag":
            sample = prof.get("sample_values") or []
            allowed_values = [str(v) for v in sample[:5]]

        fields[col_name] = {
            "type": prof.get("physical_type", "unknown"),
            "semantic_role": role,
            "description": sem.get("description") or f"{col_name}",
            "unit": sem.get("unit", ""),
            "default_aggregation": props["default_aggregation"],
            "recommended_use": list(props["recommended_use"]),
            "not_recommended_use": list(props["not_recommended_use"]),
            "is_dimension": props["is_dimension"],
            "is_measure": props["is_measure"],
            "is_identifier": props["is_identifier"],
            "confidence": sem.get("confidence", 0.4),
            "user_confirmed": False,   # caller 在 user click confirm 後改 True
            "allowed_values": allowed_values,
            "warnings": prof.get("warnings", []),
            "rule_hits": sem.get("rule_hits", []),
            "llm_used": sem.get("llm_used", False),
            "llm_agreement": sem.get("llm_agreement"),
        }

    # 商業 description
    if user_business_description:
        biz_desc = user_business_description
    else:
        biz_desc = (
            f"User uploaded dataset `{dataset_name}` for ad-hoc analysis. "
            f"{row_count:,} rows × {len(fields)} columns. "
            f"Grain: {grain_text}."
        )

    return {
        "dataset_id": dataset_id,
        "dataset_name": dataset_name,
        "source_type": "upload",   # ★ 關鍵旗標
        "business_context": {
            "business_description": biz_desc,
            "main_business_questions": sample_qs,
            "domain": "upload",
        },
        "recommended_mongodb": {
            # upload-driven 不用 MongoDB pipeline,保留 key 避免 LLMService 炸
            "database": "<n/a — uploaded dataset>",
            "collections": {},
            "join_key": None,
        },
        "collections": {
            table_id: {
                "primary_key": primary_key,
                "grain": grain_text,
                "description": f"Uploaded table from `{dataset_name}`",
                "fields": fields,
            },
        },
        "relationships": [],
        "kpi_definitions": kpis,
        "data_limitations": limitations,
        "charting_guidance": charting,
    }


# ============================================================
# Confidence summary
# ============================================================
def summarize_confidence(metadata: dict) -> dict[str, int]:
    """統計 metadata 內各欄位 confidence 等級。

    Returns:
        {"high_confidence_fields": N, "medium_confidence_fields": N,
         "low_confidence_fields": N}
    """
    high = medium = low = 0
    for coll in metadata.get("collections", {}).values():
        for field_meta in coll.get("fields", {}).values():
            conf = float(field_meta.get("confidence", 0))
            if conf >= 0.85:
                high += 1
            elif conf >= 0.6:
                medium += 1
            else:
                low += 1
    return {
        "high_confidence_fields": high,
        "medium_confidence_fields": medium,
        "low_confidence_fields": low,
    }
