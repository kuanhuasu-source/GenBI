"""
metadata_correction_service.py — v0.12.0+

把使用者在 Review UI 上做的修正(改 semantic_role / description / unit /
default_aggregation / grain / data_limitations)apply 到 metadata,
產出新的 metadata version 並寫 audit log。

# 流程

```
caller (Streamlit page) 提交 corrections
   │
   ▼ list of {target, old_value, new_value, reason}
CorrectionService.apply_corrections(...)
   │
   ├─ 1. 從 upload_repository 拿 current active metadata
   ├─ 2. 把每個 correction apply 進 metadata dict
   ├─ 3. 重新 derive(若 grain 改了 → primary_key 變)
   ├─ 4. 寫新 metadata version(is_active=True)
   └─ 5. 寫 upload_user_corrections(audit)
   ▼
回傳 new_version_number
```

# Correction 格式

```python
{
  "target": "sheet1.leadtime.unit",   # 路徑式 key
  "old_value": "unknown",
  "new_value": "days",
  "reason": "User confirmed in review UI"
}
```

Target 路徑語法:`<table_id>.<col_name>.<field_attr>` 或 `<grain>` /
`<data_limitations.missing_dimensions>` 等 top-level。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from upload_repository import UploadRepository
from upload_metadata_generator import summarize_confidence

logger = logging.getLogger(__name__)


# ============================================================
# Path-based mutation
# ============================================================
def _apply_correction_to_dict(
    metadata: dict,
    target: str,
    new_value: Any,
) -> bool:
    """把 target path 對應的 value 改成 new_value。

    Supported targets:
      - `<table>.<col>.<attr>` — 例 `sheet1.leadtime.unit` → fields.leadtime.unit
      - `grain.<table>` — 例 `grain.sheet1` → collections.sheet1.grain
      - `primary_key.<table>` — 例 `primary_key.sheet1` → collections.sheet1.primary_key
      - `business_description` — 整體 description
      - `main_business_questions` — sample questions list
      - `data_limitations.missing_dimensions` — list
      - `data_limitations.not_supported_analysis` — list
      - `kpi.<key>.<attr>` — 例 `kpi.avg_leadtime.user_confirmed`

    Returns True if applied successfully, False if path not found.
    """
    parts = target.split(".")
    if not parts:
        return False

    # Top-level shortcuts
    if target == "business_description":
        metadata.setdefault("business_context", {})
        metadata["business_context"]["business_description"] = new_value
        return True
    if target == "main_business_questions":
        metadata.setdefault("business_context", {})
        metadata["business_context"]["main_business_questions"] = new_value
        return True

    if parts[0] == "grain" and len(parts) == 2:
        table_id = parts[1]
        coll = (metadata.get("collections") or {}).get(table_id)
        if not coll:
            return False
        coll["grain"] = new_value
        return True

    if parts[0] == "primary_key" and len(parts) == 2:
        table_id = parts[1]
        coll = (metadata.get("collections") or {}).get(table_id)
        if not coll:
            return False
        coll["primary_key"] = new_value
        return True

    if parts[0] == "data_limitations" and len(parts) == 2:
        metadata.setdefault("data_limitations", {})
        metadata["data_limitations"][parts[1]] = new_value
        return True

    if parts[0] == "kpi" and len(parts) == 3:
        kpi_key, attr = parts[1], parts[2]
        kpis = metadata.setdefault("kpi_definitions", {})
        if kpi_key not in kpis:
            return False
        kpis[kpi_key][attr] = new_value
        return True

    # Standard path: <table>.<col>.<attr>
    if len(parts) == 3:
        table_id, col_name, attr = parts
        coll = (metadata.get("collections") or {}).get(table_id)
        if not coll:
            return False
        fields = coll.get("fields") or {}
        col = fields.get(col_name)
        if col is None:
            return False
        col[attr] = new_value
        # 連動 update:當 semantic_role 改變,is_dimension/is_measure/is_identifier
        # default_aggregation / recommended_use 也要更新
        if attr == "semantic_role":
            from semantic_profiler import ROLE_PROPERTIES
            props = ROLE_PROPERTIES.get(new_value, ROLE_PROPERTIES["unknown"])
            col["default_aggregation"] = props["default_aggregation"]
            col["recommended_use"] = list(props["recommended_use"])
            col["not_recommended_use"] = list(props["not_recommended_use"])
            col["is_dimension"] = props["is_dimension"]
            col["is_measure"] = props["is_measure"]
            col["is_identifier"] = props["is_identifier"]
        # User 直接改任何 field attr → user_confirmed=True
        col["user_confirmed"] = True
        return True

    logger.warning(f"Unknown correction target path: `{target}`")
    return False


# ============================================================
# Service
# ============================================================
class MetadataCorrectionService:
    """處理 user 對 metadata 的修正,產新 version + 寫 audit。"""

    def __init__(self, upload_repo: UploadRepository):
        self.repo = upload_repo

    def apply_corrections(
        self,
        dataset_id: str,
        corrections: list[dict],
        user: str,
        confirm: bool = False,
    ) -> dict:
        """套用 corrections list,寫新 metadata version,寫 audit log。

        Args:
            dataset_id: 目標 dataset
            corrections: [{target, old_value, new_value, reason}, ...]
            user: 修正者識別(寫進 audit log)
            confirm: True 時把新版 status 設 'confirmed';False 仍是 'draft'

        Returns:
            {
              "version": int,            # 新 version number
              "applied": int,            # 成功 apply 數
              "skipped": int,            # path not found 數
              "skipped_targets": [...],  # 失敗的 targets
              "confidence_summary": {...},
            }
        """
        # 1. 取當前 active metadata
        active_doc = self.repo.get_active_metadata(dataset_id)
        if not active_doc:
            raise ValueError(
                f"No active metadata for dataset `{dataset_id}` — "
                "請先 build_metadata 寫 v1"
            )
        current_version = active_doc["version"]
        metadata = dict(active_doc["metadata"])

        # 2. Apply each correction(deep copy 避免 mutate active doc)
        import copy
        metadata = copy.deepcopy(metadata)

        applied = 0
        skipped: list[str] = []
        for corr in corrections:
            target = corr.get("target")
            new_value = corr.get("new_value")
            if not target:
                skipped.append("<missing target>")
                continue
            ok = _apply_correction_to_dict(metadata, target, new_value)
            if ok:
                applied += 1
            else:
                skipped.append(target)

        # 3. 寫新 version
        new_version = self.repo.save_metadata_version(
            dataset_id=dataset_id,
            metadata=metadata,
            confirmation_status="confirmed" if confirm else "draft",
            confirmed_by=user if confirm else None,
            notes=f"User corrections: applied {applied}, skipped {len(skipped)}",
            activate=True,
        )

        # 4. 寫 audit
        self.repo.save_corrections(
            dataset_id=dataset_id,
            metadata_version_before=current_version,
            metadata_version_after=new_version,
            corrections=corrections,
            user=user,
        )

        return {
            "version": new_version,
            "applied": applied,
            "skipped": len(skipped),
            "skipped_targets": skipped,
            "confidence_summary": summarize_confidence(metadata),
        }

    def confirm_metadata(
        self,
        dataset_id: str,
        user: str,
        notes: str = "",
    ) -> dict:
        """Confirm the currently-active draft metadata.

        Behavior:
          - Writes a new metadata_version with status='confirmed'.
          - v0.18 M7: also merges confirmed/edited rows from
            upload_relationship_candidates into metadata['relationships']
            on the new version (spec §14.5 #5). When no confirmed rels
            exist the field stays absent — backward compat preserved.

        If the active version is already confirmed, no new version is
        written and `already_confirmed=True` is returned.

        Returns:
            {"version": int, "already_confirmed": bool,
             "n_relationships_merged": int}
        """
        active = self.repo.get_active_metadata(dataset_id)
        if not active:
            raise ValueError(f"No active metadata for `{dataset_id}`")
        if active["confirmation_status"] == "confirmed":
            return {
                "version": active["version"],
                "already_confirmed": True,
                "n_relationships_merged": 0,
            }

        # M7: project confirmed/edited relationships into metadata.relationships.
        # The repository defaults to the latest metadata_version's rows,
        # which is what we want (most recent profile).
        # Only the executable fields go into metadata — evidence/audit
        # stays in the dedicated collection.
        merged_relationships: list[dict] = []
        try:
            cands = self.repo.list_relationship_candidates(dataset_id)
            for c in cands:
                if c.get("status") not in ("confirmed", "edited"):
                    continue
                merged_relationships.append({
                    "relationship_id": c.get("relationship_id"),
                    "from_table": c.get("from_table"),
                    "from_field": c.get("from_field"),
                    "to_table": c.get("to_table"),
                    "to_field": c.get("to_field"),
                    "relationship_type": c.get("relationship_type"),
                    "default_join_type": c.get("default_join_type", "left"),
                    "status": c.get("status"),
                })
        except Exception as e:
            # Don't let a missing/empty rel collection block confirm.
            logger.warning(
                f"confirm_metadata: relationship merge skipped "
                f"({type(e).__name__}: {e})"
            )

        new_metadata = dict(active["metadata"])
        if merged_relationships:
            new_metadata["relationships"] = merged_relationships

        new_version = self.repo.save_metadata_version(
            dataset_id=dataset_id,
            metadata=new_metadata,
            confirmation_status="confirmed",
            confirmed_by=user,
            notes=notes or "User confirmed without further edits",
            activate=True,
        )
        return {
            "version": new_version,
            "already_confirmed": False,
            "n_relationships_merged": len(merged_relationships),
        }
