"""
pages/01_test_cases.py — v0.3.0+

Streamlit admin page for test case CRUD。

# 功能
- Domain selector(列 active domains)
- 列當前 domain 所有 cases(table 形式)
- 點 row 編輯:case_id / name / query / type / expected_chart / echarts_* checks / tags
- 新增 case 按鈕(複製模板)
- 啟用 / 停用 toggle
- 真刪除(危險,需 confirm)

# 注意
- 沒接 DB 時 sidebar 顯示 read-only banner,UI 仍能看 embedded cases 但所有寫入按鈕 disable
- 改動立即寫入 DB,沒 staging area(v0.3.0 簡化)
"""

from __future__ import annotations

import sys
from pathlib import Path

# Project root to sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import json

import streamlit as st

import config
from test_case_repository import (
    TestCaseRepository,
    ECHARTS_CHECK_KEYS,
    build_default_test_case_repo,
)

# ============================================================
# 頁面設定
# ============================================================
st.set_page_config(
    page_title="GenBI · Test Cases",
    page_icon="🧪",
    layout="wide",
)
st.markdown(
    "<h1 style='font-size:2.2rem;margin:0 0 .3rem 0'>🧪 Test Case Admin</h1>",
    unsafe_allow_html=True,
)
st.caption(f"MongoDB: {config.MONGO_URI}{config.MONGO_DB} · "
           f"collection: {config.TEST_CASES_COLLECTION}")


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

case_repo = build_default_test_case_repo(mongo_db=mongo_db)
if mongo_db is not None:
    try:
        case_repo.ensure_indexes()
    except Exception:
        pass

if mongo_db is None:
    st.warning(
        f"⚠️ MongoDB 連線失敗 — 純 embedded 模式(所有編輯 / 啟用 / 刪除按鈕禁用)\n\n"
        f"錯誤:{mongo_err}"
    )

# ============================================================
# Sidebar - domain switcher
# ============================================================
with st.sidebar:
    st.markdown("### 🌐 Domain")
    _domains = case_repo.list_domains_with_cases() or ["tflex"]
    if "tc_domain" not in st.session_state:
        # tflex 永遠優先 default(v0.3.1+)
        st.session_state.tc_domain = (
            "tflex" if "tflex" in _domains else _domains[0]
        )
    domain = st.selectbox(
        "Active domain",
        options=_domains,
        index=_domains.index(st.session_state.tc_domain) if st.session_state.tc_domain in _domains else 0,
        label_visibility="collapsed",
    )
    st.session_state.tc_domain = domain

    st.divider()
    n_active = case_repo.count(domain)
    n_total = case_repo.count(domain, include_inactive=True)
    st.metric("Active / Total", f"{n_active} / {n_total}")
    st.caption(f"collection: {config.TEST_CASES_COLLECTION}")

    st.divider()
    show_inactive = st.toggle("顯示停用的 cases", value=False)
    filter_prefix = st.text_input(
        "過濾 case_id 前綴",
        placeholder="例:STK",
    )
    st.divider()
    if st.button("🔄 重整 cache", use_container_width=True):
        case_repo.invalidate()
        st.rerun()


# ============================================================
# 主區:列 cases + 編輯 form
# ============================================================
cases = case_repo.get_cases(
    domain=domain,
    filter_prefix=filter_prefix.strip(),
    include_inactive=show_inactive,
)

# 表頭 + 統計
st.markdown(
    f"### 📋 Cases in `{domain}` "
    f"<span style='color:var(--color-text-secondary);font-size:0.85rem'>"
    f"({len(cases)} 筆顯示)</span>",
    unsafe_allow_html=True,
)

# ── Editing state ──
# `tc_editing`: case_id 字串或 "__new__" 或 None
if "tc_editing" not in st.session_state:
    st.session_state.tc_editing = None

# 列 cases 的 table(精簡版 — case_id / name / type / active / 操作)
col_h1, col_h2 = st.columns([4, 1])
with col_h1:
    pass
with col_h2:
    if st.button("➕ 新增 case", type="primary", use_container_width=True,
                 disabled=mongo_db is None):
        st.session_state.tc_editing = "__new__"
        st.rerun()

