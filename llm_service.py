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


def _build_column_clusters_block(metadata: dict) -> str:
    """
    v0.8.3 — 把 metadata.data_preprocessing_guidance.column_clusters 攤平成
    Phase A 看得懂的「同生共死欄位群」文字。

    若 metadata 沒定 column_clusters,回傳空字串(prompt 不會多印一個空 section)。
    """
    clusters = (
        (metadata.get("data_preprocessing_guidance") or {}).get("column_clusters") or []
    )
    if not clusters:
        return ""
    lines = []
    for c in clusters:
        cluster_id = c.get("cluster_id", "?")
        cols = c.get("cols") or []
        reason = c.get("reason", "")
        if not cols:
            continue
        cols_str = ", ".join(f"`{x}`" for x in cols)
        lines.append(f"  - **{cluster_id}**: {cols_str}")
        if reason:
            lines.append(f"    └ 理由:{reason}")
    return "\n".join(lines)


def build_domain_knowledge(metadata: dict) -> str:
    """組裝完整的 Domain Knowledge text block,完全由 metadata 驅動。"""
    db_name = metadata.get("recommended_mongodb", {}).get("database", "unknown")
    dataset_name = metadata.get("dataset_name") or metadata.get("dataset_id") or "Dataset"
    cluster_block = _build_column_clusters_block(metadata)
    cluster_section = (
        f"\n# 欄位 cluster (同生共死 — Phase A 引用其一必須全選)\n{cluster_block}\n"
        if cluster_block else ""
    )
    return f"""### {dataset_name} (database: {db_name})

# Collections & 欄位定義
{_build_schema_block(metadata)}

# 關聯關係 (跨表 join 用)
{_build_relationship_block(metadata)}

# KPI 公式 (鐵律 — 必須嚴格依此計算,禁止自創邏輯)
{_build_kpi_block(metadata)}

# 資料限制 (CRITICAL — 違反必須回覆「資料不足」)
{_build_limitation_block(metadata)}
{cluster_section}"""


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
    # 英文
    "dashboard", "overview", "summary", "kpi overview", "kpi cards",
    "executive summary", "exec summary",
    # 中文(含常見變體)
    "儀表板", "儀錶板", "管理儀表板", "kpi 儀表板",
    "管理面板", "管理頁面",
    "執行摘要", "總覽", "概覽", "一覽", "匯總", "卡片",
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


