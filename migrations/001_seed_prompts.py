"""
migrations/001_seed_prompts.py — v0.3.0

把 embedded_prompts.py 中的 prompt template 推進 MongoDB `prompt_templates` collection。

# 使用方式
```bash
# Dry-run(只列要做什麼,不寫 DB)
python migrations/001_seed_prompts.py --dry-run

# 實際 seed
python migrations/001_seed_prompts.py

# 強制覆蓋(若 DB 已有 v1,會插入 v2 並 activate)
python migrations/001_seed_prompts.py --force
```

# 邏輯
- 對每個 EMBEDDED_PROMPTS 的 (prompt_key, domain_scope):
  - 若 DB 沒有對應的 active 版本 → 插入 v1 並 activate
  - 若 DB 已有 active 版本:
    - 預設:跳過(idempotent,migration 可多次跑)
    - `--force`:插入下一個 version 並 activate
- 完成後印摘要(seeded / skipped / errored 計數)

# Idempotency
重跑這個 script 不會重複塞同一份內容 — 已 active 則跳過。

# 失敗復原
若中途出錯,可手動 mongosh 刪 collection 或執行 `db.prompt_templates.drop()`,
然後重跑 migration。Embedded 副本永遠是 source of truth(在 v0.3.0 階段)。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 確保 import 路徑找得到 project root 的模組
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config
from embedded_prompts import EMBEDDED_PROMPTS, list_embedded


def main():
    parser = argparse.ArgumentParser(description="Seed embedded prompts into MongoDB")
    parser.add_argument("--dry-run", action="store_true",
                        help="只列要做什麼,不寫 DB")
    parser.add_argument("--force", action="store_true",
                        help="若 DB 已有 active 版本,仍插入新版並 activate")
    parser.add_argument("--user", default="migration_001",
                        help="created_by 欄位(預設: migration_001)")
    args = parser.parse_args()

    # 列出 embedded 狀態
    print("═" * 60)
    print(" Migration 001 · Seed prompts into MongoDB")
    print("═" * 60)
    print(f"\n📦 embedded_prompts.py 含有 {len(EMBEDDED_PROMPTS)} 個 template:\n")
    for key, domain, length in list_embedded():
        print(f"   {key:25s}  domain={domain:8s}  {length:>6,d} chars")

    if not EMBEDDED_PROMPTS:
        print("\n⚠️  EMBEDDED_PROMPTS 是空的 — 沒東西可 seed。")
        return 0

    # 連 DB
    if args.dry_run:
        print("\n🟡 Dry-run mode — 不會寫 DB")
        return _dry_run_report(args)

    print(f"\n🔌 連線 MongoDB: {config.MONGO_URI}{config.MONGO_DB}")
    try:
        from pymongo import MongoClient
    except ImportError:
        print("❌ pymongo 未安裝,請先 `pip install pymongo`")
        return 1

    try:
        client = MongoClient(
            config.MONGO_URI,
            serverSelectionTimeoutMS=config.MONGO_SERVER_SELECTION_TIMEOUT_MS,
        )
        client.admin.command("ping")
        db = client[config.MONGO_DB]
        print(f"✅ 連線成功")
    except Exception as e:
        print(f"❌ MongoDB 連線失敗: {e}")
        return 1

    from prompt_repository import PromptRepository
    repo = PromptRepository(
        mongo_db=db,
        prompt_collection=config.PROMPT_COLLECTION,
        embedded_fallback=EMBEDDED_PROMPTS,
        enabled=True,
    )

    seeded = []
    skipped = []
    errored = []
    coll = db[config.PROMPT_COLLECTION]

    for (prompt_key, domain), template in EMBEDDED_PROMPTS.items():
        try:
            existing = coll.find_one({
                "prompt_key": prompt_key,
                "domain_scope": domain,
                "is_active": True,
            })

            if existing and not args.force:
                print(
                    f"   ⏭  {prompt_key:25s}  domain={domain:8s}  "
                    f"已有 v{existing['version']} active,跳過 (用 --force 覆蓋)"
                )
                skipped.append((prompt_key, domain))
                continue

            new_id = repo.save_new_version(
                prompt_key=prompt_key,
                domain=domain,
                template=template,
                notes=(
                    "Initial seed from embedded_prompts.py (v0.3.0)"
                    if not existing else
                    f"Re-seed via --force (previous v{existing['version']} demoted)"
                ),
                created_by=args.user,
                activate=True,
            )
            print(
                f"   ✅ {prompt_key:25s}  domain={domain:8s}  "
                f"seeded {len(template):,} chars · id={new_id}"
            )
            seeded.append((prompt_key, domain))
        except Exception as e:
            print(f"   ❌ {prompt_key:25s}  domain={domain:8s}  失敗: {e}")
            errored.append((prompt_key, domain, str(e)))

    print()
    print("═" * 60)
    print(f"✅ Seeded : {len(seeded)}")
    print(f"⏭  Skipped: {len(skipped)} (已 active,可用 --force 覆蓋)")
    print(f"❌ Errored: {len(errored)}")
    print("═" * 60)

    if errored:
        return 1

    # 驗證:讀回來 byte-compare
    print("\n🔍 Byte-level 驗證:讀回 DB 比對 embedded 副本...")
    repo.invalidate_all()
    all_match = True
    for (prompt_key, domain), embedded_template in EMBEDDED_PROMPTS.items():
        try:
            from_db = repo.get_template(prompt_key, domain)
            if from_db == embedded_template:
                print(f"   ✅ {prompt_key:25s}  domain={domain:8s}  byte-equal")
            else:
                print(f"   ❌ {prompt_key:25s}  domain={domain:8s}  MISMATCH!")
                print(f"      embedded chars: {len(embedded_template):,}")
                print(f"      from DB chars  : {len(from_db):,}")
                all_match = False
        except Exception as e:
            print(f"   ❌ {prompt_key:25s}  domain={domain:8s}  讀回失敗: {e}")
            all_match = False

    if all_match:
        print("\n✅ 全部 byte-equal,migration 完成。")
        print("\n下一步:")
        print("   1. 設定環境變數 `export GENBI_PROMPT_REPO=true`")
        print("   2. 重啟 Streamlit / 跑 test_runner")
        print("   3. 確認行為跟前版一致")
        return 0
    else:
        print("\n⚠️  存在 byte-mismatch,請檢查。")
        return 1


def _dry_run_report(args):
    """只報告會做什麼,不連 DB / 不寫入。"""
    print("\n計畫操作:")
    for (prompt_key, domain), template in EMBEDDED_PROMPTS.items():
        action = (
            "insert v1 + activate(若 DB 未有此 key)"
            "  | re-seed as v_next + activate(若 --force)"
        )
        print(f"   {prompt_key:25s}  domain={domain:8s}  {action}")
        print(f"      template length: {len(template):,} chars")
    print("\n(實際跑請拿掉 --dry-run)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
