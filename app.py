"""
tFlex GenBI — Agentic Workflow (Streamlit + vLLM + MongoDB)

Phase 0 → Plan       (LLM 規劃)
Phase A → MongoDB    (LLM 產 pipeline → 真實 DB 撈取 / CSV fallback)
Phase B → Pandas     (LLM 產 preprocess code → 計算 KPI)
Phase C → Plotly     (LLM 產 plot code → 視覺化)
Phase D → Insight    (LLM 產商業洞察文字)
"""

import os
import json
import traceback
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st
from pymongo import MongoClient
from pymongo.errors import PyMongoError, ServerSelectionTimeoutError
from streamlit_echarts import st_echarts

from llm_service import (
    LLMService,
    is_dashboard_query,
    classify_intent,
    is_followup_query,
    sanitize_pipeline,
    rescue_empty_echarts,
    ensure_default_styling,
    coerce_option_native_types,
    _detect_chart_intent,
    _detect_preprocess_intent,
)
from task_trace import TaskTrace
import config


# ============================================================
# ⚙️ 環境設定:全部由 config.py 提供 (.env / 環境變數驅動)
# ============================================================
MONGO_URI = config.MONGO_URI
MONGO_DB_NAME = config.MONGO_DB
MONGO_COLL_APPLICATIONS = config.MONGO_COLL_APPLICATIONS
MONGO_COLL_COMPANY_HC = config.MONGO_COLL_COMPANY_HC
MONGO_SERVER_TIMEOUT_MS = config.MONGO_SERVER_SELECTION_TIMEOUT_MS

LLM_PROVIDER = config.LLM_PROVIDER
LLM_BASE_URL = config.LLM_BASE_URL
LLM_API_URL = config.LLM_API_URL
LLM_API_KEY = config.LLM_API_KEY
LLM_MODEL = config.LLM_MODEL
LLM_TEMPERATURE = config.LLM_TEMPERATURE
LLM_TIMEOUT_S = config.LLM_TIMEOUT_S

DATA_DIR = config.DATA_DIR
CSV_APPLICATIONS = DATA_DIR / "tflex_applications_rawdata_v2.csv"
CSV_COMPANY_HC = DATA_DIR / "tflex_company_hc_rawdata_v2.csv"

# ============================================================
# 🔌 MongoDB 連線 (cached)
# ============================================================
@st.cache_resource(show_spinner=False)
def get_mongo_db():
    """嘗試建立 MongoDB 連線,失敗則回傳 None 並附帶錯誤訊息。"""
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=MONGO_SERVER_TIMEOUT_MS)
        # 強制 ping 一次確認真的連得上
        client.admin.command("ping")
        return client[MONGO_DB_NAME], None
    except (ServerSelectionTimeoutError, PyMongoError) as e:
        return None, str(e)
    except Exception as e:
        return None, str(e)


@st.cache_data(show_spinner=False)
def load_csv_fallback() -> pd.DataFrame:
    """CSV fallback:把兩張表 left-join 後當成 raw_df 來源。"""
    apps = pd.read_csv(
        CSV_APPLICATIONS,
        dtype={"employee_id": str, "application_no": str, "company_code": str},
        keep_default_na=False, na_values=[""],
    )
    hc = pd.read_csv(
        CSV_COMPANY_HC,
        dtype={"company_code": str},
    )
    merged = apps.merge(hc, on="company_code", how="left")
    return merged


# ============================================================
# 🪛 Pipeline 解譯器 (僅在 CSV fallback 模式下使用)
#     - 把 LLM 產的 pipeline 當「過濾意圖」執行在 pandas 上
#     - 僅支援 $match / $project,其他 stage 一律忽略
# ============================================================
def _apply_match(df: pd.DataFrame, match_doc: dict) -> pd.DataFrame:
    out = df
    for field, cond in match_doc.items():
        if field.startswith("$"):
            # $and / $or 之類的複雜邏輯先跳過
            continue
        if isinstance(cond, dict):
            if "$in" in cond and field in out.columns:
                out = out[out[field].isin(cond["$in"])]
            elif "$eq" in cond and field in out.columns:
                out = out[out[field] == cond["$eq"]]
            elif "$ne" in cond and field in out.columns:
                out = out[out[field] != cond["$ne"]]
        else:
            if field in out.columns:
                out = out[out[field] == cond]
    return out


def _apply_project(df: pd.DataFrame, project_doc: dict) -> pd.DataFrame:
    keep = [k for k, v in project_doc.items() if v in (1, True) and k != "_id"]
    # 處理 alias / rename,例如 "hc": "$hc_info.hc"  → 若已 join 過,直接保留 hc 即可
    keep = [k for k in keep if k in df.columns]
    if not keep:
        return df
    return df[keep].copy()


def execute_pipeline_on_pandas(raw_df: pd.DataFrame, pipeline: list) -> pd.DataFrame:
    """在 fallback 模式下,把 pipeline 的 $match / $project 套用到已 join 的 DataFrame。"""
    out = raw_df.copy()
    for stage in pipeline:
        if "$match" in stage:
            out = _apply_match(out, stage["$match"])
        elif "$project" in stage:
            out = _apply_project(out, stage["$project"])
        else:
            # $lookup / $unwind / 其他 stage 在 fallback 已等同預先 join,直接略過
            continue
    return out


# ============================================================
# 🎨 精美表格渲染:KPI 卡片 + Auto column_config
# ============================================================
def try_recover_Q(ns: dict, raw_df: pd.DataFrame) -> tuple["pd.DataFrame | None", str | None]:
    """
    Phase B 安全網:若 LLM 忘了把聚合結果指派回 Q,試著在 namespace 中找替代品。
    回傳 (替代 DataFrame, 訊息) — 如果不需要替代或找不到,回傳 (None, None)。
    觸發條件:Q 列數 == raw_df 列數 (高度疑似 LLM 沒做最終指派)。
    """
    Q = ns.get("Q")
    if Q is None or not isinstance(Q, pd.DataFrame):
        return None, None
    if Q.shape[0] != raw_df.shape[0]:
        return None, None  # Q 已經聚合過,不需要救援

    # 找出比 raw_df 「更聚合」的候選 DataFrame
    candidates: list[tuple[str, pd.DataFrame]] = []
    for name, val in ns.items():
        if name == "Q" or name.startswith("_") or not isinstance(val, pd.DataFrame):
            continue
        # 列數比 raw_df 少 (聚合過),且至少 1 列
        if 1 <= len(val) < len(raw_df) * 0.9:
            candidates.append((name, val))

    if not candidates:
        return None, None

    # 選列數最少的 (最聚合的) — 通常那就是 LLM 的「最終結果」
    candidates.sort(key=lambda x: len(x[1]))
    name, df = candidates[0]
    msg = (
        f"⚠️ Phase B 安全網觸發:LLM 似乎忘了 `Q = {name}` 終態指派,"
        f"自動 fallback 到 `{name}` (shape={df.shape})。建議重新 prompt 強調終態指派。"
    )
    return df, msg


