"""
safe_exec.py — v0.14.1+ (M4b)

Upload Workspace 的 Pandas code execution sandbox 強化。對齊 spec §14.2:
1. 禁止 LLM 產生 open / read_csv / read_excel / os / subprocess / requests / socket
2. exec namespace 只暴露 pd / np / 指定 input dataframe / 目標 output 變數
3. 設 execution timeout(預設 30s)
4. 設 row / col limit(配合 file_parser 100MB 限制)

# 為什麼新建一個 module 而非加進 upload_analysis_service

upload_analysis_service 是 orchestrator,職責太多。把「safe code execution」
單獨抽出來:
- 容易 unit test
- 給 Phase A / Phase B 共用(目前 Phase A 走 phase_a_validator static check
  + 直接 exec,Phase B 也 exec — 都該過 safe layer)
- 未來換 Python subprocess sandbox 或 RestrictedPython 時只動這層

# 跟 phase_a_validator 的關係

- phase_a_validator:**static 分析** code 字串,找 import / IO / 派生欄位
  等違規。**這層 cheap,可重複跑**。
- safe_exec:**runtime exec** code,加 builtins restriction + timeout +
  output validation。**這層 expensive,每 attempt 跑一次**。

兩層互補:static catch 大部分 LLM 出包,runtime 接住罕見的 dynamic 攻擊
(e.g. `getattr(__builtins__, 'open')`)。

# 用法

```python
from safe_exec import safe_exec_pandas

result = safe_exec_pandas(
    code="raw_df = source_df[source_df['x'] > 0]",
    inputs={"source_df": some_df},
    expected_output_var="raw_df",
    timeout_s=30,
    max_rows=100_000,
)
# result.success: bool
# result.output: DataFrame(如果 success)
# result.error: str | None
# result.exec_time_s: float
```
"""

from __future__ import annotations

import logging
import signal
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ============================================================
# Restricted builtins
# ============================================================
# 從 default builtins 移除以下高風險 — code 用到會 NameError
_FORBIDDEN_BUILTINS = frozenset({
    "open", "exec", "eval", "compile", "__import__",
    "input", "exit", "quit", "help",
    "globals", "locals", "vars", "dir",
    "memoryview", "bytearray", "bytes",  # 可用於繞 import
    "breakpoint",
})


def _build_safe_builtins() -> dict[str, Any]:
    """從 default builtins 移除高風險函式,回新 dict。"""
    import builtins as _b
    safe: dict[str, Any] = {}
    for name in dir(_b):
        if name.startswith("__"):
            # 保留 __build_class__ / __name__ 等系統用
            if name in ("__name__", "__doc__", "__package__"):
                safe[name] = getattr(_b, name)
            continue
        if name in _FORBIDDEN_BUILTINS:
            continue
        safe[name] = getattr(_b, name)
    # 保留 print(debug 用,但不該 chart label 用)
    safe["print"] = _b.print
    return safe


_SAFE_BUILTINS = _build_safe_builtins()


# ============================================================
# Result dataclass
# ============================================================
@dataclass
class SafeExecResult:
    """safe_exec_pandas 回傳。"""
    success: bool
    output: Optional[pd.DataFrame] = None
    error: Optional[str] = None
    error_type: Optional[str] = None
    exec_time_s: float = 0.0
    namespace_keys: list[str] = field(default_factory=list)
    truncated: bool = False    # output 超過 max_rows 被截斷


# ============================================================
# Thread-based timeout(不用 signal,因為 streamlit thread 內 signal 跑不到)
# ============================================================
class _ExecRunner(threading.Thread):
    """Daemon thread 跑 exec,主 thread 用 .join(timeout) 限時。"""

    def __init__(self, code: str, ns: dict):
        super().__init__(daemon=True)
        self.code = code
        self.ns = ns
        self.exc: Exception | None = None
        self.done = False

    def run(self):
        try:
            exec(self.code, self.ns, self.ns)
            self.done = True
        except Exception as e:
            self.exc = e


