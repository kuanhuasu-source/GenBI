import json
import re
import time
from openai import OpenAI


# ============================================================
# 1. 從任意 task_metadata 動態組裝 LLM 用的 Domain Knowledge
#    這裡只把對 LLM 真正關鍵的部分濃縮出來,避免 prompt 過長。
#    所有函式都接受 metadata 參數,因此可以輕鬆替換成其他 domain。
# ============================================================

def _build_schema_block(metadata: dict) -> str:
    """從 metadata.collections 組出 schema 描述字串。"""
    lines = []
    for coll_name, coll_meta in metadata.get("collections", {}).items():
        lines.append(f"【{coll_name}】({coll_meta.get('description', '')})")
        lines.append(f"  grain: {coll_meta.get('grain', '')}")
        lines.append(f"  primary_key: {coll_meta.get('primary_key', '')}")
        for fname, fmeta in coll_meta.get("fields", {}).items():
            allowed = fmeta.get("allowed_values")
            if isinstance(allowed, dict):
                allowed_str = ", ".join(f"{k}={v}" for k, v in allowed.items())
            elif isinstance(allowed, list):
                allowed_str = ", ".join(allowed)
            else:
                allowed_str = ""
            extra = f" | allowed: {allowed_str}" if allowed_str else ""
            lines.append(
                f"    - {fname} ({fmeta.get('type', '')}): {fmeta.get('description', '')}{extra}"
            )
    return "\n".join(lines)


def _build_kpi_block(metadata: dict) -> str:
    lines = []
    for kpi_key, kpi_meta in metadata.get("kpi_definitions", {}).items():
        line = f"  - {kpi_meta['name']} ({kpi_key}): {kpi_meta['formula']}"
        if kpi_meta.get("important_note"):
            line += f"  ⚠️ {kpi_meta['important_note']}"
        lines.append(line)
    return "\n".join(lines)


def _build_limitation_block(metadata: dict) -> str:
    lim = metadata.get("data_limitations", {})
    missing = lim.get("missing_dimensions", [])
    not_supported = lim.get("not_supported_analysis", [])
    return (
        "缺少欄位 (NOT AVAILABLE): " + "; ".join(missing) + "\n"
        "不支援分析類型 (MUST REFUSE): " + "; ".join(not_supported)
    )


def _build_relationship_block(metadata: dict) -> str:
    lines = []
    for rel in metadata.get("relationships", []):
        lines.append(
            f"  {rel['from_collection']}.{rel['from_field']} "
            f"-[{rel['type']}]-> {rel['to_collection']}.{rel['to_field']}"
        )
    return "\n".join(lines)


def build_domain_knowledge(metadata: dict) -> str:
    """組裝完整的 Domain Knowledge text block,完全由 metadata 驅動。"""
    db_name = metadata.get("recommended_mongodb", {}).get("database", "unknown")
    dataset_name = metadata.get("dataset_name") or metadata.get("dataset_id") or "Dataset"
    return f"""### {dataset_name} (database: {db_name})

# Collections & 欄位定義
{_build_schema_block(metadata)}

# 關聯關係 (跨表 join 用)
{_build_relationship_block(metadata)}

# KPI 公式 (鐵律 — 必須嚴格依此計算,禁止自創邏輯)
{_build_kpi_block(metadata)}

# 資料限制 (CRITICAL — 違反必須回覆「資料不足」)
{_build_limitation_block(metadata)}
"""


def build_echarts_few_shot(metadata: dict) -> str:
    """
    從 metadata.charting_guidance.recommended_charts 自動合成 ECharts 範例。
    使用當前 domain 真實欄位名,確保 LLM 不會把舊 domain 的欄位帶過來。
    """
    charts_meta = (metadata.get("charting_guidance") or {}).get("recommended_charts", {})
    if not charts_meta:
        return "(metadata 未提供 recommended_charts;LLM 須依 schema 自行設計)"

    examples = []
    seen_types: set[str] = set()
    color_palette = (
        '"color": ["#5470c6", "#91cc75", "#fac858", "#ee6666", "#73c0de", '
        '"#3ba272", "#fc8452", "#9a60b4", "#ea7ccc", "#5b9bd5", '
        '"#a5a5a5", "#ffc000", "#7b78de", "#27a39d", "#e15759", '
        '"#f28e2c", "#76b7b2", "#59a14f", "#edc949", "#b07aa1"],'
    )

    for chart_name, chart_def in charts_meta.items():
        ct = chart_def.get("chart_type", "bar")
        if ct in seen_types:
            continue
        seen_types.add(ct)
        x_col = chart_def.get("x", "")
        y_def = chart_def.get("y", "")

        if ct == "bar" and isinstance(y_def, str):
            example = f"""### 範例 — {chart_name} (bar, x={x_col}, y={y_def})
```python
option = {{
    "title": {{"text": "<圖表標題>", "left": "center"}},
    "tooltip": {{"trigger": "axis"}},
    {color_palette}
    "xAxis": {{"type": "category", "data": Q["{x_col}"].tolist()}},
    "yAxis": {{"type": "value"}},
    "series": [
        {{"name": "{y_def}", "type": "bar", "data": Q["{y_def}"].tolist()}}
    ],
    "grid": {{"left": 60, "right": 30, "top": 60, "bottom": 40}}
}}
```"""
            examples.append(example)
        elif ct == "stacked_bar" and isinstance(y_def, list) and len(y_def) >= 2:
            s_lines = ",\n        ".join(
                f'{{"name": "{y}", "type": "bar", "stack": "total", "data": Q["{y}"].tolist()}}'
                for y in y_def
            )
            example = f"""### 範例 — {chart_name} (stacked_bar, x={x_col}, y={y_def})
```python
option = {{
    "title": {{"text": "<圖表標題>", "left": "center"}},
    "tooltip": {{"trigger": "axis"}},
    "legend": {{"data": {y_def}, "top": 30}},
    {color_palette}
    "xAxis": {{"type": "category", "data": Q["{x_col}"].tolist()}},
    "yAxis": {{"type": "value"}},
    "series": [
        {s_lines}
    ],
    "grid": {{"left": 60, "right": 30, "top": 60, "bottom": 40}}
}}
```"""
            examples.append(example)
        elif ct == "heatmap":
            example = f"""### 範例 — {chart_name} (heatmap)
```python
# Q 為 long-format,含 (row_dim, col_dim, value) 三欄
option = {{
    "title": {{"text": "<圖表標題>", "left": "center"}},
    "tooltip": {{"trigger": "item"}},
    "xAxis": {{"type": "category", "data": Q["<col_dim>"].unique().tolist()}},
    "yAxis": {{"type": "category", "data": Q["<row_dim>"].unique().tolist()}},
    "visualMap": {{"min": int(Q["<value>"].min()), "max": int(Q["<value>"].max()),
                   "calculable": True, "orient": "horizontal", "left": "center", "bottom": 0}},
    "series": [{{
        "name": "<value 名稱>", "type": "heatmap",
        "data": Q[["<col_dim>", "<row_dim>", "<value>"]].values.tolist(),
        "label": {{"show": True}}
    }}],
    "grid": {{"left": 80, "right": 30, "top": 60, "bottom": 80}}
}}
```"""
            examples.append(example)
        if len(examples) >= 3:
            break

    # 永遠附帶一個 table fallback 範例
    examples.append("""### 範例 — Executive Summary (use_table + KPI cards)
適用「dashboard」「執行摘要」「完整一覽」等查詢。LLM 自由選擇 3-4 個最具代表性的 KPI。
```python
option = {
    "_use_table": True,
    "_kpi_cards": [
        # 用 f-string 在 Q 上即時運算,標題 8 字內
        {"label": "<KPI 中文名>", "value": f"{Q['<col>'].sum():,}"},
        {"label": "<比率 KPI>",  "value": f"{Q['<rate_col>'].mean()*100:.2f}%"},
    ],
    "_table_caption": f"資料筆數:{len(Q)}"
}
```""")

    return "\n\n".join(examples)


_DASHBOARD_KEYWORDS = (
    "dashboard", "執行摘要", "overview", "匯總", "kpi overview",
    "summary", "管理面板", "一覽", "卡片", "總覽",
)


_INTENT_PATTERNS = {
    "greeting": [
        # 只認很短的純打招呼
        re.compile(r"^\s*(hi|hello|hey|嗨+|你好|哈囉|哈嘍|早安|午安|晚安)[\s,.!?。!?]*$",
                   re.IGNORECASE),
    ],
    "intro": [
        re.compile(r"(你會做什麼|你能做什麼|你的功能|你會什麼|你能幫.*?做|"
                   r"介紹一?下你|你是.*?系統|介紹這個系統|介紹.*?產品)", re.IGNORECASE),
        re.compile(r"(what can you do|what.*?capabilities|what are you|tell me about you|"
                   r"introduce yourself)", re.IGNORECASE),
    ],
    "data_overview": [
        re.compile(r"(你有什麼資料|有什麼資料可|什麼資料可分析|資料概覽|資料字典|"
                   r"資料.*?簡介|schema|有什麼欄位|有什麼表|有什麼指標|有什麼 ?kpi)",
                   re.IGNORECASE),
        re.compile(r"(what data|what.*?available|what.*?fields|what.*?tables|"
                   r"what.*?kpis|show.*?schema)", re.IGNORECASE),
    ],
    "guidance": [
        re.compile(r"(怎麼開始|怎麼用|如何使用|如何開始|有範例|有什麼範例|舉例|"
                   r"範例問題|sample|example|可以怎麼問|可以問什麼)", re.IGNORECASE),
        re.compile(r"(how (to|do).*?(start|use|begin)|getting started|"
                   r"give me.*?example|show.*?example)", re.IGNORECASE),
    ],
    "data_check": [
        # 「你有 X 嗎 / 是否有 X / 有沒有 X」之類,subject 後面接「資料/欄位/嗎/?」
        re.compile(r"(你有沒有|你有|是否有|是不是有|有沒有)\s*(.+?)\s*(?:資料|欄位|這個|嗎|呢)?[\s?。?]*$",
                   re.IGNORECASE),
        re.compile(r"(do you have|is there|are there)\s+(.+?)[\s?.]*$", re.IGNORECASE),
    ],
}


_GENERIC_BI_TERMS = frozenset({
    # 通用分析詞 (中)
    "比較", "對比", "排名", "排序", "分佈", "分布", "占比", "佔比",
    "組成", "結構", "最多", "最少", "最高", "最低", "前", "top",
    "顯示", "列出", "看", "畫", "分析", "看看", "分析一下",
    "公司", "類別", "種類", "數量", "比例", "比率",
    "kpi", "dashboard", "圖表", "報表", "趨勢",
    # 視覺化術語 (避免「改成 stacked bar」這類短 query 被誤判)
    "圖", "柱狀", "長條", "圓餅", "折線", "散點", "熱力", "熱圖",
    "堆疊", "stacked", "bar", "line", "pie", "scatter", "heatmap",
    "histogram", "area", "donut", "treemap",
    # 英文分析動詞
    "compare", "rank", "show", "plot", "chart", "analyze",
    "list", "give me", "what is", "how many", "how much",
})


def _tokenize_for_vocab(text: str) -> set:
    """產生 bilingual token set:英文單字 + 中文 bi-gram。"""
    tokens: set = set()
    if not text:
        return tokens
    text = text.lower()
    # 英文單字 (≥3 字)
    for word in re.findall(r"[a-z][a-z_]{2,}", text):
        tokens.add(word)
    # 中文 bi-gram
    for run in re.findall(r"[一-鿿]+", text):
        for i in range(len(run) - 1):
            tokens.add(run[i:i + 2])
    return tokens


def build_metadata_vocab(metadata: dict) -> set:
    """
    從 metadata 抽出詞彙集,用於 out_of_scope 偵測。
    回傳 set 內容:英文單字 + 中文 bi-gram + 完整欄位/KPI名。
    """
    vocab: set = set()

    # Collections
    for coll_name, coll in metadata.get("collections", {}).items():
        vocab.add(coll_name.lower())
        # Fields
        for field_name, field_meta in coll.get("fields", {}).items():
            vocab.add(field_name.lower())
            vocab |= _tokenize_for_vocab(field_meta.get("description", ""))
            # allowed_values 也算 vocab (例:公司代碼 TST 之類)
            av = field_meta.get("allowed_values")
            if isinstance(av, dict):
                for k, v in av.items():
                    vocab.add(str(k).lower())
                    vocab |= _tokenize_for_vocab(str(v))
            elif isinstance(av, list):
                for v in av:
                    vocab.add(str(v).lower())
                    vocab |= _tokenize_for_vocab(str(v))

    # KPI definitions
    for kpi_key, kpi in metadata.get("kpi_definitions", {}).items():
        vocab.add(kpi_key.lower())
        vocab |= _tokenize_for_vocab(kpi.get("name", ""))
        vocab |= _tokenize_for_vocab(kpi.get("formula", ""))

    # Business description
    biz = metadata.get("business_context", {})
    vocab |= _tokenize_for_vocab(biz.get("business_description", ""))
    vocab |= _tokenize_for_vocab(biz.get("domain", ""))
    for q in biz.get("main_business_questions", []) or []:
        vocab |= _tokenize_for_vocab(q)

    return vocab


def is_out_of_scope(query: str, metadata_vocab: set) -> bool:
    """
    判斷 query 是否與 metadata 完全無關。
    純啟發式,保守判斷 — 漏判時 Phase 0 refusal 會接住,雙層防禦。
    """
    if not query or len(query.strip()) < 3:
        return False
    full_vocab = (metadata_vocab or set()) | _GENERIC_BI_TERMS
    if not full_vocab:
        return False  # 沒 vocab 不下判斷
    q_lower = query.lower()
    # 任一 vocab 詞作為 substring 出現於 query → 不算 out_of_scope
    for v in full_vocab:
        if len(v) >= 2 and v in q_lower:
            return False
    return True


