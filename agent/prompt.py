"""PromptBuilder — System Prompt 组装器

层次结构：
  (1) 核心身份  (2) 当前日期  (3) 用户偏好  (4) 持久记忆
  (5) 历史会话摘要  (6) 项目上下文  (7) 工具规则  (8) 调用约定

辅助功能：
  - search_context_files()  从 CWD 向上搜索 CLAUDE.md 等上下文文件
  - detect_injection()      正则检测 prompt injection
  - 超出 max_prompt_chars 时保头保尾截中间
"""

import datetime
import os
import re
import sys

# ── Injection Detection ──

INJECTION_PATTERNS: list[tuple[str, str]] = [
    ("ignore_prev_zh", r"忽略(之前|以前|上面|以上).{0,10}(指令|指示|内容|命令|设定)"),
    ("ignore_prev_en", r"ignore\s+(the\s+|all\s+)?(previous|above|prior)\s+(instructions|directives|commands)"),
    ("you_are_now_zh", r"你(现在|接下来).{0,5}(是|扮演|成为)"),
    ("you_are_now_en", r"you\s+are\s+(now\s+)?(a\s+)?(new\s+)?(system|assistant|bot)"),
    ("forget_zh", r"忘记.{0,10}(设定|设置|规则|身份|指令)"),
    ("forget_en", r"(forget|disregard)\s+(all\s+)?(previous|prior)"),
    ("system_override", r"(系统|system)\s*(指令|覆盖|重置|重设|override|prompt)"),
    ("role_redefine", r"^#\s*(系统|system|角色|role)\s*[:：]"),
]

_compiled = [(name, re.compile(p, re.IGNORECASE)) for name, p in INJECTION_PATTERNS]


def detect_injection(text: str) -> str | None:
    """检测 prompt injection，返回首个匹配的模式名，无则返回 None。"""
    for name, pattern in _compiled:
        if pattern.search(text):
            return name
    return None


# ── Context File Search ──

CONTEXT_FILE_NAMES = ["CHIP.md", ".chip/CHIP.md", "CONTEXT.md"]


def search_context_files(start_dir: str | None = None) -> list[tuple[str, str, str]]:
    """从 start_dir 向上搜索至 git 根目录，返回 [(abs_path, rel_path, content), ...]。

    跳过空文件及被 injection 检测命中的文件。"""
    if start_dir is None:
        start_dir = os.getcwd()
    start_dir = os.path.abspath(start_dir)

    # 向上查找 git 根目录作为搜索上界
    git_root = None
    d = start_dir
    while d:
        if os.path.isdir(os.path.join(d, ".git")):
            git_root = d
            break
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent

    results: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    d = start_dir
    while d:
        for name in CONTEXT_FILE_NAMES:
            path = os.path.join(d, name)
            real = os.path.realpath(path)
            if real in seen:
                continue
            seen.add(real)
            if not os.path.isfile(path):
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    content = f.read().strip()
            except Exception:
                continue
            if not content:
                continue
            hit = detect_injection(content)
            if hit:
                print(f"⚠ 跳过上下文文件（检测到注入模式 [{hit}]）：{path}", file=sys.stderr)
                continue
            rel = os.path.relpath(path, start_dir)
            results.append((path, rel, content))

        parent = os.path.dirname(d)
        if parent == d:
            break
        if git_root and d == git_root:
            break
        d = parent

    return results


# ── Prompt Text Constants ──

IDENTITY_PROMPT = """你是 chips，一个通用 AI agent，由 chips-agent 驱动。
你的核心能力是通过工具和代码执行来帮助用户完成各种任务。

## 行为准则
- 使用中文回答，技术术语不强行翻译
- 如果缺少完成任务所需的信息，主动询问用户
- 如果遇到错误，说明原因并提供解决方案
- 对于复杂任务，先规划再执行"""

CONVENTIONS_PROMPT = """## 回复规范
- 使用中文给出最终回复
- 如果需要执行终端命令，调用 terminal 工具
- 一次只调用一个工具，等待结果后再决定下一步
- 任务完成后，用中文给出简洁总结"""

QUICKAPP_PROMPT = """## 快应用创建规则
- 当用户想要一个「自动化工具」时，优先考虑创建快应用
- 创建前必须先调 get_quick_app_draft 获取草案卡片
- 严禁跳过 get_quick_app_draft 直接调 create_app
- 草案卡片的 source 字段必须来自工具返回结果，不可自行捏造
- 用户确认草案后再调 create_app 完成创建"""


# ── PromptBuilder ──

