# UI Refactor Plan(v0.17.0)

3 個 UX 改善的實作 spec。本 doc 是 fresh-session 接手的 checkpoint —
所有 discovery 已完成,可直接照清單執行。

**Date:** 2026-05-26
**Status:** Plan locked in,實作未開始
**Estimated effort:** 14-15 hours total(across multiple sessions)

---

## 目標總覽

| Problem | Solution | Effort | Sprint |
|---|---|---:|---|
| 1. Upload workspace 分析段獨立成頁 | Split `pages/07_upload_workspace.py` 成 2 頁 | 3-4h | A1 |
| 2. 分析過程逐步顯示(非阻塞渲染)| `handle_query(..., on_phase=callback)` | 4-5h | A2 |
| 3. Sidebar 簡約 + admin 折疊 | `st.navigation()` 分組 + app.py refactor | 5-6h | B |

---

## Sprint A1 · Split upload workspace

### 現況

`pages/07_upload_workspace.py`:**1608 行**(survey 過後實際值),含 12 sections。

### Section 邊界(已 grep 確認)

| Lines | Section | 屬於哪頁 |
|---|---|---|
| 1-67 | Imports + page_config + page header | **共用**(複製到兩頁) |
| 68-119 | MongoDB / Service init(@st.cache_resource)| **共用** |
| 122-179 | §1 Upload section | **07** |
| 180-268 | §2 Existing datasets selector | **07** |
| 269-361 | §3 Table list + sample + profile | **07** |
| 362-817 | §4-9 Metadata Review(field editor / grain / status / apply / confirm)| **07** |
| 818-1132 | §10 Chat Analysis | **08(新)** |
| 1133-1292 | §11 Save Asset | **08(新)** |
| 1293-1608 | §12 Debug Panel | **08(新)** |

### 新檔結構

#### `pages/07_data_workspace.py`(原 07,只刪 sections 10-12)

```python
"""pages/07_data_workspace.py — v0.17.0 · Data Preparation Workspace

UI 重構於 v0.17:此頁專注「資料準備」流程 —
上傳 → profile → metadata review → confirm。
分析功能拆到 pages/08_data_analysis.py。
"""
# (同 imports + page_config,只是 page_title 改 "Data Workspace")
# Sections 1-9 完全照搬
# 結尾加 button:跳到 pages/08_data_analysis.py
if last_confirmed_dataset:
    if st.button("📊 開始分析此資料集 →", type="primary"):
        st.session_state["analysis_dataset_id"] = selected_id
        st.switch_page("pages/08_data_analysis.py")
```

#### `pages/08_data_analysis.py`(新檔)

```python
"""pages/08_data_analysis.py — v0.17.0 · Data Analysis Page

UI 重構於 v0.17:從 pages/07 拆出。
Dataset picker → Chat → Progressive phase rendering → Save asset → Debug。
"""
# 同 imports + page_config(page_title: "📊 Data Analysis")
# 同 MongoDB / Service init

# v0.17.0 NEW:Dataset picker at top
def _dataset_picker(repo) -> str | None:
    """List confirmed datasets,let user pick which to analyze."""
    confirmed = repo.list_datasets(status="confirmed")
    if not confirmed:
        st.warning("⚠️ 還沒有 confirmed 的資料集。請先到「📁 資料準備」上傳並 confirm metadata。")
        if st.button("→ 前往資料準備"):
            st.switch_page("pages/07_data_workspace.py")
        st.stop()
    default = st.session_state.get("analysis_dataset_id")
    default_idx = next(
        (i for i, d in enumerate(confirmed) if d["dataset_id"] == default), 0,
    )
    options = [d["dataset_id"] for d in confirmed]
    labels = [f"{d['dataset_id']} · {d.get('dataset_name', '')}"
              for d in confirmed]
    label_to_id = dict(zip(labels, options))
    chosen_label = st.selectbox(
        "📂 選擇資料集",
        labels,
        index=default_idx,
        key="_data_analysis_picker",
    )
    chosen_id = label_to_id[chosen_label]
    st.session_state["analysis_dataset_id"] = chosen_id
    return chosen_id

selected_id = _dataset_picker(repo)
if not selected_id:
    st.stop()

# 然後 = 原 pages/07 lines 818-1608 整段拷貝(sections 10/11/12)
# 注意:原本這些 section 假設 selected_id 是 sec 2 的 selectbox 出來的,
# 現在改用上面 _dataset_picker 出的 — 變數名一樣,直接 reuse 不必改邏輯
```