def classify_intent(query: str) -> dict:
    """
    Pre-Phase 0 · 把使用者查詢分類到 6 種 intent 之一。
    純 heuristic 推理,零 LLM call,毫秒級延遲。

    返回:
        {"intent": "intro"|"data_overview"|"data_check"|"guidance"|"greeting"|"analysis",
         "subject": "<for data_check, what they ask about>"}

    設計原則:**只在明確匹配時才分為 meta 類型,否則一律 analysis**(優先 precision over recall)。
    """
    if not query or not query.strip():
        return {"intent": "analysis", "subject": ""}

    q = query.strip()

    # 1. greeting (最嚴格 — 只認短訊息)
    for pat in _INTENT_PATTERNS["greeting"]:
        if pat.match(q):
            return {"intent": "greeting", "subject": ""}

    # 2. intro / data_overview / guidance (短語匹配)
    for intent in ("intro", "data_overview", "guidance"):
        for pat in _INTENT_PATTERNS[intent]:
            if pat.search(q):
                return {"intent": intent, "subject": ""}

    # 3. data_check (帶 subject 萃取,但要小心別跟分析查詢混淆)
    for pat in _INTENT_PATTERNS["data_check"]:
        m = pat.search(q)
        if m:
            # 萃取 subject - 第 2 個 capture group
            try:
                subject = m.group(2).strip(" 的?,。!?")
            except (IndexError, AttributeError):
                subject = ""
            # 過濾掉太長的 subject (可能誤判,例如「你有什麼建議要給 TST 公司...」)
            if subject and len(subject) <= 20:
                return {"intent": "data_check", "subject": subject}

    # 4. 預設 analysis
    return {"intent": "analysis", "subject": ""}


_FOLLOWUP_MARKERS = (
    # 修改類動詞
    "改成", "改為", "改用", "改畫", "改看", "改一下", "改", "換成", "換用",
    # 追加類
    "再加", "再來", "再看", "再分析", "也加", "也看", "也展示", "也要",
    "順便", "還要", "另外", "額外", "加上",
    # 範圍調整
    "縮小", "擴大", "範圍", "只看", "只要",
    # 排序/重整
    "排序", "排排看", "重新", "倒過來",
    # 代詞 / 上下文指代
    "上面", "剛才", "剛剛", "前面", "上一個", "上一張", "上次",
    "這張", "這份", "這個結果", "那張", "那份",
    # 英文
    "change", "instead", "switch", "make it", "rather than",
    "also show", "also add", "add to", "remove", "drop", "filter",
    "narrow down", "expand", "sort by", "rank by",
)


def is_followup_query(current_query: str, last_analysis: dict | None) -> bool:
    """
    Pre-Phase 0 · 判斷此 query 是否為延續性提問(modification of last analysis)。
    純 heuristic,零 LLM call。

    觸發條件 (需同時成立):
    1. last_analysis 存在 (有可接續的前次分析)
    2. current_query 含至少一個 follow-up marker
    """
    if not last_analysis or not current_query:
        return False
    q = current_query.strip().lower()
    return any(m in q or m in current_query for m in _FOLLOWUP_MARKERS)


def build_followup_preamble(last_analysis: dict) -> str:
    """產生「接續分析提示」preamble,塞到 Phase 0 的 user message 開頭。"""
    if not last_analysis:
        return ""
    prev_query = last_analysis.get("query", "")
    prev_plan = (last_analysis.get("plan_summary") or "")[:300]
    prev_cols = last_analysis.get("Q_cols") or []
    prev_chart = last_analysis.get("chart_descriptor", "")

    return f"""【🔗 接續分析提示 — CRITICAL】

使用者現在的訊息是「對前一個分析的修改/延伸」,**不是新分析**。

【前次分析脈絡】
- 原問題: {prev_query}
- 前次 Q.columns: {prev_cols}
- 前次圖表類型: {prev_chart or "(未知)"}
- Plan 摘要: {prev_plan or "(無)"}

══════════════════════════════════════════════
【🎯 最高指導原則:MINIMAL CHANGE】
══════════════════════════════════════════════

❶ 若使用者只是要「**換圖表類型**」(改成 X / 換成 Y / 改畫 Z):
   ⭐ A 段(資料獲取)**完全沿用前次**
   ⭐ B 段(Pandas)**完全沿用前次**,Q.columns 必須與前次一致
   ⭐ C 段(視覺化)只改圖表類型
   ⭐ 不要重新解讀維度、不要新增/刪除欄位、不要重做 KPI

❷ 若使用者要「**也加 / 再加** 某個 KPI / 指標」:
   ⭐ A 段保持
   ⭐ B 段在 groupby agg 加新欄位(原欄位不動)
   ⭐ C 段加對應 series

❸ 若使用者要「**只看 / 縮窄 / 改範圍**」:
   ⭐ A 段在 $match 加新過濾
   ⭐ B 段沿用,C 段沿用

══════════════════════════════════════════════
【⚠️ 單一指標 stack 的處理】
══════════════════════════════════════════════

若前次 Q 只有 1 個 numeric 指標(例如只有 `average_return_rate`),
而使用者要求 `stacked bar`,**這在統計上無意義**。請選一:

  (a) 保留為一般 bar,在 plan 中說明「單一指標無法 stack」
  (b) 主動建議:「想看 PAY / RTN / InProgress 的 100% 占比 stacked 嗎?」
  (c) 若使用者明示要堆疊哪些指標,才在 B 段加入

══════════════════════════════════════════════
【🚫 絕對禁忌(常犯錯誤)】
══════════════════════════════════════════════

- 不要把 `hc` / `headcount` 當 x-axis 維度 — 那是參考值,不是分組依據
- 不要產生有重複行的 Q(每個 dimension 值應該只出現一次)
- 不要把 raw count 配 `axisLabel.formatter = "{{value}}%"` — formatter 不會自動 / 100
- 不要在「占比」場景以外,把 yAxis 軸線拉到 > 100% 範圍
- 不要保留前次的「比率類 KPI 欄位名」但配上不同的 raw 數據

══════════════════════════════════════════════
"""


def sanitize_pipeline(pipeline: list) -> list:
    """
    結構性防禦:strip whitespace + 補回漏掉的 `$` 前綴。

    為什麼需要:LLM 偶爾會產出帶前導空格的 stage 鍵名(例如 `" $project"`)或
    漏掉 `$` 寫成 `"match"`,送進 MongoDB 會觸發 `Unrecognized pipeline stage`。
    在 aggregate 之前先過一遍,直接救回來,避免一次 LLM call 浪費。

    僅處理「stage 鍵」這一層 — 不遞迴改 stage 內容(`$in`、`$lookup` 內欄位等),
    因為那些鍵的合法性與業務語意綁定,不該無腦改寫。

    Args:
        pipeline: List[dict] — MongoDB aggregation pipeline。

    Returns:
        新的 list,每個 stage 鍵都已正規化。
    """
    KNOWN_STAGES = {"match", "lookup", "unwind", "project", "addFields",
                    "set", "replaceRoot", "redact"}
    cleaned = []
    for stage in pipeline:
        if not isinstance(stage, dict):
            cleaned.append(stage)
            continue
        new_stage = {}
        for k, v in stage.items():
            key = k.strip()  # 去前後空白
            # 若 key 漏 $ 但語意上是已知 stage,補回去
            if not key.startswith("$") and key in KNOWN_STAGES:
                key = "$" + key
            new_stage[key] = v
        cleaned.append(new_stage)
    return cleaned


def rescue_empty_echarts(option, Q):
    """
    結構性救援:LLM 偶爾會吐出「結構完整但 data 全空」的 option
    (xAxis.data=[]、series=[] 或 series 內 data=[]),exec 不會報錯,
    但畫面是空白圖。此函式偵測這種「殼」並用 Q 做 pivot 補回資料。

    僅在這幾個條件下啟動:
      - option 是 dict
      - series 缺(空 list)或所有 series 的 data 都是空
      - Q 至少有 2 個 non-numeric dim 欄 + 1 個 numeric value 欄

    救援策略:
      - 以 Q 的前兩個非數值欄當 (x_dim, series_dim)
      - 以 (含 percentage / pct / rate / count / total 關鍵字優先) 數值欄當 value
      - pivot → fillna(0) → 灌進 option

    若 option 已是橫向 (xAxis.type=value, yAxis.type=category),
    把 pivot.index 灌到 yAxis;否則灌到 xAxis。

    回傳 (option, was_rescued: bool)。
    """
    import pandas as pd
    if not isinstance(option, dict) or Q is None or len(Q) == 0:
        return option, False

    series = option.get("series", []) or []
    xaxis = option.get("xAxis", {}) or {}
    yaxis = option.get("yAxis", {}) or {}

    # 雙軸圖 (yAxis 或 xAxis 是 list) → 跳過救援,因為 pivot 邏輯不適用
    # (Q 多欄對映到不同 yAxisIndex,結構性救援會搞錯方向)
    if isinstance(yaxis, list) or isinstance(xaxis, list):
        return option, False

    # 判斷是否「空殼」
    series_empty = not series
    series_data_empty = (
        not series_empty
        and all(
            (isinstance(s, dict) and not s.get("data"))
            for s in series
        )
    )
    xaxis_empty = (
        isinstance(xaxis, dict)
        and xaxis.get("type") == "category" and not xaxis.get("data")
    )
    yaxis_empty = (
        isinstance(yaxis, dict)
        and yaxis.get("type") == "category" and not yaxis.get("data")
    )

    if not (series_empty or series_data_empty or xaxis_empty or yaxis_empty):
        return option, False

    # 需要至少 2 dim + 1 value 才能 pivot
    numeric_cols = [c for c in Q.columns if pd.api.types.is_numeric_dtype(Q[c])]
    dim_cols = [c for c in Q.columns if c not in numeric_cols]
    if len(dim_cols) < 2 or not numeric_cols:
        return option, False

    # 挑 value 欄(偏好百分比類)
    preferred = ['percentage', 'percent', '_pct', 'rate', 'ratio',
                 'count', 'total', 'amount']
    value_col = None
    for kw in preferred:
        for c in numeric_cols:
            if kw in c.lower():
                value_col = c
                break
        if value_col:
            break
    if value_col is None:
        value_col = numeric_cols[0]

    x_dim, series_dim = dim_cols[0], dim_cols[1]

    try:
        pivot = (Q.pivot_table(index=x_dim, columns=series_dim,
                                values=value_col, aggfunc='sum')
                  .fillna(0))
    except Exception:
        return option, False

    if pivot.empty:
        return option, False

    # 偵測橫向
    is_horizontal = (
        xaxis.get("type") == "value" and yaxis.get("type") == "category"
    )

    if is_horizontal:
        option.setdefault("yAxis", {})["data"] = pivot.index.astype(str).tolist()
    else:
        option.setdefault("xAxis", {})["data"] = pivot.index.astype(str).tolist()

    # 用既有 stack 名(若 LLM 寫了),否則預設 'stack'
    stack_name = "stack"
    if series:
        for s in series:
            if isinstance(s, dict) and s.get("stack"):
                stack_name = s["stack"]
                break

    option["series"] = [
        {"name": str(col), "type": "bar", "stack": stack_name,
         "data": pivot[col].round(2).tolist()}
        for col in pivot.columns
    ]
    return option, True


# 20-色擴充色盤,避免 ECharts 預設 6 色在 series 多時循環造成顏色重複
DEFAULT_COLOR_PALETTE = [
    "#5470c6", "#91cc75", "#fac858", "#ee6666", "#73c0de",
    "#3ba272", "#fc8452", "#9a60b4", "#ea7ccc", "#5b9bd5",
    "#a5a5a5", "#ffc000", "#7b78de", "#27a39d", "#e15759",
    "#f28e2c", "#76b7b2", "#59a14f", "#edc949", "#b07aa1",
]


def _extend_palette(n: int) -> list:
    """
    確保色盤至少 n 色。前 20 色從 DEFAULT_COLOR_PALETTE 取,
    超過的用 HSL 均勻分佈生成,確保每個 series 都拿到唯一顏色。
    """
    if n <= len(DEFAULT_COLOR_PALETTE):
        return DEFAULT_COLOR_PALETTE[:max(n, 6)]
    import colorsys
    extras = []
    extra_count = n - len(DEFAULT_COLOR_PALETTE)
    for i in range(extra_count):
        # HSL 均勻分佈,避開太亮 / 太暗
        h = (i * 0.618033988749895) % 1.0  # 黃金比例避免相鄰太近
        r, g, b = colorsys.hls_to_rgb(h, 0.55, 0.55)
        extras.append(f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}")
    return DEFAULT_COLOR_PALETTE + extras


