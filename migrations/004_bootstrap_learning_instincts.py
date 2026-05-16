"""
migrations/004_bootstrap_learning_instincts.py — v0.8.0 (Week 1 D1)

Bootstrap migration:把 GenBI v0.3.x–v0.7.x 累積的 13 條 historical hotfix
seed 進 `learning_instincts` collection。

對齊 GenBI v1.3 Self-Learning MVP Spec §8.1。

# 使用方式
```bash
# Dry run(預覽不寫 DB)
python migrations/004_bootstrap_learning_instincts.py --dry-run

# 實際 seed
python migrations/004_bootstrap_learning_instincts.py

# Force re-seed(若想覆蓋手動改過的 seed)
python migrations/004_bootstrap_learning_instincts.py --force
```

跟既有 001/002/003 migration 一致風格,可由 admin CLI 呼叫或在 CI 自動跑。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

import config
from learning.bootstrap import HISTORICAL_SEEDS, seed_all, _ensure_indexes


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bootstrap historical hotfix instincts into learning_instincts"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview without writing to DB")
    parser.add_argument("--force", action="store_true",
                        help="Force re-seed even if production-modified records exist")
    args = parser.parse_args()

    print("═" * 70)
    print("  Migration 004 · Bootstrap Learning Instincts")
    print(f"  Source: GenBI v0.3.x–v0.7.x hotfixes ({len(HISTORICAL_SEEDS)} seeds)")
    print(f"  Spec:   GenBI_v1.3_Self_Learning_MVP_Implementation_Spec.md §8.1")
    print("═" * 70)
    print()

    try:
        from pymongo import MongoClient
        client = MongoClient(
            config.MONGO_URI,
            serverSelectionTimeoutMS=config.MONGO_SERVER_SELECTION_TIMEOUT_MS,
        )
        client.admin.command("ping")
        db = client[config.MONGO_DB]
        print(f"✅ Connected: {config.MONGO_DB}")
    except Exception as e:
        print(f"❌ MongoDB 連線失敗:{e}", file=sys.stderr)
        return 1

    # 1. Ensure indexes
    if not args.dry_run:
        print("\n📐 Ensuring indexes on learning_instincts...")
        _ensure_indexes(db, verbose=True)

    # 2. Seed
    mode = " (DRY RUN)" if args.dry_run else ""
    print(f"\n🌱 Seeding {len(HISTORICAL_SEEDS)} historical instincts{mode}...\n")
    stats = seed_all(db, dry_run=args.dry_run, verbose=True)

    # 3. Report
    print()
    print("─" * 70)
    print(f"  ➕ Inserted: {stats['inserted']}")
    print(f"  ✏️  Updated:  {stats['updated']}")
    print(f"  ⏭️  Skipped:  {stats['skipped']}  (production-modified, preserved)")
    print(f"     Total:    {stats['total']}")
    print("─" * 70)

    # 4. Verify (only if not dry-run)
    if not args.dry_run:
        coll = db["learning_instincts"]
        active_count = coll.count_documents({"status": "active", "source": "historical_seed"})
        print(f"\n📊 Verification: {active_count} active historical_seed instincts in DB")
        if active_count != len(HISTORICAL_SEEDS):
            print(f"   ⚠️  Expected {len(HISTORICAL_SEEDS)}, got {active_count}")
            print(f"   (差異可能來自 production override,跑 --force 強制覆蓋)")

    print()
    if stats["inserted"] + stats["updated"] > 0:
        print("✅ Migration 004 complete.")
        if not args.dry_run:
            print("\n📝 Next step:`python -m learning.bootstrap` 之後再跑可確認 idempotent。")
        return 0
    else:
        print("ℹ️  No changes (already up-to-date or all skipped).")
        return 0


if __name__ == "__main__":
    sys.exit(main())
