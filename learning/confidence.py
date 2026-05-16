"""
learning/confidence.py — Week 3 D1 (v0.8.4)

Pure Python 函式,計算 observation 的 confidence 分數與其 4 個 sub-component。

對齊 GenBI_v1.3_Self_Learning_MVP_Implementation_Spec.md §13 + §13.5。

# 公式
    confidence =
        0.40 * evidence_support
      + 0.30 * specificity
      + 0.20 * consistency
      + 0.10 * novelty

每個 component 範圍 [0, 1]。Composite 也 cap 在 [0, 1]。

# 設計重點
- **Pure functions**:不需要 LLM、不需要 DB(consistency/novelty 在沒 db 時走 default)
- **Testable**:每個 sub-component 都可獨立 unit test
- **MVP fallback**:novelty 沒 embedding 模型時用 Jaccard token 相似度,夠用
- **沒撞到時保守給高分**:consistency 找不到「相似觀察」時回 1.0(代表「沒人反對」),
  novelty 找不到 active instinct 時回 1.0(代表「沒撞既有」)
"""

from __future__ import annotations

import re
from typing import Any


# ============================================================
# Sub-component 1: evidence_support
# ============================================================
def compute_evidence_support(trace_quotes_count: int) -> float:
    """
    Spec §13.5: min(trace_quotes_count / 3.0, 1.0)

    Args:
        trace_quotes_count: observation 從 trace 引用了幾條證據
                            (error message / step phase / LLM message 片段 等)

    Returns:
        [0, 1] float
    """
    if not isinstance(trace_quotes_count, (int, float)) or trace_quotes_count < 0:
        return 0.0
    return min(float(trace_quotes_count) / 3.0, 1.0)


# ============================================================
# Sub-component 2: specificity
# ============================================================
# Column name 啟發式:
#   (a) snake_case 含底線 ($_)               例:`review_status` / `total_count`
#   (b) 反引號圍住的 identifier `xxx`         例:`Q['col']`
#   (c) `Q[' ... ']` / `df[' ... ']` 結構     例:Q['pct']
#   (d) 全大寫 enum / code(2+ uppercase)    例:PAY / RTN
# 「improve」「prompt」「rule」這類無底線常見英文字不算 column name(避免假陽性)。
_COLUMN_NAME_PATTERN = re.compile(
    r"(?:\w*_\w+|\w+_\w*)"                      # 任何含底線的 token(review_status / _pct)
    r"|`[^`\s]{2,}`"                            # 反引號 identifier
    r"|\b[A-Za-z]+\[['\"][^'\"]+['\"]\]"        # df['col'] / Q['col']
    r"|\b[A-Z]{2,}\b"                           # PAY / RTN 全大寫 enum
)
# 算術 / 比較 / 賦值 operator(只認 code-like 才算)
_OPERATOR_PATTERN = re.compile(r"(==|!=|>=|<=|=>|<-|\+=|->)")
# 數字閾值
_NUMERIC_THRESHOLD_PATTERN = re.compile(r"\b\d+(?:\.\d+)?%?\b")
# 「testable in code」啟發式
_TESTABLE_PATTERN = re.compile(
    r"(if\s+\w|add\s+rule|add\s+phase|add\s+validator|require|must|禁止|"
    r"必須|`[^`]+`|when\s+\w)",
    re.IGNORECASE,
)


def compute_specificity(recommendation: str) -> float:
    """
    Spec §13.5: 0.5 if recommendation contains column name or operator;
                +0.3 if contains numeric threshold;
                +0.2 if directly testable in code.

    Cap at 1.0.

    Heuristics(明確 documented,不依 LLM):
      - 找 snake_case identifier 或算術 / 比較 operator → 0.5
      - 找數字(可帶 %)→ +0.3
      - 找 "if X" / "add rule:" / 反引號 code 片段 / "必須"/"禁止" → +0.2
    """
    if not isinstance(recommendation, str) or not recommendation.strip():
        return 0.0

    score = 0.0
    rec = recommendation.strip()

    has_col_or_op = bool(_COLUMN_NAME_PATTERN.search(rec)) or bool(
        _OPERATOR_PATTERN.search(rec)
    )
    if has_col_or_op:
        score += 0.5

    if _NUMERIC_THRESHOLD_PATTERN.search(rec):
        score += 0.3

    if _TESTABLE_PATTERN.search(rec):
        score += 0.2

    return min(score, 1.0)


# ============================================================
# Tokenization helper(consistency / novelty 共用)
# ============================================================
_TOKEN_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[0-9]+(?:\.[0-9]+)?")


def _tokenize(text: str) -> set[str]:
    """把 text 切成 lowercase token set,供 Jaccard 用。"""
    if not isinstance(text, str):
        return set()
    return {t.lower() for t in _TOKEN_PATTERN.findall(text)}