def ensure_default_styling(option, query: str = ""):
    """
    結構性樣式補強:處理三個 LLM 容易漏的細節 — legend / 色盤長度 / grid 留白。

    為什麼只補 legend + 色盤、不補 label:
      - legend 漏掉 = 多 series 圖看不出顏色對應,補了無副作用。
      - 色盤太短 = ECharts 預設 6 色,>6 series 時會循環造成「TST 跟 TDC 都紅色」,補了無副作用。
      - label 是否擁擠跟 series 數、bar 數、figure 大小有關,LLM 比 hardcode 判斷更準。

    若使用者明說「不要 legend」「精簡」「minimal」等,則不補 legend(色盤仍會補,顏色問題跟簡潔無關)。

    Args:
        option: ECharts option dict (mutated in place)
        query: 使用者原始查詢,用於辨識「minimal / 不要 legend」傾向

    Returns:
        (option, injected: bool) — injected=True 代表有任何補強(legend 或色盤擴充)
    """
    if not isinstance(option, dict):
        return option, False

    if option.get("_use_table"):
        return option, False

    series = option.get("series", []) or []
    n_series = len(series) if isinstance(series, list) else 0
    injected_any = False

    # ━━━ 色盤擴充 ━━━(無條件啟動,跟 minimal 無關)
    current_color = option.get("color")
    needs_palette_extension = (
        n_series > 0
        and (
            not isinstance(current_color, list)
            or len(current_color) < n_series
        )
    )
    if needs_palette_extension:
        option["color"] = _extend_palette(n_series)
        injected_any = True

    # ━━━ 偏態長尾資料 auto log scale ━━━
    # 偵測 bar series 值域跨越 >100 倍,把對應 yAxis 切成 log,避免小值被壓扁
    def _series_skew_ratio(s):
        """回傳 (max/min) ratio,若無法計算則 None。要求所有值 > 0(log 需求)。"""
        if not isinstance(s, dict):
            return None
        if s.get("type") not in ("bar", "line"):
            return None
        data = s.get("data") or []
        if not isinstance(data, list):
            return None
        vals = []
        for v in data:
            try:
                # ECharts 也接受 {value: x, name: ...} 結構
                if isinstance(v, dict):
                    v = v.get("value")
                v = float(v)
            except (TypeError, ValueError):
                return None
            if v <= 0:  # log scale 不支援 0 或負值
                return None
            vals.append(v)
        if len(vals) < 3:
            return None
        return max(vals) / min(vals)

    # 找出該套 log 的 series 對應 yAxisIndex
    yaxis_obj = option.get("yAxis")
    yaxis_is_list = isinstance(yaxis_obj, list)
    log_targets = set()  # yAxisIndex 集合
    SKEW_THRESHOLD = 100  # max/min 跨越 100 倍視為需 log

    for s in series:
        if not isinstance(s, dict):
            continue
        ratio = _series_skew_ratio(s)
        if ratio is None or ratio <= SKEW_THRESHOLD:
            continue
        # bar / line 才考慮 log;率類欄位通常不會跨 100 倍但保險起見排除
        name = (s.get("name") or "").lower()
        if any(t in name for t in ("率", "rate", "ratio", "百分比", "percent")):
            continue
        log_targets.add(s.get("yAxisIndex", 0))

    if log_targets:
        if yaxis_is_list:
            for idx in log_targets:
                if 0 <= idx < len(yaxis_obj):
                    ax = yaxis_obj[idx]
                    if isinstance(ax, dict) and ax.get("type") != "log":
                        ax["type"] = "log"
                        injected_any = True
        elif isinstance(yaxis_obj, dict):
            if 0 in log_targets and yaxis_obj.get("type") != "log":
                yaxis_obj["type"] = "log"
                injected_any = True

    # ━━━ Heatmap 救援 ━━━(cast numpy 型別 + 修 tooltip.trigger)
    heatmap_series = [s for s in series if isinstance(s, dict) and s.get("type") == "heatmap"]
    if heatmap_series:
        # tooltip.trigger 必須是 "item"
        tooltip = option.get("tooltip")
        if isinstance(tooltip, dict) and tooltip.get("trigger") in ("cell", "axis"):
            tooltip["trigger"] = "item"
            injected_any = True
        # visualMap min/max cast 成 float
        vm = option.get("visualMap")
        if isinstance(vm, dict):
            for k in ("min", "max"):
                v = vm.get(k)
                if v is not None and not isinstance(v, (int, float, bool)):
                    try:
                        vm[k] = float(v)
                        injected_any = True
                    except Exception:
                        pass
            # 缺 inRange.color → 補上預設藍漸層
            if "inRange" not in vm or not vm["inRange"]:
                vm["inRange"] = {
                    "color": ["#e6f1fb", "#85b7eb", "#185fa5", "#0c447c"]
                }
                injected_any = True
        # series.data cast 每筆值(無條件覆寫 — Python list 等值比較對 numpy 失效,
        # 改用「有沒有 numpy 型別」當判斷依據)
        for s in heatmap_series:
            data = s.get("data")
            if isinstance(data, list) and data:
                needs_cast = any(
                    isinstance(row, (list, tuple)) and len(row) >= 3
                    and not isinstance(row[2], (int, float, bool))
                    for row in data
                )
                if needs_cast:
                    cleaned = []
                    for row in data:
                        if isinstance(row, (list, tuple)) and len(row) >= 3:
                            try:
                                cleaned.append([
                                    str(row[0]),
                                    str(row[1]),
                                    float(row[2]),
                                ])
                            except Exception:
                                cleaned.append(list(row))
                        else:
                            cleaned.append(row)
                    s["data"] = cleaned
                    injected_any = True

    # ━━━ legend 補強 ━━━(尊重 minimal 傾向)
    minimal_signals = ("不要 legend", "不要圖例", "不要標籤", "精簡", "minimal", "乾淨", "清爽")
    q_lower = (query or "").lower()
    is_minimal = any(sig in q_lower for sig in minimal_signals)

    if (not is_minimal
            and n_series >= 2
            and not (option.get("legend") and option["legend"])):
        option["legend"] = {"show": True, "top": 30}
        grid = option.get("grid")
        if isinstance(grid, dict) and grid.get("top", 60) < 60:
            grid["top"] = 70
        injected_any = True

    return option, injected_any


def is_dashboard_query(query: str) -> bool:
    """
    啟發式偵測:此查詢是否為「dashboard / 執行摘要」場景。

    為什麼需要這個:
    這類查詢通常需要算「整體 scalar KPI」(總數、平均率等),
    沒有明確 groupby 維度。LLM 容易在 Phase B 嘗試 `Q.agg(named_agg)`
    這種 anti-pattern 並失敗。
    偵測到後改走「row-level pass-through + Phase C `_kpi_cards`」路徑。
    """
    if not query:
        return False
    q = query.lower()
    if not any(kw in q for kw in _DASHBOARD_KEYWORDS):
        return False
    # 排除明確有 groupby 維度的句子(避免誤判)
    strong_groupby = (
        "by ", "per ", "各", "依", "by region", "by category",
        "by channel", "by company", "by department", "by specialty",
    )
    has_groupby = any(g in q for g in strong_groupby)
    return not has_groupby


PANDAS_ANTIPATTERN_CHEATSHEET = """
### 🛡 常見 Pandas Anti-pattern 速查表 (重生時請對照,避免再犯):

❌  `Q.agg(name=(col, op), ...)` 直接對 DataFrame 用 named agg
    為什麼:這個 syntax 只在 `Q.groupby(...).agg(...)` 內合法,沒 groupby 直接用會出現奇怪形狀。
    ✅  做整體 (overall scalar) 聚合請改用 scalar 變數:
        total = len(Q)
        paid  = (Q['col'] == 'Y').sum()
        amt   = (Q['x'] * Q['y']).sum()
        Q = pd.DataFrame({{"metric": [...], "value": [...]}})

❌  `Q['col'].first()` (Series 方法幻覺)
    為什麼:Series 沒有 `.first()` 方法,會 AttributeError。
    ✅  `Q['col'].iloc[0]` 取首列值;groupby 內可用 `agg(col=('col', 'first'))` (字串 'first')。

❌  `Q.merge(Q[[...]], on='col')` self-merge
    為什麼:同欄位名衝突,pandas 自動 rename 成 `col_x` / `col_y`,後續引用 KeyError。
    ✅  用 `agg(col=('col', 'first'))` 在 groupby 結果直接帶上參考欄位。

❌  漏寫 `Q = grouped` 最終指派
    為什麼:中間做了 groupby/agg,但忘了把結果 assign 回 Q,Phase C 找不到 KPI 欄位。
    ✅  不管中間用什麼變數名 (grouped/result/agg_df...),**最後一行**必寫 `Q = <最終結果>`。

❌  引用 raw_df 樣本中沒有的欄位 (幻覺欄位)
    為什麼:即使你「直覺認為」某欄位該存在,只要不在 avail_cols 中,引用就會 KeyError。
    ✅  寫前心算每個 `Q['xxx']` 是否存在於 avail_cols;計 row 數請用 `Q.groupby(...).size()`。

❌  `Q.pivot(index=A, columns=B)` 把 Q 變 wide format
    為什麼:把值散到動態欄位名後,Phase C 沒辦法用固定欄位名引用,且 ECharts heatmap 要 long format。
    ✅  維持 long format `[dim_a, dim_b, value]` 三欄;只有純表格 (use_table) 場景可考慮 wide。

❌  比率/除法 用 string 欄位當分子或分母 (例:`Q['count'] / Q['employee_id']`)
    為什麼:string 欄位 (任何 ID / code / category / status / mechanism) 無法做算術運算,
    會炸 `TypeError: operation 'rtruediv' not supported for dtype 'str'`。
    識別:metadata 中 `type: "string"` 或 `"string_or_null"` 的欄位都是字串型,**只能用於 filter / groupby / nunique**。
    ✅  比率類 KPI 的分子分母**必須都是 numeric** (透過 sum/count 等得到):
        ```python
        Q['is_ai'] = (Q['review_status']=='Y') & (Q['review_mechanism']=='AI')  # bool
        agg = Q.groupby('<dim>').agg(
            ai_count=('is_ai', 'sum'),              # int
            completed=('is_completed', 'sum'),       # int
        )
        agg['ai_rate'] = agg['ai_count'] / agg['completed']  # ✅ int / int
        ```
    ✅  Distinct string ID 計數請用 `nunique()`,**不要用 div**:
        `submitter_count=('employee_id', 'nunique')`
"""


