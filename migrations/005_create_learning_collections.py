"""
migrations/005_create_learning_collections.py — v0.8.1 (Week 1 D2)

建立 self-learning system 用的其他 4 個 MongoDB collections + indexes:

  - learning_observations
  - verifier_results
  - learning_jobs
  - prompt_rule_candidates

(learning_instincts 已由 migration 004 + bootstrap 建好,本 migration 不重複)

對齊 GenBI_v1.3_Self_Learning_MVP_Implementation_Spec.md §7。

# 使用方式
```bash
# Dry run(只印不寫)
python migrations/005_create_learning_collections.py --dry-run

# 實際建 indexes
python migrations/005_create_learning_collections.py
```

Idempotent:多次執行不會 break(MongoDB createIndex 對 already-existing
spec 是 no-op)。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

import config


# ============================================================
# Collection 規格(對齊 spec §7.1–7.5)
# ============================================================
# 每個 collection 描述:
#   - name: collection 名(也是 config.* env 對應)
#   - indexes: list of (keys, options) tuples 給 create_index 用
#   - description: 給 admin / log 用
# ============================================================

COLLECTION_SPECS: list[dict] = [
    {
        "name": "learning_observations",
        "description": "從 failed task_traces 抽出的結構化 observation",
        "indexes": [
            # observation_id 必 unique
            ([("observation_id", 1)], {"unique": True, "name": "observation_id_unique"}),
            # 查某次 run 抽出的 observations
            ([("run_id", 1)], {"name": "run_id_idx"}),
            # 篩 candidate/verified/rejected
            ([("status", 1)], {"name": "status_idx"}),
            # dedupe — 同樣 cause+recommendation 不應重複進
            ([("dedupe_key", 1)], {"unique": True, "name": "dedupe_key_unique"}),
            # 依時間倒序看最新 observations
            ([("created_at", -1)], {"name": "created_at_desc"}),
            # tag-based 查詢(consolidation 用)
            ([("tags", 1)], {"name": "tags_idx"}),
            # phase-specific 篩選
            ([("phase", 1), ("status", 1)],
             {"name": "phase_status_idx"}),
        ],
    },
    {
        "name": "verifier_results",
        "description": "Verifier agent 對 observation 的獨立驗證結果",
        "indexes": [
            ([("observation_id", 1)], {"name": "observation_id_idx"}),
            ([("decision", 1)], {"name": "decision_idx"}),
            ([("created_at", -1)], {"name": "created_at_desc"}),
        ],
    },
    {
        "name": "learning_jobs",
        "description": "Consolidation / decay / resolution scan 等定期 job 的執行記錄",
        "indexes": [
            ([("job_id", 1)], {"unique": True, "name": "job_id_unique"}),
            ([("status", 1)], {"name": "status_idx"}),
            ([("job_type", 1)], {"name": "job_type_idx"}),
            ([("started_at", -1)], {"name": "started_at_desc"}),
        ],
    },
    {
        "name": "prompt_rule_candidates",
        "description": "從 active instinct 升出的 prompt patch candidate",
        "indexes": [
            ([("candidate_id", 1)], {"unique": True, "name": "candidate_id_unique"}),
            ([("status", 1)], {"name": "status_idx"}),
            ([("instinct_id", 1)], {"name": "instinct_id_idx"}),
            ([("target_component", 1)], {"name": "target_component_idx"}),
            ([("created_at", -1)], {"name": "created_at_desc"}),
        ],
    },
]


# ============================================================
# Core function
# ============================================================
def ensure_learning_collections(db, *, dry_run: bool = False,
                                  verbose: bool = True) -> dict:
    """
    Idempotent:建好 4 個 learning_* collection + 所有 indexes。

    Returns:
        {
          "collections_existed": [name, ...],
          "collections_created": [name, ...],
          "indexes_created": int,
          "indexes_existed": int,
        }
    """
    if db is None:
        raise ValueError("db is required (pymongo Database instance)")

    stats = {
        "collections_existed": [],
        "collections_created": [],
        "indexes_created": 0,
        "indexes_existed": 0,
    }

    existing_collections = set(db.list_collection_names()) if not dry_run else set()

    for spec in COLLECTION_SPECS:
        name = spec["name"]
        desc = spec["description"]

        if verbose:
            print(f"\n📦 {name}")
            print(f"   {desc}")

        # ── Step 1:collection 本身 ──
        if dry_run:
            print(f"   [dry-run] would ensure collection")
        else:
            if name in existing_collections:
                stats["collections_existed"].append(name)
                if verbose:
                    print(f"   ⏭️  collection already exists")
            else:
                db.create_collection(name)
                stats["collections_created"].append(name)
                if verbose:
                    print(f"   ✅ created collection")

        # ── Step 2:indexes ──
        if dry_run:
            for keys, opts in spec["indexes"]:
                print(f"   [dry-run] would create_index {keys} {opts}")
            continue

        coll = db[name]
        existing_index_names = {idx["name"] for idx in coll.list_indexes()}

        for keys, opts in spec["indexes"]:
            idx_name = opts.get("name", "_".join(f"{k}_{v}" for k, v in keys))
            if idx_name in existing_index_names:
                stats["indexes_existed"] += 1
                if verbose:
                    print(f"   ⏭️  index {idx_name} already exists")
                continue
            try:
                coll.create_index(keys, **opts)
                stats["indexes_created"] += 1
                if verbose:
                    print(f"   ✅ created index {idx_name}")
            except Exception as e:
                if verbose:
                    print(f"   ❌ index {idx_name} failed: {e}")

    return stats


# ============================================================
# CLI entry
# ============================================================
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create learning_* collections + indexes "
                    "(observation / verifier / jobs / candidates)"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview without writing to DB")
    args = parser.parse_args()

    print("═" * 70)
    print("  Migration 005 · Create Learning Collections")
    print(f"  Spec:  GenBI_v1.3_Self_Learning_MVP_Implementation_Spec.md §7")
    print("═" * 70)

    try:
        from pymongo import MongoClient
        client = MongoClient(
            config.MONGO_URI,
            serverSelectionTimeoutMS=config.MONGO_SERVER_SELECTION_TIMEOUT_MS,
        )
        client.admin.command("ping")
        db = client[config.MONGO_DB]
        print(f"\n✅ Connected: {config.MONGO_DB}")
    except Exception as e:
        print(f"\n❌ MongoDB 連線失敗:{e}", file=sys.stderr)
        return 1

    mode = " (DRY RUN)" if args.dry_run else ""
    print(f"\nEnsuring {len(COLLECTION_SPECS)} collections + indexes{mode}...")

    stats = ensure_learning_collections(db, dry_run=args.dry_run, verbose=True)

    print()
    print("─" * 70)
    print(f"  Collections existed: {len(stats['collections_existed'])}")
    print(f"  Collections created: {len(stats['collections_created'])}")
    print(f"  Indexes existed:     {stats['indexes_existed']}")
    print(f"  Indexes created:     {stats['indexes_created']}")
    print("─" * 70)
    print("\n✅ Migration 005 complete.")

    if not args.dry_run:
        print("\n📊 Verify with:")
        print("   mongo {db}".format(db=config.MONGO_DB))
        print("   db.learning_observations.getIndexes()")

    return 0


if __name__ == "__main__":
    sys.exit(main())
