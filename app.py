"""
app.py — v0.17.0 · Navigation Registry(主入口)

歷史:v0.1 - v0.16,app.py 是 schema-driven chat 的本體。
v0.17 因 UI 重構,chat 內容抽到 pages/main_chat.py,本檔變成純 navigation 註冊。

Two sidebar groups(by st.navigation):
- 📊 分析工作區 · default expanded · 給 end user
- ⚙️ 系統管理(Admin)· default collapsed · 給 ops / dev

set_page_config 在這裡一次設定(streamlit>=1.36 規則:只能在 main app 設一次),
其他 pages/ 檔不再呼叫 set_page_config(避免 warning)。
"""

from __future__ import annotations

import sys
from pathlib import Path

# Project root 加入 sys.path,讓 pages/*.py 透過 streamlit script runner 載入時
# 能 import 頂層模組(config / llm_service / ...)
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st


# ============================================================
# Global page config(只能呼叫一次,故統一在此設)
# ============================================================
_LOGO_PATH = _PROJECT_ROOT / "assets" / "hr_chatchart_4_svg_assets" / "02_soft_app_icon.svg"
_FALLBACK_LOGO = _PROJECT_ROOT / "assets" / "genbi_logo.svg"

st.set_page_config(
    page_title="HR 話圖 · HR ChatChart",
    page_icon=(
        str(_LOGO_PATH) if _LOGO_PATH.exists()
        else (str(_FALLBACK_LOGO) if _FALLBACK_LOGO.exists() else "📊")
    ),
    layout="wide",
)


# ============================================================
# Navigation registry(st.navigation · streamlit>=1.36)
# ============================================================
PAGES = {
    "📊 分析工作區": [
        st.Page(
            "pages/main_chat.py",
            title="Schema-driven 分析",
            icon="💬",
            default=True,
        ),
        st.Page(
            "pages/08_data_analysis.py",
            title="Upload 分析",
            icon="📊",
        ),
        st.Page(
            "pages/07_data_workspace.py",
            title="資料準備",
            icon="📤",
        ),
        st.Page(
            "pages/09_saved_assets.py",
            title="已存圖表",
            icon="⭐",
        ),
    ],
    "⚙️ 系統管理(Admin)": [
        st.Page("pages/04_metadata.py",        title="Metadata 編輯",  icon="🗂️"),
        st.Page("pages/03_prompts.py",         title="Prompt 編輯",    icon="📝"),
        st.Page("pages/01_test_cases.py",      title="測試案例",        icon="🧪"),
        st.Page("pages/02_test_runs.py",       title="測試紀錄",        icon="📊"),
        st.Page("pages/05_task_traces.py",     title="Trace 追蹤",      icon="🔍"),
        st.Page("pages/06_learning_review.py", title="自學審核",        icon="🤖"),
    ],
}

pg = st.navigation(PAGES)
pg.run()