def _jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard 相似度,空集合 vs 空集合 → 0.0(避免 division by zero)。"""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


# ============================================================
# Sub-component 3: consistency
# ============================================================
def compute_consistency(
    observation: dict,
    db: Any = None,
    *,
    similarity_threshold: float = 0.4,
    collection_name: str = "learning_observations",
) -> float:
    """
    Spec §13.5: similar_observations_same_recommendation /
                max(similar_observations, 1)

    「相似 observation」= 同 phase + tags 有交集 + recommendation Jaccard >= 0.4
    其中「same recommendation」= Jaccard >= 0.7 (更嚴一檔當「同意」)

    若 db 為 None 或查不到任何相似 observation,**回 1.0**
    (代表「沒人反對」,新觀察值得相信)。

    Args:
        observation: dict 含 phase / tags / recommendation
        db: pymongo Database(可 None)
        similarity_threshold: 視為「相似」的 Jaccard 門檻(default 0.4)
        collection_name: learning_observations collection 名

    Returns:
        [0, 1] float
    """
    if db is None:
        return 1.0

    phase = observation.get("phase")
    tags = observation.get("tags") or []
    rec = observation.get("recommendation", "")
    if not phase or not rec:
        return 1.0

    rec_tokens = _tokenize(rec)
    if not rec_tokens:
        return 1.0

    # 撈同 phase + tags 有任一交集的既有 observations(verified / candidate 都算)
    query = {"phase": phase, "status": {"$in": ["candidate", "verified"]}}
    if tags:
        query["tags"] = {"$in": tags}

    try:
        cursor = db[collection_name].find(
            query, {"recommendation": 1, "_id": 0, "observation_id": 1}
        ).limit(200)
        existing = list(cursor)
    except Exception:
        return 1.0

    if not existing:
        return 1.0

    similar_count = 0
    agree_count = 0
    for doc in existing:
        other_rec = doc.get("recommendation", "")
        sim = _jaccard(rec_tokens, _tokenize(other_rec))
        if sim >= similarity_threshold:
            similar_count += 1
            # 「Same recommendation」用更嚴的門檻
            if sim >= 0.7:
                agree_count += 1

    if similar_count == 0:
        return 1.0
    return agree_count / similar_count


# ============================================================
# Sub-component 4: novelty
# ============================================================
def compute_novelty(
    observation: dict,
    db: Any = None,
    *,
    instincts_collection: str = "learning_instincts",
) -> float:
    """
    Spec §13.5: 1 - max(cosine_similarity(current_observation, active_instincts))

    MVP 用 Jaccard 取代 cosine(我們沒 embedding model 啟動)。
    結果語意一致:**沒撞到既有 instinct 就 1.0,撞越像越接近 0**。

    若 db 為 None 或沒有 active instinct,回 1.0(代表全新,沒撞到任何 known)。
    """
    if db is None:
        return 1.0

    rec = observation.get("recommendation", "")
    cause = observation.get("cause", "")
    if not rec and not cause:
        return 1.0

    obs_tokens = _tokenize(rec) | _tokenize(cause)
    if not obs_tokens:
        return 1.0

    try:
        cursor = db[instincts_collection].find(
            {"status": "active"},
            {"rule": 1, "name": 1, "_id": 0, "instinct_id": 1},
        ).limit(500)
        instincts = list(cursor)
    except Exception:
        return 1.0

    if not instincts:
        return 1.0

    max_sim = 0.0
    for inst in instincts:
        inst_tokens = _tokenize(inst.get("rule", "")) | _tokenize(inst.get("name", ""))
        sim = _jaccard(obs_tokens, inst_tokens)
        if sim > max_sim:
            max_sim = sim

    return max(0.0, 1.0 - max_sim)


# ============================================================
# Composite
# ============================================================
def compute_confidence(
    observation: dict,
    *,
    trace_quotes_count: int = 0,
    db: Any = None,
) -> dict:
    """
    組合 4 個 sub-component。回傳 dict 含每個分數加總分,讓 caller / dashboard
    可以看到拆解。

    Args:
        observation: dict(至少含 phase / tags / cause / recommendation)
        trace_quotes_count: 從 trace 抽到幾條 evidence(extractor 算給的)
        db: pymongo Database — consistency / novelty 會查。可 None。

    Returns:
        {
          "confidence": float,
          "evidence_support": float,
          "specificity": float,
          "consistency": float,
          "novelty": float,
        }
    """
    ev = compute_evidence_support(trace_quotes_count)
    sp = compute_specificity(observation.get("recommendation", ""))
    cn = compute_consistency(observation, db=db)
    nv = compute_novelty(observation, db=db)

    conf = 0.40 * ev + 0.30 * sp + 0.20 * cn + 0.10 * nv
    conf = max(0.0, min(1.0, conf))

    return {
        "confidence": round(conf, 4),
        "evidence_support": round(ev, 4),
        "specificity": round(sp, 4),
        "consistency": round(cn, 4),
        "novelty": round(nv, 4),
    }
