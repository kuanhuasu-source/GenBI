"""
embedded_metadata.py — v0.3.0+

各 domain metadata 的「絕對 fallback」副本。

# 為什麼存在?
- DB 連線失敗 / 文件缺失時,系統不會死
- v0.3.0 增量遷移的起點:把 tflex / ecommerce / healthcare 三個現有 metadata
  整理進統一 dict,migration 002 從這裡 seed 進 DB

# 規範
- key 是 domain 名稱(string)
- value 是 metadata dict(schema / kpi_definitions / data_limitations / ...)

# 預設 domain
- `tflex` — tFlex 員工福利申請(主 domain,production-ready)
- `ecommerce` — 假電商訂單(generality test 用)
- `healthcare` — 假健保理賠(generality test 用)
"""

from __future__ import annotations

# 直接 import 現有 metadata 檔案(這些檔案還會存在一段時間當 source of truth)
# 之後 DB 接上後,這些 Python 檔案會變 read-only 文獻
try:
    from tflex_task_metadata_agent_v3 import TASK_METADATA as _TFLEX_METADATA
except ImportError:
    _TFLEX_METADATA = None

try:
    from _test_ecommerce_metadata import ECOMMERCE_METADATA as _ECOMMERCE_METADATA
except ImportError:
    _ECOMMERCE_METADATA = None

try:
    from _test_healthcare_metadata import HEALTHCARE_METADATA as _HEALTHCARE_METADATA
except ImportError:
    _HEALTHCARE_METADATA = None


# ============================================================
# EMBEDDED_METADATA — domain → metadata dict
# ============================================================
EMBEDDED_METADATA: dict[str, dict] = {}

if _TFLEX_METADATA is not None:
    EMBEDDED_METADATA["tflex"] = _TFLEX_METADATA
if _ECOMMERCE_METADATA is not None:
    EMBEDDED_METADATA["ecommerce"] = _ECOMMERCE_METADATA
if _HEALTHCARE_METADATA is not None:
    EMBEDDED_METADATA["healthcare"] = _HEALTHCARE_METADATA


# ============================================================
# 整合進 EMBEDDED_PROMPTS dict 的 helper
# (PromptRepository 用 ("__metadata__", domain) 作為 key 來查 metadata fallback)
# ============================================================
def merge_into_embedded_prompts(prompts_dict: dict) -> None:
    """把 metadata 整合進 embedded_prompts.EMBEDDED_PROMPTS,
    讓 PromptRepository 的 fallback 機制能找到。

    呼叫一次即可(通常在 module import 時自動跑)。
    """
    for domain, md in EMBEDDED_METADATA.items():
        prompts_dict[("__metadata__", domain)] = md


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
# 自動把 metadata 接到 EMBEDDED_PROMPTS(讓 repo fallback 機制無痛吃到)
# ============================================================
try:
    from embedded_prompts import EMBEDDED_PROMPTS as _EP
    merge_into_embedded_prompts(_EP)
except ImportError:
    pass


if __name__ == "__main__":
    rows = list_embedded_domains()
    if not rows:
        print("(embedded_metadata 是空的 — 確認 tflex/_test_* metadata files 存在)")
    else:
        print(f"{'domain':15s}  {'collections':>12s}")
        print("─" * 35)
        for domain, n in rows:
            print(f"{domain:15s}  {n:>12d}")
