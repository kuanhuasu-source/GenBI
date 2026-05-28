"""
pages/08_data_analysis.py — v0.17.0 · Data Analysis Page

UI 重構於 v0.17:從 pages/07_upload_workspace.py 拆出。
Flow: Dataset picker → Chat → Save asset → Debug。

# 範圍(從原 07 拆出)
- Section 10:Chat analysis(LLMService + UploadAnalysisService chat handler)
- Section 11:Save asset(chart / metric / template)
- Section 12:Debug panel(dataset/session/metadata/asset/relationship/system)

# Cross-page state
- `analysis_dataset_id` — 由 pages/07_data_workspace.py confirm 後寫入,
  本頁讀作 default selection。
"""

from __future__ import annotations

import sys
from pathlib import Path

# Project root to sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pandas as pd
import streamlit as st

import config
from upload_repository import UploadRepository
from upload_analysis_service import UploadAnalysisService
from analysis_asset_service import AnalysisAssetService
from metadata_correction_service import MetadataCorrectionService
from metadata_provider import UploadMetadataProvider
from llm_service import LLMService
import file_parser

# v0.10.0+: composite chart layout + ECharts renderer
from streamlit_echarts import st_echarts

# ============================================================
# 頁面設定
# ============================================================
# v0.17:set_page_config 改在 app.py 統一設定(st.navigation 規則)
st.markdown(
    "<h1 style='font-size:2.2rem;margin:0 0 .3rem 0'>📊 Data Analysis</h1>"
    "<p style='color:#8B6F4A;font-size:0.95rem;margin:0 0 1rem 0'>"
    "對 confirmed 資料集提出分析問題 — Plan / Phase A 抽取 / Phase B 處理 / "
    "Phase C 圖表 / Phase D 商業洞察。"
    "</p>",
    unsafe_allow_html=True,
)

# ============================================================
# MongoDB / Service init
# ============================================================
@st.cache_resource(show_spinner=False)
def _get_mongo_db():
    from pymongo import MongoClient
    try:
        client = MongoClient(
            config.MONGO_URI,
            serverSelectionTimeoutMS=config.MONGO_SERVER_SELECTION_TIMEOUT_MS,
        )
        client.admin.command("ping")
        return client[config.MONGO_DB], None
    except Exception as e:
        return None, str(e)


mongo_db, mongo_err = _get_mongo_db()

if mongo_db is None:
    st.error(
        f"⚠️ **MongoDB 連線失敗** — Data Analysis 需要 DB 才能運作。\n\n"
        f"錯誤訊息:`{mongo_err or 'unknown'}`\n\n"
        "請先 `brew services start mongodb-community` 再 refresh 此頁。"
    )
    st.stop()


@st.cache_resource(show_spinner=False)
def _get_repo(_db):
    repo = UploadRepository(_db)
    repo.ensure_indexes()
    return repo


repo = _get_repo(mongo_db)

# ============================================================
# Sidebar
# ============================================================
with st.sidebar:
    st.markdown("### 📊 Data Analysis")
    st.caption("v0.17 · 對 confirmed dataset 聊天分析")
    st.markdown("---")
    st.caption(
        "💡 **流程提示**\n\n"
        "1. 先到 **📤 Data Workspace** 上傳 + Confirm metadata\n\n"
        "2. 回此頁選資料集 → 開新 session → 提問\n\n"
        "3. 成功分析後可在 **Save Asset** 區塊沉澱為資產\n\n"
        "4. 已存資產到 **Saved Assets** page 瀏覽 / 重執行"
    )

# ============================================================
# Dataset picker(只列 confirmed metadata 的 dataset)
# ============================================================
def _list_confirmed_datasets(_repo) -> list[dict]:
    """List datasets whose active metadata is confirmed.

    dataset.status 是 parse status(uploaded/parsing/parsed/profiled/error),
    confirmation 狀態在 metadata_versions.confirmation_status,故需逐筆檢查。
    """
    out = []
    for d in _repo.list_datasets(limit=100):
        meta_doc = _repo.get_active_metadata(d["_id"])
        if meta_doc and meta_doc.get("confirmation_status") == "confirmed":
            out.append(d)
    return out


confirmed_datasets = _list_confirmed_datasets(repo)
if not confirmed_datasets:
    st.warning(
        "⚠️ 還沒有 confirmed 的資料集。請先到 **📤 Data Workspace** 上傳 + Confirm metadata。"
    )
    if st.button("→ 前往 Data Workspace", type="primary"):
        st.switch_page("pages/07_data_workspace.py")
    st.stop()

# Default 從 session_state["analysis_dataset_id"] 來(從 07 confirm 後寫入)
default_id = st.session_state.get("analysis_dataset_id")
options = [d["_id"] for d in confirmed_datasets]
labels = [
    f"{d['_id'][:30]}... · {d.get('dataset_name', '?')}"
    if len(d["_id"]) > 30 else
    f"{d['_id']} · {d.get('dataset_name', '?')}"
    for d in confirmed_datasets
]
default_idx = options.index(default_id) if default_id in options else 0

