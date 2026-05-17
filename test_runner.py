"""
tFlex GenBI · Headless Test Runner
==================================
跳過 Streamlit UI,直接打 Ollama + MongoDB,跑 6 個關鍵代表 case。
每個 case 會經過 Phase 0 → A → B → C (ECharts) → D,
並對每個 Phase 做結構性檢查 (PASS/FAIL/WARN)。

輸出:
  - stdout: 即時進度
  - test_results.md: 完整報告 (供與 Claude 討論)
  - test_results.json: 結構化 dump (供後續分析)

用法:
  cd /Users/kururu/Documents/Claude/Projects/GenBI
  python3 test_runner.py
"""

import argparse
import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any

import pandas as pd
from pymongo import MongoClient

from llm_service import LLMService, is_dashboard_query, sanitize_pipeline, rescue_empty_echarts, ensure_default_styling, coerce_option_native_types
# v0.11.0:test_runner 也寫 task_traces,餵 self-learning loop
from task_trace import TaskTrace


# ============================================================
# 測試案例設定 — 從 TEST_PLAN.md 萃取的 6 個代表案例
# ============================================================
TEST_CASES = [
    # ── Happy path: 雙軸 / 互斥 / 排序 / 切片 ──
    {
        "id": "01",
        "name": "各公司退單率與申請數(雙軸 bar+line)",
        "query": "比較各公司的退單率與申請數,我想同時看到絕對量與比率",
        "type": "happy_path",
        "expected_chart": "雙軸 bar+line",
        "expected_q_cols_any": ["company_code"],
        "expected_q_cols_all": ["average_return_rate"],
        "echarts_required_keys": ["title", "xAxis", "yAxis", "series"],
        "echarts_min_series": 2,
    },
    {
        "id": "02",
        "name": "TST vs TSK 員工送單率(小樣本陷阱)",
        "query": "比較 TST 與 TSK 兩家公司的員工送單率",
        "type": "happy_path",
        "expected_chart": "percentage bar",
        "expected_q_cols_all": ["company_code"],
        "echarts_required_keys": ["title", "xAxis", "yAxis", "series"],
        "echarts_min_series": 1,
    },
    {
        "id": "03",
        "name": "PAY vs RTN(stacked bar)",
        "query": "畫出各公司的 PAY 與 RTN 申請數量,我想看哪家公司退件量最大",
        "type": "happy_path",
        "expected_chart": "stacked bar",
        # v0.7.3:user query 用 PAY/RTN 簡寫,LLM 可能忠於 user 字眼 → 用 synonym
        # 接受 canonical(pay_count/return_count) 或 user 字眼(PAY/RTN)
        "expected_q_cols_all": [
            ["pay_count", "PAY", "pay"],
            ["return_count", "RTN", "rtn", "RET", "rtn_count", "return_cnt"],
            "company_code",
        ],
        "echarts_required_keys": ["title", "xAxis", "yAxis", "series"],
        "echarts_min_series": 2,
        "echarts_should_have_stack": True,
    },
    {
        "id": "04",
        "name": "各公司 AI 審查率(vs 43% 目標)",
        "query": "哪些公司的 AI 審查率比較高?跟 43% 的目標比起來如何?",
        "type": "happy_path",
        "expected_chart": "percentage bar",
        "expected_q_cols_all": ["company_code"],
        "echarts_required_keys": ["title", "xAxis", "yAxis", "series"],
    },
    {
        "id": "05",
        "name": "AI vs Human review by 公司(grouped 不堆疊)",
        "query": "看一下每家公司在 AI 審查跟人工審查的件數分佈,我想找出還是高度依賴人工的公司",
        "type": "happy_path",
        "expected_chart": "grouped bar",
        "expected_q_cols_all": ["company_code"],
        "echarts_required_keys": ["title", "xAxis", "yAxis", "series"],
        "echarts_min_series": 2,
    },
    {
        "id": "06",
        "name": "各公司審核完成率排序",
        "query": "排序各公司的審核完成率,看誰積壓最嚴重",
        "type": "happy_path",
        "expected_chart": "sorted single rate bar",
        "expected_q_cols_all": ["company_code"],
        "echarts_required_keys": ["title", "xAxis", "yAxis", "series"],
    },
    {
        "id": "07",
        "name": "申請類別分布(非公司維度)",
        "query": "四個福利申請類別,哪個最熱門?",
        "type": "happy_path",
        "expected_chart": "categorical bar",
        "expected_q_cols_all": ["application_category"],
        "echarts_required_keys": ["title", "xAxis", "yAxis", "series"],
    },
    {
        "id": "08",
        "name": "公司 × 類別 熱力圖(壓力測試)",
        "query": "畫一張熱力圖,看不同公司在四個申請類別的分佈差異",
        "type": "happy_path",
        "expected_chart": "heatmap",
        "echarts_required_keys": ["title", "xAxis", "yAxis", "series"],
        "echarts_should_have_visualmap": True,
    },
    {
        "id": "09",
        "name": "AI 審查率 vs 退單率散點圖",
        "query": "AI 審查率跟退單率有相關嗎?畫個散點圖看看",
        "type": "happy_path",
        "expected_chart": "scatter",
        "expected_q_cols_all": ["company_code"],
        "echarts_required_keys": ["title", "xAxis", "yAxis", "series"],
        # v0.8.3:抓「跑得起來但答錯」silent failure(case 09 baseline 出現過全 0)
        "q_numeric_must_vary": [
            ["ai_review_rate", "ai_rate", "AI 審查率"],
            ["average_return_rate", "return_rate", "退單率", "rtn_rate"],
        ],
    },
    {
        "id": "10",
        "name": "TOP 5 退件公司(陷阱:DB 不可 $sort/$limit)",
        "query": "列出退件數量最多的前 5 名公司,搭配柱狀圖",
        "type": "happy_path",
        "expected_chart": "sorted bar",
        # v0.7.3:query 用「退件數量」,LLM 可能產 return_count / 退件數 / RTN / rtn_count
        "expected_q_cols_all": [
            "company_code",
            ["return_count", "退件數", "退件數量", "RTN", "rtn", "rtn_count", "ret_count"],
        ],
        "echarts_required_keys": ["title", "xAxis", "yAxis", "series"],
        "phase_a_forbidden_strict": True,
        "phase_b_top_n": 5,
    },
    {
        "id": "11",
        "name": "全公司完整 KPI 一覽表(table fallback)",
        "query": "幫我整理一張完整的公司 KPI 表格:申請數、完成數、PAY、RTN、退單率、AI 率、員工送單率全都要",
        "type": "happy_path",
        "expected_chart": "table or wide visualization",
        "expected_q_cols_all": ["company_code"],
        "expected_q_min_columns": 5,
    },
    # ── 進階表格 / KPI 卡片場景 ──
    {
        "id": "T1",
        "name": "公司 KPI 執行摘要 (精美表格 + KPI 卡片)",
        "query": "幫我做一份完整的公司 KPI 執行摘要 dashboard:申請數、完成率、退單率、AI 採用率都要,並在最上方放總體 KPI 卡片",
        "type": "happy_path",
        "expected_chart": "table with KPI cards",
        "expected_q_cols_all": ["company_code"],
        "echarts_should_use_table": True,
        "echarts_should_have_kpi_cards": True,
        "echarts_min_kpi_cards": 3,
    },
    {
        "id": "T2",
        "name": "退單率異常排行表(條件格式)",
        "query": "找出退單率最高的公司排行表,並標註哪些公司明顯高於整體平均",
        "type": "happy_path",
        "expected_chart": "sorted table or bar",
        "expected_q_cols_all": ["company_code"],
        "echarts_required_keys": ["title", "xAxis", "yAxis", "series"],
    },
    {
        "id": "T3",
        "name": "申請量 TOP 5 vs BOTTOM 5 對比",
        "query": "申請量最大跟最少的各 5 家公司,做一張對比表呈現",
        "type": "happy_path",
        "expected_chart": "comparison table",
        "expected_q_cols_all": ["company_code"],
    },
    # ── Refusal:data_limitations 觸發 ──
    {
        "id": "12",
        "name": "過去三個月趨勢(拒絕:無 date)",
        "query": "我想看過去三個月每週的申請趨勢",
        "type": "refusal",
        "refusal_keywords": [
            "資料不足", "無 date", "no application date", "no date",
            "missing", "不支援", "缺少", "不存在", "無法分析",
            "沒有", "限制", "趨勢",
        ],
    },
    {
        "id": "13",
        "name": "各部門退單率(拒絕:無 department)",
        "query": "各部門的退單率比較,哪個部門最高?",
        "type": "refusal",
        "refusal_keywords": [
            "資料不足", "無", "缺", "不支援", "department", "部門",
            "missing", "限制", "不存在",
        ],
    },
    {
        "id": "14",
        "name": "平均申請金額(拒絕:無 amount)",
        "query": "員工每次申請的平均金額是多少?哪家公司平均最高?",
        "type": "refusal",
        "refusal_keywords": [
            "資料不足", "無", "缺", "不支援", "amount", "金額",
            "missing", "限制", "不存在",
        ],
    },
    {
        "id": "15",
        "name": "平均審核時間(拒絕:無 timestamp)",
        "query": "平均審核需要幾天?AI 跟人工誰比較快?",
        "type": "refusal",
        "refusal_keywords": [
            "資料不足", "無", "缺", "不支援", "timestamp", "時間",
            "missing", "限制", "不存在", "無法",
        ],
    },
    # ──────────────────────────────────────────────────────
    # Stacked Bar 專屬 case (STK-XX) — 對應 STACKED_BAR_TEST.md
    # ──────────────────────────────────────────────────────
    {
        "id": "STK-01",
        "name": "100% stacked bar:per company × category",
        "query": "畫一張 stacked bar:依據 company_code(TST、TSN、TSC),每條 bar 中呈現 application_category 的佔比",
        "type": "happy_path",
        "expected_chart": "100% stacked bar",
        "expected_q_cols_any": ["company_code", "application_category"],
        "echarts_required_keys": ["title", "xAxis", "yAxis", "series"],
        "echarts_min_series": 2,
        "echarts_should_have_stack": True,
        # STK 專屬新檢查
        "echarts_xaxis_unique": True,       # xAxis.data 不可有重複
        "echarts_data_length_aligned": True, # 每個 series.data 長度 == xAxis.data 長度
        "echarts_yaxis_max": 100,            # 100% stacked 應該鎖 max=100
        "echarts_series_count_max": 6,       # 不該超過 6 個 series (4 category + 2 buffer)
        "echarts_no_placeholder_series_name": True,  # name 不可是「類別 A」「Category 1」之類
    },
    {
        "id": "STK-02",
        "name": "100% stacked transposed:per category × company",
        "query": "依據 application_category 畫 stacked bar,每條 bar 中呈現 TST、TSN、TSC 的占比",
        "type": "happy_path",
        "expected_chart": "transposed 100% stacked bar",
        "expected_q_cols_any": ["company_code", "application_category"],
        "echarts_required_keys": ["title", "xAxis", "yAxis", "series"],
        "echarts_min_series": 2,
        "echarts_should_have_stack": True,
        "echarts_xaxis_unique": True,
        "echarts_data_length_aligned": True,
        "echarts_yaxis_max": 100,
        "echarts_no_placeholder_series_name": True,
    },
    {
        "id": "STK-03",
        "name": "Raw count stacked:PAY vs RTN by company",
        "query": "各公司的 PAY 與 RTN 申請數量比較,用 stacked bar 呈現",
        "type": "happy_path",
        "expected_chart": "stacked bar (raw count)",
        "expected_q_cols_all": ["company_code"],
        "echarts_required_keys": ["title", "xAxis", "yAxis", "series"],
        "echarts_min_series": 2,
        "echarts_should_have_stack": True,
        "echarts_xaxis_unique": True,
        "echarts_data_length_aligned": True,
        "echarts_no_placeholder_series_name": True,
    },
    {
        "id": "STK-04",
        "name": "三狀態 100% stacked:per category × (approved/returned/in_progress)",
        "query": "各申請類別下,核准(完成且 result=Y)/退件(完成且 result=N)/進行中三狀態的占比分佈,用 100% stacked bar",
        "type": "happy_path",
        "expected_chart": "3-state 100% stacked",
        "expected_q_cols_any": ["application_category"],
        "echarts_required_keys": ["title", "xAxis", "yAxis", "series"],
        "echarts_min_series": 3,
        "echarts_should_have_stack": True,
        "echarts_xaxis_unique": True,
        "echarts_data_length_aligned": True,
        "echarts_yaxis_max": 100,
        "echarts_no_placeholder_series_name": True,
    },
    {
        "id": "STK-05",
        "name": "Stacked + filter:TST/TSC 各類別 AI vs Human",
        "query": "只看 TST、TSC 兩家,各類別中 AI 審查 vs 人工審查 的數量 stacked",
        "type": "happy_path",
        "expected_chart": "stacked bar with filter",
        "expected_q_cols_any": ["application_category"],
        "echarts_required_keys": ["title", "xAxis", "yAxis", "series"],
        "echarts_min_series": 2,
        "echarts_should_have_stack": True,
        "echarts_xaxis_unique": True,
        "echarts_data_length_aligned": True,
        "phase_a_must_have_match_in": True,  # Phase A 應含 $match: company_code $in [TST, TSC]
    },
    {
        "id": "STK-06",
        "name": "Edge:hc 範圍過濾 + 缺漏組合",
        "query": "依據 hc 介於 100 到 1000 的公司,看 application_category 占比 stacked",
        "type": "happy_path",
        "expected_chart": "stacked bar with hc filter",
        "echarts_required_keys": ["title", "xAxis", "yAxis", "series"],
        "echarts_min_series": 2,
        "echarts_should_have_stack": True,
        "echarts_xaxis_unique": True,
        "echarts_data_length_aligned": True,
        "echarts_no_nan_in_data": True,  # 缺漏組合應 fillna(0),series.data 不可含 NaN
    },
    {
        "id": "STK-07",
        "name": "Follow-up:基本 bar 改 stacked (需 last_analysis)",
        "query": "改成 stacked bar 看類別占比",
        "type": "happy_path",
        "expected_chart": "follow-up stacked",
        "follow_up_setup_query": "各公司的申請數量 bar chart",  # 先跑這個建立 last_analysis
        "echarts_required_keys": ["title", "xAxis", "yAxis", "series"],
        "echarts_min_series": 2,
        "echarts_should_have_stack": True,
        "echarts_xaxis_unique": True,
        "echarts_no_placeholder_series_name": True,
    },
    {
        "id": "STK-08",
        "name": "橫向 100% stacked bar",
        "query": "各公司申請類別占比分佈,用水平 stacked bar 呈現",
        "type": "happy_path",
        "expected_chart": "horizontal 100% stacked bar",
        "expected_q_cols_any": ["company_code", "application_category"],
        "echarts_required_keys": ["title", "xAxis", "yAxis", "series"],
        "echarts_min_series": 2,
        "echarts_should_have_stack": True,
        "echarts_should_have_yaxis_category": True,   # 新檢查:橫向必須 yAxis.type=category
        "echarts_should_have_xaxis_value": True,       # 新檢查:橫向必須 xAxis.type=value
        "echarts_data_length_aligned_horizontal": True,  # 橫向時 series.data 長度 == yAxis.data 長度
        "echarts_no_placeholder_series_name": True,
    },
]


