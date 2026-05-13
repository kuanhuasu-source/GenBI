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
)
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
# 🚀 系統初始化
# ============================================================
st.set_page_config(page_title="tFlex GenBI", page_icon="📊", layout="wide")
st.title("📊 tFlex 員工福利申請 GenBI 系統")
st.markdown(f"**Powered by `{LLM_MODEL}` via OpenAI-compatible endpoint**")

if "messages" not in st.session_state:
    st.session_state.messages = []

# 用於延續性分析:儲存上一次成功(或部分成功)的分析脈絡
if "last_analysis" not in st.session_state:
    st.session_state.last_analysis = None

# 用於 sample question 按鈕注入到 chat input
if "pending_query" not in st.session_state:
    st.session_state.pending_query = None

# LLMService 用 session_state 快取避免重複建立 OpenAI client
if "llm_service" not in st.session_state:
    st.session_state.llm_service = LLMService(
        api_url=LLM_API_URL,
        api_key=LLM_API_KEY,
        model_name=LLM_MODEL,
        timeout_s=LLM_TIMEOUT_S,
        default_temperature=LLM_TEMPERATURE,
    )
llm_service = st.session_state.llm_service

mongo_db, mongo_err = get_mongo_db()

# ============================================================
# 🧭 Sidebar:資料源狀態 + 切換
# ============================================================
with st.sidebar:
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
# 💬 歷史訊息渲染
# ============================================================
for idx, msg in enumerate(st.session_state.messages):
    with st.chat_message(msg["role"]):
        st.write(msg["content"])
        # 圖表回放 — Plotly fig 或 ECharts option dict 二擇一
        if msg.get("fig") is not None:
            st.plotly_chart(msg["fig"], use_container_width=True)
        elif msg.get("echarts_option") is not None:
            st_echarts(
                options=msg["echarts_option"],
                height="520px",
                key=f"echarts_history_{idx}",
            )
        elif msg.get("table_df") is not None:
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
        if is_followup:
            st.info(
                "🔗 **偵測為延續性分析** — 將帶入前次的 Q 欄位、圖表類型、計畫摘要等脈絡到 Phase 0。"
                "若需開新分析,可在左側 sidebar 按「🆕 開始新分析」清除脈絡。"
            )
        status = st.status("🧠 Agent 思考與執行中...", expanded=True)
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
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": f"⚠️ 資料不足\n\n{clean_msg}",
                })
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
            pipeline = sanitize_pipeline(db_instruction.get("pipeline", []))

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
            st.markdown(
                f"📥 **Phase A 完成** · 來源:{source_label} · "
                f"撈出 {len(raw_df):,} 筆明細 · 欄位:{list(raw_df.columns)}"
            )
            st.dataframe(raw_df.head(100), use_container_width=True)

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
                        st.stop()

            Q = workflow_namespace.get("Q")
            if Q is None or (hasattr(Q, "empty") and Q.empty):
                raise ValueError("Phase B 處理後 Q 為空,請檢查篩選條件。")

            st.markdown(f"⚙️ **Phase B 完成** · KPI 已計算 (共 {len(Q):,} 筆)")
            st.dataframe(Q.head(100), use_container_width=True)

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
                    break
                except Exception:
                    plot_err = traceback.format_exc()
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
            if chart_engine == "ECharts":
                if use_table_fallback:
                    st.info("📋 LLM 判斷此查詢更適合用表格呈現,套用精美 KPI 表格樣式。")
                    render_pretty_table(Q, final_option, key_prefix=f"live_{len(st.session_state.messages)}")
                else:
                    st_echarts(
                        options=final_option,
                        height="520px",
                        key=f"echarts_live_{len(st.session_state.messages)}",
                    )
            else:
                st.plotly_chart(final_fig, use_container_width=True)

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

            st.session_state.messages.append({
                "role": "assistant",
                "content": "分析已完成,如上方資料、圖表與洞察所示。",
                "fig": final_fig,
                "echarts_option": None if use_table_fallback else final_option,
                "table_df": Q if use_table_fallback else None,
                "table_option": final_option if use_table_fallback else None,
                "insight": insight_text,
            })

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

        except Exception as e:
            status.update(label="❌ 系統執行中斷", state="error", expanded=True)
            st.error(f"發生系統級錯誤:\n{str(e)}")
            with st.expander("🔍 展開 Traceback"):
                st.code(traceback.format_exc(), language="bash")