selected_id = st.selectbox(
    "📂 選擇資料集(僅顯示 confirmed metadata)",
    options=options,
    index=default_idx,
    format_func=lambda x: labels[options.index(x)],
    key="_data_analysis_picker",
)
st.session_state["analysis_dataset_id"] = selected_id

if not selected_id:
    st.stop()

# ============================================================
# 讀 dataset / tables / active metadata(原本由 07 sections 1-9 設定)
# ============================================================
dataset = repo.get_dataset(selected_id)
if not dataset:
    st.error(f"❌ dataset `{selected_id}` 不存在")
    st.stop()

tables = repo.list_tables(selected_id)
if not tables:
    st.error(f"❌ dataset `{selected_id}` 無 table 資料")
    st.stop()

active_meta_doc = repo.get_active_metadata(selected_id)
if not active_meta_doc:
    st.error(
        f"❌ dataset `{selected_id}` 無 active metadata — 請回 Data Workspace 重新產生"
    )
    st.stop()

metadata = active_meta_doc["metadata"]
md_version = active_meta_doc["version"]
md_status = active_meta_doc.get("confirmation_status", "draft")

# Dataset 摘要(精簡版,vs 07 的 4-metric 樣式)
col_a, col_b, col_c = st.columns(3)
with col_a:
    st.metric("Dataset", selected_id[:24] + "…" if len(selected_id) > 24 else selected_id)
with col_b:
    st.metric("Metadata version", f"v{md_version}")
with col_c:
    st.metric("Tables", len(tables))

st.divider()

# ============================================================
# 🔟 · Chat Analysis(從原 07 sections 10 整段搬過來)
# ============================================================
# 既然 picker 已過濾為 confirmed,理論上不會走到下面 warning,但保留 defensive check
st.markdown("### 🔟 Chat Analysis")

if md_status != "confirmed":
    st.warning(
        "⚠️ **Metadata 尚未確認** — 請先回 **📤 Data Workspace** "
        "完成 metadata confirm 後再來分析。"
    )
    st.stop()

# ────────────────────────────────────────
# 10.1 · LLMService for upload dataset
# ────────────────────────────────────────
@st.cache_resource(show_spinner=False)
def _get_upload_llm_service(_dataset_id: str, _md_version: int, _mongo_db):
    """為某個 upload dataset + metadata version 建一個 cached LLMService。

    Cache key = (dataset_id, metadata_version) — 確認新版 metadata 時 cache 失效。
    """
    provider = UploadMetadataProvider(
        UploadRepository(_mongo_db), require_confirmed=True,
    )
    task_md = provider.get_metadata(_dataset_id)
    return LLMService(
        api_url=config.LLM_API_URL,
        api_key=config.LLM_API_KEY,
        model_name=config.LLM_MODEL,
        timeout_s=config.LLM_TIMEOUT_S,
        default_temperature=config.LLM_TEMPERATURE,
        task_metadata=task_md,
        # prompt_repo 對 upload-driven 無關鍵作用(Phase 0/A 走 inline),保留 None
        prompt_repo=None,
        domain=_dataset_id,
        model_profile=config.MODEL_PROFILE,
        disable_thinking=config.LLM_DISABLE_THINKING,  # v0.13.3
    )


try:
    upload_llm = _get_upload_llm_service(selected_id, md_version, mongo_db)
except Exception as e:
    st.error(f"❌ 無法建 LLMService:{e}")
    st.stop()


@st.cache_resource(show_spinner=False)
def _get_analysis_service(_mongo_db, _llm_id):
    """UploadAnalysisService(每個 LLMService instance 對應一個 service)。"""
    return UploadAnalysisService(
        mongo_db=_mongo_db,
        upload_repo=UploadRepository(_mongo_db),
        llm_service=upload_llm,
        uploads_root=_PROJECT_ROOT / "uploads",
    )


analysis_service = _get_analysis_service(mongo_db, id(upload_llm))


# M3A: AnalysisAssetService(save chart / metric / template)
@st.cache_resource(show_spinner=False)
def _get_asset_service(_mongo_db):
    _repo = UploadRepository(_mongo_db)
    return AnalysisAssetService(
        upload_repo=_repo,
        correction_service=MetadataCorrectionService(_repo),
    )


asset_service = _get_asset_service(mongo_db)

# ────────────────────────────────────────
# 10.2 · Session 選擇 / 新建
# ────────────────────────────────────────
st.markdown("#### 10.2 · Session 管理")

sessions = repo.list_sessions(dataset_id=selected_id, limit=20)

col_new, col_pick = st.columns([1, 2])
with col_new:
    if st.button(
        "🆕 開新 Session",
        use_container_width=True,
        type="primary",
    ):
        user = st.session_state.get("_upload_owner", "anonymous")
        sid = analysis_service.start_session(
            dataset_id=selected_id,
            metadata_version=md_version,
            user=user,
        )
        st.session_state[f"_active_session_{selected_id}"] = sid
        st.toast(f"已開新 session · {sid[:20]}...", icon="✨")
        st.rerun()

