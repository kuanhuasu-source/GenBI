"""
metadata_provider.py — v0.12.0+

Metadata lookup 抽象層 — 讓既有 schema-driven domain 與新 upload-driven dataset
共用同一介面,LLMService caller 不必知道 metadata 從哪來。

# 為什麼需要這層

v0.11.x 之前,app.py 直接呼叫 `prompt_repo.get_metadata(domain)` 拿 dict 傳給
LLMService。這對 schema-driven path 沒問題,但 v0.12 新增 Upload Workspace 後,
metadata 可能來自:
  - `domain_metadata` collection(既有,人工維護)
  - `upload_metadata_versions` collection(新增,動態產生 + 使用者確認)

引入 MetadataProvider 抽象,讓兩條路徑 share lookup interface,呼叫端只需:
    md = provider.get_metadata(dataset_id)
    llm = LLMService(..., task_metadata=md, ...)

# 凍結條款(critical)

`StaticDomainMetadataProvider.get_metadata(dataset_id)` **必須** byte-equal 透傳
`prompt_repo.get_metadata(dataset_id)` 的回傳 dict。任何 wrapping / mutating /
key 注入都會破壞既有 schema-driven baseline。**只有** `UploadMetadataProvider`
會在 dict 內注入 `source_type="upload"` flag(用於 LLMService.generate_plan 切
Phase A prompt key)。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class MetadataProvider(ABC):
    """Metadata lookup 抽象介面。

    兩個實作:
      - StaticDomainMetadataProvider:包裝 prompt_repo,服務既有 domain
      - UploadMetadataProvider:包裝 upload_repository,服務上傳 dataset (v0.12+)
    """

    @abstractmethod
    def get_metadata(self, dataset_id: str) -> dict[str, Any]:
        """回傳 LLMService 認得的 metadata dict。

        若 dataset_id 不存在,raise KeyError(由 caller 處理 fallback)。
        """
        ...

    @abstractmethod
    def list_available(self) -> list[str]:
        """回傳此 provider 可服務的 dataset_id list(domain 名 / upload dataset_id)。"""
        ...

    @abstractmethod
    def get_source_type(self) -> str:
        """回傳 'static' | 'upload',給 caller 做 UI / routing 判斷。"""
        ...


class StaticDomainMetadataProvider(MetadataProvider):
    """既有 schema-driven 路徑 — 純粹透傳 prompt_repo,行為 100% transparent。

    凍結條款:get_metadata 必須回傳跟 `prompt_repo.get_metadata(...)` 完全相同的 dict
    (不可加 key、不可改 key、不可改 value)。違反會炸 schema-driven baseline。
    """

    def __init__(self, prompt_repo):
        """
        Args:
            prompt_repo: PromptRepository instance(必須有 get_metadata 與 list_active_domains)
        """
        self.repo = prompt_repo

    def get_metadata(self, dataset_id: str) -> dict[str, Any]:
        # 透傳,不 mutate。prompt_repo 內部 cache + DB → embedded fallback 都會生效。
        return self.repo.get_metadata(dataset_id)

    def list_available(self) -> list[str]:
        return self.repo.list_active_domains()

    def get_source_type(self) -> str:
        return "static"
