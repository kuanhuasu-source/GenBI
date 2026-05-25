"""scripts/diff_test_runs.py — diff two test runs (RAG off vs on) by case.

兩種輸入方式:
  (A) JSON files:`python scripts/diff_test_runs.py --a runs/rag_off.json --b runs/rag_on.json`
  (B) MongoDB _ids:`python scripts/diff_test_runs.py --a-id <ObjectId> --b-id <ObjectId>`

Output:per-case status + 哪些 case flipped(off→on)+ phase-level token diff。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))


def _load_from_file(path: Path) -> list[dict]:
    """test_results.json 是 case_results list,直接 load。"""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict) and "case_results" in raw:
        return raw["case_results"]
    raise ValueError(f"Unknown JSON shape in {path}: top type={type(raw).__name__}")


def _load_from_mongo(oid_str: str) -> list[dict]:
    """從 MongoDB test_runs collection 拿 case_results。"""
    try:
        from bson import ObjectId
        from pymongo import MongoClient
        import config
    except ImportError as e:
        print(f"❌ pymongo/bson 沒裝:{e}")
        sys.exit(1)
    client = MongoClient(config.MONGO_URI)
    db = client[config.MONGO_DB]
    doc = db[config.TEST_RUNS_COLLECTION].find_one({"_id": ObjectId(oid_str)})
    if not doc:
        print(f"❌ test_runs _id={oid_str} not found")
        sys.exit(1)
    return doc.get("case_results", [])


def main() -> int:
    parser = argparse.ArgumentParser(description="Diff two test runs")
    parser.add_argument("--a", help="A run JSON file(baseline)")
    parser.add_argument("--b", help="B run JSON file(challenger)")
    parser.add_argument("--a-id", help="A run MongoDB _id")
    parser.add_argument("--b-id", help="B run MongoDB _id")
    parser.add_argument("--a-label", default="A(off)")
    parser.add_argument("--b-label", default="B(on)")
    args = parser.parse_args()

    if args.a:
        a_cases = _load_from_file(Path(args.a))
    elif args.a_id:
        a_cases = _load_from_mongo(args.a_id)
    else:
        print("❌ Need --a <file> or --a-id <oid>")
        return 1
    if args.b:
        b_cases = _load_from_file(Path(args.b))
    elif args.b_id:
        b_cases = _load_from_mongo(args.b_id)
    else:
        print("❌ Need --b <file> or --b-id <oid>")
        return 1

    # Index by case_id
    a_by_id = {c.get("case_id") or c.get("id"): c for c in a_cases}
    b_by_id = {c.get("case_id") or c.get("id"): c for c in b_cases}
    all_ids = sorted(set(a_by_id) | set(b_by_id))

    print(f"\n{'=' * 70}")
    print(f"DIFF · {args.a_label}  vs  {args.b_label}")
    print(f"{'=' * 70}\n")

    # Per-case table
    header = f"{'case_id':18s} {args.a_label:18s} {args.b_label:18s} flow"
    print(header)
    print("─" * len(header))
    a_pass = b_pass = 0
    flips = {"off_to_on": [], "on_to_off": [], "same": [], "missing": []}
    for cid in all_ids:
        ca = a_by_id.get(cid)
        cb = b_by_id.get(cid)
        sa = (ca or {}).get("status", "missing")
        sb = (cb or {}).get("status", "missing")
        if sa == "pass":
            a_pass += 1
        if sb == "pass":
            b_pass += 1
        arrow = "  "
        if sa == sb:
            arrow = " ="
            flips["same"].append(cid)
        elif sa != "pass" and sb == "pass":
            arrow = "✅"
            flips["off_to_on"].append((cid, sa, sb))
        elif sa == "pass" and sb != "pass":
            arrow = "❌"
            flips["on_to_off"].append((cid, sa, sb))
        else:
            arrow = "↔"
            flips["missing"].append((cid, sa, sb))
        print(f"{cid:18s} {sa:18s} {sb:18s} {arrow}")

    # Summary
    print(f"\n{'─' * 70}")
    print(f"SUMMARY")
    print(f"{'─' * 70}")
    print(f"  {args.a_label}:{a_pass}/{len(a_by_id)} pass")
    print(f"  {args.b_label}:{b_pass}/{len(b_by_id)} pass")
    print(f"  Δ = {b_pass - a_pass:+d}")
    print(f"  Off→On wins:{len(flips['off_to_on'])}")
    print(f"  On→Off losses:{len(flips['on_to_off'])}")
    print(f"  Both same: {len(flips['same'])}")

    # Per-case detail on flips
    if flips["off_to_on"]:
        print(f"\n{'─' * 70}")
        print(f"WINS(off→on,RAG 救起來的 case)")
        print(f"{'─' * 70}")
        for cid, sa, sb in flips["off_to_on"]:
            ca = a_by_id.get(cid, {})
            cb = b_by_id.get(cid, {})
            query = (ca.get("query") or cb.get("query") or "?")[:80]
            print(f"  [{cid}] {sa} → {sb}")
            print(f"     query: {query}")
            # 試列 error
            err_a = ca.get("error") or ca.get("failure_phase") or ""
            if err_a:
                print(f"     A error: {err_a[:120]}")

    if flips["on_to_off"]:
        print(f"\n{'─' * 70}")
        print(f"LOSSES(on→off,RAG 弄壞的 case)— ⚠️ critical")
        print(f"{'─' * 70}")
        for cid, sa, sb in flips["on_to_off"]:
            cb = b_by_id.get(cid, {})
            query = (cb.get("query") or "?")[:80]
            err = cb.get("error") or cb.get("failure_phase") or ""
            print(f"  [{cid}] {sa} → {sb}")
            print(f"     query: {query}")
            if err:
                print(f"     B error: {err[:120]}")

    # Token diff
    print(f"\n{'─' * 70}")
    print(f"TOKENS")
    print(f"{'─' * 70}")
    a_tok = sum(c.get("token_usage", {}).get("total_tokens", 0) or 0 for c in a_cases)
    b_tok = sum(c.get("token_usage", {}).get("total_tokens", 0) or 0 for c in b_cases)
    print(f"  {args.a_label} total: {a_tok:,}")
    print(f"  {args.b_label} total: {b_tok:,}")
    if a_tok and b_tok:
        print(f"  Δ tokens: {b_tok - a_tok:+,}({(b_tok/a_tok - 1)*100:+.1f}%)")
    if a_pass and b_pass:
        a_eff = a_tok / a_pass
        b_eff = b_tok / b_pass
        print(f"  tokens/success {args.a_label}: {a_eff:,.0f}")
        print(f"  tokens/success {args.b_label}: {b_eff:,.0f}")
        print(f"  Δ tokens/success: {(b_eff/a_eff - 1)*100:+.1f}%")

    return 0


if __name__ == "__main__":
    sys.exit(main())
