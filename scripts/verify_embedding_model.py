"""scripts/verify_embedding_model.py — 驗證 embedding model(本地 / 離線)可載。

用法:
    python scripts/verify_embedding_model.py

讀環境變數:
    GENBI_EMBEDDING_MODEL  指向本地 model 目錄(絕對路徑優先)
    HF_HUB_OFFLINE / TRANSFORMERS_OFFLINE  =1 時強制離線

對應 SPRINT2_RUN_GUIDE.md §7.5。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# v0.16.1+ FIX:import config 觸發 .env 載入,確認 GENBI_EMBEDDING_BACKEND
# 等 env 有進到 Python process(否則只看 OS-level env 會誤判)
import config  # noqa: F401


def main() -> int:
    print("=" * 60)
    print("GenBI · Embedding model verification")
    print("=" * 60)

    # ── Env 報告 ──
    em = os.environ.get("GENBI_EMBEDDING_MODEL", "(未設,走 HF default)")
    off = os.environ.get("HF_HUB_OFFLINE", "(未設)")
    toff = os.environ.get("TRANSFORMERS_OFFLINE", "(未設)")
    hf_home = os.environ.get("HF_HOME", "(未設,走 ~/.cache/huggingface)")
    print(f"GENBI_EMBEDDING_MODEL = {em}")
    print(f"HF_HUB_OFFLINE        = {off}")
    print(f"TRANSFORMERS_OFFLINE  = {toff}")
    print(f"HF_HOME               = {hf_home}")

    # ── 路徑檢查 ──
    if em.startswith("/") or em.startswith("./"):
        p = Path(em).resolve()
        print(f"\n本地 model dir:{p}")
        if not p.exists():
            print(f"  ❌ 路徑不存在")
            return 1
        # 必要檔案(§7.1)
        required = [
            "config.json",
            "tokenizer.json",
            "tokenizer_config.json",
            "vocab.txt",
            "special_tokens_map.json",
            "modules.json",
            "sentence_bert_config.json",
            "1_Pooling/config.json",
        ]
        # 主權重 — safetensors 優先,pytorch_model.bin 也接受
        weight_ok = (p / "model.safetensors").exists() or \
                    (p / "pytorch_model.bin").exists()
        if not weight_ok:
            print("  ❌ 缺主權重 model.safetensors 或 pytorch_model.bin")
            return 1
        missing = [f for f in required if not (p / f).exists()]
        if missing:
            print(f"  ❌ 缺檔案:{missing}")
            return 1
        print(f"  ✅ 所有必要檔案都在")

    # ── 嘗試實際載入 ──
    print("\n載入 EmbeddingPipeline...")
    try:
        from embedding_pipeline import get_embedding_pipeline
        ep = get_embedding_pipeline()
        print(f"  Model         = {ep.model_name}")
        print(f"  Expected dim  = {ep.dim}")
    except Exception as e:
        print(f"  ❌ Pipeline init failed: {e}")
        return 1

    # ── 跑一次 embed ──
    print("\n跑 ep.embed_one('hello world')...")
    try:
        v = ep.embed_one("hello world")
        print(f"  ✅ Output dim   = {v.shape[0]}")
        print(f"     First 5 vals = {v[:5]}")
        if v.shape[0] != ep.dim:
            print(f"  ⚠️  dim mismatch:got {v.shape[0]} expected {ep.dim}")
            return 1
    except Exception as e:
        print(f"  ❌ Embed failed: {e}")
        if "Connection" in str(e) or "timeout" in str(e).lower():
            print("     Hint:仍在試連 HF。檢查 HF_HUB_OFFLINE=1 是否有設。")
        return 1

    # ── 一致性 sanity check(同 text 該產同 vec)──
    v2 = ep.embed_one("hello world")
    same = (v == v2).all()
    print(f"\n  Deterministic check:{'✅' if same else '❌'}")

    print("\n" + "=" * 60)
    print("✅ Embedding model 部署 OK,可以跑 scripts/build_rag_indices.py")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
