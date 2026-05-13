"""
test_case_repository.py — v0.3.0+

Test case CRUD layer。Schema 設計見 D8 plan / CHANGELOG。

# Storage
- MongoDB collection: `test_cases`
- Compound unique index: (domain, case_id)
- Fallback: `embedded_test_cases.EMBEDDED_TEST_CASES`(嵌入式副本)

# Schema (per document)
```python
{
    "_id": ObjectId,
    "domain": "tflex" | "ecommerce" | "healthcare",
    "case_id": "STK-01",                # 在 domain 內 unique
    "name": "...",
    "query": "...",                      # LLM 收到的查詢字串
    "type": "happy_path" | "refusal",
    "expected_chart": "...",             # 人讀標籤
    "expected_q_cols_any": [...],
    "expected_q_cols_all": [...],
    "echarts_required_keys": [...],
    "echarts_min_series": int,
    "echarts_should_have_stack": bool,
    "echarts_xaxis_unique": bool,
    "echarts_data_length_aligned": bool,
    "echarts_yaxis_max": int,
    "echarts_no_placeholder_series_name": bool,
    "echarts_no_nan_in_data": bool,
    "echarts_should_have_yaxis_category": bool,
    "echarts_should_have_xaxis_value": bool,
    "echarts_should_have_visualmap": bool,
    "echarts_data_length_aligned_horizontal": bool,
    "echarts_should_have_kpi_cards": bool,
    "echarts_min_kpi_cards": int,
    "refusal_keywords": [...],
    "follow_up_setup_query": str | None,
    "is_active": True,
    "tags": [...],
    "created_at": ISODate,
    "updated_at": ISODate,
    "created_by": "kuanhua.su",
    "updated_by": "kuanhua.su",
    "notes": "..."
}
```

# 使用方式
```python
from test_case_repository import TestCaseRepository

repo = TestCaseRepository(mongo_db, embedded_fallback=EMBEDDED_TEST_CASES)

# 讀某 domain 的 active cases
cases = repo.get_cases(domain="tflex")

# 篩選
stk_cases = repo.get_cases(domain="tflex", filter_prefix="STK")
only_cases = repo.get_cases(domain="tflex", case_ids=["STK-01", "STK-04"])
```
"""

from __future__ import annotations

import datetime as _dt
import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)


# 已知的 echarts_* check key — 給 UI 列 + repo 驗證用
ECHARTS_CHECK_KEYS = (
    "echarts_required_keys",
    "echarts_min_series",
    "echarts_series_count_max",
    "echarts_should_have_stack",
    "echarts_xaxis_unique",
    "echarts_data_length_aligned",
    "echarts_data_length_aligned_horizontal",
    "echarts_yaxis_max",
    "echarts_no_placeholder_series_name",
    "echarts_no_nan_in_data",
    "echarts_should_have_yaxis_category",
    "echarts_should_have_xaxis_value",
    "echarts_should_have_visualmap",
    "echarts_should_have_kpi_cards",
    "echarts_min_kpi_cards",
    "echarts_should_use_table",
)