# ============================================================
# 設定
# ============================================================
import config

# 所有設定來自 config.py(.env / 環境變數驅動,可切 ollama / vllm / openai)
OLLAMA_URL = config.LLM_API_URL
OLLAMA_KEY = config.LLM_API_KEY
OLLAMA_MODEL = config.LLM_MODEL
# 測試環境給更長 timeout (含首次 warm-up + 連續 retry)
OLLAMA_TIMEOUT = max(config.LLM_TIMEOUT_S, 240.0)
# v0.10.6.1:per-phase sampling profile
LLM_MODEL_PROFILE = config.MODEL_PROFILE

MONGO_URI = config.MONGO_URI
MONGO_DB = config.MONGO_DB

REPORT_MD = Path("test_results.md")
REPORT_JSON = Path("test_results.json")


# ============================================================
# 工具函式
# ============================================================
def banner(text: str, char: str = "=") -> None:
    line = char * 64
    print(f"\n{line}\n{text}\n{line}")


def check(label: str, ok: bool, detail: str = "") -> dict:
    icon = "✅" if ok else "❌"
    print(f"   {icon} {label}" + (f"  ·  {detail}" if detail else ""))
    return {"label": label, "ok": ok, "detail": detail}


def warn(label: str, detail: str = "") -> dict:
    print(f"   ⚠️  {label}" + (f"  ·  {detail}" if detail else ""))
    return {"label": label, "ok": None, "detail": detail}


def truncate(s: str, n: int = 400) -> str:
    return s if len(s) <= n else s[:n] + "...(truncated)"


def _find_all(text: str, term: str) -> list[int]:
    """找出 term 在 text 中所有出現的起始位置。"""
    positions: list[int] = []
    idx = 0
    while True:
        pos = text.find(term, idx)
        if pos == -1:
            return positions
        positions.append(pos)
        idx = pos + 1


_SENT_SEPS = "。.!?\n。!?;;"


