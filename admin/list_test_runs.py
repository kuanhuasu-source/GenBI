"""
admin/list_test_runs.py — v0.3.0+

列最近 N 筆 test_runs(可 by domain / 只看 baseline)。

# 使用方式
```bash
python admin/list_test_runs.py
python admin/list_test_runs.py --domain tflex
python admin/list_test_runs.py --limit 50 --only-baseline
python admin/list_test_runs.py --json    # JSON 輸出,給 jq 用
```
"""

from __future__ import annotations

import argparse
import sys
import json
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

import config


def main():
    parser = argparse.ArgumentParser(description="List recent test runs")
    parser.add_argument("--domain", default="", help="只看此 domain")
    parser.add_argument("--limit", type=int, default=20, help="最多列幾筆")
    parser.add_argument("--only-baseline", action="store_true",
                        help="只列被標為 baseline 的 runs")
    parser.add_argument("--json", action="store_true", help="JSON 格式輸出")
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
        print(f"❌ MongoDB 連線失敗: {e}", file=sys.stderr)
        return 1

    query = {}
    if args.domain:
        query["domain"] = args.domain
    if args.only_baseline:
        query["is_baseline"] = True

    runs = list(
        db[config.TEST_RUNS_COLLECTION]
        .find(query, {
            "run_id": 1, "domain": 1, "git_commit": 1,
            "started_at": 1, "total_wall_s": 1,
            "summary": 1, "is_baseline": 1, "baseline_notes": 1,
            "filter": 1,
        })
        .sort("started_at", -1)
        .limit(args.limit)
    )

    if args.json:
        for r in runs:
            r.pop("_id", None)
        print(json.dumps(runs, default=str, ensure_ascii=False, indent=2))
        return 0

    # Pretty table
    print(f"\n{'═' * 100}")
    print(f"  Recent {len(runs)} test runs"
          + (f" · domain={args.domain}" if args.domain else "")
          + (" · only baseline" if args.only_baseline else ""))
    print(f"{'═' * 100}\n")

    if not runs:
        print("  (no runs found)\n")
        return 0

    # pass 欄含 refusal_detected(預期成功路徑) — 跟 test_runner 的 pass_count 邏輯一致
    print(f"  {'run_id':19s} {'domain':10s} {'commit':9s} "
          f"{'OK/total':>10s}{'refusal':>8s}{'failed':>7s} "
          f"{'wall':>7s} {'tokens':>10s}  baseline")
    print(f"  {'-' * 19} {'-' * 10} {'-' * 9} {'-' * 10}"
          f"{'-' * 8}{'-' * 7} {'-' * 7} {'-' * 10}  {'-' * 10}")

    for r in runs:
        s = r.get("summary", {})
        passed = s.get("passed", 0)
        refusal = s.get("refusal_detected", 0)
        ok = passed + refusal  # 預期成功(pass + 正確拒絕)
        total = s.get("total_cases", 0)
        failed = s.get("failed", 0) + s.get("fatal_error", 0) + s.get("phaseA_error", 0)
        tokens = s.get("total_tokens", 0)
        wall = r.get("total_wall_s", 0)
        is_baseline = "🏁 baseline" if r.get("is_baseline") else ""

        print(
            f"  {r.get('run_id', '?'):19s} "
            f"{r.get('domain', '?')[:10]:10s} "
            f"{(r.get('git_commit') or '?')[:9]:9s} "
            f"{ok:>4d}/{total:<5d}"
            f"{refusal:>8d}"
            f"{failed:>7d} "
            f"{wall:>6.0f}s "
            f"{tokens:>10,d}  {is_baseline}"
        )

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