def render_pretty_table(Q: pd.DataFrame, option: dict | None = None, key_prefix: str = "") -> None:
    """
    取代純 st.dataframe 的進階表格:
    - option 可選帶入 `_kpi_cards` (list of {label, value, delta, help}) → 表格上方顯示 st.metric 卡片
    - option 可選帶入 `_table_caption` → 表格下方 caption
    - 自動將比率欄位 (名含 rate / ratio / 率) 轉為 ProgressColumn (含百分比格式)
    - 整數欄位自動千分位逗號
    - 表格高度依列數動態縮放,最多 800px
    """
    option = option or {}

    # === 1. KPI 卡片區 (頂部) ===
    cards = option.get("_kpi_cards") or []
    if cards:
        n_cols = min(len(cards), 4)
        row_objs = st.columns(n_cols)
        for i, card in enumerate(cards):
            with row_objs[i % n_cols]:
                st.metric(
                    label=str(card.get("label", "—")),
                    value=str(card.get("value", "—")),
                    delta=card.get("delta"),
                    help=card.get("help"),
                )
        st.markdown("")  # 與下方表格留白

    # === 2. 自動 column_config ===
    display_Q = Q.copy()
    column_cfg: dict = {}

    rate_keywords = ("rate", "ratio", "百分", "佔比")
    for col in display_Q.columns:
        s = display_Q[col]
        col_str = str(col)
        col_lower = col_str.lower()
        is_rate = any(k in col_lower for k in rate_keywords) or "率" in col_str

        if not pd.api.types.is_numeric_dtype(s):
            continue

        # 比率欄位:0-1 範圍 → 轉百分比 + ProgressColumn
        s_clean = s.dropna()
        if is_rate and not s_clean.empty and s_clean.max() <= 1.5:
            display_Q[col] = (s * 100).round(2)
            column_cfg[col] = st.column_config.ProgressColumn(
                col_str,
                format="%.1f%%",
                min_value=0,
                max_value=100,
                help="比率欄位 — 進度條長度反映 0-100% 範圍",
            )
        # 整數欄位:千分位逗號
        elif pd.api.types.is_integer_dtype(s) or (s_clean % 1 == 0).all():
            column_cfg[col] = st.column_config.NumberColumn(col_str, format="%,d")
        # 小數欄位:兩位小數
        else:
            column_cfg[col] = st.column_config.NumberColumn(col_str, format="%.2f")

    # === 3. 渲染表格 ===
    n_rows = len(display_Q)
    table_height = min(800, 35 * (n_rows + 1) + 20)
    st.dataframe(
        display_Q,
        use_container_width=True,
        column_config=column_cfg,
        hide_index=True,
        height=table_height,
    )

    # === 4. 表格 caption ===
    if option.get("_table_caption"):
        st.caption(option["_table_caption"])


# ============================================================
# v0.10.0 · Composite chart layout helper
# ============================================================
def _render_q_side_panel(
    Q: "pd.DataFrame | None",
    intent: str = "",
    key_prefix: str = "",
    max_rows: int = 100,
) -> None:
    """側邊欄渲染 Q DataFrame(標準 / 複合模式右側 pane)。

    v0.10.0:標準模式統一渲染。v0.10.1+ 會依 intent 改成 intent-specific
    summary(top-N / 占比 / outlier 等)。
    """
    import pandas as pd  # local import for clarity
    if Q is None or not isinstance(Q, pd.DataFrame) or Q.empty:
        st.caption("📊 (無資料)")
        return

    n_total = len(Q)
    n_shown = min(max_rows, n_total)
    st.caption(f"📊 處理後資料 Q · {n_total:,} 列 × {len(Q.columns)} 欄"
                + (f"(顯示前 {n_shown})" if n_total > max_rows else ""))

    # 數值欄自動千分位逗號顯示;比率類欄位 (rate / ratio / 率) 顯示百分比
    column_config = {}
    for col in Q.columns:
        cl = col.lower()
        if any(k in cl for k in ("rate", "ratio", "_pct", "percentage")) \
                or any(k in col for k in ("率", "占比", "佔比", "比例")):
            column_config[col] = st.column_config.NumberColumn(
                col, format="%.2f%%"
                if Q[col].dropna().abs().max() > 1.5 else "%.2%"
            )
        elif pd.api.types.is_integer_dtype(Q[col]):
            column_config[col] = st.column_config.NumberColumn(col, format="%d")
        elif pd.api.types.is_float_dtype(Q[col]):
            column_config[col] = st.column_config.NumberColumn(col, format="%.2f")

    st.dataframe(
        Q.head(max_rows),
        use_container_width=True,
        hide_index=True,
        column_config=column_config,
        height=min(35 + n_shown * 28, 520),
    )


def render_composite_chart(
    chart_render_fn,
    Q: "pd.DataFrame | None",
    intent: str = "",
    mode: str = "標準",
    key_prefix: str = "",
) -> None:
    """v0.10.0:把 chart 渲染包成 composite layout。

    Args:
        chart_render_fn: 0-arg callable,渲染主圖(st_echarts / st.plotly_chart)
        Q: 處理後資料,供 side panel / composite 用
        intent: chart intent string(供 v0.10.1+ composite mode 決定 layout)
        mode: '精簡' | '標準'(預設)| '複合'
        key_prefix: streamlit key prefix(避免 widget 衝突)

    Modes:
        精簡: 只渲染主圖,全寬度
        標準: 主圖 60% | Q DataFrame 40% 並排
        複合: 同「標準」(v0.10.0 階段);v0.10.1+ 會 intent-driven
    """
    if mode == "精簡" or Q is None or (hasattr(Q, "empty") and Q.empty):
        chart_render_fn()
        return

    # 標準 / 複合:chart 60% + Q side panel 40%
    col_chart, col_side = st.columns([3, 2])
    with col_chart:
        chart_render_fn()
    with col_side:
        _render_q_side_panel(Q, intent=intent, key_prefix=key_prefix)


# ============================================================
# 🚀 系統初始化
# ============================================================
# HR 話圖 · HR ChatChart logo(v0.3.7+)
# SVG 已含 wordmark(中文「話圖」+ 英文「HR ChatChart」),整張當 header
# 想換設計只要改 _LOGO_FILE 檔名:01/02/03/04 任選
_LOGO_FILE = "02_soft_app_icon.svg"  # ← 想換就改這裡
_LOGO_PATH = (
    Path(__file__).parent / "assets" / "hr_chatchart_4_svg_assets" / _LOGO_FILE
)
_LOGO_EXISTS = _LOGO_PATH.exists()


@st.cache_data(show_spinner=False)
def _load_logo_svg_main_only(svg_path: str) -> str:
    """
    讀 HR 話圖 SVG,移除底下的 `icon-only-app-tile` layer(App tile 重複小圖),
    只保留主 logo + wordmark 區。

    用 depth-counter 找匹配的 `<g id="icon-only-app-tile">...</g>`(會嵌套 6 層 g)。
    回傳 **inline SVG 字串**(給 `st.markdown` 用,不能用 `st.image` — Streamlit
    把 bytes 解成 raster 失敗)。失敗時 fallback 回原始檔案內容。
    """
    try:
        src = Path(svg_path).read_text(encoding="utf-8")
        start = src.find('<g id="icon-only-app-tile"')
        if start < 0:
            return src
        # depth counter 找對應的閉合 </g>
        depth = 0
        i = start
        end = -1
        while i < len(src):
            if src[i:i + 3] == "<g ":
                depth += 1
                i += 3
            elif src[i:i + 4] == "</g>":
                depth -= 1
                i += 4
                if depth == 0:
                    end = i
                    break
            else:
                i += 1
        if end < 0:
            return src
        # 把 icon-only-app-tile 區塊整段砍掉
        return src[:start] + src[end:]
    except Exception:
        try:
            return Path(svg_path).read_text(encoding="utf-8")
        except Exception:
            return ""


def _render_inline_svg(svg_text: str, width_px: int) -> None:
    """把 inline SVG 用指定寬度渲染進 Streamlit。"""
    # 把 SVG root 的 width/height 屬性砍掉,改用 wrapper div 控寬
    import re as _re
    svg_text = _re.sub(r'\swidth="[^"]+"', '', svg_text, count=1)
    svg_text = _re.sub(r'\sheight="[^"]+"', '', svg_text, count=1)
    st.markdown(
        f'<div style="width:{width_px}px;max-width:100%">{svg_text}</div>',
        unsafe_allow_html=True,
    )