def extract_json_block(text: str) -> str:
    """
    從 LLM 回應中找出第一個 balanced `{...}` JSON block,跳過任何 preamble。

    為什麼需要這個(v0.3.6+):
    Phase A 的 LLM 回應有時會夾雜:
      - 自然語言 preamble(「根據您的需求...」「以下是符合要求的...」)
      - Markdown headers(「### A. 資料獲取:」)
      - Code fence(```json ... ```)
      - 結尾說明(「以上為完整 pipeline...」)
    `json.loads(raw)` 會在這些 noise 上炸掉。

    這個 utility **不靠 model 行為**,純文字 parsing:
      1. 跳過所有 `{` 之前的內容
      2. 用 balanced-brace 演算法找出第一個完整 JSON object
      3. 處理嵌套 `{}` 跟字串內的 `"`

    Returns:
        提取到的 JSON 字串(可直接 `json.loads()`)。若找不到合法 block,
        回傳 stripped 原文(讓 caller 自行錯誤處理,行為跟原本一致)。

    通用性:不 hardcode 任何 model 名稱或 preamble 字眼。所有 model 都適用。
    """
    if not text:
        return ""

    # 先 strip 常見 code fence 開頭(```json 或 ```)
    s = text.strip()
    if s.startswith("```"):
        # 找下一個換行,跳過 fence header
        nl = s.find("\n")
        if nl > 0:
            s = s[nl + 1:]
        # 如果有結尾 ```,砍掉
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3].rstrip()

    # 找第一個 `{`
    start = s.find("{")
    if start < 0:
        return text  # 沒 JSON object,回原文讓 caller 報錯

    # Balanced-brace 演算法 + 字串感知
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(s)):
        ch = s[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                # 找到 balanced block
                return s[start:i + 1]

    # 沒找到 balanced(LLM 截斷?),回 partial
    return s[start:]


_DERIVED_EXPR_OPS = {
    "$cond", "$switch", "$ifNull",
    "$divide", "$multiply", "$add", "$subtract", "$mod",
    "$round", "$abs", "$ceil", "$floor",
    "$concat", "$concatArrays",
    "$sum",  # 只在 $project / $addFields 內非法 — $group 是合法的
}


def _value_uses_derived_expr(v) -> bool:
    """遞迴判斷 $project / $addFields 的某個欄位 value 是否使用了派生表達式。
    遇到 `_DERIVED_EXPR_OPS` 任一 key 即視為派生 → Phase B 該做的事。"""
    if isinstance(v, dict):
        for k, vv in v.items():
            if isinstance(k, str) and k in _DERIVED_EXPR_OPS:
                return True
            if _value_uses_derived_expr(vv):
                return True
    elif isinstance(v, list):
        for item in v:
            if _value_uses_derived_expr(item):
                return True
    return False


def sanitize_pipeline(pipeline: list) -> tuple[list, list[str]]:
    """
    結構性防禦:strip whitespace + 補回漏掉的 `$` 前綴 + 移除 $project/$addFields/$set
    內以派生表達式($cond, $switch, $divide, $multiply 等)定義的欄位。

    為什麼需要:
      1. LLM 偶爾產出帶前導空格的 stage 鍵名(`" $project"`)或漏掉 `$`(`"match"`),
         送進 MongoDB 會觸發 `Unrecognized pipeline stage`。
      2. v0.4.1+ Phase A 鐵律:派生欄位(布林 flag、比率、加總)必須留給 Phase B 用
         pandas 算。LLM 違規時若直接送 MongoDB,即使結果正確,test_runner 仍會把
         該 case 標 fail(因為 check「Pipeline 不含禁忌 stage」會擋下)。
         在這裡先 strip,讓 pipeline 過 check,Phase B 自行重算。

    僅處理「stage 鍵」與「$project/$addFields/$set 內派生表達式欄位」這兩層 —
    不遞迴改 stage 內容(`$in`、`$lookup` 內欄位等),因為那些鍵的合法性與業務
    語意綁定,不該無腦改寫。

    Args:
        pipeline: List[dict] — MongoDB aggregation pipeline。

    Returns:
        (cleaned_pipeline, warnings):
            cleaned_pipeline 為新的 list,每個 stage 鍵都已正規化、派生表達式欄位已剝除。
            warnings 為 human-readable 訊息列(空 list 表示完全乾淨)。
    """
    KNOWN_STAGES = {"match", "lookup", "unwind", "project", "addFields",
                    "set", "replaceRoot", "redact"}
    PROJ_STAGE_KEYS = {"$project", "$addFields", "$set"}
    cleaned = []
    warnings: list[str] = []
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

            # v0.4.1+:$project / $addFields / $set 內派生表達式欄位 → 移除
            if key in PROJ_STAGE_KEYS and isinstance(v, dict):
                kept = {}
                for field, expr in v.items():
                    if _value_uses_derived_expr(expr):
                        warnings.append(
                            f"[sanitize] 移除 {key} 內派生欄位 `{field}` "
                            f"(用了 $cond/$divide 等 — 派生欄位請在 Phase B 用 pandas 算)"
                        )
                        continue
                    kept[field] = expr
                new_stage[key] = kept
            else:
                new_stage[key] = v
        cleaned.append(new_stage)
    return cleaned, warnings


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

    numeric_cols = [c for c in Q.columns if pd.api.types.is_numeric_dtype(Q[c])]
    dim_cols = [c for c in Q.columns if c not in numeric_cols]

    # 偵測橫向
    is_horizontal = (
        xaxis.get("type") == "value" and yaxis.get("type") == "category"
    )

    # 抽出 stack 名:LLM 寫了就用,沒寫預設
    stack_name = "stack"
    if series:
        for s in series:
            if isinstance(s, dict) and s.get("stack"):
                stack_name = s["stack"]
                break

    # ────────────────────────────────────────────────────────────
    # 路徑 A:long format(2+ dims + 1+ numeric)— pivot to wide
    # ────────────────────────────────────────────────────────────
    if len(dim_cols) >= 2 and numeric_cols:
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

        if is_horizontal:
            option.setdefault("yAxis", {})["data"] = pivot.index.astype(str).tolist()
        else:
            option.setdefault("xAxis", {})["data"] = pivot.index.astype(str).tolist()
        option["series"] = [
            {"name": str(col), "type": "bar", "stack": stack_name,
             "data": pivot[col].round(2).tolist()}
            for col in pivot.columns
        ]
        return option, True

    # ────────────────────────────────────────────────────────────
    # 路徑 B:wide format(1 dim + N numerics)— 每個 numeric 當一條 series
    # 例:STK-04 Q = [application_category, pay_pct, rtn_pct, in_progress_pct]
    # ────────────────────────────────────────────────────────────
    if len(dim_cols) == 1 and len(numeric_cols) >= 1:
        x_dim = dim_cols[0]
        x_values = Q[x_dim].astype(str).tolist()

        if is_horizontal:
            option.setdefault("yAxis", {})["data"] = x_values
        else:
            option.setdefault("xAxis", {})["data"] = x_values

        option["series"] = [
            {"name": str(col), "type": "bar", "stack": stack_name,
             "data": [float(v) if v is not None else 0 for v in Q[col].round(2).tolist()]}
            for col in numeric_cols
        ]
        return option, True

    return option, False


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


def coerce_option_native_types(option):
    """
    結構性救援 — 把 ECharts option dict 內所有 numpy/pandas scalar 型別
    遞迴轉成 Python native(int / float / str / bool / None)。

    為什麼需要(v0.4.6+):
      LLM 在 Phase C 產 ECharts option 時,常用 `Q['col'].iloc[i]` / `row['col']`
      直接餵 value。這些是 `numpy.int64` / `numpy.float64` / `numpy.str_`,
      傳給 `streamlit-echarts` 的 BidiComponent serializer 時,部分 numpy scalar
      會被序列化為 JS `null`,前端取 `Object.keys(null)` 直接炸:
          BidiComponent Error: Cannot convert undefined or null to object

      Phase C rule 5.7H 已交代 heatmap 場景必須 `int()` / `float()` cast,
      但 LLM 沒把這條類化到 pie / bar / line 場景。在 LLM 出包前就把 option
      洗一遍,直接根治。

    支援的轉換:
      - numpy scalar(int64 / float64 / bool_ / str_ / datetime64)→ .item()
      - pandas Timestamp / Timedelta → .isoformat() / str()
      - numpy NaN / NaT → None(ECharts 視為 null,渲染斷點)
      - 巢狀 dict / list / tuple 遞迴處理

    純 functional — 回傳新物件,不 mutate 輸入。
    """
    import math
    import numpy as _np
    import pandas as _pd
    NoneType = type(None)
    LEAF_NATIVE = (str, int, float, bool, NoneType)

    def _coerce(v):
        # native 已 OK
        if isinstance(v, LEAF_NATIVE):
            # 但 float('nan') / float('inf') 不能進 JSON → 轉 None
            if isinstance(v, float) and not math.isfinite(v):
                return None
            return v
        # dict / list / tuple 遞迴
        if isinstance(v, dict):
            return {str(k): _coerce(vv) for k, vv in v.items()}
        if isinstance(v, (list, tuple)):
            return [_coerce(vv) for vv in v]
        # pandas / numpy NaN / NaT
        try:
            if _pd.isna(v):
                return None
        except (TypeError, ValueError):
            pass
        # numpy scalar → .item() 取 Python native
        if isinstance(v, _np.generic):
            try:
                native = v.item()
                if isinstance(native, float) and not math.isfinite(native):
                    return None
                return native
            except Exception:
                return str(v)
        # pandas Timestamp / Timedelta
        if isinstance(v, (_pd.Timestamp, _pd.Timedelta)):
            return v.isoformat() if hasattr(v, "isoformat") else str(v)
        # 其他不認識的型別 → str fallback(總比炸好)
        try:
            # 看看能不能 hint 是 numpy array
            if hasattr(v, "tolist"):
                return _coerce(v.tolist())
        except Exception:
            pass
        return str(v)

    if not isinstance(option, dict):
        return option
    return _coerce(option)


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


# ============================================================
# v0.5.0:Phase C chart-intent detector
#
# 從 query 偵測該注入哪一組 chart-specific rules,讓 Phase C prompt 從
# ~24K(all-in-one)降到 ~9-10K(只注入相關規則)。
#
# Intent 列表(11 個):
#   pie / stacked_100 / stacked_raw / line_dual / heatmap /
#   bar_horizontal / line_single / scatter / kpi_table / bar_grouped /
#   bar_basic(default fallback)
#
# 判斷順序:複合條件 (多關鍵字 AND) 優先於單關鍵字。
# ============================================================

# v0.5.1:count + rate 都改 regex-based,跨 domain 通用
# count:universal 短詞 + regex 抓「X 數 / X 量 / X 次 / X 筆」compound
#   (健保「人次」/「病例數」、電商「訂單數」、HR「員工數」都吃得到)
_CHART_COUNT_WORDS = ('絕對量', 'count', 'volume')  # universal 純詞,具體 compound 留給 regex
_COUNT_REGEX = __import__('re').compile(r"[一-鿿]+(?:數|量|次|筆|件)(?!率)")
# (?!率) 避免「率」結尾(「百分率」/「成功率」)誤觸 count

# rate:universal 短詞 + regex 抓「X 率」compound
#   (健保「再入院率」、電商「轉換率」、HR「離職率」都吃得到)
_CHART_RATE_WORDS = ('比率', '比例', '佔比', '占比', '百分比',
                      'rate', 'ratio', '%')
_RATE_REGEX = __import__('re').compile(r"[一-鿿]+率")
_CHART_COMPARE_WORDS = ('比較', '同時看到', '同時', 'vs', '對比', 'compared',
                         '對照')
_CHART_PIE_WORDS = ('圓餅圖', 'pie chart', 'pie', '餅圖', '圓形圖', '派圖',
                     'donut')
_CHART_HEATMAP_WORDS = ('熱力圖', 'heatmap', 'heat map', '熱度', '熱圖')
_CHART_STACK_WORDS = ('stacked', 'stack', '堆疊', '堆積')
_CHART_100PCT_WORDS = ('100%', '100 %', '百分比堆疊', '占比分佈',
                        '比例分佈', 'percentage stack',
                        # v0.9.1:「百分圖」「百分比圖」「百分百」等變體
                        '百分圖', '百分比圖', '百分百')
# v0.8.8 / v0.9.1:「在每柱/每條 bar 內」+「占比/佔比/比例」= 強信號,
# 等同 100% normalize。
# v0.9.1 補無空格變體 (中文輸入習慣不一定加空格):
# 「每條bar」「每個bar」對齊「每條 bar」「每個 bar」。
_CHART_INTRA_BAR_WORDS = (
    # 有空格
    '每條 bar', '每個 bar', '每一條 bar',
    'each bar', 'per bar', 'within each bar', 'per category bar',
    'inside each bar',
    # 無空格(v0.9.1)
    '每條bar', '每個bar', '每一條bar',
    # 中文純漢字(原來就有)
    '每條柱', '每柱', '每根',
)
_CHART_PROPORTION_WORDS = ('占比', '佔比', '比例', 'proportion', 'share')
_CHART_HORIZONTAL_WORDS = ('橫向', '水平', 'horizontal', '排名', 'ranking',
                            'rank', 'top n', 'top 10', 'top 5', 'top 3')
_CHART_SCATTER_WORDS = ('散布圖', '散點圖', 'scatter', '相關性', 'correlation')
_CHART_LINE_WORDS = ('趨勢', 'trend', '折線', 'line chart', '時間序列',
                      'time series', '走勢')
_CHART_KPI_TABLE_WORDS = ('kpi 一覽', 'dashboard', '儀表板', '執行摘要',
                            '一覽', '匯總', '摘要報表', 'kpi overview',
                            'executive summary')
_CHART_GROUPED_WORDS = ('並排', 'grouped', 'side-by-side', '分別看',
                         '分組比較')
# v0.13.1:Histogram(分佈直方圖)— ECharts 沒有 histogram type,
# 需要 Phase B 用 np.histogram 預 bin,Phase C 用 bar + markLine
_CHART_HISTOGRAM_WORDS = ('直方圖', '分佈圖', '分布圖', 'histogram',
                           'distribution plot', 'frequency distribution',
                           '頻率分佈', '頻率分布', '頻次分佈', '頻次分布',
                           '分佈直方', '分布直方')
# 註:不加單字「分佈」/「distribution」— 太 ambiguous,
# 可能是 stacked / pie / histogram。需明確 compound 詞(直方圖 / distribution plot)才觸發。


def _has_any(haystack: str, needles: tuple) -> bool:
    """大小寫不敏感檢查 haystack 是否含 needles 中任一字串。"""
    if not haystack:
        return False
    low = haystack.lower()
    for n in needles:
        if n.lower() in low or n in haystack:
            return True
    return False


def _has_rate(query: str) -> bool:
    """偵測 query 含「比率/比例」類詞 — domain-generic 設計。
    快路徑用 universal 短詞;慢路徑用 regex `[CJK]+率` 抓任何 domain 的「X 率」
    (例:健保「再入院率」/「住院率」、電商「轉換率」/「跳出率」、HR「離職率」)。
    """
    if not query:
        return False
    if _has_any(query, _CHART_RATE_WORDS):
        return True
    return bool(_RATE_REGEX.search(query))


def _has_count(query: str) -> bool:
    """偵測 query 含「絕對量/計數」類詞 — domain-generic 設計。
    快路徑用 universal 短詞;慢路徑用 regex `[CJK]+(?:數|量|次|筆|件)` 抓
    「X 數」「X 量」「X 次」compound(健保「人次」、電商「訂單數」、HR「員工數」)。
    `(?!率)` 排除「X 率」誤觸(它是 rate 不是 count)。
    """
    if not query:
        return False
    if _has_any(query, _CHART_COUNT_WORDS):
        return True
    return bool(_COUNT_REGEX.search(query))


def _detect_chart_intent(query: str) -> str:
    """
    從 query 偵測 Phase C 應該注入哪組 chart-specific rules。

    回傳 11 種 intent 之一:
      pie / stacked_100 / stacked_raw / line_dual / heatmap /
      bar_horizontal / line_single / scatter / kpi_table /
      bar_grouped / bar_basic(default)

    判斷順序(從特異到通用):
      1. 複合條件(雙軸 = count + rate + compare 三件齊)
      2. 強單關鍵字(pie / heatmap / scatter / kpi_table)
      3. 100% stacked 變體(必須有 stack 詞 + 100%/百分比)
      4. 一般 stacked
      5. 橫向 bar / 折線 / 並排
      6. fallback: bar_basic

    純 heuristic,零 LLM call。
    """
    if not query:
        return "bar_basic"

    has_count = _has_count(query)  # v0.5.1:regex-based,跨 domain
    has_rate = _has_rate(query)    # v0.5.1:regex-based,跨 domain
    has_compare = _has_any(query, _CHART_COMPARE_WORDS)
    has_stack = _has_any(query, _CHART_STACK_WORDS)
    has_100pct = _has_any(query, _CHART_100PCT_WORDS)
    # v0.8.9:跟 _detect_preprocess_intent 同步 — intra-bar proportion 也算 100%。
    has_intra_bar = _has_any(query, _CHART_INTRA_BAR_WORDS)
    has_proportion = _has_any(query, _CHART_PROPORTION_WORDS)
    intra_bar_proportion = has_intra_bar and has_proportion
    # v0.9.1:orientation 是與 chart type 正交的維度。user 明說「橫向 / 水平 /
    # horizontal」時,優先級高於 stack 組合詞 → stacked+horizontal 不該被
    # vertical stacked block 攔走(否則 user 看到的是 vertical bar)。
    has_horizontal = _has_any(query, _CHART_HORIZONTAL_WORDS)

    # ━━━ Tier 1:複合條件 ━━━
    # 雙軸 bar+line:三件齊(絕對量 + 比率 + 比較)
    if has_count and has_rate and has_compare:
        return "line_dual"

    # ━━━ Tier 2:強單關鍵字(明示圖型)━━━
    if _has_any(query, _CHART_HEATMAP_WORDS):
        return "heatmap"
    # v0.13.1:histogram 是強信號(直方圖 / distribution 都明示),優先於 pie / scatter / bar
    if _has_any(query, _CHART_HISTOGRAM_WORDS):
        return "histogram"
    if _has_any(query, _CHART_PIE_WORDS):
        return "pie"
    if _has_any(query, _CHART_SCATTER_WORDS):
        return "scatter"

    # ━━━ Tier 3:stacked 變體(orientation 正交,優先檢查 horizontal)━━━
    if has_stack and (has_100pct or '百分比' in query or intra_bar_proportion):
        return "stacked_100_horizontal" if has_horizontal else "stacked_100"
    if has_stack:
        return "stacked_raw_horizontal" if has_horizontal else "stacked_raw"

    # 100% / 百分比 + 占比/比例分佈 等強信號,即使沒明說 stacked 也走 100%
    if has_100pct and (has_rate or '分佈' in query or '結構' in query):
        return "stacked_100"

    # ━━━ Tier 4:其他圖型 ━━━
    if _has_any(query, _CHART_HORIZONTAL_WORDS):
        return "bar_horizontal"
    if _has_any(query, _CHART_KPI_TABLE_WORDS):
        return "kpi_table"
    if _has_any(query, _CHART_LINE_WORDS):
        return "line_single"
    if _has_any(query, _CHART_GROUPED_WORDS):
        return "bar_grouped"

    return "bar_basic"


# ============================================================
# v0.6.0:Phase B preprocess intent detector
#
# 6 種 intent → 對應 Phase B skeleton block:
#   dashboard_kpi / stacked_long_pct / stacked_wide / ratio_kpi /
#   time_series / simple_groupby (default)
#
# 完全 domain-generic:用 _has_rate / _has_count regex + universal pattern。
# ============================================================

_TIMESERIES_WORDS = ('趨勢', 'trend', '時間序列', 'time series',
                      '走勢', '時序', '每月', '每年', '每週', '每日',
                      'monthly', 'weekly', 'daily', 'yearly')


def _metadata_has_time_col(metadata: dict | None) -> bool:
    """檢查 metadata 是否描述了任何時間欄位(date / timestamp / datetime)。
    schema-driven 偵測,避免在沒時間欄的 domain 誤走 time_series。"""
    if not metadata or not isinstance(metadata, dict):
        return False
    collections = metadata.get("collections", {}) or {}
    if not isinstance(collections, dict):
        return False
    for _, col_def in collections.items():
        if not isinstance(col_def, dict):
            continue
        fields = col_def.get("fields", []) or []
        for f in fields:
            if not isinstance(f, dict):
                continue
            ftype = (f.get("type") or "").lower()
            fname = (f.get("name") or "").lower()
            if ftype in ("date", "datetime", "timestamp"):
                return True
            if any(t in fname for t in ("date", "time", "timestamp", "_at", "_dt")):
                return True
    return False


def _detect_preprocess_intent(query: str,
                               dashboard_hint: bool = False,
                               metadata: dict | None = None) -> str:
    """
    Phase B routing — 決定該注入哪個 skeleton block。

    回傳 6 種 intent 之一:
      dashboard_kpi / stacked_long_pct / stacked_wide / ratio_kpi /
      time_series / simple_groupby

    判斷順序(從特異到通用):
      1. dashboard_hint 已 set → dashboard_kpi
      2. stacked + 100%/百分比 信號 → stacked_long_pct
      3. stacked 但非 100% → stacked_wide
      4. time_series 詞 + metadata 有 time col → time_series
      5. 含 rate 詞 → ratio_kpi
      6. fallback → simple_groupby

    純 heuristic + schema check,零 LLM call。Domain-generic。
    """
    if dashboard_hint:
        return "dashboard_kpi"
    if not query:
        return "simple_groupby"

    has_stack = _has_any(query, _CHART_STACK_WORDS)
    has_100pct = _has_any(query, _CHART_100PCT_WORDS)
    # v0.8.8:intra-bar 占比 = 100% normalize 強信號
    has_intra_bar = _has_any(query, _CHART_INTRA_BAR_WORDS)
    has_proportion = _has_any(query, _CHART_PROPORTION_WORDS)
    intra_bar_proportion = has_intra_bar and has_proportion

    if has_stack and (has_100pct or '百分比' in query or intra_bar_proportion):
        return "stacked_long_pct"
    if has_stack:
        return "stacked_wide"

    # v0.13.1:histogram 需特殊 binning 預處理(np.histogram),優先於其他 intent
    if _has_any(query, _CHART_HISTOGRAM_WORDS):
        return "histogram"

    # time_series 需要 schema-level 確認(domain 沒時間欄不該走這條)
    if _has_any(query, _TIMESERIES_WORDS) and _metadata_has_time_col(metadata):
        return "time_series"

    if _has_rate(query):
        return "ratio_kpi"

    return "simple_groupby"


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

❌  在 code 內用全形標點 (`，` U+FF0C / `；` U+FF1B / `（` U+FF08 / `）` U+FF09 / `：` U+FF1A)
    為什麼:Python parser 只接半形 ASCII 標點;全形標點會炸
    `SyntaxError: invalid character '，' (U+FF0C)`,而且這個錯不會自動修。
    識別:中文輸入法切換時很容易混進 code,LLM 思考中文時偶發。
    ✅  程式碼內所有標點(逗號 / 分號 / 括號 / 冒號 / 點)**必須全部 ASCII 半形**:
        ```python
        agg = Q.groupby('col').agg(           # ✅ 半形 ( ) ,
            count=('flag', 'sum'),
        )
        # ❌ agg = Q.groupby('col').agg(     # ❌ 全形 ( 會炸
        #         count=('flag'，'sum')，    # ❌ 全形 ， 會炸
        #     )
        ```
        Comment / docstring 內的中文標點 OK;只有 **code 結構字元** 必須 ASCII。

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
                 domain: str = "tflex",
                 model_profile: dict | None = None,
                 disable_thinking: bool = False,
                 retrieval_orchestrator=None,
                 rag_enabled: bool = False):
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
        model_profile: v0.10.6+。phase → sampling 參數 dict 的 mapping
                       (例 {"pipeline": {"temperature": 0.6, "retry_temperature": 0.75},
                            "insight":  {"temperature": 0.7, "presence_penalty": 1.5}, ...})
                       由 config.MODEL_PROFILE 提供。None 代表「沿用既有 hardcoded 行為」
                       (向下相容,不會打破現有部署)。
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
        # v0.10.6+ phase → sampling profile;None = legacy hardcoded fallback
        self.model_profile = model_profile or {}
        # v0.13.3+: Ollama Qwen 3.6 thinking toggle(對 Qwen 3.6 等 thinking 模型有效)
        # default False = 既有 schema-driven byte-equal,不加 extra_body
        self.disable_thinking = bool(disable_thinking)

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

        # ── v0.7.0+ TaskTrace hook ──
        # caller(app.py / test_runner.py)可在 query 開始前 attach 一個 TaskTrace
        # instance;_call_llm 會自動同步完整 messages + response 進去。
        # 若 None,trace 機制完全 disabled(向後相容)。
        self.trace = None

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

        # ── v0.16.0+ RAG dynamic prompt(M6.2 Sprint 1)──
        # retrieval_orchestrator=None + rag_enabled=False(default)→ 完全 byte-equal v0.15
        # caller 注入 RetrievalOrchestrator 並設 rag_enabled=True 才會跑 RAG
        self.retrieval_orchestrator = retrieval_orchestrator
        self.rag_enabled = bool(rag_enabled) and (retrieval_orchestrator is not None)
        # ── v0.16.0+ M6.3 Sprint 3 結論:Phase B/C RAG 分開 gate ──
        # 預設關閉(Sprint 3 實測 -2 cases vs Sprint 2);需要時用 env / kwarg 開
        try:
            import config as _cfg
            self.rag_phase_bc_enabled = bool(
                getattr(_cfg, "RAG_PHASE_BC_ENABLED", False)
            )
        except Exception:
            self.rag_phase_bc_enabled = False
        # last_query 由 generate_plan/pipeline/insight 在進 LLM call 前 set,
        # 給 _render_phase_X 拿來當 RAG query string(避免改 _render 簽章)。
        self._last_query: str = ""

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
    def _resolve_phase_sampling(self, phase: str, is_retry: bool = False,
                                  fallback_temp: float | None = None) -> dict:
        """v0.10.6+ — 依 phase 查 model_profile 解析 sampling 參數。

        回傳 dict,包含 keys:
            - temperature (float)
            - presence_penalty (float, optional — 只有 profile 有設才出現)

        參數:
            phase: "plan" / "pipeline" / "preprocess" / "plotly" / "echarts" /
                   "insight" / "meta_response"
            is_retry: True 時優先取 profile 的 retry_temperature,
                      沒設就退回 temperature + 0.15(維持 v0.10.3 既有 retry bump 行為)
            fallback_temp: profile 沒蓋到時用此值(通常 caller 給 self.default_temperature
                           或既有 hardcoded 值,例如 plan 給 0.2)

        Notes:
            - profile 為 None / 空 dict / phase 沒蓋到 → 完全 fallback 到舊行為
              (caller 自己控制 temperature,不傳 presence_penalty)
        """
        cfg = (self.model_profile or {}).get(phase, {})
        out: dict = {}
        if cfg:
            base_t = cfg.get("temperature", fallback_temp)
            if is_retry:
                out["temperature"] = cfg.get("retry_temperature",
                                              (base_t + 0.15) if base_t is not None else None)
            else:
                out["temperature"] = base_t
            if "presence_penalty" in cfg:
                out["presence_penalty"] = cfg["presence_penalty"]
        else:
            # profile 沒蓋到此 phase → caller 自決
            out["temperature"] = fallback_temp
        # 過濾 None(避免傳給 OpenAI client 出錯)
        return {k: v for k, v in out.items() if v is not None}

    # --- v0.10.6+ <think>...</think> stripper ---
    # reasoning distilled 模型(如 Qwen3.6-Claude-Opus-Reasoning-Distilled)會輸出
    # <think>...</think> 區塊,下游 parser(JSON / code fence)會被搞亂,統一在
    # _call_llm 出口處 strip 掉。對沒有 think 區塊的 response 完全無感(noop)。
    _THINK_BLOCK_RE = re.compile(
        r"<think>.*?</think>\s*", flags=re.DOTALL | re.IGNORECASE
    )

    @classmethod
    def _strip_think_blocks(cls, raw: str) -> str:
        if not raw or "<think" not in raw.lower():
            return raw
        return cls._THINK_BLOCK_RE.sub("", raw).lstrip()

    def _call_llm(self, messages, temperature=None, max_tokens=2048,
                   phase: str = "unknown", presence_penalty: float | None = None):
        if temperature is None:
            temperature = self.default_temperature
        # 組 sampling kwargs;只有 caller / profile 明確指定才傳 presence_penalty
        # (避免對舊 endpoint / 不支援的 backend 出錯)
        _create_kwargs = {
            "model": self.model_name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if presence_penalty is not None:
            _create_kwargs["presence_penalty"] = presence_penalty
        # v0.13.3+: Ollama Qwen 3.6 thinking toggle
        # 對 thinking 模型(/no_think prompt directive 在 Qwen 3.6 失效),
        # 必須走 Ollama API extra_body={"think": False}。對不支援此 key 的 backend
        # (OpenAI 雲端 / vLLM 部分版本)會被 ignore,不影響;對 Ollama Qwen 3.6 才真關。
        if self.disable_thinking:
            _create_kwargs["extra_body"] = {"think": False}
        t0 = time.time()
        try:
            response = self.client.chat.completions.create(**_create_kwargs)
        except Exception as e:
            elapsed = round(time.time() - t0, 2)
            self.call_log.append({
                "phase": phase,
                "elapsed_s": elapsed,
                "prompt_tokens": None,
                "completion_tokens": None,
                "total_tokens": None,
                "error": str(e),
            })
            # v0.7.0:trace 失敗 call(若 trace recorder 存在)
            if getattr(self, "trace", None) is not None:
                try:
                    self.trace.record_llm_call(
                        phase=phase, model=self.model_name,
                        messages=messages, response="",
                        prompt_tokens=None, completion_tokens=None, total_tokens=None,
                        elapsed_s=elapsed, error=str(e),
                    )
                except Exception:
                    pass  # silent — trace 失敗不影響 user query
            raise RuntimeError(f"LLM API 呼叫失敗: {str(e)}")

        elapsed = round(time.time() - t0, 2)
        usage = getattr(response, "usage", None)
        pt = getattr(usage, "prompt_tokens", None) if usage else None
        ct = getattr(usage, "completion_tokens", None) if usage else None
        tt = getattr(usage, "total_tokens", None) if usage else None
        self.call_log.append({
            "phase": phase,
            "elapsed_s": elapsed,
            "prompt_tokens": pt,
            "completion_tokens": ct,
            "total_tokens": tt,
        })
        response_content = response.choices[0].message.content

        # v0.10.6+:reasoning distilled model 會輸出 <think>...</think> 區塊
        # 在 trace 之後 strip,讓 trace 保留完整 raw output 方便 debug
        response_content_clean = self._strip_think_blocks(response_content)

        # v0.7.0:trace recorder hook — 完整記錄 messages + response(含 think block 方便 debug)
        if getattr(self, "trace", None) is not None:
            try:
                self.trace.record_llm_call(
                    phase=phase, model=self.model_name,
                    messages=messages, response=response_content,
                    prompt_tokens=pt, completion_tokens=ct, total_tokens=tt,
                    elapsed_s=elapsed,
                )
            except Exception:
                pass  # silent — trace 失敗不影響 user query

        return response_content_clean

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
                            cheatsheet: str = "",
                            phase: str = "unknown") -> str:
        """把上一次失敗的 code + traceback 轉成 LLM 修正提示。
        可選 cheatsheet 附在後面,提示常見 anti-pattern。

        v0.10.5:`phase` 參數(preprocess / echarts / plotly / pipeline)
        讓特定錯誤的 hint 能 phase-aware 給出對的 fix(例 KeyError 在
        Phase B 跟 Phase C 是兩種不同情境,該給不同 hint)。
        """
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
        # v0.7.1 / v0.8.7:特定錯誤模式偵測,加強對應提示
        # (baseline 觀察:LLM 在 retry feedback 內常常忽略主要規則,
        #  3 attempts 重複同錯。給「error → fix」明確映射可大幅改善。)
        if previous_error:
            err = previous_error
            if "ModuleNotFoundError" in err or "No module named" in err:
                hint += (
                    "\n🚨【關鍵修正提示】你嘗試 `import` 一個不存在的套件。\n"
                    "**禁止 import 任何套件** — `pd`(pandas)跟 `np`(numpy)已備好。\n"
                    "Phase B 只負責資料處理(groupby / agg / filter / 計算 KPI),**不畫圖**。\n"
                    "畫圖是 Phase C 的工作,Phase B 完全不需要 matplotlib / plotly / seaborn 等。\n"
                    "把所有 `import xxx` 那行刪掉重來。\n"
                )
            # v0.8.7 / v0.8.9:.round() 雷,baseline 出現 6+ 次
            elif "object has no attribute 'round'" in err:
                hint += (
                    "\n🚨【關鍵修正提示】你對 **scalar** 物件呼叫了 `.round(N)`。\n"
                    "Python `float` / `int` / `str` 都**沒有** `.round()` 方法,只有 pandas Series / DataFrame 有。\n"
                    "**改用 `round(value, N)` builtin**(對任何 numeric 都行):\n\n"
                    "  ❌ 常見錯誤型態(v0.8.9 baseline 第 6 次):\n"
                    "     `value.round(2)`\n"
                    "     `Q['rate'].iloc[0].round(2)`\n"
                    "     `min(Q['x']).round(2)`\n"
                    "     `(rate * 100).round(2)`                              # expr 結果是 scalar\n"
                    "     `[(v * 100).round(2) for v in Q['x'].tolist()]`     # list comp 內每元素也是 scalar\n\n"
                    "  ✅ 正解兩條路:\n"
                    "     **路 1:Series 鏈式**(最簡)\n"
                    "       `(Q['rate'] * 100).round(2).tolist()`              # Series → Series → list\n"
                    "     **路 2:list comp 用 builtin**\n"
                    "       `[round(v * 100, 2) for v in Q['rate'].tolist()]`\n\n"
                    "  ⚠️ 不論你想 round 的是不是 expr,只要那個物件是 scalar,就用 `round(x, 2)`,**禁止寫 `x.round(2)`**。\n"
                )
            # v0.8.7:long format xAxis no dedupe,baseline 連續 3 次中
            elif ("KeyError" in err and any(t in err for t in
                  ("'PAY'", "'RTN'", "'AI'", "'H'", "'Y'", "'N'"))):
                hint += (
                    "\n🚨【關鍵修正提示】KeyError 在 value 字串上,代表你把 **long format 的「值」當成「欄位名」**。\n"
                    "Long format 下,`Q['<dim_col>']` 裡的 'PAY' / 'RTN' / 'AI' 等是**欄位裡的值**,不是欄位本身。\n"
                    "  ❌ `Q['PAY']`(KeyError,PAY 是 review_result 欄位的值)\n"
                    "  ✅ `Q[Q['review_result'] == 'PAY']['count']`(filter row 再取 value column)\n"
                )
            # v0.8.8:Phase C 對 aggregated Q 用 raw_df 級欄位 filter
            # baseline Cases 03 / 05 兩個連 hit,且 retry 3 次都同錯。
            elif ("KeyError" in err and any(t in err for t in
                  ("'review_status'", "'review_result'", "'review_mechanism'",
                   "'application_no'", "'employee_id'", "review_status",
                   "review_result", "review_mechanism"))):
                hint += (
                    "\n🚨【關鍵修正提示】KeyError 在 raw_df 級欄位上(`review_status` / "
                    "`review_result` / `review_mechanism` / `application_no` 等),\n"
                    "代表你對 `Q` 用了 raw 級欄位 filter — 但 **Phase B 已經把這些 aggregate 掉了**!\n"
                    "**Q 是 Phase B 的終態,raw 級欄位幾乎一定不在 Q.columns**。\n\n"
                    "  ❌ 你寫的(`Q['review_result']` / `Q[Q['review_mechanism']=='AI']` 之類):\n"
                    "     對 Q 做 filter / groupby,引用 raw 級欄位\n\n"
                    "  ✅ 正解:**只用 q_columns 裡實際存在的 KPI 欄位**\n"
                    "     例如 `Q.columns = ['company_code', 'pay_count', 'return_count', 'ai_rate']`\n"
                    "     → 多 series stacked bar:每個 KPI column 直接做一個 series:\n"
                    "       ```python\n"
                    "       series = [\n"
                    "           {'name': 'PAY',    'type': 'bar', 'stack': 'x',\n"
                    "             'data': Q['pay_count'].tolist()},\n"
                    "           {'name': 'RTN',    'type': 'bar', 'stack': 'x',\n"
                    "             'data': Q['return_count'].tolist()},\n"
                    "       ]\n"
                    "       ```\n"
                    "     **完全不需要也禁止 filter Q**!\n"
                )
            elif "KeyError" in err:
                # v0.10.5:phase-aware — Phase B 的 KeyError 多是 agg result 缺欄位
                if phase == "preprocess":
                    hint += (
                        "\n🚨【關鍵修正提示 · Phase B · v0.10.5 加強】\n"
                        "KeyError 在 Phase B 多半是這 2 種情境之一:\n\n"
                        "**情境 A** — `Q.groupby(...).agg(...)` 後想用沒列在 agg() 內的欄位:\n"
                        "```python\n"
                        "agg = raw_df.groupby('company_code').agg(\n"
                        "    total=('application_no', 'size'),\n"
                        "    completed=('review_status', lambda x: (x=='Y').sum()),\n"
                        "    # ❌ 忘了把 review_mechanism 列進來!\n"
                        ")\n"
                        "agg['ai_rate'] = agg['review_mechanism'].apply(...) / agg['completed']\n"
                        "#                ^^^^^^^^^^^^^^^^^^^^^^^^ ❌ KeyError\n"
                        "```\n"
                        "✅ 修法:把要用的 col 加進 agg(...):\n"
                        "```python\n"
                        "agg = raw_df.groupby('company_code').agg(\n"
                        "    total=('application_no', 'size'),\n"
                        "    completed=('review_status', lambda x: (x=='Y').sum()),\n"
                        "    ai_count=('review_mechanism', lambda x: (x=='AI').sum()),  # ✅ 加上\n"
                        ")\n"
                        "agg['ai_rate'] = agg['ai_count'] / agg['completed']  # ✅ 用 agg.col\n"
                        "```\n\n"
                        "**情境 B** — raw_df 加 bool flag 但 agg 沒帶上:\n"
                        "```python\n"
                        "Q['is_ai'] = (Q['review_mechanism'] == 'AI')  # ✅ 加 flag\n"
                        "company_agg = Q.groupby('company_code').agg(\n"
                        "    total=('application_no', 'size'),\n"
                        "    # ❌ 漏 is_ai!\n"
                        ")\n"
                        "company_agg['ai_rate'] = company_agg['is_ai'].sum() / company_agg['total']\n"
                        "#                       ^^^^^^^^^^^^^^^^^^^^^^^^^ ❌ KeyError\n"
                        "```\n"
                        "✅ 修法:`ai_count=('is_ai', 'sum')` 加進 agg。\n\n"
                        "⚠️ 核心心法:**agg 後 ONLY 含 agg(name=...) 內列出的欄位**;raw_df 級 / 中間 flag 都消失,要用就明列進 agg(...)。\n"
                    )
                else:
                    hint += (
                        "\n🚨【關鍵修正提示】KeyError 表示你引用了不存在的欄位。\n"
                        "**只能用 `q_columns` / `avail_cols` 中真實存在的欄位**,不要憑想像。\n"
                        "若做 `Q.groupby(...).agg(...)` 後想保留 raw 維度欄位(例如 hc / company_code),\n"
                        "**必須**用 `agg(<col>=('<col>', 'first'))` 主動帶上,否則 column 會消失。\n"
                    )
            # v0.8.7:Phase B str/numeric divide,baseline Case 01 中
            elif "rtruediv" in err and "str" in err:
                hint += (
                    "\n🚨【關鍵修正提示】TypeError on rtruediv 表示**用 string 欄位做除法**(分子或分母是字串)。\n"
                    "計比率類 KPI 不要直接除原始 string 欄位(如 review_result / status / id);要先**轉 bool 再 sum**:\n"
                    "  ❌ `Q['review_result'] / Q['count']`(string ÷ int 必炸)\n"
                    "  ✅ Step 1: `Q['is_X'] = (Q['<status_col>'] == '<value>')`  ← bool\n"
                    "     Step 2: `agg(X_count=('is_X', 'sum'), total=('<id>', 'count'))`  ← int / int\n"
                    "     Step 3: `agg['rate'] = agg['X_count'] / agg['total']`\n"
                )
            # v0.8.7:bracket/paren mismatch,Phase C STK-01 連續 3 次
            elif ("does not match opening parenthesis" in err
                  or "EOF while" in err
                  or "did you forget parentheses around the comprehension target" in err):
                hint += (
                    "\n🚨【關鍵修正提示】SyntaxError 表示**括號 `( [ {` 對不上**或 comprehension 缺括號。\n"
                    "常見坑:\n"
                    "  ❌ `series: [{...} for cat in cats]}`(`{...}` 對 `[`,最後 `}` 多餘)\n"
                    "  ❌ `data = [i, j, v for i in ... for j in ...]`(tuple 在 comp 內必須加括號)\n"
                    "  ✅ `data = [(i, j, v) for i in ... for j in ...]`\n"
                    "  ✅ 用「先建 list / dict 再組裝」拆成多步,避免一行寫滿炸括號。\n"
                )
            elif "object has no attribute" in err:
                # generic AttributeError(非 round)
                hint += (
                    "\n🚨【關鍵修正提示】AttributeError 通常是「對錯型別呼叫方法」。\n"
                    "檢查那個變數是 Series / DataFrame / scalar / str / list 哪一種,\n"
                    "選對 API(例:scalar 用 builtin,Series 用 `.method()`)。\n"
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
        #
        # v0.12.0+: 對 upload-driven metadata(含 source_type='upload' 旗標),
        # 走另一條 Phase 0 plan prompt 描述 A 段為 Pandas filter 而非 MongoDB pipeline。
        # 既有 metadata 無 source_type key → fallthrough 走 'phase_0_plan' → byte-equal。
        is_upload = (self.task_metadata or {}).get("source_type") == "upload"
        prompt_key = "phase_0_plan_upload" if is_upload else "phase_0_plan"
        self._last_query = query
        system_prompt = self._render_phase_0_plan_prompt(prompt_key=prompt_key)
        # 接續分析時注入前次脈絡
        followup_preamble = build_followup_preamble(followup_context) if followup_context else ""
        user_msg = followup_preamble + f"需求:{query}\n請給出計畫:"
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ]
        try:
            _samp = self._resolve_phase_sampling("plan", fallback_temp=0.2)
            return {"status": "success", "message": self._call_llm(messages, phase="plan", **_samp)}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _render_phase_0_plan_prompt(self, prompt_key: str = "phase_0_plan") -> str:
        """
        產生 Phase 0 plan system prompt。

        優先序:
        1. PromptRepository.render(prompt_key) — 若 repo 可用且 enabled
        2. Inline f-string fallback — v0.2.x 行為

        v0.12.0+: 加 `prompt_key` 參數
          - "phase_0_plan"(default)— 既有 schema-driven 路徑 → inline 行為 byte-equal
          - "phase_0_plan_upload" — Upload Workspace 路徑 → A 段描述為 Pandas filter

        驗證點(D3 byte-equal):
            assert llm._render_phase_0_plan_prompt() == _inline_phase_0_plan_prompt()
        """
        if self.prompt_repo is not None:
            try:
                rag_kwargs = self._retrieve_rag_slots("phase_0_plan")
                return self.prompt_repo.render(
                    prompt_key,
                    domain=self.domain,
                    domain_knowledge=self.domain_knowledge,
                    **rag_kwargs,
                )
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(
                    f"Phase 0 prompt repo render `{prompt_key}` 失敗,fallback to inline: {e}"
                )
        # Inline fallback
        if prompt_key == "phase_0_plan_upload":
            return self._inline_phase_0_plan_upload_prompt()
        return self._inline_phase_0_plan_prompt()

    # ============================================================
    # v0.16.0+ RAG slot retrieval helper
    # ============================================================
    def _retrieve_rag_slots(self, phase: str,
                              extra_filters: dict | None = None) -> dict:
        """從 RetrievalOrchestrator 抽 RAG slots,回 `dict[rag_slot_name -> str]`。

        rag_enabled=False / orchestrator=None / 無 query / 任何錯誤 → 回 {}。
        prompt_repo.render() 會 auto-inject 空字串 default,所以空 dict OK。

        Args:
            phase: phase id(對齊 RetrievalOrchestrator 的 phase_policy keys)
            extra_filters: 額外 filter,合併進 orchestrator filter
                (eg Phase C 帶 {"intent": "pie"} 過濾 chart_recipe)
        """
        if not self.rag_enabled or self.retrieval_orchestrator is None:
            return {}
        # v0.16.0+ M6.3 Sprint 3:Phase B/C 分開 gate(預設關)
        if phase in ("phase_b_preprocess", "phase_c_chart") and \
                not self.rag_phase_bc_enabled:
            return {}
        query = (self._last_query or "").strip()
        if not query:
            return {}
        try:
            return self.retrieval_orchestrator.retrieve_for_phase(
                phase=phase,
                query=query,
                domain=self.domain,
                rag_enabled=True,
                extra_filters=extra_filters,
            )
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                f"RAG retrieve failed for phase={phase}: {e}. RAG slots skipped."
            )
            return {}

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

