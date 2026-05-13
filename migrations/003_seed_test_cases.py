"""
migrations/003_seed_test_cases.py — v0.3.0

把 embedded_test_cases.EMBEDDED_TEST_CASES 推進 MongoDB `test_cases` collection。

# 使用方式
```bash
python migrations/003_seed_test_cases.py --dry-run
python migrations/003_seed_test_cases.py
python migrations/003_seed_test_cases.py --force      # 覆蓋已存在
python migrations/003_seed_test_cases.py --domain tflex  # 只 seed 特定 domain
```

# 邏輯
- 對 EMBEDDED_TEST_CASES 的每個 (domain, case_id):
  - 若 DB 沒對應 → insert
  - 已有 → 預設跳過,`--force` 覆蓋

# Idempotency
重跑不會塞重複(以 domain+case_id 為 key)。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config
from embedded_test_cases import EMBEDDED_TEST_CASES


def main():
    parser = argparse.ArgumentParser(description="Seed embedded test cases into MongoDB")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true",
                        help="若 DB 已有此 case,仍覆蓋")
    parser.add_argument("--domain", default="",
                        help="只 seed 此 domain(預設全部)")
    parser.add_argument("--user", default="migration_003")
    args = parser.parse_args()

    print("═" * 60)
    print(" Migration 003 · Seed test cases into MongoDB")
    print("═" * 60)

    total = sum(len(v) for v in EMBEDDED_TEST_CASES.values())
    print(f"\n📦 embedded_test_cases.py 含有:")
    for domain, cases in sorted(EMBEDDED_TEST_CASES.items()):
        marker = " ← 過濾選中" if args.domain and args.domain == domain else ""
        print(f"   {domain:15s}  {len(cases):>3d} cases{marker}")
    print(f"   {'TOTAL':15s}  {total:>3d} cases\n")

    if not EMBEDDED_TEST_CASES:
        print("⚠️  EMBEDDED_TEST_CASES 是空的 — 沒東西可 seed")
        return 0

    targets = {
        d: cases for d, cases in EMBEDDED_TEST_CASES.items()
        if not args.domain or d == args.domain
    }
    if args.domain and not targets:
        print(f"❌ 找不到 domain {args.domain!r}")
        return 1

    if args.dry_run:
        print("🟡 Dry-run mode — 不會寫 DB\n")
        for domain, cases in targets.items():
            print(f"   {domain}: 會 upsert {len(cases)} cases")
            for c in cases[:3]:
                print(f"     - {c['case_id']:10s}  {c['name'][:50]}")
            if len(cases) > 3:
                print(f"     ...及 {len(cases) - 3} 筆")
        return 0

    # 連 DB
    print(f"🔌 連線 MongoDB: {config.MONGO_URI}{config.MONGO_DB}")
    try:
        from pymongo import MongoClient
    except ImportError:
        print("❌ pymongo 未安裝")
        return 1

    try:
        client = MongoClient(
            config.MONGO_URI,
            serverSelectionTimeoutMS=config.MONGO_SERVER_SELECTION_TIMEOUT_MS,
        )
        client.admin.command("ping")
        db = client[config.MONGO_DB]
        print("✅ 連線成功\n")
    except Exception as e:
        print(f"❌ MongoDB 連線失敗: {e}")
        return 1

    from test_case_repository import TestCaseRepository
    repo = TestCaseRepository(
        mongo_db=db,
        collection=config.TEST_CASES_COLLECTION,
        embedded_fallback=EMBEDDED_TEST_CASES,
        enabled=True,
    )

    # 確保 indexes 存在
    repo.ensure_indexes()
    print("📋 Indexes ensured")

    inserted, updated, skipped, errored = [], [], [], []
    coll = db[config.TEST_CASES_COLLECTION]

    for domain, cases in targets.items():
        print(f"\n📁 Seeding domain={domain} ({len(cases)} cases)...")
        for case in cases:
            cid = case.get("case_id", "?")
            try:
                existing = coll.find_one({"domain": domain, "case_id": cid})
                if existing and not args.force:
                    skipped.append((domain, cid))
                    continue
                # Copy + strip id/_id 避免衝突
                case_copy = dict(case)
                case_copy.pop("_id", None)
                case_copy.pop("domain", None)  # repo 自己設
                case_copy.pop("case_id", None)
                repo.upsert_case(domain, cid, case_copy, user=args.user)
                if existing:
                    updated.append((domain, cid))
                else:
                    inserted.append((domain, cid))
            except Exception as e:
                errored.append((domain, cid, str(e)))
                print(f"   ❌ {cid}: {e}")

    print()
    print("═" * 60)
    print(f"✅ Inserted : {len(inserted)}")
    print(f"🔄 Updated  : {len(updated)} (--force 覆蓋)")
    print(f"⏭  Skipped  : {len(skipped)}")
    print(f"❌ Errored  : {len(errored)}")
    print("═" * 60)

    if errored:
        for d, cid, e in errored:
            print(f"   {d}/{cid}: {e}")
        return 1

    # 驗證:讀回對齊
    print("\n🔍 驗證:從 DB 讀回對比 embedded 副本...")
    repo.invalidate()
    all_match = True
    for domain, embedded_cases in targets.items():
        db_cases = repo.get_cases(domain, include_inactive=True)
        emb_ids = {c["case_id"] for c in embedded_cases}
        db_ids = {c["case_id"] for c in db_cases}
        missing = emb_ids - db_ids
        if missing:
            print(f"   ❌ {domain}: DB 缺 {len(missing)} cases: {sorted(missing)}")
            all_match = False
        else:
            print(f"   ✅ {domain}: {len(emb_ids)} cases all present in DB")

    if all_match:
        print("\n✅ Migration 完成。")
        print("\n下一步:")
        print("   1. export GENBI_PROMPT_REPO=true")
        print("   2. test_runner.py 改從 DB 讀(D8c 完成後)")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
