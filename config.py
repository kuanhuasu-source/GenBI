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
# .env loader — v0.3.2 修正(之前 .env 從沒被讀!)
# ============================================================
# 在 os.getenv 之前,先把 .env 內容載入 os.environ
# 若 python-dotenv 沒裝(舊環境)或檔案不存在,silently skip
try:
    from dotenv import load_dotenv as _load_dotenv
    import pathlib as _pl
    _env_path = _pl.Path(__file__).resolve().parent / ".env"
    if _env_path.exists():
        # override=False:shell export 的 env 優先,.env 是備援
        _load_dotenv(dotenv_path=_env_path, override=False)
except ImportError:
    pass


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
# Model Profiles — per-phase sampling 參數(v0.10.6+)
# ============================================================
# 不同 model 系列對 sampling 的最佳值差很多:
#   - 既有 code-tuned non-thinking 模型(qwen3-coder, Qwen2.5-Coder)
#       → code-gen 用 temp=0,plan/insight 微抬
#   - reasoning distilled 模型(Qwen3.6-27B-Claude-Opus-Reasoning-Distilled 系列)
#       → coding=0.6 / thinking=1.0 / non-thinking=0.7 + presence_penalty=1.5
#       (HF 模型卡:https://huggingface.co/rico03/Qwen3.6-27B-Claude-Opus-Reasoning-Distilled-GGUF)
#
# 每個 profile 是 phase → sampling dict 的 mapping。
# `retry_temperature` 是 optional,沒設就回退 temperature + 0.15(維持 v0.10.3 既有行為)。
#
# 切 profile 用 env:HRDA_MODEL_PROFILE=default | reasoning_distilled
# 預設 default(沿用舊行為,不會打破現有部署)。
MODEL_PROFILES: dict[str, dict[str, dict]] = {
    # 既有行為快照 — code-tuned non-thinking(qwen3-coder:30b 等)
    "default": {
        "plan":         {"temperature": 0.2},
        "pipeline":     {"temperature": 0.0, "retry_temperature": 0.15},
        "preprocess":   {"temperature": 0.0, "retry_temperature": 0.15},
        "plotly":       {"temperature": 0.0, "retry_temperature": 0.15},
        "echarts":      {"temperature": 0.0, "retry_temperature": 0.15},
        "insight":      {"temperature": 0.3},
        "meta_response": {"temperature": 0.3},
    },
    # Reasoning distilled 模型(Qwen3.6 系列)— 會輸出 <think>...</think>
    # Coding 用 0.6,thinking 用 1.0,non-thinking 用 0.7 + presence_penalty=1.5
    "reasoning_distilled": {
        "plan":         {"temperature": 1.0},  # general thinking
        "pipeline":     {"temperature": 0.6, "retry_temperature": 0.75},  # coding
        "preprocess":   {"temperature": 0.6, "retry_temperature": 0.75},  # coding
        "plotly":       {"temperature": 0.6, "retry_temperature": 0.75},  # coding
        "echarts":      {"temperature": 0.6, "retry_temperature": 0.75},  # coding
        "insight":      {"temperature": 0.7, "presence_penalty": 1.5},   # non-thinking
        "meta_response": {"temperature": 0.7, "presence_penalty": 1.5},  # non-thinking
    },
}

# Profile 選擇 — env HRDA_MODEL_PROFILE 不指定就 default
MODEL_PROFILE_NAME: str = os.getenv("HRDA_MODEL_PROFILE", "default").lower()
if MODEL_PROFILE_NAME not in MODEL_PROFILES:
    # fallback to default,印 warning 但不 raise(讓 import 不會炸)
    print(f"⚠️ HRDA_MODEL_PROFILE='{MODEL_PROFILE_NAME}' 不存在於 MODEL_PROFILES,"
          f"回退 'default'(可選:{list(MODEL_PROFILES.keys())})")
    MODEL_PROFILE_NAME = "default"
MODEL_PROFILE: dict = MODEL_PROFILES[MODEL_PROFILE_NAME]


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

