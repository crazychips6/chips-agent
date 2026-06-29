"""SubAgentRecord + SubAgentManager — 子 Agent 生命周期管理与结果存储

数据层，零内部依赖，仅使用标准库。
供 AIAgent 在 ReAct 循环中管理子 Agent 的生命周期。

Usage::
    manager = SubAgentManager(max_history_per_role=5)
    record_id = manager.create("researcher", "搜索 API 文档")
    manager.update(record_id, status=AgentStatus.RUNNING)
    # ... 子 Agent 执行 ...
    manager.update(record_id, status=AgentStatus.COMPLETED, output="...")
    result = manager.get(record_id)
    all_results = manager.list_all()
"""

from __future__ import annotations

import enum
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any


class AgentStatus(enum.Enum):
    """子 Agent 生命周期的合法状态。

    合法转换:
        CREATED → RUNNING → COMPLETED
                          → FAILED
                          → CANCELLED
        COMPLETED / FAILED / CANCELLED  → (终态)
    """

    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    def can_transition_to(self, target: AgentStatus) -> bool:
        """判断从当前状态能否转换到目标状态。"""
        return target in _STATUS_TRANSITIONS.get(self, set())

    @staticmethod
    def validate_transition(current: AgentStatus, target: AgentStatus) -> None:
        """校验状态转换，非法时抛 ValueError。"""
        if current.can_transition_to(target):
            return
        msg = f"非法状态转换: {current.value} → {target.value}"
        raise ValueError(msg)


# 合法转换表（定义在类外部，避免 enum 元类干扰）
_STATUS_TRANSITIONS: dict[AgentStatus, set[AgentStatus]] = {
    AgentStatus.CREATED: {AgentStatus.RUNNING},
    AgentStatus.RUNNING: {AgentStatus.COMPLETED, AgentStatus.FAILED, AgentStatus.CANCELLED},
    AgentStatus.COMPLETED: set(),
    AgentStatus.FAILED: set(),
    AgentStatus.CANCELLED: set(),
}


@dataclass
class SubAgentRecord:
    """单个子 Agent 的执行记录。

    从 CREATED 到 COMPLETED/FAILED/CANCELLED，完整生命周期。
    messages 和 tool_calls 只在 get() 时返回，list() 跳过以节省 token。
    """

    id: str
    agent_name: str                      # 角色名（如 "researcher"）或 "(inline)"
    task: str                            # 子任务描述
    started_at: float
    status: AgentStatus = AgentStatus.CREATED
    completed_at: float | None = None
    iterations: int = 0
    messages: list[dict] | None = None   # 子 Agent 的完整消息列表（仅 get 时可见）
    tool_calls: list[dict] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    output: str = ""
    error: str | None = None


# ── 工具函数 ──


def _extract_tool_calls(messages: list[dict]) -> list[dict]:
    """从子 Agent 的消息列表中提取工具调用链。

    返回: [{name, args, result_preview, duration_ms?}, ...]
    """
    calls: list[dict] = []
    for msg in messages:
        if msg.get("role") == "assistant":
            for tc in msg.get("tool_calls", ()):
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                calls.append({
                    "name": fn.get("name", "?") if isinstance(fn, dict) else "?",
                    "arguments": fn.get("arguments", "") if isinstance(fn, dict) else "",
                    "result": "",
                })
        elif msg.get("role") == "tool":
            if calls and not calls[-1].get("result"):
                content = msg.get("content", "") or ""
                calls[-1]["result"] = content[:500]
    return calls


def _estimate_messages_tokens(messages: list[dict]) -> tuple[int, int]:
    """粗略估算消息列表的 prompt 和 completion token 数。

    按每 4 字符 ≈ 1 token 估算，不依赖 tiktoken。
    """
    prompt = 0
    completion = 0
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "") or ""
        if isinstance(content, list):
            content = " ".join(
                b.get("text", "") if isinstance(b, dict) else str(b)
                for b in content
            )
        tokens = len(content) // 4 + 5  # content + role overhead
        if role == "assistant":
            completion += tokens
            # tool_calls 参数
            for tc in msg.get("tool_calls", ()):
                if isinstance(tc, dict):
                    fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                    args_str = fn.get("arguments", "") if isinstance(fn, dict) else ""
                    completion += len(args_str) // 4
        else:
            prompt += tokens
    return prompt, completion


# ── SubAgentManager ──


