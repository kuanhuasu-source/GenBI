"""
pages/03_prompts.py — v0.3.0+

Streamlit admin page for prompt template management。

# 功能
- 5 個 phase prompt × domain_scope (預設 "*",可加 domain-specific)
- 列當前版本歷史 / current active 標記
- 編輯 Jinja2 template + sample variables 即時預覽
- 儲存為新版本 + activate

# 注意
- 沒接 DB 時純 read-only(embedded fallback,只能看)
- 改 prompt 立即影響下次 LLM call(60s cache 後)
- "Activate" 按鈕會自動 deactivate 同 (key, scope) 其他版本
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st
import config


# ============================================================
# 頁面設定
# ============================================================
# v0.17:set_page_config 改在 app.py 統一設定(st.navigation 規則)
st.markdown(
    "<h1 style='font-size:2.2rem;margin:0 0 .3rem 0'>📝 Prompt Admin</h1>",
    unsafe_allow_html=True,
)
st.caption(f"collection: {config.PROMPT_COLLECTION} · "
           f"MongoDB: {config.MONGO_URI}{config.MONGO_DB}")


# ============================================================
# 連 DB + Repository
# ============================================================
@st.cache_resource
def _get_mongo():
    try:
        from pymongo import MongoClient
        client = MongoClient(
            config.MONGO_URI,
            serverSelectionTimeoutMS=config.MONGO_SERVER_SELECTION_TIMEOUT_MS,
        )
        client.admin.command("ping")
        return client[config.MONGO_DB], None
    except Exception as e:
        return None, str(e)


mongo_db, mongo_err = _get_mongo()

from prompt_repository import build_default_repo, PROMPT_KEYS
import embedded_metadata  # noqa: F401 — merge into EMBEDDED_PROMPTS

prompt_repo = build_default_repo(mongo_db=mongo_db)

if mongo_db is None:
    st.warning(
        f"⚠️ MongoDB 連線失敗 — 純 embedded read-only 模式\n\n錯誤:{mongo_err}"
    )

# ============================================================
# Sidebar - prompt 選擇器
# ============================================================
PROMPT_KEY_LABELS = {
    "phase_0_plan": "Phase 0 · Plan(規劃三階段)",
    "phase_a_pipeline": "Phase A · MongoDB Pipeline",
    "phase_b_preprocess": "Phase B · Pandas Preprocess",
    "phase_c_echarts": "Phase C · ECharts 視覺化",
    "phase_d_insight": "Phase D · 商業洞察",
}

with st.sidebar:
    st.markdown("### 🎯 Prompt")
    selected_key = st.selectbox(
        "Phase",
        options=list(PROMPT_KEY_LABELS.keys()),
        format_func=lambda k: PROMPT_KEY_LABELS.get(k, k),
        index=0,
        label_visibility="collapsed",
    )

    st.markdown("### 🌐 Domain Scope")
    # 列當前 prompt 的所有 scope
    scope_options = ["*"]  # 通用永遠在
    if mongo_db is not None:
        try:
            for s in mongo_db[config.PROMPT_COLLECTION].distinct(
                "domain_scope", {"prompt_key": selected_key}
            ):
                if s not in scope_options:
                    scope_options.append(s)
        except Exception:
            pass

    selected_scope = st.selectbox(
        "Domain scope",
        options=scope_options,
        index=0,
        label_visibility="collapsed",
        help="`*` = 通用模板。domain-specific 模板會覆蓋通用版。",
    )

    st.divider()

    # 顯示當前 active 版本概要
    try:
        current_template = prompt_repo.get_template(selected_key, selected_scope)
        st.metric("Current template", f"{len(current_template):,} chars")
    except Exception:
        st.warning("無法讀當前模板")

    st.divider()
    if st.button("🔄 重整 cache", use_container_width=True):
        prompt_repo.invalidate_all()
        st.rerun()


# ============================================================
# 主區:版本歷史
# ============================================================
st.markdown(
    f"### 📋 `{selected_key}` · scope=`{selected_scope}`"
)

# 列所有版本
versions = []
if mongo_db is not None:
    try:
        versions = prompt_repo.list_versions(selected_key, selected_scope)
    except Exception as e:
        st.error(f"列版本失敗: {e}")

if not versions:
    st.info(
        f"💭 DB 中沒有 `{selected_key}` × scope=`{selected_scope}` 的紀錄。\n\n"
        "目前 LLM service 走 **embedded fallback**。要進 DB 請跑:\n"
        "```bash\npython migrations/001_seed_prompts.py\n```"
    )
else:
    st.markdown(f"**{len(versions)} 個版本** (按 version desc)")

    for v in versions:
        is_active = bool(v.get("is_active"))
        with st.container(border=True):
            cols = st.columns([1, 3, 1, 1, 1])
            cols[0].markdown(
                f"**v{v.get('version', '?')}**"
                + (" 🟢 active" if is_active else "")
            )
            cols[1].caption(
                f"{(v.get('notes') or '(no notes)')[:80]}"
            )
            ts = v.get("created_at")
            cols[2].markdown(
                f"<span style='font-size:0.78rem;color:var(--color-text-secondary)'>"
                f"{str(ts)[:16] if ts else '?'}<br>by {v.get('created_by','?')}"
                f"</span>",
                unsafe_allow_html=True,
            )
            if cols[3].button(
                "👁️ 檢視",
                key=f"view_{v.get('_id')}",
                use_container_width=True,
            ):
                st.session_state.viewing_version = str(v.get("_id"))
                st.rerun()
            if not is_active:
                if cols[4].button(
                    "✅ Activate",
                    key=f"activate_{v.get('_id')}",
                    use_container_width=True,
                    disabled=mongo_db is None,
                ):
                    try:
                        prompt_repo.activate(v["_id"])
                        st.toast(f"✅ 已啟用 v{v['version']}", icon="🎯")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Activate 失敗: {e}")
            else:
                cols[4].markdown(
                    "<div style='text-align:center;padding-top:0.4rem;color:#7DB650;font-weight:600'>active</div>",
                    unsafe_allow_html=True,
                )


# ============================================================
# 檢視 / 編輯區
# ============================================================
st.divider()

# 拿到要編輯的 template — 預設拿 active 版本
current_template = ""
viewing_version_id = st.session_state.get("viewing_version")
viewing_meta = {}
if viewing_version_id and versions:
    # 找指定版本
    from bson import ObjectId
    try:
        for v in versions:
            if str(v.get("_id")) == viewing_version_id:
                current_template = v.get("template", "")
                viewing_meta = v
                break
    except Exception:
        pass

if not current_template:
    try:
        current_template = prompt_repo.get_template(selected_key, selected_scope)
        viewing_meta = {"version": "current active", "notes": ""}
    except Exception:
        current_template = ""

# 標題:顯示在編輯哪個版本
label_suffix = (
    f"v{viewing_meta.get('version')}" if viewing_meta.get("version") else "(current)"
)
st.markdown(f"### ✏️ 編輯 / 檢視 · `{label_suffix}`")

if viewing_meta.get("notes"):
    st.caption(f"📝 Notes: {viewing_meta['notes']}")

# Template editor + variables 預覽
ec_col1, ec_col2 = st.columns([3, 2])
with ec_col1:
    st.markdown("**Jinja2 Template**")
    edited_template = st.text_area(
        "template source",
        value=current_template,
        height=500,
        label_visibility="collapsed",
        help="支援 Jinja2 語法。`{{varname}}` 變數插值,`{ }` 字面括號。",
        key=f"editor_{selected_key}_{selected_scope}",
    )

with ec_col2:
    st.markdown("**🔍 Sample 變數 + 預覽**")
    # 列出該 prompt 預期的變數(從 phase 推斷)
    var_hints = {
        "phase_0_plan": ["domain_knowledge"],
        "phase_a_pipeline": ["domain_knowledge"],
        "phase_b_preprocess": ["cols_info", "domain_knowledge", "dashboard_block"],
        "phase_c_echarts": ["cols_info"],
        "phase_d_insight": ["domain_knowledge"],
    }
    expected_vars = var_hints.get(selected_key, ["domain_knowledge"])

    sample_vars = {}
    for v in expected_vars:
        default = f"<{v} 範例內容>"
        if v == "domain_knowledge":
            default = "### Domain Knowledge\n(metadata 會自動注入這裡)"
        elif v == "cols_info":
            default = "Q 欄位:['company_code', 'count', 'rate']"
        elif v == "dashboard_block":
            default = ""  # default empty
        sample_vars[v] = st.text_area(
            f"{{{{ {v} }}}}",
            value=default,
            height=80,
            key=f"sample_{selected_key}_{v}",
        )

    if st.button("🎨 Render preview", use_container_width=True):
        try:
            from jinja2 import Environment, StrictUndefined
            env = Environment(undefined=StrictUndefined)
            tmpl = env.from_string(edited_template)
            rendered = tmpl.render(**sample_vars)
            with st.expander("✅ Rendered output", expanded=True):
                st.code(rendered, language="text")
                st.caption(f"輸出長度:{len(rendered):,} chars")
        except Exception as e:
            st.error(f"❌ Render 失敗: {e}")


# ============================================================
# 儲存區
# ============================================================
st.divider()
st.markdown("### 💾 儲存為新版本")

if mongo_db is None:
    st.warning("⚠️ DB 沒接,無法儲存。")
else:
    save_col1, save_col2 = st.columns([3, 1])
    with save_col1:
        save_notes = st.text_input(
            "Notes (描述這版改了什麼)",
            placeholder="例:加 rule 5.6 強化 stacked bar pivot,fix few_shot injection",
        )
    with save_col2:
        save_activate = st.checkbox("儲存後立即啟用", value=True)

    sc1, sc2 = st.columns(2)
    if sc1.button(
        "💾 儲存新版本",
        type="primary",
        use_container_width=True,
        disabled=not edited_template.strip(),
    ):
        if edited_template == current_template:
            st.warning("⚠️ 與目前內容一致,沒變化")
        elif not save_notes.strip():
            st.error("❌ 請填 notes 描述這版的改動")
        else:
            try:
                new_id = prompt_repo.save_new_version(
                    prompt_key=selected_key,
                    domain=selected_scope,
                    template=edited_template,
                    notes=save_notes.strip(),
                    created_by="admin_ui",
                    activate=save_activate,
                )
                msg = f"✅ 已儲存新版本"
                if save_activate:
                    msg += " 並啟用"
                st.toast(msg, icon="💾")
                st.session_state.viewing_version = None
                st.rerun()
            except Exception as e:
                st.error(f"❌ 儲存失敗: {e}")

    if sc2.button("✖ 清掉檢視狀態", use_container_width=True):
        if "viewing_version" in st.session_state:
            del st.session_state.viewing_version
        st.rerun()
