"""
semantic_profiler.py — v0.12.0+

從 physical profile 推論每個欄位的 semantic_role / description / unit / aggregation,
讓上傳資料可以變成 GenBI agentic workflow 可消化的 metadata。

# 12 種 semantic_role(spec §9.2)

| Role                 | 用途                       | 可以 sum/avg? |
|----------------------|---------------------------|--------------|
| identifier           | ID / 編號 / 代碼            | 否(只能 count_distinct) |
| dimension            | 分組維度(類別)              | 否            |
| categorical_status   | 狀態碼 (R/D/X 之類)         | 否            |
| measure_count        | 計數 (件數 / 數量)           | 是 (sum) |
| measure_amount       | 金額                       | 是 (sum/avg) |
| measure_duration     | 時長 (leadtime, days)       | 是 (avg/median/p95) |
| measure_percentage   | 比率 / 百分比                | 否(只能 avg)  |
| date_dimension       | 日期(年月日)                | 否(可 group by) |
| datetime_dimension   | 日期時間                    | 否(可 group by) |
| text_description     | 描述性文字                  | 否(不建議分析) |
| boolean_flag         | Y/N 旗標                    | 否(可 sum 得到 count) |
| unknown              | 無法判斷                    | — (待人工確認) |

# 設計重點

- **Rule-based 為主**:對常見命名 + physical_type 組合 95% 能直接判出 role,confidence ≥0.85
- **LLM-assisted 補強**:rule-based confidence < 0.7 才打 LLM,降低成本
- **Domain-agnostic**:純規則,沒有 hard-code domain 字眼(KPI 名 / 欄位名前綴等)
- **保留 reasoning**:每個推論都帶 `reason` 給使用者看,他能判斷對不對

# 用法

```python
from semantic_profiler import profile_columns_semantic

# Pure rule-based(快,~ms)
roles = profile_columns_semantic(column_profiles, use_llm=False)

# LLM-assisted(慢,low-confidence 才打 LLM)
roles = profile_columns_semantic(
    column_profiles,
    use_llm=True,
    api_url=config.LLM_API_URL,
    api_key=config.LLM_API_KEY,
    model=config.LLM_MODEL,
)
```
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ============================================================
# Role enumeration + role property table
# ============================================================
SEMANTIC_ROLES = (
    "identifier",
    "dimension",
    "categorical_status",
    "measure_count",
    "measure_amount",
    "measure_duration",
    "measure_percentage",
    "date_dimension",
    "datetime_dimension",
    "text_description",
    "boolean_flag",
    "unknown",
)

# 每個 role 的預設推薦 / 不建議 aggregation(spec §9.2 表格的程式化版本)
ROLE_PROPERTIES: dict[str, dict[str, Any]] = {
    "identifier": {
        "default_aggregation": "no_agg",
        "recommended_use": ["label", "count_distinct"],
        "not_recommended_use": ["sum", "average"],
        "is_dimension": False,
        "is_measure": False,
        "is_identifier": True,
    },
    "dimension": {
        "default_aggregation": "no_agg",
        "recommended_use": ["group_by", "filter"],
        "not_recommended_use": ["sum", "average"],
        "is_dimension": True,
        "is_measure": False,
        "is_identifier": False,
    },
    "categorical_status": {
        "default_aggregation": "no_agg",
        "recommended_use": ["group_by", "filter"],
        "not_recommended_use": ["sum", "average"],
        "is_dimension": True,
        "is_measure": False,
        "is_identifier": False,
    },
    "measure_count": {
        "default_aggregation": "sum",
        "recommended_use": ["sum", "average", "median"],
        "not_recommended_use": [],
        "is_dimension": False,
        "is_measure": True,
        "is_identifier": False,
    },
    "measure_amount": {
        "default_aggregation": "sum",
        "recommended_use": ["sum", "average", "median", "p95"],
        "not_recommended_use": [],
        "is_dimension": False,
        "is_measure": True,
        "is_identifier": False,
    },
    "measure_duration": {
        "default_aggregation": "avg",
        "recommended_use": ["avg", "median", "min", "max", "p95", "histogram"],
        "not_recommended_use": ["sum"],   # sum 時長通常無意義
        "is_dimension": False,
        "is_measure": True,
        "is_identifier": False,
    },
    "measure_percentage": {
        "default_aggregation": "avg",
        "recommended_use": ["avg", "median"],
        "not_recommended_use": ["sum"],   # 百分比不可 sum
        "is_dimension": False,
        "is_measure": True,
        "is_identifier": False,
    },
    "date_dimension": {
        "default_aggregation": "no_agg",
        "recommended_use": ["group_by", "trend"],
        "not_recommended_use": ["sum", "average"],
        "is_dimension": True,
        "is_measure": False,
        "is_identifier": False,
    },
    "datetime_dimension": {
        "default_aggregation": "no_agg",
        "recommended_use": ["group_by", "trend"],
        "not_recommended_use": ["sum", "average"],
        "is_dimension": True,
        "is_measure": False,
        "is_identifier": False,
    },
    "text_description": {
        "default_aggregation": "no_agg",
        "recommended_use": ["display"],
        "not_recommended_use": ["sum", "average", "group_by"],
        "is_dimension": False,
        "is_measure": False,
        "is_identifier": False,
    },
    "boolean_flag": {
        "default_aggregation": "sum",   # sum bool 等於 count(True)
        "recommended_use": ["sum", "filter"],
        "not_recommended_use": ["average"],
        "is_dimension": False,
        "is_measure": True,
        "is_identifier": False,
    },
    "unknown": {
        "default_aggregation": "no_agg",
        "recommended_use": [],
        "not_recommended_use": [],
        "is_dimension": False,
        "is_measure": False,
        "is_identifier": False,
    },
}


# ============================================================
# Naming pattern keywords(domain-agnostic)
# ============================================================
# Identifier hints(欄名 + suspect_id warning 雙重命中視為 high-confidence)
_ID_NAME_HINTS = (
    "_id", "id_", "_no", "_code", "_key", "_uuid",
    "編號", "代碼", "代号", "編碼", "id",
)

# Amount hints
_AMOUNT_NAME_HINTS = (
    "amount", "price", "cost", "revenue", "sales", "value", "total",
    "金額", "價", "價格", "成本", "收入", "銷售", "營收", "總額",
)

# Count hints(含 CJK + EN compound)
_COUNT_NAME_HINTS = (
    "count", "qty", "num", "_n", "headcount", "size",
    "數量", "數", "件數", "筆數", "次數", "人數", "人次",
)

# Duration hints
_DURATION_NAME_HINTS = (
    "duration", "days", "hours", "minutes", "seconds",
    "leadtime", "lead_time", "lag", "delay", "elapsed",
    "天", "時", "分", "秒", "時長", "週期", "延遲", "leadtime",
)

# Percentage hints
_PERCENTAGE_NAME_HINTS = (
    "_pct", "pct", "rate", "ratio", "percent", "percentage", "%",
    "率", "百分比", "占比", "佔比",
)

# Date hints
_DATE_NAME_HINTS = (
    "date", "_dt", "day", "_at", "_on",
    "日期", "日", "月", "年",
)

_DATETIME_NAME_HINTS = (
    "datetime", "timestamp", "_at_utc", "_time", "_ts",
    "時間", "時刻",
)

# Text description hints
_TEXT_NAME_HINTS = (
    "name", "title", "desc", "description", "comment", "note", "remark",
    "summary", "abstract", "content", "message", "text",
    "名稱", "標題", "說明", "描述", "備註", "註",
)

# Boolean values
_BOOLEAN_VALUE_SETS = (
    frozenset(["Y", "N"]),
    frozenset(["YES", "NO"]),
    frozenset(["T", "F"]),
    frozenset(["TRUE", "FALSE"]),
    frozenset(["0", "1"]),
    frozenset([True, False]),
    frozenset([0, 1]),
    frozenset(["是", "否"]),
    frozenset(["有", "無"]),
)


# ============================================================
# Helper functions
# ============================================================
def _name_has_any(name: str, hints: tuple) -> Optional[str]:
    """檢查 name(已小寫)是否含任一 hint;命中回傳該 hint。"""
    if not name:
        return None
    nm = name.lower()
    for h in hints:
        if h.lower() in nm:
            return h
    return None


def _is_percent_range(min_v, max_v) -> str:
    """從 min/max 推測是否為百分比範圍。
    Returns: "ratio" (0-1) | "percent" (0-100) | "neither"。
    """
    try:
        mn = float(min_v) if min_v is not None else None
        mx = float(max_v) if max_v is not None else None
    except (TypeError, ValueError):
        return "neither"
    if mn is None or mx is None:
        return "neither"
    if -0.05 <= mn <= 1.05 and -0.05 <= mx <= 1.05:
        return "ratio"
    if -5 <= mn <= 110 and -5 <= mx <= 110:
        return "percent"
    return "neither"


def _is_boolean_values(sample_values: list) -> bool:
    """檢查 sample_values 是否落在已知 boolean value set。"""
    if not sample_values:
        return False
    vs = frozenset(
        v if isinstance(v, bool) else str(v).strip().upper()
        for v in sample_values
        if v is not None
    )
    return any(vs.issubset(bs) or bs.issubset(vs)
               for bs in _BOOLEAN_VALUE_SETS)


# ============================================================
# Rule-based profiler — main entry per column
# ============================================================
def infer_role_rule_based(col_profile: dict) -> dict:
    """從一個 column profile 推 semantic_role(純規則)。

    Args:
        col_profile: data_profiler.profile_column 的輸出

    Returns:
        dict:
        {
          "role": str,                # 12 種之一
          "confidence": float,        # 0-1
          "reason": str,              # 判斷依據(人讀)
          "description": str,         # 自動產生的中文 description(留給 user 修)
          "unit": str,                # "days" | "percent" | "ratio" | "" 等
          "rule_hits": list,          # 觸發了哪些 rules(debug 用)
        }
    """
    name = col_profile.get("name", "")
    name_lower = name.lower()
    phys = col_profile.get("physical_type", "unknown")
    warnings = col_profile.get("warnings") or []
    sample = col_profile.get("sample_values") or []
    distinct = col_profile.get("distinct_count")
    null_pct = col_profile.get("null_pct", 0)
    min_v = col_profile.get("min")
    max_v = col_profile.get("max")

    hits: list[str] = []
    role = "unknown"
    confidence = 0.4
    reason = ""
    unit = ""
    description = ""

    # ============================================================
    # 0. Datetime 物理型別 → 直接 date_dimension / datetime_dimension
    # ============================================================
    if phys == "datetime":
        # 偵測欄位精度(從 sample_values 是否含 time 部分)
        has_time = any(
            isinstance(v, str) and ("T" in v or " " in v.strip())
            for v in sample
        )
        if has_time or _name_has_any(name, _DATETIME_NAME_HINTS):
            role = "datetime_dimension"
        else:
            role = "date_dimension"
        confidence = 0.95
        reason = f"物理型別 = datetime"
        description = f"日期欄位 {name}"
        hits.append(f"physical_type={phys}")
        return _wrap(role, confidence, reason, description, unit, hits)

    # ============================================================
    # 1. Boolean 物理型別 / value pattern
    # ============================================================
    if phys == "boolean" or _is_boolean_values(sample):
        role = "boolean_flag"
        confidence = 0.90
        reason = "物理型別 boolean 或 sample 值為 Y/N、True/False、0/1"
        description = f"布林旗標 {name}"
        hits.append("boolean_values")
        return _wrap(role, confidence, reason, description, unit, hits)

    # ============================================================
    # 2. String type → identifier / dimension / status / text_description
    # ============================================================
    if phys == "string":
        # 2a. Identifier 強信號:suspect_id warning + 名字命中 ID hint
        id_name_hit = _name_has_any(name, _ID_NAME_HINTS)
        if "suspect_id" in warnings and id_name_hit:
            role = "identifier"
            confidence = 0.95
            reason = (f"suspect_id warning + 欄名含 `{id_name_hit}`,"
                      f"distinct={distinct} 接近 row count")
            description = f"識別碼 {name}"
            hits.extend(["suspect_id", f"id_name:{id_name_hit}"])
            return _wrap(role, confidence, reason, description, unit, hits)

        # 2b. 弱 identifier:high_cardinality + 名字命中 ID hint
        if id_name_hit and "high_cardinality" in warnings:
            role = "identifier"
            confidence = 0.85
            reason = f"high_cardinality + 欄名含 `{id_name_hit}`"
            description = f"識別碼 {name}"
            hits.extend(["high_cardinality", f"id_name:{id_name_hit}"])
            return _wrap(role, confidence, reason, description, unit, hits)

        # 2c. Date hints in string column(例:`hire_date` 是 string 但其實是日期)
        date_hit = _name_has_any(name, _DATE_NAME_HINTS)
        if date_hit:
            # 試著看 sample 是否像 ISO date
            sample_str = [str(v) for v in sample if v is not None][:5]
            iso_like = any(
                re.match(r"^\d{4}-\d{2}-\d{2}", s) for s in sample_str
            )
            if iso_like:
                role = "date_dimension"
                confidence = 0.85
                reason = f"欄名含 `{date_hit}` + sample 為 ISO date 樣式"
                description = f"日期欄位 {name}(string 型別,建議轉 datetime)"
                hits.append(f"date_name:{date_hit}")
                return _wrap(role, confidence, reason, description, unit, hits)

        # 2d. Text description:名字命中 text hint
        text_hit = _name_has_any(name, _TEXT_NAME_HINTS)
        if text_hit:
            role = "text_description"
            confidence = 0.80
            reason = f"欄名含 `{text_hit}`"
            description = f"描述文字 {name}"
            hits.append(f"text_name:{text_hit}")
            return _wrap(role, confidence, reason, description, unit, hits)

        # 2e. Categorical status:low cardinality (2-3 distinct)
        if "low_cardinality" in warnings or "all_same" in warnings:
            role = "categorical_status"
            confidence = 0.85 if "low_cardinality" in warnings else 0.60
            reason = (f"low_cardinality (distinct={distinct}) — "
                      f"sample: {sample[:5]}")
            description = f"狀態類別 {name}"
            hits.append("low_cardinality")
            return _wrap(role, confidence, reason, description, unit, hits)

        # 2f. dimension (medium cardinality)
        # distinct_count > LOW + 沒 high_cardinality(不像 ID)
        if (distinct is not None and distinct > 3
                and "high_cardinality" not in warnings):
            role = "dimension"
            confidence = 0.70
            reason = f"中等基數 string 欄(distinct={distinct})"
            description = f"分組維度 {name}"
            hits.append("medium_cardinality")
            return _wrap(role, confidence, reason, description, unit, hits)

        # 2g. high_cardinality string 無 ID hint → 可能是 dimension 或 text
        if "high_cardinality" in warnings:
            role = "text_description"
            confidence = 0.55
            reason = (f"high_cardinality 但無 ID hint,"
                      f"可能是 dimension 或描述文字 — 建議使用者確認")
            description = f"高基數字串欄 {name}"
            hits.append("high_cardinality_no_id")
            return _wrap(role, confidence, reason, description, unit, hits)

        # 2h. fallback for string
        role = "dimension"
        confidence = 0.50
        reason = "string 欄位,規則未明確匹配 — 預設 dimension,建議使用者確認"
        description = f"未知字串欄 {name}"
        hits.append("string_fallback")
        return _wrap(role, confidence, reason, description, unit, hits)

    # ============================================================
    # 3. Number / Integer → measure_count / amount / duration / percentage
    # ============================================================
    if phys in ("number", "integer"):
        # 3a. Percentage 強信號:名字含 rate/率 OR values 全 0-1
        pct_name_hit = _name_has_any(name, _PERCENTAGE_NAME_HINTS)
        pct_range = _is_percent_range(min_v, max_v)
        if pct_name_hit:
            role = "measure_percentage"
            if pct_range == "ratio":
                confidence = 0.95
                unit = "ratio"
                reason = (f"欄名含 `{pct_name_hit}` + values 在 0-1 範圍 "
                          f"(min={min_v}, max={max_v})")
            elif pct_range == "percent":
                confidence = 0.92
                unit = "percent"
                reason = (f"欄名含 `{pct_name_hit}` + values 在 0-100 範圍 "
                          f"(min={min_v}, max={max_v})")
            else:
                confidence = 0.55
                unit = "percent_uncertain"
                reason = (f"欄名含 `{pct_name_hit}` 但 values 超出 0-100 範圍 "
                          f"(min={min_v}, max={max_v}) — 建議檢查 unit")
            description = f"比率欄位 {name}"
            hits.extend([f"pct_name:{pct_name_hit}", f"value_range:{pct_range}"])
            return _wrap(role, confidence, reason, description, unit, hits)

        # 3b. Amount:名字含 amount / 金額
        amt_name_hit = _name_has_any(name, _AMOUNT_NAME_HINTS)
        if amt_name_hit:
            role = "measure_amount"
            confidence = 0.90
            reason = f"欄名含 `{amt_name_hit}`"
            description = f"金額欄位 {name}"
            unit = ""
            hits.append(f"amount_name:{amt_name_hit}")
            return _wrap(role, confidence, reason, description, unit, hits)

        # 3c. Duration:名字含 days / hours / leadtime / 天 / 時
        dur_name_hit = _name_has_any(name, _DURATION_NAME_HINTS)
        if dur_name_hit:
            role = "measure_duration"
            confidence = 0.90
            # 從 name 推 unit
            if any(k in name_lower for k in ("day", "天", "leadtime")):
                unit = "days"
            elif any(k in name_lower for k in ("hour", "時")):
                unit = "hours"
            elif any(k in name_lower for k in ("minute", "分")):
                unit = "minutes"
            elif any(k in name_lower for k in ("second", "秒")):
                unit = "seconds"
            else:
                unit = "duration"
            reason = f"欄名含 `{dur_name_hit}`,unit 推為 `{unit}`"
            description = f"時長欄位 {name}"
            hits.extend([f"duration_name:{dur_name_hit}", f"unit:{unit}"])
            return _wrap(role, confidence, reason, description, unit, hits)

        # 3d. Count:名字含 count / 數量 / 件
        cnt_name_hit = _name_has_any(name, _COUNT_NAME_HINTS)
        if cnt_name_hit:
            role = "measure_count"
            confidence = 0.85
            unit = "count"
            reason = f"欄名含 `{cnt_name_hit}`"
            description = f"計數欄位 {name}"
            hits.append(f"count_name:{cnt_name_hit}")
            return _wrap(role, confidence, reason, description, unit, hits)

        # 3e. Integer ID(numeric ID 場景):suspect_id + integer + 名字含 id
        id_name_hit = _name_has_any(name, _ID_NAME_HINTS)
        if "suspect_id" in warnings and id_name_hit:
            role = "identifier"
            confidence = 0.85
            reason = f"suspect_id + 欄名含 `{id_name_hit}`(numeric ID)"
            description = f"識別碼 {name}"
            hits.extend(["suspect_id", f"id_name:{id_name_hit}"])
            return _wrap(role, confidence, reason, description, unit, hits)

        # 3f. fallback for numeric:右偏 → 可能 amount;普通 → 可能 count
        # 規則不確定,confidence 給低,讓 LLM 補
        if "right_skewed" in warnings:
            role = "measure_amount"
            confidence = 0.55
            reason = "數值欄 + right_skewed warning,但無 amount name hint"
            description = f"數值欄 {name}(可能為金額,建議確認)"
            hits.append("right_skewed_no_name_hint")
            return _wrap(role, confidence, reason, description, unit, hits)

        # 3g. numeric default
        role = "measure_count"
        confidence = 0.45
        unit = ""
        reason = (f"數值欄位但無明確 hint — 預設 measure_count,建議使用者確認")
        description = f"數值欄位 {name}"
        hits.append("numeric_fallback")
        return _wrap(role, confidence, reason, description, unit, hits)

    # ============================================================
    # 4. Unknown physical type
    # ============================================================
    role = "unknown"
    confidence = 0.30
    reason = f"未支援的物理型別 `{phys}`"
    description = f"未知欄位 {name}"
    hits.append(f"unknown_type:{phys}")
    return _wrap(role, confidence, reason, description, unit, hits)


def _wrap(role, confidence, reason, description, unit, hits) -> dict:
    return {
        "role": role,
        "confidence": round(float(confidence), 3),
        "reason": reason,
        "description": description,
        "unit": unit,
        "rule_hits": hits,
    }


# ============================================================
# LLM-assisted refinement
# ============================================================
LLM_REFINE_THRESHOLD = 0.70  # confidence < 此值才打 LLM


_LLM_SEMANTIC_SYSTEM_PROMPT = """你是 dataset metadata 分析師。給定欄位的物理 profile,
判斷最合適的 semantic_role。

