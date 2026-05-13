"""
embedded_metadata.py — v0.3.0+ (revised v0.3.2)

各 domain metadata 的「絕對 fallback」副本。

# 為什麼存在?
- DB 連線失敗 / 文件缺失時,系統不會死
- v0.3.0 增量遷移的起點:把 production domain metadata 整理進統一 dict

# 規範
- key 是 domain 名稱(string)
- value 是 metadata dict(schema / kpi_definitions / data_limitations / ...)

# v0.3.1+ 預設 domain
- `tflex` — tFlex 員工福利申請(主 domain,production-ready)

# 為什麼不再 embed ecommerce / healthcare?
v0.2.x 時代有 `_test_ecommerce_metadata.py` / `_test_healthcare_metadata.py`
作為 `test_generality.py` 的測試 fixture。但這兩個 domain 不是 production
用戶會接觸到的 — 把它們塞進 embedded fallback,UI 跟 sidebar 就會誤秀
讓使用者以為要選哪個。所以 v0.3.1 起預設只 embed `tflex`。

要在 UI 看到 ecommerce/healthcare,有兩條路:
  1) 透過 admin UI(`pages/04_metadata.py` 的「➕ 新增 domain」)手動建立
  2) 跑 `python migrations/002_seed_metadata.py --include-test-fixtures`
     (預設不會帶,要明確加 flag)

注:`test_generality.py` 仍然直接從 `_test_*_metadata.py` import,
跨 domain 通用性測試不受影響。
"""

from __future__ import annotations

# Production domain metadata(預設只有 tflex)
try:
    from tflex_task_metadata_agent_v3 import TASK_METADATA as _TFLEX_METADATA
except ImportError:
    _TFLEX_METADATA = None


# ============================================================
# EMBEDDED_METADATA — domain → metadata dict
# ============================================================
EMBEDDED_METADATA: dict[str, dict] = {}

if _TFLEX_METADATA is not None:
    EMBEDDED_METADATA["tflex"] = _TFLEX_METADATA


# ============================================================
# 整合進 EMBEDDED_PROMPTS dict 的 helper
# ============================================================
def merge_into_embedded_prompts(prompts_dict: dict) -> None:
    """把 metadata 整合進 embedded_prompts.EMBEDDED_PROMPTS,
    讓 PromptRepository 的 fallback 機制能找到。
    """
    for domain, md in EMBEDDED_METADATA.items():
        prompts_dict[("__metadata__", domain)] = md


# ============================================================
# 給 migration 002 用 — 含「測試 fixture」的可選清單
# (預設不放進 EMBEDDED_METADATA,要明確 import 才會出現)
# ============================================================
def load_test_fixture_metadata() -> dict[str, dict]:
    """載入 v0.2.x 的 ecommerce / healthcare 測試 metadata。

    Returns:
        { "ecommerce": dict, "healthcare": dict } — 只含成功 import 的

    使用場景:
        - `migrations/002_seed_metadata.py --include-test-fixtures` 模式
        - 開發者想跨 domain 試系統時手動載入
    """
    out: dict[str, dict] = {}
    try:
        from _test_ecommerce_metadata import ECOMMERCE_METADATA
        out["ecommerce"] = ECOMMERCE_METADATA
    except ImportError:
        pass
    try:
        from _test_healthcare_metadata import HEALTHCARE_METADATA
        out["healthcare"] = HEALTHCARE_METADATA
    except ImportError:
        pass
    return out


# ============================================================
# 開發工具
# ============================================================
def list_embedded_domains() -> list[tuple[str, int]]:
    """回傳 (domain, schema collection 數) 摘要。"""
    rows = []
    for domain, md in EMBEDDED_METADATA.items():
        collections_count = len(md.get("collections", {}) or {})
        rows.append((domain, collections_count))
    return sorted(rows)


# ============================================================
# 自動把 metadata 接到 EMBEDDED_PROMPTS
# ============================================================
try:
    from embedded_prompts import EMBEDDED_PROMPTS as _EP
    merge_into_embedded_prompts(_EP)
except ImportError:
    pass


if __name__ == "__main__":
    rows = list_embedded_domains()
    if not rows:
        print("(embedded_metadata 是空的 — 確認 tflex metadata 檔案存在)")
    else:
        print(f"{'domain':15s}  {'collections':>12s}")
        print("─" * 35)
        for domain, n in rows:
            print(f"{domain:15s}  {n:>12d}")
    print()
    extras = load_test_fixture_metadata()
    if extras:
        print(f"(v0.2.x 測試 fixture 可選 import — {len(extras)} 個 — "
              f"用 migration 002 --include-test-fixtures 啟用)")
        for d in extras:
            print(f"   • {d}")
