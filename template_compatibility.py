"""
template_compatibility.py — v0.15.0+ (M5.6)

判斷一個 Saved Analysis Template 是否能套到不同 dataset。對齊 spec §4.2 item 10。

# 為什麼

Analysis Template 在 M3A 已存 source_query + plan + 期望 Q.columns。MVP
只允許「同 dataset 重執行」(把 query 推回 chat input)。M5.6 加跨 dataset 套
用:檢查目標 dataset 的 schema 是否 cover template 所需欄位 + 語意角色,
讓 user 把 template 套到「相似 schema」的新 dataset。

# Compatibility 評分

- HIGH (≥0.85):全部 expected_q_columns 在 target 都有 + 對應 semantic_role 同
- MEDIUM (0.6-0.85):大部分欄位有 + 缺的可由 user mapping 補
- LOW (<0.6):核心欄位缺,不建議套用

# 用法

```python
from template_compatibility import check_template_compatibility

result = check_template_compatibility(
    template_asset=template_doc,   # from analysis_assets collection
    target_dataset_metadata=md,     # 目標 dataset metadata
)
# result.compatibility_level: "HIGH" | "MEDIUM" | "LOW" | "INCOMPATIBLE"
# result.score: float
# result.missing_columns: [...]
# result.column_mappings: [{template_col: ..., target_col: ..., method: ...}]
```
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ============================================================
# Result dataclass
# ============================================================
@dataclass
class TemplateCompatibilityResult:
    compatibility_level: str   # HIGH / MEDIUM / LOW / INCOMPATIBLE
    score: float                # 0-1
    matched_columns: list[dict] = field(default_factory=list)
    missing_columns: list[str] = field(default_factory=list)
    column_mappings: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    same_source_type: bool = True
    source_query: str = ""


# ============================================================
# Column matching strategies
# ============================================================
def _match_by_exact_name(
    template_col: str,
    target_fields: dict[str, dict],
) -> Optional[str]:
    """Strategy 1:欄名完全相同。"""
    if template_col in target_fields:
        return template_col
    return None


def _match_by_semantic_role(
    template_col: str,
    template_role: str,
    target_fields: dict[str, dict],
) -> Optional[str]:
    """Strategy 2:沒同名,但 target 有同 semantic_role 的欄位。"""
    if not template_role or template_role == "unknown":
        return None
    candidates = [
        n for n, f in target_fields.items()
        if f.get("semantic_role") == template_role
    ]
    if len(candidates) == 1:
        return candidates[0]   # 唯一候選,自動 match
    return None   # 多個 / 0 個,讓 user 手動


# ============================================================
# Main check
# ============================================================
def check_template_compatibility(
    template_asset: dict,
    target_dataset_metadata: dict,
) -> TemplateCompatibilityResult:
    """檢查 Analysis Template 是否能套到 target dataset。

    Args:
        template_asset: analysis_assets 文件,asset_type='analysis_template'
            或 'saved_chart' / 'saved_metric'(都有 q_columns)
        target_dataset_metadata: 目標 dataset 的 metadata dict
            (UploadMetadataProvider.get_metadata)

    Returns:
        TemplateCompatibilityResult
    """
    # 1. 抽 template 的 expected columns
    payload = template_asset.get("asset_payload") or {}
    asset_type = template_asset.get("asset_type", "")

    if asset_type == "analysis_template":
        steps = payload.get("template_steps") or {}
        expected_cols = steps.get("expected_q_columns") or []
        source_query = steps.get("query") or template_asset.get("source_query", "")
    elif asset_type in ("saved_chart", "saved_metric"):
        expected_cols = payload.get("q_columns") or payload.get("q_columns_at_save") or []
        source_query = template_asset.get("source_query", "")
    else:
        return TemplateCompatibilityResult(
            compatibility_level="INCOMPATIBLE",
            score=0.0,
            warnings=[f"未知 asset_type: `{asset_type}`,無法判 compatibility"],
        )

    if not expected_cols:
        return TemplateCompatibilityResult(
            compatibility_level="INCOMPATIBLE",
            score=0.0,
            warnings=["Template 沒記錄 expected_q_columns,無法判 compatibility"],
            source_query=source_query,
        )

    # 2. 取 template 來源 dataset 的 metadata(從 lineage / 自身 metadata_version)
    #    這 MVP 簡化:不取(取需多次 DB 查),只比 Q.columns 字串
    #    semantic_role 比對留 phase 2 加強

    # 3. 拿 target dataset 的 fields(MVP single-collection)
    target_collections = target_dataset_metadata.get("collections") or {}
    if not target_collections:
        return TemplateCompatibilityResult(
            compatibility_level="INCOMPATIBLE",
            score=0.0,
            warnings=["Target dataset 沒 collection"],
            source_query=source_query,
        )
    # 取第一個 collection 的 fields(MVP single-table)
    target_table_id = next(iter(target_collections.keys()))
    target_fields = target_collections[target_table_id].get("fields") or {}

    # 4. 對每個 expected col 跑 matching strategies
    matched = []
    missing = []
    mappings = []
    for col in expected_cols:
        # Strategy 1:exact name
        exact = _match_by_exact_name(col, target_fields)
        if exact:
            matched.append({"template_col": col, "target_col": exact,
                             "method": "exact_name"})
            mappings.append({"template_col": col, "target_col": exact,
                              "method": "exact_name", "confidence": 1.0})
            continue
        # Strategy 2:semantic_role(template 端 role 未知,只能 best-effort)
        # 因為我們沒 template 來源 metadata,跳過 role match,直接記 missing
        missing.append(col)

    # 5. Score
    total = len(expected_cols)
    matched_count = len(matched)
    score = matched_count / total if total > 0 else 0.0

    # 6. Source type check(upload-driven vs schema-driven 不該互套)
    target_source_type = target_dataset_metadata.get("source_type", "static")
    same_source_type = True   # MVP 簡化,後續加 template.dataset metadata source_type 比

    # 7. Level
    if score >= 0.85:
        level = "HIGH"
    elif score >= 0.6:
        level = "MEDIUM"
    elif score > 0:
        level = "LOW"
    else:
        level = "INCOMPATIBLE"

    warnings = []
    if missing:
        warnings.append(
            f"Target dataset 缺 {len(missing)} 個 expected column:"
            f"{', '.join(missing[:5])}{'...' if len(missing) > 5 else ''}"
        )
    if level in ("LOW", "INCOMPATIBLE"):
        warnings.append(
            "建議:用 target dataset 的相似欄位手動改寫 source_query,"
            "或直接重寫 query 適配新 schema。"
        )

    return TemplateCompatibilityResult(
        compatibility_level=level,
        score=round(score, 3),
        matched_columns=matched,
        missing_columns=missing,
        column_mappings=mappings,
        warnings=warnings,
        same_source_type=same_source_type,
        source_query=source_query,
    )


# ============================================================
# 列出 template 可套用的目標 dataset
# ============================================================
def find_compatible_datasets(
    template_asset: dict,
    candidate_metadata_provider,
    min_compatibility: str = "MEDIUM",
) -> list[dict]:
    """掃 candidate provider 內所有 dataset,回 compatibility ≥ min_compatibility 的列表。

    Args:
        template_asset: 要 apply 的 template
        candidate_metadata_provider: 通常是 UploadMetadataProvider
        min_compatibility: "HIGH" | "MEDIUM" | "LOW"

    Returns:
        list of {dataset_id, compatibility, score, warnings}(按 score 降冪)
    """
    level_order = {"INCOMPATIBLE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}
    min_level = level_order.get(min_compatibility, 2)

    results = []
    template_dataset_id = template_asset.get("dataset_id")
    for did in candidate_metadata_provider.list_available():
        if did == template_dataset_id:
            continue   # skip 同 dataset
        try:
            md = candidate_metadata_provider.get_metadata(did)
        except KeyError:
            continue
        compat = check_template_compatibility(template_asset, md)
        if level_order.get(compat.compatibility_level, 0) >= min_level:
            results.append({
                "dataset_id": did,
                "compatibility": compat.compatibility_level,
                "score": compat.score,
                "matched_count": len(compat.matched_columns),
                "missing_count": len(compat.missing_columns),
                "warnings": compat.warnings,
            })

    # Sort by score desc
    results.sort(key=lambda r: -r["score"])
    return results