🚨【鐵律】(CRITICAL FATAL — 最容易踩) 「圖型詞」**完全不參與**這份判斷:
- **圓餅圖 / pie / 圓形圖 / 派圖 / donut**
- **長條圖 / bar / 柱狀圖**
- **折線圖 / line**
- **熱力圖 / heatmap**
- **散布圖 / scatter**
- **stacked bar / 堆疊圖**

這些只是「呈現方式」,不會額外要求任何欄位。即使你「直覺覺得 pie chart 通常需要時間軸」,
**忘掉這個直覺** — pie chart 只需要「類別 + 數值」兩欄,任何 BI domain 都能畫。
判斷拒絕只看「使用者要 *算出* 什麼指標」,**不看使用者要怎麼 *畫出* 來**。

**Step 2 · 查 metadata 的 schema** — 對應的維度/指標是否存在於 `collections.*.fields`?
- 是 → Step 3 不適用,**走 A/B/C 計畫**
- 否 → 進入 Step 3

**Step 3 · 查 data_limitations** — 該分析類型是否被 `missing_dimensions` 或
`not_supported_analysis` 明文列為不支援?
- 是 → 進入【最後檢查】
- 否 → 即使欄位缺,也嘗試走計畫(讓使用者看到下游錯誤而非錯拒絕)