#### `pages/09_saved_assets.py`(rename from 08)

`mv pages/08_saved_assets.py pages/09_saved_assets.py` — 內容不變。

### Session state migration

新增的 cross-page key:
- `analysis_dataset_id` — 跨頁傳遞當前分析的 dataset。Page 07 confirm 後寫入,Page 08 讀作 default。

既有 keys 全部本頁內 scope,不需動:
- `_upload_owner` / `_just_uploaded`(只 07 用)
- `_active_session_{dataset_id}` / `_last_result_{active_sid}`(在 08 用,因 sections 10-12 整段搬過去)
- `_rel_result_{selected_id}`(只 07 用,sections 3-5 用)

### Acceptance criteria

- [ ] `pages/07_data_workspace.py` 跑得起來,sections 1-9 全部 work
- [ ] `pages/08_data_analysis.py` 跑得起來,dataset picker + chat + save + debug 全部 work
- [ ] `pages/09_saved_assets.py` 不動,只是檔名改了
- [ ] 既有 integration tests 過(`tests/integration/test_upload_*.py`)
- [ ] 從 07 confirm 後按 button 能跳 08 自動 select 該 dataset
- [ ] 從 08 直接打開,有 confirmed dataset 該預設第一個,沒 confirmed 該顯示 warning + 引導去 07
- [ ] `st.session_state["analysis_dataset_id"]` cross-page 跨頁傳遞 work
- [ ] 既有 470 unit tests 全綠
- [ ] Manual smoke test:上傳 → confirm → 分析 → save → 在 saved assets 看到

---

## Sprint A2 · Progressive phase render

### 現況

`upload_analysis_service.handle_query(session_id, query, ...)` 同步阻塞:
- 跑完 Phase 0/A/B/C/D
- Return full result dict
- UI 等 30-60s 後一次顯示

### 提案:Callback 設計(backward compat)

#### `upload_analysis_service.py` 變更

```python
from typing import Callable, Optional

PhaseEvent = Literal["start", "complete", "error", "skipped"]

def handle_query(
    self,
    session_id: str,
    query: str,
    chart_engine: str = "ECharts",
    enable_insight: bool = True,
    on_phase: Optional[Callable[[str, str, dict], None]] = None,
) -> dict:
    """...(既有 docstring)...

    Args:
        on_phase: Optional callback(phase_id, event, payload)
            phase_id: 'phase_0_plan' | 'phase_a_pipeline' | 'phase_b_preprocess'
                       | 'phase_c_chart' | 'phase_d_insight'
            event:    'start' | 'complete' | 'error' | 'skipped'
            payload:  per-phase dict — see下表
            
            None(default) → 不發 callback,行為跟 v0.16 一致(byte-equal)。
    """
```

**Callback payload per phase + event:**

| phase_id × event | payload |
|---|---|
| `phase_0_plan` / start | `{"query": str}` |
| `phase_0_plan` / complete | `{"plan_text": str, "elapsed_s": float, "is_refusal": bool}` |
| `phase_a_pipeline` / start | `{}` |
| `phase_a_pipeline` / complete | `{"code": str, "raw_df_info": dict, "elapsed_s": float}` |
| `phase_b_preprocess` / start | `{}` |
| `phase_b_preprocess` / complete | `{"code": str, "Q_info": dict, "Q_preview_md": str, "elapsed_s": float}` |
| `phase_c_chart` / start | `{}` |
| `phase_c_chart` / complete | `{"code": str, "chart_option": dict, "use_table_fallback": bool, "elapsed_s": float}` |
| `phase_d_insight` / start | `{}` |
| `phase_d_insight` / complete | `{"insight": str, "elapsed_s": float}` |
| `phase_d_insight` / skipped | `{"reason": str}` |
| any / error | `{"phase": str, "error": str, "traceback": str}` |