with col_pick:
    if sessions:
        active_sid = st.session_state.get(f"_active_session_{selected_id}")
        if not active_sid:
            active_sid = sessions[0]["_id"]
        sid_options = [s["_id"] for s in sessions]
        sid_labels = [
            f"{s['_id'][:20]}... · md_v{s.get('metadata_version', '?')} · "
            f"{len(s.get('messages', []))} 訊息"
            for s in sessions
        ]
        try:
            current_idx = sid_options.index(active_sid)
        except ValueError:
            current_idx = 0
        chosen_sid = st.selectbox(
            "選擇 session(或按左邊開新 session)",
            options=sid_options,
            index=current_idx,
            format_func=lambda x: sid_labels[sid_options.index(x)],
            key=f"_session_selector_{selected_id}",
        )
        st.session_state[f"_active_session_{selected_id}"] = chosen_sid
    else:
        st.info("尚無 session — 按左邊「🆕 開新 Session」開始第一輪分析。")
        st.stop()

active_sid = st.session_state[f"_active_session_{selected_id}"]
session_doc = repo.get_session(active_sid)
if not session_doc:
    st.error(f"Session `{active_sid}` 已不存在")
    st.session_state.pop(f"_active_session_{selected_id}", None)
    st.stop()

# ────────────────────────────────────────
# 10.3 · Chart engine / Insight toggle
# ────────────────────────────────────────
col_ce, col_ins = st.columns(2)
with col_ce:
    chart_engine = st.radio(
        "📊 圖表引擎",
        options=["ECharts", "Plotly"],
        index=0, horizontal=True,
        key=f"_chart_engine_{active_sid}",
    )
with col_ins:
    enable_insight = st.toggle(
        "啟用 Phase D 商業洞察",
        value=True,
        key=f"_insight_toggle_{active_sid}",
    )

# ────────────────────────────────────────
# 10.4 · Chat history(replay)
# ────────────────────────────────────────
st.markdown("#### 10.4 · 對話歷史")

messages = session_doc.get("messages", [])
if not messages:
    st.caption("(尚無對話)")
else:
    for idx, msg in enumerate(messages):
        role = msg.get("role", "?")
        content = msg.get("content", "")
        with st.chat_message(role):
            # Refusal 短路
            if msg.get("refusal"):
                st.warning("⚠️ 資料不足")
                st.markdown(content)
                continue
            # Meta intent response
            if msg.get("meta_intent"):
                st.markdown(content)
                continue
            # v0.18 META 結構性問題短路 — plan_text 已含答案,跳過 phase 展示
            if msg.get("meta_structural"):
                st.info("ℹ️ 結構性問題 — 直接從 metadata 回答(略過 Phase A/B/C/D)")
                st.markdown(content)
                continue
            # 正常 assistant
            if role == "assistant" and msg.get("plan_text"):
                with st.expander("📋 Plan", expanded=False):
                    st.markdown(msg["plan_text"])
                if msg.get("phase_a_code"):
                    with st.expander("🪛 Phase A · Pandas filter code", expanded=False):
                        st.code(msg["phase_a_code"], language="python")
                if msg.get("phase_b_code"):
                    with st.expander("🐍 Phase B · Preprocess code", expanded=False):
                        st.code(msg["phase_b_code"], language="python")
                if msg.get("phase_c_code"):
                    with st.expander("🎨 Phase C · Chart code", expanded=False):
                        st.code(msg["phase_c_code"], language="python")
                if msg.get("insight"):
                    with st.expander("🧠 商業洞察", expanded=False):
                        st.markdown(msg["insight"])
                if msg.get("trace_id"):
                    st.caption(f"🔍 trace_id: `{msg['trace_id'][:8]}`")
            st.write(content)

# ────────────────────────────────────────
# 10.5 · Chat input · v0.17 Progressive render
# ────────────────────────────────────────
# Phase metadata: (phase_id, emoji, label, step_n, total)
PHASE_META = [
    ("phase_0_plan",       "📋", "Phase 0 · 制定計畫",      1, 5),
    ("phase_a_pipeline",   "🛠️", "Phase A · 資料抽取",      2, 5),
    ("phase_b_preprocess", "🐍", "Phase B · 資料處理",      3, 5),
    ("phase_c_chart",      "🎨", "Phase C · 視覺化",        4, 5),
    ("phase_d_insight",    "🧠", "Phase D · 商業洞察",      5, 5),
]