🛑【拒絕前的最後檢查】(必做,否則寧可走計畫):
你打算引用的 `<missing_dimension>` / `<not_supported_analysis>` 項目,是否真的**對應**
到 query 中**明確提及**的需求?
- ✅ 一致(query 真的要算 trend / amount / reviewer productivity 等)→ `[REFUSE]`
- ❌ 不一致(query 沒有要算那個東西,只是引用列表第一個項目湊理由)→
  **撤回拒絕,走計畫**

舉例:
- query「員工 H/C 圓餅圖」+ 引用「No application date」→ ❌ 不一致(query 沒要時間軸,
  只是要 pie chart,application date 跟畫 pie chart 無關)→ 走計畫
- query「過去三個月趨勢」+ 引用「No application date」→ ✅ 一致 → `[REFUSE]`

【判斷練習(以當前 metadata 為準,LLM 自行推理)】

範例 1(REFUSE 經典 — 時間/金額類缺欄位):
   「我想看過去三個月的申請趨勢」(或「平均申請金額」)
- Step 1:需要時間維度(或金額/價格欄位)
- Step 2:查 schema → 沒有對應欄位
- Step 3:`data_limitations.not_supported_analysis` 含 "trend"/"time"(或金額) → 是
- 最後檢查:query 確實要 trend / 金額,引用一致 → ✅
- ❌ `[REFUSE]`