# ============================================================
# Main entry
# ============================================================
def safe_exec_pandas(
    code: str,
    inputs: dict[str, Any],
    expected_output_var: str,
    timeout_s: float = 30.0,
    max_rows: int = 100_000,
    max_cols: int = 500,
) -> SafeExecResult:
    """跑 LLM 產的 pandas code,加 sandbox + timeout + output limit。

    Args:
        code: 要 exec 的 Python code 字串
        inputs: 暴露給 code 的變數(例 `{"source_df": df}`)
        expected_output_var: code 結束後該 namespace 應該存在的變數名
            (例 "raw_df" / "Q")
        timeout_s: thread join 上限(預設 30s)
        max_rows: output DataFrame 列數上限,超過 truncate
        max_cols: output DataFrame 欄數上限,超過 truncate

    Returns:
        SafeExecResult
    """
    # 1. 構造 restricted namespace
    ns: dict[str, Any] = {
        "__builtins__": _SAFE_BUILTINS,
        "pd": pd,
        "np": np,
        **inputs,
    }

    # 2. 起 thread 跑 exec
    t0 = time.time()
    runner = _ExecRunner(code, ns)
    runner.start()
    runner.join(timeout=timeout_s)
    elapsed = round(time.time() - t0, 3)

    # 3. timeout?
    if runner.is_alive():
        return SafeExecResult(
            success=False,
            error=(f"Execution timeout after {timeout_s}s. "
                   "Possibly infinite loop or LLM 寫了 expensive 操作."),
            error_type="TimeoutError",
            exec_time_s=elapsed,
        )

    # 4. exec 內部 exception?
    if runner.exc is not None:
        return SafeExecResult(
            success=False,
            error=f"{type(runner.exc).__name__}: {runner.exc}",
            error_type=type(runner.exc).__name__,
            exec_time_s=elapsed,
            namespace_keys=[k for k in ns.keys() if not k.startswith("_")],
        )

    # 5. 期待的 output var 存在?
    if expected_output_var not in ns:
        return SafeExecResult(
            success=False,
            error=(f"Expected output variable `{expected_output_var}` 不在 "
                   f"namespace 內。LLM 可能忘了寫 `{expected_output_var} = ...`."),
            error_type="MissingOutput",
            exec_time_s=elapsed,
            namespace_keys=[k for k in ns.keys() if not k.startswith("_")],
        )

    output = ns[expected_output_var]

    # 6. output 必須是 DataFrame
    if not isinstance(output, pd.DataFrame):
        # Series → 自動 to_frame
        if isinstance(output, pd.Series):
            output = output.to_frame().reset_index()
        else:
            return SafeExecResult(
                success=False,
                error=(f"`{expected_output_var}` 是 `{type(output).__name__}`,"
                       "不是 DataFrame。"),
                error_type="WrongOutputType",
                exec_time_s=elapsed,
                namespace_keys=[k for k in ns.keys() if not k.startswith("_")],
            )

    # 7. 列數 / 欄數 limit
    truncated = False
    if len(output) > max_rows:
        logger.warning(
            f"safe_exec_pandas: output {len(output)} rows > limit {max_rows},"
            f"truncating."
        )
        output = output.head(max_rows)
        truncated = True
    if output.shape[1] > max_cols:
        logger.warning(
            f"safe_exec_pandas: output {output.shape[1]} cols > limit {max_cols},"
            f"truncating."
        )
        output = output.iloc[:, :max_cols]
        truncated = True

    return SafeExecResult(
        success=True,
        output=output,
        exec_time_s=elapsed,
        namespace_keys=[k for k in ns.keys() if not k.startswith("_")],
        truncated=truncated,
    )


# ============================================================
# Convenience:check input DataFrame within limits
# ============================================================
def check_dataframe_limits(
    df: pd.DataFrame,
    max_rows: int = 100_000,
    max_cols: int = 500,
) -> tuple[bool, Optional[str]]:
    """快速檢查 input df 沒超過 limit。caller 在 Phase A exec 前驗一次。

    Returns:
        (ok, error_msg)
    """
    if len(df) > max_rows:
        return False, (f"Input DataFrame has {len(df):,} rows > "
                       f"max_rows={max_rows:,}. Phase 2 DuckDB 才能處理。")
    if df.shape[1] > max_cols:
        return False, (f"Input DataFrame has {df.shape[1]} cols > "
                       f"max_cols={max_cols}.")
    return True, None
