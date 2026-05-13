"""
test_run_repository.py — v0.3.0+

把 test_runner.py 跑出來的結果寫進 MongoDB,提供:
- 歷史回溯(看每次 prompt / metadata 改動後的 pass rate / token 用量趨勢)
- Baseline 對比(指定某一筆當基準,後續跑分跟它比)
- Run snapshot(每筆紀錄當下生效的 prompt / metadata 版本,可重現)

# Document Schema (test_runs collection)
```python
{
    "_id": ObjectId,
    "run_id": "20260513_143020",       # 人讀 timestamp,可手動標
    "started_at": ISODate,
    "completed_at": ISODate,
    "total_wall_s": 1080.5,
    "git_commit": "e5f726d",           # current HEAD short SHA
    "active_versions": {                # 快照 — 重現用
        "prompts": {
            "phase_0_plan": ObjectId,
            ...
        },
        "metadata": ObjectId,
    },
    "filter": null | "STK" | "STK-01,STK-04",
    "summary": {
        "total_cases": 26,
        "passed": 22, "refusal_detected": 4, "failed": 4,
        "total_calls": 145, "total_tokens": 489201,
        "prompt_tokens": 423120, "completion_tokens": 66081,
    },
    "is_baseline": False,
    "baseline_notes": "",
    "case_results": [                   # 跟 test_results.json 同 schema
        {"id": "STK-01", "name": "...", "status": "pass", ...},
        ...
    ],
}
```

# 使用方式 (test_runner.py 整合)
```python
from test_run_repository import TestRunRepository

repo = TestRunRepository(mongo_db)
inserted_id = repo.save_run({
    "started_at": start_dt,
    "completed_at": end_dt,
    "summary": {...},
    "case_results": [...],
    ...
})
```
"""

from __future__ import annotations

import datetime as _dt
import logging
import subprocess
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _utcnow() -> _dt.datetime:
    """Timezone-aware UTC now(避免 utcnow deprecation warning,Python 3.12+ 友好)。"""
    return _dt.datetime.now(_dt.timezone.utc)


