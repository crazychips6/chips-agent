"""危险命令审批模块

三层筛查机制：
1. HARDLINE_PATTERNS — 命中直接拒绝，不询问用户
2. DANGEROUS_PATTERNS — 命中需要用户交互确认
3. 未命中任何模式 — 直接放行

check() 返回 ApprovalResult，调用方根据 action 决定是否执行。

零项目内部依赖。"""

from dataclasses import dataclass
from enum import Enum
import logging
import re
import sys

logger = logging.getLogger("chips")


class ApprovalAction(Enum):
    ALLOW = "allow"
    DENY = "deny"


@dataclass
class ApprovalResult:
    action: ApprovalAction
    reason: str = ""


# ── 硬拦截 ──

HARDLINE_PATTERNS: list[tuple[str, str, str]] = [
    ("rm_root", r"\brm\s+(-rf|[-]?\w*r\w*f\w*)\s+/",
     "拒绝执行：危险命令（删除根目录）"),
    ("dd_zero", r"\bdd\s+if=(/dev/zero|/dev/null)",
     "拒绝执行：危险命令（覆写磁盘）"),
    ("fork_bomb", r":\(\)\s*\{",
     "拒绝执行：fork 炸弹"),
    ("mkfs", r"\bmkfs\.[a-z]+\s+/dev/",
     "拒绝执行：危险命令（格式化磁盘）"),
    ("overwrite_disk", r"[>]+\s+/dev/sd[a-z]",
     "拒绝执行：危险命令（直接写入块设备）"),
    ("shutdown", r"^(sudo\s+)?(reboot|shutdown|halt|poweroff)\b",
     "拒绝执行：系统关闭/重启命令"),
    ("chmod_root", r"\bchmod\s+(-R|[-]\w*R\w*)\s+000\s+/",
     "拒绝执行：取消根目录权限"),
]

# ── 危险模式（需用户确认）──

DANGEROUS_PATTERNS: list[tuple[str, str, str]] = [
    ("pipe_to_shell", r"(curl|wget)\s+.*\|\s*(bash|sh|zsh)",
     "从网络下载脚本并管道执行到 Shell"),
    ("rm_recursive", r"\brm\s+(-rf|[-]\w*r\w*f\w*)",
     "递归强制删除文件"),
    ("sudo", r"\bsudo\b",
     "以 root 权限执行命令"),
    ("eval", r"\beval\b",
     "动态执行代码（eval）"),
    ("chmod_777", r"\bchmod\s+777",
     "授予文件完全权限"),
    ("overwrite_etc", r"[>]+\s+/etc/",
     "覆写系统配置文件"),
    ("kill_9", r"\bkill\s+-9\b",
     "强制终止进程"),
    ("docker_destructive", r"\bdocker\s+(rm\s+-f|rmi|system\s+prune)",
     "破坏性 Docker 操作"),
    ("git_force_push", r"\bgit\s+push\s+.*(--force|-f)\b",
     "强制推送 git 分支"),
]

_HARDLINE = [(name, re.compile(pat), msg) for name, pat, msg in HARDLINE_PATTERNS]
_DANGEROUS = [(name, re.compile(pat), msg) for name, pat, msg in DANGEROUS_PATTERNS]


def check(command: str, interactive: bool = True) -> ApprovalResult:
    """检查命令是否安全。

    Args:
        command: 要检查的命令字符串。
        interactive: True 时对危险命令通过 stdin 询问用户。

    Returns:
        ApprovalResult，调用方根据 action 决定是否执行。
    """
    # 1. 硬拦截 — 直接拒绝
    for _name, pattern, msg in _HARDLINE:
        if pattern.search(command):
            logger.warning("approval=deny reason=hardline pattern=%s command=%.120s", _name, command)
            return ApprovalResult(ApprovalAction.DENY, msg)

    # 2. 危险模式 — 交互询问
    for _name, pattern, msg in _DANGEROUS:
        if pattern.search(command):
            if interactive:
                print(f"\n⚠ {msg}", file=sys.stderr)
                resp = input("  确认执行? (y/N) ").strip().lower()
                if resp in ("y", "yes"):
                    logger.info("approval=allow reason=user_confirm pattern=%s command=%.120s", _name, command)
                    return ApprovalResult(ApprovalAction.ALLOW, "用户已确认")
                logger.info("approval=deny reason=user_reject pattern=%s command=%.120s", _name, command)
                return ApprovalResult(ApprovalAction.DENY, f"用户拒绝：{msg}")
            logger.info("approval=deny reason=non_interactive pattern=%s command=%.120s", _name, command)
            return ApprovalResult(ApprovalAction.DENY, f"非交互模式拒绝：{msg}")

    return ApprovalResult(ApprovalAction.ALLOW, "")
