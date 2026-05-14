"""
v0.4.0 smoke test:用人造 Q + option 跑 build_report_pptx,
驗證 bar / stacked-bar / line(雙軸) / pie / heatmap / table / kpi 7 種場景
都能輸出合法 .pptx,並寫到 outputs/smoke_*.pptx。

執行:
    python scripts/smoke_export_pptx.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd

# 確保 import path 含專案根目錄
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
sys.path.insert(0, str(_ROOT))

from export_pptx import build_report_pptx  # noqa: E402

OUT_DIR = _ROOT / "outputs" / "smoke_pptx"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def _write(name: str, data: bytes) -> Path:
    path = OUT_DIR / f"{name}.pptx"
    path.write_bytes(data)
    print(f"  ✓ {path.relative_to(_ROOT)}  ({len(data):,} bytes)")
    return path


INSIGHT_SAMPLE = """## 📌 重點摘要

本季關鍵指標皆呈現 **正向趨勢**,其中以 R&D 部門增長最為顯著(+18%)。

## 📊 細部觀察

- **R&D 部門**:申請件數較上季 +18%,主要由半導體製程驗證貢獻。
- **製造部門**:申請件數持平,反映產線穩定運作。
- **行政部門**:輕微下滑 -3%,屬季節性波動。

## 💡 建議

