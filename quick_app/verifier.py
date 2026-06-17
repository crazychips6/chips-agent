"""自我验证 — 生成后自动跑样例，通过才注册

验证方式统一：spawn 子进程执行 QuickApp，检查 returncode + stdout + 关键词。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from quick_app.models import VerificationResult


def verify_quick_app(
    app_path: Path,
    python_path: str | None,
    sample_args: dict,
    *,
    timeout: int = 30,
) -> VerificationResult:
    """用样例参数运行 QuickApp 并检查输出。

    Args:
        app_path: app.py 的完整路径。
        python_path: 使用的 Python 解释器路径（由 Manager 按 venv 级别解析）。
        sample_args: 样例参数字典，传给子进程的 JSON 参数。
        timeout: 超时秒数（用 verify_external 的场景会传更大的值）。

    返回 VerificationResult，passed=True 表示通过。
    """
    if not app_path.exists():
        return VerificationResult(
            passed=False,
            error="快应用文件不存在",
        )

    if python_path is None:
        python_path = sys.executable

    cmd = [python_path, str(app_path), _args_to_json(sample_args)]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return VerificationResult(
            passed=False,
            error=f"验证超时（{timeout} 秒），请检查是否死循环或需要更长超时",
        )
    except FileNotFoundError:
        return VerificationResult(
            passed=False,
            error=f"Python 解释器未找到：{python_path}",
        )
    except OSError as e:
        return VerificationResult(
            passed=False,
            error=f"子进程启动失败：{e}",
        )

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()

    # ── 检查清单 ──
    checks = _run_checks(result.returncode, stdout, stderr)

    if not checks["passed"]:
        return VerificationResult(
            passed=False,
            stdout=stdout,
            stderr=stderr,
            error=checks["reason"],
        )

    return VerificationResult(
        passed=True,
        stdout=stdout,
        stderr=stderr,
    )


def _run_checks(returncode: int, stdout: str, stderr: str) -> dict:
    """结构化检查列表。"""
    # ① returncode == 0
    if returncode != 0:
        return {"passed": False, "reason": f"返回码非零 ({returncode})"}

    # ② stdout 非空
    if not stdout:
        return {"passed": False, "reason": "输出为空"}

    # ③ 不包含错误关键词（不区分大小写）
    error_keywords = [
        "error", "exception", "traceback", "failed",
        "错误", "异常", "失败",
    ]
    stdout_lower = stdout.lower()
    for kw in error_keywords:
        if kw.lower() in stdout_lower:
            # 特例：某些工具 stdout 中含 "error" 但不一定是错误（如 ffmpeg 输出）
            # 如果是简短输出且含错误关键词，谨慎拒绝
            if len(stdout) < 200:
                return {
                    "passed": False,
                    "reason": f"输出包含错误关键词「{kw}」，可能为错误信息",
                }

    # ④ 合理最小长度（至少 4 个非空白字符）
    visible_chars = sum(1 for c in stdout if not c.isspace())
    if visible_chars < 4:
        return {"passed": False, "reason": "输出过短（至少需要 4 个非空白字符）"}

    return {"passed": True}


def _args_to_json(sample_args: dict) -> str:
    """将样例参数编码为单行 JSON 字符串（子进程 sys.argv[1] 用）。"""
    import json
    return json.dumps(sample_args, ensure_ascii=False, separators=(",", ":"))
