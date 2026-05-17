"""
scripts/backfill_task_traces_datetime.py — v0.11.0.1 hotfix backfill

把既有 task_traces 中被 v0.11.0(P1)之前 json round-trip 序列化成
字串的 started_at / completed_at 欄位轉回 datetime 物件。

需求背景:
  v0.7.0 ~ v0.11.0 P1 的 task_trace._safe_doc 用 json.dumps(default=str),
  datetime 被吃成 ISO 字串。
  v0.11.0.1 改用 recursive sanitizer 保留 datetime,但既有資料還是 str → 跑這個
  backfill 一次性修。

用法:
    python scripts/backfill_task_traces_datetime.py            # dry-run
    python scripts/backfill_task_traces_datetime.py --apply    # 真正改
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

import config


def _parse_iso(s):
    """容忍多種 ISO 變體(Python str(datetime) 跟 isoformat 略有差)。"""
    if not isinstance(s, str):
        return s
    # Python 預設 str(datetime) 用空格,fromisoformat 接受
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        pass
    # 嘗試把 "Z" suffix 改 "+00:00"
    if s.endswith("Z"):
        try:
            return datetime.fromisoformat(s[:-1] + "+00:00")
        except ValueError:
            pass
    return None  # 解析失敗


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true",
                   help="真的執行 update(預設 dry-run)")
    p.add_argument("--collection", default=None,
                   help="task_traces collection name(預設讀 config)")
    args = p.parse_args()

    from pymongo import MongoClient
    db = MongoClient(config.MONGO_URI)[config.MONGO_DB]
    coll = db[args.collection or getattr(config, "TASK_TRACES_COLLECTION", "task_traces")]

    print(f"DB: {config.MONGO_DB}, collection: {coll.name}")
    print(f"Mode: {'APPLY' if args.apply else 'DRY-RUN(不會改)'}\n")

    fixed = 0
    skipped = 0
    failed = 0
    for doc in coll.find({}):
        updates = {}
        for fld in ("started_at", "completed_at"):
            v = doc.get(fld)
            if isinstance(v, str):
                parsed = _parse_iso(v)
                if isinstance(parsed, datetime):
                    updates[fld] = parsed
                else:
                    print(f"  ⚠️ {doc.get('trace_id', '?')[:8]} {fld!r}={v!r} 解析失敗")
                    failed += 1
        if not updates:
            skipped += 1
            continue
        if args.apply:
            coll.update_one({"_id": doc["_id"]}, {"$set": updates})
        fixed += 1
        if fixed <= 5:  # 印前 5 個 sample
            print(f"  {doc.get('trace_id', '?')[:8]}: {list(updates.keys())} → datetime")

    print(f"\n結果:")
    print(f"  fixed   : {fixed}{' (已寫回)' if args.apply else ' (would fix)'}")
    print(f"  skipped : {skipped}(已經是 datetime / 沒這 2 欄)")
    print(f"  failed  : {failed}(解析失敗)")

    if not args.apply and fixed > 0:
        print(f"\n💡 加 --apply 真的執行 update")


if __name__ == "__main__":
    main()