# 舊 genbi logo 當 fallback(若新 logo 路徑找不到)
_FALLBACK_LOGO = Path(__file__).parent / "assets" / "genbi_logo.svg"

st.set_page_config(
    page_title="HR 話圖 · HR ChatChart",
    page_icon=str(_LOGO_PATH if _LOGO_EXISTS else _FALLBACK_LOGO)
    if (_LOGO_EXISTS or _FALLBACK_LOGO.exists()) else "📊",
    layout="wide",
)

# 整張 SVG 當 header(內含 logo + 「HR 話圖」+「HR ChatChart」雙語 wordmark)
# 處理過的 SVG 已移除底下 App tile 小圖,只留主 logo + wordmark
# 用 inline SVG via st.markdown(`st.image` 對 SVG bytes 會 raster decode 失敗)
if _LOGO_EXISTS:
    _render_inline_svg(_load_logo_svg_main_only(str(_LOGO_PATH)), width_px=480)
elif _FALLBACK_LOGO.exists():
    # 路徑找錯也不破:fallback 到舊 genbi logo + 文字 wordmark
    st.image(str(_FALLBACK_LOGO), width=110)
    st.markdown(
        "<h1 style='font-size:2.4rem;margin:0;line-height:1.1'>"
        "<span style='color:#D71920'>HR</span> "
        "<span style='color:#1F1F1F'>話圖</span> · "
        "<span style='color:#1F1F1F'>HR</span>"
        "<span style='color:#D71920'>ChatChart</span>"
        "</h1>",
        unsafe_allow_html=True,
    )
else:
    st.markdown("# HR 話圖 · HR ChatChart")

# 模型資訊隱藏(若需 debug,改開下行)
# st.caption(f"Powered by `{LLM_MODEL}` via OpenAI-compatible endpoint")

if "messages" not in st.session_state:
    st.session_state.messages = []

# 用於延續性分析:儲存上一次成功(或部分成功)的分析脈絡
if "last_analysis" not in st.session_state:
    st.session_state.last_analysis = None

# v0.4.0:Export Insight → 保留最後一次成功跑完的素材(query / Q / option / fig / insight)
if "last_export_payload" not in st.session_state:
    st.session_state.last_export_payload = None

# 用於 sample question 按鈕注入到 chat input
if "pending_query" not in st.session_state:
    st.session_state.pending_query = None

mongo_db, mongo_err = get_mongo_db()

# ─────────────────────────────────────────────────────────
# v0.3.0+ Prompt / Metadata Repository(共用,所有 LLM call 走這條)
# ─────────────────────────────────────────────────────────
# 1) 啟動時建一次 repo,接 MongoDB(失敗自動 fallback 到 embedded)
import embedded_metadata  # noqa: F401 — auto-merges metadata into EMBEDDED_PROMPTS

if "prompt_repo" not in st.session_state:
    from prompt_repository import build_default_repo
    st.session_state.prompt_repo = build_default_repo(mongo_db=mongo_db)

# 2) 預設 active domain — tflex 永遠優先(v0.3.1+)
#    若 tflex 不存在 → 才退到 list 第一個 → 都沒才 hardcode 'tflex'
if "active_domain" not in st.session_state:
    _available = st.session_state.prompt_repo.list_active_domains()
    if "tflex" in _available:
        st.session_state.active_domain = "tflex"
    elif _available:
        st.session_state.active_domain = _available[0]
    else:
        st.session_state.active_domain = "tflex"

# 3) 用 active domain 建 LLMService。LLMService 內部會接 prompt_repo 走模板讀取
def _build_llm_service_for_domain(domain: str) -> LLMService:
    """為指定 domain 建一個新的 LLMService(切換 domain 時呼叫)。"""
    try:
        task_md = st.session_state.prompt_repo.get_metadata(domain)
    except KeyError:
        # Fallback to default tflex metadata if domain not found
        task_md = None
    return LLMService(
        api_url=LLM_API_URL,
        api_key=LLM_API_KEY,
        model_name=LLM_MODEL,
        timeout_s=LLM_TIMEOUT_S,
        default_temperature=LLM_TEMPERATURE,
        task_metadata=task_md,
        prompt_repo=st.session_state.prompt_repo,
        domain=domain,
    )

if "llm_service" not in st.session_state:
    st.session_state.llm_service = _build_llm_service_for_domain(
        st.session_state.active_domain
    )
llm_service = st.session_state.llm_service

# ============================================================
# 🧭 Sidebar:資料源狀態 + 切換
# ============================================================
with st.sidebar:
    # ─────────────────────────────────────────────────────────
    # 🌐 Active Domain switcher(v0.3.0+)
    # ─────────────────────────────────────────────────────────
    st.markdown("### 🌐 Active Domain")
    _available_domains = (
        st.session_state.prompt_repo.list_active_domains() or [st.session_state.active_domain]
    )
    try:
        _current_idx = _available_domains.index(st.session_state.active_domain)
    except ValueError:
        _current_idx = 0

    _selected_domain = st.selectbox(
        "選擇要分析的 domain",
        options=_available_domains,
        index=_current_idx,
        key="_domain_selector",
        label_visibility="collapsed",
        help="切換 domain 會清空目前對話脈絡並載入該 domain 的 metadata。",
    )

    # 偵測使用者選了不同 domain → 進入 confirm 流程
    if _selected_domain != st.session_state.active_domain:
        st.session_state._pending_domain = _selected_domain

    # Confirm dialog(inline,不用 modal,相容所有 Streamlit 版本)
    _pending = st.session_state.get("_pending_domain")
    if _pending and _pending != st.session_state.active_domain:
        st.warning(
            f"⚠️ 切換到 **{_pending}** 會:\n"
            f"- 清空目前對話脈絡({len(st.session_state.messages)} 則訊息 + 接續分析狀態)\n"
            f"- 重新載入 LLM service 使用 {_pending} 的 schema / KPI 定義"
        )
        _c1, _c2 = st.columns(2)
        if _c1.button("✅ 確認切換", type="primary", use_container_width=True):
            st.session_state.llm_service = _build_llm_service_for_domain(_pending)
            llm_service = st.session_state.llm_service
            st.session_state.active_domain = _pending
            st.session_state.messages = []
            st.session_state.last_analysis = None
            st.session_state.pending_query = None
            del st.session_state._pending_domain
            st.toast(f"🌐 已切換到 {_pending}", icon="✅")
            st.rerun()
        if _c2.button("✖ 取消", use_container_width=True):
            del st.session_state._pending_domain
            st.rerun()
    else:
        # 沒有 pending 時顯示當前 domain 的小摘要
        try:
            _md = st.session_state.prompt_repo.get_metadata(st.session_state.active_domain)
            _name = _md.get("dataset_name") or _md.get("dataset_id") or st.session_state.active_domain
            _n_coll = len((_md.get("collections") or {}))
            _n_kpi = len((_md.get("kpi_definitions") or {}))
            st.caption(f"📦 {_name} · {_n_coll} collections · {_n_kpi} KPIs")
        except Exception:
            st.caption(f"📦 {st.session_state.active_domain}")

    st.divider()

    st.header("🔧 系統狀態")

    st.markdown(f"**LLM ({LLM_PROVIDER})**")
    st.code(
        f"endpoint: {LLM_BASE_URL}\n"
        f"model:    {LLM_MODEL}\n"
        f"timeout:  {LLM_TIMEOUT_S:.0f}s\n"
        f"temp:     {LLM_TEMPERATURE}",
        language="text",
    )

    st.markdown("**MongoDB**")
    if mongo_db is not None:
        st.success(f"✅ Connected — {MONGO_DB_NAME}")
    else:
        st.warning("⚠️ 無法連線,將自動 fallback 到 CSV")
        with st.expander("錯誤詳情"):
            st.code(mongo_err or "(unknown error)", language="text")

    available_sources = ["MongoDB (real)"] if mongo_db is not None else []
    if CSV_APPLICATIONS.exists() and CSV_COMPANY_HC.exists():
        available_sources.append("CSV fallback (dev)")
    if not available_sources:
        available_sources = ["⛔ 無可用資料源"]

    data_source = st.radio(
        "資料來源",
        options=available_sources,
        index=0,
        help="MongoDB 未啟動或未匯入資料時,可選擇 CSV fallback 來開發測試。",
    )

    st.divider()
    chart_engine = st.radio(
        "📊 圖表引擎",
        options=["ECharts", "Plotly"],
        index=0,
        help="ECharts:BI 風格動畫與互動,適合 demo 與管理層匯報。"
             "Plotly:Pythonic、表格類渲染直接 (go.Table)。",
    )

    enable_insight = st.toggle("啟用 Phase D 商業洞察", value=True)

    # v0.10.0:圖表呈現模式 — 預設「標準」(chart + Q DataFrame side panel)
    chart_layout_mode = st.radio(
        "🧩 圖表呈現模式",
        options=["精簡", "標準", "複合"],
        index=1,
        help=(
            "精簡:只顯示主圖。"
            "標準(預設):chart 左 60% + Q DataFrame 右 40%。"
            "複合:依 chart intent 套 BI 風格 layout(top-N / 占比 / outlier 等;v0.10.1+ 漸進加)。"
        ),
    )
    st.session_state["chart_layout_mode"] = chart_layout_mode
    st.divider()

    # 接續分析狀態
    if st.session_state.last_analysis:
        st.markdown("**🔗 延續性分析狀態**")
        la = st.session_state.last_analysis
        st.caption(f"前次:_{(la.get('query') or '')[:40]}_")
        if st.button("🆕 開始新分析(清除延續脈絡)"):
            st.session_state.last_analysis = None
            st.rerun()
    if st.session_state.messages:
        if st.button("🗑️ 清除對話歷史"):
            st.session_state.messages = []
            st.session_state.last_analysis = None
            st.rerun()

    st.divider()
    st.caption("💡 環境變數可調:HRDA_MODEL_BASE_URL / HRDA_MODEL_NAME / MONGO_URI …")