class TestCaseRepository:
    """test_cases collection 的存取層。"""

    def __init__(
        self,
        mongo_db: Any = None,
        cache_ttl_s: int = 60,
        collection: str = "test_cases",
        embedded_fallback: Optional[dict[str, list[dict]]] = None,
        enabled: bool = True,
    ):
        """
        Args:
            mongo_db: pymongo Database。None → 完全走 embedded fallback。
            cache_ttl_s: list-by-domain cache TTL(秒)。
            collection: collection 名(可由 config 覆寫)。
            embedded_fallback: { "tflex": [case_dict, ...], "ecommerce": [...], ... }
            enabled: False → 強制走 embedded。
        """
        self._db = mongo_db
        self._cache_ttl_s = cache_ttl_s
        self._coll_name = collection
        self._embedded = embedded_fallback or {}
        self._enabled = enabled
        # cache: domain → (list[case_dict], expires_at)
        self._cases_cache: dict[str, tuple[list[dict], float]] = {}

    # ============================================================
    # 讀取
    # ============================================================
    def get_cases(
        self,
        domain: str,
        filter_prefix: str = "",
        case_ids: Optional[list[str]] = None,
        include_inactive: bool = False,
    ) -> list[dict]:
        """讀某 domain 的 cases。

        Args:
            domain: 必填。
            filter_prefix: 只回傳 case_id 以此開頭的(例 'STK')。空字串=不過濾。
            case_ids: 只回傳指定 case_id list(優先級高於 filter_prefix)。
            include_inactive: True 連 is_active=False 的也回。

        Returns:
            list of case dicts(按 case_id sort)。
        """
        # 從 cache / DB / embedded 取所有
        all_cases = self._fetch_domain_cases(domain)

        out = []
        for c in all_cases:
            if not include_inactive and not c.get("is_active", True):
                continue
            cid = c.get("case_id", "")
            if case_ids and cid not in case_ids:
                continue
            if filter_prefix and not cid.startswith(filter_prefix):
                continue
            out.append(c)
        # Sort by case_id (穩定)
        return sorted(out, key=lambda x: x.get("case_id", ""))

    def get_case(self, domain: str, case_id: str) -> Optional[dict]:
        """讀單一 case。"""
        for c in self._fetch_domain_cases(domain):
            if c.get("case_id") == case_id:
                return c
        return None

    def _fetch_domain_cases(self, domain: str) -> list[dict]:
        """DB → cache → embedded 三層 fallback。"""
        cached = self._cases_cache.get(domain)
        if cached and cached[1] > time.time():
            return cached[0]

        cases: list[dict] = []
        if self._enabled and self._db is not None:
            try:
                coll = self._db[self._coll_name]
                cases = list(coll.find({"domain": domain}))
            except Exception as e:
                logger.warning(
                    f"TestCaseRepository DB read failed for {domain}: {e}. "
                    f"Will fall back to embedded."
                )
                cases = []

        if not cases:
            # Fallback to embedded
            cases = [dict(c) for c in self._embedded.get(domain, [])]
            # Ensure 'is_active' defaults to True for embedded
            for c in cases:
                c.setdefault("is_active", True)
                c.setdefault("domain", domain)

        # 短 cache(activate 後手動 invalidate)
        self._cases_cache[domain] = (cases, time.time() + self._cache_ttl_s)
        return cases

    def list_domains_with_cases(self) -> list[str]:
        """列出所有有 cases 的 domain。"""
        domains = set()
        if self._enabled and self._db is not None:
            try:
                coll = self._db[self._coll_name]
                for d in coll.distinct("domain"):
                    domains.add(d)
            except Exception as e:
                logger.warning(f"list_domains_with_cases DB failed: {e}")
        for d in self._embedded.keys():
            domains.add(d)
        return sorted(domains)

    def count(self, domain: str, include_inactive: bool = False) -> int:
        cases = self._fetch_domain_cases(domain)
        if include_inactive:
            return len(cases)
        return sum(1 for c in cases if c.get("is_active", True))

    # ============================================================
    # 寫入
    # ============================================================
    def upsert_case(
        self,
        domain: str,
        case_id: str,
        case_data: dict,
        user: str = "system",
    ) -> Any:
        """新增或更新一個 case。以 (domain, case_id) 為 key。

        Returns:
            ObjectId of the doc(insert or existing)。
        """
        if self._db is None:
            raise RuntimeError("Cannot upsert: mongo_db not provided.")
        coll = self._db[self._coll_name]
        now = _dt.datetime.now(_dt.timezone.utc)

        # 不允許 caller 蓋掉 system 欄位
        for key in ("_id", "domain", "case_id", "created_at", "created_by"):
            case_data.pop(key, None)

        # 確保 case_id 存在於 doc
        doc = dict(case_data)
        doc["domain"] = domain
        doc["case_id"] = case_id
        doc["updated_at"] = now
        doc["updated_by"] = user
        doc.setdefault("is_active", True)

        existing = coll.find_one({"domain": domain, "case_id": case_id})
        if existing:
            coll.update_one(
                {"_id": existing["_id"]},
                {"$set": doc},
            )
            self.invalidate(domain)
            return existing["_id"]
        else:
            doc["created_at"] = now
            doc["created_by"] = user
            result = coll.insert_one(doc)
            self.invalidate(domain)
            return result.inserted_id

    def deactivate_case(self, domain: str, case_id: str, user: str = "system") -> bool:
        """停用某 case(保留歷史,不真刪)。"""
        if self._db is None:
            return False
        result = self._db[self._coll_name].update_one(
            {"domain": domain, "case_id": case_id},
            {"$set": {
                "is_active": False,
                "updated_at": _dt.datetime.now(_dt.timezone.utc),
                "updated_by": user,
            }},
        )
        if result.matched_count:
            self.invalidate(domain)
            return True
        return False

    def activate_case(self, domain: str, case_id: str, user: str = "system") -> bool:
        if self._db is None:
            return False
        result = self._db[self._coll_name].update_one(
            {"domain": domain, "case_id": case_id},
            {"$set": {
                "is_active": True,
                "updated_at": _dt.datetime.now(_dt.timezone.utc),
                "updated_by": user,
            }},
        )
        if result.matched_count:
            self.invalidate(domain)
            return True
        return False

    def delete_case(self, domain: str, case_id: str) -> bool:
        """真刪除一筆(不建議用,deactivate 較安全)。"""
        if self._db is None:
            return False
        result = self._db[self._coll_name].delete_one(
            {"domain": domain, "case_id": case_id}
        )
        if result.deleted_count:
            self.invalidate(domain)
            return True
        return False

    # ============================================================
    # Indexes(初次連線時呼叫,idempotent)
    # ============================================================
    def ensure_indexes(self) -> None:
        if self._db is None:
            return
        coll = self._db[self._coll_name]
        try:
            coll.create_index(
                [("domain", 1), ("case_id", 1)],
                unique=True,
                name="domain_case_id_unique",
            )
            coll.create_index(
                [("domain", 1), ("is_active", 1)],
                name="domain_is_active",
            )
            coll.create_index(
                [("domain", 1), ("tags", 1)],
                name="domain_tags",
            )
        except Exception as e:
            logger.warning(f"ensure_indexes failed: {e}")

    def invalidate(self, domain: Optional[str] = None) -> None:
        if domain is None:
            self._cases_cache.clear()
        else:
            self._cases_cache.pop(domain, None)


# ============================================================
# 工廠函式
# ============================================================
def build_default_test_case_repo(mongo_db: Any = None) -> TestCaseRepository:
    """從 config 建一個 TestCaseRepository。"""
    try:
        import config
        from embedded_test_cases import EMBEDDED_TEST_CASES
        return TestCaseRepository(
            mongo_db=mongo_db,
            cache_ttl_s=config.PROMPT_CACHE_TTL_S,
            collection=config.TEST_CASES_COLLECTION,
            embedded_fallback=EMBEDDED_TEST_CASES,
            enabled=config.PROMPT_REPO_ENABLED,
        )
    except ImportError as e:
        logger.warning(
            f"build_default_test_case_repo failed import ({e}),純 embedded 模式"
        )
        try:
            from embedded_test_cases import EMBEDDED_TEST_CASES
        except ImportError:
            EMBEDDED_TEST_CASES = {}
        return TestCaseRepository(
            mongo_db=None,
            embedded_fallback=EMBEDDED_TEST_CASES,
            enabled=False,
        )
