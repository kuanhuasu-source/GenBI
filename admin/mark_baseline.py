"""
admin/mark_baseline.py — v0.3.0+

把某個 test run 標為 baseline(或取消)。

# 使用方式
```bash
# 標 baseline
python admin/mark_baseline.py 20260513_143020 "v0.3.0 launch baseline"

# 取消 baseline
python admin/mark_baseline.py 20260513_143020 --unmark

# 自動把最後一筆 run 設為 baseline (常用)
python admin/mark_baseline.py --latest "post-prompt-tweak baseline"
```
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

import config


def main():
    parser = argparse.ArgumentParser(description="Mark / unmark a test run as baseline")
    parser.add_argument("run_id", nargs="?", default="",
                        help="run_id 字串(若使用 --latest 可省略)")
    parser.add_argument("notes", nargs="?", default="",
                        help="baseline notes(描述為什麼這版要當 baseline)")
    parser.add_argument("--latest", action="store_true",
                        help="自動標最後一筆為 baseline")
    parser.add_argument("--unmark", action="store_true",
                        help="取消 baseline")
    parser.add_argument("--domain", default="",
                        help="搭配 --latest 用,限定只看某 domain 的 runs")
    args = parser.parse_args()

    try:
        from pymongo import MongoClient
        client = MongoClient(
            config.MONGO_URI,
            serverSelectionTimeoutMS=config.MONGO_SERVER_SELECTION_TIMEOUT_MS,
        )
        client.admin.command("ping")
        db = client[config.MONGO_DB]
    except Exception as e:
        print(f"❌ MongoDB 連線失敗: {e}")
        return 1

    from test_run_repository import TestRunRepository
    repo = TestRunRepository(mongo_db=db, collection=config.TEST_RUNS_COLLECTION)

    # 處理 --latest
    if args.latest:
        latest = repo.get_latest(domain=args.domain or None)
        if not latest:
            print(f"❌ 找不到任何 run"
                  + (f"(domain={args.domain})" if args.domain else ""))
            return 1
        target_run_id = latest["run_id"]
        print(f"🎯 Latest run = {target_run_id} (domain={latest.get('domain','?')})")
        # 若有其他 domain 的 baseline,提醒一下
        target_domain = latest.get("domain")
        if target_domain and not args.unmark:
            existing = repo.get_baseline(domain=target_domain)
            if existing and existing.get("run_id") != target_run_id:
                print(f"⚠️  注意:domain={target_domain} 已有舊 baseline = "
                      f"{existing['run_id']},此操作會 implicit 取代它")
    else:
        if not args.run_id:
            print("❌ 請提供 run_id 或 --latest")
            return 1
        target_run_id = args.run_id

    if args.unmark:
        if repo.unmark_baseline(target_run_id):
            print(f"✅ 已取消 baseline: {target_run_id}")
            return 0
        else:
            print(f"❌ 找不到 run_id={target_run_id}")
            return 1
    else:
        if repo.mark_as_baseline(target_run_id, notes=args.notes):
            print(f"🏁 已標 baseline: {target_run_id}")
            if args.notes:
                print(f"   Notes: {args.notes}")
            return 0
        else:
            print(f"❌ 找不到 run_id={target_run_id}")
            return 1


if __name__ == "__main__":
    sys.exit(main())
