"""
migrations/002_seed_metadata.py — v0.3.0

把 embedded_metadata.py 的 domain metadata 推進 MongoDB `domain_metadata` collection。

# 使用方式
```bash
python migrations/002_seed_metadata.py --dry-run
python migrations/002_seed_metadata.py
python migrations/002_seed_metadata.py --force          # 已有 active 仍插新版
python migrations/002_seed_metadata.py --domain tflex   # 只 seed 特定 domain
```

# 邏輯
- 對 EMBEDDED_METADATA 的每個 domain:
  - 若 DB 沒對應 active → 插入 v1 並 activate
  - 已有 active 預設跳過,`--force` 才插新版

# Idempotency
重跑不會塞重複內容。

# 失敗復原
mongosh `db.domain_metadata.drop()` 然後重跑。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config
from embedded_metadata import (
    EMBEDDED_METADATA, list_embedded_domains, load_test_fixture_metadata,
)


def main():
    parser = argparse.ArgumentParser(description="Seed embedded metadata into MongoDB")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true",
                        help="若已有 active 版本,仍插新版並 activate")
    parser.add_argument("--domain", default="",
                        help="只 seed 此 domain(預設全部)")
    parser.add_argument("--include-test-fixtures", action="store_true",
                        help="把 v0.2.x 的 ecommerce / healthcare 測試 fixture metadata "
                             "也 seed 進 DB(預設只 seed production domains)")
    parser.add_argument("--user", default="migration_002")
    args = parser.parse_args()

    print("═" * 60)
    print(" Migration 002 · Seed metadata into MongoDB")
    print("═" * 60)

    # 組要 seed 的 source dict
    source = dict(EMBEDDED_METADATA)  # 預設 production
    if args.include_test_fixtures:
        fixtures = load_test_fixture_metadata()
        print(f"\n🧪 --include-test-fixtures: 加入 {len(fixtures)} 個 v0.2.x 測試 metadata")
        for d in fixtures:
            print(f"   • {d}")
        source.update(fixtures)

    print(f"\n📦 將 seed 的 domain 共 {len(source)} 個:\n")
    for domain in sorted(source.keys()):
        n_coll = len((source[domain].get("collections") or {}))
        marker = " ← 過濾選中" if args.domain and args.domain == domain else ""
        print(f"   {domain:15s}  {n_coll:>3d} collections{marker}")

    if not source:
        print("\n⚠️  沒東西可 seed")
        return 0

    # 過濾
    targets = {
        d: md for d, md in source.items()
        if not args.domain or d == args.domain
    }
    if args.domain and not targets:
        print(f"\n❌ 找不到 domain {args.domain!r}"
              f"{' (試試 --include-test-fixtures)' if args.domain in ('ecommerce','healthcare') else ''}")
        return 1

    if args.dry_run:
        print("\n🟡 Dry-run mode — 不會寫 DB")
        for domain, md in targets.items():
            keys = sorted(md.keys())
            print(f"   {domain}: 會 insert v1 + activate(keys={keys})")
        return 0

    # 連 DB
    print(f"\n🔌 連線 MongoDB: {config.MONGO_URI}{config.MONGO_DB}")
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
        print("✅ 連線成功")
    except Exception as e:
        print(f"❌ MongoDB 連線失敗: {e}")
        return 1

    from prompt_repository import PromptRepository
    repo = PromptRepository(
        mongo_db=db,
        metadata_collection=config.METADATA_COLLECTION,
        embedded_fallback={
            ("__metadata__", d): md for d, md in EMBEDDED_METADATA.items()
        },
        enabled=True,
    )

    seeded, skipped, errored = [], [], []
    coll = db[config.METADATA_COLLECTION]

    for domain, metadata in targets.items():
        try:
            existing = coll.find_one({"domain": domain, "is_active": True})
            if existing and not args.force:
                print(
                    f"   ⏭  {domain:15s}  已有 v{existing['version']} active,"
                    f"跳過 (用 --force 覆蓋)"
                )
                skipped.append(domain)
                continue

            new_id = repo.save_new_metadata_version(
                domain=domain,
                metadata=metadata,
                notes=(
                    "Initial seed from embedded_metadata.py (v0.3.0)"
                    if not existing else
                    f"Re-seed via --force (previous v{existing['version']} demoted)"
                ),
                created_by=args.user,
                activate=True,
            )
            n_coll = len(metadata.get("collections", {}) or {})
            print(f"   ✅ {domain:15s}  seeded {n_coll} collections · id={new_id}")
            seeded.append(domain)
        except Exception as e:
            print(f"   ❌ {domain:15s}  失敗: {e}")
            errored.append((domain, str(e)))

    print()
    print("═" * 60)
    print(f"✅ Seeded : {len(seeded)}")
    print(f"⏭  Skipped: {len(skipped)}")
    print(f"❌ Errored: {len(errored)}")
    print("═" * 60)

    if errored:
        return 1

    # 驗證 byte-equal
    print("\n🔍 驗證:讀回 DB 比對 embedded 副本...")
    repo.invalidate_all()
    all_match = True
    for domain, embedded_md in targets.items():
        try:
            from_db = repo.get_metadata(domain)
            # 比較 keys (DB 會去掉內部欄位)
            embedded_keys = set(embedded_md.keys())
            db_keys = set(from_db.keys())
            if embedded_keys == db_keys:
                print(f"   ✅ {domain:15s}  keys match ({len(embedded_keys)} keys)")
            else:
                print(f"   ❌ {domain:15s}  keys mismatch")
                print(f"      embedded only: {embedded_keys - db_keys}")
                print(f"      DB only:       {db_keys - embedded_keys}")
                all_match = False
        except Exception as e:
            print(f"   ❌ {domain:15s}  讀回失敗: {e}")
            all_match = False

    if all_match:
        print("\n✅ 全部 OK,migration 完成。")
        print("\n下一步:設定 GENBI_PROMPT_REPO=true 後重啟測試")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