可選 role(只能選下列之一):
- identifier:ID / 編號 / 代碼,不可 sum / avg
- dimension:分組維度(類別、地區、產品線等)
- categorical_status:狀態碼(Y/N、PENDING、APPROVED 等)
- measure_count:可計數的數量(件數、筆數、人次)
- measure_amount:金額類數值
- measure_duration:時長(天、小時)— 含 leadtime / delay
- measure_percentage:比率類(率、百分比)
- date_dimension:日期欄位
- datetime_dimension:日期時間欄位
- text_description:描述性文字(名稱、備註、留言)
- boolean_flag:Y/N 旗標
- unknown:資訊不足,無法判斷

回應格式(嚴格 JSON,不要包 code fence):
{
  "role": "<role 之一>",
  "confidence": <0-1 float>,
  "description": "<該欄位的中文描述,15 字以內>",
  "unit": "<例:days, count, percent, ratio,沒有就空字串>",
  "reasoning": "<為何選此 role,30 字以內>"
}

判斷時優先考慮:
1. sample_values 內容(實際資料樣貌)
2. 欄名語意(英文或中文)
3. physical_type(string/number/datetime)
4. 統計特徵(distinct_count、null_pct、min/max)
5. warnings(suspect_id / right_skewed / low_cardinality 等)
"""


def _build_llm_user_prompt(col_profile: dict, rule_result: dict) -> str:
    """組 user message 給 LLM,只含 column 必要資訊。"""
    info = {
        "name": col_profile.get("name"),
        "physical_type": col_profile.get("physical_type"),
        "null_pct": col_profile.get("null_pct"),
        "distinct_count": col_profile.get("distinct_count"),
        "distinct_pct": col_profile.get("distinct_pct"),
        "sample_values": col_profile.get("sample_values", [])[:10],
        "warnings": col_profile.get("warnings", []),
    }
    # Numeric extras
    for k in ("min", "max", "mean", "median", "p95"):
        if col_profile.get(k) is not None:
            info[k] = col_profile[k]

    rule_guess = (
        f"\n\n規則引擎初步判斷:`{rule_result['role']}` "
        f"(confidence={rule_result['confidence']}),"
        f"理由:{rule_result['reason']}。請判斷此判斷是否合理或建議改成其他 role。"
    )
    return (
        f"欄位資訊:\n```json\n"
        f"{json.dumps(info, ensure_ascii=False, indent=2, default=str)}"
        f"\n```{rule_guess}"
    )


def refine_with_llm(
    col_profile: dict,
    rule_result: dict,
    api_url: str,
    api_key: str,
    model: str,
    timeout_s: float = 60.0,
) -> dict:
    """對 low-confidence column 打一次 LLM,refine semantic_role + description + unit。

    若 LLM 同意 rule_result,confidence 提升;若 LLM 提出不同 role,
    取 LLM 的結果但 confidence 標 medium(代表規則跟 LLM 不一致)。

    Args:
        col_profile: 該 column 的 physical profile
        rule_result: rule-based 判斷的結果(infer_role_rule_based 回傳)
        api_url, api_key, model, timeout_s: LLM 連線參數

    Returns:
        refined dict(與 infer_role_rule_based 同 schema,但加 `llm_used=True`)
        若 LLM 失敗,回傳原 rule_result(加 `llm_error` 欄位)
    """
    try:
        from openai import OpenAI
        client = OpenAI(
            base_url=api_url.replace("/chat/completions", ""),
            api_key=api_key,
            timeout=timeout_s,
        )
        messages = [
            {"role": "system", "content": _LLM_SEMANTIC_SYSTEM_PROMPT},
            {"role": "user", "content": _build_llm_user_prompt(col_profile, rule_result)},
        ]
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.0,
            max_tokens=400,
        )
        raw = response.choices[0].message.content or ""
        # Strip code fence + extract JSON
        s = raw.strip()
        if s.startswith("```"):
            nl = s.find("\n")
            if nl > 0:
                s = s[nl + 1:]
            if s.rstrip().endswith("```"):
                s = s.rstrip()[:-3].rstrip()
        # find first { and last }
        start = s.find("{")
        end = s.rfind("}")
        if start < 0 or end < start:
            raise ValueError(f"LLM 回應沒找到 JSON: {raw[:200]}")
        llm_json = json.loads(s[start:end + 1])

        llm_role = llm_json.get("role") or "unknown"
        if llm_role not in SEMANTIC_ROLES:
            llm_role = "unknown"
        llm_conf = float(llm_json.get("confidence", 0.6))
        llm_desc = str(llm_json.get("description") or rule_result["description"])[:80]
        llm_unit = str(llm_json.get("unit") or rule_result["unit"])[:30]
        llm_reasoning = str(llm_json.get("reasoning") or "")[:150]

        # 一致性合併:LLM 同意 rule → boost confidence
        agreement = (llm_role == rule_result["role"])
        if agreement:
            final_conf = max(llm_conf, rule_result["confidence"], 0.80)
            final_role = rule_result["role"]
            final_reason = (f"{rule_result['reason']} · "
                            f"LLM 同意此判斷({llm_reasoning})")
        else:
            # 不一致 → 取 LLM 結果但 confidence 偏中
            final_conf = min(max(llm_conf, 0.55), 0.80)
            final_role = llm_role
            final_reason = (f"規則初判 `{rule_result['role']}` 但 LLM 改判 "
                            f"`{llm_role}`({llm_reasoning})")

        return {
            "role": final_role,
            "confidence": round(final_conf, 3),
            "reason": final_reason,
            "description": llm_desc,
            "unit": llm_unit,
            "rule_hits": rule_result.get("rule_hits", []),
            "llm_used": True,
            "llm_agreement": agreement,
        }
    except Exception as e:
        logger.warning(f"refine_with_llm 失敗 for `{col_profile.get('name')}`: {e}")
        out = dict(rule_result)
        out["llm_used"] = False
        out["llm_error"] = str(e)[:100]
        return out


# ============================================================
# Public entry — profile entire table
# ============================================================
def profile_columns_semantic(
    column_profiles: list[dict],
    use_llm: bool = False,
    api_url: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    timeout_s: float = 60.0,
    llm_threshold: float = LLM_REFINE_THRESHOLD,
) -> list[dict]:
    """對 column profile list 全部跑 semantic inference。

    Args:
        column_profiles: data_profiler.profile_table(...)['columns'] 的內容
        use_llm: True 才會對 low-confidence 欄位打 LLM
        api_url/api_key/model/timeout_s: use_llm=True 時必傳
        llm_threshold: confidence 低於此值才送 LLM(預設 0.70)

    Returns:
        list,長度跟輸入相同,每個 dict 帶:
          role / confidence / reason / description / unit / rule_hits /
          (optional) llm_used / llm_agreement / llm_error
    """
    results = []
    for col_prof in column_profiles:
        rule = infer_role_rule_based(col_prof)
        if (use_llm and rule["confidence"] < llm_threshold
                and api_url and api_key and model):
            try:
                refined = refine_with_llm(
                    col_prof, rule,
                    api_url=api_url, api_key=api_key, model=model,
                    timeout_s=timeout_s,
                )
                results.append(refined)
                continue
            except Exception as e:
                logger.warning(
                    f"LLM refine failed for {col_prof.get('name')}: {e}"
                )
        results.append(rule)
    return results
