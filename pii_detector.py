"""
pii_detector.py — v0.14.1+ (M4b)

PII(Personally Identifiable Information)欄位偵測。對齊 spec §14.3:
偵測到 PII 欄位後,metadata 標 `semantic_role=pii`,LLM prompt 提示「不要
在 chart label / insight 中逐筆列出」。

# 支援偵測類型

| Type        | Heuristic                                                |
|-------------|---------------------------------------------------------|
| email       | RFC 5322 簡化 regex(`<local>@<domain>.<tld>`)         |
| phone       | 全形/半形數字串 7-15 碼,含 - / 空白 / 括號分隔        |
| national_id | TW 國民身分證(A123456789)/ 中國身分證 18 碼          |
| employee_id | 欄名含 employee/staff 等 + 高基數 string                |
| name_like   | 欄名含 name/contact 等(low confidence,user 確認)      |
| address     | 欄名含 address/addr/住址                                 |

# 用法

```python
from pii_detector import detect_pii_in_column

result = detect_pii_in_column(
    column_name="email",
    sample_values=["a@x.com", "b@y.com"],
    physical_type="string",
)
# {"is_pii": True, "pii_type": "email", "confidence": 0.95, "reason": "..."}
```

# Integration

`data_profiler.profile_column` 會在偵測完物理 type / warnings 後,呼叫
`detect_pii_in_column` 並把結果寫進 column profile 的 `pii_info` 子欄位。
Metadata generator 看到 `pii_info.is_pii=True` 會把 semantic_role 設為 `pii`
(蓋掉 rule-based 推論)。
"""

from __future__ import annotations

import re
from typing import Any

# ============================================================
# Regex patterns
# ============================================================
# RFC 5322 簡化版 — 抓常見 email
_EMAIL_RE = re.compile(
    r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
)

# 電話號碼 — 7-15 位數字,可含 +/-/空白/括號
# 至少 7 個數字,避免把 "20231215" 之類日期誤判
_PHONE_RE = re.compile(
    r"^[\+\(\s\-]*\d[\d\-\s\(\)]{6,18}\d$"
)

# 日期樣式(2025-01-15 / 2025/1/1)— 排除誤判為 phone
_DATE_LIKE_RE = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}$")

# TW 國民身分證:1 字母 + 9 數字
_TW_NATIONAL_ID_RE = re.compile(r"^[A-Z][12]\d{8}$")

# 中國 18 碼身分證:17 數字 + 1 數字/X
_CN_NATIONAL_ID_RE = re.compile(r"^\d{17}[\dXx]$")


# ============================================================
# Name hints
# ============================================================
_EMPLOYEE_ID_NAME_HINTS = (
    "employee_id", "emp_id", "staff_id", "user_id", "personnel_id",
    "員工編號", "員工_id", "工號",
)

_PHONE_NAME_HINTS = (
    "phone", "tel", "mobile", "cellphone", "電話", "手機", "聯絡電話",
)

_EMAIL_NAME_HINTS = ("email", "e_mail", "電郵", "電子郵件")

_NAME_NAME_HINTS = (
    "full_name", "first_name", "last_name", "given_name", "family_name",
    "name", "contact_name", "person_name",
    "姓名", "名字", "全名", "聯絡人",
)

_ADDRESS_NAME_HINTS = (
    "address", "addr", "street", "city", "postal", "zip",
    "住址", "地址", "戶籍",
)

_NATIONAL_ID_NAME_HINTS = (
    "national_id", "id_card", "identity", "ssn", "身分證", "身份證",
    "國民身分證",
)


# ============================================================
# Helper:check pattern 在 sample 上的 hit rate
# ============================================================
def _pattern_hit_rate(samples: list[Any], regex: re.Pattern,
                       exclude_regex: re.Pattern | None = None) -> float:
    """回傳 regex 在 sample 上的 hit rate(0-1)。空 sample 回 0。

    Args:
        exclude_regex:若提供,該 regex match 的 sample 不算 hit(用於排除
            date-like 字串被誤判為 phone)。
    """
    if not samples:
        return 0.0
    hits = 0
    n = 0
    for v in samples:
        if v is None:
            continue
        n += 1
        s = str(v).strip()
        if exclude_regex and exclude_regex.match(s):
            continue   # 視為不命中
        if regex.match(s):
            hits += 1
    return hits / n if n > 0 else 0.0


def _name_has(name: str, hints: tuple) -> bool:
    if not name:
        return False
    n = name.lower()
    return any(h in n for h in hints)