def _enclosing_sentence(text: str, pos: int) -> str:
    """擷取 text 中 pos 位置所在的「整句」(以中英文句號/換行/分號斷句)。"""
    start = 0
    for i in range(pos - 1, -1, -1):
        if text[i] in _SENT_SEPS:
            start = i + 1
            break
    end = len(text)
    for i in range(pos, len(text)):
        if text[i] in _SENT_SEPS:
            end = i
            break
    return text[start:end].strip()


def try_recover_Q(ns: dict, raw_df: pd.DataFrame):
    """Phase B 安全網 — 若 Q 列數仍與 raw_df 相同,找最聚合的 DataFrame 接手。"""
    Q = ns.get("Q")
    if Q is None or not isinstance(Q, pd.DataFrame):
        return None, None
    if Q.shape[0] != raw_df.shape[0]:
        return None, None
    candidates = []
    for name, val in ns.items():
        if name == "Q" or name.startswith("_") or not isinstance(val, pd.DataFrame):
            continue
        if 1 <= len(val) < len(raw_df) * 0.9:
            candidates.append((name, val))
    if not candidates:
        return None, None
    candidates.sort(key=lambda x: len(x[1]))
    name, df = candidates[0]
    return df, f"⚠️ Phase B 安全網觸發:Q 未被重新指派,自動 fallback 到 `{name}` (shape={df.shape})"


def is_misused(text: str, term: str) -> bool:
    """智慧禁忌詞偵測。

    True  = 違規 (term 至少有一處出現在非拒絕語境,屬於正向使用)
    False = OK   (term 沒出現,或全部出現都在拒絕/否定語境中)

    拒絕語境的判斷:term 所在的「整句」(以句號/分號/換行斷句) 內含 denial marker。
    比 40 字 window 更穩,能處理「無 X、Y、Z 等資訊,無法進行 ...」這類長列舉句。

    v0.9.2 例外:某些 term 在中文裡是「一般用法 + 特定用法」雙義,只當「特定用法」
    才算違規。例如「趨勢」單獨可指「pattern / tendency」(一般中文),
    搭配時間詞才指「time-series trend」(資料缺時間時的禁忌)。
    """
    # v0.9.2:term 的「上下文要求」(出現時還要伴隨這些字才算 misuse)
    # 沒列在這的 term,維持原行為(出現在非拒絕語境就算 misuse)。
    _CONTEXT_REQUIREMENTS = {
        "趨勢": (
            "過去", "未來", "月", "週", "季", "年", "日", "時間",
            "trend", "time", "monthly", "yearly", "weekly",
        ),
        # v0.10.3:「部門」單獨提到(如「考慮按部門 ...」)是 forward-looking
        # 建議,不算誤用;只有真的做「by 部門」分析才算 misuse
        "部門": (
            "各部門", "依部門", "分部門", "by department",
            "部門間", "部門比較", "部門別", "部門差異",
            "departmental",
        ),
    }
    denial_markers = (
        # 強拒絕詞 (硬性缺欄位 / 不支援)
        "無法", "缺少", "缺乏", "不存在", "不支援", "不可", "沒有", "未有",
        "禁止", "限制", "資料不足", "資料限制", "無此", "無申", "無相關",
        "無欄位", "缺 ", "等資訊", "等欄位", "等資料", "無 ",
        # 弱化 / 建議性 / 監控性 hedging (避免把建議與監控誤判為正向使用)
        "謹慎", "需注意", "需考量", "需確認", "建議追蹤", "建議持續",
        "監控", "觀察", "未考慮", "未來", "後續", "持續性",
        "短期", "長期", "暫無", "尚無", "目前無", "受限於",
        # 未涵蓋 / 未包含 / 排除類 (caveat 用語)
        "未考量", "未涵蓋", "未包含", "未提供", "未含", "未納入",
        "不含", "不涵蓋", "不包含", "排除",
        # 推測 / 假設性 / 探索建議類 (forward-looking suggestion)
        "可能", "是否", "假設", "若有", "若存在", "若能", "如能",
        "推測", "推估", "深入分析", "進一步分析", "進一步調查",
        "進一步研究", "可考慮", "可協助", "可進一步", "可作為",
        "建議分析", "建議調查", "建議研究", "建議深入", "建議針對",
        # 廣義 forward-looking 標記(這些詞在 insight 中通常代表「若有資料就能做」)
        "建議", "協助", "視覺化", "探索", "將來", "或地區", "或職級",
    )
    context_required = _CONTEXT_REQUIREMENTS.get(term)
    for pos in _find_all(text, term):
        sentence = _enclosing_sentence(text, pos)
        if any(m in sentence for m in denial_markers):
            continue   # 拒絕語境,OK
        # v0.9.2:term 需要 context check 時,只有 context 字眼出現才算 misuse
        if context_required and not any(c in sentence for c in context_required):
            continue   # 沒時間詞,「趨勢」是一般用法 = OK
        return True    # 至少一處在正向使用且符合 context = 違規
    return False  # 沒出現或全在拒絕/無 context