#### Page 08 progressive render

```python
PHASE_META = [
    ("phase_0_plan",       "📋", "Phase 0 · 制定計畫",      1, 5),
    ("phase_a_pipeline",   "🛠️", "Phase A · 資料抽取",      2, 5),
    ("phase_b_preprocess", "🐍", "Phase B · 資料處理",      3, 5),
    ("phase_c_chart",      "🎨", "Phase C · 視覺化",        4, 5),
    ("phase_d_insight",    "🧠", "Phase D · 商業洞察",      5, 5),
]

def _start_progressive_render(query: str):
    """Returns(status, containers, callback)— for service.handle_query。"""
    status = st.status(
        f"🧠 處理中:{query[:60]}{'…' if len(query) > 60 else ''}",
        expanded=True,
    )
    containers = {pid: st.container() for pid, *_ in PHASE_META}
    elapsed_so_far = {}

    def callback(phase_id: str, event: str, payload: dict):
        emoji, label, n, total = next(
            (e, l, n, t) for p, e, l, n, t in PHASE_META if p == phase_id
        )
        if event == "start":
            status.update(label=f"{emoji} {label} · 進行中... [{n}/{total}]")
            with containers[phase_id]:
                st.markdown(f"⏳ **{emoji} {label}** · 進行中...")
        elif event == "complete":
            elapsed_so_far[phase_id] = payload.get("elapsed_s", 0)
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
                    f"❌ **{emoji} {label}** · 失敗:{payload.get('error', '')}"
                )
                with st.expander("🔍 Traceback", expanded=False):
                    st.code(payload.get("traceback", ""), language="text")
        elif event == "skipped":
            with containers[phase_id]:
                st.info(f"⏭️ **{emoji} {label}** · 跳過:{payload.get('reason', '')}")

    return status, containers, callback

def _render_phase_detail(phase_id: str, payload: dict):
    """各 phase 展開後的細節 — 對齊 schema-driven app.py 第 894-1118 行。"""
    if phase_id == "phase_0_plan":
        st.markdown(payload["plan_text"])
    elif phase_id == "phase_a_pipeline":
        st.code(payload["code"], language="python")
        st.json(payload.get("raw_df_info", {}), expanded=False)
    elif phase_id == "phase_b_preprocess":
        st.code(payload["code"], language="python")
        st.markdown(payload.get("Q_preview_md", ""))
    elif phase_id == "phase_c_chart":
        st.code(payload["code"], language="python")
    elif phase_id == "phase_d_insight":
        st.markdown(payload.get("insight", ""))

# Usage in chat handler:
status, _, callback = _start_progressive_render(query)
try:
    result = svc.handle_query(
        session_id=active_sid,
        query=query,
        chart_engine=chart_engine,
        enable_insight=enable_insight,
        on_phase=callback,
    )
    status.update(label="✅ 全部完成", state="complete", expanded=False)
except Exception as e:
    status.update(label=f"❌ 失敗:{e}", state="error", expanded=True)
    raise
```

### Service-side 編輯點

`upload_analysis_service.py._handle_query_inner`(line 224)裡有 5 個 phase 邊界,每個邊界前後加:

```python
# 前
import time
_t0 = time.time()
if on_phase:
    on_phase("phase_0_plan", "start", {"query": query})

# 跑 phase
plan_res = self.llm_service.generate_plan(query, ...)

# 後
if on_phase:
    on_phase("phase_0_plan", "complete", {
        "plan_text": plan_res["message"],
        "elapsed_s": time.time() - _t0,
        "is_refusal": plan_text.startswith("[REFUSE]"),
    })
```

5 個 phase 各加一組,共 ~30 行新增。

### Acceptance criteria

