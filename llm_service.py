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
    color_palette = '"color": ["#5470c6", "#91cc75", "#fac858", "#ee6666", "#73c0de"],'

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
                 task_metadata: dict | None = None):
        """
        參數預設指向 Ollama (localhost:11434);
        若你在用 vLLM,把 api_url 改成 http://localhost:8000/v1/chat/completions、
        model_name 改成 vLLM 啟動時 --served-model-name 設定的值即可。

        timeout_s: 本地 thinking 模型首次推論可能 120-180s,給足。
        default_temperature: code-gen 任務建議 0.0,Plan/Insight 會在內部自行抬高。
        task_metadata: domain 描述 dict (schema/KPI/限制/recommended_charts)。
                       若 None,自動載入 tflex_task_metadata_agent_v3.TASK_METADATA。
                       換不同 domain 時傳入該 domain 的 metadata 即可,不必改本 module 的程式碼。
        """
        self.client = OpenAI(
            base_url=api_url.replace("/chat/completions", ""),
            api_key=api_key,
            timeout=timeout_s,
        )
        self.model_name = model_name
        self.default_temperature = default_temperature
        self.timeout_s = timeout_s

        # ── 載入並組裝 domain knowledge / few-shot ──
        if task_metadata is None:
            from tflex_task_metadata_agent_v3 import TASK_METADATA as _DEFAULT_META
            task_metadata = _DEFAULT_META
        self.task_metadata = task_metadata
        self.domain_knowledge = build_domain_knowledge(task_metadata)
        self.echarts_few_shot = build_echarts_few_shot(task_metadata)

        # ── Telemetry:每次 LLM call 的耗時與 token 用量 ──
        # 由外部測試框架在 case 開始前呼叫 reset_call_log(),結束時 get_call_summary()
        self.call_log: list[dict] = []

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
    def generate_plan(self, query):
        system_prompt = f"""你是專業的 AI 商業智慧助理。請以上方 Domain Knowledge 為唯一依據規劃分析。

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
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"需求:{query}\n請給出計畫:"},
        ]
        try:
            return {"status": "success", "message": self._call_llm(messages, temperature=0.2, phase="plan")}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    # --------------------------------------------------------
    # Phase A: MongoDB pipeline
    # --------------------------------------------------------
    def generate_pipeline(self, query, plan_text="",
                          previous_code: str = "", previous_error: str = ""):
        system_prompt = f"""你是精通 MongoDB 的資料庫工程師,負責【A. 資料獲取】。
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
        user_msg = f"需求:{query}\n計畫:{plan_text}"
        user_msg += self._format_retry_hint(previous_code, previous_error)
        raw = self._call_llm(
            [{"role": "system", "content": system_prompt},
             {"role": "user", "content": user_msg}],
            phase="pipeline",
        )
        return self._strip_code_fence(raw, lang="json")

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
9. 🚫【Series.first() 禁區】(CRITICAL — 常見幻覺) Series 物件**沒有 `.first()` 方法**!
   - ❌ `Q['hc'].first()` → AttributeError
   - ✅ `Q['hc'].iloc[0]` (取首列值)
   - ✅ `Q.groupby(...).agg(hc=('hc', 'first'))` (此處 'first' 是 agg function 字串,合法)
   - 一般取「每組第一筆」請用 `groupby(...).first()` (回傳 DataFrame,合法)。

9.5 🎯【100% Stacked / 占比分佈樣板】(CRITICAL — 常被誤解)
    當 query 含「**占比分佈**」、「**比例分佈**」、「**100% stacked**」、
    「**percentage stack**」、「占比 stacked bar」+「百分比」之類語義時,
    意思是「每組內各 sub-state 加總應為 100」(per-group 歸一化),**不是**直接顯示 raw count 再加 % 符號。

    正確做法:**Phase B 必須 per-group normalize**,Q 的數值已是 0-100 範圍:
    ```python
    # 例:每類別內各狀態占比(approved / returned / in_progress 加總=100)
    Q = raw_df.copy()
    Q['is_approved']    = (Q['review_status']=='Y') & (Q['review_result']=='Y')
    Q['is_returned']    = (Q['review_status']=='Y') & (Q['review_result']=='N')
    Q['is_in_progress'] = (Q['review_status']=='N')

    agg = Q.groupby('application_category').agg(
        approved=('is_approved', 'sum'),
        returned=('is_returned', 'sum'),
        in_progress=('is_in_progress', 'sum'),
    ).reset_index()

    # ⭐ per-row normalize 到 100
    agg['_total'] = agg['approved'] + agg['returned'] + agg['in_progress']
    agg['approved_pct']    = (agg['approved']    / agg['_total'] * 100).round(2)
    agg['returned_pct']    = (agg['returned']    / agg['_total'] * 100).round(2)
    agg['in_progress_pct'] = (agg['in_progress'] / agg['_total'] * 100).round(2)

    Q = agg.drop(columns=['_total'])  # 不需 _total 給下游
    ```
    這樣 Phase C 拿到的 *_pct 欄位已是 0-100 範圍,stacked 時每柱加總 = 100%。

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

5.6 📚【100% Stacked Bar 完整配方】(配合 Phase B 5.5 規則使用)
    當查詢含「占比分佈」「比例分佈」「100% stacked」+「百分比」時:
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
6. 🎁【色盤】使用 `color: ['#5470c6', '#91cc75', '#fac858', '#ee6666', '#73c0de', '#3ba272']`。
7. 📐【grid 留白】`grid: {{"left": 60, "right": 60, "top": 60, "bottom": 40}}` 起手。
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