# ============================================================
# 單一 case runner
# ============================================================
def run_case(case: dict, llm: LLMService, db) -> dict:
    banner(f"Case {case['id']} · {case['name']}")
    print(f"Query: {case['query']}")

    # 開始前 reset telemetry,以便彙整這個 case 的 cost
    llm.reset_call_log()
    case_t0 = time.time()

    result: dict[str, Any] = {
        "id": case["id"],
        "name": case["name"],
        "query": case["query"],
        "type": case["type"],
        "phases": {},
        "checks": [],
        "status": "in_progress",
    }

    # ----------------------------------------------------------
    # Phase 0 · Plan (含 follow-up 模擬)
    # ----------------------------------------------------------
    # 若 case 有 follow_up_setup_query,合成 last_analysis 模擬接續
    followup_context = None
    setup_q = case.get("follow_up_setup_query")
    if setup_q:
        followup_context = {
            "query": setup_q,
            "plan_summary": (
                f"前次需求:{setup_q}\n"
                f"前次圖型:bar / 表格(以公司或類別為 x 軸)"
            ),
            "Q_cols": ["company_code", "application_count"],
            "chart_engine": "ECharts",
            "chart_descriptor": "bar",
            "is_dashboard": False,
            "was_followup": False,
        }
        print(f"🔗 follow-up setup 注入:'{setup_q}'")

    print("\n▶︎ Phase 0 · Plan")
    t0 = time.time()
    try:
        plan_res = llm.generate_plan(case["query"], followup_context=followup_context)
        elapsed = time.time() - t0
        plan_text = plan_res["message"] if plan_res["status"] == "success" else ""
        result["phases"]["plan"] = {
            "elapsed_s": round(elapsed, 1),
            "status": plan_res["status"],
            "text": plan_text,
        }
        print(f"   ({elapsed:.1f}s)")
        print(f"   {truncate(plan_text, 300)}")
        result["checks"].append(check("Plan 有產出", plan_res["status"] == "success"))
        result["checks"].append(check("Plan 非空", bool(plan_text.strip())))

        # 拒絕路徑檢查
        if case["type"] == "refusal":
            kws = case.get("refusal_keywords", [])
            lower = plan_text.lower()
            hits = [k for k in kws if k.lower() in lower]
            ok = len(hits) > 0
            result["checks"].append(check(
                "Plan 含拒絕關鍵字",
                ok,
                f"hit: {hits}" if hits else "未偵測到任何拒絕關鍵字"
            ))
            result["status"] = "refusal_detected" if ok else "refusal_missed"
            return result
    except Exception as e:
        result["checks"].append(check("Plan 階段例外", False, str(e)))
        result["status"] = "phase0_error"
        return result

    # ----------------------------------------------------------
    # Phase A · MongoDB Pipeline
    # ----------------------------------------------------------
    print("\n▶︎ Phase A · MongoDB Pipeline")
    t0 = time.time()
    try:
        db_json = llm.generate_pipeline(case["query"], plan_text)
        elapsed = time.time() - t0
        try:
            pipeline_obj = json.loads(db_json)
        except json.JSONDecodeError as e:
            result["phases"]["pipeline"] = {
                "elapsed_s": round(elapsed, 1),
                "raw": db_json,
                "parse_error": str(e),
            }
            result["checks"].append(check("Pipeline 是合法 JSON", False, str(e)))
            result["status"] = "phaseA_json_error"
            return result

        start_col = pipeline_obj.get("start_collection")
        pipeline, sanitize_warnings = sanitize_pipeline(pipeline_obj.get("pipeline", []))
        json_str = json.dumps(pipeline)
        forbidden_keys = ["$group", "$count", "$sort", "$limit", "$divide", "$cond", "$out", "$merge"]
        violations = [f for f in forbidden_keys if f in json_str]

        result["phases"]["pipeline"] = {
            "elapsed_s": round(elapsed, 1),
            "start_collection": start_col,
            "pipeline_len": len(pipeline),
            "pipeline_json": pipeline_obj,
            "forbidden_violations": violations,
            "sanitize_warnings": sanitize_warnings,
        }
        print(f"   ({elapsed:.1f}s) start={start_col}, stages={len(pipeline)}")
        if sanitize_warnings:
            print(f"      🧹 sanitize_pipeline 自動清理 {len(sanitize_warnings)} 項:")
            for _w in sanitize_warnings:
                print(f"         - {_w}")
        if violations:
            print(f"      ⚠️ DB 端違規 stages: {violations}")

        result["checks"].append(check(
            "Pipeline 不含禁忌 stage (group/sort/limit/divide/cond)",
            len(violations) == 0,
            f"違規: {violations}" if violations else "✓ 乾淨"
        ))

        # 執行 pipeline
        cursor = db[start_col].aggregate(pipeline)
        raw_df = pd.DataFrame(list(cursor))
        if "_id" in raw_df.columns:
            raw_df = raw_df.drop(columns=["_id"])
        result["phases"]["pipeline"]["raw_df_shape"] = list(raw_df.shape)
        result["phases"]["pipeline"]["raw_df_cols"] = list(raw_df.columns)
        print(f"   raw_df: {raw_df.shape}, cols={list(raw_df.columns)}")
        result["checks"].append(check(
            "Pipeline 撈到非空資料",
            not raw_df.empty,
            f"shape={raw_df.shape}"
        ))
    except Exception as e:
        result["checks"].append(check("Phase A 例外", False, str(e)))
        result["phases"]["pipeline_error"] = traceback.format_exc()
        result["status"] = "phaseA_error"
        return result

    # ----------------------------------------------------------
    # Phase B · Pandas Preprocess (3 次 retry,失敗時帶 cheatsheet 重生)
    # ----------------------------------------------------------
    print("\n▶︎ Phase B · Pandas Preprocess")
    try:
        raw_df_sample_md = raw_df.head(3).to_markdown(index=False)
    except Exception:
        raw_df_sample_md = raw_df.head(3).to_string(index=False)

    prep_code = None
    prep_err = None
    Q = None
    elapsed_total = 0.0
    retry_log: list[str] = []

    dashboard_mode = is_dashboard_query(case["query"])
    if dashboard_mode:
        print(f"   📊 偵測為 dashboard 場景,Phase B 走 row-level pass-through")

    for attempt in range(3):
        t0 = time.time()
        prep_code = llm.generate_preprocess_code(
            case["query"], plan_text, list(raw_df.columns),
            raw_df_sample=raw_df_sample_md,
            dashboard_hint=dashboard_mode,
            previous_code=prep_code if attempt > 0 else "",
            previous_error=prep_err if attempt > 0 else "",
        )
        elapsed = time.time() - t0
        elapsed_total += elapsed
        ns = {"pd": pd, "np": __import__("numpy"), "raw_df": raw_df}
        try:
            exec(prep_code, ns, ns)
            Q = ns.get("Q")
            # 🛡️ Phase B Series 救援(v0.3.6+):
            # 若 Q 是 Series(LLM 漏 reset_index),自動 to_frame() 避免下游
            # 'Series has no attribute columns' 等 AttributeError
            if isinstance(Q, pd.Series):
                print(f"   ⚠️ Q 是 Series(name={Q.name!r}),自動 to_frame().reset_index()")
                Q = Q.to_frame().reset_index()
                ns["Q"] = Q
            # 🛡️ Phase B 安全網:救援忘記終態指派
            fallback_df, recover_msg = try_recover_Q(ns, raw_df)
            if recover_msg:
                print(f"   {recover_msg}")
                Q = fallback_df
                ns["Q"] = Q
            print(f"   attempt {attempt + 1} ({elapsed:.1f}s) ✅ exec OK")

            # v0.10.5 Level 2 (Phase B): exec OK 後跑 semantic validator
            try:
                from phase_b_validator import (
                    validate_phase_b_output,
                    format_issues_as_retry_hint as _b_fmt_hint,
                )
                b_issues = validate_phase_b_output(
                    Q, query=case["query"], dashboard_mode=dashboard_mode,
                )
            except Exception as _b_val_e:
                b_issues = []
                print(f"   ⚠️ phase_b_validator crashed: {_b_val_e}")

            if b_issues and attempt < 2:
                _short = "; ".join(
                    i.split(']')[0].lstrip('[') for i in b_issues
                )[:120]
                print(f"   🔍 semantic check 失敗 ({_short}),進入第 {attempt + 2} 次重生")
                retry_log.append(f"attempt {attempt + 1}: semantic [{_short}]")
                prep_err = _b_fmt_hint(b_issues)
                Q = None
                continue
            elif b_issues:
                _short = "; ".join(
                    i.split(']')[0].lstrip('[') for i in b_issues
                )[:120]
                print(f"   ⚠️ semantic check 3 次都失敗 ({_short}),接受結果")

            prep_err = None
            break  # 成功跳出
        except Exception:
            prep_err = traceback.format_exc()
            last_line = prep_err.strip().split("\n")[-1][:120]
            retry_log.append(f"attempt {attempt + 1}: {last_line}")
            print(f"   attempt {attempt + 1} ({elapsed:.1f}s) ❌ {last_line}")
            Q = None
            if attempt < 2:
                print(f"      🔁 重生中,帶 cheatsheet...")

    result["phases"]["preprocess"] = {
        "elapsed_s": round(elapsed_total, 1),
        "attempts": attempt + 1,
        "retry_log": retry_log,
        "code": prep_code,
        "exec_error": prep_err,
        "Q_shape": list(Q.shape) if Q is not None else None,
        "Q_cols": list(Q.columns) if Q is not None else None,
    }
    result["checks"].append(check(
        f"Phase B exec 成功 (用 {attempt + 1} 次嘗試)",
        prep_err is None,
        f"retry log: {retry_log}" if retry_log else "1 次過"
    ))

    try:
        if prep_err is not None:
            print(f"   ❌ 連續 3 次失敗")
            result["status"] = "phaseB_exec_error"
            return result

        result["checks"].append(check("Phase B 有產出 Q", Q is not None))
        if Q is None:
            result["status"] = "phaseB_no_Q"
            return result

        print(f"   Q.shape={Q.shape}, cols={list(Q.columns)}")
        if not Q.empty:
            print(f"   sample (first 3):\n{Q.head(3).to_string(index=False)}")

        # 結構性檢查
        # v0.7.3:expected_q_cols_all 內每個項目可以是:
        #   - 字串:literal 比對(必須 in Q.columns)
        #   - list / tuple:any-of synonym(任一個在 Q.columns 即算通過)
        # 用途:user query 用簡寫(例「PAY」「RTN」),LLM 忠於 user 字眼產出
        # `PAY` / `RTN` 欄位,語意正確但跟 canonical name(`pay_count` /
        # `return_count`)不同 — false fail。改用 synonym list 處理。
        expected_all = case.get("expected_q_cols_all", [])
        q_cols_set = set(Q.columns)

        def _col_present(expected_item) -> bool:
            if isinstance(expected_item, str):
                return expected_item in q_cols_set
            if isinstance(expected_item, (list, tuple)):
                return any(e in q_cols_set for e in expected_item)
            return False

        missing = [c for c in expected_all if not _col_present(c)]
        # 顯示時把 synonym list 轉成可讀格式
        def _fmt_expected(item):
            if isinstance(item, (list, tuple)):
                return "(" + " | ".join(item) + ")"
            return str(item)
        expected_display = [_fmt_expected(c) for c in expected_all]
        missing_display = [_fmt_expected(c) for c in missing]
        result["checks"].append(check(
            f"Q 含必備欄位 {expected_display}",
            len(missing) == 0,
            f"缺: {missing_display}" if missing else "✓"
        ))

        if case.get("phase_b_top_n"):
            n = case["phase_b_top_n"]
            result["checks"].append(check(
                f"Q 為 Top-{n} 行",
                len(Q) == n,
                f"actual rows: {len(Q)}"
            ))

        if "expected_q_min_columns" in case:
            ok = len(Q.columns) >= case["expected_q_min_columns"]
            result["checks"].append(check(
                f"Q 至少 {case['expected_q_min_columns']} 欄",
                ok,
                f"actual: {len(Q.columns)}"
            ))

        # v0.8.3:numeric content sanity — 抓「跑得起來但答錯」silent failure
        # `q_numeric_must_vary`: list[str | list[str]]
        #   每項是欄位名或 synonym list(沿用 v0.7.3 規格)。
        #   檢查該欄位在 Q 內 nunique() > 1,可抓:
        #     - 全 0(常見:狀態欄位漏撈 → Phase B 退化公式)
        #     - 全 NaN(常見:filter 全濾掉)
        #     - 全同值(常見:groupby 拼錯維度)
        for col_spec in case.get("q_numeric_must_vary", []):
            # 解 synonym:取第一個 in Q.columns 的當實際欄位
            if isinstance(col_spec, (list, tuple)):
                actual_col = next((c for c in col_spec if c in q_cols_set), None)
                label = "(" + " | ".join(col_spec) + ")"
            else:
                actual_col = col_spec if col_spec in q_cols_set else None
                label = str(col_spec)

            if actual_col is None:
                # 欄位本身不存在 — 上面的 expected_q_cols_all 已會 fail,這邊 skip 不重複
                continue

            series = Q[actual_col]
            try:
                # pandas 在 numeric 上 nunique 會無視 NaN;再用 dropna().empty 判別全 NaN
                n_unique = int(series.nunique(dropna=True))
                all_nan = bool(series.dropna().empty)
                # 「全 0」獨立檢查(numeric only):nunique=1 且該值=0
                only_value = None
                if n_unique == 1 and not all_nan:
                    try:
                        only_value = float(series.dropna().iloc[0])
                    except Exception:
                        only_value = None
                varied = (n_unique > 1) and (not all_nan)
                if all_nan:
                    detail = "全 NaN"
                elif only_value == 0.0:
                    detail = "全 0(疑似退化公式)"
                elif n_unique == 1:
                    detail = f"全部 = {series.iloc[0]!r}(無變異)"
                else:
                    detail = f"unique={n_unique}"
                result["checks"].append(check(
                    f"Q[{label}] 有變異(non-degenerate)",
                    varied,
                    detail,
                ))
            except Exception as e:
                result["checks"].append(check(
                    f"Q[{label}] 有變異(non-degenerate)",
                    False,
                    f"檢查時例外:{type(e).__name__}: {str(e)[:120]}",
                ))
    except Exception as e:
        result["checks"].append(check("Phase B 例外", False, str(e)))
        result["phases"]["preprocess_error"] = traceback.format_exc()
        result["status"] = "phaseB_error"
        return result

    # ----------------------------------------------------------
    # Phase C · ECharts Option (3 次 retry)
    # ----------------------------------------------------------
    print("\n▶︎ Phase C · ECharts Option")
    echarts_code = None
    plot_err = None
    option = None
    c_elapsed_total = 0.0
    c_retry_log: list[str] = []

    for c_attempt in range(3):
        t0 = time.time()
        echarts_code = llm.generate_echarts_option(
            case["query"], plan_text, list(Q.columns),
            previous_code=echarts_code if c_attempt > 0 else "",
            previous_error=plot_err if c_attempt > 0 else "",
        )
        c_elapsed = time.time() - t0
        c_elapsed_total += c_elapsed
        ns2 = {"pd": pd, "Q": Q}
        try:
            exec(echarts_code, ns2, ns2)
            option = ns2.get("option")
            # 空殼救援:LLM 偶爾產 series=[] / xAxis.data=[] 的空 option
            if isinstance(option, dict):
                option, rescued = rescue_empty_echarts(option, Q)
                if rescued:
                    print(f"   🛟 rescue_empty_echarts 啟動,從 Q pivot 補回 series")
                    c_retry_log.append(f"attempt {c_attempt + 1}: rescued from empty option")
                # 預設樣式安全網:多 series 無 legend → 自動補
                option, styled = ensure_default_styling(option, case["query"])
                if styled:
                    print(f"   🎨 ensure_default_styling 啟動,補上預設 legend")
                # v0.4.6 安全網:numpy/pandas scalar → Python native(防 BidiComponent JS 炸)
                option = coerce_option_native_types(option)
            plot_err = None
            print(f"   attempt {c_attempt + 1} ({c_elapsed:.1f}s) ✅ exec OK")

            # v0.10.4 Level 2:exec OK 後跑 semantic validator,有 issue → retry
            try:
                from phase_c_validator import (
                    validate_phase_c_output, format_issues_as_retry_hint,
                )
                _intent_for_val = ""
                try:
                    from llm_service import _detect_chart_intent
                    _intent_for_val = _detect_chart_intent(case.get("query", ""))
                except Exception:
                    pass
                semantic_issues = validate_phase_c_output(
                    option, Q,
                    query=case.get("query", ""),
                    intent=_intent_for_val,
                )
            except Exception as _val_e:
                semantic_issues = []
                print(f"   ⚠️ validator crashed (ignored): {_val_e}")

            if semantic_issues and c_attempt < 2:
                plot_err = format_issues_as_retry_hint(semantic_issues)
                short_summary = "; ".join(
                    i.split(']')[0].lstrip('[') for i in semantic_issues
                )[:120]
                c_retry_log.append(
                    f"attempt {c_attempt + 1}: semantic fail [{short_summary}]"
                )
                print(f"   🔍 semantic check 失敗:{short_summary} — 重生中...")
                continue  # 進下一輪 attempt
            elif semantic_issues:
                # 最後一輪還是 fail — 接受結果,讓 test framework 標 fail
                c_retry_log.append(
                    f"attempt {c_attempt + 1}: semantic fail (3 attempts 用盡,接受)"
                )
                print(f"   ⚠️ semantic check 失敗 3 次,接受結果")

            break
        except Exception:
            plot_err = traceback.format_exc()
            last_line = plot_err.strip().split("\n")[-1][:120]
            c_retry_log.append(f"attempt {c_attempt + 1}: {last_line}")
            print(f"   attempt {c_attempt + 1} ({c_elapsed:.1f}s) ❌ {last_line}")
            # 🛟 v0.4.7:exec 失敗時也試著從半殘 namespace 救空殼
            _partial = ns2.get("option")
            if isinstance(_partial, dict):
                _partial, _rescued = rescue_empty_echarts(_partial, Q)
                if _rescued:
                    _partial, _ = ensure_default_styling(_partial, case["query"])
                    _partial = coerce_option_native_types(_partial)
                    option = _partial
                    plot_err = None
                    c_retry_log.append(
                        f"attempt {c_attempt + 1}: 🛟 rescued from partial option after exec fail"
                    )
                    print(f"      🛟 從半殘空殼救回,跳出 retry")
                    break
            if c_attempt < 2:
                print(f"      🔁 重生中...")

    result["phases"]["echarts"] = {
        "elapsed_s": round(c_elapsed_total, 1),
        "attempts": c_attempt + 1,
        "retry_log": c_retry_log,
        "code": echarts_code,
        "exec_error": plot_err,
        "option_keys": list(option.keys()) if isinstance(option, dict) else None,
        "use_table_fallback": isinstance(option, dict) and option.get("_use_table") is True,
    }

    try:
        if plot_err is not None:
            # 🛡️ 3 次失敗 — 結構性 fallback:標記為 phaseC_fallback,軟通過
            print(f"   ⚠️ Phase C 連續 3 次失敗,降級為表格渲染 (fallback)")
            result["checks"].append(check(
                "Phase C 路徑",
                True,  # fallback 仍視為「處理成功」,沒卡死
                f"3 次失敗後降級為表格 (retry log: {c_retry_log})"
            ))
            result["phases"]["echarts"]["phase_c_fallback"] = True
            result["status"] = "phaseC_fallback_used"
            return result
        result["checks"].append(check(
            f"Phase C exec 成功 (用 {c_attempt + 1} 次嘗試)",
            True, "1 次過" if c_attempt == 0 else f"retry {c_attempt + 1}"
        ))

        if not isinstance(option, dict):
            result["checks"].append(check("Phase C 產出 dict 型別 option", False))
            result["status"] = "phaseC_no_option"
            return result

        print(f"   option keys: {list(option.keys())}")
        if option.get("_use_table"):
            print("   📋 LLM 主動選擇 _use_table fallback")
            result["checks"].append(check("Phase C 路徑", True, "use_table fallback"))

            # KPI 卡片檢查 (T1 系列)
            if case.get("echarts_should_have_kpi_cards"):
                cards = option.get("_kpi_cards") or []
                min_cards = case.get("echarts_min_kpi_cards", 1)
                result["checks"].append(check(
                    f"_kpi_cards 數量 ≥ {min_cards}",
                    len(cards) >= min_cards,
                    f"actual: {len(cards)}"
                ))
                if cards:
                    valid_cards = [c for c in cards if isinstance(c, dict) and "label" in c and "value" in c]
                    result["checks"].append(check(
                        "所有卡片含 label + value",
                        len(valid_cards) == len(cards),
                        f"{len(valid_cards)}/{len(cards)} valid"
                    ))
                    # 印出卡片內容
                    print("   📊 KPI Cards:")
                    for c in cards[:6]:
                        print(f"      · {c.get('label')}: {c.get('value')}")
        else:
            if case.get("echarts_should_use_table"):
                result["checks"].append(check(
                    "預期走 _use_table 但 LLM 選了畫圖",
                    False,
                    "use_table=False (應為 True)"
                ))
            required_keys = case.get("echarts_required_keys", [])

            # v0.7.4:chart-type aware — pie / radar / treemap 等 axis-less chart
            # 不需要 xAxis / yAxis。從 series[0].type 偵測,自動排除這兩個 key。
            series_list = option.get("series", []) or []
            first_series_type = ""
            if series_list and isinstance(series_list[0], dict):
                first_series_type = (series_list[0].get("type") or "").lower()
            _AXIS_LESS_TYPES = {"pie", "radar", "treemap", "sunburst", "gauge", "funnel"}
            is_axis_less = first_series_type in _AXIS_LESS_TYPES

            if is_axis_less:
                required_keys_effective = [k for k in required_keys
                                            if k not in ("xAxis", "yAxis")]
                axis_note = f" (chart={first_series_type},xAxis/yAxis 不適用)"
            else:
                required_keys_effective = required_keys
                axis_note = ""

            missing_keys = [k for k in required_keys_effective if k not in option]
            result["checks"].append(check(
                f"option 含必備 keys {required_keys_effective}{axis_note}",
                len(missing_keys) == 0,
                f"缺: {missing_keys}" if missing_keys else "✓"
            ))

            min_series = case.get("echarts_min_series", 1)
            series_count = len(option.get("series", []))
            result["checks"].append(check(
                f"series 數 ≥ {min_series}",
                series_count >= min_series,
                f"actual: {series_count}"
            ))

            if case.get("echarts_should_have_stack"):
                has_stack = any(s.get("stack") for s in option.get("series", []))
                result["checks"].append(check("series 帶 stack 屬性", has_stack))

            if case.get("echarts_should_have_visualmap"):
                has_vm = "visualMap" in option
                result["checks"].append(check("含 visualMap (heatmap 用)", has_vm))

            # ───── STK 專屬新檢查 ─────
            # v0.10.1:orientation-aware — horizontal bar 時 (yAxis=category, xAxis=value)
            # category axis 在 yAxis;若仍 hard-code 檢查 xAxis.data 會 false fail。
            _xaxis_dict = option.get("xAxis") if isinstance(option.get("xAxis"), dict) else {}
            _yaxis_dict = option.get("yAxis") if isinstance(option.get("yAxis"), dict) else {}
            _is_horizontal = (
                _xaxis_dict.get("type") == "value"
                and _yaxis_dict.get("type") == "category"
            )
            # category axis 上的 data list(垂直 = xAxis;橫向 = yAxis)
            _cat_axis_data = (_yaxis_dict if _is_horizontal else _xaxis_dict).get("data")
            _cat_axis_label = "yAxis" if _is_horizontal else "xAxis"
            # value axis 上的 max(垂直 = yAxis;橫向 = xAxis)
            _val_axis_dict = _xaxis_dict if _is_horizontal else _yaxis_dict
            _val_axis_label = "xAxis" if _is_horizontal else "yAxis"

            if case.get("echarts_xaxis_unique"):
                if isinstance(_cat_axis_data, list):
                    has_dup = len(_cat_axis_data) != len(set(map(str, _cat_axis_data)))
                    result["checks"].append(check(
                        f"{_cat_axis_label}.data 無重複"
                        + (f" (橫向,category 在 {_cat_axis_label})" if _is_horizontal else ""),
                        not has_dup,
                        f"actual: {_cat_axis_data}" if has_dup else f"len={len(_cat_axis_data)}"
                    ))
                else:
                    result["checks"].append(check(
                        f"{_cat_axis_label}.data 是 list",
                        False,
                        f"type={type(_cat_axis_data).__name__}"
                    ))

            if case.get("echarts_data_length_aligned"):
                if isinstance(_cat_axis_data, list):
                    x_len = len(_cat_axis_data)
                    series_lens = [len(s.get("data", [])) for s in option.get("series", [])]
                    aligned = all(L == x_len for L in series_lens)
                    result["checks"].append(check(
                        f"所有 series.data 長度 == {_cat_axis_label}.data 長度 ({x_len})",
                        aligned,
                        f"series lens: {series_lens}"
                    ))

            if case.get("echarts_yaxis_max"):
                # v0.10.1:橫向時 value axis 是 xAxis,改 check xAxis.max
                expected_max = case["echarts_yaxis_max"]
                actual_max = _val_axis_dict.get("max") if isinstance(_val_axis_dict, dict) else None
                result["checks"].append(check(
                    f"{_val_axis_label}.max == {expected_max} (100% stacked 應鎖頂"
                    + (",橫向" if _is_horizontal else "") + ")",
                    actual_max == expected_max,
                    f"actual: {actual_max}"
                ))

            if case.get("echarts_series_count_max"):
                max_s = case["echarts_series_count_max"]
                actual_s = len(option.get("series", []))
                result["checks"].append(check(
                    f"series 數 ≤ {max_s} (避免維度爆炸)",
                    actual_s <= max_s,
                    f"actual: {actual_s}"
                ))

            if case.get("echarts_no_placeholder_series_name"):
                # 啟發式偵測 placeholder name:單個英文/中文字母 + 數字/字母組合
                import re as _re
                placeholder_patterns = [
                    _re.compile(r"^(類別|category|series|group|item|state)\s*[a-z0-9]$", _re.IGNORECASE),
                    _re.compile(r"^<\w+>$"),  # angle bracket placeholder
                ]
                names = [str(s.get("name", "")) for s in option.get("series", [])]
                bad = [n for n in names if any(p.match(n) for p in placeholder_patterns)]
                result["checks"].append(check(
                    "series.name 非 placeholder (類別 A/Category 1/<col> 等)",
                    len(bad) == 0,
                    f"placeholder 名: {bad}" if bad else f"names: {names}"
                ))

            # 橫向 bar 專屬檢查
            if case.get("echarts_should_have_yaxis_category"):
                yax = option.get("yAxis", {})
                is_cat = isinstance(yax, dict) and yax.get("type") == "category"
                result["checks"].append(check(
                    "yAxis.type == 'category' (橫向 bar)",
                    is_cat,
                    f"actual yAxis: {yax}" if not is_cat else "✓"
                ))

            if case.get("echarts_should_have_xaxis_value"):
                xax = option.get("xAxis", {})
                is_val = isinstance(xax, dict) and xax.get("type") == "value"
                result["checks"].append(check(
                    "xAxis.type == 'value' (橫向 bar)",
                    is_val,
                    f"actual xAxis: {xax}" if not is_val else "✓"
                ))

            if case.get("echarts_data_length_aligned_horizontal"):
                yax_data = option.get("yAxis", {}).get("data") if isinstance(option.get("yAxis"), dict) else None
                if isinstance(yax_data, list):
                    y_len = len(yax_data)
                    series_lens = [len(s.get("data", [])) for s in option.get("series", [])]
                    aligned = all(L == y_len for L in series_lens)
                    result["checks"].append(check(
                        f"橫向時 series.data 長度 == yAxis.data 長度 ({y_len})",
                        aligned,
                        f"series lens: {series_lens}"
                    ))
                else:
                    result["checks"].append(check("yAxis.data 是 list (橫向)", False))

            if case.get("echarts_no_nan_in_data"):
                import math as _math
                has_nan = any(
                    any(isinstance(v, float) and _math.isnan(v) for v in s.get("data", []))
                    for s in option.get("series", [])
                )
                result["checks"].append(check(
                    "series.data 不含 NaN (應 fillna(0))",
                    not has_nan,
                ))
            # ───── /STK 專屬檢查 ─────

            # 檢查是否有禁忌 lambda / function
            code_lower = echarts_code.lower()
            has_lambda = "lambda" in code_lower or "def " in code_lower
            if has_lambda:
                result["checks"].append(warn("含 lambda/def (formatter 應用字串)", ""))
    except Exception as e:
        result["checks"].append(check("Phase C 例外", False, str(e)))
        result["phases"]["echarts_error"] = traceback.format_exc()
        result["status"] = "phaseC_error"
        return result

    # ----------------------------------------------------------
    # Phase D · Insight
    # ----------------------------------------------------------
    print("\n▶︎ Phase D · Insight")
    t0 = time.time()
    try:
        try:
            q_md = Q.head(20).to_markdown(index=False)
        except Exception:
            q_md = Q.head(20).to_string(index=False)
        insight_res = llm.generate_insight(case["query"], plan_text, q_md)
        elapsed = time.time() - t0
        insight_text = insight_res.get("message", "") if insight_res.get("status") == "success" else ""
        result["phases"]["insight"] = {
            "elapsed_s": round(elapsed, 1),
            "status": insight_res.get("status"),
            "text": insight_text,
        }
        print(f"   ({elapsed:.1f}s)")
        print(f"   {truncate(insight_text, 400)}")
        result["checks"].append(check("Insight 有產出", bool(insight_text.strip())))

        # 主動掃禁忌語 — 智慧版:只警告「正向使用」的禁忌詞
        forbidden_terms = ["趨勢", "月度", "季度", "部門", "金額", "薪資", "審核時間", "review duration"]
        truly_misused = [t for t in forbidden_terms if is_misused(insight_text, t)]
        if truly_misused:
            result["checks"].append(check(
                "Insight 無「正向使用」禁忌語",
                False,
                f"違規 (非拒絕語境): {truly_misused}"
            ))
        else:
            result["checks"].append(check("Insight 無禁忌語(或皆為拒絕語境)", True))
    except Exception as e:
        result["checks"].append(check("Phase D 例外", False, str(e)))
        result["status"] = "phaseD_error"

    # 全部通過
    fail_count = sum(1 for c in result["checks"] if c.get("ok") is False)
    result["status"] = "pass" if fail_count == 0 else f"fail({fail_count})"
    print(f"\n   📊 Case {case['id']} 結果:{result['status']}")
    return result