class SubAgentManager:
    """子 Agent 生命周期 + 结果存储 + 检索。

    内部以 role_name → list[SubAgentRecord] 组织，
    同时维护 id → record 的扁平索引方便查询。
    每角色最多保留 max_history_per_role 条记录（淘汰最旧的）。
    """

    def __init__(self, max_history_per_role: int = 5):
        self._max = max_history_per_role
        self._agents: dict[str, list[SubAgentRecord]] = {}
        self._by_id: dict[str, SubAgentRecord] = {}

    # ── 生命周期 ──

    def create(self, agent_name: str, task: str) -> str:
        """创建子 Agent 记录，返回 record id。状态为 CREATED。"""
        record_id = uuid.uuid4().hex[:12]
        record = SubAgentRecord(
            id=record_id,
            agent_name=agent_name,
            task=task,
            status=AgentStatus.CREATED,
            started_at=time.time(),
        )
        self._by_id[record_id] = record

        # 按角色分组
        if agent_name not in self._agents:
            self._agents[agent_name] = []
        self._agents[agent_name].append(record)

        # 淘汰超限：保留最新的 max 条
        if len(self._agents[agent_name]) > self._max:
            removed = self._agents[agent_name].pop(0)
            self._by_id.pop(removed.id, None)

        return record_id

    def update(self, id: str, **fields: Any) -> None:
        """更新记录字段。支持 status / output / error 等。

        当 status 变化时自动校验状态转换合法性。
        字段值为 AgentStatus 枚举或字符串均可。

        Raises:
            ValueError: id 不存在 或 非法状态转换
        """
        record = self._by_id.get(id)
        if record is None:
            raise ValueError(f"Unknown sub-agent id: {id}")

        # 状态转换校验
        if "status" in fields:
            new_status = fields["status"]
            if isinstance(new_status, str):
                new_status = AgentStatus(new_status)
            AgentStatus.validate_transition(record.status, new_status)
            fields["status"] = new_status

        for k, v in fields.items():
            if hasattr(record, k):
                setattr(record, k, v)

    # ── 结果捕获（子 Agent 执行完毕后调用） ──

    def capture_result(
        self,
        id: str,
        messages: list[dict],
        output: str,
        *,
        error: str | None = None,
    ) -> None:
        """子 Agent 执行完成后，捕获其消息链、工具调用、token 估算。"""
        tool_calls = _extract_tool_calls(messages)
        prompt_tok, comp_tok = _estimate_messages_tokens(messages)

        # 迭代次数 ≈ assistant 消息数
        iterations = sum(
            1 for m in messages
            if m.get("role") == "assistant"
        )

        self.update(
            id,
            status=AgentStatus.FAILED if error else AgentStatus.COMPLETED,
            completed_at=time.time(),
            iterations=iterations,
            messages=messages,
            tool_calls=tool_calls,
            prompt_tokens=prompt_tok,
            completion_tokens=comp_tok,
            output=output,
            error=error,
        )

    # ── 查询 ──

    def get(self, id: str) -> SubAgentRecord | None:
        """按 id 查询，返回完整记录（含 messages）。"""
        return self._by_id.get(id)

    def list(self, agent_name: str | None = None) -> list[dict]:
        """按角色名列出子 Agent 记录（不含 messages，避免刷 token）。

        Args:
            agent_name: 不传则返回所有角色前 5 条。
        """
        if agent_name:
            records = self._agents.get(agent_name, [])
        else:
            records = []
            for rs in self._agents.values():
                records.extend(rs)

        records.sort(key=lambda r: r.started_at, reverse=True)
        return [self._to_list_item(r) for r in records]

    def list_all(self) -> list[dict]:
        """列出所有子 Agent 记录，按时间降序。"""
        all_records: list[SubAgentRecord] = []
        for rs in self._agents.values():
            all_records.extend(rs)
        all_records.sort(key=lambda r: r.started_at, reverse=True)
        return [self._to_list_item(r) for r in all_records]

    def get_latest(self, agent_name: str) -> SubAgentRecord | None:
        """获取某角色最近一次执行记录。"""
        records = self._agents.get(agent_name)
        if not records:
            return None
        return records[-1]

    # ── 内部 ──

    @staticmethod
    def _to_list_item(r: SubAgentRecord) -> dict:
        """转成 list 所用的精简输出（跳过 messages）。"""
        return {k: v for k, v in asdict(r).items() if k != "messages"}