if cases:
    # 用 dataframe 樣式顯示
    for c in cases:
        is_editing = st.session_state.tc_editing == c["case_id"]
        with st.container(border=True):
            row_cols = st.columns([1, 3, 1, 1, 1, 1])
            row_cols[0].markdown(f"**{c['case_id']}**")
            row_cols[1].caption(c.get("name", "(no name)"))
            row_cols[2].markdown(
                f"<span style='font-size:0.78rem;color:var(--color-text-secondary)'>"
                f"{c.get('type', '?')}</span>",
                unsafe_allow_html=True,
            )
            row_cols[3].markdown(
                "🟢 active" if c.get("is_active", True) else "⚪ inactive"
            )
            if row_cols[4].button(
                "✏️ Edit", key=f"edit_{c['case_id']}",
                use_container_width=True,
                disabled=mongo_db is None,
            ):
                st.session_state.tc_editing = c["case_id"]
                st.rerun()
            if row_cols[5].button(
                "停用" if c.get("is_active", True) else "啟用",
                key=f"toggle_{c['case_id']}",
                use_container_width=True,
                disabled=mongo_db is None,
            ):
                if c.get("is_active", True):
                    case_repo.deactivate_case(domain, c["case_id"], user="admin_ui")
                else:
                    case_repo.activate_case(domain, c["case_id"], user="admin_ui")
                st.toast(f"已切換 {c['case_id']} active 狀態", icon="✅")
                st.rerun()
else:
    st.info(f"💭 沒有符合條件的 case(filter={filter_prefix!r}, show_inactive={show_inactive})")


