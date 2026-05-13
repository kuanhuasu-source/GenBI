"""
pages/02_test_runs.py — v0.3.0+

Streamlit page for test run history、baseline mark、compare-with-baseline。

# 功能
- 列最近 N 次跑(per domain),含 pass rate / tokens / wall time / git_commit
- 標 / 取消 baseline
- 兩筆 run 對比(side-by-side delta + case 變化清單)
- Drill-down 看某 run 的逐 case 結果
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
st.set_page_config(
    page_title="GenBI · Test Runs",
    page_icon="📊",
    layout="wide",
)
st.markdown(
    "<h1 style='font-size:2.2rem;margin:0 0 .3rem 0'>📊 Test Run History</h1>",
    unsafe_allow_html=True,
)
st.caption(f"collection: {config.TEST_RUNS_COLLECTION} · "
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
if mongo_db is None:
    st.error(f"❌ MongoDB 連線失敗 — test_runs 無法載入\n\n{mongo_err}")
    st.stop()

from test_run_repository import TestRunRepository
run_repo = TestRunRepository(
    mongo_db=mongo_db,
    collection=config.TEST_RUNS_COLLECTION,
)


# ============================================================
# Sidebar
# ============================================================
with st.sidebar:
    st.markdown("### 🔍 Filter")
    # Domain filter
    try:
        domain_options = ["(all)"] + sorted(
            mongo_db[config.TEST_RUNS_COLLECTION].distinct("domain") or []
        )
    except Exception:
        domain_options = ["(all)"]

    selected_domain = st.selectbox(
        "Domain",
        options=domain_options,
        index=0,
    )

    limit = st.slider("Recent N runs", min_value=5, max_value=100, value=20, step=5)
    only_baseline = st.toggle("只看 baseline", value=False)
    st.divider()

    if st.button("🔄 重整列表", use_container_width=True):
        st.rerun()


# ============================================================
# 讀 runs
# ============================================================
query = {}
if selected_domain != "(all)":
    query["domain"] = selected_domain
if only_baseline:
    query["is_baseline"] = True

try:
    runs = list(
        mongo_db[config.TEST_RUNS_COLLECTION]
        .find(query)
        .sort("started_at", -1)
        .limit(limit)
    )
except Exception as e:
    st.error(f"❌ 讀 test_runs 失敗: {e}")
    runs = []


# ============================================================
# Top — 概覽 metric
# ============================================================
# baseline 跟 latest 都按當前過濾的 domain 取(若 "(all)" 則不過濾)
_domain_filter = None if selected_domain == "(all)" else selected_domain
baseline = run_repo.get_baseline(domain=_domain_filter)
latest = runs[0] if runs else None

mc1, mc2, mc3, mc4 = st.columns(4)
mc1.metric("Total runs shown", len(runs))
if baseline:
    mc2.metric(
        "Baseline",
        f"{baseline.get('run_id', '?')[:15]}",
        help=f"domain={baseline.get('domain','?')} · "
             f"passed={baseline.get('summary',{}).get('passed','?')}/"
             f"{baseline.get('summary',{}).get('total_cases','?')}"
    )
else:
    mc2.metric("Baseline", "(尚未設定)")
if latest:
    lp = latest.get("summary", {}).get("passed", 0)
    lt = latest.get("summary", {}).get("total_cases", 0)
    mc3.metric("Latest run pass", f"{lp}/{lt}")
    mc4.metric("Latest wall time",
               f"{latest.get('total_wall_s', 0):.0f}s")


# ============================================================
# Run list table
# ============================================================
st.markdown("### 📋 Runs")

if not runs:
    st.info("💭 沒有符合條件的 run。跑一次 `python test_runner.py` 試試。")
else:
    if "selected_run_id" not in st.session_state:
        st.session_state.selected_run_id = None

    for r in runs:
        is_baseline = bool(r.get("is_baseline"))
        is_selected = st.session_state.selected_run_id == r.get("run_id")
        s = r.get("summary", {})
        with st.container(border=True):
            cols = st.columns([2, 1, 1, 1, 1, 1, 1, 1])

            # ── 第 1 欄:run_id + baseline / domain badge ──
            badge = " 🏁" if is_baseline else ""
            cols[0].markdown(
                f"**{r.get('run_id','?')}**{badge}<br>"
                f"<span style='font-size:0.78rem;color:var(--color-text-secondary)'>"
                f"🌐 {r.get('domain','?')} · "
                f"📦 {r.get('git_commit', '?')[:8]}"
                f"</span>",
                unsafe_allow_html=True,
            )

            # ── 2-5 欄:metric ──
            passed = s.get("passed", 0)
            total = s.get("total_cases", 0)
            pass_color = "#7DB650" if passed >= total * 0.9 else (
                "#FFC130" if passed >= total * 0.7 else "#D9342B"
            )
            cols[1].markdown(
                f"<span style='color:{pass_color};font-weight:600'>"
                f"{passed}/{total}</span><br>"
                f"<span style='font-size:0.75rem;color:var(--color-text-secondary)'>"
                f"passed</span>",
                unsafe_allow_html=True,
            )
            failed = s.get("failed", 0) + s.get("fatal_error", 0)
            cols[2].markdown(
                f"❌ {failed}<br>"
                f"<span style='font-size:0.75rem;color:var(--color-text-secondary)'>"
                f"failed</span>",
                unsafe_allow_html=True,
            )
            wall = r.get("total_wall_s", 0)
            cols[3].markdown(
                f"⏱ {wall:.0f}s<br>"
                f"<span style='font-size:0.75rem;color:var(--color-text-secondary)'>"
                f"wall</span>",
                unsafe_allow_html=True,
            )
            tokens = s.get("total_tokens", 0)
            cols[4].markdown(
                f"💬 {tokens:,}<br>"
                f"<span style='font-size:0.75rem;color:var(--color-text-secondary)'>"
                f"tokens</span>",
                unsafe_allow_html=True,
            )

            # ── 6-8 欄:操作 ──
            if cols[5].button(
                "🔍 詳情",
                key=f"detail_{r.get('run_id')}",
                use_container_width=True,
            ):
                st.session_state.selected_run_id = r.get("run_id")
                st.rerun()
            if not is_baseline:
                if cols[6].button(
                    "🏁 設 baseline",
                    key=f"baseline_{r.get('run_id')}",
                    use_container_width=True,
                ):
                    run_repo.mark_as_baseline(
                        r["run_id"],
                        notes=f"Marked via UI at {r.get('completed_at','')}",
                    )
                    st.toast(f"✅ 已標 baseline: {r['run_id']}", icon="🏁")
                    st.rerun()
            else:
                if cols[6].button(
                    "取消 baseline",
                    key=f"unbase_{r.get('run_id')}",
                    use_container_width=True,
                ):
                    run_repo.unmark_baseline(r["run_id"])
                    st.toast(f"已取消 baseline: {r['run_id']}", icon="⚪")
                    st.rerun()
            if baseline and r.get("run_id") != baseline.get("run_id"):
                if cols[7].button(
                    "vs baseline",
                    key=f"compare_{r.get('run_id')}",
                    use_container_width=True,
                ):
                    st.session_state.compare_run_a = baseline.get("run_id")
                    st.session_state.compare_run_b = r.get("run_id")
                    st.rerun()


# ============================================================
# Compare view
# ============================================================
if (st.session_state.get("compare_run_a")
        and st.session_state.get("compare_run_b")):
    st.divider()
    a_id = st.session_state.compare_run_a
    b_id = st.session_state.compare_run_b
    st.markdown(f"### 🔬 對比 · `{a_id}` vs `{b_id}`")

    try:
        diff = run_repo.compare(a_id, b_id)

        # 摘要對比
        st.markdown("#### 摘要差異")
        d = diff.get("delta", {})

        def _format_delta(value):
            if not isinstance(value, (int, float)):
                return str(value)
            if value > 0:
                return f"↑ +{value:,}"
            if value < 0:
                return f"↓ {value:,}"
            return "—"

        delta_cols = st.columns(4)
        delta_cols[0].metric(
            "Passed",
            diff["b"].get("passed", 0),
            delta=_format_delta(d.get("passed", 0)),
        )
        delta_cols[1].metric(
            "Failed",
            diff["b"].get("failed", 0),
            delta=_format_delta(d.get("failed", 0)),
            delta_color="inverse",
        )
        delta_cols[2].metric(
            "Total tokens",
            f"{diff['b'].get('total_tokens', 0):,}",
            delta=_format_delta(d.get("total_tokens", 0)),
            delta_color="inverse",
        )
        delta_cols[3].metric(
            "LLM calls",
            diff["b"].get("total_calls", 0),
            delta=_format_delta(d.get("total_calls", 0)),
            delta_color="inverse",
        )

        # Case 變化
        st.markdown("#### Case 變化(只列狀態改變的)")
        changes = diff.get("case_changes", [])
        if not changes:
            st.success("✅ 所有 case 狀態相同(無退步、無改善)")
        else:
            for c in changes:
                a_st = c.get("a_status", "?")
                b_st = c.get("b_status", "?")
                # 簡單推斷方向
                is_progress = (a_st != "pass" and b_st == "pass") or \
                              (a_st == "fail" and b_st != "fail")
                is_regress = (a_st == "pass" and b_st != "pass") or \
                             (a_st != "fail" and b_st == "fail")
                icon = "✅" if is_progress else ("⚠️" if is_regress else "🔄")
                st.markdown(
                    f"  {icon} **{c.get('id','?')}**: `{a_st}` → `{b_st}`"
                )

        if st.button("✖ 關閉對比"):
            del st.session_state.compare_run_a
            del st.session_state.compare_run_b
            st.rerun()
    except Exception as e:
        st.error(f"❌ 對比失敗: {e}")
        if st.button("✖ 關閉"):
            del st.session_state.compare_run_a
            del st.session_state.compare_run_b
            st.rerun()


# ============================================================
# Run detail view
# ============================================================
if st.session_state.get("selected_run_id"):
    st.divider()
    run_id = st.session_state.selected_run_id
    st.markdown(f"### 🔍 詳情 · `{run_id}`")
    doc = run_repo.get_by_run_id(run_id)
    if not doc:
        st.error(f"❌ 找不到 run_id={run_id}")
    else:
        sc1, sc2 = st.columns([1, 3])
        with sc1:
            st.markdown("**Metadata**")
            st.json({
                "run_id": doc.get("run_id"),
                "domain": doc.get("domain"),
                "git_commit": doc.get("git_commit"),
                "started_at": str(doc.get("started_at", "?")),
                "wall_s": doc.get("total_wall_s"),
                "is_baseline": doc.get("is_baseline"),
                "baseline_notes": doc.get("baseline_notes"),
                "filter": doc.get("filter"),
            }, expanded=False)
            st.markdown("**Active versions snapshot**")
            st.json(doc.get("active_versions", {}), expanded=False)

        with sc2:
            st.markdown("**Summary**")
            s = doc.get("summary", {})
            ssc = st.columns(4)
            ssc[0].metric("passed", s.get("passed", 0))
            ssc[1].metric("failed", s.get("failed", 0))
            ssc[2].metric("refusal", s.get("refusal_detected", 0))
            ssc[3].metric("tokens", f"{s.get('total_tokens', 0):,}")

            st.markdown("**Case results**")
            case_results = doc.get("case_results", [])
            for cr in case_results:
                status = cr.get("status", "?")
                cid = cr.get("id", "?")
                status_color = "#7DB650" if status == "pass" else (
                    "#FFC130" if status in ("refusal_detected",
                                            "phaseC_fallback_used") else "#D9342B"
                )
                with st.container(border=True):
                    rcols = st.columns([1, 4, 1])
                    rcols[0].markdown(f"**{cid}**")
                    rcols[1].caption(cr.get("name", ""))
                    rcols[2].markdown(
                        f"<span style='color:{status_color};font-weight:600'>"
                        f"{status}</span>",
                        unsafe_allow_html=True,
                    )

    if st.button("✖ 關閉詳情"):
        del st.session_state.selected_run_id
        st.rerun()