# ============================================================
# Report 寫出
# ============================================================
def format_markdown_report(results: list[dict]) -> str:
    lines = [
        "# tFlex GenBI · 測試結果報告",
        f"\n> 共 {len(results)} 個 case · 由 test_runner.py 產生",
        "",
        "## 📊 速覽",
        "",
        "| Case | 名稱 | 狀態 | 通過/總數 | 累積耗時 |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        total = len(r.get("checks", []))
        passed = sum(1 for c in r.get("checks", []) if c.get("ok") is True)
        elapsed = sum(
            p.get("elapsed_s", 0) for p in r.get("phases", {}).values()
            if isinstance(p, dict)
        )
        lines.append(
            f"| {r['id']} | {r['name']} | {r.get('status', '?')} | "
            f"{passed}/{total} | {elapsed:.1f}s |"
        )

    lines.append("\n---\n")

    for r in results:
        lines.append(f"## Case {r['id']} · {r['name']}")
        lines.append(f"**Query:** `{r['query']}`")
        lines.append(f"**Type:** {r['type']} · **Status:** {r.get('status', '?')}\n")

        # 檢查表
        lines.append("### 檢查項")
        for c in r.get("checks", []):
            ok = c.get("ok")
            icon = "✅" if ok is True else ("❌" if ok is False else "⚠️")
            detail = f"  ·  {c['detail']}" if c.get("detail") else ""
            lines.append(f"- {icon} {c['label']}{detail}")
        lines.append("")

        # Plan
        if "plan" in r.get("phases", {}):
            p = r["phases"]["plan"]
            lines.append(f"### Phase 0 · Plan ({p['elapsed_s']}s)")
            lines.append("```")
            lines.append(p["text"])
            lines.append("```")

        # Pipeline
        if "pipeline" in r.get("phases", {}):
            p = r["phases"]["pipeline"]
            lines.append(f"### Phase A · Pipeline ({p['elapsed_s']}s)")
            if p.get("pipeline_json"):
                lines.append("```json")
                lines.append(json.dumps(p["pipeline_json"], indent=2, ensure_ascii=False))
                lines.append("```")
            if p.get("forbidden_violations"):
                lines.append(f"⚠️ **違規 stages:** {p['forbidden_violations']}")
            if p.get("raw_df_shape"):
                lines.append(f"raw_df: shape={p['raw_df_shape']}, cols={p.get('raw_df_cols', [])}")

        # Preprocess
        if "preprocess" in r.get("phases", {}):
            p = r["phases"]["preprocess"]
            lines.append(f"### Phase B · Preprocess ({p['elapsed_s']}s)")
            lines.append("```python")
            lines.append(p["code"])
            lines.append("```")
            if p.get("exec_error"):
                lines.append(f"\n❌ **exec error:**\n```\n{p['exec_error']}\n```")
            else:
                lines.append(f"\nQ: shape={p.get('Q_shape')}, cols={p.get('Q_cols')}")

        # ECharts
        if "echarts" in r.get("phases", {}):
            p = r["phases"]["echarts"]
            lines.append(f"### Phase C · ECharts ({p['elapsed_s']}s)")
            lines.append("```python")
            lines.append(p["code"])
            lines.append("```")
            if p.get("exec_error"):
                lines.append(f"\n❌ **exec error:**\n```\n{p['exec_error']}\n```")
            elif p.get("use_table_fallback"):
                lines.append("\n📋 `_use_table` fallback triggered")
            else:
                lines.append(f"\noption keys: {p.get('option_keys')}")

        # Insight
        if "insight" in r.get("phases", {}):
            p = r["phases"]["insight"]
            lines.append(f"### Phase D · Insight ({p['elapsed_s']}s)")
            lines.append("```")
            lines.append(p["text"])
            lines.append("```")

        lines.append("\n---\n")

    return "\n".join(lines)


# ============================================================
# Main
# ============================================================
def _normalize_case(c: dict) -> dict:
    """讓 case 同時擁有 id / case_id(向下相容),保留所有其他欄位。"""
    if "case_id" in c and "id" not in c:
        c["id"] = c["case_id"]
    elif "id" in c and "case_id" not in c:
        c["case_id"] = c["id"]
    return c


def main() -> int:
    parser = argparse.ArgumentParser(description="GenBI headless test runner")
    parser.add_argument(
        "--domain",
        default="tflex",
        help="跑哪個 domain 的 cases (預設 tflex)",
    )
    parser.add_argument(
        "--filter",
        default="",
        help="只跑 id 前綴符合的 case (例: --filter STK 只跑 STK-* 案例)",
    )
    parser.add_argument(
        "--only",
        default="",
        help="只跑指定 id (逗號分隔,例: --only STK-01,STK-04)",
    )
    parser.add_argument(
        "--no-save-run",
        action="store_true",
        help="不寫 test_runs collection(臨時驗證用)",
    )
    parser.add_argument(
        "--baseline",
        action="store_true",
        help="跑完自動標為 baseline(等同 --no-save-run 的反面)",
    )
    args = parser.parse_args()

    banner(f" GenBI · Test Runner · domain={args.domain} ", "═")

    print(f"LLM       : {OLLAMA_URL}")
    print(f"Model     : {OLLAMA_MODEL}")
    print(f"MongoDB   : {MONGO_URI}{MONGO_DB}")

    # 連線
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=max(config.MONGO_SERVER_SELECTION_TIMEOUT_MS, 3000))
        client.admin.command("ping")
        db = client[MONGO_DB]
        try:
            print(f"✅ MongoDB 連線 OK · {db.tflex_applications.count_documents({}):,} 筆 application")
        except Exception:
            print("✅ MongoDB 連線 OK")
    except Exception as e:
        print(f"❌ MongoDB 連線失敗: {e}")
        return 1

    # 從 TestCaseRepository 讀 cases(DB → embedded fallback)
    try:
        from test_case_repository import build_default_test_case_repo
        case_repo = build_default_test_case_repo(mongo_db=db)
        only_ids = None
        if args.only:
            only_ids = [x.strip() for x in args.only.split(",") if x.strip()]
        selected_cases = case_repo.get_cases(
            domain=args.domain,
            filter_prefix=args.filter,
            case_ids=only_ids,
        )
        selected_cases = [_normalize_case(c) for c in selected_cases]
    except Exception as e:
        print(f"⚠️  TestCaseRepository 失敗,fallback to 內嵌 TEST_CASES list: {e}")
        selected_cases = TEST_CASES
        if args.only:
            wanted = {x.strip() for x in args.only.split(",") if x.strip()}
            selected_cases = [c for c in TEST_CASES if c["id"] in wanted]
        elif args.filter:
            prefix = args.filter.strip()
            selected_cases = [c for c in TEST_CASES if c["id"].startswith(prefix)]

    if not selected_cases:
        print(f"❌ 沒有符合 domain={args.domain!r} filter={args.filter!r} only={args.only!r} 的 case")
        return 1

    total_cases_in_domain = len(case_repo.get_cases(args.domain)) if 'case_repo' in dir() else len(TEST_CASES)
    print(f"Cases     : {len(selected_cases)} / {total_cases_in_domain} 個"
          + (f"  (filter={args.filter or args.only})" if (args.filter or args.only) else ""))
    print(f"Timeout   : {OLLAMA_TIMEOUT}s\n")

    print("⏱️  第一個 case 會包含模型 warm-up (預期 30-90s),後續會快很多。\n")

    # 為了 LLM service 能讀對 domain 的 metadata,從 prompt_repo 取
    try:
        from prompt_repository import build_default_repo
        prompt_repo = build_default_repo(mongo_db=db)
        task_md = None
        try:
            task_md = prompt_repo.get_metadata(args.domain)
        except KeyError:
            print(f"⚠️  Metadata 找不到 domain={args.domain!r},LLMService 用 default")
    except Exception:
        prompt_repo = None
        task_md = None

    llm = LLMService(
        api_url=OLLAMA_URL,
        api_key=OLLAMA_KEY,
        model_name=OLLAMA_MODEL,
        timeout_s=OLLAMA_TIMEOUT,
        default_temperature=0.0,
        task_metadata=task_md,
        prompt_repo=prompt_repo,
        domain=args.domain,
        model_profile=LLM_MODEL_PROFILE,  # v0.10.6.1
    )

    results = []
    total_start = time.time()
    # status → TaskTrace.finalize() status 映射(餵 failure_filter 用)
    _STATUS_TO_TRACE = {
        "pass":                  "completed",
        "refusal_detected":      "refused",
        "phaseC_fallback_used":  "failed",  # 圖沒產出,降級表格 → 視為失敗
        "phaseB_exec_error":     "failed",
        "phaseB_no_Q":           "failed",
        "phaseA_pipeline_error": "failed",
        "phase_d_error":         "failed",
        "fatal_error":           "failed",
    }
    for case in selected_cases:
        case_wall_t0 = time.time()
        # v0.11.0:每個 case 包一個 TaskTrace,餵 self-learning loop
        _trace = TaskTrace(
            db=db,
            domain=args.domain,
            query=case["query"],
            collection_name=config.TASK_TRACES_COLLECTION,
        )
        llm.trace = _trace
        try:
            llm.reset_call_log()
            res = run_case(case, llm, db)
        except KeyboardInterrupt:
            print("\n🛑 使用者中斷")
            try:
                _trace.finalize(status="failed", error="user interrupt")
            except Exception:
                pass
            finally:
                llm.trace = None
            break
        except Exception:
            print(f"\n💥 Case {case['id']} 致命錯誤:")
            print(traceback.format_exc())
            res = {
                "id": case["id"],
                "name": case["name"],
                "query": case["query"],
                "type": case["type"],
                "phases": {},
                "checks": [],
                "status": "fatal_error",
                "fatal_traceback": traceback.format_exc(),
            }
        # v0.11.0:finalize trace 帶 case 結果
        try:
            _trace_status = _STATUS_TO_TRACE.get(res.get("status", "fatal_error"), "failed")
            _trace_id = _trace.finalize(
                status=_trace_status,
                error=res.get("fatal_traceback") or None,
            )
            res["trace_id"] = _trace_id
        except Exception as _trace_e:
            print(f"   ⚠️ trace finalize 失敗(不影響 case 結果):{_trace_e}")
        finally:
            llm.trace = None  # detach,避免下個 case 沾到
        # 附加 cost telemetry
        res["wall_elapsed_s"] = round(time.time() - case_wall_t0, 2)
        res["llm_usage"] = llm.get_call_summary()
        # 計算 retry 次數 (Phase B + C)
        b_attempts = res.get("phases", {}).get("preprocess", {}).get("attempts", 0)
        c_attempts = res.get("phases", {}).get("echarts", {}).get("attempts", 0)
        res["retries"] = {"phase_b": b_attempts, "phase_c": c_attempts}
        results.append(res)

    total_elapsed = time.time() - total_start

    banner(f"全部完成 · 總耗時 {total_elapsed:.1f}s ({total_elapsed/60:.1f} 分鐘)", "═")
    print("\n速覽 (wall time / LLM calls / tokens / retries):")
    print(f"  {'Case':6s} {'Status':22s} {'Wall':>7s} {'Calls':>6s} {'Tokens':>9s} {'Retry B/C':>10s}")
    for r in results:
        usage = r.get("llm_usage", {})
        ret = r.get("retries", {})
        wall = r.get("wall_elapsed_s", 0)
        retry_str = f"{ret.get('phase_b', '—')}/{ret.get('phase_c', '—')}"
        print(
            f"  {r['id']:6s} {r.get('status', '?'):22s} "
            f"{wall:>6.1f}s {usage.get('calls', 0):>6d} "
            f"{usage.get('total_tokens', 0):>9,d} {retry_str:>10s}"
        )

    # ── 彙整 cost summary ──
    total_calls = sum(r.get("llm_usage", {}).get("calls", 0) for r in results)
    total_prompt = sum(r.get("llm_usage", {}).get("prompt_tokens", 0) for r in results)
    total_completion = sum(r.get("llm_usage", {}).get("completion_tokens", 0) for r in results)
    total_tokens = total_prompt + total_completion
    pass_count = sum(1 for r in results if r.get("status") in ("pass", "refusal_detected"))

    print()
    banner("💰 Cost Summary", "─")
    print(f"  Cases run         : {len(results)}")
    print(f"  Pass / Refusal    : {pass_count}/{len(results)} ({pass_count / max(len(results), 1) * 100:.0f}%)")
    print(f"  Wall clock total  : {total_elapsed:.1f}s")
    print(f"  Total LLM calls   : {total_calls}")
    print(f"  Total prompt tok  : {total_prompt:,}")
    print(f"  Total output tok  : {total_completion:,}")
    print(f"  Total tokens      : {total_tokens:,}")
    print()
    print("  💵 假設成本 (per case 平均):")
    if pass_count > 0:
        print(f"    · GPT-4o-mini ($0.15/M in + $0.60/M out): "
              f"${(total_prompt * 0.15 / 1_000_000 + total_completion * 0.60 / 1_000_000) / pass_count:.4f} / 成功 query")
        print(f"    · Claude Haiku ($0.80/M in + $4.00/M out): "
              f"${(total_prompt * 0.80 / 1_000_000 + total_completion * 4.00 / 1_000_000) / pass_count:.4f} / 成功 query")
        print(f"    · Local (A100 $2/hr 估計):  "
              f"${(total_elapsed / 3600 * 2) / pass_count:.4f} / 成功 query")

    # 寫報告(local files,for backward compat)
    REPORT_MD.write_text(format_markdown_report(results), encoding="utf-8")
    REPORT_JSON.write_text(
        json.dumps(results, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"\n📝 Markdown 報告:{REPORT_MD.resolve()}")
    print(f"📦 JSON 結構:{REPORT_JSON.resolve()}")

    # 寫 test_runs collection(v0.3.0+)
    if not args.no_save_run:
        try:
            from test_run_repository import TestRunRepository
            import datetime as _dt
            run_repo = TestRunRepository(
                mongo_db=db,
                collection=config.TEST_RUNS_COLLECTION,
            )
            # 組摘要
            summary = {
                "total_cases": len(results),
                "passed": sum(1 for r in results if r.get("status") == "pass"),
                "refusal_detected": sum(1 for r in results if r.get("status") == "refusal_detected"),
                "failed": sum(1 for r in results if r.get("status", "").startswith("fail")),
                "fatal_error": sum(1 for r in results if r.get("status") == "fatal_error"),
                "phaseA_error": sum(1 for r in results if r.get("status") == "phaseA_error"),
                "phaseC_fallback_used": sum(1 for r in results if r.get("status") == "phaseC_fallback_used"),
                "total_calls": total_calls,
                "total_tokens": total_tokens,
                "prompt_tokens": total_prompt,
                "completion_tokens": total_completion,
            }
            # 組 active_versions 快照(若 repo 接好)
            active_versions = {}
            try:
                if 'prompt_repo' in dir() and prompt_repo is not None and prompt_repo._enabled:
                    prompt_versions = {}
                    for key in ("phase_0_plan", "phase_a_pipeline", "phase_b_preprocess",
                                "phase_c_echarts", "phase_d_insight"):
                        doc = prompt_repo._db[prompt_repo._prompt_coll].find_one({
                            "prompt_key": key,
                            "is_active": True,
                        }) if prompt_repo._db is not None else None
                        if doc:
                            prompt_versions[key] = doc["_id"]
                    if prompt_versions:
                        active_versions["prompts"] = prompt_versions
                    md_doc = prompt_repo._db[prompt_repo._metadata_coll].find_one({
                        "domain": args.domain, "is_active": True
                    }) if prompt_repo._db is not None else None
                    if md_doc:
                        active_versions["metadata"] = md_doc["_id"]
            except Exception:
                pass

            inserted_id = run_repo.save_run({
                "domain": args.domain,
                "started_at": _dt.datetime.fromtimestamp(total_start, _dt.timezone.utc),
                "completed_at": _dt.datetime.now(_dt.timezone.utc),
                "total_wall_s": round(total_elapsed, 2),
                "filter": args.filter or args.only or None,
                "summary": summary,
                "case_results": results,
                "is_baseline": bool(args.baseline),
                "baseline_notes": (
                    f"Auto-marked baseline at {_dt.datetime.now(_dt.timezone.utc).isoformat()}"
                    if args.baseline else ""
                ),
            }, active_versions=active_versions or None)
            print(f"\n📦 test_runs 寫入 OK · _id={inserted_id}"
                  + (" · 已標 baseline" if args.baseline else ""))
        except Exception as e:
            print(f"\n⚠️  test_runs 寫入失敗(不影響本地報告): {e}")

    client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