# ============================================================
# v0.9.3: 把上次 session 的 in_progress message 標 interrupted
# (page navigation 中斷 / 刷新頁面 後,讓 user 知道發生什麼)
# ============================================================
for _m in st.session_state.messages:
    if _m.get("role") == "assistant" and _m.get("in_progress"):
        _m["in_progress"] = False
        _m["interrupted"] = True


def _render_phases_done(phases: dict, interrupted: bool = False) -> None:
    """v0.9.3:從 message['phases_done'] 重渲已完成 phase 的 expander。

    Live execution path 也會即時 inline 渲染這些,但 page navigation 後
    inline 沒了 — 這裡從 session_state 重渲。
    """
    if interrupted:
        st.warning(
            "⚠️ 上次執行被中斷(可能因切換頁面或 reload),僅顯示已完成階段。"
            "重新提交相同問題即可繼續。"
        )
    if "plan" in phases:
        with st.expander("📋 檢視 AI 執行計畫", expanded=False):
            st.markdown(phases["plan"].get("text", ""))
    if "pipeline" in phases:
        p = phases["pipeline"]
        if p.get("summary"):
            st.markdown(p["summary"])
        start_coll = p.get("start_collection", "?")
        with st.expander(f"🛠️ 檢視 MongoDB Pipeline (起點: {start_coll})",
                         expanded=False):
            st.code(p.get("json", "(missing)"), language="json")
        if p.get("raw_df_head") is not None:
            n = p.get("n_rows", 0)
            with st.expander(f"📄 檢視原始資料前 100 筆 ({n:,} 筆中)",
                             expanded=False):
                try:
                    st.dataframe(p["raw_df_head"], use_container_width=True)
                except Exception:
                    pass
    if "preprocess" in phases:
        pp = phases["preprocess"]
        with st.expander("🐍 Phase B 處理程式碼", expanded=False):
            st.code(pp.get("code", "(missing)"), language="python")
        if pp.get("q_info"):
            st.caption(pp["q_info"])
        if pp.get("q_head") is not None:
            with st.expander(f"📊 Q 前 {pp.get('q_head_rows', 5)} 列",
                             expanded=False):
                try:
                    st.dataframe(pp["q_head"], use_container_width=True)
                except Exception:
                    pass
    if "echarts_code" in phases:
        with st.expander("🎨 檢視 ECharts 繪圖腳本", expanded=False):
            st.code(phases["echarts_code"], language="python")


# ============================================================
# 💬 歷史訊息渲染
# ============================================================
for idx, msg in enumerate(st.session_state.messages):
    with st.chat_message(msg["role"]):
        # v0.9.3:in_progress / 完成後的 message 都有 phases_done — 重渲已完成階段
        if msg.get("phases_done"):
            _render_phases_done(
                msg["phases_done"],
                interrupted=msg.get("interrupted", False),
            )
        st.write(msg["content"])
        # 圖表回放 — Plotly fig 或 ECharts option dict 二擇一
        # v0.10.0:支援 composite layout(chart + Q side panel),
        # 走目前 sidebar 切換的 mode(切 mode 時 history 也跟著重渲)
        _hist_mode = st.session_state.get("chart_layout_mode", "標準")
        _hist_intent = msg.get("chart_intent", "")
        _hist_Q = msg.get("q_for_composite")

        if msg.get("fig") is not None:
            def _hist_render_fig():
                st.plotly_chart(msg["fig"], use_container_width=True)
            render_composite_chart(
                _hist_render_fig, _hist_Q,
                intent=_hist_intent, mode=_hist_mode,
                key_prefix=f"hist_{idx}",
            )
        elif msg.get("echarts_option") is not None:
            def _hist_render_echarts():
                st_echarts(
                    options=msg["echarts_option"],
                    height="520px",
                    key=f"echarts_history_{idx}",
                )
            render_composite_chart(
                _hist_render_echarts, _hist_Q,
                intent=_hist_intent, mode=_hist_mode,
                key_prefix=f"hist_{idx}",
            )
        elif msg.get("table_df") is not None:
            # table fallback 不加 side panel
            render_pretty_table(
                msg["table_df"],
                msg.get("table_option"),
                key_prefix=f"hist_{idx}",
            )

        if msg.get("insight"):
            with st.expander("🧠 商業洞察", expanded=False):
                st.markdown(msg["insight"])

# ============================================================
# 🚀 核心執行引擎 (Agentic Workflow)
# ============================================================
# 極簡開場 — 不顯示預設範例 / 按鈕,引導資訊在使用者主動問時才出現
chat_input_value = st.chat_input(
    "輸入你想分析的問題;若不確定可問「你會做什麼?」「有什麼資料?」「怎麼開始?」"
)
# pending_query 機制保留(供未來功能注入查詢使用,例如 follow-up 建議按鈕)
query = chat_input_value or st.session_state.pending_query
if st.session_state.pending_query:
    st.session_state.pending_query = None  # consume