class PromptBuilder:
    """System Prompt 组装器。

    拆分两阶段：
      build_frozen()  — 一次构建永久缓存（身份 + 记忆快照 + 技能 + 上下文 + 调用约定）
      build_dynamic() — 每轮重建（实时检索 + 当前时间），量很小

    build() 作为兼容旧接口的封装，委托给 build_frozen()。
    工具感知依赖 OpenAI tools API 参数，不在 system prompt 中冗余列举。
    """

    def __init__(self, verbose: bool = False, max_prompt_chars: int = 6000):
        self.verbose = verbose
        self.max_prompt_chars = max_prompt_chars
        self._has_verbose_printed = False

    def build_frozen(
        self,
        *,
        memory_snapshot: str = "",
        context_files: list[tuple[str, str, str]] | None = None,
        skills_index: str = "",
    ) -> str:
        """冷冻层 —— 一次构建，全程复用 (prefix caching 收益最大)。"""
        layers: list[tuple[str, str]] = []

        # Layer 1 — 核心身份（始终存在）
        layers.append(("核心身份", IDENTITY_PROMPT))

        # Layer 2 — 持久记忆快照（session 启动时拍，写入后靠 tool response 同步）
        if memory_snapshot:
            layers.append(("持久记忆", memory_snapshot.strip()))

        # Layer 3 — 技能索引（可选）
        if skills_index:
            layers.append(("技能", skills_index))

        # Layer 4 — 项目上下文（可选）
        if context_files:
            parts = []
            for _path, rel, content in context_files:
                parts.append(f"文件：{rel}\n{content}")
            layers.append(("项目上下文", "\n\n---\n\n".join(parts)))

        # Layer 5 — 调用约定（始终存在）
        layers.append(("调用约定", CONVENTIONS_PROMPT))

        # Layer 6 — 快应用规则（始终存在）
        layers.append(("快应用规则", QUICKAPP_PROMPT))

        if self.verbose and not self._has_verbose_printed:
            self._dump_layers(layers)
            self._has_verbose_printed = True

        return self._assemble_and_truncate(layers)

    def build_dynamic(
        self,
        *,
        prefetch: str = "",
        timestamp: str = "",
        toolset_availability: str = "",
    ) -> str:
        """动态层 —— 每轮重建，量很小。

        包含实时检索结果、当前时间戳和工具集可用性表。
        """
        parts = []
        if timestamp:
            parts.append(f"# 当前日期\n{timestamp}")
        if prefetch:
            parts.append(f"# 实时上下文\n{prefetch.strip()}")
        if toolset_availability:
            parts.append(f"# 可用工具集\n{toolset_availability.strip()}")
        return "\n\n".join(parts)

    def build(
        self,
        *,
        memory_prompt: str = "",
        context_files: list[tuple[str, str, str]] | None = None,
        skills_index: str = "",
        toolset_availability: str = "",
    ) -> str:
        """完全兼容旧接口。供测试和旧调用方使用。"""
        layers: list[tuple[str, str]] = []
        layers.append(("核心身份", IDENTITY_PROMPT))
        layers.append(("当前日期", f"当前日期：{datetime.date.today()}"))
        if memory_prompt:
            layers.append(("持久记忆", memory_prompt.strip()))
        if skills_index:
            layers.append(("技能", skills_index))
        if toolset_availability:
            layers.append(("可用工具集", toolset_availability))
        if context_files:
            parts = []
            for _path, rel, content in context_files:
                parts.append(f"文件：{rel}\n{content}")
            layers.append(("项目上下文", "\n\n---\n\n".join(parts)))
        layers.append(("调用约定", CONVENTIONS_PROMPT))
        layers.append(("快应用规则", QUICKAPP_PROMPT))
        if self.verbose and not self._has_verbose_printed:
            self._dump_layers(layers)
            self._has_verbose_printed = True
        return self._assemble_and_truncate(layers)

    def _dump_layers(self, layers: list[tuple[str, str]]):
        """输出各层统计到 stderr（--verbose 模式用）。"""
        print("── System Prompt Layers ──", file=sys.stderr)
        total_chars = 0
        total_lines = 0
        for name, content in layers:
            chars = len(content)
            lines = content.count("\n") + 1
            total_chars += chars
            total_lines += lines
            print(f"  [{name}] {chars} 字符 / {lines} 行", file=sys.stderr)
        print(f"  总计: {total_chars} 字符 / {total_lines} 行", file=sys.stderr)
        print("─────────────────────────", file=sys.stderr)

    def _assemble_and_truncate(self, layers: list[tuple[str, str]]) -> str:
        """组装各层为最终 system prompt，超长时按层丢弃。"""
        result = "\n\n".join(f"# {name}\n{content}" for name, content in layers)
        if len(result) <= self.max_prompt_chars:
            return result

        # 层太少，硬截断兜底
        if len(layers) <= 4:
            return result[: self.max_prompt_chars]

        head = layers[:2]
        tail = layers[-2:]
        middle = layers[2:-2]

        # 从后往前整层丢弃中间层，直到总长度不超限
        for keep in range(len(middle), -1, -1):
            kept = head + middle[:keep] + tail
            parts = [f"# {name}\n{content}" for name, content in kept]

            dropped = len(middle) - keep
            if dropped > 0:
                # 在 head 之后插入截断提示
                parts.insert(2 + keep, f"...(中间 {dropped} 层已截断)...")

            assembled = "\n\n".join(parts)
            if len(assembled) <= self.max_prompt_chars:
                return assembled

        # 极端兜底：head + tail 自身超限
        head_text = "\n\n".join(f"# {name}\n{content}" for name, content in head)
        tail_text = "\n\n".join(f"# {name}\n{content}" for name, content in tail)
        sep = "\n\n...(中间内容已截断)...\n\n"
        return (head_text + sep + tail_text)[: self.max_prompt_chars]
