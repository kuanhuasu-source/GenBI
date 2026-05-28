"""
analysis_asset_service.py — v0.13.2+ (M3A)

把成功的 chat analysis 沉澱成可重用資產:
  - Saved Chart       完整保存 chart option + lineage,可重執行 / 重命名 / 刪除
  - Saved Metric      保存 KPI 計算邏輯 + **寫回 dynamic metadata 的 kpi_definitions**
  - Analysis Template 保存「query + plan」,可重新以同 query 觸發分析

# 設計重點

- **Saved Metric 寫回 metadata 走 MetadataCorrectionService**(M2 已建),每次 save
  產一個新 metadata_version。這保證:
    - 完整 audit trail(誰在何時加哪個 KPI)
    - 可 rollback(切回舊 version)
    - 後續 LLM call 在 prompt 注入新 KPI(metadata.kpi_definitions 是 prompt 一部分)

- **Rerun 走 replay source_query**:不用 saved code 直接 exec,改用
  `UploadAnalysisService.handle_query(session_id, asset.source_query)` 重新跑
  5-phase。優點是 metadata 演進後仍能正確分析;缺點是 LLM output 可能微異(被
  variance 接受,跟 M1B/M2/M3 baseline 噪聲帶一致)。

- **Asset lineage 是 immutable snapshot**:Save 時把 phase_0/A/B/C code 完整存進去,
  audit / debug 都能回看。但 rerun 不參考它(走 replay)。

# 對外 API

```python
service = AnalysisAssetService(upload_repo, correction_service)

# Save
asset_id = service.save_chart(session_id, last_result, name=..., description=...)
asset_id = service.save_metric(session_id, last_result, kpi_key=..., name=..., formula=..., ...)
asset_id = service.save_template(session_id, last_result, name=..., description=...)

# Browse
assets = service.list(dataset_id=..., asset_type=...)

# Rerun(僅產 replay query 字串,由 caller(UI)送進 chat input)
query = service.get_replay_query(asset_id)

# Manage
service.rename(asset_id, new_name, description=...)
service.delete(asset_id)
```
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from upload_repository import UploadRepository, generate_asset_id
from metadata_correction_service import MetadataCorrectionService

logger = logging.getLogger(__name__)


# ============================================================
# Service
# ============================================================
class AnalysisAssetService:
    """Analysis Assets 的 CRUD + Saved Metric 回寫 metadata 的 orchestrator。"""

    def __init__(
        self,
        upload_repo: UploadRepository,
        correction_service: MetadataCorrectionService,
    ):
        self.repo = upload_repo
        self.corrections = correction_service

    # ============================================================
    # Save
    # ============================================================
    def save_chart(
        self,
        dataset_id: str,
        session_id: str,
        analysis_result: dict,
        name: str,
        description: str = "",
        user: str = "anonymous",
    ) -> str:
        """保存一張圖表 — 從 analysis_result(UploadAnalysisService.handle_query 的回傳)取資料。

        Args:
            dataset_id: 來源 dataset
            session_id: 來源 session(用於 lineage)
            analysis_result: handle_query 回傳的 dict(必須 status='completed')
            name: 使用者給的圖表名
            description: 使用者給的描述
            user: 保存者

        Returns:
            asset_id

        Raises:
            ValueError: result.status != 'completed' 或缺欄位
        """
        self._validate_completed(analysis_result)
        active_meta_doc = self.repo.get_active_metadata(dataset_id)
        if not active_meta_doc:
            raise ValueError(f"Dataset `{dataset_id}` 沒 active metadata")

        asset_id = generate_asset_id("saved_chart")
        chart_option = analysis_result.get("chart_option") or {}
        # 把 Q.head(N) 存進 lineage 給 audit / preview 用
        Q = analysis_result.get("Q")
        q_preview = []
        if Q is not None:
            try:
                q_preview = Q.head(20).to_dict(orient="records")
            except Exception:
                q_preview = []

        doc = {
            "_id": asset_id,
            "asset_type": "saved_chart",
            "dataset_id": dataset_id,
            "metadata_version": active_meta_doc["version"],
            "name": name,
            "description": description,
            "source_query": self._extract_source_query(session_id),
            "asset_payload": {
                "q_columns": analysis_result.get("Q_info", {}).get("columns", []),
                "chart_engine": ("echarts" if chart_option
                                  else "plotly"),
                "chart_option": chart_option,
                "use_table_fallback": analysis_result.get("use_table_fallback", False),
            },
            "lineage": {
                "session_id": session_id,
                "trace_id": analysis_result.get("trace_id"),
                "phase_0_plan": analysis_result.get("plan_text", ""),
                "phase_a_code": analysis_result.get("phase_a_code", ""),
                "phase_b_code": analysis_result.get("phase_b_code", ""),
                "phase_c_code": analysis_result.get("phase_c_code", ""),
                "q_preview": q_preview,
            },
            "created_by": user,
        }
        self.repo.create_asset(doc)
        return asset_id

    def save_metric(
        self,
        dataset_id: str,
        session_id: str,
        analysis_result: dict,
        kpi_key: str,
        name: str,
        formula: str,
        important_note: str = "",
        description: str = "",
        user: str = "anonymous",
    ) -> str:
        """保存一個 KPI metric — **同時寫回 dynamic metadata.kpi_definitions**。

        Args:
            kpi_key: KPI 識別字(用作 metadata.kpi_definitions[<key>] + asset_payload.kpi_key)
            name: KPI 顯示名(中文或英文)
            formula: 公式字串(例 `mean(leadtime)` / `sum(amount)` / `Q['ai_count'].sum() / Q['total'].sum()`)
            important_note: 重要說明(unit / 限制)

        會做兩件事:
            1. metadata_correction_service.apply_corrections 寫一個 kpi 新增 correction
               → 出新 metadata_version + audit log
            2. 寫 analysis_asset 文件,綁定 new metadata_version

        Returns:
            asset_id
        """
        self._validate_completed(analysis_result)
        active_meta_doc = self.repo.get_active_metadata(dataset_id)
        if not active_meta_doc:
            raise ValueError(f"Dataset `{dataset_id}` 沒 active metadata")

        # ── Step 1:寫回 kpi_definitions 走 MetadataCorrectionService ──
        new_kpi_value = {
            "name": name,
            "formula": formula,
            "important_note": important_note,
            "auto_suggested": False,
            "user_confirmed": True,
            "source_query": self._extract_source_query(session_id),
            "source_session_id": session_id,
        }
        corrections = [{
            "target": f"kpi.{kpi_key}.name",
            "old_value": None,
            "new_value": name,
            "reason": f"Save Metric: {name}",
        }]
        # 不能用 target='kpi.<key>' 因為 _apply_correction_to_dict 只認 3 層路徑
        # → 直接走 helper(下方 _inject_kpi_to_metadata)
        new_metadata_version = self._inject_kpi_and_save_version(
            dataset_id=dataset_id,
            active_meta_doc=active_meta_doc,
            kpi_key=kpi_key,
            new_kpi_value=new_kpi_value,
            user=user,
        )

        # ── Step 2:寫 analysis_asset 文件,綁 new metadata_version ──
        Q = analysis_result.get("Q")
        q_preview = []
        if Q is not None:
            try:
                q_preview = Q.head(20).to_dict(orient="records")
            except Exception:
                q_preview = []

        asset_id = generate_asset_id("saved_metric")
        doc = {
            "_id": asset_id,
            "asset_type": "saved_metric",
            "dataset_id": dataset_id,
            "metadata_version": new_metadata_version,
            "name": name,
            "description": description,
            "source_query": self._extract_source_query(session_id),
            "asset_payload": {
                "kpi_key": kpi_key,
                "kpi_name": name,
                "formula": formula,
                "important_note": important_note,
                "q_columns_at_save": analysis_result.get("Q_info", {}).get("columns", []),
            },
            "lineage": {
                "session_id": session_id,
                "trace_id": analysis_result.get("trace_id"),
                "phase_0_plan": analysis_result.get("plan_text", ""),
                "phase_a_code": analysis_result.get("phase_a_code", ""),
                "phase_b_code": analysis_result.get("phase_b_code", ""),
                "phase_c_code": analysis_result.get("phase_c_code", ""),
                "q_preview": q_preview,
            },
            "created_by": user,
        }
        self.repo.create_asset(doc)
        return asset_id

    def save_template(
        self,
        dataset_id: str,
        session_id: str,
        analysis_result: dict,
        name: str,
        description: str = "",
        user: str = "anonymous",
    ) -> str:
        """保存一個 Analysis Template — 只存 query + plan,用 chat input 重執行。"""
        self._validate_completed(analysis_result)
        active_meta_doc = self.repo.get_active_metadata(dataset_id)
        if not active_meta_doc:
            raise ValueError(f"Dataset `{dataset_id}` 沒 active metadata")

        asset_id = generate_asset_id("analysis_template")
        source_query = self._extract_source_query(session_id)
        doc = {
            "_id": asset_id,
            "asset_type": "analysis_template",
            "dataset_id": dataset_id,
            "metadata_version": active_meta_doc["version"],
            "name": name,
            "description": description,
            "source_query": source_query,
            "asset_payload": {
                "template_steps": {
                    "query": source_query,
                    "plan_text": analysis_result.get("plan_text", ""),
                    "expected_chart": (
                        "echarts" if analysis_result.get("chart_option")
                        else "plotly"
                    ),
                    "expected_q_columns": analysis_result.get("Q_info", {}).get("columns", []),
                },
            },
            "lineage": {
                "session_id": session_id,
                "trace_id": analysis_result.get("trace_id"),
                "phase_0_plan": analysis_result.get("plan_text", ""),
                "phase_a_code": analysis_result.get("phase_a_code", ""),
                "phase_b_code": analysis_result.get("phase_b_code", ""),
                "phase_c_code": analysis_result.get("phase_c_code", ""),
                "q_preview": [],
            },
            "created_by": user,
        }
        self.repo.create_asset(doc)
        return asset_id

    # ============================================================
    # Browse / Rerun / Manage
    # ============================================================
    def list(
        self,
        dataset_id: Optional[str] = None,
        asset_type: Optional[str] = None,
        owner: Optional[str] = None,
        include_inactive: bool = False,
        limit: int = 100,
    ) -> list[dict]:
        return self.repo.list_assets(
            dataset_id=dataset_id, asset_type=asset_type, owner=owner,
            include_inactive=include_inactive, limit=limit,
        )

    def get(self, asset_id: str) -> Optional[dict]:
        return self.repo.get_asset(asset_id)

    def get_replay_query(self, asset_id: str) -> Optional[str]:
        """回傳 asset 的 source_query — UI 把它送進 chat input 即可重執行。

        若 asset.metadata_version 跟當前 active 不一致,**不**自動切換,只在
        回傳 dict 帶 warning(由 UI 顯示)。
        """
        asset = self.repo.get_asset(asset_id)
        if not asset:
            return None
        return asset.get("source_query")

    # ============================================================
    # v0.18 M6 (Assets 2.0): Save Derived Table / Save Analysis Template
    # ============================================================
    def save_derived_table(
        self,
        session_id: str,
        step_id: str,
        name: str,
        description: str = "",
        user: str = "anonymous",
    ) -> str:
        """Persist a completed M5 step's output as a reusable derived table.

        Per spec §17 M6 + rule 27, the asset doc binds:
          - dataset_id          (lineage to source upload)
          - metadata_version    (snapshot — drift check uses this)
          - source_step_ids     ([step_id] — provenance to M5 chain)
          - storage path        (referenced from the step, not copied —
                                 the step parquet is the source of truth;
                                 if the dataset is deleted both go)

        Args:
            session_id: M5 analysis session the step belongs to.
            step_id: A completed analysis_step (action_type ∈
                {extract_data, add_column, aggregate, create_table}).
                `visualize` steps don't materialize data and are rejected.

        Returns:
            asset_id.

        Raises:
            ValueError on missing session / step, failed step, visualize
            step (no data), or dataset missing active metadata.
        """
        session = self.repo.get_session(session_id)
        if not session:
            raise ValueError(f"session `{session_id}` not found")
        step = self.repo.get_analysis_step(step_id)
        if not step:
            raise ValueError(f"step `{step_id}` not found")
        if step.get("session_id") != session_id:
            raise ValueError(
                f"step `{step_id}` does not belong to session `{session_id}`"
            )
        if step.get("status") != "completed":
            raise ValueError(
                f"step `{step_id}` has status `{step.get('status')}` — "
                f"can only save completed steps"
            )
        if step.get("action_type") == "visualize":
            raise ValueError(
                "save_derived_table: visualize steps don't produce data; "
                "use save_chart() for chart assets instead"
            )
        storage = step.get("storage")
        if not storage or not storage.get("path"):
            raise ValueError(
                f"step `{step_id}` has no materialized storage — "
                f"cannot be saved as derived table"
            )

        dataset_id = session["dataset_id"]
        active_meta = self.repo.get_active_metadata(dataset_id)
        if not active_meta:
            raise ValueError(
                f"Dataset `{dataset_id}` has no active metadata"
            )

        asset_id = generate_asset_id("saved_derived_table")
        doc = {
            "_id": asset_id,
            "asset_type": "saved_derived_table",
            "dataset_id": dataset_id,
            "metadata_version": active_meta["version"],
            "name": name,
            "description": description,
            # source_query is required by create_asset; for step-based
            # assets the originating user_query is the closest analog.
            "source_query": step.get("user_query", "") or f"step:{step_id}",
            "source_step_ids": [step_id],   # spec rule 27
            "asset_payload": {
                "output_table": step.get("output_table"),
                "output_schema": step.get("output_schema") or [],
                "row_count": step.get("row_count", 0),
                "action_type": step.get("action_type"),
                "params": step.get("params") or {},
            },
            "storage": storage,             # reference, not copy
            "lineage": {
                "session_id": session_id,
                "step_no": step.get("step_no"),
                "user_query": step.get("user_query", ""),
                "input_tables": step.get("input_tables") or [],
            },
            "created_by": user,
        }
        self.repo.create_asset(doc)
        return asset_id

    def save_template_from_steps(
        self,
        session_id: str,
        step_ids: list[str],
        name: str,
        description: str = "",
        user: str = "anonymous",
    ) -> str:
        """Persist a chain of M5 steps as a replayable analysis template.

        Unlike save_template (which preserves a single 5-phase
        `analysis_result`), this captures a multi-step M5 sequence so
        the user can replay extract→add→aggregate→visualize on a
        different dataset with the same shape.

        Per spec §17 M6 + spec §14.5 #11 (test acceptance):
          - source_step_ids preserves the full chain
          - template_payload serializes each step's action + params

        Args:
            session_id: must exist.
            step_ids: ordered list of step_ids to include in the
                template (typically all completed steps in the session,
                but caller can subset).

        Raises:
            ValueError on missing session / unknown step / failed step
            in the list / empty step_ids.
        """
        if not step_ids:
            raise ValueError("save_template_from_steps: step_ids empty")
        session = self.repo.get_session(session_id)
        if not session:
            raise ValueError(f"session `{session_id}` not found")
        dataset_id = session["dataset_id"]
        active_meta = self.repo.get_active_metadata(dataset_id)
        if not active_meta:
            raise ValueError(
                f"Dataset `{dataset_id}` has no active metadata"
            )

        # Fetch + validate all referenced steps
        step_specs: list[dict] = []
        for sid in step_ids:
            step = self.repo.get_analysis_step(sid)
            if not step:
                raise ValueError(f"step `{sid}` not found")
            if step.get("session_id") != session_id:
                raise ValueError(
                    f"step `{sid}` does not belong to session `{session_id}`"
                )
            if step.get("status") != "completed":
                raise ValueError(
                    f"step `{sid}` has status `{step.get('status')}` — "
                    f"template requires all steps to be completed"
                )
            step_specs.append({
                "step_no": step.get("step_no"),
                "action_type": step.get("action_type"),
                "params": step.get("params") or {},
                "user_query": step.get("user_query", ""),
                "input_tables": step.get("input_tables") or [],
                "output_table": step.get("output_table"),
            })

        # Sort by step_no so replay order matches original execution
        step_specs.sort(key=lambda s: s.get("step_no", 0))

        asset_id = generate_asset_id("analysis_template")
        # Compose a representative source_query — caller-friendly preview
        # of what this template replays. Prefer the first non-empty
        # user_query; else fall back to the template name.
        rep_query = next(
            (s.get("user_query") for s in step_specs
             if s.get("user_query")),
            None,
        ) or f"template:{name}"

        doc = {
            "_id": asset_id,
            "asset_type": "analysis_template",
            "dataset_id": dataset_id,
            "metadata_version": active_meta["version"],
            "name": name,
            "description": description,
            "source_query": rep_query,
            "source_step_ids": list(step_ids),   # spec rule 27
            "asset_payload": {
                "n_steps": len(step_specs),
                "steps": step_specs,
            },
            "lineage": {
                "session_id": session_id,
                "step_count": len(step_specs),
            },
            "created_by": user,
        }
        self.repo.create_asset(doc)
        return asset_id

    def metadata_drift_check(self, asset_id: str) -> dict:
        """檢查 asset 的 metadata_version 跟當前 active 是否一致(spec §12A.7 #9)。

        Returns:
            {
              "asset_version": int,
              "active_version": int,
              "is_stale": bool,        # asset_version < active_version
              "warning": str | None,
            }
        """
        asset = self.repo.get_asset(asset_id)
        if not asset:
            return {"asset_version": None, "active_version": None,
                    "is_stale": False, "warning": "Asset 不存在"}
        active_doc = self.repo.get_active_metadata(asset["dataset_id"])
        if not active_doc:
            return {"asset_version": asset["metadata_version"],
                    "active_version": None,
                    "is_stale": True,
                    "warning": "Dataset 已無 active metadata"}
        av = asset["metadata_version"]
        cv = active_doc["version"]
        stale = av < cv
        return {
            "asset_version": av,
            "active_version": cv,
            "is_stale": stale,
            "warning": (
                f"⚠️ 此 asset 是基於 metadata v{av} 建的,"
                f"目前 active 是 v{cv}。重執行結果可能不同。"
                if stale else None
            ),
        }

    def rename(
        self,
        asset_id: str,
        new_name: str,
        description: Optional[str] = None,
    ) -> bool:
        return self.repo.rename_asset(asset_id, new_name, description)

    def delete(self, asset_id: str, hard: bool = False) -> bool:
        """軟刪預設(`is_active=False`);hard=True 才從 DB 真刪除。"""
        if hard:
            return self.repo.hard_delete_asset(asset_id)
        return self.repo.soft_delete_asset(asset_id)

    # ============================================================
    # Internal helpers
    # ============================================================
    @staticmethod
    def _validate_completed(result: dict) -> None:
        if not isinstance(result, dict):
            raise ValueError("analysis_result 必須是 dict")
        if result.get("status") != "completed":
            raise ValueError(
                f"Asset 只能從 status='completed' 的分析存。"
                f"當前 status={result.get('status')}"
            )

    def _extract_source_query(self, session_id: str) -> str:
        """從 session.messages 抽最後一則 user message(就是觸發本次分析的 query)。"""
        session = self.repo.get_session(session_id)
        if not session:
            return ""
        msgs = session.get("messages") or []
        for msg in reversed(msgs):
            if msg.get("role") == "user":
                return msg.get("content", "")
        return ""

    def _inject_kpi_and_save_version(
        self,
        dataset_id: str,
        active_meta_doc: dict,
        kpi_key: str,
        new_kpi_value: dict,
        user: str,
    ) -> int:
        """把新 KPI 注入 metadata.kpi_definitions,寫新 metadata_version + audit。

        為什麼不走 MetadataCorrectionService.apply_corrections:
        後者支援的 target path 是 3 層 `<table>.<col>.<attr>`,不適合用來
        新增整個 kpi_definitions[<key>] = {...}。直接用 repo 寫新版 + 寫 audit。

        Returns:
            新 metadata version number
        """
        import copy
        new_metadata = copy.deepcopy(active_meta_doc["metadata"])
        new_metadata.setdefault("kpi_definitions", {})
        # 若 kpi_key 已存在,allow overwrite 並記在 audit
        was_present = kpi_key in new_metadata["kpi_definitions"]
        new_metadata["kpi_definitions"][kpi_key] = new_kpi_value

        old_version = active_meta_doc["version"]
        new_version = self.repo.save_metadata_version(
            dataset_id=dataset_id,
            metadata=new_metadata,
            confirmation_status="confirmed",  # 來自 user save metric,視為已確認
            confirmed_by=user,
            notes=(
                f"Save Metric: {'overwrote' if was_present else 'added'} "
                f"kpi `{kpi_key}` = {new_kpi_value.get('name')}"
            ),
            activate=True,
        )

        # 寫 audit log(對應 metadata_version_before / after)
        self.repo.save_corrections(
            dataset_id=dataset_id,
            metadata_version_before=old_version,
            metadata_version_after=new_version,
            corrections=[{
                "target": f"kpi_definitions.{kpi_key}",
                "old_value": (active_meta_doc["metadata"]
                              .get("kpi_definitions", {})
                              .get(kpi_key)) if was_present else None,
                "new_value": new_kpi_value,
                "reason": f"Save Metric from session by {user}",
            }],
            user=user,
        )
        return new_version
