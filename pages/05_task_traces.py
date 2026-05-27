"""
pages/05_task_traces.py — v0.7.0+

Streamlit page:檢視每次 user query 完整執行 trace。
- List 最近 N 個 trace(time / domain / query / status / wall / tokens)
- 點進 trace_id → 細節:每個 step 的耗時、每個 LLM call 的 messages + response
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st
import pandas as pd
from datetime import datetime
import config


# ============================================================
# 頁面設定
# ============================================================
# v0.17:set_page_config 改在 app.py 統一設定(st.navigation 規則)
st.markdown(
    "<h1 style='font-size:2.2rem;margin:0 0 .3rem 0'>🔍 Task Trace Viewer</h1>",
    unsafe_allow_html=True,
)
st.caption(
    f"collection: `{config.TASK_TRACES_COLLECTION}` · "
    f"MongoDB: `{config.MONGO_DB}` · "
    "每筆 trace 含每個 step 的耗時 + 每次 LLM call 的完整 messages + response"
)


# ============================================================
# 連 DB
# ============================================================
@st.cache_resource(show_spinner=False)
def _get_repo():
    try:
        from pymongo import MongoClient
        from task_trace import TaskTraceRepository
        client = MongoClient(
            config.MONGO_URI,
            serverSelectionTimeoutMS=config.MONGO_SERVER_SELECTION_TIMEOUT_MS,
        )
        client.admin.command("ping")
        db = client[config.MONGO_DB]
        return TaskTraceRepository(db, config.TASK_TRACES_COLLECTION), None
    except Exception as e:
        return None, str(e)


repo, err = _get_repo()
if repo is None:
    st.error(f"❌ MongoDB 連線失敗:{err}")
    st.stop()


# ============================================================
# Sidebar — filters
# ============================================================
with st.sidebar:
    st.markdown("### 🔧 Filters")
    domain_filter = st.text_input("Domain(空白 = 全部)", value="")
    limit = st.slider("Trace 數量上限", 5, 100, 20, 5)
    st.divider()
    if st.button("🧹 清空所有 cache(強制 reload)"):
        st.cache_resource.clear()
        st.cache_data.clear()
        st.rerun()
    st.markdown("### 🗑️ 清理舊 trace")
    purge_days = st.number_input("刪除 N 天前的 trace", min_value=1, value=30)
    if st.button("執行 purge"):
        n = repo.purge_older_than(purge_days)
        st.success(f"已刪除 {n} 筆 ({purge_days} 天前的 trace)")
        st.cache_data.clear()


# ============================================================
# List view
# ============================================================
@st.cache_data(show_spinner="載入 trace 列表中...", ttl=30)
def _list_recent(domain: str, limit: int):
    return repo.list_recent(limit=limit, domain=domain)


traces = _list_recent(domain_filter, limit)

if not traces:
    st.info("📭 沒有 trace 紀錄。先去 app.py 跑幾個 query,trace 會自動寫進這裡。")
    st.stop()


# 表格列出
rows = []
for t in traces:
    s = t.get("summary", {}) or {}
    rows.append({
        "trace_id": t.get("trace_id", "")[:8],
        "started_at": t.get("started_at"),
        "domain": t.get("domain", ""),
        "query": (t.get("query") or "")[:60],
        "status": t.get("status", ""),
        "wall(s)": round(t.get("total_wall_s") or 0, 2),
        "LLM calls": s.get("total_llm_calls", 0),
        "prompt_tok": s.get("total_prompt_tokens", 0),
        "comp_tok": s.get("total_completion_tokens", 0),
        "chart": t.get("intent_chart", "") or "",
        "preproc": t.get("intent_preprocess", "") or "",
        "_full_trace_id": t.get("trace_id", ""),
    })
df = pd.DataFrame(rows)


st.markdown(f"### 📋 最近 {len(rows)} 個 trace"
            + (f" · domain=`{domain_filter}`" if domain_filter else ""))

# 用 dataframe 顯示(全欄)
st.dataframe(
    df.drop(columns=["_full_trace_id"]),
    use_container_width=True,
    hide_index=True,
    column_config={
        "wall(s)": st.column_config.NumberColumn(format="%.2f"),
        "prompt_tok": st.column_config.NumberColumn(format="%d"),
        "comp_tok": st.column_config.NumberColumn(format="%d"),
        "status": st.column_config.TextColumn(width="small"),
    },
)


# ============================================================
# Detail view
# ============================================================
st.divider()
st.markdown("### 🔬 Trace 細節")

trace_options = {
    f"{r['trace_id']} · {r['started_at']} · {r['query'][:40]}": r["_full_trace_id"]
    for r in rows
}
selected_label = st.selectbox(
    "選一個 trace 看細節",
    options=list(trace_options.keys()),
    index=0,
)
selected_trace_id = trace_options[selected_label]


@st.cache_data(show_spinner="載入 trace 細節...", ttl=30)
def _get_detail(trace_id):
    return repo.get_by_id(trace_id)


trace = _get_detail(selected_trace_id)
if trace is None:
    st.error("❌ 找不到該 trace")
    st.stop()


# ── Trace meta ──
meta_cols = st.columns(5)
meta_cols[0].metric("Wall time (s)",
                     f"{trace.get('total_wall_s', 0):.2f}")
meta_cols[1].metric("Status", trace.get("status", "?"))
meta_cols[2].metric("LLM calls",
                     trace.get("summary", {}).get("total_llm_calls", 0))
meta_cols[3].metric("Prompt tokens",
                     f"{trace.get('summary', {}).get('total_prompt_tokens', 0):,}")
meta_cols[4].metric("Completion tokens",
                     f"{trace.get('summary', {}).get('total_completion_tokens', 0):,}")

st.markdown(f"**Query**:`{trace.get('query', '')}`")
st.markdown(
    f"**Domain**:`{trace.get('domain', '')}` · "
    f"**Chart intent**:`{trace.get('intent_chart', '?')}` · "
    f"**Preprocess intent**:`{trace.get('intent_preprocess', '?')}`"
)
if trace.get("error"):
    st.error(f"❌ Trace error:`{trace['error']}`")


# ── Steps timeline (bar chart) ──
steps = trace.get("steps", []) or []
if steps:
    st.markdown("#### ⏱️ 各 step 耗時")
    timeline_df = pd.DataFrame([
        {
            "step": f"#{s.get('step_id')}·{s.get('phase','')}({s.get('kind','')})",
            "elapsed_s": s.get("elapsed_s") or 0,
        }
        for s in steps
    ])
    st.bar_chart(
        timeline_df.set_index("step")["elapsed_s"],
        use_container_width=True, height=240,
    )

    # ── Per-step detail expander ──
    st.markdown("#### 📜 每個 step 細節")
    for s in steps:
        kind = s.get("kind", "function")
        phase = s.get("phase", "")
        elapsed = s.get("elapsed_s") or 0
        emoji = {"llm_call": "🤖", "function": "⚙️", "post_process": "🔧"}.get(kind, "▫️")
        err_tag = "  ❌" if s.get("error") else ""
        header = f"{emoji} #{s.get('step_id')} · `{phase}` · {kind} · {elapsed:.3f}s{err_tag}"

        with st.expander(header, expanded=False):
            if s.get("meta"):
                st.caption(f"meta: {s['meta']}")
            if s.get("error"):
                st.error(f"Error: {s['error']}")

            if kind == "llm_call" and s.get("llm_call"):
                llm = s["llm_call"]
                # Token row
                cols = st.columns(4)
                cols[0].metric("model", llm.get("model", "?"))
                cols[1].metric("prompt_tok", f"{llm.get('prompt_tokens') or 0:,}")
                cols[2].metric("comp_tok", f"{llm.get('completion_tokens') or 0:,}")
                cols[3].metric("intent", llm.get("intent") or "—")

                # Messages
                msgs = llm.get("messages", []) or []
                for i, msg in enumerate(msgs):
                    role = msg.get("role", "?")
                    content = msg.get("content", "")
                    label = f"💬 message[{i}] · role=`{role}` · {len(content):,} chars"
                    with st.expander(label, expanded=False):
                        st.code(content, language="markdown")

                # Response
                resp = llm.get("response", "")
                with st.expander(f"🎯 response · {len(resp):,} chars", expanded=True):
                    # 試著用 python 語言著色(若是 ECharts option / preprocess code 比較好讀)
                    if resp.strip().startswith(("import", "option", "Q ", "{")):
                        st.code(resp, language="python")
                    else:
                        st.markdown(resp)
else:
    st.info("此 trace 沒有 step 紀錄(可能是 refuse 或 meta_response 路徑)")


# ── Raw JSON (debug) ──
with st.expander("🛠️ Raw trace JSON(debug 用)", expanded=False):
    import json
    st.code(
        json.dumps(trace, default=str, ensure_ascii=False, indent=2),
        language="json",
    )

# ── Delete trace ──
st.divider()
if st.button(f"🗑️ 刪除這筆 trace(`{selected_trace_id[:8]}`)",
              type="secondary"):
    ok = repo.delete(selected_trace_id)
    if ok:
        st.success("已刪除")
        st.cache_data.clear()
        st.rerun()
    else:
        st.error("刪除失敗")
