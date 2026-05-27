"""
pages/04_metadata.py — v0.3.0+

Streamlit admin page for domain metadata management。

# 功能
- Domain 選擇器(列當前所有 active domains)
- 顯示當前 metadata 的結構摘要(collections / KPIs / limitations / charting)
- 切換到 JSON 編輯模式,修改後存為新版本
- 啟用某版本(自動 deactivate 同 domain 其他版本)
- 新增 domain — 提供 metadata JSON 後建立第一版

# 注意
- Metadata 是 LLM agent 的真實依據,編輯前請確認 schema 結構
- 沒接 DB 時純 read-only
- 新版啟用後 60s cache 內舊版仍可能被讀(等到 cache expire 或重啟)
"""

from __future__ import annotations

import json
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
    "<h1 style='font-size:2.2rem;margin:0 0 .3rem 0'>🗂️ Metadata Admin</h1>",
    unsafe_allow_html=True,
)
st.caption(f"collection: {config.METADATA_COLLECTION} · "
           f"MongoDB: {config.MONGO_URI}{config.MONGO_DB}")


# ============================================================
# 連 DB
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

from prompt_repository import build_default_repo
import embedded_metadata  # noqa: F401

prompt_repo = build_default_repo(mongo_db=mongo_db)

if mongo_db is None:
    st.warning(
        f"⚠️ MongoDB 連線失敗 — 純 embedded read-only 模式\n\n錯誤:{mongo_err}"
    )


# ============================================================
# Sidebar
# ============================================================
with st.sidebar:
    st.markdown("### 🌐 Domain")
    available = prompt_repo.list_active_domains() or ["tflex"]
    # tflex 永遠優先 default
    if "metadata_admin_domain" not in st.session_state:
        st.session_state.metadata_admin_domain = (
            "tflex" if "tflex" in available else available[0]
        )

    domain = st.selectbox(
        "Active domain",
        options=available + ["__new__"],
        format_func=lambda d: "➕ 新增 domain..." if d == "__new__" else d,
        index=available.index(st.session_state.metadata_admin_domain)
        if st.session_state.metadata_admin_domain in available else 0,
        label_visibility="collapsed",
    )
    if domain != "__new__":
        st.session_state.metadata_admin_domain = domain

    st.divider()
    if st.button("🔄 重整 cache", use_container_width=True):
        prompt_repo.invalidate_all()
        st.rerun()


# ============================================================
# 主區
# ============================================================
if domain == "__new__":
    st.markdown("### ➕ 新增 domain")
    st.info(
        "需要的最小 metadata structure(可參照 `tflex_task_metadata_agent_v3.py`):\n"
        "- `dataset_name` / `dataset_id`\n"
        "- `collections` (含 fields)\n"
        "- `kpi_definitions`\n"
        "- `data_limitations`\n"
        "- `recommended_charts` / `charting_guidance`"
    )

    new_domain_name = st.text_input(
        "Domain name (英文,no spaces)",
        placeholder="例:ecommerce / healthcare / hr_attendance",
    )
    new_metadata_json = st.text_area(
        "Metadata JSON",
        value=json.dumps({
            "dataset_name": "範例 dataset",
            "dataset_id": "example_v1",
            "business_context": {"business_description": "..."},
            "collections": {
                "example_coll": {
                    "description": "...",
                    "fields": {"some_field": {"type": "string"}}
                }
            },
            "kpi_definitions": {
                "total_count": {"name": "總筆數", "formula": "count of documents"}
            },
            "data_limitations": {"missing_dimensions": [], "not_supported_analysis": []},
            "recommended_charts": {},
        }, indent=2, ensure_ascii=False),
        height=400,
    )
    notes = st.text_input("Notes")

    if st.button("✅ 建立新 domain",
                 disabled=mongo_db is None or not new_domain_name.strip(),
                 type="primary"):
        try:
            parsed = json.loads(new_metadata_json)
            new_id = prompt_repo.save_new_metadata_version(
                domain=new_domain_name.strip(),
                metadata=parsed,
                notes=notes or "First metadata version",
                created_by="admin_ui",
                activate=True,
            )
            st.toast(f"✅ 已建立 domain {new_domain_name}", icon="🌐")
            st.session_state.metadata_admin_domain = new_domain_name.strip()
            st.rerun()
        except json.JSONDecodeError as e:
            st.error(f"❌ JSON 解析失敗: {e}")
        except Exception as e:
            st.error(f"❌ 建立失敗: {e}")