# ============================================================
# Main detector
# ============================================================
def detect_pii_in_column(
    column_name: str,
    sample_values: list[Any],
    physical_type: str = "string",
    name_hit_threshold: float = 0.7,
    pattern_hit_threshold: float = 0.6,
) -> dict[str, Any]:
    """偵測單一欄位是否為 PII,回判決 dict。

    Args:
        column_name: 欄位名
        sample_values: top-N + random-N 樣本(從 data_profiler.profile_column 拿)
        physical_type: 'string' | 'integer' | ...
        name_hit_threshold: 欄名 hint 命中時 confidence 下限
        pattern_hit_threshold: 樣本 regex match 比例下限視為 pii

    Returns:
        {
          "is_pii": bool,
          "pii_type": "email"|"phone"|"national_id"|"employee_id"|"name_like"|"address"|None,
          "confidence": float,        # 0-1
          "reason": str,              # 人讀
        }
    """
    name = column_name or ""

    # ============================================================
    # 1. Email — pattern + name 都可獨立觸發
    # ============================================================
    email_hit = _pattern_hit_rate(sample_values, _EMAIL_RE)
    name_email = _name_has(name, _EMAIL_NAME_HINTS)
    if email_hit >= pattern_hit_threshold:
        return _wrap(
            is_pii=True, pii_type="email",
            confidence=min(0.95, 0.7 + email_hit * 0.3),
            reason=f"sample {email_hit:.0%} 命中 email pattern",
        )
    if name_email and physical_type == "string":
        return _wrap(
            is_pii=True, pii_type="email",
            confidence=0.85,
            reason=f"欄名含 email 關鍵字",
        )

    # ============================================================
    # 2. National ID — 嚴格 regex,優先於 phone
    # ============================================================
    tw_hit = _pattern_hit_rate(sample_values, _TW_NATIONAL_ID_RE)
    cn_hit = _pattern_hit_rate(sample_values, _CN_NATIONAL_ID_RE)
    name_nid = _name_has(name, _NATIONAL_ID_NAME_HINTS)
    if tw_hit >= pattern_hit_threshold:
        return _wrap(True, "national_id", min(0.95, 0.7 + tw_hit * 0.3),
                      f"sample {tw_hit:.0%} 命中 TW national ID 格式")
    if cn_hit >= pattern_hit_threshold:
        return _wrap(True, "national_id", min(0.95, 0.7 + cn_hit * 0.3),
                      f"sample {cn_hit:.0%} 命中 CN national ID 格式")
    if name_nid:
        return _wrap(True, "national_id", 0.80,
                      f"欄名含 national_id 關鍵字")

    # ============================================================
    # 3. Phone — pattern + name(name 優先,因為 phone regex 容易誤判)
    # ============================================================
    name_phone = _name_has(name, _PHONE_NAME_HINTS)
    # Phone hit:排除 date-like 字串(2025-01-15 之類)
    phone_hit = _pattern_hit_rate(sample_values, _PHONE_RE,
                                    exclude_regex=_DATE_LIKE_RE)
    if name_phone and (phone_hit >= 0.3 or physical_type == "string"):
        # name 命中時,pattern 只需 30%(混格式也算)
        return _wrap(True, "phone", 0.85,
                      f"欄名含 phone 關鍵字 + sample {phone_hit:.0%} 像電話")
    if phone_hit >= 0.85:   # 純 pattern 觸發要求高一點(避免「20231215」誤判)
        return _wrap(True, "phone", 0.75,
                      f"sample {phone_hit:.0%} 命中 phone pattern")

    # ============================================================
    # 4. Employee ID — 強信號 name + high cardinality 推論
    # ============================================================
    if _name_has(name, _EMPLOYEE_ID_NAME_HINTS):
        return _wrap(True, "employee_id", 0.85,
                      f"欄名含 employee_id / staff_id 關鍵字")

    # ============================================================
    # 5. Name-like — 較弱信號,user 應確認
    # ============================================================
    if _name_has(name, _NAME_NAME_HINTS):
        return _wrap(True, "name_like", 0.65,
                      f"欄名含 name / contact 關鍵字(請使用者確認)")

    # ============================================================
    # 6. Address
    # ============================================================
    if _name_has(name, _ADDRESS_NAME_HINTS):
        return _wrap(True, "address", 0.80,
                      f"欄名含 address / addr / 住址 關鍵字")

    # 沒命中
    return _wrap(False, None, 0.0, "")


def _wrap(is_pii: bool, pii_type, confidence: float, reason: str) -> dict:
    return {
        "is_pii": bool(is_pii),
        "pii_type": pii_type,
        "confidence": round(float(confidence), 3),
        "reason": reason,
    }


# ============================================================
# Dataset-level summary
# ============================================================
def summarize_pii_in_dataset(column_profiles: list[dict]) -> dict[str, Any]:
    """聚合 dataset 所有 column 的 PII 偵測結果。

    Args:
        column_profiles: 各 column 含 `pii_info` sub-dict(由 data_profiler 注入)

    Returns:
        {
          "has_pii": bool,
          "pii_columns": [{"name", "pii_type", "confidence", "reason"}, ...],
          "pii_count_by_type": {"email": 1, "phone": 2, ...},
        }
    """
    pii_cols = []
    counts: dict[str, int] = {}
    for prof in column_profiles:
        info = prof.get("pii_info") or {}
        if info.get("is_pii"):
            pii_cols.append({
                "name": prof.get("name"),
                "pii_type": info.get("pii_type"),
                "confidence": info.get("confidence"),
                "reason": info.get("reason"),
            })
            t = info.get("pii_type", "unknown")
            counts[t] = counts.get(t, 0) + 1
    return {
        "has_pii": bool(pii_cols),
        "pii_columns": pii_cols,
        "pii_count_by_type": counts,
    }
