"""
pages/07_upload_workspace.py — v0.12.0+

Upload Workspace — 上傳 CSV / Excel single sheet,自動解析 + profile + 顯示。

# Milestone 1B 範圍
- 檔案上傳
- 解析 → parquet → upload_tables / upload_profiles 寫 MongoDB
- 顯示 sample data(前 100 列)
- 顯示 column-level profile(physical type / null pct / distinct / stats / warnings)
- 既有 dataset list 與刪除

# 不在 Milestone 1B 範圍
- Semantic role 推論(M2)
- Field Review UI / Status Code Editor / Grain Confirmation(M2)
- 對該 dataset 聊天分析(M3)
- Saved Chart / Metric(M3A)

# Page 編號說明
Spec §7 預期 `05_upload_workspace.py`,但既有 `05_task_traces.py` / `06_learning_review.py`
已佔用 05/06,因此 upload pages 從 07 起。Streamlit 純粹按檔名排序,不影響功能。
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
from upload_service import UploadService
from upload_analysis_service import UploadAnalysisService
from analysis_asset_service import AnalysisAssetService
from metadata_correction_service import MetadataCorrectionService
from metadata_provider import UploadMetadataProvider
from semantic_profiler import SEMANTIC_ROLES, ROLE_PROPERTIES
from upload_metadata_generator import summarize_confidence
from llm_service import LLMService
import file_parser

# v0.10.0+: composite chart layout + ECharts renderer(reuse 主 app 的 layer)
from streamlit_echarts import st_echarts

# ============================================================
# 頁面設定
# ============================================================
st.set_page_config(
    page_title="GenBI · Upload Workspace",
    page_icon="📤",
    layout="wide",
)
st.markdown(
    "<h1 style='font-size:2.2rem;margin:0 0 .3rem 0'>📤 Upload Workspace</h1>"
    "<p style='color:#8B6F4A;font-size:0.95rem;margin:0 0 1rem 0'>"
    "BYOD · 上傳 CSV / Excel,自動解析欄位、產生 profile,Milestone 2 後接 GenBI 分析流程。"
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
        f"⚠️ **MongoDB 連線失敗** — Upload Workspace 需要 DB 才能運作。\n\n"
        f"錯誤訊息:`{mongo_err or 'unknown'}`\n\n"
        "請先 `brew services start mongodb-community` 再 refresh 此頁。"
    )
    st.stop()


@st.cache_resource(show_spinner=False)
def _get_upload_service(_db):
    repo = UploadRepository(_db)
    repo.ensure_indexes()
    uploads_root = _PROJECT_ROOT / "uploads"
    return UploadService(upload_repo=repo, uploads_root=uploads_root), repo


service, repo = _get_upload_service(mongo_db)

# ============================================================
# Sidebar
# ============================================================
with st.sidebar:
    st.markdown("### 📤 Upload Workspace")
    st.caption("Milestone 1B · Upload + Parse + Profile")
    st.markdown("---")
    st.caption(
        "💡 **下個 milestone**\n\n"
        "M2 — Semantic profiler + Field Review UI · 讓 AI 推欄位語意 + 使用者確認\n\n"
        "M3 — 接入 GenBI workflow · 對上傳資料聊天分析\n\n"
        "M3A — Saved Chart / Metric · 沉澱分析資產"
    )


# ============================================================
# 1️⃣ Upload section
# ============================================================
st.markdown("### 1️⃣ 上傳新檔案")

col_up_l, col_up_r = st.columns([3, 2])
with col_up_l:
    uploaded = st.file_uploader(
        "選擇 CSV 或 Excel(single sheet)— 上限 100MB",
        type=["csv", "xlsx", "xls"],
        accept_multiple_files=False,
        help=(
            "支援 `.csv` / `.xlsx` / `.xls`。"
            "Excel multi-sheet 目前只讀第一個 sheet(Phase 2 才支援多 sheet)。"
        ),
    )
with col_up_r:
    owner_input = st.text_input(
        "上傳者識別(audit log 用)",
        value=st.session_state.get("_upload_owner", "anonymous"),
        help="MVP 簡化:直接打字。後續會接 Streamlit auth。",
    )
    if owner_input != st.session_state.get("_upload_owner"):
        st.session_state["_upload_owner"] = owner_input

if uploaded is not None:
    file_size = len(uploaded.getbuffer())
    st.caption(
        f"📎 `{uploaded.name}` · {file_size:,} bytes · "
        f"type=`{uploaded.type or '?'}`"
    )
    if st.button("🚀 開始解析", type="primary", use_container_width=True):
        with st.spinner("📦 解析中..."):
            try:
                dataset_id = service.handle_upload(
                    file_obj=uploaded.getvalue(),
                    filename=uploaded.name,
                    owner=owner_input or "anonymous",
                )
                st.success(
                    f"✅ 上傳完成 · `{dataset_id}` · 已下方 dataset 列表查看"
                )
                # 強制 rerun 讓 list 立刻更新
                st.session_state["_just_uploaded"] = dataset_id
                st.rerun()
            except file_parser.FileParseError as e:
                st.error(f"❌ 檔案解析失敗:{e}")
            except Exception as e:
                st.error(
                    f"❌ Upload 流程錯誤:`{type(e).__name__}`\n\n"
                    f"```\n{e}\n```"
                )
                import traceback
                with st.expander("🔍 Traceback"):
                    st.code(traceback.format_exc(), language="bash")

st.divider()

# ============================================================
# 2️⃣ Existing datasets
# ============================================================
st.markdown("### 2️⃣ 已上傳資料集")

datasets = repo.list_datasets(limit=50)
if not datasets:
    st.info("尚無資料集 — 上傳一份 CSV / Excel 開始。")
    st.stop()

# Dataset selector + summary
just_uploaded = st.session_state.get("_just_uploaded", "")
default_idx = 0
options = [d["_id"] for d in datasets]
labels = [
    f"{d['_id'][:30]}... · {d.get('dataset_name', '?')} · "
    f"{d.get('status', '?')}"
    if len(d["_id"]) > 30 else
    f"{d['_id']} · {d.get('dataset_name', '?')} · {d.get('status', '?')}"
    for d in datasets
]
if just_uploaded in options:
    default_idx = options.index(just_uploaded)

selected_id = st.selectbox(
    "選擇要檢視的 dataset",
    options=options,
    index=default_idx,
    format_func=lambda x: labels[options.index(x)],
    key="_dataset_selector",
)

if not selected_id:
    st.stop()

dataset = repo.get_dataset(selected_id)
if not dataset:
    st.error(f"❌ dataset `{selected_id}` 不存在")
    st.stop()

# ── Dataset 摘要 ──
col_a, col_b, col_c, col_d = st.columns(4)
with col_a:
    st.metric("狀態", dataset.get("status", "—"))
with col_b:
    file = dataset.get("file") or {}
    st.metric("檔案大小", f"{file.get('file_size_bytes', 0):,} B")
with col_c:
    st.metric("檔案類型", file.get("file_type") or "—")
with col_d:
    md_v = dataset.get("active_metadata_version")
    st.metric("Metadata version", md_v if md_v is not None else "—(待 M2)")

# 錯誤訊息
if dataset.get("status") == "error":
    st.error(
        f"❌ 此 dataset 解析失敗:`{dataset.get('error_message', '?')}`"
    )

# Details
with st.expander("📄 Dataset 詳細", expanded=False):
    st.json(dataset, expanded=False)

# 刪除按鈕
with st.expander("🗑️ 刪除此 dataset(危險)", expanded=False):
    st.warning(
        "⚠️ 此操作會刪除 MongoDB 記錄 + 本機檔案,無法復原。"
    )
    confirm_text = st.text_input(
        "輸入 dataset_id 後半段 6 hex 確認(例如 `a8c3f2`)",
        key=f"_del_confirm_{selected_id}",
    )
    expected = selected_id.rsplit("_", 1)[-1] if "_" in selected_id else ""
    if st.button(
        "✖ 確認刪除",
        type="secondary",
        disabled=(confirm_text != expected),
        key=f"_del_btn_{selected_id}",
    ):
        ok = service.delete_dataset(selected_id)
        if ok:
            st.toast(f"已刪除 {selected_id}", icon="🗑️")
            st.session_state.pop("_just_uploaded", None)
            st.rerun()
        else:
            st.error("刪除失敗,請看後台 log")

st.divider()

# ============================================================
# 3️⃣ Table list + sample data + profile
# ============================================================
tables = repo.list_tables(selected_id)
if not tables:
    if dataset.get("status") != "error":
        st.info("此 dataset 尚無 table(可能還在解析中)。")
    st.stop()

# MVP 通常只有 1 table,但設計上支援多 sheet(Phase 2)
for table in tables:
    table_id = table["table_id"]
    st.markdown(
        f"### 3️⃣ Table: `{table_id}` · "
        f"**{table['row_count']:,}** 列 × **{table['column_count']}** 欄"
    )
    if table.get("warnings"):
        for w in table["warnings"]:
            st.warning(f"⚠️ {w}")

    # Sample data
    try:
        df = file_parser.load_parquet(table["storage"]["path"])
        st.markdown("**📊 前 100 列 sample data**")
        st.dataframe(df.head(100), use_container_width=True, height=320)
    except Exception as e:
        st.error(f"讀 parquet 失敗:{e}")
        continue

    # Profile
    profile = repo.get_latest_profile(selected_id)
    if not profile:
        st.warning("尚無 profile(可能還在跑)")
        continue

    # 找對應這個 table 的 profile 區塊
    table_profile = None
    for tp in profile.get("tables", []):
        if tp.get("table_id") == table_id:
            table_profile = tp
            break
    if not table_profile:
        st.warning(f"profile 中找不到 `{table_id}`")
        continue

    st.markdown("**🔍 Column profile**")

    # 把 column profile 轉成 DataFrame 顯示
    rows = []
    for col_prof in table_profile.get("columns", []):
        rows.append({
            "Column": col_prof.get("name", "?"),
            "Type": col_prof.get("physical_type", "?"),
            "Null %": col_prof.get("null_pct", 0),
            "Distinct": col_prof.get("distinct_count"),
            "Distinct %": col_prof.get("distinct_pct"),
            "Min": col_prof.get("min"),
            "Max": col_prof.get("max"),
            "Mean": col_prof.get("mean"),
            "Median": col_prof.get("median"),
            "P95": col_prof.get("p95"),
            "Sample values": ", ".join(
                str(v) for v in (col_prof.get("sample_values") or [])[:5]
            ),
            "Warnings": ", ".join(col_prof.get("warnings", [])) or "—",
        })
    prof_df = pd.DataFrame(rows)
    st.dataframe(
        prof_df,
        use_container_width=True,
        height=min(35 + len(rows) * 35, 600),
        hide_index=True,
    )

    # Top values 區(per-column expander)
    st.markdown("**📈 String 欄 top values**")
    string_cols = [
        c for c in table_profile.get("columns", [])
        if c.get("physical_type") == "string" and c.get("top_values")
    ]
    if not string_cols:
        st.caption("無 string 欄,或無 top_values 資料。")
    else:
        cols = st.columns(min(3, len(string_cols)))
        for i, c in enumerate(string_cols[:6]):
            with cols[i % 3]:
                with st.expander(f"`{c['name']}`", expanded=False):
                    tv_df = pd.DataFrame(c["top_values"])
                    st.dataframe(tv_df, use_container_width=True,
                                 hide_index=True)

st.divider()

# ============================================================
# 4️⃣ — 8️⃣ · Metadata Review(M2 新增)
# ============================================================
correction_svc = MetadataCorrectionService(repo)
active_meta_doc = repo.get_active_metadata(selected_id)

if not active_meta_doc:
    st.warning(
        "⚠️ 此 dataset 尚無 metadata version。可能是 upload 時 metadata 階段失敗,"
        "點下方按鈕重新產生。"
    )
    if st.button("🔄 重新產生 metadata(rule-based)"):
        try:
            with st.spinner("跑 rule-based semantic profiler..."):
                v = service.regenerate_metadata(selected_id, use_llm=False)
            st.success(f"✅ 已產生 metadata v{v}")
            st.rerun()
        except Exception as e:
            st.error(f"❌ {e}")
    st.stop()

# ────────────────────────────────────────
# Section 4 · Metadata Review header + version controls
# ────────────────────────────────────────
metadata = active_meta_doc["metadata"]
md_version = active_meta_doc["version"]
md_status = active_meta_doc.get("confirmation_status", "draft")
conf_summary = summarize_confidence(metadata)

st.markdown("### 4️⃣ Metadata Review")

col_v, col_s, col_h, col_m, col_l = st.columns([1.3, 1.4, 1, 1, 1])
with col_v:
    st.metric("Active Version", f"v{md_version}")
with col_s:
    if md_status == "confirmed":
        st.markdown(
            "<div style='padding-top:8px'><span style='background:#1F7F4E;"
            "color:white;padding:4px 12px;border-radius:12px;font-size:0.85rem;"
            "font-weight:600;'>✅ Confirmed</span></div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            "<div style='padding-top:8px'><span style='background:#D9342B;"
            "color:white;padding:4px 12px;border-radius:12px;font-size:0.85rem;"
            "font-weight:600;'>⚠️ Unconfirmed</span></div>",
            unsafe_allow_html=True,
        )
with col_h:
    st.metric("High confidence", conf_summary["high_confidence_fields"])
with col_m:
    st.metric("Medium", conf_summary["medium_confidence_fields"])
with col_l:
    st.metric("Low", conf_summary["low_confidence_fields"])

# Refine with LLM + version history expander
col_refine, col_history = st.columns([1, 1])
with col_refine:
    if st.button(
        "🤖 用 LLM 補強低 confidence 欄位",
        help=(
            f"對 confidence < 0.7 的欄位送一次 LLM 重新判斷;"
            f"使用模型 `{config.LLM_MODEL}` @ `{config.LLM_BASE_URL}`"
        ),
        use_container_width=True,
    ):
        try:
            with st.spinner("送 LLM 中..."):
                new_v = service.regenerate_metadata(
                    selected_id,
                    use_llm=True,
                    api_url=config.LLM_API_URL,
                    api_key=config.LLM_API_KEY,
                    model=config.LLM_MODEL,
                    timeout_s=config.LLM_TIMEOUT_S,
                )
            st.success(f"✅ LLM refine 完成,新 metadata v{new_v}")
            st.rerun()
        except Exception as e:
            st.error(f"❌ LLM refine 失敗:{e}")
with col_history:
    with st.expander("📜 Metadata version 歷史", expanded=False):
        versions = repo.list_metadata_versions(selected_id)
        for v_doc in versions:
            tag = "✅" if v_doc.get("confirmation_status") == "confirmed" else "📝"
            active_tag = " (active)" if v_doc.get("is_active") else ""
            st.caption(
                f"{tag} v{v_doc['version']}{active_tag} · "
                f"`{v_doc.get('confirmation_status', '?')}` · "
                f"{v_doc.get('confirmed_by') or 'system'} · "
                f"{(v_doc.get('notes') or '')[:80]}"
            )

# ────────────────────────────────────────
# Section 5 · Field Review Table(可編輯)
# ────────────────────────────────────────
# 取出 M3 之後實際會用的 single table
md_table_id = list(metadata["collections"].keys())[0]
md_coll = metadata["collections"][md_table_id]
md_fields = md_coll["fields"]

st.markdown(f"#### 5️⃣ Field Review · `{md_table_id}`")
st.caption(
    "下表可編輯 `semantic_role` / `description` / `unit` / `default_aggregation`。"
    "按下方 **Apply edits** 才寫入新 metadata version。"
)

# 把 fields dict 轉成 dataframe 給 data_editor
field_rows = []
for col_name, f in md_fields.items():
    field_rows.append({
        "column": col_name,
        "type": f.get("type", "?"),
        "semantic_role": f.get("semantic_role", "unknown"),
        "description": f.get("description", ""),
        "unit": f.get("unit", ""),
        "default_aggregation": f.get("default_aggregation", "no_agg"),
        "is_dimension": f.get("is_dimension", False),
        "is_measure": f.get("is_measure", False),
        "is_identifier": f.get("is_identifier", False),
        "confidence": f.get("confidence", 0.0),
        "user_confirmed": f.get("user_confirmed", False),
        "warnings": ", ".join(f.get("warnings", [])) or "—",
    })

field_df = pd.DataFrame(field_rows)

edited_df = st.data_editor(
    field_df,
    use_container_width=True,
    num_rows="fixed",
    column_config={
        "column": st.column_config.TextColumn("Column", disabled=True),
        "type": st.column_config.TextColumn("Physical type", disabled=True),
        "semantic_role": st.column_config.SelectboxColumn(
            "Semantic role",
            options=list(SEMANTIC_ROLES),
            help="改了之後 default_aggregation 等會連動 update",
        ),
        "description": st.column_config.TextColumn(
            "Description", max_chars=150,
        ),
        "unit": st.column_config.TextColumn(
            "Unit", help="例:days / percent / ratio / count",
        ),
        "default_aggregation": st.column_config.SelectboxColumn(
            "Default agg",
            options=["sum", "avg", "median", "min", "max", "p95",
                     "count", "count_distinct", "no_agg"],
        ),
        "is_dimension": st.column_config.CheckboxColumn(
            "is_dim", disabled=True,
        ),
        "is_measure": st.column_config.CheckboxColumn(
            "is_msr", disabled=True,
        ),
        "is_identifier": st.column_config.CheckboxColumn(
            "is_id", disabled=True,
        ),
        "confidence": st.column_config.ProgressColumn(
            "Confidence", min_value=0, max_value=1, format="%.2f",
        ),
        "user_confirmed": st.column_config.CheckboxColumn(
            "Confirmed", disabled=True,
        ),
        "warnings": st.column_config.TextColumn("Warnings", disabled=True),
    },
    key=f"_field_editor_{selected_id}_{md_version}",
    hide_index=True,
)

# ────────────────────────────────────────
# Section 6 · Grain + Primary key Confirmation
# ────────────────────────────────────────
st.markdown("#### 6️⃣ Grain + Primary Key Confirmation")

current_pk = md_coll.get("primary_key") or "—"
current_grain = md_coll.get("grain") or "—"

st.caption(
    f"系統推論:每列代表一個 **{current_pk}**(grain: `{current_grain}`)"
)

col_pk, col_gr = st.columns(2)
with col_pk:
    pk_options = ["(沿用 AI 推論)"] + [c["column"] for c in field_rows] + ["(無 primary key)"]
    pk_choice = st.selectbox(
        "Primary key",
        options=pk_options,
        index=0,
        key=f"_pk_choice_{selected_id}_{md_version}",
    )
with col_gr:
    grain_choice = st.text_input(
        "Grain 描述",
        value=current_grain,
        help="自由文字。例:每列代表一筆申請 / 一個 project / 一日 snapshot",
        key=f"_grain_choice_{selected_id}_{md_version}",
    )

# ────────────────────────────────────────
# Section 7 · Status Code Editor(per categorical_status)
# ────────────────────────────────────────
status_col_names = [
    c["column"] for c in field_rows
    if c["semantic_role"] == "categorical_status"
]
if status_col_names:
    st.markdown("#### 7️⃣ Status Code Editor")
    st.caption(
        "對每個 `categorical_status` 欄位,列出已偵測到的 allowed values 與計數。"
        "可在 description 上補語意(例 `D = Delayed`),Apply 後寫進 metadata。"
    )
    status_corrections: dict[str, dict] = {}
    for sc in status_col_names:
        f = md_fields.get(sc) or {}
        av = f.get("allowed_values") or {}
        with st.expander(f"📊 `{sc}` allowed values", expanded=False):
            if isinstance(av, dict) and av:
                rows = [
                    {
                        "value": k,
                        "count": v.get("count", "?") if isinstance(v, dict) else "—",
                        "description": (v.get("description")
                                        if isinstance(v, dict) else "") or "",
                    }
                    for k, v in av.items()
                ]
                edited_status = st.data_editor(
                    pd.DataFrame(rows),
                    use_container_width=True,
                    num_rows="fixed",
                    column_config={
                        "value": st.column_config.TextColumn(
                            "Value", disabled=True,
                        ),
                        "count": st.column_config.NumberColumn(
                            "Count", disabled=True,
                        ),
                        "description": st.column_config.TextColumn(
                            "Meaning",
                            help="例:Y = Yes, R = Rejected, X = Unknown",
                        ),
                    },
                    key=f"_status_edit_{selected_id}_{md_version}_{sc}",
                    hide_index=True,
                )
                status_corrections[sc] = edited_status
            else:
                st.caption("此欄位無 allowed_values 資料(或不是 categorical)")
else:
    status_corrections = {}

# ────────────────────────────────────────
# Section 8 · Data Limitation Editor
# ────────────────────────────────────────
st.markdown("#### 8️⃣ Data Limitation Editor")
st.caption(
    "系統自動推論的限制。可調整 — 例如本來推論「無日期欄」,但你想指定 `hire_date` 當日期欄解除。"
)

lim = metadata.get("data_limitations", {})
col_missing, col_not_supp = st.columns(2)
with col_missing:
    missing_str = "\n".join(lim.get("missing_dimensions") or [])
    missing_edited = st.text_area(
        "Missing dimensions(一行一條)",
        value=missing_str,
        height=120,
        key=f"_lim_missing_{selected_id}_{md_version}",
    )
with col_not_supp:
    notsupp_str = "\n".join(lim.get("not_supported_analysis") or [])
    notsupp_edited = st.text_area(
        "Not supported analysis(一行一條)",
        value=notsupp_str,
        height=120,
        key=f"_lim_notsupp_{selected_id}_{md_version}",
    )

# ────────────────────────────────────────
# Section 9 · Apply / Confirm buttons
# ────────────────────────────────────────
st.markdown("#### 9️⃣ Apply / Confirm")

# 收集所有 corrections
def _collect_corrections() -> list[dict]:
    corrections: list[dict] = []

    # Field-level edits — 比較 edited_df 與原 field_df
    for orig, edited in zip(field_rows, edited_df.to_dict("records")):
        col = orig["column"]
        for attr in ("semantic_role", "description", "unit",
                       "default_aggregation"):
            ov = orig.get(attr)
            nv = edited.get(attr)
            if ov != nv:
                corrections.append({
                    "target": f"{md_table_id}.{col}.{attr}",
                    "old_value": ov,
                    "new_value": nv,
                    "reason": "User edited in Field Review Table",
                })

    # Primary key
    if pk_choice == "(無 primary key)":
        if md_coll.get("primary_key") is not None:
            corrections.append({
                "target": f"primary_key.{md_table_id}",
                "old_value": md_coll.get("primary_key"),
                "new_value": None,
                "reason": "User cleared primary key",
            })
    elif pk_choice != "(沿用 AI 推論)":
        if pk_choice != md_coll.get("primary_key"):
            corrections.append({
                "target": f"primary_key.{md_table_id}",
                "old_value": md_coll.get("primary_key"),
                "new_value": pk_choice,
                "reason": "User chose primary key",
            })

    # Grain
    if grain_choice != md_coll.get("grain"):
        corrections.append({
            "target": f"grain.{md_table_id}",
            "old_value": md_coll.get("grain"),
            "new_value": grain_choice,
            "reason": "User edited grain text",
        })

    # Status codes — 給每個 categorical_status 欄位寫 allowed_values 的 description
    for sc, edited_status in status_corrections.items():
        new_av: dict = {}
        original_av = md_fields.get(sc, {}).get("allowed_values") or {}
        for row in edited_status.to_dict("records"):
            v = row["value"]
            desc = row.get("description") or ""
            if isinstance(original_av, dict):
                old_entry = original_av.get(v, {})
                new_av[v] = {
                    "count": (old_entry.get("count")
                              if isinstance(old_entry, dict)
                              else None),
                    "description": desc,
                }
        if new_av != original_av:
            corrections.append({
                "target": f"{md_table_id}.{sc}.allowed_values",
                "old_value": original_av,
                "new_value": new_av,
                "reason": "User edited status code descriptions",
            })

    # Data limitations
    new_missing = [
        l.strip() for l in (missing_edited or "").splitlines() if l.strip()
    ]
    if new_missing != (lim.get("missing_dimensions") or []):
        corrections.append({
            "target": "data_limitations.missing_dimensions",
            "old_value": lim.get("missing_dimensions") or [],
            "new_value": new_missing,
            "reason": "User edited missing_dimensions",
        })
    new_notsupp = [
        l.strip() for l in (notsupp_edited or "").splitlines() if l.strip()
    ]
    if new_notsupp != (lim.get("not_supported_analysis") or []):
        corrections.append({
            "target": "data_limitations.not_supported_analysis",
            "old_value": lim.get("not_supported_analysis") or [],
            "new_value": new_notsupp,
            "reason": "User edited not_supported_analysis",
        })
    return corrections

pending_corrections = _collect_corrections()

if pending_corrections:
    st.info(
        f"📝 偵測到 {len(pending_corrections)} 個變更未儲存。"
    )
    with st.expander(f"檢視 {len(pending_corrections)} 個變更", expanded=False):
        for c in pending_corrections:
            st.caption(
                f"• `{c['target']}`:`{c['old_value']}` → `{c['new_value']}`"
            )
else:
    st.caption("✨ 無變更")

col_apply, col_confirm = st.columns(2)
with col_apply:
    if st.button(
        f"✏️ Apply edits(寫 draft v{md_version + 1})",
        disabled=not pending_corrections,
        use_container_width=True,
        type="primary" if pending_corrections else "secondary",
    ):
        try:
            user = st.session_state.get("_upload_owner", "anonymous")
            result = correction_svc.apply_corrections(
                dataset_id=selected_id,
                corrections=pending_corrections,
                user=user,
                confirm=False,
            )
            st.success(
                f"✅ 已寫 v{result['version']} draft · "
                f"applied={result['applied']} / skipped={result['skipped']}"
            )
            if result["skipped_targets"]:
                st.caption(f"⚠️ skipped: {result['skipped_targets']}")
            st.rerun()
        except Exception as e:
            st.error(f"❌ Apply 失敗:{e}")
            import traceback
            with st.expander("Traceback"):
                st.code(traceback.format_exc())

with col_confirm:
    if md_status == "confirmed":
        st.success("✅ 此 version 已 confirmed")
    else:
        if st.button(
            f"✅ Confirm metadata v{md_version}",
            disabled=bool(pending_corrections),
            help=(
                "把當前 active version 標 confirmed。"
                "若還有未 apply 的編輯,先 Apply edits 再 Confirm。"
            ),
            use_container_width=True,
            type="primary" if not pending_corrections else "secondary",
        ):
            try:
                user = st.session_state.get("_upload_owner", "anonymous")
                result = correction_svc.confirm_metadata(
                    dataset_id=selected_id,
                    user=user,
                )
                st.toast(f"✅ Confirmed v{result['version']}", icon="✅")
                st.rerun()
            except Exception as e:
                st.error(f"❌ Confirm 失敗:{e}")

st.divider()

# ────────────────────────────────────────
# 開發者面板 · 整份 metadata raw JSON
# ────────────────────────────────────────
with st.expander("🔍 Raw metadata JSON(developer / debug)", expanded=False):
    st.json(metadata, expanded=False)

st.divider()

# ============================================================
# 🔟 · Chat Analysis(M3 新增)
# ============================================================
# 只在 metadata confirmed 後才開放(對齊 spec §10.7 acceptance criteria #6)
st.markdown("### 🔟 Chat Analysis")

if md_status != "confirmed":
    st.warning(
        "⚠️ **Metadata 尚未確認** — 請先在 9️⃣ Apply / Confirm 區段按 "
        "**✅ Confirm metadata** 後才能開始分析。"
        "未確認的 metadata 進入分析會有欄位語意被誤判的風險。"
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
# 10.5 · Chat input
# ────────────────────────────────────────
query = st.chat_input(
    "對此 dataset 提出分析問題(例如:畫 leadtime 的分佈,標示平均、中位數、P95)"
)

if query:
    with st.chat_message("user"):
        st.write(query)

    with st.chat_message("assistant"):
        status_box = st.status(
            f"🧠 處理中:{query[:60]}{'…' if len(query) > 60 else ''}",
            expanded=True,
        )
        try:
            with status_box:
                st.write("🚦 Pre-Phase 0 intent + Phase 0 plan...")
                result = analysis_service.handle_query(
                    session_id=active_sid,
                    query=query,
                    chart_engine=chart_engine,
                    enable_insight=enable_insight,
                )

            # 處理 result
            if result["status"] == "meta":
                status_box.update(
                    label=f"✅ Meta intent: {result['intent']}",
                    state="complete", expanded=False,
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
                if result.get("phase_a_code"):
                    with st.expander("Phase A 最後一版 code"):
                        st.code(result["phase_a_code"], language="python")
                if result.get("phase_b_code"):
                    with st.expander("Phase B 最後一版 code"):
                        st.code(result["phase_b_code"], language="python")

            elif result["status"] == "completed":
                status_box.update(
                    label="✅ 分析完成", state="complete", expanded=False,
                )
                if result.get("is_followup"):
                    st.caption("🔗 偵測為延續性分析")
                # Plan
                with st.expander("📋 Plan", expanded=False):
                    st.markdown(result["plan_text"])
                # Phase A
                with st.expander(
                    f"🪛 Phase A · Pandas filter "
                    f"(撈出 {result['raw_df_info']['n_rows']:,} 列)",
                    expanded=False,
                ):
                    st.code(result["phase_a_code"], language="python")
                # Phase B
                with st.expander(
                    f"🐍 Phase B · Preprocess "
                    f"(Q.shape={result['Q_info']['n_rows']} × "
                    f"{len(result['Q_info']['columns'])})",
                    expanded=False,
                ):
                    st.code(result["phase_b_code"], language="python")
                # Q dataframe
                with st.expander(f"📊 Q ({result['Q_info']['n_rows']:,} 列)",
                                  expanded=False):
                    st.dataframe(result["Q"].head(200),
                                  use_container_width=True)
                # Phase C
                if result.get("phase_c_code"):
                    with st.expander("🎨 Phase C · Chart code", expanded=False):
                        st.code(result["phase_c_code"], language="python")
                # 圖表
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
                # Phase D
                if result.get("insight"):
                    with st.expander("🧠 商業洞察", expanded=True):
                        st.markdown(result["insight"])
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

st.caption(
    "✅ M3A — Saved Chart / Saved Metric / Analysis Template + Saved Assets Panel"
)