def _render_phase_detail(phase_id: str, payload: dict) -> None:
    """各 phase complete 後的細節 — 對齊 schema-driven app.py 的 expander 樣式。"""
    if phase_id == "phase_0_plan":
        st.markdown(payload.get("plan_text", ""))
    elif phase_id == "phase_a_pipeline":
        if payload.get("code"):
            st.code(payload["code"], language="python")
        rdf = payload.get("raw_df_info") or {}
        if rdf:
            st.caption(
                f"📊 raw_df: {rdf.get('n_rows', 0):,} rows × "
                f"{len(rdf.get('columns', []))} cols"
            )
    elif phase_id == "phase_b_preprocess":
        if payload.get("code"):
            st.code(payload["code"], language="python")
        q_info = payload.get("Q_info") or {}
        if q_info:
            st.caption(
                f"📊 Q: {q_info.get('n_rows', 0):,} rows × "
                f"{len(q_info.get('columns', []))} cols"
            )
        if payload.get("Q_preview_md"):
            st.markdown(payload["Q_preview_md"])
    elif phase_id == "phase_c_chart":
        if payload.get("code"):
            st.code(payload["code"], language="python")
        if payload.get("use_table_fallback"):
            st.info("📋 Phase C 降級為 table fallback(3 次重試失敗)")
    elif phase_id == "phase_d_insight":
        st.markdown(payload.get("insight", ""))


def _start_progressive_render(query_text: str):
    """建立 status box + 5 個 phase containers + callback。

    Returns:
        (status_box, containers, callback)
    """
    status_box = st.status(
        f"🧠 處理中:{query_text[:60]}{'…' if len(query_text) > 60 else ''}",
        expanded=True,
    )
    containers = {pid: st.container() for pid, *_ in PHASE_META}

    # 用 dict 儲 elapsed_s,讓 closure 在多次 callback 間共享狀態
    elapsed_so_far: dict[str, float] = {}

    def callback(phase_id: str, event: str, payload: dict) -> None:
        meta = next(
            (m for m in PHASE_META if m[0] == phase_id), None
        )
        if not meta:
            return
        _, emoji, label, step_n, total = meta

        if event == "start":
            status_box.update(
                label=f"{emoji} {label} · 進行中... [{step_n}/{total}]"
            )
            with containers[phase_id]:
                st.markdown(f"⏳ **{emoji} {label}** · 進行中...")

        elif event == "complete":
            elapsed_so_far[phase_id] = payload.get("elapsed_s", 0.0)
            containers[phase_id].empty()
            with containers[phase_id]:
                st.success(
                    f"✅ **{emoji} {label}** · 完成 "
                    f"({elapsed_so_far[phase_id]:.1f}s)"
                )
                with st.expander("▶ 展開查看", expanded=False):
                    _render_phase_detail(phase_id, payload)

        elif event == "error":
            containers[phase_id].empty()
            with containers[phase_id]:
                st.error(
                    f"❌ **{emoji} {label}** · 失敗:"
                    f"{payload.get('error', 'unknown')}"
                )
                tb = payload.get("traceback", "")
                if tb:
                    with st.expander("🔍 Traceback", expanded=False):
                        st.code(tb, language="text")

        elif event == "skipped":
            containers[phase_id].empty()
            with containers[phase_id]:
                st.info(
                    f"⏭️ **{emoji} {label}** · 跳過:"
                    f"{payload.get('reason', '')}"
                )

    return status_box, containers, callback


query = st.chat_input(
    "對此 dataset 提出分析問題(例如:畫 leadtime 的分佈,標示平均、中位數、P95)"
)