1. 持續追蹤 R&D 申請的後續完工率
2. 製造部門可以加強跨廠合作以再衝量
3. 行政部門無須額外行動
"""


def case_single_bar():
    print("[1/7] single bar")
    Q = pd.DataFrame({
        "department": ["R&D", "Manufacturing", "Sales", "HR", "Admin"],
        "count": [120, 95, 68, 42, 30],
    })
    option = {
        "title": {"text": "各部門申請件數"},
        "xAxis": {"type": "category", "data": Q["department"].tolist(),
                  "name": "部門"},
        "yAxis": {"type": "value", "name": "件數"},
        "series": [
            {"type": "bar", "data": Q["count"].tolist(), "name": "申請件數"},
        ],
    }
    data = build_report_pptx(
        query="各部門申請件數",
        Q=Q, final_option=option,
        insight_text=INSIGHT_SAMPLE,
        chart_engine="ECharts",
        source_label="MongoDB (tflex.applications)",
        domain="tflex",
    )
    _write("01_single_bar", data)


def case_stacked_bar():
    print("[2/7] stacked bar")
    Q = pd.DataFrame({
        "month": ["Jan", "Feb", "Mar", "Apr", "May"],
        "R&D": [40, 45, 50, 55, 60],
        "Manufacturing": [30, 32, 35, 33, 30],
        "Sales": [20, 22, 25, 28, 30],
    })
    option = {
        "title": {"text": "每月各部門申請件數(堆疊)"},
        "xAxis": {"type": "category", "data": Q["month"].tolist(),
                  "name": "月份"},
        "yAxis": {"type": "value", "name": "件數"},
        "series": [
            {"type": "bar", "data": Q["R&D"].tolist(),
             "name": "R&D", "stack": "total"},
            {"type": "bar", "data": Q["Manufacturing"].tolist(),
             "name": "Manufacturing", "stack": "total"},
            {"type": "bar", "data": Q["Sales"].tolist(),
             "name": "Sales", "stack": "total"},
        ],
    }
    data = build_report_pptx(
        query="每月各部門申請件數(堆疊)",
        Q=Q, final_option=option,
        insight_text=INSIGHT_SAMPLE,
        chart_engine="ECharts",
        source_label="MongoDB",
        domain="tflex",
    )
    _write("02_stacked_bar", data)


def case_dual_axis_line():
    print("[3/7] line w/ dual-axis")
    Q = pd.DataFrame({
        "month": ["Jan", "Feb", "Mar", "Apr", "May", "Jun"],
        "applications": [120, 135, 150, 180, 220, 240],
        "approval_rate": [0.62, 0.65, 0.71, 0.74, 0.78, 0.81],
    })
    option = {
        "title": {"text": "申請件數 vs 通過率"},
        "xAxis": {"type": "category", "data": Q["month"].tolist(),
                  "name": "月份"},
        "yAxis": [
            {"type": "value", "name": "件數"},
            {"type": "value", "name": "通過率"},
        ],
        "series": [
            {"type": "line", "data": Q["applications"].tolist(),
             "name": "申請件數", "yAxisIndex": 0},
            {"type": "line", "data": Q["approval_rate"].tolist(),
             "name": "通過率", "yAxisIndex": 1},
        ],
    }
    data = build_report_pptx(
        query="申請件數 vs 通過率",
        Q=Q, final_option=option,
        insight_text=INSIGHT_SAMPLE,
        chart_engine="ECharts",
        source_label="MongoDB",
        domain="tflex",
    )
    _write("03_dual_axis_line", data)


def case_pie():
    print("[4/7] pie")
    Q = pd.DataFrame({
        "level": ["P1", "P2", "P3", "P4", "P5"],
        "count": [45, 78, 120, 92, 35],
    })
    option = {
        "title": {"text": "申請人職等分布"},
        "series": [{
            "type": "pie",
            "name": "職等分布",
            "data": [{"name": r["level"], "value": int(r["count"])}
                     for _, r in Q.iterrows()],
        }],
    }
    data = build_report_pptx(
        query="申請人職等分布",
        Q=Q, final_option=option,
        insight_text=INSIGHT_SAMPLE,
        chart_engine="ECharts",
        source_label="MongoDB",
        domain="tflex",
    )
    _write("04_pie", data)


def case_heatmap():
    print("[5/7] heatmap")
    x_labels = ["Mon", "Tue", "Wed", "Thu", "Fri"]
    y_labels = ["09:00", "12:00", "15:00", "18:00"]
    rows = []
    data_cells = []
    import random
    random.seed(7)
    for yi, hh in enumerate(y_labels):
        for xi, dow in enumerate(x_labels):
            v = random.randint(2, 25)
            data_cells.append([xi, yi, v])
            rows.append({"day": dow, "time": hh, "count": v})
    Q = pd.DataFrame(rows)
    option = {
        "title": {"text": "申請熱度(週X 時段)"},
        "xAxis": {"type": "category", "data": x_labels, "name": "週幾"},
        "yAxis": {"type": "category", "data": y_labels, "name": "時段"},
        "series": [{"type": "heatmap", "data": data_cells, "name": "申請數"}],
    }
    data = build_report_pptx(
        query="申請熱度(週X 時段)",
        Q=Q, final_option=option,
        insight_text=INSIGHT_SAMPLE,
        chart_engine="ECharts",
        source_label="MongoDB",
        domain="tflex",
    )
    _write("05_heatmap", data)


def case_table_fallback():
    print("[6/7] table fallback")
    Q = pd.DataFrame({
        "employee_id": [f"E{i:04d}" for i in range(1, 11)],
        "department": ["R&D", "MFG", "Sales", "HR", "Admin",
                       "R&D", "MFG", "Sales", "HR", "Admin"],
        "applications": [4, 2, 1, 0, 3, 6, 1, 2, 1, 1],
        "approval_rate": [0.75, 0.50, 1.00, 0.00, 0.66,
                          0.83, 1.00, 0.50, 1.00, 1.00],
    })
    option = {"_use_table": True}
    data = build_report_pptx(
        query="員工申請彙整",
        Q=Q, final_option=option,
        insight_text=INSIGHT_SAMPLE,
        chart_engine="ECharts",
        source_label="MongoDB",
        domain="tflex",
        use_table_fallback=True,
    )
    _write("06_table_fallback", data)


def case_kpi_cards():
    print("[7/7] KPI cards")
    Q = pd.DataFrame({
        "label": ["總申請", "通過率", "退件率", "中位審核天數"],
        "value": [1234, 0.72, 0.18, 3.5],
    })
    option = {
        "_use_table": True,
        "_kpi_cards": [
            {"label": "總申請件數", "value": "1,234", "delta": "▲ 18% MoM"},
            {"label": "通過率", "value": "72%", "delta": "▲ 4 pp"},
            {"label": "退件率", "value": "18%", "delta": "▼ 2 pp"},
            {"label": "中位審核天數", "value": "3.5 days", "delta": "持平"},
        ],
    }
    data = build_report_pptx(
        query="KPI 儀表板",
        Q=Q, final_option=option,
        insight_text=INSIGHT_SAMPLE,
        chart_engine="ECharts",
        source_label="MongoDB",
        domain="tflex",
        use_table_fallback=True,
    )
    _write("07_kpi_cards", data)


def main():
    print(f"\n[smoke] outputs → {OUT_DIR.relative_to(_ROOT)}\n")
    case_single_bar()
    case_stacked_bar()
    case_dual_axis_line()
    case_pie()
    case_heatmap()
    case_table_fallback()
    case_kpi_cards()
    print(f"\n✅ 全部 7 個 case 完成,檔案在 {OUT_DIR}")


if __name__ == "__main__":
    main()