# ============================================================
# 編輯 / 新增 form
# ============================================================
if st.session_state.tc_editing:
    st.divider()
    is_new = st.session_state.tc_editing == "__new__"
    if is_new:
        st.markdown("### ➕ 新增 case")
        current = {
            "case_id": "",
            "name": "",
            "query": "",
            "type": "happy_path",
            "expected_chart": "",
            "expected_q_cols_any": [],
            "expected_q_cols_all": [],
            "echarts_required_keys": ["title", "xAxis", "yAxis", "series"],
            "echarts_min_series": 1,
            "echarts_should_have_stack": False,
            "refusal_keywords": [],
            "follow_up_setup_query": None,
            "tags": [],
            "notes": "",
        }
    else:
        st.markdown(f"### ✏️ 編輯 case · `{st.session_state.tc_editing}`")
        current = case_repo.get_case(domain, st.session_state.tc_editing) or {}

    with st.form("edit_case_form"):
        c1, c2 = st.columns(2)
        with c1:
            case_id_input = st.text_input(
                "case_id (在 domain 內 unique)",
                value=current.get("case_id", ""),
                disabled=not is_new,  # 編輯時不准改 case_id
                help="例:STK-01 / T1 / 12",
            )
            name = st.text_input("name", value=current.get("name", ""))
            query_input = st.text_area(
                "query (LLM 收到的)", value=current.get("query", ""),
                height=80,
            )
            type_input = st.selectbox(
                "type",
                options=["happy_path", "refusal"],
                index=0 if current.get("type", "happy_path") == "happy_path" else 1,
            )
            expected_chart = st.text_input(
                "expected_chart", value=current.get("expected_chart", "")
            )
        with c2:
            q_cols_any = st.text_input(
                "expected_q_cols_any (逗號分隔)",
                value=",".join(current.get("expected_q_cols_any") or []),
            )
            q_cols_all = st.text_input(
                "expected_q_cols_all (逗號分隔)",
                value=",".join(current.get("expected_q_cols_all") or []),
            )
            refusal_kw = st.text_input(
                "refusal_keywords (逗號分隔,僅 type=refusal 用)",
                value=",".join(current.get("refusal_keywords") or []),
            )
            follow_up_setup = st.text_input(
                "follow_up_setup_query (空=非接續 case)",
                value=current.get("follow_up_setup_query") or "",
            )
            tags = st.text_input(
                "tags (逗號分隔)",
                value=",".join(current.get("tags") or []),
            )

        st.markdown("**ECharts checks** (高階驗證,通常照預設就好)")
        with st.expander("展開 echarts_* 檢查項",
                         expanded=any(k in current for k in ECHARTS_CHECK_KEYS)):
            ec_kwargs = {}
            cc1, cc2 = st.columns(2)
            with cc1:
                ec_kwargs["echarts_required_keys"] = st.text_input(
                    "echarts_required_keys",
                    value=",".join(current.get("echarts_required_keys") or []),
                    help="逗號分隔,例 title,xAxis,yAxis,series",
                )
                ec_kwargs["echarts_min_series"] = st.number_input(
                    "echarts_min_series", min_value=0, max_value=20,
                    value=int(current.get("echarts_min_series") or 0),
                )
                ec_kwargs["echarts_series_count_max"] = st.number_input(
                    "echarts_series_count_max (0=不限)",
                    min_value=0, max_value=50,
                    value=int(current.get("echarts_series_count_max") or 0),
                )
                ec_kwargs["echarts_yaxis_max"] = st.number_input(
                    "echarts_yaxis_max (0=不檢查)",
                    min_value=0, max_value=1000,
                    value=int(current.get("echarts_yaxis_max") or 0),
                )
                ec_kwargs["echarts_min_kpi_cards"] = st.number_input(
                    "echarts_min_kpi_cards",
                    min_value=0, max_value=20,
                    value=int(current.get("echarts_min_kpi_cards") or 0),
                )
            with cc2:
                bool_keys = [
                    "echarts_should_have_stack",
                    "echarts_should_have_visualmap",
                    "echarts_should_have_yaxis_category",
                    "echarts_should_have_xaxis_value",
                    "echarts_should_have_kpi_cards",
                    "echarts_should_use_table",
                    "echarts_xaxis_unique",
                    "echarts_data_length_aligned",
                    "echarts_data_length_aligned_horizontal",
                    "echarts_no_placeholder_series_name",
                    "echarts_no_nan_in_data",
                ]
                for k in bool_keys:
                    ec_kwargs[k] = st.checkbox(k, value=bool(current.get(k, False)))

        notes = st.text_area("notes (free-form)",
                             value=current.get("notes", ""), height=60)

        col_save, col_cancel, col_del = st.columns([2, 1, 1])
        submitted = col_save.form_submit_button(
            "💾 儲存", type="primary", use_container_width=True,
            disabled=mongo_db is None,
        )
        canceled = col_cancel.form_submit_button(
            "✖ 取消", use_container_width=True,
        )
        deleted = False
        if not is_new:
            deleted = col_del.form_submit_button(
                "🗑️ 刪除", use_container_width=True,
                disabled=mongo_db is None,
                help="真實刪除(無法復原)。建議停用而非刪除。",
            )

    # 表單後處理
    if canceled:
        st.session_state.tc_editing = None
        st.rerun()
    if deleted:
        case_repo.delete_case(domain, st.session_state.tc_editing)
        st.toast(f"已刪除 case {st.session_state.tc_editing}", icon="🗑️")
        st.session_state.tc_editing = None
        st.rerun()
    if submitted:
        # 驗證
        if not case_id_input.strip():
            st.error("❌ case_id 不可空")
        elif not query_input.strip():
            st.error("❌ query 不可空")
        else:
            # 組 case dict
            def _split(s):
                return [x.strip() for x in (s or "").split(",") if x.strip()]

            doc = {
                "name": name.strip(),
                "query": query_input.strip(),
                "type": type_input,
                "expected_chart": expected_chart.strip() or None,
                "expected_q_cols_any": _split(q_cols_any),
                "expected_q_cols_all": _split(q_cols_all),
                "refusal_keywords": _split(refusal_kw),
                "follow_up_setup_query": follow_up_setup.strip() or None,
                "tags": _split(tags),
                "notes": notes.strip(),
                "is_active": current.get("is_active", True),
            }
            # ECharts checks — 只塞非 0 / 非空值
            for k, v in ec_kwargs.items():
                if k == "echarts_required_keys":
                    parsed = _split(v)
                    if parsed:
                        doc[k] = parsed
                elif isinstance(v, bool):
                    if v:
                        doc[k] = True
                elif isinstance(v, (int, float)):
                    if v:
                        doc[k] = int(v)

            # 移除 None / empty 欄位
            doc = {k: v for k, v in doc.items() if v not in (None, "", [], 0)}
            # 確保 is_active 還在(上面可能被 filter 掉)
            doc["is_active"] = current.get("is_active", True)

            cid = case_id_input.strip()
            try:
                inserted_id = case_repo.upsert_case(domain, cid, doc, user="admin_ui")
                st.toast(f"✅ {'新增' if is_new else '更新'} case {cid}",
                         icon="✅")
                st.session_state.tc_editing = None
                st.rerun()
            except Exception as e:
                st.error(f"❌ 儲存失敗: {e}")


# ============================================================
# 底部 — debug 區
# ============================================================
with st.expander("🔧 Debug / Raw view", expanded=False):
    st.caption(f"Showing {len(cases)} cases for domain={domain}")
    st.json(cases, expanded=False)
