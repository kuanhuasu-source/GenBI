"""
admin/compare_baseline.py — v0.3.0+

比對兩筆 test run(預設「最新 vs baseline」)。

# 使用方式
```bash
# 最新 vs 當前 baseline
python admin/compare_baseline.py

# 指定 domain
python admin/compare_baseline.py --domain tflex

# 指定兩筆 run_id 對比
python admin/compare_baseline.py --a 20260513_120000 --b 20260513_143020
```
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

import config


def _arrow(value):
    if not isinstance(value, (int, float)):
        return ""
    if value > 0:
        return f"↑ +{value:,}"
    if value < 0:
        return f"↓ {value:,}"
    return "—"


def main():
    parser = argparse.ArgumentParser(description="Compare two test runs")
    parser.add_argument("--a", default="",
                        help="第一筆 run_id (預設:當前 baseline)")
    parser.add_argument("--b", default="",
                        help="第二筆 run_id (預設:最新 run)")
    parser.add_argument("--domain", default="",
                        help="搭配預設模式 — 限定某 domain")
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

    # 預設 a = baseline, b = latest
    if not args.a:
        baseline = repo.get_baseline()
        if not baseline:
            print("❌ 沒有設定 baseline,請先 `python admin/mark_baseline.py --latest ...`")
            return 1
        args.a = baseline["run_id"]

    if not args.b:
        query = {}
        if args.domain:
            query["domain"] = args.domain
        latest = db[config.TEST_RUNS_COLLECTION].find_one(
            query, sort=[("started_at", -1)]
        )
        if not latest:
            print(f"❌ 找不到任何 run"
                  + (f"(domain={args.domain})" if args.domain else ""))
            return 1
        args.b = latest["run_id"]

    if args.a == args.b:
        print(f"⚠️  a 跟 b 是同一筆 run ({args.a}),沒得比")
        return 1

    try:
        diff = repo.compare(args.a, args.b)
    except KeyError as e:
        print(f"❌ {e}")
        return 1

    a_info, b_info = diff["a"], diff["b"]
    delta = diff["delta"]
    changes = diff["case_changes"]

    print()
    print("╭" + "─" * 70 + "╮")
    print(f"│ Run A (baseline?): {a_info['run_id']:48s} │")
    print(f"│ Run B (latest?):   {b_info['run_id']:48s} │")
    print("├" + "─" * 70 + "┤")
    print(f"│ {'Metric':25s} {'A':>14s} {'B':>14s} {'delta':>14s} │")
    print("├" + "─" * 70 + "┤")
    for key in ("passed", "failed", "refusal_detected", "total_cases",
                "total_tokens", "total_calls"):
        va = a_info.get(key, 0)
        vb = b_info.get(key, 0)
        d = delta.get(key, 0)
        print(f"│ {key:25s} {va:>14,} {vb:>14,} {_arrow(d):>14s} │")
    print("╰" + "─" * 70 + "╯")
    print()

    # Case 變化
    print(f"📋 Case 狀態變化 (改變的有 {len(changes)} 筆):\n")
    if not changes:
        print("   ✅ 所有 case 狀態相同(無改變)")
    else:
        for c in changes:
            cid = c.get("id", "?")
            a_st = c.get("a_status", "?")
            b_st = c.get("b_status", "?")
            is_progress = (a_st != "pass" and b_st == "pass") or \
                          (a_st.startswith("fail") and b_st == "pass")
            is_regress = (a_st == "pass" and b_st != "pass") or \
                         (a_st == "refusal_detected" and b_st.startswith("fail"))
            icon = "✅" if is_progress else ("⚠️ " if is_regress else "🔄")
            print(f"   {icon} {cid:10s} {a_st:25s} → {b_st}")

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
