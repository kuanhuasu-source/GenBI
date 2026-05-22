"""
upload_service.py — v0.12.0+

Upload Workspace 入口 service — 把上傳檔案串成完整的 upload → parse → profile pipeline。

# 流程

```
caller (Streamlit page)
   │
   ▼ uploaded file bytes + filename
UploadService.handle_upload(...)
   │
   ├─ 1. 寫 staging file  → uploads/<dataset_id>/source.<ext>
   ├─ 2. 算 sha256
   ├─ 3. 寫 uploaded_datasets 文件(status='uploaded')
   ├─ 4. file_parser.parse_to_parquet  → uploads/<dataset_id>/sheet1.parquet
   ├─ 5. 寫 upload_tables 文件
   ├─ 6. 更新 dataset.status='parsed'
   ├─ 7. data_profiler.profile_dataset  → profile dict
   ├─ 8. upload_repository.save_profile
   └─ 9. 更新 dataset.status='profiled'
   ▼
回傳 dataset_id 給 caller
```

任何一步失敗,把 dataset.status 改成 'error' 並寫 error_message。
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from pathlib import Path
from typing import Any, BinaryIO

from upload_repository import UploadRepository, generate_dataset_id
import file_parser
import data_profiler
import semantic_profiler
import upload_metadata_generator

logger = logging.getLogger(__name__)


# ============================================================
# Helpers
# ============================================================
def compute_sha256(file_path: Path, chunk_size: int = 65536) -> str:
    """串流式 sha256(避免整檔讀進記憶體)。"""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def safe_filename(name: str) -> str:
    """把使用者上傳的 filename 轉成 safe 字串(避免路徑穿越 / shell escape)。
    我們不直接用使用者 filename 當路徑,但會記錄在 file.original_filename 中。"""
    import re
    s = name.strip()
    # 移除路徑分隔符跟控制字元
    s = re.sub(r"[/\\:*?\"<>|\x00-\x1f]", "_", s)
    return s[:255] or "uploaded_file"


# ============================================================
# Service
# ============================================================
class UploadService:
    """Upload Workspace 主 orchestrator。

    Usage:
        service = UploadService(upload_repo, uploads_root=Path("./uploads"))
        dataset_id = service.handle_upload(
            file_bytes=uploaded.read(),
            filename=uploaded.name,
            owner="alan",
        )
    """

    def __init__(
        self,
        upload_repo: UploadRepository,
        uploads_root: Path,
    ):
        """
        Args:
            upload_repo: UploadRepository instance(已接 MongoDB)
            uploads_root: 本機 uploads 根目錄(會 mkdir)
        """
        self.repo = upload_repo
        self.uploads_root = Path(uploads_root)
        self.uploads_root.mkdir(parents=True, exist_ok=True)

    # ============================================================
    # 主入口
    # ============================================================
    def handle_upload(
        self,
        file_obj: BinaryIO | bytes,
        filename: str,
        owner: str,
        table_id: str = "sheet1",
    ) -> str:
        """端到端:接檔 → 解析 → 寫 parquet → profile → 寫 MongoDB。

        Args:
            file_obj: file-like object (例 Streamlit UploadedFile) 或 raw bytes
            filename: 使用者原檔名(用於 file.original_filename + 副檔名偵測)
            owner: 上傳者識別(MVP 直接傳 username 字串)
            table_id: 該 table 的內部 id,預設 "sheet1"

        Returns:
            dataset_id

        Raises:
            file_parser.FileParseError: 副檔名 / 大小 / 內容檢查失敗
            其他例外:DB 寫入 / IO 失敗 — caller 應 catch 並顯示給使用者
        """
        dataset_id = generate_dataset_id()
        dataset_dir = self.uploads_root / dataset_id
        dataset_dir.mkdir(parents=True, exist_ok=True)

        safe_name = safe_filename(filename)
        ext = Path(safe_name).suffix.lower()
        if not ext:
            # 從 file_obj 推不出來 → 就讓 file_parser.validate_file 報錯
            ext = ""
        staging_path = dataset_dir / f"source{ext}"

        # ── 1. 把 bytes 寫到 staging ──
        try:
            if isinstance(file_obj, (bytes, bytearray)):
                staging_path.write_bytes(bytes(file_obj))
            else:
                # file-like object
                with open(staging_path, "wb") as out:
                    if hasattr(file_obj, "seek"):
                        try:
                            file_obj.seek(0)
                        except Exception:
                            pass
                    shutil.copyfileobj(file_obj, out)
        except Exception as e:
            # 寫失敗 → 清掉 dataset_dir
            self._cleanup_dataset_dir(dataset_dir)
            raise RuntimeError(f"寫 staging file 失敗: {e}") from e

        # ── 2. SHA256 + size ──
        try:
            sha = compute_sha256(staging_path)
            size_bytes = staging_path.stat().st_size
        except Exception as e:
            self._cleanup_dataset_dir(dataset_dir)
            raise RuntimeError(f"計算 sha256 失敗: {e}") from e

        # ── 3. 寫 uploaded_datasets(status='uploaded')──
        dataset_doc = {
            "_id": dataset_id,
            "dataset_name": safe_name,
            "owner": owner,
            "source_type": "file_upload",
            "file": {
                "original_filename": filename,
                "stored_path": str(staging_path.relative_to(self.uploads_root.parent))
                    if staging_path.is_relative_to(self.uploads_root.parent)
                    else str(staging_path),
                "file_type": "csv" if ext == ".csv" else "excel" if ext in (".xlsx", ".xls") else "unknown",
                "file_size_bytes": size_bytes,
                "sha256": sha,
            },
            "status": "uploaded",
            "active_metadata_version": None,
            "error_message": None,
        }
        try:
            self.repo.create_dataset(dataset_doc)
        except Exception as e:
            self._cleanup_dataset_dir(dataset_dir)
            raise RuntimeError(f"寫 uploaded_datasets 失敗: {e}") from e

        # ── 4-6. Parse → parquet → upload_tables ──
        try:
            self.repo.update_status(dataset_id, "parsing")
            parse_result = file_parser.parse_to_parquet(
                source_path=staging_path,
                parquet_dir=dataset_dir,
                table_id=table_id,
            )
            table_doc = {
                "dataset_id": dataset_id,
                "table_id": parse_result["table_id"],
                "table_name": parse_result["table_name"],
                "row_count": parse_result["row_count"],
                "column_count": parse_result["column_count"],
                "normalized_columns": parse_result["normalized_columns"],
                "original_to_normalized": parse_result["original_to_normalized"],
                "storage": parse_result["storage"],
                "warnings": parse_result["warnings"],
            }
            self.repo.create_table(table_doc)
            self.repo.update_status(dataset_id, "parsed")
        except file_parser.FileParseError as e:
            self.repo.update_status(dataset_id, "error", error_message=str(e))
            raise
        except Exception as e:
            self.repo.update_status(
                dataset_id, "error",
                error_message=f"未預期錯誤: {type(e).__name__}: {e}",
            )
            raise

        # ── 7-9. Profile → save_profile → status='profiled' ──
        try:
            df = file_parser.load_parquet(parse_result["storage"]["path"])
            profile = data_profiler.profile_dataset(
                tables=[(parse_result["table_id"], df)],
            )
            self.repo.save_profile(dataset_id, profile)
            self.repo.update_status(
                dataset_id, "profiled",
                active_metadata_version=None,
            )
        except Exception as e:
            self.repo.update_status(
                dataset_id, "error",
                error_message=f"Profile 階段失敗: {type(e).__name__}: {e}",
            )
            raise

        # ── 10. (M2+) Semantic profile + metadata v1 draft ──
        # Rule-based only,LLM-assisted refine 留給使用者在 UI 觸發
        try:
            self.regenerate_metadata(dataset_id, use_llm=False)
        except Exception as e:
            # Metadata 失敗不阻塞 upload(使用者可在 UI 重試)
            logger.warning(
                f"M2 metadata v1 build 失敗 dataset_id={dataset_id}: {e}"
            )

        logger.info(f"Upload pipeline 完成: dataset_id={dataset_id}")
        return dataset_id

    # ============================================================
    # Metadata re-generation(M2+)
    # ============================================================
    def regenerate_metadata(
        self,
        dataset_id: str,
        use_llm: bool = False,
        api_url: str = "",
        api_key: str = "",
        model: str = "",
        timeout_s: float = 60.0,
    ) -> int:
        """跑 semantic_profiler + upload_metadata_generator,寫成新 draft metadata version。

        Args:
            dataset_id: 目標 dataset
            use_llm: True 時對 low-confidence column 打 LLM refine
            api_url / api_key / model / timeout_s: use_llm=True 必傳

        Returns:
            new version number
        """
        dataset = self.repo.get_dataset(dataset_id)
        if not dataset:
            raise ValueError(f"Dataset `{dataset_id}` 不存在")

        tables = self.repo.list_tables(dataset_id)
        if not tables:
            raise ValueError(f"Dataset `{dataset_id}` 沒 table")

        profile = self.repo.get_latest_profile(dataset_id)
        if not profile:
            raise ValueError(f"Dataset `{dataset_id}` 沒 profile")

        # MVP single-table:取第一個 table + 對應 profile
        table = tables[0]
        table_id = table["table_id"]
        table_profile = None
        for tp in profile.get("tables", []):
            if tp.get("table_id") == table_id:
                table_profile = tp
                break
        if not table_profile:
            raise ValueError(
                f"Profile 中找不到 table `{table_id}`"
            )

        column_profiles = table_profile.get("columns", [])

        # Semantic inference(可選 LLM-assisted)
        semantic_results = semantic_profiler.profile_columns_semantic(
            column_profiles,
            use_llm=use_llm,
            api_url=api_url, api_key=api_key, model=model,
            timeout_s=timeout_s,
        )

        # Compose metadata
        metadata = upload_metadata_generator.build_metadata(
            dataset_doc=dataset,
            table_doc=table,
            column_profiles=column_profiles,
            semantic_results=semantic_results,
        )

        # 寫 v(N+1) draft
        version = self.repo.save_metadata_version(
            dataset_id=dataset_id,
            metadata=metadata,
            confirmation_status="draft",
            confirmed_by=None,
            notes=(
                f"Auto-generated by semantic_profiler "
                f"(LLM-assisted={use_llm})"
            ),
            activate=True,
        )
        logger.info(
            f"regenerate_metadata: dataset={dataset_id} version={version} "
            f"use_llm={use_llm}"
        )
        return version

    # ============================================================
    # 清理
    # ============================================================
    def delete_dataset(self, dataset_id: str) -> bool:
        """刪除 dataset 的 DB 記錄 + 本機檔案。"""
        # 先刪 DB(失敗也要嘗試 cleanup filesystem)
        db_ok = self.repo.delete_dataset(dataset_id)
        fs_dir = self.uploads_root / dataset_id
        if fs_dir.exists():
            self._cleanup_dataset_dir(fs_dir)
        return db_ok

    def _cleanup_dataset_dir(self, dataset_dir: Path) -> None:
        """rm -rf dataset_dir(silently)。"""
        try:
            if dataset_dir.exists():
                shutil.rmtree(dataset_dir)
        except Exception as e:
            logger.warning(f"清 dataset_dir 失敗: {e}")
