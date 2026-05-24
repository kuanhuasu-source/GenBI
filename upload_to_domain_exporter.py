"""
upload_to_domain_exporter.py — v0.15.0+ (M5.4)

把 confirmed upload dataset 的 metadata 「graduate」成正式 schema-driven domain,
寫進 `domain_metadata` collection,讓上傳 dataset 出現在主 app.py sidebar
Domain switcher,跟 tflex / ecommerce / healthcare 並列。

# 為什麼要做這個

對齊 spec §4.2 item 7:upload dataset 經多輪 review / confirm / save metric 後
本來就 production-ready,該允許 graduate 成永久 domain(避免每次重 upload)。

# 流程

```
caller(UI)點 "Promote to schema-driven domain" 按鈕
   ↓
1. 取 upload dataset 的 active confirmed metadata
2. Validate:status 必須 'confirmed'(防止把 draft graduate)
3. 轉換 metadata format(upload-specific → schema-driven generic)
   - 移除 source_type='upload' 旗標(graduate 後是 static 路徑)
   - upload_table_id → collection name (e.g. 'sheet1' → 'projects')
   - 保留 fields / kpi_definitions / data_limitations / charting_guidance
4. 透過 prompt_repository.save_new_metadata_version 寫進 domain_metadata
   (跟既有 tflex / ecommerce 同一 collection,同一 schema)
5. 回 user 該 domain 已建,可在主 app sidebar 切過去
```

# 注意

- **不刪原 upload dataset** — graduate 是 export,upload 仍存在
- 後續對 graduated domain 的修改要透過正式 admin/ CLI 或 metadata page
- 對應 upload 端的 dataset_id 跟 domain_id 是兩個概念 — graduate 後脫鉤
"""

from __future__ import annotations