範例 2(反例 · 絕對不可拒絕):
   「請依照 <實體列表>,計算 <某指標>,並以圓餅圖呈現」
- Step 1:需要該實體維度 + 該指標欄位。
  「圓餅圖」是圖型詞 → 鐵律已交代:**不參與判斷**。
- Step 2:兩個欄位都在 schema → ✅ 直接走計畫,Step 3 跳過
- 即使有人「直覺」想引用 missing dimension(例 `No application date`)來拒絕 →
  最後檢查會發現 query 根本沒提「時間/趨勢/月份」,引用不一致 → **撤回拒絕,走計畫**

⚠️ 注意:**今天的 metadata 可能下個月變**(domain expert 新增欄位後,以前不支援的分析變成支援)。
你的推理必須**完全基於 prompt 上方提供的當前 metadata 內容**,不要對某 domain 預設「永遠不支援 X」。

【拒絕回覆格式】(僅在 Step 3 + 最後檢查皆通過後使用):
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
**C. 視覺化建議:** 圖型選擇與理由。
   - 類別數 ≤ 7 且 query 明確點名 pie chart → 走 pie 沒問題,不要否決
   - 類別數 > 7 → 建議改 bar(可讀性較好),但仍走計畫不拒絕
   - 「dashboard / 執行摘要」場景 → 表格 + KPI 卡片
   ⚠️ 「pie chart 適不適合」是視覺化建議,**不是拒絕理由**;Step 3 已通過就一律走計畫。"""

    # --------------------------------------------------------
    # v0.12.0+ Phase 0 plan prompt for Upload Workspace
    # 跟原版差別只在「A 段:資料獲取」描述為 Pandas filter,不是 MongoDB pipeline
    # --------------------------------------------------------
    def _inline_phase_0_plan_upload_prompt(self) -> str:
        """v0.12.0+: Upload-driven Phase 0 plan prompt。

        關鍵差異 vs `_inline_phase_0_plan_prompt`:
        - A 段(資料獲取)描述為「從 source_df 用 Pandas filter 取 raw_df」
          不是「從 MongoDB collection aggregate」
        - 強調 source_df 已載入、欄位由 dynamic metadata 定義
        - 禁止 import / 外部 IO / 新增不存在欄位
        其他段落(Domain Knowledge / refuse 守則 / B/C 段)同 schema-driven 版本。
        """
        return f"""你是專業的 AI 商業智慧助理。請以上方 Domain Knowledge 為唯一依據規劃分析。
此 dataset 是使用者上傳的檔案,**不在 MongoDB 中**:
- 若 Domain Knowledge 的 `# Collections & 欄位定義` 區段**只有 1 張表**,
  該表已載入為 Pandas DataFrame `source_df`。
- 若**有多張表**(multi-sheet workbook,v0.18+),每張 sheet 已分別載入為
  `source_dfs[<table_id>]` dict。`source_df` 仍可用,等於第一張 sheet
  (向下相容)。table_id 列在 Collections 區段。
Phase A 不產生 MongoDB pipeline,而是產生 Pandas filter / selection code。

{self.domain_knowledge}

### 🌟 META 結構性問題 — 直接從 Domain Knowledge 回答,不用走 Phase A/B/C
若使用者問的是 dataset 的**結構** — 例:
- 「列出有哪些 sheet / table」
- 「每張表 row count 與可能主鍵」
- 「欄位類型 / schema」
- 「dataset 描述」

→ Domain Knowledge 已有完整答案(`# Collections & 欄位定義` 區段列出
所有 sheet + row 數 + primary_key + 欄位類型 + grain)。請**直接在 A 段
text 中列出答案**(不需要寫 Pandas code,Phase A 可以是 `raw_df = source_df.copy()`
作 placeholder)。Phase B/C 亦可標示「無需處理 — 此為結構性問題」。

### 任務說明
請把使用者問題拆解成三個小段,**用 markdown 三層標題包**。

**A. 資料獲取 (Phase A · Pandas filter):**
從 `source_df`(單表)或 `source_dfs['<table_id>']`(多表)取得分析所需的明細列。
- 只能 `filter` / `selection`(`source_df[...]` / `.loc[...]` / `.query(...)`)
- 多表跨 sheet:**只有 metadata.relationships 已 confirmed 的關聯**才可 merge
  (`source_dfs['A'].merge(source_dfs['B'], on='<confirmed_key>')`);未 confirmed
  的關聯 / 同名但不同語意的欄位不可 join
- **嚴禁** `groupby` / `agg` / `apply` 派生欄 — 那是 Phase B 的工作
- **嚴禁** 新增 raw 沒有的欄位(`source_df['new_col'] = ...` 是 Phase B)
- **嚴禁** `import` / `open` / `read_csv` / `os` / `subprocess` / 任何外部 IO
- 變數名鎖死:輸出必須 `raw_df = ...` 之類
- 若使用者問題明確列出實體值(例「Apparel 類別」「2024 年」),A 段必須帶上對應 filter