if query:
    with st.chat_message("user"):
        st.write(query)

    with st.chat_message("assistant"):
        status_box, _phase_containers, on_phase_cb = _start_progressive_render(query)
        try:
            result = analysis_service.handle_query(
                session_id=active_sid,
                query=query,
                chart_engine=chart_engine,
                enable_insight=enable_insight,
                on_phase=on_phase_cb,
            )

            # 後處理 result —— 每 phase 細節已由 on_phase_cb 漸進顯示,
            # 此處只渲染:狀態收尾、最終圖表、Q 完整 dataframe、trace_id。
            if result["status"] == "meta":
                status_box.update(
                    label=f"✅ Meta intent: {result['intent']}",
                    state="complete", expanded=False,
                )
                st.markdown(result.get("meta_response", ""))
                st.rerun()

            elif result["status"] == "meta_structural":
                # v0.18 [META] short-circuit — Phase 0 plan_text already
                # contains the structural answer; Phase A/B/C/D skipped.
                status_box.update(
                    label="ℹ️ 結構性問題 — Phase A/B/C/D 略過",
                    state="complete", expanded=False,
                )
                st.info(
                    "ℹ️ 偵測為結構性問題(列表 / 主鍵 / schema 之類),"
                    "直接從 metadata 回答,略過 Phase A/B/C/D。"
                )
                st.markdown(result.get("meta_response", ""))
                st.rerun()

            elif result["status"] == "refused":
                status_box.update(
                    label="🛑 資料不足", state="error", expanded=False,
                )
                st.warning("⚠️ 資料不足 — 此分析觸犯 metadata 中的 data_limitations")
                st.markdown(result.get("refusal_message", ""))
                st.rerun()

            elif result["status"] == "failed":
                status_box.update(
                    label="❌ 失敗", state="error", expanded=True,
                )
                st.error(f"❌ {result.get('error', 'unknown error')}")
                # phase callback 已展示各 phase code,此處不重複

            elif result["status"] == "completed":
                status_box.update(
                    label="✅ 分析完成", state="complete", expanded=False,
                )
                if result.get("is_followup"):
                    st.caption("🔗 偵測為延續性分析")

                # 最終圖表(callback 不渲染圖表本體)
                if result.get("use_table_fallback"):
                    st.info("📋 Phase C 降級為表格(retry 3 次失敗)")
                    st.dataframe(result["Q"], use_container_width=True)
                elif chart_engine == "ECharts" and result.get("chart_option"):
                    st_echarts(
                        options=result["chart_option"],
                        height="520px",
                        key=f"echarts_live_{result['trace_id'][:8]}",
                    )
                elif chart_engine == "Plotly" and result.get("chart_fig"):
                    st.plotly_chart(result["chart_fig"],
                                     use_container_width=True)

                # 完整 Q dataframe(callback 只給 head(5) preview)
                with st.expander(
                    f"📊 Q 完整 ({result['Q_info']['n_rows']:,} 列 × "
                    f"{len(result['Q_info']['columns'])} 欄)",
                    expanded=False,
                ):
                    st.dataframe(result["Q"].head(200),
                                  use_container_width=True)

                # Trace id
                st.caption(
                    f"🔍 trace_id: `{result['trace_id'][:8]}` · "
                    "可在 Task Traces page 查完整流程"
                )
                st.write("分析已完成,如上方資料、圖表與洞察所示。")

                # M3A: 把 result 存到 session_state,讓下方 save 區塊用
                st.session_state[f"_last_result_{active_sid}"] = result

        except Exception as e:
            status_box.update(label="❌ 系統錯誤", state="error", expanded=True)
            st.error(f"❌ 系統執行中斷:{type(e).__name__}: {e}")
            import traceback as _tb
            with st.expander("🔍 Traceback"):
                st.code(_tb.format_exc(), language="bash")