else:
    # 現有 domain 的編輯模式
    try:
        current_metadata = prompt_repo.get_metadata(domain)
    except KeyError:
        st.error(f"❌ Domain `{domain}` 沒有 metadata")
        st.stop()

    st.markdown(f"### 📦 `{domain}` · current active metadata")

    # 摘要 metric
    mc1, mc2, mc3, mc4 = st.columns(4)
    mc1.metric("Collections",
               len((current_metadata.get("collections") or {})))
    mc2.metric("KPIs",
               len((current_metadata.get("kpi_definitions") or {})))
    n_lim = sum(
        len(v) if isinstance(v, list) else (1 if v else 0)
        for v in (current_metadata.get("data_limitations") or {}).values()
    )
    mc3.metric("Limitations", n_lim)
    mc4.metric(
        "Recommended charts",
        len(
            (
                (current_metadata.get("charting_guidance") or {})
                .get("recommended_charts") or {}
            )
            or (current_metadata.get("recommended_charts") or {})
        ),
    )

    # 結構摘要(各 section 折疊)
    sections_to_show = [
        ("📊 collections", "collections"),
        ("📐 kpi_definitions", "kpi_definitions"),
        ("🚫 data_limitations", "data_limitations"),
        ("🎨 charting_guidance", "charting_guidance"),
        ("🔗 relationships", "relationships"),
        ("💼 business_context", "business_context"),
    ]
    for label, key in sections_to_show:
        content = current_metadata.get(key)
        if content:
            with st.expander(label, expanded=False):
                st.json(content, expanded=False)

    st.divider()

    # ============================================================
    # 版本歷史
    # ============================================================
    st.markdown(f"### 📋 版本歷史")
    versions = []
    if mongo_db is not None:
        try:
            versions = prompt_repo.list_metadata_versions(domain)
        except Exception as e:
            st.error(f"列版本失敗: {e}")

    if not versions:
        st.info(
            "💭 DB 中沒這個 domain 的紀錄。目前 LLM 用 **embedded fallback**。\n\n"
            "要進 DB 請跑:\n"
            "```bash\npython migrations/002_seed_metadata.py\n```"
        )
    else:
        st.caption(f"{len(versions)} 個版本")
        for v in versions:
            is_active = bool(v.get("is_active"))
            with st.container(border=True):
                cols = st.columns([1, 3, 1, 1])
                cols[0].markdown(
                    f"**v{v.get('version', '?')}**"
                    + (" 🟢 active" if is_active else "")
                )
                cols[1].caption(v.get("notes") or "(no notes)")
                ts = v.get("created_at")
                cols[2].markdown(
                    f"<span style='font-size:0.78rem;color:var(--color-text-secondary)'>"
                    f"{str(ts)[:16] if ts else '?'}<br>by {v.get('created_by','?')}"
                    f"</span>",
                    unsafe_allow_html=True,
                )
                if not is_active and mongo_db is not None:
                    if cols[3].button(
                        "✅ Activate",
                        key=f"activate_md_{v.get('_id')}",
                        use_container_width=True,
                    ):
                        try:
                            prompt_repo.activate_metadata(v["_id"])
                            st.toast(f"✅ 已啟用 v{v['version']}", icon="🎯")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Activate 失敗: {e}")

    st.divider()

    # ============================================================
    # JSON 編輯區
    # ============================================================
    st.markdown("### ✏️ 編輯 metadata(整份)")
    st.caption("⚠️ 編輯時請保持 JSON 結構合法。預覽可看摘要 metric 變化。")

    metadata_json_text = st.text_area(
        "Metadata JSON",
        value=json.dumps(current_metadata, indent=2, ensure_ascii=False, default=str),
        height=500,
        key=f"metadata_editor_{domain}",
    )

    edit_notes = st.text_input(
        "Notes (描述此版本改了什麼)",
        placeholder="例:新增 review_mechanism 欄位、修 average_return_rate 公式",
    )
    save_activate = st.checkbox("儲存後立即啟用", value=True)

    save_col, _ = st.columns([1, 3])
    if save_col.button(
        "💾 儲存為新版本",
        type="primary",
        use_container_width=True,
        disabled=mongo_db is None,
    ):
        try:
            parsed = json.loads(metadata_json_text)
        except json.JSONDecodeError as e:
            st.error(f"❌ JSON 解析失敗: {e}")
            parsed = None

        if parsed is not None:
            if not edit_notes.strip():
                st.error("❌ 請填 notes")
            elif parsed == current_metadata:
                st.warning("⚠️ 與目前一致,沒變化")
            else:
                try:
                    new_id = prompt_repo.save_new_metadata_version(
                        domain=domain,
                        metadata=parsed,
                        notes=edit_notes.strip(),
                        created_by="admin_ui",
                        activate=save_activate,
                    )
                    msg = "✅ 已儲存新版本"
                    if save_activate:
                        msg += " 並啟用"
                    st.toast(msg, icon="💾")
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ 儲存失敗: {e}")