if query:
    st.session_state.messages.append({"role": "user", "content": query})
    with st.chat_message("user"):
        st.write(query)

    # v0.9.3:預先 append in_progress assistant slot,讓 page navigation
    # 中途中斷時,session_state.messages 仍保有已完成階段的 snapshot,
    # 切回 app page 時 history loop 從 session_state 重渲。
    st.session_state.messages.append({
        "role": "assistant",
        "content": "🧠 分析進行中...",
        "in_progress": True,
        "phases_done": {},
    })

    # ============================================================
    # 🎯 Pre-Phase 0 · Intent Router
    # 偵測非分析類查詢(intro / data_overview / data_check / guidance / greeting)
    # 直接回應 meta response,不走 Phase 0/A/B/C/D
    # ============================================================
    # 🔧 routing 優先序:explicit intent → follow-up → out_of_scope → analysis
    intent_result = llm_service.classify_intent_for_query(
        query, last_analysis=st.session_state.last_analysis
    )
    intent = intent_result.get("intent", "analysis")

    if intent != "analysis":
        with st.chat_message("assistant"):
            # out_of_scope 與 data_check 都需要 query 內容做 subject 萃取
            meta_md = llm_service.generate_meta_response(
                intent,
                subject=intent_result.get("subject", ""),
                query=query,
            )
            st.markdown(meta_md)
            st.session_state.messages.append({
                "role": "assistant",
                "content": meta_md,
                "meta_intent": intent,
            })
        st.stop()  # 不進入分析 pipeline

    # ============================================================
    # 🔗 Pre-Phase 0 · Follow-up flag(由 classifier 提供)
    # 延續性分析會在 Phase 0 注入前次脈絡
    # ============================================================
    is_followup = intent_result.get("is_followup", False)
    followup_context = st.session_state.last_analysis if is_followup else None

    with st.chat_message("assistant"):
        # 📌 Current Question 醒目橫條 — 釘在 assistant response 頂端,
        #    避免長 workflow 跑下來,使用者捲動時看不到當下分析的是什麼
        _followup_tag = (
            "<span style='background:#D9342B;color:#FFFFFF;font-size:0.7rem;"
            "padding:2px 8px;border-radius:10px;margin-left:8px;font-weight:500'>"
            "🔗 接續分析"
            "</span>"
            if is_followup else ""
        )
        _domain_tag = (
            f"<span style='background:#8B2C2E;color:#FFF7E8;font-size:0.7rem;"
            f"padding:2px 8px;border-radius:10px;margin-left:8px;font-weight:500'>"
            f"🌐 {st.session_state.active_domain}"
            f"</span>"
        )
        st.markdown(
            f"""<div style='background:#FFF7E8;border-left:4px solid #D9342B;
                           padding:12px 18px;border-radius:6px;margin-bottom:14px;'>
                  <div style='font-size:0.78rem;color:#8B6F4A;font-weight:600;
                              letter-spacing:0.5px;text-transform:uppercase;'>
                    🍳 Current question{_domain_tag}{_followup_tag}
                  </div>
                  <div style='font-size:1.08rem;color:#2A1810;margin-top:4px;
                              line-height:1.5;'>{query}</div>
                </div>""",
            unsafe_allow_html=True,
        )
        if is_followup:
            st.caption(
                "🔗 偵測為延續性分析 — 已帶入前次 Q 欄位、圖表類型、計畫摘要等脈絡到 Phase 0。"
                "若要開新分析,請按左側 sidebar 的「🆕 開始新分析」。"
            )
        status = st.status(
            f"🧠 處理中:{query[:60] + ('…' if len(query) > 60 else '')}",
            expanded=True,
        )
        # v0.7.0:Task trace 開始記錄(LLMService.trace 內 hook 會自動記 LLM call)
        _trace = TaskTrace(
            db=mongo_db,
            domain=st.session_state.get("active_domain", ""),
            query=query,
            collection_name=config.TASK_TRACES_COLLECTION,
        )
        llm_service.trace = _trace
        # 先記 intent(detector 是即時的,跟 query 在同個 frame)
        try:
            _trace.set_chart_intent(_detect_chart_intent(query))
            _trace.set_preprocess_intent(_detect_preprocess_intent(
                query, dashboard_hint=is_dashboard_query(query),
                metadata=llm_service.task_metadata,
            ))
        except Exception:
            pass

        workflow_namespace = {"pd": pd, "np": __import__("numpy")}
        final_fig = None
        insight_text = None

        try:
            # ============================================================
            # Phase 0 — 制定分析計畫
            # ============================================================
            status.update(label="📋 Phase 0:制定分析計畫..." +
                          (" (含接續脈絡)" if followup_context else ""))
            plan_res = llm_service.generate_plan(query, followup_context=followup_context)
            if plan_res["status"] == "error":
                raise Exception(plan_res["message"])
            plan_text = plan_res["message"]

            with st.expander("📋 檢視 AI 執行計畫", expanded=False):
                st.markdown(plan_text)

            # v0.9.3:snapshot 進 session_state.messages 供 navigation 後重渲
            st.session_state.messages[-1]["phases_done"]["plan"] = {
                "text": plan_text,
            }

            # 🛑 拒絕短路:Plan 若標示 [REFUSE] 或明確拒絕,直接呈現結果並中止
            plan_head = plan_text.strip()[:400]
            is_refusal = (
                plan_head.startswith("[REFUSE]")
                or "[REFUSE]" in plan_head
                or any(kw in plan_head for kw in (
                    "無法執行", "無法分析", "無法計算", "無法進行",
                    "不支援此分析", "資料限制觸犯",
                ))
            )

            if is_refusal:
                status.update(label="🛑 偵測到 data_limitations,中止分析",
                              state="error", expanded=False)
                clean_msg = plan_text.replace("[REFUSE]", "").strip()
                st.warning(
                    "⚠️ **資料不足** — 此分析觸犯 metadata 中的 data_limitations,"
                    "系統不執行 Phase A/B/C/D。"
                )
                st.markdown(clean_msg)
                # v0.9.3:REPLACE in_progress slot(不要 append 第 2 條 assistant)
                st.session_state.messages[-1] = {
                    "role": "assistant",
                    "content": f"⚠️ 資料不足\n\n{clean_msg}",
                    "in_progress": False,
                    "phases_done": st.session_state.messages[-1].get(
                        "phases_done", {}),
                }
                # v0.7.0:refuse 也算結束 → finalize trace
                try:
                    _trace.finalize(status="refused")
                except Exception:
                    pass
                finally:
                    llm_service.trace = None
                st.stop()  # 中止後續 phase

            # ============================================================
            # Phase A — MongoDB pipeline → 撈資料
            # ============================================================
            status.update(label="🛢️ Phase A:產生 MongoDB pipeline 並撈資料...")
            db_json_str = llm_service.generate_pipeline(query, plan_text)

            try:
                db_instruction = json.loads(db_json_str)
            except json.JSONDecodeError:
                raise ValueError(f"LLM 未能回傳合法的 JSON 格式:\n{db_json_str}")

            start_collection = db_instruction.get("start_collection")
            pipeline, _sanitize_warnings = sanitize_pipeline(db_instruction.get("pipeline", []))
            for _w in _sanitize_warnings:
                st.toast(_w, icon="🧹")

            with st.expander(f"🛠️ 檢視 MongoDB Pipeline (起點: {start_collection})", expanded=False):
                st.code(json.dumps({"start_collection": start_collection, "pipeline": pipeline},
                                   indent=2, ensure_ascii=False), language="json")

            # === 依資料來源實際撈取 ===
            if data_source.startswith("MongoDB") and mongo_db is not None:
                cursor = mongo_db[start_collection].aggregate(pipeline)
                raw_df = pd.DataFrame(list(cursor))
                source_label = f"MongoDB ({MONGO_DB_NAME}.{start_collection})"
            elif data_source.startswith("CSV"):
                merged = load_csv_fallback()
                raw_df = execute_pipeline_on_pandas(merged, pipeline)
                source_label = "CSV fallback (本機 pandas)"
            else:
                raise RuntimeError("沒有可用的資料源,請啟動 MongoDB 或確認 CSV 路徑。")

            if "_id" in raw_df.columns:
                raw_df = raw_df.drop(columns=["_id"])
            if raw_df.empty:
                raise ValueError("Phase A 撈取結果為空,請檢查 pipeline 的 $match 條件。")

            workflow_namespace["raw_df"] = raw_df
            _phase_a_summary = (
                f"📥 **Phase A 完成** · 來源:{source_label} · "
                f"撈出 {len(raw_df):,} 筆明細 · 欄位:{list(raw_df.columns)}"
            )
            st.markdown(_phase_a_summary)
            with st.expander(f"📄 檢視原始資料前 100 筆 ({len(raw_df):,} 筆中)", expanded=False):
                st.dataframe(raw_df.head(100), use_container_width=True)

            # v0.9.3:Phase A snapshot
            st.session_state.messages[-1]["phases_done"]["pipeline"] = {
                "start_collection": start_collection,
                "json": json.dumps(
                    {"start_collection": start_collection, "pipeline": pipeline},
                    indent=2, ensure_ascii=False,
                ),
                "summary": _phase_a_summary,
                "n_rows": len(raw_df),
                "raw_df_head": raw_df.head(100).copy(),
            }

            # ============================================================
            # Phase B — Pandas 處理 (帶錯誤回饋自我修正)
            # ============================================================
            status.update(label="🐍 Phase B:Pandas 計算與 KPI 處理...")
            avail_cols = list(raw_df.columns)
            try:
                raw_df_sample_md = raw_df.head(3).to_markdown(index=False)
            except Exception:
                raw_df_sample_md = raw_df.head(3).to_string(index=False)

            # 🎯 結構性路由:偵測 dashboard 場景 → 走 row-level pass-through
            dashboard_mode = is_dashboard_query(query)
            if dashboard_mode:
                st.info("📊 偵測為 dashboard 查詢,Phase B 走 row-level pass-through(scalar 由 Phase C 處理)")

            prep_code = None
            prep_err = None

            for attempt in range(3):
                prep_code = llm_service.generate_preprocess_code(
                    query, plan_text, avail_cols,
                    raw_df_sample=raw_df_sample_md,
                    dashboard_hint=dashboard_mode,
                    previous_code=prep_code if attempt > 0 else "",
                    previous_error=prep_err if attempt > 0 else "",
                )
                try:
                    exec(prep_code, workflow_namespace, workflow_namespace)
                    if "Q" not in workflow_namespace:
                        raise ValueError("LLM 未在最外層宣告變數 Q!")
                    # 🛡️ Phase B Series 救援(v0.3.6+):
                    #   若 LLM 不小心讓 Q 變成 Series(例如 Q = raw_df.groupby(...).size() 沒 reset_index),
                    #   自動 to_frame() 轉回 DataFrame,避免 'Series has no attribute columns' 等下游崩潰
                    _Q_obj = workflow_namespace["Q"]
                    if isinstance(_Q_obj, pd.Series):
                        st.warning(
                            f"⚠️ Phase B 安全網:Q 是 Series(name={_Q_obj.name!r}),"
                            f"自動 to_frame() 轉回 DataFrame。建議 prompt 提醒 `reset_index()`。"
                        )
                        workflow_namespace["Q"] = _Q_obj.to_frame().reset_index()
                    # 🛡️ Phase B 安全網:救援忘記終態指派的情況
                    fallback_df, recover_msg = try_recover_Q(workflow_namespace, raw_df)
                    if recover_msg:
                        st.warning(recover_msg)
                        workflow_namespace["Q"] = fallback_df
                    with st.expander("🐍 檢視 Python 資料處理腳本", expanded=False):
                        st.code(prep_code, language="python")
                    break
                except Exception:
                    prep_err = traceback.format_exc()
                    if attempt < 2:  # 還有重試機會
                        st.toast(
                            f"⚠️ Phase B 第 {attempt + 1} 次失敗,帶錯誤回饋 + anti-pattern 速查表重生...",
                            icon="🔄",
                        )
                    else:
                        st.error("❌ **Phase B 連續失敗 3 次**")
                        st.info("👇 LLM 最後一版腳本:")
                        st.code(prep_code, language="python")
                        with st.expander("🔍 展開 Traceback"):
                            st.code(prep_err, language="bash")
                        # v0.9.3:REPLACE in_progress slot,標 phase_b 失敗
                        if st.session_state.messages and \
                                st.session_state.messages[-1].get("in_progress"):
                            _prev = st.session_state.messages[-1]
                            st.session_state.messages[-1] = {
                                "role": "assistant",
                                "content": "❌ Phase B 連續失敗 3 次 — 請調整 query 或 metadata",
                                "in_progress": False,
                                "phases_done": _prev.get("phases_done", {}),
                                "error": "Phase B retry exhausted",
                            }
                        # v0.7.0:finalize trace 在中止前
                        try:
                            _trace.finalize(status="failed",
                                             error="Phase B retry exhausted")
                        except Exception:
                            pass
                        finally:
                            llm_service.trace = None
                        st.stop()

            Q = workflow_namespace.get("Q")
            if Q is None or (hasattr(Q, "empty") and Q.empty):
                raise ValueError("Phase B 處理後 Q 為空,請檢查篩選條件。")

            st.markdown(f"⚙️ **Phase B 完成** · KPI 已計算 (共 {len(Q):,} 筆)")
            with st.expander(f"📊 檢視處理後資料前 100 筆 (共 {len(Q):,} 筆)", expanded=False):
                st.dataframe(Q.head(100), use_container_width=True)

            # v0.9.3:Phase B snapshot
            _q_head = Q.head(100).copy() if hasattr(Q, 'head') else None
            st.session_state.messages[-1]["phases_done"]["preprocess"] = {
                "code": prep_code or "",
                "q_info": f"Q.shape={getattr(Q, 'shape', '?')} · cols={list(getattr(Q, 'columns', []))}",
                "q_head": _q_head,
                "q_head_rows": min(100, len(Q) if hasattr(Q, '__len__') else 0),
            }

            # ============================================================
            # Phase C — 視覺化 (引擎依 sidebar 切換 / 帶錯誤回饋自我修正)
            # ============================================================
            status.update(label=f"🎨 Phase C:{chart_engine} 繪圖中...")
            q_cols = list(Q.columns)
            plot_code = None
            plot_err = None
            final_fig = None
            final_option = None
            use_table_fallback = False

            for attempt in range(3):
                if chart_engine == "ECharts":
                    plot_code = llm_service.generate_echarts_option(
                        query, plan_text, q_cols,
                        previous_code=plot_code if attempt > 0 else "",
                        previous_error=plot_err if attempt > 0 else "",
                    )
                else:
                    plot_code = llm_service.generate_plot_code(
                        query, plan_text, q_cols,
                        previous_code=plot_code if attempt > 0 else "",
                        previous_error=plot_err if attempt > 0 else "",
                    )
                    # 🛡️ 物理防護罩:消滅 textfont 幻覺
                    if "go.Table" in plot_code and "textfont" in plot_code:
                        plot_code = plot_code.replace("textfont", "font")

                try:
                    exec(plot_code, workflow_namespace, workflow_namespace)
                    if chart_engine == "ECharts":
                        final_option = workflow_namespace.get("option")
                        if not isinstance(final_option, dict):
                            raise ValueError("執行腳本後,未產生 dict 型別的 `option`。")
                        # 空殼救援:LLM 偶爾產 series=[] / xAxis.data=[] 的空 option
                        final_option, _rescued = rescue_empty_echarts(final_option, Q)
                        if _rescued:
                            st.toast("🛟 偵測到 Phase C 產出空殼,已自動 pivot 補回 series", icon="🔧")
                        # 預設樣式補強:多 series 無 legend 時自動補上
                        final_option, _styled = ensure_default_styling(final_option, query)
                        if _styled:
                            st.toast("🎨 自動補上預設 legend(若想關閉,query 加「精簡」即可)", icon="🎨")
                        # v0.4.6:numpy/pandas scalar 強制 cast 成 Python native,
                        # 避免 streamlit-echarts BidiComponent serializer 把 numpy.int64
                        # 序列化成 null → JS Object.keys(null) 炸
                        final_option = coerce_option_native_types(final_option)
                        # 表格 fallback 旗標
                        use_table_fallback = bool(final_option.get("_use_table"))
                        # 基本健全性:非表格情境必須有 series
                        if not use_table_fallback and "series" not in final_option:
                            raise ValueError("ECharts option 缺少必備 key `series`。")
                    else:
                        final_fig = workflow_namespace.get("fig")
                        if not final_fig:
                            raise ValueError("執行腳本後,未產生 `fig` 物件。")

                    with st.expander(f"🎨 檢視 {chart_engine} 繪圖腳本", expanded=False):
                        st.code(plot_code, language="python")

                    # v0.10.4 Level 2:exec OK 後跑 semantic validator
                    # 只對 ECharts + 非 table fallback 跑(plotly fig 跟 table 不適用)
                    if chart_engine == "ECharts" and not use_table_fallback:
                        try:
                            from phase_c_validator import (
                                validate_phase_c_output, format_issues_as_retry_hint,
                            )
                            _intent_for_val = ""
                            try:
                                _intent_for_val = _detect_chart_intent(query)
                            except Exception:
                                pass
                            semantic_issues = validate_phase_c_output(
                                final_option, Q, query=query, intent=_intent_for_val,
                            )
                        except Exception as _val_e:
                            semantic_issues = []
                            st.toast(f"⚠️ semantic validator crashed: {_val_e}",
                                      icon="⚠️")

                        if semantic_issues and attempt < 2:
                            plot_err = format_issues_as_retry_hint(semantic_issues)
                            short_summary = "; ".join(
                                i.split(']')[0].lstrip('[') for i in semantic_issues
                            )[:120]
                            st.toast(
                                f"🔍 semantic check 失敗 ({short_summary}),"
                                f"進入第 {attempt + 2} 次重生",
                                icon="🔁",
                            )
                            continue  # 進下一輪 attempt
                        elif semantic_issues:
                            st.toast(
                                f"⚠️ semantic check 3 次都失敗,接受結果",
                                icon="⚠️",
                            )

                    break
                except Exception:
                    plot_err = traceback.format_exc()
                    # 🛟 v0.4.7:exec 失敗時也試著從半殘 namespace 救空殼
                    # 場景:LLM 寫 `option = {空殼}` 然後接著 Phase B 該做的事,
                    # 後段 KeyError → exec raise,但 option 已在 namespace 中
                    if chart_engine == "ECharts":
                        _partial = workflow_namespace.get("option")
                        if isinstance(_partial, dict):
                            _partial, _rescued = rescue_empty_echarts(_partial, Q)
                            if _rescued:
                                _partial, _ = ensure_default_styling(_partial, query)
                                _partial = coerce_option_native_types(_partial)
                                final_option = _partial
                                use_table_fallback = bool(final_option.get("_use_table"))
                                st.toast(
                                    f"🛟 Phase C 第 {attempt + 1} 次 exec 失敗,但從半殘空殼救回 ({chart_engine})",
                                    icon="🔧",
                                )
                                with st.expander(f"🎨 檢視 {chart_engine} 繪圖腳本(失敗版本 + 結構救援)", expanded=False):
                                    st.code(plot_code, language="python")
                                    st.caption("⚠️ 上面 code exec 失敗(下面 traceback),但 option 殼有救回 → 用 Q 自動 pivot")
                                    st.code(plot_err, language="bash")
                                break
                    if attempt < 2:
                        st.toast(
                            f"⚠️ Phase C ({chart_engine}) 第 {attempt + 1} 次失敗,帶錯誤回饋重生...",
                            icon="🔄",
                        )
                    else:
                        # 🛡️ 3 次都失敗 — 結構性 fallback:渲染表格而非 st.stop()
                        st.warning(
                            f"⚠️ Phase C ({chart_engine}) 連續 3 次失敗,自動降級為表格渲染 "
                            f"(`render_pretty_table`),你仍可看到 Q 的內容。"
                        )
                        with st.expander("👇 展開最後一版失敗腳本與 traceback", expanded=False):
                            st.code(plot_code, language="python")
                            st.code(plot_err, language="bash")
                        # 設定 fallback 狀態 — 後面渲染區塊會走 use_table_fallback 分支
                        use_table_fallback = True
                        final_option = {"_use_table": True, "_phase_c_fallback": True}
                        final_fig = None

            status.update(label="🖼️ Phase C 完成,繪圖呈現中...")
            _phase_c_note = (
                "降級為表格" if use_table_fallback
                else f"引擎:{chart_engine}"
            )
            st.markdown(f"🖼️ **Phase C 完成** · {_phase_c_note}")

            # v0.9.3:Phase C echarts code snapshot(option dict 會在最終 message
            # 透過 echarts_option/table_df 渲染,所以不另外存到 phases_done)
            if plot_code:
                st.session_state.messages[-1]["phases_done"]["echarts_code"] = plot_code

            # v0.10.0:走 composite layout(依 sidebar 切換的 mode)
            _layout_mode = st.session_state.get("chart_layout_mode", "標準")
            _live_key = f"live_{len(st.session_state.messages)}"
            _live_chart_intent = ""
            try:
                _live_chart_intent = _detect_chart_intent(query)
            except Exception:
                pass

            def _render_main_chart():
                if chart_engine == "ECharts":
                    if use_table_fallback:
                        st.info("📋 LLM 判斷此查詢更適合用表格呈現,套用精美 KPI 表格樣式。")
                        render_pretty_table(Q, final_option, key_prefix=_live_key)
                    else:
                        st_echarts(
                            options=final_option,
                            height="520px",
                            key=f"echarts_{_live_key}",
                        )
                else:
                    st.plotly_chart(final_fig, use_container_width=True)

            if use_table_fallback:
                # table fallback 本身就是 table,不再加 side panel
                _render_main_chart()
            else:
                render_composite_chart(
                    _render_main_chart, Q,
                    intent=_live_chart_intent,
                    mode=_layout_mode,
                    key_prefix=_live_key,
                )

            # ============================================================
            # Phase D — 商業洞察 (可選)
            # ============================================================
            if enable_insight:
                status.update(label="🧠 Phase D:產生商業洞察...")
                # 給 LLM 一個 markdown 預覽,避免 prompt 過長
                try:
                    q_preview_md = Q.head(30).to_markdown(index=False)
                except Exception:
                    q_preview_md = Q.head(30).to_string(index=False)

                insight_res = llm_service.generate_insight(query, plan_text, q_preview_md)
                if insight_res["status"] == "success":
                    insight_text = insight_res["message"]
                    with st.expander("🧠 商業洞察", expanded=True):
                        st.markdown(insight_text)
                else:
                    st.warning(f"Phase D 失敗 (不影響主流程):{insight_res['message']}")

            # ============================================================
            # 🎉 最終呈現
            # ============================================================
            status.update(label="✅ 分析完成", state="complete", expanded=False)

            # v0.9.3:REPLACE in_progress slot,不要 append 第 2 條 assistant。
            # 保留 phases_done 讓 navigation 後重渲。
            # v0.10.0:也存 q_for_composite + chart_intent 給 history loop 重渲 composite。
            _prev_phases = st.session_state.messages[-1].get("phases_done", {})
            st.session_state.messages[-1] = {
                "role": "assistant",
                "content": "分析已完成,如上方資料、圖表與洞察所示。",
                "fig": final_fig,
                "echarts_option": None if use_table_fallback else final_option,
                "table_df": Q if use_table_fallback else None,
                "table_option": final_option if use_table_fallback else None,
                "insight": insight_text,
                "in_progress": False,
                "phases_done": _prev_phases,
                # v0.10.0: composite layout replay
                "q_for_composite": Q.copy() if isinstance(Q, pd.DataFrame) else None,
                "chart_intent": _live_chart_intent,
            }

            # 🔗 寫入「上次分析脈絡」供下一輪 follow-up 使用
            if use_table_fallback:
                chart_descriptor = f"{chart_engine} table fallback"
            elif chart_engine == "ECharts" and isinstance(final_option, dict):
                series_types = [s.get("type", "?") for s in final_option.get("series", [])]
                chart_descriptor = f"ECharts ({'/'.join(series_types) or 'unknown'})"
            elif chart_engine == "Plotly":
                chart_descriptor = "Plotly chart"
            else:
                chart_descriptor = chart_engine

            st.session_state.last_analysis = {
                "query": query,
                "plan_summary": plan_text[:400],
                "Q_cols": list(Q.columns) if Q is not None else [],
                "chart_engine": chart_engine,
                "chart_descriptor": chart_descriptor,
                "is_dashboard": dashboard_mode,
                "was_followup": is_followup,
            }

            # ============================================================
            # 📤 v0.4.0 · Export Insight payload(保留素材,讓使用者按鈕觸發下載)
            # 注意:實際的 Export / Download button 統一在 script 尾端 render
            # (見檔尾「📤 Export Insight 區塊」),確保 button click rerun 後仍能進到 handler。
            # ============================================================
            st.session_state.last_export_payload = {
                "query": query,
                "plan_text": plan_text,
                "Q": Q.copy() if isinstance(Q, pd.DataFrame) else Q,
                "final_option": final_option,
                "final_fig": final_fig,
                "insight_text": insight_text,
                "chart_engine": chart_engine,
                "source_label": source_label,
                "domain": st.session_state.get("active_domain", ""),
                "use_table_fallback": use_table_fallback,
            }

            # v0.7.0:任務成功 → finalize trace 寫進 MongoDB
            try:
                _trace_id = _trace.finalize(status="completed")
                st.caption(f"🔍 Trace 已記錄(在「Task Traces」頁面查看)· `{_trace_id[:8]}`")
            except Exception:
                pass
            finally:
                llm_service.trace = None  # detach,避免下個 query 沾到舊 trace

        except Exception as e:
            status.update(label="❌ 系統執行中斷", state="error", expanded=True)
            st.error(f"發生系統級錯誤:\n{str(e)}")
            with st.expander("🔍 展開 Traceback"):
                st.code(traceback.format_exc(), language="bash")
            # v0.9.3:REPLACE in_progress slot,標 error,保留 phases_done
            if st.session_state.messages and \
                    st.session_state.messages[-1].get("in_progress"):
                _prev = st.session_state.messages[-1]
                st.session_state.messages[-1] = {
                    "role": "assistant",
                    "content": f"❌ 系統執行中斷:{str(e)[:200]}",
                    "in_progress": False,
                    "phases_done": _prev.get("phases_done", {}),
                    "error": str(e),
                }
            # v0.7.0:任務失敗 → 仍寫 trace(便於除錯)
            try:
                _trace.finalize(status="failed", error=str(e))
            except Exception:
                pass
            finally:
                llm_service.trace = None