# v0.13.3+: Ollama Qwen 3.6 thinking toggle
# 對 thinking 模型(如 qwen3.6:27b),Ollama API 走 extra_body={"think": False}
# 才能真關 thinking(legacy /no_think directive 只 work 在 Qwen 3.5)。
# Default False — 既有 schema-driven 行為 byte-equal,不影響 baseline。
# 對 Qwen 3.6 / 其他 thinking 模型才設 true 省 token + latency。
LLM_DISABLE_THINKING: bool = os.getenv(
    "HRDA_MODEL_DISABLE_THINKING", "false",
).lower() in ("true", "1", "yes")


def llm_service_kwargs() -> dict:
    """回傳可直接 `LLMService(**kwargs)` 使用的 dict。
    使用方式:
        from llm_service import LLMService
        from config import llm_service_kwargs
        llm = LLMService(**llm_service_kwargs(), task_metadata=METADATA)

    v0.10.6+ 自動把 MODEL_PROFILE 帶進去,LLMService 會依 phase 查 profile 解析
    sampling 參數(temperature / presence_penalty)。
    """
    return {
        "api_url": LLM_API_URL,
        "api_key": LLM_API_KEY,
        "model_name": LLM_MODEL,
        "timeout_s": LLM_TIMEOUT_S,
        "default_temperature": LLM_TEMPERATURE,
        "model_profile": MODEL_PROFILE,
        "disable_thinking": LLM_DISABLE_THINKING,    # v0.13.3+
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
# 3. Prompt / Metadata Repository (v0.3.0+)
# ============================================================
# 是否從 MongoDB 讀 prompt / metadata (False = 純走 code-embedded fallback)。
# 為了 v0.3.0 增量遷移,**目前預設 False**,即使 DB 沒接也能跑;
# seed migration 跑完 + 驗證 byte-equal 後再切 True。
PROMPT_REPO_ENABLED: bool = os.getenv("GENBI_PROMPT_REPO", "false").lower() in ("true", "1", "yes")

# Cache TTL — repo 讀取後在記憶體保留多久,避免每次 LLM call 都打 DB
PROMPT_CACHE_TTL_S: int = int(os.getenv("GENBI_PROMPT_CACHE_TTL_S", "60"))

# 4 個 repository 用的 collection 名(可由 env 覆寫,例如多環境共用同 DB 時加前綴)
PROMPT_COLLECTION: str = os.getenv("GENBI_PROMPT_COLLECTION", "prompt_templates")
METADATA_COLLECTION: str = os.getenv("GENBI_METADATA_COLLECTION", "domain_metadata")
TEST_CASES_COLLECTION: str = os.getenv("GENBI_TEST_CASES_COLLECTION", "test_cases")
TEST_RUNS_COLLECTION: str = os.getenv("GENBI_TEST_RUNS_COLLECTION", "test_runs")
AUDIT_LOG_COLLECTION: str = os.getenv("GENBI_AUDIT_LOG_COLLECTION", "audit_log")
# v0.7.0+:Task trace 紀錄(每次 query 完整步驟 + LLM call 內容)
TASK_TRACES_COLLECTION: str = os.getenv("GENBI_TASK_TRACES_COLLECTION", "task_traces")


# ============================================================
# 4. 開發 / 路徑相關
# ============================================================
import pathlib as _pl

PROJECT_ROOT = _pl.Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"


# ============================================================
# 5. Helpers
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
    print(f"  LLM profile      : {MODEL_PROFILE_NAME}")
    print(f"  Disable thinking : {LLM_DISABLE_THINKING}")
    print(f"  MongoDB URI      : {MONGO_URI}")
    print(f"  MongoDB DB       : {MONGO_DB}")
    print(f"  Mongo app coll   : {MONGO_COLL_APPLICATIONS}")
    print(f"  Mongo hc coll    : {MONGO_COLL_COMPANY_HC}")
    print(f"  Prompt repo      : {'ON' if PROMPT_REPO_ENABLED else 'OFF (embedded fallback only)'}")
    print(f"  Prompt cache TTL : {PROMPT_CACHE_TTL_S}s")
    print("─" * 60)


if __name__ == "__main__":
    # 執行 `python config.py` 直接看當前設定
    print_summary()
