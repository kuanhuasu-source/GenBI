"""
pages/06_learning_review.py — v0.9.0 (Week 6)

Streamlit admin page for Self-Learning MVP review workflow。

對齊 GenBI_v1.3_Self_Learning_MVP_Implementation_Spec.md §15.5 + §20 + §25。

4 個 section:
  1. 📊 Dashboard metrics(operational / quality / impact)
  2. 📝 Pending prompt_rule_candidates 審批(approve / reject 按鈕)
  3. ⚠️ Contradiction review queue(learning_jobs needs_review)
  4. 🔍 Recent observations browser(filter + 細節)

設計重點:
  - read-mostly,寫入只在「人類點 Approve/Reject」時發生
  - 用 st.cache_resource 緩存 DB connection,但**不 cache data**(避免看到舊狀態)
  - 每 section 獨立可摺,避免大量資料時 page 太長
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st
import pandas as pd

import config


# ============================================================
# Page config
# ============================================================
st.set_page_config(
    page_title="GenBI · Learning Review",
    page_icon="🧠",
    layout="wide",
)
st.markdown(
    "<h1 style='font-size:2.2rem;margin:0 0 .3rem 0'>🧠 Self-Learning Review</h1>",
    unsafe_allow_html=True,
)
st.caption(
    f"MongoDB: `{config.MONGO_DB}` · "
    "Observation → Verifier → Instinct → Candidate → Gate · 全 loop 可在此手動 review"
)


# ============================================================
# DB connection(cache resource,不 cache data)
# ============================================================
@st.cache_resource(show_spinner=False)
def _get_db():
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


db, err = _get_db()
if db is None:
    st.error(f"❌ MongoDB 連線失敗:{err}")
    st.stop()


# ============================================================
# Sidebar — 全域 filter
# ============================================================
with st.sidebar:
    st.subheader("Filter")
    window_days = st.slider("Quality metrics window (days)", 1, 60, 7)
    refresh = st.button("🔄 Refresh data", use_container_width=True)
    if refresh:
        st.rerun()


# ============================================================
# Section 1:Dashboard metrics
# ============================================================
st.markdown("---")
st.subheader("📊 Dashboard Metrics")

from learning.dashboard_metrics import (
    operational_metrics,
    quality_metrics,
    impact_metrics,
    needs_review_queue,
)

op = operational_metrics(db, window_days=None)
qm = quality_metrics(db, window_days=window_days)
im = impact_metrics(db)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Observations · verified", op.get("observations_verified", 0),
          f"+{op.get('observations_candidate', 0)} candidate")
c2.metric("Instincts · active", op.get("instincts_active", 0),
          f"+{op.get('instincts_candidate', 0)} candidate")
c3.metric("Candidates · approved", im.get("candidates_approved", 0),
          f"{im.get('candidates_pending', 0)} pending")
br = qm.get("latest_baseline_pass_rate")
c4.metric("Baseline pass rate",
          f"{br * 100:.1f}%" if br is not None else "N/A",
          qm.get("latest_baseline_run_id", ""))

with st.expander(f"⚙️ Quality details (last {window_days}d)", expanded=False):
    st.write(qm)

with st.expander("📊 Full operational counts", expanded=False):
    st.json(op)
    st.json(im)


# ============================================================
# Section 2:Pending candidates
# ============================================================
st.markdown("---")
st.subheader("📝 Pending Prompt Rule Candidates")
st.caption("人類審後 click Approve / Reject 直接寫進 `prompt_rule_candidates.status`")

try:
    candidates = list(
        db["prompt_rule_candidates"]
        .find({"status": {"$in": ["candidate", "testing"]}})
        .sort([("confidence", -1), ("created_at", -1)])
        .limit(50)
    )
except Exception as e:
    candidates = []
    st.warning(f"無法讀 prompt_rule_candidates: {e}")

if not candidates:
    st.info("(無待審 candidate)")
else:
    for cand in candidates:
        cid = cand.get("candidate_id", "?")
        inst_id = cand.get("instinct_id", "?")
        target = cand.get("target_component", "?")
        conf = cand.get("confidence", 0)
        ev = cand.get("evidence_count", 0)
        rule = cand.get("proposed_rule", "")
        status = cand.get("status", "candidate")
        tags = cand.get("source_instinct_tags") or []

        with st.expander(
            f"{cid} · {target} · conf={conf:.2f} · evidence={ev} · {status}",
            expanded=False,
        ):
            st.markdown(f"**Source instinct**:`{inst_id}` · tags: {', '.join(tags)}")
            st.code(rule, language="text")

            sup_ids = cand.get("supporting_observation_ids") or []
            if sup_ids:
                st.caption(f"Supporting observations: {', '.join(sup_ids[:5])}"
                            + (f" (+{len(sup_ids)-5} more)" if len(sup_ids) > 5 else ""))

            colA, colR, colT = st.columns([1, 1, 1])
            if colA.button(f"✅ Approve", key=f"a_{cid}"):
                try:
                    db["prompt_rule_candidates"].update_one(
                        {"candidate_id": cid},
                        {"$set": {
                            "status": "approved",
                            "approved_at": datetime.now(timezone.utc),
                            "approved_by": "manual_review",
                        }},
                    )
                    st.success(f"{cid} → approved")
                    st.rerun()
                except Exception as e:
                    st.error(f"Approve failed: {e}")
            if colR.button(f"❌ Reject", key=f"r_{cid}"):
                try:
                    db["prompt_rule_candidates"].update_one(
                        {"candidate_id": cid},
                        {"$set": {
                            "status": "rejected",
                            "rejected_at": datetime.now(timezone.utc),
                            "rejected_by": "manual_review",
                        }},
                    )
                    st.success(f"{cid} → rejected")
                    st.rerun()
                except Exception as e:
                    st.error(f"Reject failed: {e}")
            if colT.button(f"🧪 Mark testing", key=f"t_{cid}"):
                try:
                    db["prompt_rule_candidates"].update_one(
                        {"candidate_id": cid},
                        {"$set": {"status": "testing"}},
                    )
                    st.rerun()
                except Exception as e:
                    st.error(f"Mark testing failed: {e}")


# ============================================================
# Section 3:Contradiction review queue
# ============================================================
st.markdown("---")
st.subheader("⚠️ Contradiction Review Queue")
st.caption("auto-degrade 偵測到的潛在矛盾;人類審後可 Confirm(維持 degrade)或 Dismiss(revert)")

nr = needs_review_queue(db, limit=30)
if not nr:
    st.info("(無待審 contradiction)")
else:
    for item in nr:
        nid = item.get("job_id", "?")
        obs_id = item.get("linked_observation_id", "?")
        inst_id = item.get("linked_instinct_id", "?")
        conf_after = item.get("confidence_after", 0)
        status_after = item.get("instinct_status_after", "?")
        reason = item.get("reason", "")
        started = item.get("started_at")

        with st.expander(
            f"{nid} · obs={obs_id} vs instinct={inst_id} · "
            f"conf_after={conf_after} · status={status_after}",
            expanded=False,
        ):
            st.caption(f"Detected at: {started}")
            st.write(f"**Reason**: {reason}")

            # 顯示 obs + instinct 詳情
            try:
                obs = db["learning_observations"].find_one({"observation_id": obs_id})
                if obs:
                    st.markdown("**Observation**:")
                    st.write({k: obs.get(k) for k in
                              ("phase", "tags", "cause", "recommendation")})
            except Exception:
                pass
            try:
                inst = db["learning_instincts"].find_one({"instinct_id": inst_id})
                if inst:
                    st.markdown("**Instinct (current state)**:")
                    st.write({k: inst.get(k) for k in
                              ("phase", "tags", "rule", "confidence", "status",
                               "contradiction_count")})
            except Exception:
                pass

            cC, cD = st.columns(2)
            if cC.button("✅ Confirm degrade", key=f"c_{nid}"):
                try:
                    db["learning_jobs"].update_one(
                        {"job_id": nid},
                        {"$set": {
                            "status": "completed",
                            "review_decision": "confirmed",
                            "reviewed_at": datetime.now(timezone.utc),
                        }},
                    )
                    st.success("Confirmed")
                    st.rerun()
                except Exception as e:
                    st.error(f"Confirm failed: {e}")
            if cD.button("↩️ Dismiss (revert instinct)", key=f"d_{nid}"):
                try:
                    # revert:把 conf +0.05、status active(若被 deprecated)
                    inst = db["learning_instincts"].find_one({"instinct_id": inst_id})
                    if inst:
                        new_conf = min(1.0, (inst.get("confidence") or 0) + 0.05)
                        db["learning_instincts"].update_one(
                            {"instinct_id": inst_id},
                            {"$set": {
                                "confidence": round(new_conf, 4),
                                "status": "active",
                                "updated_at": datetime.now(timezone.utc),
                            },
                             "$inc": {"contradiction_count": -1}},
                        )
                    db["learning_jobs"].update_one(
                        {"job_id": nid},
                        {"$set": {
                            "status": "completed",
                            "review_decision": "dismissed",
                            "reviewed_at": datetime.now(timezone.utc),
                        }},
                    )
                    st.success("Dismissed + reverted instinct")
                    st.rerun()
                except Exception as e:
                    st.error(f"Dismiss failed: {e}")


# ============================================================
# Section 4:Recent observations browser
# ============================================================
st.markdown("---")
st.subheader("🔍 Recent Observations")

colF1, colF2, colF3 = st.columns(3)
status_filter = colF1.selectbox(
    "Status", ["all", "candidate", "verified", "rejected"], index=0
)
phase_filter = colF2.selectbox(
    "Phase",
    ["all", "phase_0", "phase_a", "phase_b", "phase_c", "phase_d", "meta"],
    index=0,
)
limit = colF3.number_input("Limit", min_value=5, max_value=200, value=30, step=5)

q: dict = {}
if status_filter != "all":
    q["status"] = status_filter
if phase_filter != "all":
    q["phase"] = phase_filter

try:
    obs_list = list(
        db["learning_observations"]
        .find(q)
        .sort("created_at", -1)
        .limit(int(limit))
    )
except Exception as e:
    obs_list = []
    st.warning(f"無法讀 learning_observations: {e}")

if not obs_list:
    st.info("(無 observation)")
else:
    # 整理 dataframe 顯示
    rows = []
    for o in obs_list:
        rows.append({
            "id": o.get("observation_id"),
            "phase": o.get("phase"),
            "status": o.get("status"),
            "conf": o.get("verifier_confidence"),
            "tags": ", ".join((o.get("tags") or [])[:3]),
            "rec": (o.get("recommendation") or "")[:80],
            "created_at": o.get("created_at"),
        })
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)

    # 點選看細節
    selected_obs_id = st.selectbox(
        "Select observation to inspect", [""] + [r["id"] for r in rows], index=0
    )
    if selected_obs_id:
        obs = next((o for o in obs_list if o.get("observation_id") == selected_obs_id), None)
        if obs:
            st.json({k: obs.get(k) for k in (
                "observation_id", "source_trace_id", "query_hash", "phase",
                "context", "action", "result", "cause", "recommendation",
                "tags", "status", "verifier_confidence", "verifier_decision",
                "verifier_issues", "dedupe_key", "created_at",
            )})

st.markdown("---")
st.caption("v0.9.0 · Self-Learning MVP Week 6 admin page")