# ────────────────────────────────────────
# 11 · Save Asset(M3A — 上次成功分析才會出現)
# ────────────────────────────────────────
last_result = st.session_state.get(f"_last_result_{active_sid}")
if last_result and last_result.get("status") == "completed":
    st.markdown("#### 1️⃣1️⃣ Save Analysis Asset")
    st.caption(
        f"💾 把上次分析(`{last_result['trace_id'][:8]}`)沉澱成可重用資產。"
        f"3 種類型,各自獨立保存。"
    )

    tab_chart, tab_metric, tab_template = st.tabs([
        "📊 Save Chart", "📐 Save Metric", "📋 Save Template",
    ])

    user = st.session_state.get("_upload_owner", "anonymous")

    with tab_chart:
        with st.form(f"_save_chart_form_{active_sid}", clear_on_submit=True):
            chart_name = st.text_input(
                "Chart name *",
                placeholder="例:HC 分佈直方圖(M3 baseline)",
                max_chars=80,
            )
            chart_desc = st.text_area(
                "Description",
                placeholder="(可選)為何保存這張、何時看",
                max_chars=300, height=80,
            )
            submitted = st.form_submit_button("💾 Save Chart", type="primary")
            if submitted:
                if not chart_name.strip():
                    st.error("Chart name 不能為空")
                else:
                    try:
                        asset_id = asset_service.save_chart(
                            dataset_id=selected_id,
                            session_id=active_sid,
                            analysis_result=last_result,
                            name=chart_name.strip(),
                            description=chart_desc.strip(),
                            user=user,
                        )
                        st.success(f"✅ Saved Chart 已存 · `{asset_id}`")
                        st.toast("📊 Chart asset 已建", icon="💾")
                    except Exception as e:
                        st.error(f"❌ 保存失敗:{e}")

    with tab_metric:
        with st.form(f"_save_metric_form_{active_sid}", clear_on_submit=True):
            st.caption(
                "⚠️ Save Metric 會把此 KPI **寫回 dynamic metadata.kpi_definitions**,"
                "並產生新的 metadata version。後續分析可用自然語言引用。"
            )
            metric_key = st.text_input(
                "KPI key * (snake_case)",
                placeholder="例:avg_hc / pay_rate / total_revenue",
                max_chars=40,
            )
            metric_name = st.text_input(
                "KPI 顯示名 *",
                placeholder="例:平均人數 / 通過率 / 總營收",
                max_chars=60,
            )
            metric_formula = st.text_input(
                "Formula *",
                placeholder=(
                    "例:mean(hc) / sum(pay_count)/sum(total) / "
                    "Q['amount'].sum()"
                ),
                max_chars=200,
                help="用 dataset 欄位或 Q 欄位寫公式,給後續 LLM 看",
            )
            metric_note = st.text_area(
                "Important note(unit / 限制)",
                placeholder="例:unit=people / 不可 sum,只能 mean",
                max_chars=200, height=70,
            )
            metric_desc = st.text_area(
                "Description",
                placeholder="(可選)業務含義",
                max_chars=300, height=70,
            )
            submitted = st.form_submit_button("💾 Save Metric", type="primary")
            if submitted:
                missing = [
                    f for f, v in (("KPI key", metric_key),
                                    ("KPI 顯示名", metric_name),
                                    ("Formula", metric_formula))
                    if not v.strip()
                ]
                if missing:
                    st.error(f"必填欄位空白:{', '.join(missing)}")
                elif not metric_key.replace("_", "").isalnum():
                    st.error("KPI key 只能用 [a-zA-Z0-9_],請改成 snake_case")
                else:
                    try:
                        asset_id = asset_service.save_metric(
                            dataset_id=selected_id,
                            session_id=active_sid,
                            analysis_result=last_result,
                            kpi_key=metric_key.strip(),
                            name=metric_name.strip(),
                            formula=metric_formula.strip(),
                            important_note=metric_note.strip(),
                            description=metric_desc.strip(),
                            user=user,
                        )
                        st.success(
                            f"✅ Saved Metric 已存 · `{asset_id}`\n\n"
                            f"📌 已寫回 dynamic metadata,產出新 metadata version。"
                            f"下次 query 引用「{metric_name}」LLM 會看得到。"
                        )
                        st.toast("📐 Metric 寫回 metadata 完成", icon="💾")
                        # metadata 變了 → 清 LLMService cache 讓下次 query 重 build
                        st.cache_resource.clear()
                    except Exception as e:
                        st.error(f"❌ 保存失敗:{type(e).__name__}: {e}")

    with tab_template:
        with st.form(f"_save_tmpl_form_{active_sid}", clear_on_submit=True):
            st.caption(
                "📋 Template 保存「query + plan」,在 Saved Assets page 可一鍵"
                "重新以同 query 觸發分析。MVP 只支援同 dataset 內重執行。"
            )
            tmpl_name = st.text_input(
                "Template name *",
                placeholder="例:每月人力分佈分析(template)",
                max_chars=80,
            )
            tmpl_desc = st.text_area(
                "Description",
                placeholder="(可選)什麼場景下適用此 template",
                max_chars=300, height=80,
            )
            submitted = st.form_submit_button("💾 Save Template", type="primary")
            if submitted:
                if not tmpl_name.strip():
                    st.error("Template name 不能為空")
                else:
                    try:
                        asset_id = asset_service.save_template(
                            dataset_id=selected_id,
                            session_id=active_sid,
                            analysis_result=last_result,
                            name=tmpl_name.strip(),
                            description=tmpl_desc.strip(),
                            user=user,
                        )
                        st.success(f"✅ Saved Template 已存 · `{asset_id}`")
                        st.toast("📋 Template 已建", icon="💾")
                    except Exception as e:
                        st.error(f"❌ 保存失敗:{e}")

    st.caption(
        "🔗 已保存的 assets 在 **Saved Assets** page 瀏覽 / 重執行 / 重命名 / 刪除。"
    )

st.divider()

# ============================================================
# 1️⃣2️⃣ · Debug Panel(M4c · spec §15)
# ============================================================
# 統一彙整本 page 各 phase / session / metadata / asset 的觀測點,給開發者除錯
# + 給使用者看分析過程透明度
st.markdown("### 1️⃣2️⃣ Debug Panel")

debug_tabs = st.tabs([
    "🗂 Dataset / Session",
    "📜 Metadata history",
    "🤖 Last analysis trace",
    "💾 Assets summary",
    "🔗 Relationships",
    "⚙️ System status",
])

# ── Tab 1:Dataset / Session 基本資訊 ──
with debug_tabs[0]:
    col_a, col_b, col_c, col_d = st.columns(4)
    col_a.metric("Dataset", selected_id[:24] + "…" if len(selected_id) > 24 else selected_id)
    col_b.metric("Tables", len(tables))
    col_c.metric("Metadata version", md_version)
    col_d.metric("Status", md_status)
    if active_sid:
        st.caption(f"📍 Active session: `{active_sid}` · "
                   f"messages: {len(session_doc.get('messages', []))} · "
                   f"created_at: {session_doc.get('created_at')}")
        with st.expander("Session raw doc(JSON)", expanded=False):
            # 拿掉 messages 細節以免太大
            display_doc = {k: v for k, v in session_doc.items() if k != "messages"}
            display_doc["messages_count"] = len(session_doc.get("messages", []))
            st.json(display_doc, expanded=False)

