"""
admin/dump_test_run_details.py — v0.4.0 helper

把指定 run_id(或最近一筆)的 per-case 細節整理成 JSON,
給 Claude / dev 做失敗歸因。預設只顯示「非 passed/refusal」的 case,
減少雜訊;加 --all 看全部。

對齊 test_runner.py 的實際 schema:
    case_results: [
        {
            "id": "...", "name": "...", "query": "...", "type": "...",
            "phases": {plan, pipeline, preprocess, echarts, insight},
            "checks": [{label, ok, detail}, ...],
            "status": "passed|failed|refusal_detected|fatal_error|...",
            "fatal_traceback": "...",
            "wall_elapsed_s": float,
            "llm_usage": {...},
            "retries": {"phase_b": int, "phase_c": int},
        }, ...
    ]

# 使用方式
```bash
# 最新一筆,只顯示非預期成功的 case
python admin/dump_test_run_details.py --out outputs/latest_run_details.json

# 看指定 run_id
python admin/dump_test_run_details.py --run-id 20260514_115447

# 全部 case 都列(含 passed)
python admin/dump_test_run_details.py --all
```
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

import config


_EXPECTED_OK = {"passed", "refusal_detected"}


def _trim(s, n=400):
    if s is None:
        return None
    if not isinstance(s, str):
        try:
            s = json.dumps(s, ensure_ascii=False, default=str)
        except Exception:
            s = str(s)
    return s if len(s) <= n else s[:n] + f"… (+{len(s)-n} chars)"


def _flatten_phase(phase_dict: dict | None) -> dict:
    """phases 子 dict 通常包含 status / message / attempts / code / option / 等。
    我們只挑會有 debug 價值的欄位。"""
    if not isinstance(phase_dict, dict):
        return {}
    out = {}
    for k in ("status", "attempts", "engine", "fallback_to_table",
              "phase_a_attempts", "preview_md_head", "error",
              "message", "errmsg", "traceback"):
        if k in phase_dict:
            v = phase_dict[k]
            if k == "traceback":
                v = _trim(v, 600)
            elif isinstance(v, str):
                v = _trim(v, 400)
            out[k] = v
    # 程式碼類欄位獨立截斷
    for k in ("code", "preprocess_code", "plot_code", "echarts_code",
              "pipeline_json", "echarts_option", "option"):
        if k in phase_dict and phase_dict[k] is not None:
            out[k + "_head"] = _trim(phase_dict[k], 800)
    return out


def main():
    p = argparse.ArgumentParser(description="Dump per-case detail for a test_run")
    p.add_argument("--run-id", default="", help="預設取最新")
    p.add_argument("--domain", default="", help="跟 run-id 一起 disambiguate")
    p.add_argument("--all", action="store_true",
                   help="列出所有 case(預設只列非 passed/refusal)")
    p.add_argument("--out", default="", help="寫到檔案;否則 stdout")
    args = p.parse_args()

    try:
        from pymongo import MongoClient
        client = MongoClient(
            config.MONGO_URI,
            serverSelectionTimeoutMS=config.MONGO_SERVER_SELECTION_TIMEOUT_MS,
        )
        client.admin.command("ping")
        db = client[config.MONGO_DB]
    except Exception as e:
        print(f"❌ MongoDB 連線失敗: {e}", file=sys.stderr)
        return 1

    coll = db[config.TEST_RUNS_COLLECTION]
    query = {}
    if args.run_id:
        query["run_id"] = args.run_id
    if args.domain:
        query["domain"] = args.domain

    if args.run_id:
        doc = coll.find_one(query)
    else:
        doc = coll.find_one(query, sort=[("started_at", -1)])

    if not doc:
        print("❌ 找不到符合條件的 test_run", file=sys.stderr)
        return 1

    # 真實 schema 是 case_results,不是 results
    cases = doc.get("case_results") or doc.get("results") or []
    total = len(cases)
    if not args.all:
        cases = [r for r in cases if r.get("status") not in _EXPECTED_OK]

    out = {
        "run_id": doc.get("run_id"),
        "domain": doc.get("domain"),
        "git_commit": doc.get("git_commit"),
        "started_at": str(doc.get("started_at")),
        "total_wall_s": doc.get("total_wall_s"),
        "summary": doc.get("summary"),
        "filter_mode": "all" if args.all else "fails_only",
        "n_total_cases": total,
        "n_returned": len(cases),
        "cases": [
            {
                "id": r.get("id") or r.get("case_id"),
                "name": r.get("name") or r.get("label"),
                "type": r.get("type") or r.get("category"),
                "status": r.get("status"),
                "query": r.get("query"),
                "wall_elapsed_s": r.get("wall_elapsed_s"),
                "retries": r.get("retries"),
                "llm_usage": {
                    k: r.get("llm_usage", {}).get(k)
                    for k in ("total_calls", "total_tokens",
                              "prompt_tokens", "completion_tokens")
                } if isinstance(r.get("llm_usage"), dict) else None,
                "fatal_traceback": _trim(r.get("fatal_traceback"), 800),
                "phases": {
                    phase_name: _flatten_phase(phase_data)
                    for phase_name, phase_data in (r.get("phases") or {}).items()
                },
                "checks_failed": [
                    {"label": c.get("label"),
                     "ok": c.get("ok"),
                     "detail": _trim(c.get("detail"), 300)}
                    for c in (r.get("checks") or [])
                    if c.get("ok") is False
                ],
                "checks_ok_count": sum(
                    1 for c in (r.get("checks") or []) if c.get("ok") is True
                ),
            }
            for r in cases
        ],
    }

    payload = json.dumps(out, default=str, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(payload, encoding="utf-8")
        print(f"✅ wrote {args.out}  ({len(payload):,} chars · "
              f"{out['n_returned']}/{out['n_total_cases']} cases)")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