**B. 資料處理 (Phase B · Pandas):**
從 `raw_df` 計算 KPI、aggregate、轉成最終 `Q` DataFrame 給視覺化用。

**C. 視覺化建議 (Phase C · ECharts/Plotly):**
描述圖表類型 / 維度配置。
- 類別數 ≤ 7 且 query 明確點名 pie chart → 走 pie 沒問題
- 類別數 > 7 → 建議改 bar
- 「dashboard / 執行摘要」場景 → 表格 + KPI 卡片

### 拒絕協定 (Schema-Driven Refusal)
若使用者問題觸犯 Domain Knowledge 中「資料限制」(missing_dimensions /
not_supported_analysis),**第一個字元就輸出 `[REFUSE]`**,後面接說明:
- 例:`[REFUSE] 此資料集無日期欄位,無法做趨勢分析。建議改看靜態分佈。`
不在限制清單內的問題,即使資料品質有疑慮,也應該嘗試規劃 — Phase A/B/C 失敗會被
retry 機制接住,**Phase 0 不該預先拒絕**。

⚠️ 「pie chart 適不適合」是視覺化建議,**不是拒絕理由**;若 query 通過資料限制檢查,一律走計畫。"""

    # --------------------------------------------------------
    # v0.12.0+ Phase A · Pandas extraction(Upload Workspace)
    # 對應 schema-driven 的 generate_pipeline,但產 Pandas code 而非 MongoDB JSON。
    # 既有 generate_pipeline 完全不動,byte-equal 凍結條款不受影響。
    # --------------------------------------------------------
    def generate_pandas_extraction(
        self,
        query: str,
        plan_text: str = "",
        source_columns: list | None = None,
        source_df_sample: str = "",
        previous_code: str = "",
        previous_error: str = "",
        tables_info: dict[str, list[str]] | None = None,
    ) -> str:
        """產 Pandas filter / selection code,從 `source_df` 取出 `raw_df`。

        Args:
            query: 使用者原問題
            plan_text: Phase 0 plan output(用於提示)
            source_columns: source_df 實際欄位 list(鎖死)
            source_df_sample: source_df 前幾列 markdown 樣本
            previous_code / previous_error: retry 時的回饋
            tables_info: v0.18 M4 Tier B · 多表時的 {table_id: cols} dict;
                None 時走單表 source_df 路徑(向下相容)

        Returns:
            Python code 字串(無 ``` 圍欄,直接 exec)。caller 應在 namespace 內
            提供 `pd` / `np` / `source_df`(+ `source_dfs` for multi-table),
            exec 後檢查 `raw_df` 存在。
        """
        # v0.18 M4 Tier B · multi-table info block(只在多表時加入)
        multi_table_block = ""
        if tables_info and len(tables_info) > 1:
            tables_md = "\n".join(
                f"  - `source_dfs['{tid}']` · {len(cols)} 欄: {cols}"
                for tid, cols in tables_info.items()
            )
            multi_table_block = (
                f"\n\n### ⭐ 多表 workbook(v0.18 M4 Tier B)\n"
                f"本資料集為多 sheet workbook,所有 sheet 都已載入為 "
                f"`source_dfs` dict(以 table_id 索引):\n{tables_md}\n\n"
                f"- 單表分析 → `raw_df = source_dfs['<table_id>'][filter]`\n"
                f"- 跨表 join → `raw_df = source_dfs['A'].merge(source_dfs['B'], "
                f"on='<key>')`(只有 metadata.relationships 已 confirmed 的 "
                f"關聯才允許 merge)\n"
                f"- 單表 backward-compat:`source_df` 仍等於第一個 sheet,既有 "
                f"`raw_df = source_df[...]` 寫法仍可用"
            )

        cols_info = (
            f"`source_df` 已載入。實際欄位(鎖死,不可亂改名): {source_columns}"
            if source_columns else "`source_df` 已載入,欄位未知。"
        )
        if source_df_sample:
            cols_info += (
                "\n\n### source_df 實際前 3 列樣本 (你必須以此為準,不要憑想像猜測):\n"
                f"{source_df_sample}\n\n"
                "⚠️ 上面沒列出的欄位,絕對禁止引用。"
            )
        cols_info += multi_table_block

        system_prompt = f"""你是資料篩選工程師,負責 Upload Workspace 的【A. 資料獲取】。
{cols_info}

{self.domain_knowledge}

### 任務
從 `source_df` 取得分析所需的明細,輸出 `raw_df`(Pandas DataFrame)。
Phase B 會收 `raw_df` 做後續聚合 / KPI 計算。

### 實作守則 (CRITICAL FATAL RULES):
1. 🎯【變數鎖死】最後必須有 `raw_df = ...`。Phase B 找不到 raw_df 就炸。
2. 🚫【禁止 import】sandbox 只給 `pd`(pandas)、`np`(numpy)、`source_df`。
   絕對禁止 `import xxx` / `from xxx import` — 寫了會直接拒絕執行。
3. 🚫【禁止外部 IO】禁止 `open`、`read_csv`、`read_excel`、`os`、`subprocess`、
   `requests`、`socket`、`eval`、`exec`、`__import__`。
4. 🚫【禁止派生欄位】絕對禁止 `raw_df['new_col'] = ...` 或 `.assign(new=...)`。
   新欄位是 Phase B 的工作;A 段只能 **filter** / **select column subset**。
5. 🚫【禁止聚合】絕對禁止 `groupby`、`agg`、`pivot`、`merge`。同上,留給 Phase B。
6. ✅【允許的操作】
   - row filtering: `source_df[source_df['col']=='X']` / `.loc[...]` / `.query("...")`
   - column subset(可選): `raw_df = source_df[['col_a', 'col_b']]`
   - 多條件: `source_df[(source_df['a']=='X') & (source_df['b']>10)]`
7. ✅【欄名鎖死】只能用 `source_columns` 中真實存在的欄位。
8. 📌【Plan A 段】請嚴格遵照 Phase 0 plan 中 A 段提到的 filter 條件。

### 輸出範例
```python
# 範例 1:單一條件 filter
raw_df = source_df[source_df['<dim_col>'] == '<value>']

# 範例 2:多條件 + 欄位 subset
mask = (source_df['<col_a>'] == '<v1>') & (source_df['<col_b>'].notna())
raw_df = source_df.loc[mask, ['<col_a>', '<col_b>', '<col_c>']]

# 範例 3:無 filter 直接全帶(若 plan 沒列任何 filter)
raw_df = source_df.copy()
```

請只輸出 Python code,不要前言不要 markdown。"""

        user_msg = f"需求:{query}\n\n計畫:{plan_text}"
        user_msg += self._format_retry_hint(
            previous_code, previous_error,
            phase="pandas_extraction",
        )

        _samp = self._resolve_phase_sampling(
            "pipeline",   # 同 Phase A 路徑用 pipeline profile
            is_retry=bool(previous_error),
            fallback_temp=(0.15 if previous_error else self.default_temperature),
        )
        raw = self._call_llm(
            [{"role": "system", "content": system_prompt},
             {"role": "user", "content": user_msg}],
            phase="pandas_extraction",
            **_samp,
        )
        return self._strip_code_fence(raw, lang="python")

    # --------------------------------------------------------
    # Phase A: MongoDB pipeline
    # --------------------------------------------------------
    def generate_pipeline(self, query, plan_text="",
                          previous_code: str = "", previous_error: str = ""):
        """
        產 MongoDB pipeline JSON。

        v0.3.6+ 改善:
        - 透過 `extract_json_block` 防衛性 parsing,容忍 LLM 加 preamble / code fence
        - 加 retry loop:JSON parse 失敗時帶錯誤訊息重生(最多 2 次,共 3 attempts)
        """
        import json as _json
        self._last_query = query
        system_prompt = self._render_phase_a_pipeline_prompt()
        user_msg = f"需求:{query}\n計畫:{plan_text}"
        user_msg += self._format_retry_hint(previous_code, previous_error,
                                              phase="pipeline")

        last_raw = ""
        last_err = ""
        for attempt in range(3):
            # v0.10.2:retry 時 temp 抬高打破 LLM stuck pattern(temp=0 deterministic
            # 會讓同個 prompt 連續產同樣錯誤)。attempt 1 維持 default 求穩。
            # v0.10.6:改走 profile,reasoning_distilled coding base=0.6 / retry=0.75
            _samp = self._resolve_phase_sampling(
                "pipeline", is_retry=(attempt > 0),
                fallback_temp=(0.0 if attempt == 0 else 0.15),
            )
            raw = self._call_llm(
                [{"role": "system", "content": system_prompt},
                 {"role": "user", "content": user_msg}],
                phase="pipeline",
                **_samp,
            )
            last_raw = raw
            # 第一步:strip code fence
            stripped = self._strip_code_fence(raw, lang="json")
            # 第二步:defensive extract balanced {...} block
            extracted = extract_json_block(stripped)
            # 第三步:驗證能 parse
            try:
                _json.loads(extracted)
                # OK,回傳清乾淨的 JSON 字串給上層
                return extracted
            except _json.JSONDecodeError as e:
                last_err = (
                    f"JSONDecodeError: {e}\n"
                    f"你回覆的前 200 字元:\n{stripped[:200]}"
                )
                # 第二三次 attempt:把錯誤訊息 + 嚴格指令塞進 user_msg
                if attempt < 2:
                    user_msg = (
                        f"需求:{query}\n計畫:{plan_text}\n\n"
                        f"### 🔁 自我修正提示(JSON parse 失敗)\n"
                        f"你上次回覆無法被 `json.loads()` 解析。錯誤:\n"
                        f"```\n{last_err}\n```\n\n"
                        f"⚠️【強制要求】請只輸出純 JSON,**第一個字元必須是 `{{`**。\n"
                        f"- 不要寫「根據您的需求...」「以下是...」之類前言\n"
                        f"- 不要寫 `### A. 資料獲取:` 之類 markdown header\n"
                        f"- 不要包 ```json fence(雖然系統會 strip,但別依賴)\n"
                        f"- JSON 必須含 `start_collection` (string) 與 `pipeline` (array) 兩 key\n"
                    )
        # 3 attempts 都失敗 — 回傳最後一次原文(維持 v0.3.5 行為,讓下游報錯)
        return last_raw

    def _render_phase_a_pipeline_prompt(self) -> str:
        """產生 Phase A pipeline system prompt(repo → inline fallback)。"""
        if self.prompt_repo is not None:
            try:
                # v0.16.0+ M6.3 fix:anti_pattern 該只取 phase_a 的
                rag_kwargs = self._retrieve_rag_slots(
                    "phase_a_pipeline",
                    extra_filters={"applies_to_phase": "phase_a"},
                )
                return self.prompt_repo.render(
                    "phase_a_pipeline",
                    domain=self.domain,
                    domain_knowledge=self.domain_knowledge,
                    **rag_kwargs,
                )
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(
                    f"Phase A prompt repo render 失敗,fallback to inline: {e}"
                )
        return self._inline_phase_a_pipeline_prompt()

    def _inline_phase_a_pipeline_prompt(self) -> str:
        """v0.3.6+ inline f-string 副本(同步 embedded 模板更新)。"""
        return f"""你是精通 MongoDB 的資料庫工程師,負責【A. 資料獲取】。
{self.domain_knowledge}

### 實作守則 (CRITICAL RULES):
1. 🚨【輸出格式】(CRITICAL FATAL — 最容易出錯)
   你的回覆**第一個字元必須是 `{{`**,最後一個字元必須是 `}}`。
   除 JSON 外不要包含任何說明文字、markdown header、code fence。

   JSON 必須含兩個 top-level keys:
   - `"start_collection"`: 字串(主表名)
   - `"pipeline"`: 陣列(aggregation stages)

   ❌ 絕對禁止下列前綴 / 後綴(下游 `json.loads()` 會炸):
   - 「根據您的需求,以下是 ...」
   - 「以下是符合要求的 JSON ...」
   - 「### A. 資料獲取:」 / 「## Phase A」 之類 markdown header
   - ` ```json ` / ` ``` ` code fence wrapper
   - 結尾「以上即為完整 pipeline」之類說明

   ✅ 正確結構就是直接 JSON object,從 `{{` 開始,以 `}}` 結束。
2. 🔗【關聯鐵律】當 KPI 公式需要其他 collection 的欄位 (參照上方 relationships),
   必須 `$lookup` 該表並緊接 `$unwind` (使用 `preserveNullAndEmptyArrays: true`)。
3. 🚫【禁止寫入】禁止 `$out`、`$merge`。
4. 🚫【嚴禁在 DB 端聚合】(CRITICAL FATAL) 任務是撈「明細」交給 Pandas。
   禁止 `$group`、`$count`、`$sort`、`$limit`,只能用 `$match`、`$lookup`、`$unwind`、`$project`。
5. 🚫【禁止在 DB 端做派生欄位】(CRITICAL FATAL)
   `$project` / `$addFields` / `$set` 內【絕對禁止】出現以下 operator:
   - 條件類:`$cond` / `$switch` / `$ifNull`
   - 算術類:`$divide` / `$multiply` / `$add` / `$subtract` / `$mod` / `$round`
   - 字串/聚合類:`$concat` / `$sum`(`$project` 內)

   ❌ 反例:`{{"$project": {{"is_returned": {{"$cond": [...]}}, "rate": {{"$divide": [...]}}}}}}`
   ✅ 正解:`$project` 只保留原始欄位,讓 Phase B 用 pandas 算:`Q['rate'] = Q['<num>'] / Q['<den>']`

   口訣:**「Phase A 撈,Phase B 算」** — 凡是「新名字 = 某個運算」就是派生,留給 Phase B。
   違規會被 sanitize_pipeline 自動移除 + test 標 fail。

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
                                  previous_code: str = "", previous_error: str = "",
                                  tables_info: dict[str, list[str]] | None = None):
        self._last_query = query   # v0.16.0+ M6.3:給 _retrieve_rag_slots 用
        cols_info = (
            f"目前 raw_df 的欄位 (鎖死,不可亂改名): {available_columns}"
            if available_columns else "欄位未知。"
        )
        # v0.18 M4 Tier B · 多表 workbook 時 Phase B 也可存取其他 sheet
        # (透過 source_dfs[<table_id>])。Phase A 焦點是 filter/select,
        # Phase B 焦點是 preprocess / agg / derive,但 META 問題或補充查找
        # 時 Phase B 可能需要從原始 sheets 撈資訊。
        if tables_info and len(tables_info) > 1:
            tables_md = "\n".join(
                f"  - `source_dfs['{tid}']` · {len(cols)} 欄: {cols}"
                for tid, cols in tables_info.items()
            )
            cols_info += (
                "\n\n### ⭐ 多表 workbook(v0.18 M4 Tier B · Phase B 也可用)\n"
                f"除了 `raw_df`(來自 Phase A),你也可存取原始 sheets:\n{tables_md}\n\n"
                "- `source_df` = 第一張 sheet(向下相容)\n"
                "- META 問題(列出 sheet / row count / schema)→ 用 `source_dfs` "
                "建一個 summary Q dict:\n"
                "  `Q = pd.DataFrame([{'table': k, 'rows': len(v)} "
                "for k, v in source_dfs.items()])`\n"
                "- 跨表 join 應在 Phase A 完成,Phase B 收 raw_df 即可\n"
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

        # v0.6.0:偵測 preprocess intent,只注入相關 skeleton(prompt size -40%)
        intent = _detect_preprocess_intent(
            query, dashboard_hint=dashboard_hint, metadata=self.task_metadata,
        )
        system_prompt = self._render_phase_b_preprocess_prompt(
            cols_info=cols_info,
            dashboard_block=dashboard_block,
            intent=intent,
        )
        user_msg = f"需求:{query}\n計畫:{plan_text}"
        user_msg += self._format_retry_hint(
            previous_code, previous_error,
            cheatsheet=PANDAS_ANTIPATTERN_CHEATSHEET,
            phase="preprocess",
        )
        # v0.10.2:retry 時 temp 抬高打破 stuck pattern
        # v0.10.6:走 profile,reasoning_distilled coding base=0.6 / retry=0.75
        _samp = self._resolve_phase_sampling(
            "preprocess", is_retry=bool(previous_error),
            fallback_temp=(0.15 if previous_error else self.default_temperature),
        )
        raw = self._call_llm(
            [{"role": "system", "content": system_prompt},
             {"role": "user", "content": user_msg}],
            phase="preprocess",
            **_samp,
        )
        return self._strip_code_fence(raw, lang="python")

    def _render_phase_b_preprocess_prompt(self, cols_info: str,
                                            dashboard_block: str = "",
                                            intent: str = "simple_groupby") -> str:
        """產生 Phase B preprocess system prompt。

        v0.6.0 變更(per Option A 設計案):
        - 走 embedded_prompts.compose_phase_b_prompt_modular(intent)
        - **跳過 DB repo path**(repo 仍有 v0.4.x monolithic 版,v0.6.0 不用它)
        - DB migration 留 v0.6.1

        為什麼跳過 repo:repo 的 `phase_b_preprocess` 是 v0.4.x ~9.7K 整塊,
        和新 modular 不相容。v0.6.0 先讓 inline 走 modular 拿到 -40% prompt size
        的好處,production migration 留 v0.6.1。
        """
        from embedded_prompts import compose_phase_b_prompt_modular
        # v0.16.0+ M6.3:RAG slots(anti_pattern + few_shot)
        # anti_pattern 該只取 phase_b 的(避免 Phase A/C anti-pattern 噪音)
        rag_kwargs = self._retrieve_rag_slots(
            "phase_b_preprocess",
            extra_filters={"applies_to_phase": "phase_b"},
        )
        return compose_phase_b_prompt_modular(
            intent=intent,
            cols_info=cols_info,
            domain_knowledge=self.domain_knowledge,
            dashboard_block=dashboard_block,
            rag_anti_pattern=rag_kwargs.get("rag_anti_pattern", ""),
            rag_few_shot=rag_kwargs.get("rag_few_shot", ""),
        )

    def _inline_phase_b_preprocess_prompt(self, cols_info: str, dashboard_block: str) -> str:
        return f"""你是精通 Pandas 的資深資料工程師,負責【B. 資料處理】。
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
        user_msg += self._format_retry_hint(previous_code, previous_error, phase="plotly")
        # v0.10.2:retry 時 temp 抬高打破 stuck pattern
        # v0.10.6:走 profile,reasoning_distilled coding base=0.6 / retry=0.75
        _samp = self._resolve_phase_sampling(
            "plotly", is_retry=bool(previous_error),
            fallback_temp=(0.15 if previous_error else self.default_temperature),
        )
        raw = self._call_llm(
            [{"role": "system", "content": system_prompt},
             {"role": "user", "content": user_msg}],
            phase="plotly",
            **_samp,
        )
        return self._strip_code_fence(raw, lang="python")

    # --------------------------------------------------------
    # Phase C (alt): ECharts option dict
    # --------------------------------------------------------
    def generate_echarts_option(self, query, plan_text="", q_columns=None,
                                 previous_code: str = "", previous_error: str = ""):
        """產生 ECharts 5 option Python dict literal,變數名 `option`。

        v0.5.0+:依 query 偵測 chart intent → 只注入相關 chart-specific block,
        prompt size 從 ~24K 降到 6-9K per call。
        """
        self._last_query = query   # v0.16.0+ M6.3:給 _retrieve_rag_slots 用
        cols_info = (
            f"`Q` 實際欄位 (THE ONLY SOURCE OF TRUTH): {q_columns}\n"
            "⚠️ 上面這份 q_columns 是 Phase B 實際產出的欄位。\n"
            "⚠️ 不論下方 Domain Knowledge 提到什麼 KPI 名稱,**你只能使用 q_columns 中的欄位**。\n"
            "⚠️ 若你想引用的 KPI 在 q_columns 中沒對應欄位,改用最接近的、或直接放棄該指標。"
            if q_columns else "`Q` 欄位未知。"
        )
        intent = _detect_chart_intent(query)
        system_prompt = self._render_phase_c_echarts_prompt(cols_info, intent=intent)
        user_msg = f"需求:{query}\n計畫:{plan_text}"
        user_msg += self._format_retry_hint(previous_code, previous_error, phase="echarts")
        # v0.10.2:retry 時 temp 抬高打破 stuck pattern
        # v0.10.6:走 profile,reasoning_distilled coding base=0.6 / retry=0.75
        _samp = self._resolve_phase_sampling(
            "echarts", is_retry=bool(previous_error),
            fallback_temp=(0.15 if previous_error else self.default_temperature),
        )
        raw = self._call_llm(
            [{"role": "system", "content": system_prompt},
             {"role": "user", "content": user_msg}],
            phase="echarts",
            **_samp,
        )
        return self._strip_code_fence(raw, lang="python")

    def _render_phase_c_echarts_prompt(self, cols_info: str,
                                         intent: str = "bar_basic") -> str:
        """產生 Phase C echarts system prompt。

        v0.5.0 變更(per Option A 設計案):
        - 直接走 embedded_prompts.compose_phase_c_prompt_modular(intent)
        - **跳過 DB repo path**(repo 仍有 phase_c_echarts 24K 老版,但 v0.5.0 不用它)
        - DB 端 migration deferred 到 v0.5.1

        為什麼跳過 repo:repo 的 `phase_c_echarts` 是 v0.4.x 的 monolithic 24K,
        和新的 modular composition 不相容。v0.5.0 先讓 inline 走 modular 拿到 -60%
        prompt size 的好處,production migration 留 v0.5.1。
        """
        from embedded_prompts import compose_phase_c_prompt_modular
        # v0.16.0+ M6.3:RAG slots(chart_recipe + anti_pattern)
        # chart_recipe 由 intent filter,anti_pattern 由 applies_to_phase filter
        rag_kwargs = self._retrieve_rag_slots(
            "phase_c_chart",
            extra_filters={
                "intent": intent,
                "applies_to_phase": "phase_c",
            },
        )
        return compose_phase_c_prompt_modular(
            intent=intent,
            cols_info=cols_info,
            echarts_few_shot=self.echarts_few_shot,
            rag_chart_recipe=rag_kwargs.get("rag_chart_recipe", ""),
            rag_anti_pattern=rag_kwargs.get("rag_anti_pattern", ""),
        )

    def _inline_phase_c_echarts_prompt(self, cols_info: str) -> str:
        """v0.2.x inline f-string 副本(byte-equal 保持原 bug 行為)。"""
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

3.1 🚫【禁止「空殼 + dynamic fill」pattern】(CRITICAL FATAL — 100% stacked 場景常踩)
    很多 Phase C 失敗的根源是這個 anti-pattern:
    ```python
    option = {{
        "xAxis": {{"type": "category", "data": []}},   # ❌ 空殼
        "series": [],                                 # ❌ 空殼
        ...
    }}
    # 然後嘗試從 Q 重做 Phase B 該做的事:
    Q['status'] = ''
    Q.loc[(Q['review_status'] == 'Y') & ...]          # ⚠️ Q 已被 Phase B 處理過,
                                                       # 原始欄位很可能已不存在 → KeyError
    ```
    Q 是 Phase B 最終產出,**它已經是 plot-ready 形態**。`raw_df` 才有 `review_status`/
    `review_result` 那種底層欄位,**Phase C 拿不到 raw_df**。直接用 `Q['<col>']`(`<col>`
    是 `q_columns` 列出的)就好。

    ✅ 正解(直接用 q_columns,一次寫到位):
    ```python
    # 從 q_columns 確認 Q 是什麼形態,然後一次 option literal 寫完:
    option = {{
        "title": {{"text": "..."}},
        "xAxis": {{"type": "category", "data": Q['<dim_col>'].astype(str).tolist()}},
        "yAxis": {{"type": "value"}},
        "series": [
            {{"name": "<m>", "type": "bar", "stack": "s",
              "data": Q['<m_col>'].round(2).tolist()}}
            for <m_col>, <m> in [(<spec_a>), (<spec_b>), ...]
        ],
        ...
    }}
    ```
    口訣:**option literal 寫完就是完整的;不允許先空再填、不允許再算 KPI。**

3.3 🔢【numpy / pandas 型別必須 cast 為 Python native】(CRITICAL FATAL — 適用所有圖型)
    `Q['col'].iloc[i]` 或 `row['col']` 取出的 scalar 是 `numpy.int64` / `numpy.float64` /
    `numpy.str_`,直接放進 `option` dict 會在前端炸:
    ```
    BidiComponent Error: Cannot convert undefined or null to object
    ```
    這個雷不只 heatmap 會踩(rule 5.7H),pie / bar / line / scatter **任何圖型用
    list-comprehension 從 Q 取 value 都會踩**。

    ❌ 反例(會炸):
    ```python
    "data": [
        {{"value": Q['total_hc'].iloc[i],         # numpy.int64
         "name": Q['company_code'].iloc[i]}}      # numpy.object / str_
        for i in range(len(Q))
    ]
    ```

    ✅ 正解(三選一):
    ```python
    # 方案 A:每個 scalar 都顯式 cast(最安全)
    "data": [
        {{"value": int(Q['total_hc'].iloc[i]),
         "name": str(Q['company_code'].iloc[i])}}
        for i in range(len(Q))
    ]

    # 方案 B:用 .tolist() 把整 column 轉成 native list(Pandas 自動 cast)
    _vals = Q['total_hc'].tolist()
    _names = Q['company_code'].astype(str).tolist()
    "data": [{{"value": v, "name": n}} for v, n in zip(_vals, _names)]

    # 方案 C:用 .to_dict('records') 然後 cast(行多時最簡潔)
    "data": [
        {{"value": int(r['total_hc']), "name": str(r['company_code'])}}
        for r in Q.to_dict('records')
    ]
    ```

    口訣:**「進 option 的每個值都必須是 `int` / `float` / `str` / `bool` / `None`」**
    — 不確定就 `int()` / `float()` / `str()` 包一層。

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
    | 「**依據 X** 畫多條 bar,每條 bar 中呈現 Y」 | X | Y |
    | 「**用 X** 去畫多條 bar,每條 bar 中呈現 Y」 | X | Y |
    | 「**用 X** 畫 stacked bar,內含 Y 的數量/占比」 | X | Y |
    | 「**各 X** 的 Y 占比 stacked」 | X | Y |
    | 「**按 X** 分組,看 Y 的分佈」 | X | Y |
    | 「**以 X 為軸**,呈現 Y」 | X | Y |
    | 「**每個 X** 內部的 Y 結構」 | X | Y |
    | 「**X 為 x 軸**,Y 為 stack」 | X | Y |

    口訣:**「依據 / 用 / 以 / 各 / 按 / 每個 / X 為 x 軸」後面接的維度 = xAxis**。
    Series(stack 內層)= 「呈現 / 內含 / 分佈」後面那個維度。

    ❌ 反例(transposed,常見錯):
    使用者問「**用 application_category** 畫多條 bar,每條 bar 中呈現 **company_code** 的數量」
    → LLM 寫 xAxis=[各 company], series=[各 category]  ❌ 反了!
    → 結果:每家公司柱裡堆疊類別,但使用者要的是「類別柱裡堆疊公司」

    ✅ 正解:xAxis 是「用」後面的那個維度(此例為 application_category)。
    series 是「呈現」後面的維度(此例為 company_code)。

    🔑【最關鍵判斷】「**用 / 依據 / 以 X 為**」介系詞後面 → 一定是 xAxis。
    不要被表面的「公司」「類別」字眼影響直覺。

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

5.9 🎯【雙軸 bar+line 強制路由】(CRITICAL — v0.4.2 新增)

    當 query **同時**出現以下兩類詞,**必須**走 dual-axis(bar + line + 兩個 yAxis):

    ✅ 觸發條件(下面【A 組】與【B 組】各至少一個):
    - 【A 組 · 絕對量】:**絕對量**、**件數**、**數量**、**總數**、**人數**、**count**
    - 【B 組 · 比率】:**比率**、**比例**、**佔比**、**通過率**、**退單率**、**達成率**、
      **rate**、**ratio**、**%**

    且 query 中還明示「**比較各 X**」、「**對比**」、「**同時看到**」、「**vs**」這類比較性副詞。

    ✅ 標準配方(以 case 01 為原型:「比較各公司的退單率與申請數,我想同時看到絕對量與比率」):
    ```python
    option = {{
        "title": {{"text": "各公司申請數 vs 退單率"}},
        "tooltip": {{"trigger": "axis", "axisPointer": {{"type": "shadow"}}}},
        "legend": {{"show": True, "top": 30}},
        "grid": {{"left": 60, "right": 60, "top": 70, "bottom": 40}},
        "xAxis": {{
            "type": "category",
            "data": Q['<entity_col>'].astype(str).tolist(),
        }},
        "yAxis": [
            {{"type": "value", "name": "<絕對量 axis 名>",
              "axisLabel": {{"formatter": "{{value}}"}}}},
            {{"type": "value", "name": "<比率 axis 名>",
              "min": 0, "max": 100,                         # 比率類軸建議鎖 0-100
              "axisLabel": {{"formatter": "{{value}}%"}}}},
        ],
        "series": [
            {{"name": "<絕對量名>", "type": "bar",
              "yAxisIndex": 0,                              # ⭐ bar 走左軸
              "data": Q['<count_col>'].tolist(),
              "label": {{"show": True, "position": "top", "formatter": "{{c}}"}}}},
            {{"name": "<比率名>", "type": "line",
              "yAxisIndex": 1,                              # ⭐ line 走右軸
              "data": (Q['<rate_col>'] * 100).round(2).tolist()
                       if Q['<rate_col>'].max() <= 1 else
                       Q['<rate_col>'].round(2).tolist(),
              "label": {{"show": True, "position": "top", "formatter": "{{c}}%"}}}},
        ],
    }}
    ```

    🚫 反例(這些路徑都【錯】):
    - ❌ 走 `_use_table` + `_kpi_cards`(KPI 卡片無法呈現「各公司之間的差異」)
    - ❌ 只畫 bar 或只畫 line(會丟掉一半資訊)
    - ❌ 把兩個 series 都塞同一個 yAxis(絕對量會把比率壓成貼地線)
    - ❌ yAxis 寫成 dict 而非 list(雙軸必須是 list of 2 dicts)

    口訣:**「比較 + 絕對 + 比率」三件齊 → 直接 bar+line 雙軸,別走 KPI、別走純表格。**

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
   - 查詢同時要「**絕對量**」+「**比率/率/比例**」→ **走 rule 5.9 雙軸 bar+line**,不要走 KPI 卡片
     (KPI 卡片只能呈現「全體單一數字」,無法呈現「各公司之間的對比」)

   【決策範例】
   - 「畫 KPI 一覽表」→ 走 _use_table ✅
   - 「dashboard 顯示總申請與完成率」→ 走 _use_table ✅
   - 「各公司核准與退件比較,哪家退件最多」→ **畫 sorted stacked bar**,**不走 _use_table** ❌
   - 「比較 AI 與人工的退件率」→ 畫 bar / grouped bar,不走 _use_table ❌
   - 「全公司 KPI 完整一覽:申請、完成、退件、AI 率、員工率」→ 走 _use_table ✅
   - 「**比較各公司的退單率與申請數,同時看到絕對量與比率**」→
     ❌ 不要走 `_use_table` + `_kpi_cards`(會變成 3 張總計卡,丟掉各公司差異)
     ✅ 走 **rule 5.9 雙軸 bar+line**(bar=申請數左軸、line=退單率右軸)

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

請只輸出 python code,不要前言不要說明。"""
        # 注入此 service 實例對應 domain 的 few-shot
        # (silent bug 保留:雙括號 replace 對單括號內容是 no-op,但保 byte-equal)
        # 結尾不要加 \n,對齊 Jinja2 keep_trailing_newline=False 預設行為(該模板尾端的 \n 會被 strip)
        return system_prompt.replace("{{ECHARTS_FEW_SHOT}}", self.echarts_few_shot)

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
        self._last_query = query
        system_prompt = self._render_phase_d_insight_prompt()
        user_msg = (
            f"使用者問題:{query}\n\n"
            f"分析計畫:\n{plan_text}\n\n"
            f"處理後資料表 (Q,前 30 列 markdown):\n{q_preview_md}\n\n"
            f"請產出商業洞察。"
        )
        # v0.10.6:走 profile,reasoning_distilled non-thinking base=0.7+pp=1.5
        _samp = self._resolve_phase_sampling("insight", fallback_temp=0.3)
        try:
            return {
                "status": "success",
                "message": self._call_llm(
                    [{"role": "system", "content": system_prompt},
                     {"role": "user", "content": user_msg}],
                    phase="insight",
                    **_samp,
                ),
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _render_phase_d_insight_prompt(self) -> str:
        """產生 Phase D insight system prompt(repo → inline fallback)。"""
        if self.prompt_repo is not None:
            try:
                rag_kwargs = self._retrieve_rag_slots("phase_d_insight")
                return self.prompt_repo.render(
                    "phase_d_insight",
                    domain=self.domain,
                    domain_knowledge=self.domain_knowledge,
                    **rag_kwargs,
                )
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(
                    f"Phase D prompt repo render 失敗,fallback to inline: {e}"
                )
        return self._inline_phase_d_insight_prompt()

    def _inline_phase_d_insight_prompt(self) -> str:
        return f"""你是資深商業分析師,負責撰寫【D. 商業洞察】。
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
