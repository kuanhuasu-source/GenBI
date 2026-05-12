"""
GenBI 統一設定檔 — 所有 LLM / MongoDB 連線參數的單一來源 of truth。

# Provider 支援
- **ollama**(預設,本機開發):本機跑 qwen3-coder:30b 之類
- **vllm**(production):A100 上跑 Qwen2.5-Coder-32B-Instruct-AWQ
- **openai**(雲端 API):OpenAI / 任何 OpenAI-compatible
- **custom**:完全自定義 endpoint

# 切換方式
最簡單:在 `.env` 中設定 `HRDA_MODEL_PROVIDER=ollama|vllm|openai`,
其他欄位若不指定會自動套用該 provider 的合理預設。

# 環境變數優先序
1. `HRDA_MODEL_*`(預設名稱,跨專案一致)
2. `VLLM_*` / `OLLAMA_*`(舊式別名,向下相容)
3. Provider 預設值
4. 程式碼最終 fallback
"""

import os


# ============================================================
# Provider 預設值表
# ============================================================
_PROVIDER_DEFAULTS = {
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "api_key": "ollama",
        "model": "qwen3-coder:30b",
        "timeout_s": 180.0,  # 本機 thinking 模型首次推論慢,給足
    },
    "vllm": {
        "base_url": "http://localhost:8000/v1",
        "api_key": "vllm-dummy",
        "model": "qwen-coder",  # 對應 vLLM --served-model-name
        "timeout_s": 60.0,
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "api_key": "",  # 必須由 env 提供
        "model": "gpt-4o-mini",
        "timeout_s": 60.0,
    },
}


# ============================================================
# 1. LLM 設定
# ============================================================
LLM_PROVIDER: str = os.getenv("HRDA_MODEL_PROVIDER", "ollama").lower()
_defaults = _PROVIDER_DEFAULTS.get(LLM_PROVIDER, _PROVIDER_DEFAULTS["ollama"])


def _normalize_base_url(url: str) -> str:
    """接受 `/v1`、`/v1/chat/completions`、無尾的 `/` — 統一成 `/v1` 結尾。"""
    if not url:
        return ""
    url = url.rstrip("/")
    if url.endswith("/chat/completions"):
        url = url[: -len("/chat/completions")]
    if not url.endswith("/v1"):
        url = url + "/v1"
    return url


LLM_BASE_URL: str = _normalize_base_url(
    os.getenv("HRDA_MODEL_BASE_URL")
    or os.getenv("VLLM_URL")
    or _defaults["base_url"]
)

# LLMService 接受 `/chat/completions` 形式
LLM_API_URL: str = LLM_BASE_URL + "/chat/completions"

LLM_API_KEY: str = (
    os.getenv("HRDA_MODEL_API_KEY")
    or os.getenv("VLLM_API_KEY")
    or _defaults["api_key"]
)

LLM_MODEL: str = (
    os.getenv("HRDA_MODEL_NAME")
    or os.getenv("VLLM_MODEL")
    or _defaults["model"]
)

LLM_TIMEOUT_S: float = float(
    os.getenv("HRDA_MODEL_TIMEOUT_S", str(_defaults["timeout_s"]))
)

LLM_TEMPERATURE: float = float(os.getenv("HRDA_MODEL_TEMPERATURE", "0.0"))


def llm_service_kwargs() -> dict:
    """回傳可直接 `LLMService(**kwargs)` 使用的 dict。
    使用方式:
        from llm_service import LLMService
        from config import llm_service_kwargs
        llm = LLMService(**llm_service_kwargs(), task_metadata=METADATA)
    """
    return {
        "api_url": LLM_API_URL,
        "api_key": LLM_API_KEY,
        "model_name": LLM_MODEL,
        "timeout_s": LLM_TIMEOUT_S,
        "default_temperature": LLM_TEMPERATURE,
    }


# ============================================================
# 2. MongoDB 設定
# ============================================================
MONGO_URI: str = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
MONGO_DB: str = os.getenv("MONGO_DB", "tflex_demo")
MONGO_COLL_APPLICATIONS: str = os.getenv(
    "MONGO_COLLECTION_APPLICATIONS", "tflex_applications"
)
MONGO_COLL_COMPANY_HC: str = os.getenv(
    "MONGO_COLLECTION_COMPANY_HC", "tflex_company_hc"
)
MONGO_SERVER_SELECTION_TIMEOUT_MS: int = int(
    os.getenv("MONGO_SERVER_SELECTION_TIMEOUT_MS", "2000")
)


# ============================================================
# 3. 開發 / 路徑相關
# ============================================================
import pathlib as _pl

PROJECT_ROOT = _pl.Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"


# ============================================================
# 4. Helpers
# ============================================================
def mask_secret(value: str, keep: int = 4) -> str:
    """敏感字串遮罩 — 用於印出設定不洩漏 api key。"""
    if not value:
        return "(empty)"
    if len(value) <= keep:
        return "***"
    return value[:keep] + "***"


def print_summary() -> None:
    """啟動時印出當前設定 (敏感資訊已遮罩)。"""
    print("─" * 60)
    print(" GenBI Config Summary")
    print("─" * 60)
    print(f"  LLM provider     : {LLM_PROVIDER}")
    print(f"  LLM endpoint     : {LLM_BASE_URL}")
    print(f"  LLM model        : {LLM_MODEL}")
    print(f"  LLM timeout      : {LLM_TIMEOUT_S}s")
    print(f"  LLM api_key      : {mask_secret(LLM_API_KEY)}")
    print(f"  LLM temperature  : {LLM_TEMPERATURE}")
    print(f"  MongoDB URI      : {MONGO_URI}")
    print(f"  MongoDB DB       : {MONGO_DB}")
    print(f"  Mongo app coll   : {MONGO_COLL_APPLICATIONS}")
    print(f"  Mongo hc coll    : {MONGO_COLL_COMPANY_HC}")
    print("─" * 60)


if __name__ == "__main__":
    # 執行 `python config.py` 直接看當前設定
    print_summary()
