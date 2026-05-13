"""
admin/list_prompts.py — v0.3.0+

列當前所有 active prompt versions。

# 使用方式
```bash
python admin/list_prompts.py
python admin/list_prompts.py --domain tflex
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
    parser = argparse.ArgumentParser(description="List active prompt versions")
    parser.add_argument("--domain", default="", help="只看此 domain (預設全部)")
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

    query = {"is_active": True}
    if args.domain:
        query["domain_scope"] = {"$in": [args.domain, "*"]}

    docs = list(
        db[config.PROMPT_COLLECTION].find(query)
        .sort([("prompt_key", 1), ("domain_scope", 1)])
    )

    if not docs:
        print(f"(no active prompts found"
              + (f" for domain={args.domain}" if args.domain else "")
              + ")")
        return 0

    print(f"\n{'═' * 90}")
    print(f"  Active prompt versions"
          + (f" · domain={args.domain}" if args.domain else " · all"))
    print(f"{'═' * 90}\n")

    print(f"  {'prompt_key':25s} {'domain':10s} {'ver':>4s} "
          f"{'chars':>8s}  {'created_at':20s}  notes")
    print(f"  {'-' * 25} {'-' * 10} {'-' * 4} "
          f"{'-' * 8}  {'-' * 20}  {'-' * 30}")

    for d in docs:
        ts = d.get("created_at")
        ts_str = str(ts)[:19] if ts else "?"
        print(
            f"  {d.get('prompt_key','?'):25s} "
            f"{d.get('domain_scope','?'):10s} "
            f"{d.get('version','?'):>4} "
            f"{len(d.get('template','')):>8,d}  "
            f"{ts_str:20s}  "
            f"{(d.get('notes') or '')[:50]}"
        )

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
