"""
prompt_repository.py — v0.3.0+

Prompt / Metadata 從 MongoDB 讀取的抽象層。

# 設計重點
- **DB → embedded fallback**:DB 連線失敗 / 文件不存在 / cache miss → 回退到 `embedded_prompts.py` 內嵌副本。
  這個 fallback 是「絕對救援」,即使整個 DB 掛掉,GenBI 行為跟 v0.2.x 一模一樣。
- **Cache TTL**:每個 (prompt_key, domain_scope) 在記憶體保留 60s (config.PROMPT_CACHE_TTL_S),
  避免每次 LLM call 都打 DB。Activate 新版本時呼叫 `invalidate_all()` 清光 cache。
- **Jinja2 template**:DB 中 template 字串以 Jinja2 語法,`{{var}}` 為變數插值、`{ }` 為純文字大括號。
  比 Python f-string 安全(不會撞 nesting 上限)、可寫條件 / 迴圈。
- **Domain scope `"*"`**:通用 prompt(例如 meta_response_intro)用 `domain_scope="*"`,
  domain-specific (e.g. tflex 的 phase_0_plan) 用 `domain_scope="tflex"`。
  讀取時優先 domain-specific,沒有再回退 `"*"`。

# 標準 Document Schema (prompt_templates collection)
```python
{
    "_id": ObjectId,
    "prompt_key": "phase_0_plan",     # 6 個固定 key
    "version": 3,
    "domain_scope": "tflex" | "*",
    "template": "...",                 # Jinja2 source
    "variables": ["query","domain_knowledge",...],
    "is_active": True,                 # 同 (key, scope) 只能一筆 active
    "created_at": ISODate,
    "created_by": "kuanhua.su",
    "notes": "說明這版改了什麼",
}
```

# 使用方式
```python
from prompt_repository import PromptRepository

repo = PromptRepository(mongo_db_or_none)

# 讀單一 prompt template (raw)
template_str = repo.get_template("phase_0_plan", domain="tflex")

# 直接 render(讀 + Jinja2 變數套用)
prompt_text = repo.render("phase_0_plan", domain="tflex",
                          query="...", domain_knowledge="...")
```
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

try:
    from jinja2 import Environment, StrictUndefined, Template
    _JINJA2_AVAILABLE = True
except ImportError:
    _JINJA2_AVAILABLE = False

logger = logging.getLogger(__name__)


# ============================================================
# 全域 6 個 prompt_key
# ============================================================
PROMPT_KEYS = {
    "phase_0_plan",
    "phase_a_pipeline",
    "phase_b_preprocess",
    "phase_c_echarts",
    "phase_d_insight",
    "meta_response",
}


# ============================================================
# Repository
# ============================================================
class PromptRepository:
    """Prompt / Metadata 統一存取層。"""

    def __init__(
        self,
        mongo_db: Any = None,
        cache_ttl_s: int = 60,
        prompt_collection: str = "prompt_templates",
        metadata_collection: str = "domain_metadata",
        embedded_fallback: Optional[dict] = None,
        enabled: bool = True,
    ):
        """
        Args:
            mongo_db: pymongo Database 物件。None → 完全走 embedded fallback。
            cache_ttl_s: 每筆 cache 保留秒數。
            prompt_collection: prompt_templates collection 名(可由 config 覆寫)。
            metadata_collection: domain_metadata collection 名。
            embedded_fallback: dict 形如
                {("phase_0_plan", "tflex"): "...template..."}
                提供緊急 fallback(DB 連不上 / 內容缺)。
            enabled: False → 強制走 embedded (用於 dev / 隔離測試)。
        """
        self._db = mongo_db
        self._cache_ttl_s = cache_ttl_s
        self._prompt_coll = prompt_collection
        self._metadata_coll = metadata_collection
        self._embedded = embedded_fallback or {}
        self._enabled = enabled
        # cache: (prompt_key, domain_scope) → (template_str, expires_at)
        self._template_cache: dict[tuple[str, str], tuple[str, float]] = {}
        # cache: domain → (metadata_dict, expires_at)
        self._metadata_cache: dict[str, tuple[dict, float]] = {}

        if not _JINJA2_AVAILABLE:
            logger.warning(
                "jinja2 未安裝,template render 將失敗。"
                "請執行 `pip install jinja2`。"
            )

        # Jinja2 environment:嚴格模式(未提供的變數會 raise,容易抓 bug)
        if _JINJA2_AVAILABLE:
            self._jinja_env = Environment(
                undefined=StrictUndefined,
                keep_trailing_newline=True,
                autoescape=False,  # prompt 是文字不是 HTML
            )
        else:
            self._jinja_env = None

    # ------------------------------------------------------------
    # Template 讀取 (DB → cache → embedded fallback)
    # ------------------------------------------------------------
    def get_template(self, prompt_key: str, domain: str = "*") -> str:
        """
        讀取 prompt template 原始字串。
        優先序:cache → DB (domain-specific) → DB (`*` 通用) → embedded fallback。

        Returns:
            Template 字串(尚未 Jinja2 render)。

        Raises:
            KeyError: 連 embedded 都沒這個 key,代表系統設計問題。
        """
        if prompt_key not in PROMPT_KEYS:
            raise ValueError(
                f"Unknown prompt_key: {prompt_key!r}. "
                f"Valid keys: {sorted(PROMPT_KEYS)}"
            )

        # Cache 命中 → 直接回
        cache_key = (prompt_key, domain)
        cached = self._template_cache.get(cache_key)
        if cached and cached[1] > time.time():
            return cached[0]

        # 嘗試 DB
        if self._enabled and self._db is not None:
            template = self._fetch_from_db(prompt_key, domain)
            if template is not None:
                self._template_cache[cache_key] = (
                    template,
                    time.time() + self._cache_ttl_s,
                )
                return template

        # Fallback to embedded
        embedded = self._embedded.get((prompt_key, domain)) \
            or self._embedded.get((prompt_key, "*"))
        if embedded is not None:
            # Embedded 也存 cache(短 TTL 讓使用者 enable DB 後快速感知)
            self._template_cache[cache_key] = (
                embedded,
                time.time() + min(self._cache_ttl_s, 30),
            )
            return embedded

        raise KeyError(
            f"Prompt template not found: key={prompt_key!r} domain={domain!r}. "
            f"DB enabled={self._enabled}, db={'set' if self._db else 'None'}, "
            f"embedded keys={list(self._embedded.keys())}"
        )

    def _fetch_from_db(self, prompt_key: str, domain: str) -> Optional[str]:
        """從 MongoDB 撈當前 active 的 template。"""
        try:
            coll = self._db[self._prompt_coll]
            # 先嘗試 domain-specific
            doc = coll.find_one({
                "prompt_key": prompt_key,
                "domain_scope": domain,
                "is_active": True,
            })
            # 沒有再 fallback to 通用 "*"
            if not doc and domain != "*":
                doc = coll.find_one({
                    "prompt_key": prompt_key,
                    "domain_scope": "*",
                    "is_active": True,
                })
            if doc and "template" in doc:
                return doc["template"]
            return None
        except Exception as e:
            logger.warning(
                f"PromptRepository DB read failed for "
                f"({prompt_key}, {domain}): {e}. Will fall back to embedded."
            )
            return None

    # ------------------------------------------------------------
    # Render(讀 + Jinja2 變數替換)
    # ------------------------------------------------------------
    def render(self, prompt_key: str, domain: str = "*", **variables: Any) -> str:
        """
        讀 template + Jinja2 render。

        Args:
            prompt_key: 6 個固定 key 之一。
            domain: tflex / ecommerce / healthcare / "*"。
            **variables: 模板中 `{{varname}}` 對應的值。

        Returns:
            填入變數後的 prompt 字串。
        """
        template_str = self.get_template(prompt_key, domain)
        if not _JINJA2_AVAILABLE or self._jinja_env is None:
            raise RuntimeError(
                "Cannot render template: jinja2 not installed. "
                "Run `pip install jinja2`."
            )
        try:
            template: Template = self._jinja_env.from_string(template_str)
            return template.render(**variables)
        except Exception as e:
            logger.error(
                f"Jinja2 render failed for {prompt_key}/{domain}: {e}. "
                f"Variables provided: {list(variables.keys())}"
            )
            raise

    # ------------------------------------------------------------
    # Admin / 寫入路徑
    # ------------------------------------------------------------
    def save_new_version(
        self,
        prompt_key: str,
        domain: str,
        template: str,
        notes: str = "",
        created_by: str = "system",
        activate: bool = False,
    ) -> Any:
        """
        新增一筆 version,可選擇直接啟用。

        Returns:
            ObjectId of inserted doc。
        """
        if prompt_key not in PROMPT_KEYS:
            raise ValueError(f"Unknown prompt_key: {prompt_key!r}")
        if self._db is None:
            raise RuntimeError("Cannot save: mongo_db not provided.")

        coll = self._db[self._prompt_coll]
        # 算下一個 version
        latest = coll.find_one(
            {"prompt_key": prompt_key, "domain_scope": domain},
            sort=[("version", -1)],
        )
        next_version = (latest["version"] + 1) if latest else 1

        import datetime as _dt
        doc = {
            "prompt_key": prompt_key,
            "version": next_version,
            "domain_scope": domain,
            "template": template,
            "is_active": False,  # 預設不啟用,要明確 activate
            "created_at": _dt.datetime.utcnow(),
            "created_by": created_by,
            "notes": notes,
        }
        result = coll.insert_one(doc)
        if activate:
            self.activate(result.inserted_id)
        return result.inserted_id

    def activate(self, doc_id: Any) -> None:
        """啟用某版本,同 (prompt_key, domain_scope) 其他自動下線。"""
        if self._db is None:
            raise RuntimeError("Cannot activate: mongo_db not provided.")
        coll = self._db[self._prompt_coll]
        target = coll.find_one({"_id": doc_id})
        if not target:
            raise KeyError(f"No prompt doc with _id={doc_id}")
        # 同 (key, scope) 全部設 is_active=False
        coll.update_many(
            {"prompt_key": target["prompt_key"],
             "domain_scope": target["domain_scope"]},
            {"$set": {"is_active": False}},
        )
        # 目標啟用
        coll.update_one({"_id": doc_id}, {"$set": {"is_active": True}})
        # 清 cache,讓下次讀立即拿新版
        self.invalidate_all()

    def list_versions(self, prompt_key: str, domain: str = "*") -> list[dict]:
        """列出 (prompt_key, domain) 所有版本,按 version desc。"""
        if self._db is None:
            return []
        coll = self._db[self._prompt_coll]
        return list(coll.find(
            {"prompt_key": prompt_key, "domain_scope": domain},
        ).sort("version", -1))

    def invalidate_all(self) -> None:
        """清光所有 in-memory cache。Activate 後呼叫,確保下次讀拿新版。"""
        self._template_cache.clear()
        self._metadata_cache.clear()

    # ============================================================
    # Metadata 路徑 (domain_metadata collection)
    # ============================================================
    # 跟 prompt 一樣有 DB → cache → embedded fallback 三層保險。
    # 但 metadata 不是模板,沒有 Jinja2 render,直接回 dict。

    def get_metadata(self, domain: str) -> dict:
        """讀某 domain 的 active metadata。優先 DB → cache → embedded fallback。

        Returns:
            metadata dict(含 schema / kpi_definitions / data_limitations / ...)

        Raises:
            KeyError: DB 與 embedded 都沒這個 domain。
        """
        # Cache 命中
        cached = self._metadata_cache.get(domain)
        if cached and cached[1] > time.time():
            return cached[0]

        # DB
        if self._enabled and self._db is not None:
            doc = self._fetch_metadata_from_db(domain)
            if doc is not None:
                self._metadata_cache[domain] = (
                    doc,
                    time.time() + self._cache_ttl_s,
                )
                return doc

        # Embedded fallback
        embedded = self._embedded.get(("__metadata__", domain))
        if embedded is not None:
            self._metadata_cache[domain] = (
                embedded,
                time.time() + min(self._cache_ttl_s, 30),
            )
            return embedded

        raise KeyError(
            f"Metadata not found for domain={domain!r}. "
            f"DB enabled={self._enabled}, embedded keys="
            f"{[k[1] for k in self._embedded.keys() if k[0] == '__metadata__']}"
        )

    def _fetch_metadata_from_db(self, domain: str) -> Optional[dict]:
        """從 MongoDB 撈該 domain 的當前 active metadata。"""
        try:
            coll = self._db[self._metadata_coll]
            doc = coll.find_one({"domain": domain, "is_active": True})
            if not doc:
                return None
            # 去掉 MongoDB 內部欄位,組回乾淨 metadata dict
            cleaned = {
                k: v for k, v in doc.items()
                if k not in ("_id", "domain", "version", "is_active",
                             "created_at", "created_by", "notes")
            }
            return cleaned
        except Exception as e:
            logger.warning(
                f"PromptRepository metadata DB read failed for {domain}: {e}. "
                f"Will fall back to embedded."
            )
            return None

    def list_active_domains(self) -> list[str]:
        """列出所有有 active metadata 的 domain 名稱。給 sidebar selector 用。

        Returns:
            sorted list of domain names。DB 沒接時回 embedded 副本的 domain list。
        """
        domains = set()

        # DB 來源
        if self._enabled and self._db is not None:
            try:
                coll = self._db[self._metadata_coll]
                for doc in coll.find({"is_active": True}, {"domain": 1}):
                    if "domain" in doc:
                        domains.add(doc["domain"])
            except Exception as e:
                logger.warning(f"list_active_domains DB read failed: {e}")

        # Embedded 也加上(確保 fallback 場景也能列)
        for key in self._embedded.keys():
            if key[0] == "__metadata__":
                domains.add(key[1])

        return sorted(domains)

    def save_new_metadata_version(
        self,
        domain: str,
        metadata: dict,
        notes: str = "",
        created_by: str = "system",
        activate: bool = False,
    ) -> Any:
        """新增一筆 domain metadata 版本。"""
        if self._db is None:
            raise RuntimeError("Cannot save: mongo_db not provided.")
        coll = self._db[self._metadata_coll]
        latest = coll.find_one(
            {"domain": domain},
            sort=[("version", -1)],
        )
        next_version = (latest["version"] + 1) if latest else 1

        import datetime as _dt
        doc = {
            "domain": domain,
            "version": next_version,
            "is_active": False,
            "created_at": _dt.datetime.utcnow(),
            "created_by": created_by,
            "notes": notes,
            **metadata,  # schema / kpi_definitions / ... 直接展開
        }
        result = coll.insert_one(doc)
        if activate:
            self.activate_metadata(result.inserted_id)
        return result.inserted_id

    def activate_metadata(self, doc_id: Any) -> None:
        """啟用某 domain metadata 版本,同 domain 其他自動下線。"""
        if self._db is None:
            raise RuntimeError("Cannot activate: mongo_db not provided.")
        coll = self._db[self._metadata_coll]
        target = coll.find_one({"_id": doc_id})
        if not target:
            raise KeyError(f"No metadata doc with _id={doc_id}")
        coll.update_many(
            {"domain": target["domain"]},
            {"$set": {"is_active": False}},
        )
        coll.update_one({"_id": doc_id}, {"$set": {"is_active": True}})
        self.invalidate_all()

    def list_metadata_versions(self, domain: str) -> list[dict]:
        """列出某 domain 所有 metadata 版本,按 version desc。"""
        if self._db is None:
            return []
        coll = self._db[self._metadata_coll]
        return list(coll.find({"domain": domain}).sort("version", -1))


# ============================================================
# 工廠函式 — 從 config 建構 repo
# ============================================================
def build_default_repo(mongo_db: Any = None) -> PromptRepository:
    """從 config 載入設定建一個 repo。

    Args:
        mongo_db: 已連線的 MongoDB Database 物件,或 None(走純 embedded)。
    """
    try:
        import config
        from embedded_prompts import EMBEDDED_PROMPTS
        return PromptRepository(
            mongo_db=mongo_db,
            cache_ttl_s=config.PROMPT_CACHE_TTL_S,
            prompt_collection=config.PROMPT_COLLECTION,
            metadata_collection=config.METADATA_COLLECTION,
            embedded_fallback=EMBEDDED_PROMPTS,
            enabled=config.PROMPT_REPO_ENABLED,
        )
    except ImportError as e:
        logger.warning(
            f"build_default_repo: config / embedded_prompts 載入失敗 ({e}),"
            f"建立純 embedded 模式 repo。"
        )
        try:
            from embedded_prompts import EMBEDDED_PROMPTS
        except ImportError:
            EMBEDDED_PROMPTS = {}
        return PromptRepository(
            mongo_db=None,
            embedded_fallback=EMBEDDED_PROMPTS,
            enabled=False,
        )
