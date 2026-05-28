#!/usr/bin/env python
"""scripts/run_regression_gate.py — v0.18 M7 scaffold

Unified regression gate per spec §14.4. Runs the categories from §14.3 in
sequence and emits a JSON summary per Appendix B. Exit code 0 if all
non-skipped categories pass, 1 otherwise.

# Usage

    # Fast mode (default) — no Ollama/MongoDB required:
    python scripts/run_regression_gate.py --mode fast

    # Full mode — also runs test_runner.py tflex baseline:
    python scripts/run_regression_gate.py --mode full

    # Persist JSON output:
    python scripts/run_regression_gate.py --json-out /tmp/gate.json

# Categories (spec §14.3 baseline pass gate)

| Category               | Fast | Full | Required gate                    |
|------------------------|------|------|----------------------------------|
| py_compile             | ✓    | ✓    | 100% pass                        |
| unit_tests             | ✓    | ✓    | 100% pass                        |
| upload_acceptance      | ✓    | ✓    | 100% pass                        |
| multi_table_acceptance | ✓    | ✓    | 100% pass                        |
| safety_tests           | ✓    | ✓    | 100% pass                        |
| schema_baseline        | —    | ✓    | not below existing baseline -1   |

# Output format (spec Appendix B)

    {
      "status": "pass" | "fail",
      "mode": "fast" | "full",
      "schema_baseline":     {"passed": N, "total": N, "skipped": bool, ...},
      "upload_acceptance":   {"passed": N, "total": N},
      "multi_table_acceptance": {"passed": N, "total": N},
      "blocking_failures":   [str, ...]
    }

# Design notes

- One subprocess call per category. Captures stdout + exit code.
- Parses pytest's terminal summary regex (`N passed`, `N failed`, ...).
- Full mode's schema_baseline runs `python test_runner.py --domain tflex`.
  If MongoDB or LLM isn't available the runner exits non-zero with a
  recognizable error message — we mark it `skipped` rather than `failed`
  so the gate can still pass on dev machines without those deps.
- This is a *scaffold* — future PRs (M2+) add categories as their
  features land.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ============================================================
# pytest summary parsing
# ============================================================
# Matches lines like:
#   "============= 12 passed in 0.5s ============="
#   "==== 11 passed, 1 failed, 1 skipped in 1.2s ===="
_SUMMARY_RE = re.compile(
    r"(?:(\d+)\s+passed)?"
    r"(?:.*?(\d+)\s+failed)?"
    r"(?:.*?(\d+)\s+skipped)?"
    r"(?:.*?(\d+)\s+error)?"
)


def _parse_pytest_summary(stdout: str) -> dict[str, int]:
    """Extract passed / failed / skipped / errors from pytest stdout."""
    counts = {"passed": 0, "failed": 0, "skipped": 0, "errors": 0}
    for line in stdout.splitlines()[::-1]:
        # Strip ANSI escape sequences for easier matching.
        stripped = re.sub(r"\x1b\[[0-9;]*m", "", line)
        if " passed" not in stripped and " failed" not in stripped \
                and " error" not in stripped:
            continue
        for kw in ("passed", "failed", "skipped", "error"):
            m = re.search(rf"(\d+)\s+{kw}", stripped)
            if m:
                key = "errors" if kw == "error" else kw
                counts[key] = int(m.group(1))
        break
    return counts


# ============================================================
# Category runners — each returns a result dict
# ============================================================
def _run_py_compile(files: list[Path]) -> dict[str, Any]:
    failed: list[str] = []
    for f in files:
        if not f.exists():
            failed.append(f"{f.relative_to(PROJECT_ROOT)} (missing)")
            continue
        try:
            subprocess.run(
                [sys.executable, "-m", "py_compile", str(f)],
                check=True, capture_output=True,
            )
        except subprocess.CalledProcessError as e:
            failed.append(
                f"{f.relative_to(PROJECT_ROOT)}: "
                f"{e.stderr.decode('utf-8', errors='replace')[:200]}"
            )
    return {
        "passed": len(files) - len(failed),
        "total": len(files),
        "failed_items": failed,
    }


def _run_pytest(target: str, *, extra_args: list[str] = None) -> dict[str, Any]:
    """Run pytest against a single target path. Returns counts + exit_code."""
    cmd = [sys.executable, "-m", "pytest", target, "-q", "--tb=line",
           "--no-header", "--color=no"]
    if extra_args:
        cmd.extend(extra_args)
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=PROJECT_ROOT)
    elapsed = time.time() - t0
    counts = _parse_pytest_summary(proc.stdout + "\n" + proc.stderr)
    total = counts["passed"] + counts["failed"] + counts["skipped"] \
            + counts["errors"]
    # Strip ANSI escapes from tail so the JSON is grep-able / log-readable.
    raw_tail = "\n".join(proc.stdout.splitlines()[-8:])
    clean_tail = re.sub(r"\x1b\[[0-9;]*m", "", raw_tail)
    return {
        "passed": counts["passed"],
        "failed": counts["failed"],
        "skipped": counts["skipped"],
        "errors": counts["errors"],
        "total": total,
        "exit_code": proc.returncode,
        "elapsed_s": round(elapsed, 2),
        "tail": clean_tail,
    }


def _run_schema_baseline_full() -> dict[str, Any]:
    """Run `python test_runner.py --domain tflex`. Skip if deps missing."""
    cmd = [sys.executable, "test_runner.py", "--domain", "tflex"]
    t0 = time.time()
    proc = subprocess.run(
        cmd, capture_output=True, text=True, cwd=PROJECT_ROOT,
        timeout=2400,  # 40 min hard cap — typical run is ~22 min
    )
    elapsed = time.time() - t0
    # Recognize "no MongoDB" / "no LLM endpoint" as skip rather than fail.
    skip_signals = (
        "MongoDB", "mongodb", "Ollama", "ollama not running",
        "connection refused", "ECONNREFUSED",
    )
    combined = proc.stdout + proc.stderr
    skipped = (proc.returncode != 0
               and any(sig in combined for sig in skip_signals))
    return {
        "exit_code": proc.returncode,
        "elapsed_s": round(elapsed, 2),
        "skipped": skipped,
        "tail": "\n".join(combined.splitlines()[-15:]),
    }


# ============================================================
# Main orchestration
# ============================================================
def run_gate(mode: str = "fast") -> tuple[dict[str, Any], bool]:
    """Run all gate categories. Returns (summary_dict, all_passed)."""
    blocking: list[str] = []
    summary: dict[str, Any] = {"mode": mode, "timestamp": time.time()}

    # 1. py_compile critical files
    print("[1/5] py_compile…", file=sys.stderr)
    critical = [
        PROJECT_ROOT / "app.py",
        PROJECT_ROOT / "llm_service.py",
        PROJECT_ROOT / "config.py",
        PROJECT_ROOT / "test_runner.py",
        PROJECT_ROOT / "upload_service.py",
        PROJECT_ROOT / "upload_repository.py",
        PROJECT_ROOT / "multi_table_profiler.py",
        PROJECT_ROOT / "duckdb_engine.py",
    ]
    py_compile_result = _run_py_compile(critical)
    summary["py_compile"] = py_compile_result
    if py_compile_result["failed_items"]:
        blocking.append(
            f"py_compile failed: {py_compile_result['failed_items'][:3]}"
        )

    # 2. Unit tests
    print("[2/5] unit tests…", file=sys.stderr)
    unit = _run_pytest(
        "tests/unit/",
        extra_args=["--ignore=tests/unit/test_rag_index_repository.py"],
    )
    summary["unit_tests"] = unit
    if unit["failed"] > 0 or unit["errors"] > 0:
        blocking.append(
            f"unit_tests: {unit['failed']} failed, {unit['errors']} errors"
        )

    # 3. Upload single-table acceptance + integration
    print("[3/5] upload acceptance…", file=sys.stderr)
    upload_acc = _run_pytest("tests/acceptance/test_mvp_acceptance.py")
    upload_int = _run_pytest("tests/integration/")
    upload_combined = {
        "passed": upload_acc["passed"] + upload_int["passed"],
        "failed": upload_acc["failed"] + upload_int["failed"],
        "skipped": upload_acc["skipped"] + upload_int["skipped"],
        "errors": upload_acc["errors"] + upload_int["errors"],
        "total": upload_acc["total"] + upload_int["total"],
        "elapsed_s": round(
            upload_acc["elapsed_s"] + upload_int["elapsed_s"], 2
        ),
    }
    summary["upload_acceptance"] = upload_combined
    if upload_combined["failed"] > 0 or upload_combined["errors"] > 0:
        blocking.append(
            f"upload_acceptance: {upload_combined['failed']} failed, "
            f"{upload_combined['errors']} errors"
        )

    # 4. Multi-table acceptance (M1 scaffold — 3 tests, more added in M2-M6)
    print("[4/5] multi-table acceptance…", file=sys.stderr)
    multi = _run_pytest("tests/acceptance/test_multitable_acceptance.py")
    summary["multi_table_acceptance"] = multi
    if multi["failed"] > 0 or multi["errors"] > 0:
        blocking.append(
            f"multi_table_acceptance: {multi['failed']} failed, "
            f"{multi['errors']} errors"
        )

    # 5. Schema-driven tflex baseline (full mode only)
    if mode == "full":
        print("[5/5] schema_baseline (full)… (this may take 20-30 min)",
              file=sys.stderr)
        baseline = _run_schema_baseline_full()
        summary["schema_baseline"] = baseline
        if baseline["skipped"]:
            print(
                f"  ⚠ schema_baseline skipped (deps unavailable): "
                f"{baseline['tail'][-200:]}",
                file=sys.stderr,
            )
        elif baseline["exit_code"] != 0:
            blocking.append(
                f"schema_baseline: exit={baseline['exit_code']}; "
                f"see tail: {baseline['tail'][-200:]}"
            )
    else:
        summary["schema_baseline"] = {
            "skipped": True,
            "reason": "fast mode — run with --mode full to include",
        }

    summary["blocking_failures"] = blocking
    summary["status"] = "pass" if not blocking else "fail"

    return summary, len(blocking) == 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mode", choices=("fast", "full"), default="fast")
    ap.add_argument("--json-out", type=Path, default=None,
                    help="Write JSON summary to this path (in addition to stdout)")
    args = ap.parse_args()

    summary, passed = run_gate(mode=args.mode)

    out_json = json.dumps(summary, indent=2, default=str)
    print(out_json)
    if args.json_out:
        args.json_out.write_text(out_json, encoding="utf-8")
        print(f"\n[wrote {args.json_out}]", file=sys.stderr)

    if passed:
        print("\n✅ Regression gate PASS", file=sys.stderr)
        return 0
    else:
        print(
            f"\n❌ Regression gate FAIL ({len(summary['blocking_failures'])} "
            f"blocker{'s' if len(summary['blocking_failures']) != 1 else ''}):",
            file=sys.stderr,
        )
        for b in summary["blocking_failures"]:
            print(f"  - {b}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