class LLMService:
    """
    封裝 4 階段 LLM 呼叫:
        Phase 0  generate_plan          — 規劃三階段
        Phase A  generate_pipeline      — 產 MongoDB pipeline
        Phase B  generate_preprocess_code — 產 Pandas 處理腳本 (Q)
        Phase C  generate_plot_code     — 產 Plotly 繪圖腳本 (fig)
        Phase D  generate_insight       — 產商業洞察文字
    所有 code-gen 方法都支援 previous_code / previous_error 作為自我修正回饋。
    """

    def __init__(self,
                 api_url: str = "http://localhost:11434/v1/chat/completions",
                 api_key: str = "ollama",
                 model_name: str = "qwen3-coder:30b",
                 timeout_s: float = 180.0,
                 default_temperature: float = 0.0,
                 task_metadata: dict | None = None,
                 prompt_repo=None,
                 domain: str = "tflex"):
        """
        參數預設指向 Ollama (localhost:11434);
        若你在用 vLLM,把 api_url 改成 http://localhost:8000/v1/chat/completions、
        model_name 改成 vLLM 啟動時 --served-model-name 設定的值即可。

        timeout_s: 本地 thinking 模型首次推論可能 120-180s,給足。
        default_temperature: code-gen 任務建議 0.0,Plan/Insight 會在內部自行抬高。
        task_metadata: domain 描述 dict (schema/KPI/限制/recommended_charts)。
                       若 None,自動載入 tflex_task_metadata_agent_v3.TASK_METADATA。
                       換不同 domain 時傳入該 domain 的 metadata 即可,不必改本 module 的程式碼。
        prompt_repo: 可選的 PromptRepository (v0.3.0+)。
                     若 None,自動 build 一個(走 config.PROMPT_REPO_ENABLED + embedded fallback)。
                     傳入 False 強制不使用 repo,完全 inline f-string(用於 v0.2.x 行為對照)。
        domain: 目前處理的 domain 名稱(影響 prompt 讀取的 domain_scope)。
        """
        self.client = OpenAI(
            base_url=api_url.replace("/chat/completions", ""),
            api_key=api_key,
            timeout=timeout_s,
        )
        self.model_name = model_name
        self.default_temperature = default_temperature
        self.timeout_s = timeout_s
        self.domain = domain

        # ── 載入並組裝 domain knowledge / few-shot ──
        if task_metadata is None:
            from tflex_task_metadata_agent_v3 import TASK_METADATA as _DEFAULT_META
            task_metadata = _DEFAULT_META
        self.task_metadata = task_metadata
        self.domain_knowledge = build_domain_knowledge(task_metadata)
        self.echarts_few_shot = build_echarts_few_shot(task_metadata)
        # Pre-Phase 0 out_of_scope 偵測用的 vocab(一次建好)
        self._metadata_vocab = build_metadata_vocab(task_metadata)

        # ── Telemetry:每次 LLM call 的耗時與 token 用量 ──
        # 由外部測試框架在 case 開始前呼叫 reset_call_log(),結束時 get_call_summary()
        self.call_log: list[dict] = []

        # ── v0.3.0+ Prompt Repository ──
        # 若 prompt_repo is False,完全停用 repo(回退 v0.2.x inline 行為)
        # 若 prompt_repo is None,build default(走 config.PROMPT_REPO_ENABLED + embedded)
        # 若是 PromptRepository instance,直接用
        if prompt_repo is False:
            self.prompt_repo = None
        elif prompt_repo is None:
            try:
                from prompt_repository import build_default_repo
                self.prompt_repo = build_default_repo(mongo_db=None)
            except Exception:
                self.prompt_repo = None
        else:
            self.prompt_repo = prompt_repo

    def classify_intent_for_query(
        self, query: str, last_analysis: dict | None = None
    ) -> dict:
        """
        Pre-Phase 0 路由(instance 版本)。

        判斷順序(優先級從高到低):
        1. 5 個 explicit meta intent (intro / data_overview / data_check / guidance / greeting)
        2. **Follow-up**(若有 last_analysis + 含修改詞,視為 analysis,
            標 `is_followup=True` 讓 app.py 注入前次脈絡)
        3. out_of_scope(query 與 metadata 完全無關)
        4. analysis(預設 fallthrough)

        ⭐ Follow-up 優先於 out_of_scope,因為短的修改指令(「改成 X」「也加 Y」)
        本來就常缺 metadata vocab,不該被誤判離題。
        """
        base = classify_intent(query)
        if base["intent"] != "analysis":
            return base

        # ⭐ 接續分析優先 — 在 out_of_scope 之前檢查
        if is_followup_query(query, last_analysis):
            return {"intent": "analysis", "subject": "", "is_followup": True}

        # 真正的 out_of_scope
        if is_out_of_scope(query, self._metadata_vocab):
            return {"intent": "out_of_scope", "subject": query.strip()[:60]}

        return base

    # --------------------------------------------------------
    # 內部工具
    # --------------------------------------------------------
    def _call_llm(self, messages, temperature=None, max_tokens=2048,
                   phase: str = "unknown"):
        if temperature is None:
            temperature = self.default_temperature
        t0 = time.time()
        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except Exception as e:
            self.call_log.append({
                "phase": phase,
                "elapsed_s": round(time.time() - t0, 2),
                "prompt_tokens": None,
                "completion_tokens": None,
                "total_tokens": None,
                "error": str(e),
            })
            raise RuntimeError(f"LLM API 呼叫失敗: {str(e)}")

        elapsed = round(time.time() - t0, 2)
        usage = getattr(response, "usage", None)
        self.call_log.append({
            "phase": phase,
            "elapsed_s": elapsed,
            "prompt_tokens": getattr(usage, "prompt_tokens", None) if usage else None,
            "completion_tokens": getattr(usage, "completion_tokens", None) if usage else None,
            "total_tokens": getattr(usage, "total_tokens", None) if usage else None,
        })
        return response.choices[0].message.content

    def reset_call_log(self) -> None:
        """測試 framework 在每個 case 開始前呼叫,清空累積 telemetry。"""
        self.call_log = []

    def get_call_summary(self) -> dict:
        """彙總目前 call_log 的 cost telemetry。"""
        if not self.call_log:
            return {"calls": 0, "total_elapsed_s": 0.0,
                    "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                    "by_phase": {}}
        by_phase: dict[str, dict] = {}
        for c in self.call_log:
            p = c.get("phase", "unknown")
            d = by_phase.setdefault(p, {"calls": 0, "elapsed_s": 0.0,
                                        "prompt_tokens": 0, "completion_tokens": 0})
            d["calls"] += 1
            d["elapsed_s"] += c.get("elapsed_s") or 0
            d["prompt_tokens"] += c.get("prompt_tokens") or 0
            d["completion_tokens"] += c.get("completion_tokens") or 0
        total = {
            "calls": len(self.call_log),
            "total_elapsed_s": round(sum(c.get("elapsed_s") or 0 for c in self.call_log), 2),
            "prompt_tokens": sum(c.get("prompt_tokens") or 0 for c in self.call_log),
            "completion_tokens": sum(c.get("completion_tokens") or 0 for c in self.call_log),
            "total_tokens": sum(c.get("total_tokens") or 0 for c in self.call_log),
            "by_phase": {p: {**d, "elapsed_s": round(d["elapsed_s"], 2)} for p, d in by_phase.items()},
        }
        return total

    @staticmethod
    def _strip_code_fence(raw: str, lang: str = "") -> str:
        """去除 ```python ... ``` 或 ```json ... ``` 之類的圍欄。"""
        if not raw:
            return ""
        pattern = rf"^```(?:{lang})?\s*|\s*```$"
        return re.sub(pattern, "", raw, flags=re.MULTILINE).strip()

    @staticmethod
    def _format_retry_hint(previous_code: str, previous_error: str,
                            cheatsheet: str = "") -> str:
        """把上一次失敗的 code + traceback 轉成 LLM 修正提示。
        可選 cheatsheet 附在後面,提示常見 anti-pattern。"""
        if not previous_code and not previous_error:
            return ""
        hint = (
            "\n\n### 🔁 自我修正提示\n"
            "你上一次回覆的程式碼執行失敗,請仔細檢查錯誤訊息後重新生成正確版本。\n"
            "**禁止再犯同樣錯誤**,也不要對結構做不必要的改動。\n\n"
            "上次的程式碼:\n```python\n"
            f"{previous_code}\n```\n"
            "錯誤訊息 (Traceback):\n```\n"
            f"{previous_error}\n```\n"
        )
        if cheatsheet:
            hint += "\n" + cheatsheet
        return hint

    # --------------------------------------------------------
    # Phase 0: 計畫
    # --------------------------------------------------------
    def generate_plan(self, query, followup_context: dict | None = None):
        # v0.3.0+: 嘗試從 repo 讀模板;失敗則 fallback 到下方 inline f-string
        # 這個雙軌 design 確保:
        # - DB enabled 時用 DB 版本(可線上編輯)
        # - DB disabled / 連不上 / 內容缺 時自動 fallback 到 inline
        # - 兩條路徑 byte-equal 才算正確(D3 驗證重點)
        system_prompt = self._render_phase_0_plan_prompt()
        # 接續分析時注入前次脈絡
        followup_preamble = build_followup_preamble(followup_context) if followup_context else ""
        user_msg = followup_preamble + f"需求:{query}\n請給出計畫:"
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ]
        try:
            return {"status": "success", "message": self._call_llm(messages, temperature=0.2, phase="plan")}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _render_phase_0_plan_prompt(self) -> str:
        """
        產生 Phase 0 plan system prompt。

        優先序:
        1. PromptRepository.render() — 若 repo 可用且 enabled
        2. Inline f-string fallback — v0.2.x 行為

        驗證點(D3 byte-equal):
            assert llm._render_phase_0_plan_prompt(via_repo=True) == _inline_version
        """
        if self.prompt_repo is not None:
            try:
                return self.prompt_repo.render(
                    "phase_0_plan",
                    domain=self.domain,
                    domain_knowledge=self.domain_knowledge,
                )
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(
                    f"Phase 0 prompt repo render 失敗,fallback to inline: {e}"
                )
        # Inline fallback(跟 v0.2.x 完全一致)
        return self._inline_phase_0_plan_prompt()

    def _inline_phase_0_plan_prompt(self) -> str:
        """v0.2.x 行為的 inline f-string 副本 — repo 失敗時的最終救援。"""
        return f"""你是專業的 AI 商業智慧助理。請以上方 Domain Knowledge 為唯一依據規劃分析。

{self.domain_knowledge}

### 🚨 拒絕協定 (Schema-Driven · 每次從 metadata 推理,不要記憶特定詞彙)

⚠️ 核心原則:**寧可不拒絕、走計畫,也不要 false positive 拒絕。**
判斷的唯一依據是**當前 metadata 的 `collections.*.fields` 與 `data_limitations`**,
不要因為查詢含某個中文/英文詞就條件反射拒絕 — 永遠重新從 schema 推理。

【三步推理流程】

**Step 1 · 解析 query** — 識別查詢需要哪些「資料維度/指標」。例如:
- 查詢提到「過去三個月」→ 需要【時間維度】
- 查詢提到「平均金額」→ 需要【金額/數量指標】
- 查詢提到「比較各公司」→ 需要【公司類別維度】
- 查詢提到「分佈」「熱力圖」「占比」→ **這只是分析操作,不需要特定維度**

**Step 2 · 查 metadata 的 schema** — 對應的維度/指標是否存在於 `collections.*.fields`?
- 是 → Step 3 不適用,**走 A/B/C 計畫**
- 否 → 進入 Step 3

**Step 3 · 查 data_limitations** — 該分析類型是否被 `missing_dimensions` 或
`not_supported_analysis` 明文列為不支援?
- 是 → 拒絕 (`[REFUSE]`)
- 否 → 即使欄位缺,也嘗試走計畫(讓使用者看到下游錯誤而非錯拒絕)

【判斷練習(以當前 metadata 為準,LLM 自行推理)】

範例 1:「畫一張熱力圖,看不同公司在四個申請類別的分佈差異」
- Step 1:需要 company 維度 + category 維度,「熱力圖」「分佈」只是分析操作
- Step 2:`company_code` 與 `application_category` 都在 schema 中
- ✅ 走計畫

範例 2:「我想看過去三個月的申請趨勢」
- Step 1:需要時間維度(「過去三個月」+「趨勢」明確要求時間軸)
- Step 2:查 schema 是否有 date/timestamp 類欄位 → 沒有
- Step 3:`data_limitations.not_supported_analysis` 是否含 "trend" 或 "time" → 是
- ❌ `[REFUSE]`

範例 3:「平均申請金額」
- Step 1:需要金額/價格類數值欄位
- Step 2:查 schema 是否有 amount/price 類欄位 → 沒有
- Step 3:`data_limitations` 是否明示金額不支援 → 是
- ❌ `[REFUSE]`

⚠️ 注意:**今天的 metadata 可能下個月變**(domain expert 新增欄位後,以前不支援的分析變成支援)。
你的推理必須**完全基於 prompt 上方提供的當前 metadata 內容**,不要對某 domain 預設「永遠不支援 X」。

【拒絕回覆格式】(僅在 Step 3 確認後使用):
```
[REFUSE] 缺少 <metadata 中真實的欄位/維度名>,無法執行 <分析類型>。
此為 data_limitations 中 "<引用原文限制名>" 的限制。
建議改問:<以 metadata 中存在的欄位為基礎的替代分析>。
```
**不要寫 A/B/C 三段。** 下游會根據 [REFUSE] 標記自動中止。

【一般回覆格式】(Step 2 通過時使用):
**A. 資料獲取:** ...
**B. 資料處理:** ...
**C. 視覺化建議:** ...

### 三階段執行計畫格式 (僅在【未拒絕】時使用,Markdown 精簡有力):
**A. 資料獲取:** 起手 collection、需 join 的表、需要的 $match 過濾條件 (參照 schema 的維度欄位)。
**B. 資料處理:** 要算哪些 KPI (引用上方 kpi_definitions 公式) 與 pandas 邏輯重點。
**C. 視覺化建議:** 圖型選擇與理由。多類別比較禁止 pie chart;若是「dashboard / 執行摘要」場景,建議走表格 + KPI 卡片。
"""

    # --------------------------------------------------------
    # Phase A: MongoDB pipeline
    # --------------------------------------------------------
    def generate_pipeline(self, query, plan_text="",
                          previous_code: str = "", previous_error: str = ""):
        system_prompt = self._render_phase_a_pipeline_prompt()
        user_msg = f"需求:{query}\n計畫:{plan_text}"
        user_msg += self._format_retry_hint(previous_code, previous_error)
        raw = self._call_llm(
            [{"role": "system", "content": system_prompt},
             {"role": "user", "content": user_msg}],
            phase="pipeline",
        )
        return self._strip_code_fence(raw, lang="json")

    def _render_phase_a_pipeline_prompt(self) -> str:
        """產生 Phase A pipeline system prompt(repo → inline fallback)。"""
        if self.prompt_repo is not None:
            try:
                return self.prompt_repo.render(
                    "phase_a_pipeline",
                    domain=self.domain,
                    domain_knowledge=self.domain_knowledge,
                )
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(
                    f"Phase A prompt repo render 失敗,fallback to inline: {e}"
                )
        return self._inline_phase_a_pipeline_prompt()

    def _inline_phase_a_pipeline_prompt(self) -> str:
        """v0.2.x 行為 inline f-string 副本。"""
        return f"""你是精通 MongoDB 的資料庫工程師,負責【A. 資料獲取】。
{self.domain_knowledge}

### 實作守則 (CRITICAL RULES):
1. 【輸出格式】(CRITICAL FATAL) 必須輸出合法 JSON,包含 `start_collection` (字串) 與 `pipeline` (陣列),
   除 JSON 外不要包含任何說明文字。
2. 🔗【關聯鐵律】當 KPI 公式需要其他 collection 的欄位 (參照上方 relationships),
   必須 `$lookup` 該表並緊接 `$unwind` (使用 `preserveNullAndEmptyArrays: true`)。
3. 🚫【禁止寫入】禁止 `$out`、`$merge`。
4. 🚫【嚴禁在 DB 端聚合】(CRITICAL FATAL) 任務是撈「明細」交給 Pandas。
   禁止 `$group`、`$count`、`$sort`、`$limit`,只能用 `$match`、`$lookup`、`$unwind`、`$project`。
5. 🚫【禁止在 DB 算比例】(CRITICAL FATAL) 禁止 `$divide`、`$cond` 算 KPI,
   一切比例都交給 Pandas。
5.5 ✅【Entity 過濾鐵律】(CRITICAL — 容易漏)
   若使用者查詢中明確列出**特定實體值**(例如:「TST、TSN、TSC」「Apparel、Books 類別」
   「Cardiology 跟 Pediatrics 科別」),Phase A 的 pipeline **必須**包含對應的 `$in` 過濾 stage。
   做法:在下方範例結構中保留 `$match` stage(完整鍵名是 `"$match"`,含錢字符號),
   並把 `<dimension_field>` / `<value*>` 換成使用者真正列出的維度與值。
   不要寫成 `"match"`(會被 MongoDB 拒絕 `Unrecognized pipeline stage`);
   不要把過濾留給下游 Pandas — 在 DB 端就限縮,降低資料量也避免下游遺漏。

6. ✅【$project 鐵律】(CRITICAL FATAL) `$project` 必須保留 source collection
   與 join 表中【所有 metadata 描述過的欄位】(除 `_id` 外),不要為了「精簡」而砍欄位。
   原因:Phase B 的 Pandas 程式可能會引用任何原始欄位 (計 count、再次驗證 filter 條件等),
   提早砍掉會讓 Phase B KeyError。
   即使你的 `$match` 已過濾某欄位的某值,仍要把該欄位留在 $project 中。
   具體欄位清單請對照上方 Domain Knowledge 中各 collection 的 fields 區塊。

### 輸出範例結構 (僅做撈取與關聯,以 metadata 中真實 collection / 欄位為準):
{{
    "start_collection": "<上方 schema 中的主表名>",
    "pipeline": [
        {{ "$match": {{ "<dimension_field>": {{ "$in": ["<value1>", "<value2>"] }} }} }},
        {{ "$lookup": {{ "from": "<關聯表>", "localField": "<join_key>",
            "foreignField": "<join_key>", "as": "<別名>" }} }},
        {{ "$unwind": {{ "path": "$<別名>", "preserveNullAndEmptyArrays": true }} }},
        {{ "$project": {{ "_id": 0,
            "<主表所有 metadata 描述欄位>": 1,
            "<關聯表欄位>": "$<別名>.<關聯表欄位>"
        }} }}
    ]
}}"""

    # --------------------------------------------------------
    # Phase B: Pandas 處理
    # --------------------------------------------------------
    def generate_preprocess_code(self, query, plan_text="", available_columns=None,
                                  raw_df_sample: str = "",
                                  dashboard_hint: bool = False,
                                  previous_code: str = "", previous_error: str = ""):
        cols_info = (
            f"目前 raw_df 的欄位 (鎖死,不可亂改名): {available_columns}"
            if available_columns else "欄位未知。"
        )
        if raw_df_sample:
            cols_info += (
                "\n\n### raw_df 實際前 3 列樣本 (你必須以此為準,不要憑訓練資料猜測欄位):\n"
                f"{raw_df_sample}\n\n"
                "⚠️ 上面沒列出的欄位,Phase A 可能已 $project 砍掉了,**絕對禁止引用**。\n"
                "⚠️ 上游 $match 已過濾的欄位,值可能全部一致 (例如某狀態欄位全為固定值),"
                "不需要再做相同過濾,但仍可用於計算。"
            )
        dashboard_block = ""
        if dashboard_hint:
            dashboard_block = """
### 🎯 DASHBOARD MODE (系統偵測到此為 dashboard / 執行摘要場景)
此查詢屬於「整體 KPI 一覽」性質,沒有明確 groupby 維度,**請走 row-level pass-through**:

✅ 推薦做法:Q 保持 row-level (raw_df + 衍生 bool 欄位),把 scalar 算式交給 Phase C 的 `_kpi_cards`:
```python
Q = raw_df.copy()
# 只加衍生 bool / 數值欄位,不做 groupby/agg
Q['is_<state_a>'] = (Q['<status_col>'] == '<val_a>')
Q['is_<state_b>'] = (Q['<status_col>'] == '<val_b>')
# 不要再做 groupby/agg!Phase C 會用 f"{Q['col'].sum():,}" 計算總量
```

🚫 嚴禁:`Q.agg(name=(col, op))` 或任何「沒 groupby 的 named aggregation」
🚫 嚴禁:`Q = pd.DataFrame({{'metric': [...], 'value': [...]}})` 這種預組 KPI 表
   (因為 Phase C 處理 `_kpi_cards` 時會自己組,你做了反而干擾。)
🚫 嚴禁:在 Q 中加入 TOTAL / SUMMARY / GRAND TOTAL / 合計 / 總計 / 全公司 之類的「聚合摘要列」!
   (Phase C 的 `_kpi_cards` 會用 `Q['col'].sum()` 算總量,
    如果 Q 已含 TOTAL 列會導致數值【雙倍計算】。)
   ✅ 若你做了 groupby,Q 就是每組一列 (15 家公司 = 15 列),**不要再加第 16 列當 TOTAL**。

"""

        system_prompt = f"""你是精通 Pandas 的資深資料工程師,負責【B. 資料處理】。
{cols_info}

{self.domain_knowledge}
{dashboard_block}
### 實作守則 (CRITICAL RULES):
1. 🎯【最外層產出 Q】(CRITICAL FATAL) 最外層必須宣告 `Q` (DataFrame)。
   禁止包在 function/class 內,禁止 `if __name__`,禁止 `print`。

1.5 🎯【終態必須 Q = 最終結果】(CRITICAL FATAL — 最常犯) 不管你中間用什麼變數名
   (`grouped`、`result`、`agg_df`、`heatmap_data`、`return_counts` 等),
   **最後一行一定要寫 `Q = <你的最終結果>`**!
   ❌ 反例 (Phase C 會 KeyError,因為 Q 仍是原 raw_df):
   ```python
   Q = raw_df.copy()
   grouped = Q.groupby('<dim>').agg(...)
   grouped['<new_metric>'] = ...
   # 沒了!Q 還是 raw_df!
   ```
   ✅ 正解:
   ```python
   Q = raw_df.copy()
   grouped = Q.groupby('<dim>').agg(...)
   grouped['<new_metric>'] = ...
   Q = grouped            # ← 這一行絕對不能忘!
   ```

2. 🔠【大小寫鎖死 + KPI 欄名對齊】絕不擅自修改欄位名稱格式。
   ⭐ **新增欄位時(例如做完 groupby+agg 後),請優先使用 `kpi_definitions` 的 key
   作為欄位名(例:`total_applications`、`average_return_rate`、`ai_review_rate` 等)**,
   這樣 Phase C 能根據相同名稱穩定引用,避免 KeyError。
3. 🛡️【KPI 公式來源】(CRITICAL) 所有 KPI 計算【嚴格】依照上方 Domain Knowledge 中的
   `kpi_definitions`,不可自創邏輯或自行重新定義分子分母。特別注意:
   - 比率類 KPI 注意分母的精確定義 (常見錯誤:用 total count 取代 metadata 指定的 base count)。
   - 注意每個 KPI 的 `important_note`(若有),通常標示「不可包含 X 狀態」之類的限制。
   - 涉及 distinct 計數一律用 `nunique()`,不要 `len(set(...))`。
   - 涉及 ID 欄位 (主鍵類字串) 不要轉 int,保持字串。
4. 🛡️【小樣本處理】若涉及比率,請保留分子分母絕對數,便於後續判讀。
5. 🚫【禁止外部 IO】不可呼叫 read_csv / read_sql / open。raw_df 已備好。
6. 🚫【禁止 self-merge】(CRITICAL FATAL) raw_df 已是上游 join 完成的長表,
   絕對禁止 `Q.merge(Q[[...]], on='col')` 自我 merge,
   會讓欄位被 pandas 自動 rename 成 `col_x` / `col_y`,後續 KeyError。
   若要在 groupby 結果中帶上某參考欄位 (例如維度上的 headcount),
   請用 `agg(my_col=('my_col', 'first'))`,不要再去 merge raw_df。
7. 🚫【禁止幻覺欄位】只使用上方 avail_cols 與 raw_df 樣本中【實際出現】的欄位。
   即使你「直覺認為」某欄位(如主鍵 / 某狀態欄位)應該存在,只要不在 avail_cols 中就禁止引用。
   要計 row 數量請用 `Q.groupby(...).size()`,不要依賴特定 ID 欄位的 `count`。
8. ✅【寫前自我驗證】寫 code 前先在腦中跑一次:每個 `Q['xxx']` 的 'xxx'
   都必須在 avail_cols 中,否則改寫。
8.5 🎯【比率類 KPI 標準骨架】(CRITICAL — 避免型別錯誤)
   當需要新增比率類 KPI 欄位(例:AI 採用率、退單率、完成率),統一走這個骨架:
   ```python
   # Step 1: 在 raw level 建 bool flag (用 metadata 中的 string 狀態做 == 比較)
   Q['is_<state>'] = (Q['<status_col>'] == '<value>')
   #   例:Q['is_ai'] = (Q['review_status']=='Y') & (Q['review_mechanism']=='AI')

   # Step 2: groupby + agg 把 bool sum 成 int count
   agg = Q.groupby('<dim_col>').agg(
       <numerator>=('is_<state>', 'sum'),       # int
       <denominator>=('is_<base>', 'sum'),       # int
   ).reset_index()

   # Step 3: 用兩個 int 欄位相除得 float rate
   agg['<rate>'] = agg['<numerator>'] / agg['<denominator>']

   Q = agg
   ```
   ⚠️ **絕對禁止** 直接用 string 欄位做除法 (例如 `Q['review_mechanism'] / Q['count']`),
   會炸 `TypeError: operation 'rtruediv' not supported for dtype 'str'`。

9. 🚫【Series.first() 禁區】(CRITICAL — 常見幻覺) Series 物件**沒有 `.first()` 方法**!
   - ❌ `Q['hc'].first()` → AttributeError
   - ✅ `Q['hc'].iloc[0]` (取首列值)
   - ✅ `Q.groupby(...).agg(hc=('hc', 'first'))` (此處 'first' 是 agg function 字串,合法)
   - 一般取「每組第一筆」請用 `groupby(...).first()` (回傳 DataFrame,合法)。

9.5 🎯【Stack vs 100% Stack 觸發判斷】(CRITICAL — 常被誤解,預設 raw count!)

    ⚠️【預設行為】「stacked bar」「堆疊圖」「堆疊長條圖」**單獨出現,一律走 raw count 堆疊**,
    每柱高度為各 sub-state 加總(不固定 100),Phase B **不要** normalize。

    ✅【觸發 100% 歸一化的「強信號詞」】(必須明示其中之一才能 100% normalize):
    - 「**100%**」/「**100 %**」
    - 「**百分比**」+「堆疊 / stacked」
    - 「**比例**」+「堆疊 / stacked」(例如「比例堆疊」「比例分佈」)
    - 「**占比分佈**」(占比 + 分佈 連在一起明示)
    - 「**percentage stack**」/「**100% stacked bar**」
    - 「每柱加總 = 100」/ 「占比加總 100%」之類明示算式

    🚫【弱信號詞 不觸發 100% normalize】(這些詞單獨出現 → 走 raw count stack):
    - 「占比」單獨(可能只是想看相對量,不一定要 normalize 到 100)
    - 「組成」「結構」「分佈」單獨(意思是「堆疊呈現多 sub-state」,沒要 100%)
    - 「stacked bar 看 X 占比」(雖含占比,但沒明示百分比/100/比例)→ 走 raw count

    ⚠️【判斷練習】
    | Query | 走法 | 為什麼 |
    |---|---|---|
    | 「各公司 PAY 跟 RTN 數量,用 stacked bar」 | Raw count stack | 沒提百分比 |
    | 「stacked bar 看類別占比」 | Raw count stack | 「占比」單獨,沒明示 100% |
    | 「各公司類別 stacked bar,占比加總 100%」 | 100% normalize | 明示「加總 100%」 |
    | 「各公司四類別的占比分佈,100% stacked」 | 100% normalize | 「占比分佈」+「100%」雙明示 |
    | 「畫一張 100% stacked bar」 | 100% normalize | 明示「100%」|
    | 「比例堆疊圖看公司類別分佈」 | 100% normalize | 「比例」+「堆疊」明示 |

    當判定要 100% normalize,意思是「每組內各 sub-state 加總應為 100」(per-group 歸一化),**不是**直接顯示 raw count 再加 % 符號。

    🎯 **首選做法:Long format + multi-key groupby + transform**(避免 pivot→melt 繞道):
    ```python
    # 假設要算「每 <x_dim> 內各 <series_dim> 占比 (加總=100)」
    counts = raw_df.groupby(['<x_dim>', '<series_dim>']).size().reset_index(name='count')

    # transform 算每組 (<x_dim>) 總數 — 不需中介 _total 欄位
    counts['_total_per_group'] = counts.groupby('<x_dim>')['count'].transform('sum')
    counts['percentage'] = (counts['count'] / counts['_total_per_group'] * 100).round(2)

    Q = counts[['<x_dim>', '<series_dim>', 'percentage']]   # 純 long format,3 欄
    ```

    🚫 反例 — pivot→melt 繞道路徑,容易踩兩個坑:
    ```python
    # ❌ 坑 1:melt 後 var_name 欄位的「值」帶 _pct 後綴
    agg.melt(value_vars=['TST_pct', 'TSN_pct'], var_name='company_code', value_name='percentage')
    # 結果 Q['company_code'] 是 'TST_pct'/'TSN_pct',Phase C filter `==`TST` 找不到 → series.data=[]

    # ❌ 坑 2:agg 與 melt 後的 result 是兩個 DataFrame,drop 錯對象
    agg['_total'] = ...                       # _total 在 agg
    result = agg.melt(...)                     # result 是 melt 出的新 DF,沒 _total
    Q = result.drop(columns=['_total'])        # KeyError!
    ```

    若 query 要 wide-format Q(罕見),才需 pivot,但**完成後就保留 wide,不要再 melt**。
    Phase C 收到 long-format Q 時用 list comprehension 從 `Q['<series_dim>'].unique()` 動態產出 series(見規則 5.53)。

10. 🎯【保持 long / tidy format】(CRITICAL) Q 最終結果應為 long-form (tidy data):
    每列代表一個 observation,每欄是一個變數。
    - ✅ Long (推薦):`[dim_a, dim_b, value]` 三欄,row 為 dim_a × dim_b 的笛卡兒積。
    - ❌ Wide (除非使用者明說要表格):`pivot(index=dim_a, columns=dim_b)` 把 dim_b 的值散開成欄位名。
    為何?下游 ECharts / Plotly 在 long 格式下能直接用 `Q['col_name'].tolist()` 取值;
    wide 格式下欄位名變動態,Phase C 無法穩定引用。
    例外:純表格呈現 (LLM 後續決定走 `_use_table` fallback) 可保留 wide 以便人類閱讀。
    Heatmap 場景:**強制 long format**,因為 ECharts heatmap series.data 需要 `[[x_idx, y_idx, value], ...]`。

### 範例骨架 (展示流程,具體欄位以你 domain 為準):
```python
import pandas as pd
import numpy as np

Q = raw_df.copy()
# 1. 視需要建立布林輔助欄位以對應 KPI 公式中的條件
#    Q['is_<state>'] = (Q['<status_col>'] == '<value>')

# 2. groupby + agg 算出每組 KPI
agg = Q.groupby('<dimension_col>').agg(
    <metric_a>=('<col_a>', 'size'),           # 計數
    <metric_b>=('<bool_col>', 'sum'),          # 條件計數
    <reference>=('<reference_col>', 'first'),  # 維度級參考值(避免 self-merge)
).reset_index()

# 3. 衍生比率類 KPI
agg['<rate>'] = agg['<numerator>'] / agg['<denominator>']

Q = agg   # ⚠️ 絕對不能忘的終態指派
```
請只輸出 python code,不要前言不要說明。
"""
        user_msg = f"需求:{query}\n計畫:{plan_text}"
        user_msg += self._format_retry_hint(
            previous_code, previous_error,
            cheatsheet=PANDAS_ANTIPATTERN_CHEATSHEET,
        )
        raw = self._call_llm(
            [{"role": "system", "content": system_prompt},
             {"role": "user", "content": user_msg}],
            phase="preprocess",
        )
        return self._strip_code_fence(raw, lang="python")

    # --------------------------------------------------------
    # Phase C: Plotly 繪圖
    # --------------------------------------------------------
    def generate_plot_code(self, query, plan_text="", q_columns=None,
                            previous_code: str = "", previous_error: str = ""):
        cols_info = (
            f"`Q` 已備好,欄位: {q_columns}"
            if q_columns else "`Q` 欄位未知。"
        )
        system_prompt = f"""你是精通 Plotly 的資深前端工程師,負責【C. 視覺化繪圖】。
{cols_info}

### 實作守則 (CRITICAL RULES):
1. 🎯 產出名為 `fig` 的 Plotly Figure 物件。禁止 `fig.show()`、禁止 `streamlit` 相關呼叫。
2. 🚫【禁止二次計算】`Q` 已完美,絕對禁止在此再做過濾/聚合。
3. 🎯【企業視覺規範與 Table 鐵律】(CRITICAL FATAL)
   - 若要畫資料表格 (go.Table),請【完全照抄】以下語法,
     絕對禁止使用不存在的 `textfont` 屬性:
     ```
     fig = go.Figure(data=[go.Table(
         header=dict(values=list(Q.columns), fill_color='#2c3e50',
                     font=dict(color='white')),
         cells=dict(values=[Q[col] for col in Q.columns], fill_color='#f8f9fa',
                    font=dict(color='#2c3e50'))
     )])
     fig.update_layout(title="<你的標題>")
     ```
   - ⚠️ 若資料超過 500 筆,畫 Table 前請務必加上 `Q = Q.head(500)`。
4. 🚫【防幻覺】(CRITICAL FATAL) Plotly 沒有 `matchticks` 屬性。
   如需同步 Y 軸請用 `matches='y'`。
5. 🎨【格式建議】比率類 y 軸請用 `tickformat='.1%'`;
   比較多家公司時,優先 bar / grouped bar / stacked bar,禁止 pie chart。
6. 📦【import】請只 import 真正用到的模組,例如:
   `import plotly.express as px` 或 `import plotly.graph_objects as go`。
請只輸出 python code,不要前言不要說明。
"""
        user_msg = f"需求:{query}\n計畫:{plan_text}"
        user_msg += self._format_retry_hint(previous_code, previous_error)
        raw = self._call_llm(
            [{"role": "system", "content": system_prompt},
             {"role": "user", "content": user_msg}],
            phase="plotly",
        )
        return self._strip_code_fence(raw, lang="python")

    # --------------------------------------------------------
    # Phase C (alt): ECharts option dict
    # --------------------------------------------------------
    def generate_echarts_option(self, query, plan_text="", q_columns=None,
                                 previous_code: str = "", previous_error: str = ""):
        """產生 ECharts 5 option Python dict literal,變數名 `option`。"""
        cols_info = (
            f"`Q` 實際欄位 (THE ONLY SOURCE OF TRUTH): {q_columns}\n"
            "⚠️ 上面這份 q_columns 是 Phase B 實際產出的欄位。\n"
            "⚠️ 不論下方 Domain Knowledge 提到什麼 KPI 名稱,**你只能使用 q_columns 中的欄位**。\n"
            "⚠️ 若你想引用的 KPI 在 q_columns 中沒對應欄位,改用最接近的、或直接放棄該指標。"
            if q_columns else "`Q` 欄位未知。"
        )
        system_prompt = f"""你是精通 Apache ECharts 5 的資深前端工程師,負責【C. 視覺化繪圖 (ECharts)】。
{cols_info}

### 任務說明
請輸出名為 `option` 的 Python dict literal,內容符合 ECharts 5 option 規範。
app 端會把這個 dict 直接餵給 `st_echarts(option, height="520px")` 渲染。

### 實作守則 (CRITICAL RULES):
0. 🚨【欄位名鎖死】(CRITICAL FATAL — 最常犯錯) 你只能用上方 `Q 實際欄位` 中列出的欄位名。
   即使 Domain Knowledge 提到某個 KPI(如 `total_applications`、`average_return_rate`),
   只要該名稱不在 `q_columns` 中,**絕對禁止引用**,會炸 KeyError。
   寫前在心裡跑一遍:每個 `Q['<name>']` 的 `<name>` 都要在 q_columns 中。

1. 🎯【變數產出】(CRITICAL FATAL) 最外層必須宣告 `option` (dict)。
   禁止包在 function/class 內;禁止 `print`;不要再 import 任何套件。
2. 🚫【禁止函式 formatter】(CRITICAL) ECharts 透過 JSON 傳遞,formatter 只能用字串模板
   (如 '{{value}}%'、'{{b}}: {{c}}'),不能放 Python lambda / def。
3. 🚫【禁止二次處理 Q】`Q` 已完美,只允許 `Q['col'].tolist()`、`Q['col'].round(N).tolist()`、
   `(Q['col'] * 100).round(2).tolist()` 這類取值,不可再 groupby/filter。

3.5 🔢【數值精度鐵律】(CRITICAL — 影響使用者觀感) series.data 傳入 ECharts 前
    必須 `.round(N)`,否則 label 會顯示 16 位浮點(如 `1.3076923076923077`)。
    依數量級判斷精度:
    - **整數類**(count、人數、件數): 維持 int,不需 round
      ```python
      "data": Q['total_applications'].tolist()
      ```
    - **比率類**(0-1):先 `* 100` 再 `.round(2)`,顯示百分比
      ```python
      "data": (Q['return_rate'] * 100).round(2).tolist()
      ```
    - **平均/人均/連續類**(0-100 範圍): `.round(2)`
      ```python
      "data": Q['per_capita'].round(2).tolist()
      ```
    - **金額類大數**: `.round(0).astype(int)` 或 `.round(2)`
      ```python
      "data": Q['revenue'].round(0).astype(int).tolist()
      ```
    若 series.label 顯示數值,務必在 `data` 階段就 round,而不是只設 `formatter`
    (formatter 僅控制顯示樣式,不改變 tooltip 與 hover 中的原始值)。
4. 🎯【必備 keys】title、tooltip、xAxis、yAxis、series。bar/line 類請用
   `tooltip: {{"trigger": "axis", "axisPointer": {{"type": "cross"}}}}`。
5. 🎨【視覺規範】
   - 多家公司比較禁止 pie chart,優先 bar / grouped bar / stacked bar。
   - 比率欄位 (0-1) 請先 `* 100`,並設 `yAxis.axisLabel.formatter = "{{value}}%"`。
   - 數量級差很大時使用雙 yAxis (left=count,right=rate)。
   - 類別數 > 20 時加 `dataZoom: [{{"type": "inside"}}, {{"type": "slider"}}]`。

5.3 ⚠️【formatter vs data 語義分離】(CRITICAL — 常見誤用)
   `axisLabel.formatter = "{{value}}%"` **只是把 % 符號加在 label 顯示上,不會把資料 / 100**!
   - ❌ 錯誤做法:data 是 raw count (例如 28000),formatter `{{value}}%` → y 軸顯示 "28000%"
   - ✅ 正解:**data 必須先在 Phase B 轉成 0-100 範圍**,formatter `{{value}}%` 才會顯示正確「28%」
   - 對於「100% stacked」,Phase B 應已 normalize per-group 加總=100;
     Phase C 設 `yAxis: {{max: 100, axisLabel: {{formatter: "{{value}}%"}}}}` 保證軸不超出 100。
5.5 📚【Stack 觸發判斷】(CRITICAL) 當查詢符合以下任一條件,
    **兩個 (或以上) 同類 series 必須加 `"stack": "<相同字串>"` 變成 stacked bar**:
    (a) 互斥狀態對比:任何「A vs B」且 A、B 為 mutually exclusive 的成對狀態
        (通過/拒絕、成功/失敗、是/否、有/無、男/女 等)。判斷依據是
        metadata 中 allowed_values 是否描述了互斥的取值集合。
    (b) 整體量語意關鍵字:「總量」、「合計」、「堆疊」、「累積」、「整體」、「工作量」、「全貌」。
    (c) 隱含結構占比:「哪個 X 量最大」、「占比」、「組成」、「分佈」、「結構」。
    (d) 查詢明說「stacked bar」、「堆疊長條圖」、「100% stacked」、「堆疊圖」。
    違反 = grouped bar(柱子並排)= 看不出每組總量 = 視覺溝通失敗。
    反例(不要 stack):使用者明說「並排比較」、「分別看」、「對照」、「橫向對比」時。

5.53 🚫【Series 動態產出鐵律 — 原理性】(CRITICAL FATAL)

    **原則:series 結構必須是 Q 的「投影」,不能是憑空寫出的物件。**

    ❌ 禁止模式 (Static / Hardcoded Anti-patterns):
    - `series = [{{"name": "<任意字串字面值>", ...}}, {{"name": ...}}, ...]`(手工列出每個 series 物件)
    - `series[].name = "<任意字串字面值>"`(name 是 literal,不是從 Q 取)
    - `Q[Q['<col>'] == "<字串字面值>"]`(filter 條件用 literal 字串)
    - 任何「**不從 `Q` 衍生**」的 series 元素

    ✅ 唯一允許模式 (Dynamic / Projection):
    ```python
    series_keys = Q['<series_dim>'].unique().tolist()   # ← 真實值唯一來源
    "series": [
        {{
            "name": str(k),                              # ← 真實值
            "type": "bar",
            "stack": "<相同字串>",
            "data": (
                Q[Q['<series_dim>'] == k]['<value_col>'] * <factor>
            ).round(<n>).tolist(),                       # ← 用 k 真實值 filter
        }}
        for k in series_keys                             # ← 迭代真實值
    ]
    ```

    或用 pivot_table 預組(更穩):
    ```python
    pivot = (Q.pivot_table(index='<x_dim>', columns='<series_dim>',
                            values='<value_col>', aggfunc='sum')
              .reindex(<x_order>).fillna(0))
    "series": [
        {{"name": str(k), "type": "bar", "stack": "pct",
         "data": (pivot[k] * <factor>).round(<n>).tolist()}}
        for k in pivot.columns
    ]
    ```

    ⭐ 識別法則:**任何不引用 `Q[...]` 或不從 `for` 迴圈變數取 name/data 的 series 物件,皆為違規。**

    ⭐ 自我檢查 (寫前必跑):
    1. 我的 `series[].name` 是來自 `Q['<col>'].unique()` 嗎? 還是我自己寫了個字串?
    2. 我的 filter 條件中的比對值,是 for 迴圈變數嗎? 還是 literal?
    3. 我能改 Q 中的某個 unique value(例如 metadata 換 domain),series 會自動跟著變嗎?(若不會則違規)

5.54 ⚠️【維度 vs Series 中文語意辨識】(CRITICAL — 容易方向錯)
    使用者的語句決定哪個維度當 xAxis,哪個當 series:

    | 中文語句模式 | xAxis | series |
    |---|---|---|
    | 「**依據 X** 畫多條 bar,每條 bar 中呈現 Y 的占比」 | X | Y |
    | 「**各 X** 的 Y 占比 stacked」 | X | Y |
    | 「按 X 分組,看 Y 的分佈」 | X | Y |
    | 「**每個 X** 內部的 Y 結構」 | X | Y |

    口訣:**「依據 / 各 / 按 / 每個」後面接的維度 = xAxis**。
    Series 是「組合進每個 x 柱子內部那層」的維度。

    ❌ 反例(transposed,常見錯):
    使用者問「依據 company_code 畫 bar,每條 bar 中呈現 category 占比」
    → LLM 寫 xAxis=[各 category], series=[各 company] (反了!)
    → 結果:每個類別柱裡堆疊公司,而非每家公司柱裡堆疊類別

    ✅ 正解:xAxis 是「依據」後面的那個維度(此例為 company_code)。

5.55 ⚠️【Stacked Bar 強制 Pivot 鐵律】(CRITICAL FATAL — 不論 Q 是 long 或 wide,Phase C 一律先 pivot)

    **問題:** 直接從 long format Q 用 `Q[Q['col']==X]` filter 太脆弱:
    (a) 缺漏組合 → data 長度不對齊 → bar 不齊全
    (b) Phase B 如果用 melt,var_name 欄位值會帶 `_pct` / `_count` 後綴 → filter 對不上 → series.data=[]
    (c) hardcode 中文 placeholder filter → 永遠找不到

    **唯一安全做法:** Phase C 開頭一律先把 Q pivot 成 wide,xAxis 從 index 取、series 從 columns 取。

    ✅ 強制模板:
    ```python
    # 識別 (x_dim, series_dim, value_col) — 看你想呈現的方向
    x_dim       = '<xAxis 維度欄名>'         # e.g. company_code
    series_dim  = '<series 維度欄名>'        # e.g. application_category
    value_col   = '<數值欄名>'              # e.g. percentage / count

    # 強制 pivot(即使 Q 已是 long 或 wide 都這樣做)
    if value_col in Q.columns:
        # Q 是 long format,做 pivot
        pivot = (Q.pivot_table(index=x_dim, columns=series_dim,
                               values=value_col, aggfunc='sum')
                  .fillna(0))
    else:
        # Q 已是 wide,把 x_dim 設為 index
        pivot = Q.set_index(x_dim).fillna(0)

    # xAxis 與 series 都從 pivot 取
    "xAxis": {{"type": "category", "data": pivot.index.astype(str).tolist()}},
    "series": [
        {{"name": str(col), "type": "bar", "stack": "pct",
          "data": pivot[col].round(2).tolist()}}
        for col in pivot.columns
    ],
    ```

    **絕對禁忌:**
    - ❌ `Q[Q['col'] == "<任意字面值>"]` — 不論 `<任意字面值>` 是中文還是英文
    - ❌ 從 Q 沒做 pivot 直接 filter 給 series.data
    - ❌ series.name 與 Q['col'] 的實際值不一致(常見於 melt 後)

    識別法則:**series.data 必須來自 `pivot[col]`,不能來自 `Q[filter]`。**

5.65 ↔️【橫向(水平)Bar 觸發判斷】(CRITICAL — 容易忘)
    當 query 含「**水平**」、「**橫向**」、「**橫條**」、「**horizontal**」、「sideways」等詞時,
    必須把 xAxis 與 yAxis 的角色互換:

    | 預設(縱向) | 觸發橫向後 |
    |---|---|
    | `xAxis.type = "category"` + `xAxis.data = [類別 list]` | `xAxis.type = "value"`,xAxis 不放 data |
    | `yAxis.type = "value"` + `yAxis.axisLabel.formatter` | `yAxis.type = "category"` + `yAxis.data = [類別 list]` |
    | yAxis 上的 max/formatter 處理數值 | xAxis 上的 max/formatter 處理數值 |

    **series.data 的「值順序」完全不變** — ECharts 看到哪個軸是 category 自己會旋轉繪製。

    ⚠️【橫向也必須走 rule 5.55 強制 pivot】(CRITICAL FATAL)
    橫向 stacked bar **絕對禁止**用 `Q[Q['col']==k]` filter long format Q 取 series.data —
    跟縱向一樣容易缺漏組合導致 `[15,14,15,15]` 長度不一致。
    **唯一安全做法**:照 rule 5.55 先 pivot,只是把 pivot 結果掛在 yAxis(類別軸)而非 xAxis。

    ✅ 範例(橫向 100% stacked,完整流程):
    ```python
    # Step 1 · 跟 rule 5.55 一樣強制 pivot
    y_dim       = '<類別軸維度欄名>'        # e.g. company_code (要顯示為 category 軸的)
    series_dim  = '<series 維度欄名>'       # e.g. application_category
    value_col   = '<數值欄名>'             # e.g. percentage / count

    if value_col in Q.columns:
        pivot = (Q.pivot_table(index=y_dim, columns=series_dim,
                               values=value_col, aggfunc='sum')
                  .fillna(0))
    else:
        pivot = Q.set_index(y_dim).fillna(0)

    # Step 2 · 軸角色互換,yAxis 取 pivot.index
    option = {{
        "xAxis": {{"type": "value", "max": 100,
                   "axisLabel": {{"formatter": "{{value}}%"}}}},                  # 數值在 x
        "yAxis": {{"type": "category",
                   "data": pivot.index.astype(str).tolist()}},                  # 類別在 y (取自 pivot.index!)
        "series": [
            {{"name": str(col), "type": "bar", "stack": "pct",
              "data": pivot[col].round(2).tolist()}}                            # ⚠️ 取自 pivot[col],不要再 *100
            for col in pivot.columns
        ],
    }}
    ```

    **絕對禁忌(橫向版):**
    - ❌ `"yAxis": {{"data": Q['col'].unique().tolist()}}` — 用 pivot.index 才能跟 series.data 對齊
    - ❌ `"data": Q[Q['col']==k]['value'].tolist()` — 長度不對齊
    - ❌ 在 Phase B 已 normalize 為 0-100 後又在 Phase C `* 100`(會變 0-10000)

    若 query 沒明確指定方向,維持預設縱向 (xAxis=category, yAxis=value)。

5.8 📏【偏態分佈 auto log scale】(CRITICAL — 長尾資料線性軸不可讀)

    當數值跨越 **>100 倍**(例如 TST 80,919 vs TSK 2)時,線性 yAxis 會把小值全壓在零線上、
    看不見差異。請**主動判斷並提議 log scale**:

    ✅ 觸發條件(三項任一):
    - `max(series.data) / min(positive values) > 100`
    - 查詢含「員工總數 / 申請量 / 營收」等已知偏態指標
    - 同時比較大型 + 小型公司 / 部門(規模差異大)

    ✅ 解法(優先序):
    1. **log scale**(首選):`yAxis.type = "log"`,大小公司都看得到
       ⚠️ log scale 需要所有值 > 0(任何 0 或負值會被忽略 / 報錯)
       ⚠️ 若雙軸,只把 bar 那軸改 log;line(率/比例)那軸保 linear
    2. **改 horizontal sorted bar**:`xAxis = value, yAxis = category`,
       並用 `data.sort_values(ascending=True)` 讓 TST 在頂端,清楚看出排名
    3. **拆 Top-N / Bottom-N 兩張圖**:極端偏態時(差距>1000 倍),split view 比 log 更直觀

    ✅ 範例(線性 → log):
    ```python
    "yAxis": {{
        "type": "log",           # ⭐ 從 "value" 改 "log"
        "name": "員工總數",
        "axisLabel": {{"formatter": "{{value}}"}}
    }}
    ```

    雙軸範例:
    ```python
    "yAxis": [
        {{"type": "log", "name": "員工總數"}},       # ⭐ bar 那軸 log
        {{"type": "value", "name": "退單率",
          "axisLabel": {{"formatter": "{{value}}%"}}}}   # 率仍 linear (0-100)
    ]
    ```

5.7H 🔥【Heatmap 完整配方】(CRITICAL FATAL — 容易踩 3 個雷)

    當 query 含「**熱力圖**」、「**heatmap**」、「**熱度**」+「分佈/比較/矩陣」時,
    走 heatmap。**必須完整避開下面 3 個雷,否則畫面空白**:

    ⚠️【雷 1 · numpy 型別 JSON 序列化失敗】(最常見死法)
    `Q['col'].max()`、`row['col']` 都是 numpy.int64 / float64,JSON 序列化會掛掉。
    **每個值都必須顯式 cast 成 Python 原生型別**:
    ```python
    "data": [
        [str(row["<x_dim>"]),                  # ✅ str
         str(row["<y_dim>"]),                  # ✅ str
         float(row["<value_col>"])]            # ✅ float / int
        for _, row in Q.iterrows()
    ],
    "visualMap": {{
        "min": float(Q["<value_col>"].min()),  # ✅ float()
        "max": float(Q["<value_col>"].max()),  # ✅ float()
        ...
    }}
    ```

    ⚠️【雷 2 · tooltip.trigger 必須是 "item"】
    - ❌ `"trigger": "cell"`(非法值,tooltip 失效)
    - ❌ `"trigger": "axis"`(heatmap 不適用)
    - ✅ `"trigger": "item"`(正解)

    ⚠️【雷 3 · visualMap 必須帶 inRange.color】
    若不指定 inRange.color,部分 ECharts 版本會用很淺的預設色,
    cell 顏色差異看不出來。**強制給漸層**:
    ```python
    "visualMap": {{
        "min": ..., "max": ...,
        "calculable": True,
        "orient": "horizontal", "left": "center", "bottom": 20,
        "inRange": {{
            "color": ["#e6f1fb", "#85b7eb", "#185fa5", "#0c447c"]   # 淺→深藍漸層
        }}
    }}
    ```

    ✅【完整配方】:
    ```python
    x_values = Q["<x_dim>"].unique().tolist()
    y_values = Q["<y_dim>"].unique().tolist()
    option = {{
        "title": {{"text": "..."}},
        "tooltip": {{"trigger": "item"}},                   # ⚠️ 必須 "item"
        "grid": {{"left": 80, "right": 80, "top": 60, "bottom": 80}},
        "xAxis": {{"type": "category", "data": [str(v) for v in x_values],
                   "splitArea": {{"show": True}}}},
        "yAxis": {{"type": "category", "data": [str(v) for v in y_values],
                   "splitArea": {{"show": True}}}},
        "visualMap": {{
            "min": float(Q["<value_col>"].min()),
            "max": float(Q["<value_col>"].max()),
            "calculable": True,
            "orient": "horizontal", "left": "center", "bottom": 20,
            "inRange": {{"color": ["#e6f1fb", "#85b7eb", "#185fa5", "#0c447c"]}}
        }},
        "series": [{{
            "name": "<value 中文名>",
            "type": "heatmap",
            "data": [
                [str(row["<x_dim>"]), str(row["<y_dim>"]), float(row["<value_col>"])]
                for _, row in Q.iterrows()
            ],
            "label": {{"show": True, "formatter": "{{c}}"}},
            "emphasis": {{"itemStyle": {{"shadowBlur": 10, "shadowColor": "rgba(0,0,0,0.5)"}}}}
        }}]
    }}
    ```

    🚫 反例(會空白):
    - ❌ `"max": Q["count"].max()`(numpy.int64,JSON 序列化掛)
    - ❌ `[row["a"], row["b"], row["c"]]`(numpy 型別未 cast)
    - ❌ `"trigger": "cell"`
    - ❌ visualMap 沒 inRange.color

5.58 🔢【百分比欄位禁止重覆 *100】(CRITICAL FATAL — 容易犯)
    Phase B 產生的百分比欄位(名稱含 `_pct` / `percent` / `percentage` / `rate` 之類,
    或上游已 `* 100`)一律是 **0-100 範圍**。Phase C **絕對禁止**再 `* 100`:
    - ❌ `"data": (pivot[col] * 100).round(2).tolist()`  → 會變 0-10000
    - ❌ `"data": (Q['percentage'] * 100).tolist()`     → 同上
    - ✅ `"data": pivot[col].round(2).tolist()`         → 直接用
    若 Phase B 給的是 0-1 比例(欄名通常含 `ratio` / `frac` / `_share`),才需要 `* 100`;
    遇到不確定時,從 `raw_df_sample` 看數值範圍判斷,不要憑想像。

5.6 📚【100% Stacked Bar 完整配方】(配合 Phase B 9.5 規則使用)

    ⚠️【預設】「stacked bar」「堆疊圖」**單獨出現,一律走 raw count**,
    yAxis 不鎖 100、formatter 不加 %,讓 ECharts 自動算高度。

    ✅【觸發 100% 配方的強信號詞】(必須明示其中之一):
    - 「**100%**」/「**100 %**」/「**100% stacked**」
    - 「**百分比**」+「堆疊 / stacked」
    - 「**比例**」+「堆疊 / stacked」
    - 「**占比分佈**」/「**比例分佈**」(連字明示)
    - 「**percentage stack**」/「每柱加總 100」

    🚫【弱信號詞,不觸發 100%】(依舊走 raw count stack):
    - 「占比」單獨
    - 「組成 / 結構 / 分佈」單獨
    - 「stacked bar 看 X 占比」(沒明示百分比/100/比例)

    ⚠️ 100% 配方僅在強信號下使用:
    - Phase B 已 per-group normalize 後 Q 的 *_pct 欄位是 0-100 範圍
    - Phase C 需設定:
      ```python
      "yAxis": {{
          "type": "value",
          "max": 100,                                      # ⭐ 鎖住 0-100,不讓 ECharts 自動拉到 10,000
          "axisLabel": {{"formatter": "{{value}}%"}}
      }},
      "series": [
          {{"name": "<state_a>", "type": "bar", "stack": "pct",
            "data": Q['<state_a>_pct'].tolist()}},
          {{"name": "<state_b>", "type": "bar", "stack": "pct",
            "data": Q['<state_b>_pct'].tolist()}},
          {{"name": "<state_c>", "type": "bar", "stack": "pct",
            "data": Q['<state_c>_pct'].tolist()}},
      ],
      ```
    - 所有 series 同名 `stack`,每柱加總 = 100%。

5.7 🎨【預設樣式鐵律 — label + legend 自動帶上】(CRITICAL — 使用者很少明示但很在意)

    除非使用者**明說**「不要 label」「不要 legend」「精簡版」「乾淨」「minimal」,
    Phase C 預設必須讓圖一打開就帶數值與圖例,不必使用者每次都要求。

    ⭐ Bar / Line / Scatter:
    - **每筆 series 一定加 label**:
      ```python
      "label": {{"show": True, "position": "top", "formatter": "{{c}}"}}
      ```
      - 縱向 stacked bar → position 改 `"inside"`
      - 橫向 bar(yAxis 為 category)→ position 改 `"right"`
      - 100% stacked / 百分比軸 → formatter 改 `"{{c}}%"`
      - 大數字(>1000)→ formatter 改 `"{{c}}"` + 啟用 `valueAnimation`
    - **option 一定加 legend**:
      ```python
      "legend": {{"show": True, "top": 30}}
      ```

    ⭐ Pie / Donut:
    - 每筆 series 帶完整 label(b=名稱、c=值、d=占比):
      ```python
      "label": {{"show": True, "formatter": "{{b}}: {{c}} ({{d}}%)"}},
      "labelLine": {{"show": True}}
      ```
    - legend 垂直擺右:
      ```python
      "legend": {{"orient": "vertical", "right": 10, "top": "center"}}
      ```

    ⭐ Heatmap:
    - cell 上加 label(若 visualMap 已著色):
      ```python
      "label": {{"show": True, "formatter": "{{c}}"}}
      ```

    ⭐ 智慧抑制(避免擁擠,自動判斷):
    - **xAxis.data 長度 > 15**(縱向 bar / line)→ `label.show = False`(legend 仍保留)
    - **yAxis.data 長度 > 15**(橫向 bar)→ `label.show = False`
    - **只有 1 個 series**(單指標 bar)→ legend 可省(沒對照意義),label 保留
    - **stacked bar 含 4+ series 且每柱數值多**→ 仍加 label 但 position 改 `"inside"` 並用 `fontSize: 10`

    ⭐ 反例(嚴禁):
    - ❌ 全圖無 label,使用者要去 tooltip 才看得到值
    - ❌ 多 series 圖無 legend,使用者分不出哪個顏色代表什麼
    - ❌ Pie 只給 legend 不給 label,使用者得 hover 才看百分比

6. 🎁【色盤】(CRITICAL — 預設 20 色,避免 series 多時顏色重複)
   ECharts 預設色盤只有 6 色,series > 6 時會循環造成「TST 跟 TDC 都紅色」這種誤判。
   **一律使用下方 20 色擴充色盤**,即使你預期 series 不多也要寫滿(下游若 series 更多,
   app 端會再自動擴充):
   ```python
   "color": [
       "#5470c6", "#91cc75", "#fac858", "#ee6666", "#73c0de",
       "#3ba272", "#fc8452", "#9a60b4", "#ea7ccc", "#5b9bd5",
       "#a5a5a5", "#ffc000", "#7b78de", "#27a39d", "#e15759",
       "#f28e2c", "#76b7b2", "#59a14f", "#edc949", "#b07aa1"
   ]
   ```
7. 📐【grid 留白】`grid: {{"left": 60, "right": 60, "top": 60, "bottom": 40}}` 起手。
   ⚠️ 若有 legend 在 `top: 30`,grid.top 改 `70` 避免重疊。
8. 📋【表格 fallback + KPI 卡片】(慎重觸發,反例優先)

   ✅ 何時應走 `_use_table`(必須同時符合):
   - 使用者**明確要求**「表格 / 匯總 / KPI overview / dashboard / 執行摘要 / 一覽 / 摘要報表」
   - 且 Q 至少有 4 個以上獨立 KPI 欄位(欄位多到畫圖會擠)

   🚫 何時【嚴禁】走 `_use_table`(即使 Q 有多欄,也必須畫圖):
   - 查詢含「**比較**」、「對比」、「vs」(這是視覺化任務,不是匯總任務)
   - 查詢含「**最多**」、「**最少**」、「**最高**」、「**最低**」、「**排名**」、「**Top N**」
     (這類查詢需要 sorted bar 才能一眼看出極值)
   - 查詢已明確指定圖型(「畫長條圖」、「畫熱力圖」、「scatter」等)
   - 查詢含「**分佈**」、「**占比**」、「**組成**」(這類用 stacked bar / pie / heatmap 更直觀)

   【決策範例】
   - 「畫 KPI 一覽表」→ 走 _use_table ✅
   - 「dashboard 顯示總申請與完成率」→ 走 _use_table ✅
   - 「各公司核准與退件比較,哪家退件最多」→ **畫 sorted stacked bar**,**不走 _use_table** ❌
   - 「比較 AI 與人工的退件率」→ 畫 bar / grouped bar,不走 _use_table ❌
   - 「全公司 KPI 完整一覽:申請、完成、退件、AI 率、員工率」→ 走 _use_table ✅

   若決定走 _use_table,以下是精美表格樣式 (具體欄位請對應你 domain 的 Q):

   ⚠️【數學鐵律 - 比率類 KPI 必看】(CRITICAL FATAL)
   - 比率 / 平均率類 KPI **絕對禁止用 `Q['rate_col'].mean()`!**
     理由:這是「簡單平均率」(每組 rate 加起來除以組數),小組 (低樣本) 會把大組的真實率拉偏。
   - 正確做法:**用加權平均** = `sum(分子) / sum(分母)`
     ```python
     # 例:平均退單率
     total_rate = Q['return_count'].sum() / Q['completed_count'].sum()
     # 例:整體完成率
     total_rate = Q['completed_count'].sum() / Q['total_applications'].sum()
     ```

   ⚠️【總量類 KPI - 防 TOTAL 列雙倍計算】
   - 若 Q 含有 TOTAL / SUMMARY / 合計 等聚合摘要列 (理論上 Phase B 不該加,但保險起見),
     `_kpi_cards` 計算前要先過濾掉:
     ```python
     # 防禦寫法
     _df = Q[~Q['<dim_col>'].astype(str).str.upper().isin(['TOTAL', 'SUMMARY', 'GRAND TOTAL', '合計', '總計'])]
     total = int(_df['<count_col>'].sum())
     ```

   ### 完整 KPI 卡片範例
   ```python
   option = {{
       "_use_table": True,
       "_kpi_cards": [               # 表格上方的 st.metric 卡片 (最多 4 張)
           # 總量類:用 sum,必要時先過濾 TOTAL 列
           {{"label": "<總量類 KPI>",
             "value": f"{{int(Q['<count_col>'].sum()):,}}"}},
           # 比率類:用加權平均 (分子 sum / 分母 sum)
           {{"label": "<品質比率>",
             "value": f"{{(Q['<numerator>'].sum() / Q['<denominator>'].sum() * 100):.2f}}%"}},
           {{"label": "<效率比率>",
             "value": f"{{(Q['<ai_count>'].sum() / Q['<base_count>'].sum() * 100):.1f}}%"}},
           {{"label": "<維度計數>", "value": f"{{len(Q)}}"}},
       ],
       "_table_caption": f"共 {{len(Q)}} 筆"
   }}
   ```
   - app 會自動把名字含 `rate`/`率` 的欄位渲染成漸層進度條 (ProgressColumn),
     大整數加千分位逗號 — 你不需要再對 Q 做格式轉換。
   - `_kpi_cards` 的 value 請用 f-string 在 exec 階段即時運算 Q,
     不要硬編入魔法數字。每張卡 label 控制在 8 字以內。
   - 卡片數建議 3-4 張,涵蓋「總量、品質指標、效率指標」三維度。

### 套用此 domain 的圖表範例 (由 metadata.charting_guidance 自動產生,以實際欄位名為準):
{{ECHARTS_FEW_SHOT}}

請只輸出 python code,不要前言不要說明。
"""
        # 注入此 service 實例對應 domain 的 few-shot
        system_prompt = system_prompt.replace("{{ECHARTS_FEW_SHOT}}", self.echarts_few_shot)
        user_msg = f"需求:{query}\n計畫:{plan_text}"
        user_msg += self._format_retry_hint(previous_code, previous_error)
        raw = self._call_llm(
            [{"role": "system", "content": system_prompt},
             {"role": "user", "content": user_msg}],
            phase="echarts",
        )
        return self._strip_code_fence(raw, lang="python")

    # --------------------------------------------------------
    # Phase D: 商業洞察
    # --------------------------------------------------------
    # --------------------------------------------------------
    # Pre-Phase 0: Meta response (intent != analysis 時的回應)
    # 所有 generator 都是純 metadata 推理,不打 LLM,零延遲。
    # --------------------------------------------------------
    def _sample_questions(self, n: int = 5) -> list[str]:
        """從 metadata 抽出範例問題。"""
        biz = self.task_metadata.get("business_context", {})
        questions = biz.get("main_business_questions") or []
        if questions:
            return questions[:n]
        # Fallback:從 KPI 自動合成
        return [
            f"分析 {kpi['name']}"
            for kpi in list(self.task_metadata.get("kpi_definitions", {}).values())[:n]
        ]

    def generate_intro_response(self) -> str:
        """產品能力介紹。"""
        md = self.task_metadata
        name = md.get("dataset_name") or md.get("dataset_id") or "Dataset"
        biz_desc = md.get("business_context", {}).get("business_description", "")
        sample_qs = self._sample_questions(5)

        out = [f"## 👋 你好,我是 **GenBI 分析助理**\n"]
        out.append(f"我目前載入的資料集是 **{name}**。\n")
        if biz_desc:
            out.append(f"> {biz_desc}\n")
        out.append("### 我能幫你做什麼?")
        out.append("- 📊 **視覺化分析** — bar / stacked / heatmap / scatter / 雙軸圖等")
        out.append("- 📋 **KPI 一覽** — dashboard 風格的執行摘要 + 漸層進度條")
        out.append("- 🧠 **商業洞察** — 自動產出觀察、建議、解讀注意事項")
        out.append("- 🛡️ **誠實拒絕** — 資料不夠時會說明而不亂編")
        out.append("")
        out.append("### 💡 試試這些問題:")
        for q in sample_qs:
            out.append(f"- {q}")
        out.append("")
        out.append("或直接輸入你想分析的任何問題,我會自動規劃流程。")
        return "\n".join(out)

    def generate_data_overview_response(self) -> str:
        """資料概覽 — schema + KPIs + 限制。"""
        md = self.task_metadata
        name = md.get("dataset_name") or md.get("dataset_id") or "Dataset"
        db = md.get("recommended_mongodb", {}).get("database", "—")

        out = [f"## 📋 **{name}** 資料概覽\n"]
        out.append(f"`MongoDB database: {db}`\n")

        # Collections
        out.append("### 📦 資料表")
        for coll_name, coll in md.get("collections", {}).items():
            desc = coll.get("description", "")
            grain = coll.get("grain", "")
            out.append(f"\n**`{coll_name}`** — {desc}")
            if grain:
                out.append(f"  · grain: _{grain}_")
            fields = list(coll.get("fields", {}).keys())
            if fields:
                fields_str = ", ".join(f"`{f}`" for f in fields)
                out.append(f"  · 欄位: {fields_str}")

        # KPIs
        kpis = md.get("kpi_definitions", {})
        if kpis:
            out.append("\n### 📐 可計算的 KPI")
            for kpi_key, kpi in kpis.items():
                line = f"- **{kpi['name']}** (`{kpi_key}`): {kpi['formula']}"
                if kpi.get("important_note"):
                    line += f"  ⚠️ _{kpi['important_note']}_"
                out.append(line)

        # Relationships
        rels = md.get("relationships", [])
        if rels:
            out.append("\n### 🔗 跨表關聯")
            for r in rels:
                out.append(f"- `{r['from_collection']}.{r['from_field']}` "
                           f"→ `{r['to_collection']}.{r['to_field']}` ({r['type']})")

        # Limitations
        lim = md.get("data_limitations", {})
        missing = lim.get("missing_dimensions", [])
        not_supp = lim.get("not_supported_analysis", [])
        if missing or not_supp:
            out.append("\n### ⚠️ 已知資料限制")
            for m in missing:
                out.append(f"- 缺欄位:{m}")
            for n in not_supp:
                out.append(f"- 不支援:{n}")

        return "\n".join(out)

    def generate_data_check_response(self, subject: str) -> str:
        """檢查 metadata 是否含 subject 相關資料。"""
        md = self.task_metadata
        if not subject:
            return ("🤔 不確定你想查什麼。試試 `你有什麼資料?` 看完整資料字典,"
                    "或直接輸入你想做的分析問題。")
        s = subject.lower()

        # 搜尋 fields
        found_fields: list[str] = []
        for coll_name, coll in md.get("collections", {}).items():
            for fname, fmeta in coll.get("fields", {}).items():
                if (s in fname.lower()
                    or s in fmeta.get("description", "").lower()):
                    found_fields.append(f"`{coll_name}.{fname}` — {fmeta.get('description', '')}")

        # 搜尋 KPIs
        found_kpis: list[str] = []
        for kpi_key, kpi in md.get("kpi_definitions", {}).items():
            if (s in kpi_key.lower()
                or s in kpi.get("name", "").lower()
                or s in kpi.get("formula", "").lower()):
                found_kpis.append(f"**{kpi['name']}** (`{kpi_key}`) — {kpi['formula']}")

        # 搜尋 limitations
        lim_match: list[str] = []
        for m in md.get("data_limitations", {}).get("missing_dimensions", []):
            if s in m.lower():
                lim_match.append(m)
        for n in md.get("data_limitations", {}).get("not_supported_analysis", []):
            if s in n.lower():
                lim_match.append(n)

        out: list[str] = []
        if found_fields or found_kpis:
            out.append(f"## ✅ 有「{subject}」相關資料\n")
            if found_fields:
                out.append("### 📋 相關欄位")
                out.extend(f"- {f}" for f in found_fields)
            if found_kpis:
                out.append("\n### 📐 相關 KPI")
                out.extend(f"- {k}" for k in found_kpis)
            out.append("\n💬 你可以直接問例如:「比較各組的 X 表現」或「列出 X 排名」。")
        elif lim_match:
            out.append(f"## ❌ 沒有「{subject}」")
            out.append("\n**資料限制明確標示:**")
            out.extend(f"- {m}" for m in lim_match)
            out.append("\n💡 建議:換個分析角度,例如改看靜態分布或 KPI 比較。")
        else:
            out.append(f"## 🤔 沒在 metadata 中找到「{subject}」的明確對應")
            out.append("")
            out.append("可能的情況:")
            out.append("- 你想找的東西**不在這個資料集**裡")
            out.append("- 或用了不同的名稱(例如「金額」可能對應 `amount` 或 `revenue`)")
            out.append("")
            out.append("試試:")
            out.append("- 輸入 `你有什麼資料?` 看完整 schema")
            out.append("- 直接輸入完整問題,我會嘗試分析或回應「資料不足」")

        return "\n".join(out)

    def generate_guidance_response(self) -> str:
        """新手引導 + 範例分類。"""
        sample_qs = self._sample_questions(8)

        out = ["## 🚀 怎麼開始?\n"]
        out.append("### 直接用自然語言問問題")
        out.append("我會自動處理 5 個階段:**Plan → Pipeline → Pandas → 視覺化 → 商業洞察**。")
        out.append("")
        out.append("### 常見問題類型")
        out.append("")
        out.append("**📊 比較與排名類:**")
        out.append("- 「比較各 X 的 Y」")
        out.append("- 「Top 5 / Bottom 5 的 Z」")
        out.append("- 「哪個 X 的 Y 最高/最低?」")
        out.append("")
        out.append("**📋 概覽 / Dashboard 類:**")
        out.append("- 「給我一份 X 的 dashboard」")
        out.append("- 「KPI 一覽表」")
        out.append("- 「執行摘要」")
        out.append("")
        out.append("**🔍 分佈與占比類:**")
        out.append("- 「畫熱力圖看 X × Y」")
        out.append("- 「按類別分組的占比」")
        out.append("- 「stacked bar 看 X 結構」")
        out.append("")
        if sample_qs:
            out.append("### 💡 來自這個資料集的範例:")
            for q in sample_qs:
                out.append(f"- {q}")
            out.append("")
        out.append("### 🛠 小技巧")
        out.append("- 圖表類型不滿意?直接重問:「改畫成 stacked bar」")
        out.append("- 想看資料字典?輸入「你有什麼資料?」")
        out.append("- Sidebar 可切換 ECharts ↔ Plotly 雙引擎")
        return "\n".join(out)

    def generate_out_of_scope_response(self, query: str = "") -> str:
        """超出資料集範圍的查詢 — 友善引導使用者回到能處理的範圍。"""
        name = self.task_metadata.get("dataset_name") or "this dataset"
        sample_qs = self._sample_questions(5)
        truncated = query.strip()[:80] if query else ""

        out = [f"## 🧭 你問的看起來不在 **{name}** 範圍內\n"]
        if truncated:
            out.append(f"> 你輸入的:_{truncated}_\n")
        out.append("我只能分析這個資料集涵蓋的內容。試試以下方向:\n")
        out.append("- 輸入 `你有什麼資料?` 看完整 schema 與 KPI")
        out.append("- 輸入 `你會做什麼?` 看完整能力介紹")
        out.append("- 輸入 `怎麼開始?` 看範例問題分類")
        if sample_qs:
            out.append("\n**📌 來自此資料集的具體範例問題:**")
            for q in sample_qs:
                out.append(f"- {q}")
        return "\n".join(out)

    def generate_greeting_response(self) -> str:
        """簡短歡迎。"""
        name = self.task_metadata.get("dataset_name") or "this dataset"
        return (
            f"👋 你好!我是 **GenBI 分析助理**,目前載入 **{name}**。\n\n"
            "你可以:\n"
            "- 輸入 `你會做什麼?` 看完整能力介紹\n"
            "- 輸入 `有什麼資料?` 看 schema 與 KPI\n"
            "- 輸入 `怎麼開始?` 看範例問題\n"
            "- 或直接輸入你想分析的問題 🚀"
        )

    def generate_meta_response(self, intent: str, subject: str = "", query: str = "") -> str:
        """根據 intent 派發到對應的 generator。純 metadata 推理,不打 LLM。"""
        if intent == "intro":
            return self.generate_intro_response()
        if intent == "data_overview":
            return self.generate_data_overview_response()
        if intent == "data_check":
            return self.generate_data_check_response(subject)
        if intent == "guidance":
            return self.generate_guidance_response()
        if intent == "greeting":
            return self.generate_greeting_response()
        if intent == "out_of_scope":
            return self.generate_out_of_scope_response(query or subject)
        return ""  # analysis 走原本 pipeline

    # --------------------------------------------------------
    # Phase D: 商業洞察
    # --------------------------------------------------------
    def generate_insight(self, query, plan_text="", q_preview_md: str = ""):
        """根據處理後的 Q 表 (markdown 預覽) 產出商業洞察文字。"""
        system_prompt = f"""你是資深商業分析師,負責撰寫【D. 商業洞察】。
請只以上方 Domain Knowledge 描述的範圍與限制為依據,**不可超出**。

{self.domain_knowledge}

### 通用分析原則:
- 絕對量與比率並陳,避免被小樣本誤導 (注意 metadata 中提及的小樣本警告)。
- 高比率但低量未必營運關鍵;高絕對量但中等比率可能更需要關注 (體量影響)。
- 衍生比率類 KPI 一定要依照 kpi_definitions 中的分母定義,不可自行擴大分母。
- 若 metadata 中某狀態被定義為「進行中 / 待處理」,該筆不可算入「完成」或「失敗」的分子。

### 禁忌 (cautions):
- 不可推論 data_limitations.missing_dimensions 中所列的任何維度
  (例如若 metadata 標示無時間欄位 → 禁止做趨勢 / 月度 / 季節性分析;
   若無金額欄位 → 禁止推論金額;若無部門欄位 → 禁止做部門比較)。
- 不可超出 data_limitations.not_supported_analysis 所列範圍。
- 不可推論非客觀內容 (例如使用者滿意度、員工意圖) 除非 metadata 中明示有相關欄位。

### 輸出格式 (Markdown,精簡):
**🔑 重點摘要** — 3-5 條 bullet,每條一句話、含具體數字。
**📌 觀察與建議** — 2-3 條 bullet,提出可行動的建議或追蹤指標。
**⚠️ 解讀注意事項** — 1-2 條 bullet,主動標註小樣本或資料限制。
"""
        user_msg = (
            f"使用者問題:{query}\n\n"
            f"分析計畫:\n{plan_text}\n\n"
            f"處理後資料表 (Q,前 30 列 markdown):\n{q_preview_md}\n\n"
            f"請產出商業洞察。"
        )
        try:
            return {
                "status": "success",
                "message": self._call_llm(
                    [{"role": "system", "content": system_prompt},
                     {"role": "user", "content": user_msg}],
                    temperature=0.3,
                    phase="insight",
                ),
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}