# ── Tab 2:Metadata version 歷史(M5.2:加 activate + re-profile)──
with debug_tabs[1]:
    versions = repo.list_metadata_versions(selected_id)
    if not versions:
        st.caption("尚無 metadata version")
    else:
        rows = []
        for v in versions:
            rows.append({
                "version": v["version"],
                "active": "✅" if v.get("is_active") else "",
                "status": v.get("confirmation_status", "?"),
                "by": v.get("confirmed_by") or v.get("created_by", "system"),
                "created_at": v.get("created_at"),
                "notes": (v.get("notes") or "")[:60],
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True,
                      hide_index=True, height=min(35 + len(rows) * 35, 320))

        # M5.2:Activate 舊 version
        # (重 regenerate 留在 Data Workspace,本頁聚焦分析)
        st.markdown("**🔄 Version actions(M5.2)**")
        inactive_versions = [v["version"] for v in versions if not v.get("is_active")]
        if inactive_versions:
            target_v = st.selectbox(
                "Activate previous version",
                options=inactive_versions,
                format_func=lambda x: f"v{x}",
                key=f"_activate_v_{selected_id}",
            )
            if st.button(
                f"切回 v{target_v} 為 active",
                type="secondary", use_container_width=False,
                help="這 dataset 的後續分析會用此版 metadata。Saved Asset 的 drift check 也會比對此 version。",
            ):
                ok = repo.activate_metadata_version(selected_id, target_v)
                if ok:
                    st.toast(f"✅ v{target_v} 已 active", icon="🔄")
                    # 清 LLM cache 確保下次 query 用新 metadata
                    try:
                        st.cache_resource.clear()
                    except Exception:
                        pass
                    st.rerun()
                else:
                    st.error(f"Activate v{target_v} 失敗")
        else:
            st.caption("沒有可切換的舊 version(只有 1 個)")

        # Profile version history(M5.2:read-only)
        profile_versions = repo.list_profile_versions(selected_id)
        if len(profile_versions) > 1:
            st.markdown("**📊 Profile version history**")
            with st.expander(f"展開 {len(profile_versions)} 個 profile version",
                              expanded=False):
                p_rows = [
                    {"profile_version": pv["profile_version"],
                     "tables": len(pv.get("tables", [])),
                     "created_at": pv.get("created_at")}
                    for pv in profile_versions
                ]
                st.dataframe(pd.DataFrame(p_rows),
                              use_container_width=True, hide_index=True)
                st.caption(
                    "Profile 自動 versioning,latest 永遠是 active。"
                    "若要重新 profile(例:資料改了),需先 delete dataset 再上傳。"
                )
    # User corrections audit
    corrections = repo.list_corrections(selected_id)
    if corrections:
        st.markdown(f"**📝 User corrections audit ({len(corrections)} entries)**")
        with st.expander("展開 correction 詳細", expanded=False):
            for c in corrections[:10]:
                st.caption(
                    f"v{c.get('metadata_version_before')} → v{c.get('metadata_version_after')} · "
                    f"{c.get('created_by')} · {c.get('created_at')}"
                )
                for delta in c.get("corrections", [])[:5]:
                    st.code(
                        f"  {delta.get('target')}: "
                        f"{delta.get('old_value')!r} → {delta.get('new_value')!r}",
                        language="text",
                    )

# ── Tab 3:Last analysis trace(spec §15 phase outputs / retry logs / tokens) ──
with debug_tabs[2]:
    last_res = st.session_state.get(f"_last_result_{active_sid}")
    if not last_res:
        st.caption("尚無分析結果 — 在 Section 10 跑一個 query 後此處會顯示細節")
    else:
        status = last_res.get("status", "?")
        trace_id = last_res.get("trace_id", "—")
        col_s, col_t = st.columns(2)
        col_s.metric("Status", status)
        col_t.metric("Trace ID", trace_id[:12] if trace_id else "—")
        # Phase outputs sizes
        phase_sizes = {
            "Phase 0 plan": len(last_res.get("plan_text") or ""),
            "Phase A code": len(last_res.get("phase_a_code") or ""),
            "Phase B code": len(last_res.get("phase_b_code") or ""),
            "Phase C code": len(last_res.get("phase_c_code") or ""),
            "Insight": len(last_res.get("insight") or ""),
        }
        st.markdown("**Phase output size (chars)**")
        st.dataframe(pd.DataFrame(
            list(phase_sizes.items()), columns=["Phase", "Size"]
        ), use_container_width=True, hide_index=True)
        # Q info
        q_info = last_res.get("Q_info") or {}
        if q_info:
            st.markdown(
                f"**Q after Phase B**: {q_info.get('n_rows', 0):,} rows × "
                f"{len(q_info.get('columns', []))} cols → "
                f"`{q_info.get('columns', [])}`"
            )
        # Raw_df info
        rdf_info = last_res.get("raw_df_info") or {}
        if rdf_info:
            st.markdown(
                f"**raw_df after Phase A**: {rdf_info.get('n_rows', 0):,} rows × "
                f"{len(rdf_info.get('columns', []))} cols"
            )
        # Fallback reason
        if last_res.get("use_table_fallback"):
            st.warning("⚠️ Phase C 降級為 table fallback — LLM 3 次未產出有效 chart")
        # Connect to Task Traces page
        if trace_id and trace_id != "—":
            st.caption(
                f"🔍 完整 LLM messages + tokens 請見 **Task Traces** page, "
                f"trace_id=`{trace_id}`"
            )

# ── Tab 4:Assets summary ──
with debug_tabs[3]:
    all_assets = repo.list_assets(dataset_id=selected_id, include_inactive=True)
    if not all_assets:
        st.caption("尚無 saved asset")
    else:
        by_type: dict[str, int] = {}
        active_count = 0
        for a in all_assets:
            t = a.get("asset_type", "?")
            by_type[t] = by_type.get(t, 0) + 1
            if a.get("is_active"):
                active_count += 1
        col_t, col_a = st.columns(2)
        col_t.metric("Total assets", len(all_assets),
                      f"{active_count} active")
        col_a.metric("By type",
                      " / ".join(f"{k}:{v}" for k, v in by_type.items()))
        with st.expander("Asset list (latest 10)", expanded=False):
            asset_rows = [
                {
                    "id": a["_id"][:24] + "…" if len(a["_id"]) > 24 else a["_id"],
                    "type": a.get("asset_type"),
                    "name": a.get("name"),
                    "md_v": a.get("metadata_version"),
                    "active": "✅" if a.get("is_active") else "—",
                    "created_at": a.get("created_at"),
                }
                for a in all_assets[:10]
            ]
            st.dataframe(pd.DataFrame(asset_rows),
                          use_container_width=True, hide_index=True)

# ── Tab 5:Relationships(M5.3:跨 sheet relationship 偵測 + 確認)──
with debug_tabs[4]:
    if len(tables) < 2:
        st.caption(
            "此 dataset 只有 1 個 table — relationship 偵測需要 ≥2 個 table。\n\n"
            "上傳 Excel multi-sheet(M5.1)或多 CSV 才會出現 relationships。"
        )
    else:
        # v0.18 M2:relationships are auto-detected during upload and
        # persisted to upload_relationship_candidates. This panel reads
        # the stored candidates rather than re-running detection on every
        # page load. Confirm / Reject / Edit lives on pages/07.
        candidates = repo.list_relationship_candidates(selected_id)
        if not candidates:
            st.info(
                "沒偵測到 cross-table relationship,或 upload 過程偵測失敗。"
                "可重新上傳此 dataset,或到 pages/07 手動 review。"
            )
        else:
            st.caption(
                f"📊 共 {len(candidates)} 條 relationship candidates"
            )
            rel_rows = []
            for r in candidates:
                ev = r.get("evidence", {})
                rel_rows.append({
                    "from": f"{r['from_table']}.{r['from_field']}",
                    "to": f"{r['to_table']}.{r['to_field']}",
                    "type": r["relationship_type"],
                    "confidence": r["confidence"],
                    "tier": r.get("confidence_tier", "—"),
                    "status": r.get("status", "candidate"),
                    "overlap": ev.get("from_to_overlap_ratio", 0),
                    "to_unique": ev.get("to_unique_ratio", 0),
                })
            st.dataframe(pd.DataFrame(rel_rows),
                          use_container_width=True, hide_index=True)
            st.caption(
                "Review + Confirm 在 **pages/07 Data Workspace** 處理 — "
                "本頁僅顯示候選清單。"
            )


# ── Tab 6:System status(spec §14 / §15 security limits) ──
with debug_tabs[5]:
    col_l, col_t = st.columns(2)
    with col_l:
        st.markdown("**🔒 Safety limits**")
        st.code(
            "Max upload size : 100 MB\n"
            "Phase A timeout : 30s\n"
            "Phase B timeout : 60s\n"
            "Row limit       : 100,000\n"
            "Col limit       : 500\n"
            "Forbidden ops   : open / exec / eval / __import__ / "
            "os / subprocess / requests / socket",
            language="text",
        )
    with col_t:
        st.markdown("**🤖 LLM config**")
        st.code(
            f"Provider     : {config.LLM_PROVIDER}\n"
            f"Endpoint     : {config.LLM_BASE_URL}\n"
            f"Model        : {config.LLM_MODEL}\n"
            f"Timeout      : {config.LLM_TIMEOUT_S}s\n"
            f"Profile      : {config.MODEL_PROFILE_NAME}\n"
            f"Thinking off : {config.LLM_DISABLE_THINKING}\n"
            f"Prompt repo  : {'ON' if config.PROMPT_REPO_ENABLED else 'OFF'}",
            language="text",
        )
    st.markdown("**📦 MongoDB collections in use**")
    coll_names = [
        "uploaded_datasets", "upload_tables", "upload_profiles",
        "upload_metadata_versions", "upload_user_corrections",
        "analysis_sessions", "analysis_assets", "task_traces",
    ]
    coll_rows = [
        {"collection": c, "count": mongo_db[c].count_documents({})}
        for c in coll_names
    ]
    st.dataframe(pd.DataFrame(coll_rows), use_container_width=True,
                  hide_index=True, height=320)

st.caption(
    "✅ v0.17 — Data Analysis page(從 07 split)"
)