- [ ] `on_phase=None`(default)→ behavior byte-equal v0.16
- [ ] `on_phase=callback` 被傳時,5 個 phase 都觸發 start + complete event
- [ ] Phase error 時觸發 error event,後續 phase 不執行
- [ ] enable_insight=False 時 Phase D 觸發 skipped event
- [ ] Page 08 user 從第 3 秒就能看到 "Phase 0 完成"
- [ ] 既有 integration tests 過(`tests/integration/test_upload_analysis_*.py`)
- [ ] 加 2 個新 test:callback 觸發次數 + 順序

---

## Sprint B · Sidebar 重構 + app.py refactor

### 前置:Streamlit 升版

```bash
pip install -U "streamlit>=1.36"
# 也更新 requirements.txt
```

`st.navigation()` 從 1.36 起 stable。

### 步驟

**B1. extract `pages/main_chat.py` from app.py**

現 `app.py`(1300+ 行)的內容大致是:
- imports + config
- `_get_mongo_db()` / `_get_llm_service()` 等 init
- Sidebar(domain selector / new analysis button / debug toggles)
- Main chat UI + message history rendering
- Chat handler(打 LLM、跑 phases、render 結果)

拆分:
- `pages/main_chat.py` ← chat UI + handler(line ~500+)
- `app.py` ← navigation registry only(~50 行)
- 共用 logic(`_get_mongo_db()` / sidebar utilities)抽到 `app_init.py` 或 `lib/` 模組

**B2. app.py 改成 st.navigation registry**

```python
"""app.py — v0.17.0 · Navigation registry(主入口)

歷史:v0.1 - v0.16,app.py 是 schema-driven chat 的本體。v0.17 因 UI 重構,
chat 抽到 pages/main_chat.py,本檔變成純 navigation 註冊。
"""
import streamlit as st

# Page registry — 分析工作區 + Admin 兩組
PAGES = {
    "📊 分析工作區": [
        st.Page(
            "pages/main_chat.py",
            title="💬 Schema-driven 分析",
            icon="💬",
            default=True,
        ),
        st.Page(
            "pages/08_data_analysis.py",
            title="📤 Upload 分析",
            icon="📤",
        ),
        st.Page(
            "pages/07_data_workspace.py",
            title="📁 資料準備",
            icon="📁",
        ),
        st.Page(
            "pages/09_saved_assets.py",
            title="⭐ 已存圖表",
            icon="⭐",
        ),
    ],
    "⚙️ 系統管理(Admin)": [
        st.Page("pages/04_metadata.py",        title="🗂️ Metadata 編輯"),
        st.Page("pages/03_prompts.py",         title="📝 Prompt 編輯"),
        st.Page("pages/01_test_cases.py",      title="🧪 測試案例"),
        st.Page("pages/02_test_runs.py",       title="📊 測試紀錄"),
        st.Page("pages/05_task_traces.py",     title="🔍 Trace 追蹤"),
        st.Page("pages/06_learning_review.py", title="🤖 自學審核"),
    ],
}

pg = st.navigation(PAGES)
pg.run()
```

**B3. 移除 pages/0X_ prefix(optional)**

`st.navigation()` 不靠檔名排序,所以 `pages/01_test_cases.py` 可改成 `pages/test_cases.py`。但這樣會 break `st.switch_page("pages/01_test_cases.py")` 既有呼叫 — 全 codebase grep 確認沒這種引用後再改。

短期 safer:**保留 0X_ prefix**,只調 `st.Page(title=...)` 給 user 看的 label。

**B4. Page-level `st.set_page_config()` 移除**

每個 page file 開頭的 `st.set_page_config(page_title=..., ...)` 在 `st.navigation` 模式下會 warning(只能在 main app 設一次)。要把它從每 page 移到 `app.py` global。

### Acceptance criteria

- [ ] `streamlit>=1.36` 在 requirements.txt
- [ ] `streamlit run app.py` 啟動後 default 進 schema-driven chat
- [ ] Sidebar 顯示「📊 分析工作區」(expanded)+「⚙️ 系統管理」(collapsed)兩組
- [ ] Admin 組點開後 6 個 admin page 都跳得到
- [ ] schema-driven chat(原 app.py)所有功能不掉:
  - [ ] Domain selector
  - [ ] New analysis button
  - [ ] Sidebar debug toggles
  - [ ] Chat history persistence
  - [ ] Phase progressive render(既有 st.status pattern)