import copy
import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ============================================================
# Helpers
# ============================================================
def _normalize_domain_id(name: str) -> str:
    """把 dataset_name 轉成合法 domain_id(snake_case ascii)。"""
    s = name.strip().lower()
    # 移除副檔名
    s = re.sub(r"\.(csv|xlsx?|parquet)$", "", s)
    # 連續空白 / 特殊 → 底線
    s = re.sub(r"[\s\-]+", "_", s)
    # 只留 ascii alnum + underscore
    s = re.sub(r"[^a-z0-9_]", "", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "uploaded_domain"


# ============================================================
# Convert upload metadata → schema-driven metadata
# ============================================================
def convert_upload_metadata_to_domain(
    upload_metadata: dict,
    target_domain_id: str,
    target_collection_name: Optional[str] = None,
) -> dict:
    """把 UploadMetadataProvider 拿到的 dict 轉成 schema-driven domain dict。

    Schema-driven dict 大致 schema(對齊 tflex_task_metadata_agent_v3):
    ```
    {
      "dataset_id": "...",
      "dataset_name": "...",
      "business_context": {...},
      "recommended_mongodb": {database, collections, join_key},
      "collections": { "<coll_name>": {primary_key, fields: {...}}, ... },
      "relationships": [...],
      "kpi_definitions": {...},
      "data_limitations": {...},
      "charting_guidance": {...},
    }
    ```

    Args:
        upload_metadata: from UploadMetadataProvider.get_metadata()
        target_domain_id: 新 domain 識別碼(snake_case)
        target_collection_name:對應 upload table_id 在 schema-driven 內的名字。
            若 None 用原 upload table_id(通常 'sheet1')。

    Returns:
        Schema-driven metadata dict
    """
    out = copy.deepcopy(upload_metadata)

    # 1. 移除 upload-specific 旗標
    out.pop("source_type", None)
    out["dataset_id"] = target_domain_id

    # 2. 改 dataset_name(若沒指定保留)
    if "dataset_name" not in out:
        out["dataset_name"] = target_domain_id

    # 3. 重命名 collection(若指定)
    if target_collection_name:
        colls = out.get("collections", {}) or {}
        if len(colls) == 1:
            old_name = next(iter(colls.keys()))
            if old_name != target_collection_name:
                out["collections"] = {target_collection_name: colls[old_name]}
        # 多 collection 情況不自動 rename(graduate UI 應該 prompt user)

    # 4. recommended_mongodb 補預設(schema-driven prompt 需要)
    if not out.get("recommended_mongodb"):
        first_coll = next(iter(out.get("collections", {}).keys()), "main")
        out["recommended_mongodb"] = {
            "database": f"graduated_{target_domain_id}",
            "collections": {first_coll: first_coll},
            "join_key": None,
        }

    # 5. 確保 fields 沒留 upload-specific keys(rule_hits / llm_used 等)
    for coll in (out.get("collections") or {}).values():
        for field in (coll.get("fields") or {}).values():
            field.pop("rule_hits", None)
            field.pop("llm_used", None)
            field.pop("llm_agreement", None)

    return out


# ============================================================
# Exporter service
# ============================================================
class UploadToDomainExporter:
    """Graduate upload dataset → schema-driven domain。

    Usage:
        exporter = UploadToDomainExporter(prompt_repo, upload_repo)
        result = exporter.graduate(
            dataset_id="upload_20260522_xxx",
            target_domain_id="projects_q1",
            target_collection_name="projects",
            user="alice",
        )
    """

    def __init__(self, prompt_repo, upload_repo):
        """
        Args:
            prompt_repo: PromptRepository(寫 domain_metadata collection)
            upload_repo: UploadRepository(讀 upload metadata)
        """
        self.prompt_repo = prompt_repo
        self.upload_repo = upload_repo

    def graduate(
        self,
        dataset_id: str,
        target_domain_id: Optional[str] = None,
        target_collection_name: Optional[str] = None,
        user: str = "anonymous",
        notes: str = "",
        require_confirmed: bool = True,
    ) -> dict:
        """把 upload dataset graduate 到正式 schema-driven domain。

        Args:
            dataset_id: upload dataset _id
            target_domain_id: 目標 domain 名(若 None 自動從 dataset_name 推)
            target_collection_name:轉 schema-driven 後的 collection 名
            user: 操作者
            notes: 寫進 metadata version note
            require_confirmed: True 時拒絕 graduate draft metadata

        Returns:
            {
              "domain_id": str,
              "metadata_version_id": ObjectId | str,
              "status": "graduated",
              "warning": Optional[str],
            }
        """
        # 1. Get upload active metadata
        active = self.upload_repo.get_active_metadata(dataset_id)
        if not active:
            raise ValueError(
                f"Upload dataset `{dataset_id}` 沒 active metadata,無法 graduate"
            )
        if require_confirmed and active.get("confirmation_status") != "confirmed":
            raise ValueError(
                f"Upload dataset `{dataset_id}` metadata 尚未 confirmed "
                f"(status={active.get('confirmation_status')}),"
                "不可 graduate。請先在 Upload Workspace 點 Confirm。"
            )

        # 2. Derive target_domain_id
        if not target_domain_id:
            dataset_doc = self.upload_repo.get_dataset(dataset_id)
            name = (dataset_doc.get("dataset_name") if dataset_doc else dataset_id)
            target_domain_id = _normalize_domain_id(name)
        else:
            target_domain_id = _normalize_domain_id(target_domain_id)

        # 3. Check domain 不該已存在(否則 user 該選 "version up" 不是 graduate)
        try:
            existing = self.prompt_repo.get_metadata(target_domain_id)
            if existing:
                logger.warning(
                    f"Domain `{target_domain_id}` 已存在,graduate 會出 v2"
                )
        except KeyError:
            pass   # 不存在,正常

        # 4. Convert metadata
        upload_md = active["metadata"]
        domain_md = convert_upload_metadata_to_domain(
            upload_metadata=upload_md,
            target_domain_id=target_domain_id,
            target_collection_name=target_collection_name,
        )

        # 5. Write to domain_metadata via prompt_repo
        version_id = self.prompt_repo.save_new_metadata_version(
            domain=target_domain_id,
            metadata=domain_md,
            notes=notes or f"Graduated from upload dataset {dataset_id} by {user}",
            activate=True,
        )

        return {
            "domain_id": target_domain_id,
            "metadata_version_id": str(version_id) if version_id else None,
            "status": "graduated",
            "warning": None,
            "source_upload_dataset_id": dataset_id,
        }
