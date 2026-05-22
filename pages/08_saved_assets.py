"""
pages/08_saved_assets.py — v0.13.2+ (M3A)

Saved Assets Panel — 瀏覽 / 重執行 / 重命名 / 刪除 saved chart / metric / template。

# 對應 spec
- §12A.5 Saved Assets Panel:3 個 tab(Saved Charts / Saved Metrics /
  Analysis Templates)
- §12A.7 Acceptance:重開、重執行、重命名、刪除
- §12A.7 #9:metadata_version 已過期時提示使用者

# 動作

| 動作        | 邏輯                                                                   |
|------------|------------------------------------------------------------------------|
| 查看        | 展開 asset detail(lineage / payload / drift check)                    |
| 重新開啟    | st_echarts 直接渲染 asset_payload.chart_option(只 chart 適用)         |
| 重新執行    | 把 asset.source_query 注入該 dataset 對應 session 的 chat input,跳轉  |
|             | 回 Upload Workspace。Rerun 走 full replay,LLM 重跑 5-phase。           |
| 重新命名    | 開 form 改 name + description                                          |
| 刪除        | soft delete(is_active=False),保留 audit                              |

# 路徑
spec 預期 `pages/07_saved_assets.py`,既有 page 07 已佔(upload workspace),
本 page 使用 08(Streamlit 純按檔名排序)。
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pandas as pd
import streamlit as st
from streamlit_echarts import st_echarts

import config
from upload_repository import UploadRepository
from analysis_asset_service import AnalysisAssetService
from metadata_correction_service import MetadataCorrectionService


st.set_page_config(
    page_title="GenBI · Saved Assets",
    page_icon="💾",
    layout="wide",
)
st.markdown(
    "<h1 style='font-size:2.2rem;margin:0 0 .3rem 0'>💾 Saved Assets</h1>"
    "<p style='color:#8B6F4A;font-size:0.95rem;margin:0 0 1rem 0'>"
    "M3A · 瀏覽 / 重執行 / 重命名 / 刪除上傳工作區的 Saved Chart / Metric / Template。"
    "</p>",
    unsafe_allow_html=True,
)


# ============================================================
# Service init
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
        f"⚠️ MongoDB 連線失敗 — Saved Assets 需要 DB。\n\n"
        f"錯誤:`{mongo_err or 'unknown'}`"
    )
    st.stop()


@st.cache_resource(show_spinner=False)
def _get_asset_service(_db):
    repo = UploadRepository(_db)
    return AnalysisAssetService(
        upload_repo=repo,
        correction_service=MetadataCorrectionService(repo),
    ), repo


asset_service, repo = _get_asset_service(mongo_db)


# ============================================================
# Filter bar
# ============================================================
st.markdown("### 🔎 Filter")

col_d, col_o, col_inactive = st.columns([2, 2, 1])
with col_d:
    # 列出所有有 asset 的 dataset
    datasets = repo.list_datasets(limit=100)
    dataset_options = ["(全部)"] + [
        f"{d['_id']} · {d.get('dataset_name', '?')}"
        for d in datasets
    ]
    dataset_choice = st.selectbox(
        "Dataset",
        options=dataset_options,
        index=0,
        key="_assets_dataset_filter",
    )
with col_o:
    owner_filter = st.text_input(
        "Created by(留空 = 全部)",
        value="",
        key="_assets_owner_filter",
    )
with col_inactive:
    show_inactive = st.toggle(
        "顯示已刪除",
        value=False,
        key="_assets_inactive_toggle",
    )

# Map dataset 選擇 → dataset_id
filter_dataset_id = None
if dataset_choice != "(全部)":
    filter_dataset_id = dataset_choice.split(" · ")[0]
filter_owner = owner_filter.strip() or None


# ============================================================
# 3 tabs · Saved Chart / Saved Metric / Analysis Template
# ============================================================
tab_chart, tab_metric, tab_template = st.tabs([
    "📊 Saved Charts", "📐 Saved Metrics", "📋 Analysis Templates",
])


def _render_asset_card(
    asset: dict,
    asset_type: str,
) -> None:
    """單一 asset 的 expander UI。"""
    asset_id = asset["_id"]
    name = asset.get("name", "(unnamed)")
    desc = asset.get("description") or ""
    ds_id = asset.get("dataset_id", "?")
    md_v = asset.get("metadata_version", "?")
    created = asset.get("created_at", "?")
    creator = asset.get("created_by", "?")

    # Drift check
    drift = asset_service.metadata_drift_check(asset_id)
    stale_tag = ""
    if drift["is_stale"]:
        stale_tag = (
            f" <span style='background:#FFB020;color:white;padding:2px 8px;"
            f"border-radius:8px;font-size:0.75rem'>⚠️ stale v{drift['asset_version']} → v{drift['active_version']}</span>"
        )

    inactive_tag = (
        " <span style='background:#999;color:white;padding:2px 8px;"
        "border-radius:8px;font-size:0.75rem'>🗑 deleted</span>"
        if not asset.get("is_active", True) else ""
    )

    header_html = (
        f"<b>{name}</b>{stale_tag}{inactive_tag}<br/>"
        f"<small style='color:#888'>{ds_id} · md_v{md_v} · "
        f"{creator} · `{asset_id[:30]}`</small>"
    )

    with st.expander(name + (" 🗑" if not asset.get("is_active", True) else ""),
                       expanded=False):
        st.markdown(header_html, unsafe_allow_html=True)
        if desc:
            st.caption(f"📝 {desc}")
        if drift["warning"]:
            st.warning(drift["warning"])

        st.markdown(f"**Source query:** `{asset.get('source_query', '')[:200]}`")

        # ── Action buttons ──
        col_view, col_rerun, col_rename, col_del = st.columns(4)

        with col_view:
            if st.button(
                "👁 View detail",
                key=f"_view_{asset_id}",
                use_container_width=True,
            ):
                st.session_state[f"_view_open_{asset_id}"] = True

        with col_rerun:
            replay_q = asset.get("source_query") or ""
            if st.button(
                "🔄 Rerun",
                key=f"_rerun_{asset_id}",
                use_container_width=True,
                disabled=not replay_q,
                help=("把 query 寫進 session_state,你跳回 Upload Workspace "
                      "貼進 chat input 即可重執行。MVP 為簡化沒做自動跳轉。"),
            ):
                st.session_state["_replay_dataset_id"] = ds_id
                st.session_state["_replay_query"] = replay_q
                st.toast(
                    f"已預備 query — 切到 Upload Workspace 對 `{ds_id}` 貼進 chat 即可",
                    icon="🔄",
                )

        with col_rename:
            if st.button(
                "✏️ Rename",
                key=f"_rename_{asset_id}",
                use_container_width=True,
            ):
                st.session_state[f"_rename_open_{asset_id}"] = True

        with col_del:
            if st.button(
                "🗑 Delete",
                key=f"_del_{asset_id}",
                use_container_width=True,
                disabled=not asset.get("is_active", True),
            ):
                st.session_state[f"_del_confirm_{asset_id}"] = True

        # ── View detail panel ──
        if st.session_state.get(f"_view_open_{asset_id}"):
            with st.container():
                st.markdown("**📦 Asset Payload**")
                st.json(asset.get("asset_payload", {}), expanded=False)
                st.markdown("**🔗 Lineage**")
                lineage = asset.get("lineage", {}) or {}
                if lineage.get("phase_0_plan"):
                    with st.expander("📋 Phase 0 Plan",
                                       expanded=False):
                        st.markdown(lineage["phase_0_plan"])
                if lineage.get("phase_a_code"):
                    with st.expander("🪛 Phase A · Pandas filter code",
                                       expanded=False):
                        st.code(lineage["phase_a_code"], language="python")
                if lineage.get("phase_b_code"):
                    with st.expander("🐍 Phase B · Preprocess code",
                                       expanded=False):
                        st.code(lineage["phase_b_code"], language="python")
                if lineage.get("phase_c_code"):
                    with st.expander("🎨 Phase C · Chart code",
                                       expanded=False):
                        st.code(lineage["phase_c_code"], language="python")
                if lineage.get("q_preview"):
                    with st.expander(
                        f"📊 Q 樣本(前 {len(lineage['q_preview'])} 列)",
                        expanded=False,
                    ):
                        try:
                            st.dataframe(
                                pd.DataFrame(lineage["q_preview"]),
                                use_container_width=True,
                                hide_index=True,
                            )
                        except Exception as _e:
                            st.caption(f"無法渲染 Q 樣本:{_e}")

                # Chart re-render(僅對 saved_chart)
                if asset_type == "saved_chart":
                    payload = asset.get("asset_payload", {}) or {}
                    opt = payload.get("chart_option")
                    if opt and not payload.get("use_table_fallback"):
                        st.markdown("**📊 Re-rendered chart(以保存當時的 option)**")
                        try:
                            st_echarts(
                                options=opt, height="500px",
                                key=f"_replay_chart_{asset_id}",
                            )
                        except Exception as _ce:
                            st.error(f"重渲圖表失敗:{_ce}")

                if st.button("❌ 關閉 detail", key=f"_close_view_{asset_id}"):
                    st.session_state[f"_view_open_{asset_id}"] = False
                    st.rerun()

        # ── Rename form ──
        if st.session_state.get(f"_rename_open_{asset_id}"):
            with st.form(f"_rename_form_{asset_id}", clear_on_submit=False):
                new_name = st.text_input("New name", value=name, max_chars=80)
                new_desc = st.text_area("Description", value=desc, max_chars=300,
                                         height=70)
                col_rn_ok, col_rn_cancel = st.columns(2)
                with col_rn_ok:
                    if st.form_submit_button("✅ 確認改名", type="primary"):
                        ok = asset_service.rename(
                            asset_id, new_name.strip(),
                            description=new_desc.strip(),
                        )
                        st.toast("已更新" if ok else "更新失敗",
                                  icon="✏️" if ok else "❌")
                        st.session_state[f"_rename_open_{asset_id}"] = False
                        st.rerun()
                with col_rn_cancel:
                    if st.form_submit_button("✖ 取消"):
                        st.session_state[f"_rename_open_{asset_id}"] = False
                        st.rerun()

        # ── Delete confirm ──
        if st.session_state.get(f"_del_confirm_{asset_id}"):
            st.warning(
                f"⚠️ 確定要 soft-delete `{asset_id}`(is_active=False)?"
                "audit trail 保留,可重新啟用。"
            )
            col_d_ok, col_d_cancel = st.columns(2)
            with col_d_ok:
                if st.button("✅ 確認刪除", key=f"_del_ok_{asset_id}",
                              type="primary"):
                    ok = asset_service.delete(asset_id, hard=False)
                    st.toast("已刪除" if ok else "刪除失敗",
                              icon="🗑")
                    st.session_state[f"_del_confirm_{asset_id}"] = False
                    st.rerun()
            with col_d_cancel:
                if st.button("✖ 取消", key=f"_del_cancel_{asset_id}"):
                    st.session_state[f"_del_confirm_{asset_id}"] = False
                    st.rerun()


def _render_asset_tab(asset_type: str, empty_msg: str) -> None:
    """通用 tab 渲染 — 列 assets + render card。"""
    assets = asset_service.list(
        dataset_id=filter_dataset_id,
        asset_type=asset_type,
        owner=filter_owner,
        include_inactive=show_inactive,
        limit=100,
    )
    if not assets:
        st.info(empty_msg)
        return
    st.caption(f"共 {len(assets)} 個 {asset_type}")
    for asset in assets:
        _render_asset_card(asset, asset_type=asset_type)


with tab_chart:
    _render_asset_tab(
        "saved_chart",
        "尚無 Saved Chart — 在 Upload Workspace 跑完分析後按 💾 Save Chart 即可。",
    )

with tab_metric:
    _render_asset_tab(
        "saved_metric",
        "尚無 Saved Metric — 在 Upload Workspace 跑完分析後按 💾 Save Metric 即可。",
    )

with tab_template:
    _render_asset_tab(
        "analysis_template",
        "尚無 Analysis Template — 在 Upload Workspace 跑完分析後按 💾 Save Template 即可。",
    )

st.divider()

# ============================================================
# Replay queue display
# ============================================================
if st.session_state.get("_replay_query"):
    rq = st.session_state["_replay_query"]
    rd = st.session_state.get("_replay_dataset_id", "?")
    st.info(
        f"🔄 **已準備 replay query** for dataset `{rd}`:\n\n"
        f"```\n{rq}\n```\n\n"
        f"請切到 **Upload Workspace** page → 對應 dataset → 貼進 chat input 觸發 rerun。\n\n"
        f"_(MVP 為簡化沒做自動跳轉。)_"
    )
    if st.button("✖ 清掉 replay queue"):
        st.session_state.pop("_replay_query", None)
        st.session_state.pop("_replay_dataset_id", None)
        st.rerun()

st.caption(
    "💡 Rerun 走 replay source_query — LLM 重跑 5-phase,以最新 active metadata "
    "為依據。若 asset.metadata_version 落後 → 上方會顯示 ⚠️ stale 標籤。"
)