- [ ] 既有 470+ unit tests 過

---

## Execution checklist(下個 session 接手用)

```
☐ Sprint A1: Split upload workspace
   ☐ Read pages/07_upload_workspace.py 全文(分 chunks)
   ☐ Identify sections 10-12 exact line range(已 grep:818, 1133, 1293)
   ☐ Create pages/08_data_analysis.py:
       - imports + page_config(複製 07 header)
       - MongoDB / Service init(複製 07 cache_resource)
       - Dataset picker function
       - 從 07 line 818-1608 整段拷貝
   ☐ Edit pages/07_upload_workspace.py:
       - Rename to pages/07_data_workspace.py
       - 刪除 line 818-end 的 sections 10-12
       - 結尾加「→ 開始分析」button
   ☐ mv pages/08_saved_assets.py pages/09_saved_assets.py
   ☐ 跑 streamlit local test(手動 QA)

☐ Sprint A2: Progressive callback(在 split 完成的 page 08 上做)
   ☐ Edit upload_analysis_service.handle_query 加 on_phase kwarg
   ☐ 5 個 phase 邊界各加 2 行 callback
   ☐ Edit pages/08_data_analysis.py chat handler:
       - 新增 PHASE_META + _start_progressive_render 等 helper
       - 把同步 result 拿法改成 on_phase callback driven
   ☐ Add 2 unit tests:callback ordering + skipped event

☐ Sprint B: Sidebar reorg + app.py refactor
   ☐ pip install -U "streamlit>=1.36"
   ☐ Update requirements.txt
   ☐ Extract pages/main_chat.py from app.py(small core,大部分 chat logic)
   ☐ 共用 utilities → app_init.py(MongoDB / LLM service singletons / etc.)
   ☐ app.py 改寫成 navigation registry
   ☐ 每個 pages/0X_*.py 拿掉 set_page_config()(在 app.py 統一設)
   ☐ Verify all 9 pages reachable + functional
```

---

## Decision log

| 議題 | 選 | 為什麼 |
|---|---|---|
| Callback vs Generator vs Async | Callback | backward compat 完全保留 — 既有 caller 不傳 callback 跟現狀一樣 |
| Page splitting strategy | 3 pages | 07 資料準備 / 08 分析 / 09 已存 — 三 mental task 各自一頁 |
| app.py 是否拆出 | 是 | st.navigation 是 explicit registry,不拆的話會看不到 schema-driven 入口 |
| Streamlit upgrade | 1.36+ | 官方 navigation API stable 從這版開始 |
| Page filename prefix | 暫留 0X_ | st.switch_page 既有呼叫可能 break,留 prefix safe |
| Sprint 排序 | A1 → A2 → B | A1 split 後 page 結構穩定,A2 再加 progressive,最後 B 整理 sidebar |

---

## Risk register

| Risk | Severity | Mitigation |
|---|---|---|
| Sprint A1:session state cross-page 漏掉某個 key | M | Acceptance test 涵蓋 upload → confirm → analyze e2e |
| Sprint A2:Streamlit re-render 跟 callback timing 衝突 | M | 先 inline test on schema-driven path → confirm pattern works → 套用 upload |
| Sprint B:app.py refactor 破壞 schema-driven chat | H | extract pages/main_chat.py 第一步只 copy + verify,確認 100% 可跑後才 delete 原 app.py 內容 |
| Sprint B:既有 `st.switch_page("pages/0X_...")` 呼叫破裂 | L | grep 確認沒這種呼叫(暫時保留 prefix 也 safe) |

---

## 完工後的 release

v0.17.0 — UI Refactor

CHANGELOG entry 該涵蓋:
- 3 個問題的設計決策(用 SPRINT3_RESULT.md 同樣結構)
- 新 page 結構
- progressive render 規格
- Streamlit 升版到 1.36+
- 既有 features 不損失的證據(test 通過 + manual smoke)