class TestRunRepository:
    """test_runs collection 的存取層。"""

    def __init__(self, mongo_db: Any = None, collection: str = "test_runs"):
        """
        Args:
            mongo_db: pymongo Database。None → save 會 raise(不能寫無 DB)。
            collection: collection 名(可由 config 覆寫)。
        """
        self._db = mongo_db
        self._coll_name = collection

    @property
    def _coll(self):
        if self._db is None:
            raise RuntimeError(
                "TestRunRepository: mongo_db not provided. "
                "Cannot persist test runs without DB."
            )
        return self._db[self._coll_name]

    # ------------------------------------------------------------
    # 寫入
    # ------------------------------------------------------------
    def save_run(
        self,
        run_data: dict,
        active_versions: Optional[dict] = None,
        git_commit: Optional[str] = None,
    ) -> Any:
        """寫入一筆 test run。

        Args:
            run_data: 至少要含 started_at / completed_at / summary / case_results 等。
                會被合併寫入(callers 可以多丟欄位)。
            active_versions: { "prompts": {...ObjectId...}, "metadata": ObjectId } 快照。
                v0.3.0 階段 prompt repo 還沒接,可先傳 None。
            git_commit: 略過則自動 `git rev-parse --short HEAD`。

        Returns:
            insert_one result.inserted_id (ObjectId)
        """
        doc = dict(run_data)  # shallow copy 避免動到 caller
        doc.setdefault("started_at", _utcnow())
        doc.setdefault("completed_at", _utcnow())
        doc.setdefault("is_baseline", False)
        doc.setdefault("baseline_notes", "")

        # run_id — 人讀 timestamp
        if "run_id" not in doc:
            ts = doc["started_at"] if isinstance(doc["started_at"], _dt.datetime) \
                else _utcnow()
            doc["run_id"] = ts.strftime("%Y%m%d_%H%M%S")

        # active_versions 快照
        if active_versions is not None:
            doc["active_versions"] = active_versions

        # git commit
        if git_commit is None:
            git_commit = self._detect_git_commit()
        if git_commit:
            doc["git_commit"] = git_commit

        result = self._coll.insert_one(doc)
        logger.info(
            f"TestRunRepository: saved run {doc['run_id']} "
            f"(id={result.inserted_id})"
        )
        return result.inserted_id

    @staticmethod
    def _detect_git_commit() -> Optional[str]:
        """自動偵測當前 git HEAD short SHA,沒 git 就 None。"""
        try:
            out = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if out.returncode == 0:
                return out.stdout.strip()
        except Exception:
            pass
        return None

    # ------------------------------------------------------------
    # 查詢
    # ------------------------------------------------------------
    def list_recent(self, limit: int = 20, filter_only: Optional[str] = None) -> list[dict]:
        """最近 N 次跑(預設 20)。可選 filter 條件(例如 'STK' 只看 STK 系列)。"""
        query = {}
        if filter_only:
            query["filter"] = filter_only
        return list(
            self._coll.find(query).sort("started_at", -1).limit(limit)
        )

    def get_by_run_id(self, run_id: str) -> Optional[dict]:
        return self._coll.find_one({"run_id": run_id})

    def get_baseline(self, domain: Optional[str] = None) -> Optional[dict]:
        """目前 active baseline(per domain)。

        Args:
            domain: 若提供,只回該 domain 的 baseline。
                    None → 回任何 domain 的最新 baseline(向下相容)。

        為什麼要 per-domain:
            不同 domain(tflex / ecommerce / healthcare)的 cases / metadata / KPI
            計算邏輯都不同,跨 domain 比 pass rate / token 是沒意義的。
            每個 domain 應該有自己的 baseline。
        """
        query = {"is_baseline": True}
        if domain:
            query["domain"] = domain
        return self._coll.find_one(query, sort=[("started_at", -1)])

    def get_latest(self, domain: Optional[str] = None) -> Optional[dict]:
        """最後一次跑(可能不是 baseline)。可選 domain 過濾。"""
        query = {}
        if domain:
            query["domain"] = domain
        return self._coll.find_one(query, sort=[("started_at", -1)])

    def mark_as_baseline(self, run_id: str, notes: str = "") -> bool:
        """標某筆為 baseline。同時間可存在多個 baseline(代表不同階段的對照點)。

        Returns:
            True if matched and updated。
        """
        result = self._coll.update_one(
            {"run_id": run_id},
            {"$set": {"is_baseline": True, "baseline_notes": notes}},
        )
        return result.matched_count > 0

    def unmark_baseline(self, run_id: str) -> bool:
        result = self._coll.update_one(
            {"run_id": run_id},
            {"$set": {"is_baseline": False, "baseline_notes": ""}},
        )
        return result.matched_count > 0

    # ------------------------------------------------------------
    # 對比
    # ------------------------------------------------------------
    def compare(self, run_id_a: str, run_id_b: str) -> dict:
        """兩筆 run 摘要級 diff。

        Returns:
            dict:
              {
                "a": {...summary 摘要...},
                "b": {...},
                "delta": {
                  "passed": +3,
                  "failed": -3,
                  "total_tokens": -12000,
                  ...
                },
                "case_changes": [
                  {"id": "STK-04", "a_status": "phaseA_error", "b_status": "pass"},
                  ...
                ],
              }
        """
        a = self.get_by_run_id(run_id_a)
        b = self.get_by_run_id(run_id_b)
        if not a or not b:
            raise KeyError(
                f"Cannot compare: run_id_a={'found' if a else 'MISSING'}, "
                f"run_id_b={'found' if b else 'MISSING'}"
            )

        sa = a.get("summary", {})
        sb = b.get("summary", {})
        delta = {}
        for k in set(sa.keys()) | set(sb.keys()):
            va, vb = sa.get(k, 0), sb.get(k, 0)
            if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
                delta[k] = vb - va

        # case 結果對照
        a_cases = {c.get("id"): c.get("status") for c in a.get("case_results", [])}
        b_cases = {c.get("id"): c.get("status") for c in b.get("case_results", [])}
        all_ids = set(a_cases.keys()) | set(b_cases.keys())
        case_changes = []
        for cid in sorted(all_ids):
            sa_status = a_cases.get(cid, "MISSING")
            sb_status = b_cases.get(cid, "MISSING")
            if sa_status != sb_status:
                case_changes.append({
                    "id": cid,
                    "a_status": sa_status,
                    "b_status": sb_status,
                })

        return {
            "a": {"run_id": a["run_id"], **sa},
            "b": {"run_id": b["run_id"], **sb},
            "delta": delta,
            "case_changes": case_changes,
        }

    def compare_with_baseline(self, run_id: str) -> Optional[dict]:
        """指定 run vs 該 run 對應 domain 的 baseline。

        自動讀 run.domain 然後找該 domain 的 baseline。
        若 run 沒記錄 domain(舊資料),fallback 拿任何 baseline。
        """
        target = self.get_by_run_id(run_id)
        if not target:
            return None
        baseline = self.get_baseline(domain=target.get("domain"))
        if not baseline:
            # Fallback:沒 domain 過濾的 baseline
            baseline = self.get_baseline()
        if not baseline:
            return None
        return self.compare(baseline["run_id"], run_id)