# ============================================================
# 📤 v0.4.0+ · Export Insight 區塊(script 尾端統一渲染)
# 重要:必須放在所有 if 分支【之外】,任何 rerun(送訊息 / 按 button)都會走到這裡。
# ============================================================
_payload = st.session_state.get("last_export_payload")
_has_payload = bool(_payload)

if st.session_state.messages:
    st.divider()
    _cols = st.columns([1.2, 1.2, 3.6])

    with _cols[0]:
        _gen_clicked = st.button(
            "📤 Export Insight → PPTX",
            help=("將最近一次分析的圖表 + 商業洞察打包成一頁 .pptx 報告"
                  if _has_payload else
                  "請先跑一次完整分析(Phase A→D 全部成功),按鈕才會啟用"),
            key="export_insight_btn",
            disabled=not _has_payload,
            use_container_width=True,
        )
        if _gen_clicked and _has_payload:
            try:
                from export_pptx import build_report_pptx
                with st.spinner("📦 正在生成 PPTX 報告..."):
                    _pptx_bytes = build_report_pptx(
                        query=_payload["query"],
                        plan_text=_payload.get("plan_text", ""),
                        Q=_payload["Q"],
                        final_option=_payload.get("final_option"),
                        final_fig=_payload.get("final_fig"),
                        insight_text=_payload.get("insight_text"),
                        chart_engine=_payload.get("chart_engine", "ECharts"),
                        source_label=_payload.get("source_label", ""),
                        domain=_payload.get("domain", ""),
                        use_table_fallback=_payload.get("use_table_fallback", False),
                    )
                st.session_state._pptx_bytes = _pptx_bytes
                st.session_state._pptx_filename = (
                    f"HR_ChatChart_{datetime.now(timezone.utc).astimezone().strftime('%Y%m%d_%H%M%S')}.pptx"
                )
                st.toast("✅ PPTX 已備好,按右邊 ⬇️ 下載", icon="📤")
                st.rerun()  # 讓 download button 變 enabled
            except Exception as _exc:
                st.error(f"❌ 生成 PPTX 失敗:{type(_exc).__name__}: {_exc}")
                with st.expander("🔍 展開 Traceback"):
                    st.code(traceback.format_exc(), language="bash")

    with _cols[1]:
        if st.session_state.get("_pptx_bytes"):
            st.download_button(
                "⬇️ Download PPTX",
                data=st.session_state._pptx_bytes,
                file_name=st.session_state.get("_pptx_filename",
                                                "HR_ChatChart_report.pptx"),
                mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                key="export_insight_download",
                use_container_width=True,
            )
        else:
            st.button(
                "⬇️ Download PPTX",
                disabled=True,
                help="先按左邊 📤 Export Insight 生成 PPTX,這顆按鈕才會啟用",
                key="export_insight_download_disabled",
                use_container_width=True,
            )

    with _cols[2]:
        if not _has_payload:
            st.caption(
                "💡 跑完一次成功的分析(Phase A→D 全綠)後,左邊按鈕會啟用,可匯出單頁 PPTX 報告。"
            )
        else:
            st.caption(
                f"📊 已備好上次分析:**{(_payload.get('query') or '')[:40]}…** "
                f"({_payload.get('chart_engine', '?')},領域:{_payload.get('domain', '?')})"
            )
