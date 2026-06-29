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
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Callable

logger = logging.getLogger("chips.agent.sub_agent")


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
    """子 Agent 生命周期 + 结果存储 + 检索 + 持久化。

    内部以 role_name → list[SubAgentRecord] 组织，
    同时维护 id → record 的扁平索引方便查询。
    每角色最多保留 max_history_per_role 条记录（淘汰最旧的）。

    可选对接 SessionDB 实现持久化：
      manager.set_session(session_db, session_id)
      manager.restore(session_id)  # 启动时恢复历史
    """

    def __init__(self, max_history_per_role: int = 5, cleanup_interval: int = 30):
        self._max = max_history_per_role
        self._agents: dict[str, list[SubAgentRecord]] = {}
        self._by_id: dict[str, SubAgentRecord] = {}
        self._session_db: Any = None
        self._session_id: str = ""
        # 生命周期钩子: event → [fn(record), ...]
        self._hooks: dict[str, list[Callable]] = {
            "created": [],
            "running": [],
            "completed": [],
            "failed": [],
            "cancelled": [],
        }
        # 超时控制
        self._ttl: dict[str, float] = {}  # record_id → 截止时间戳
        self._cleaner_thread: threading.Thread | None = None
        self._cleanup_interval = cleanup_interval
        self._cleaner_stop = threading.Event()
        # 锁（线程安全）
        self._lock = threading.Lock()

    def on(self, event: str, fn: Callable) -> None:
        """注册生命周期钩子。event: created/running/completed/failed/cancelled。"""
        if event in self._hooks:
            self._hooks[event].append(fn)

    def off(self, event: str, fn: Callable) -> None:
        """移除已注册的钩子。"""
        if event in self._hooks and fn in self._hooks[event]:
            self._hooks[event].remove(fn)

    def _emit(self, event: str, record: SubAgentRecord) -> None:
        """触发生命周期钩子（异常安全）。"""
        for fn in self._hooks.get(event, []):
            try:
                fn(record)
            except Exception as exc:
                logger.error("sub_agent_hook_error event=%s fn=%s error=%s",
                             event, getattr(fn, "__name__", "?"), exc)

    # ── 超时控制 ──

    def create_with_ttl(self, agent_name: str, task: str, ttl: int = 300) -> str:
        """创建子 Agent 并设定超时秒数。超时后自动 CANCELLED。"""
        record_id = self.create(agent_name, task)
        deadline = time.time() + ttl
        with self._lock:
            self._ttl[record_id] = deadline
        return record_id

    def _cleaner_loop(self):
        """后台线程：定期扫描超时的 running 记录 → 自动取消。"""
        while not self._cleaner_stop.is_set():
            self._cleaner_stop.wait(self._cleanup_interval)
            self._check_timeouts()

    def _check_timeouts(self):
        """检查所有 running 的子 Agent，超时的自动取消。"""
        now = time.time()
        to_cancel: list[str] = []
        with self._lock:
            for rid, deadline in list(self._ttl.items()):
                if now >= deadline:
                    to_cancel.append(rid)
                    del self._ttl[rid]

        for rid in to_cancel:
            try:
                record = self._by_id.get(rid)
                if record is not None and record.status == AgentStatus.RUNNING:
                    self.update(rid, status=AgentStatus.CANCELLED)
                    logger.info("sub_agent_timeout_cancelled id=%s agent=%s ttl_exceeded",
                                rid, record.agent_name)
            except Exception as exc:
                logger.warning("sub_agent_timeout_error id=%s error=%s", rid, exc)

    def _remove_ttl(self, record_id: str) -> None:
        """移除超时记录（子 Agent 正常结束时调用）。"""
        with self._lock:
            self._ttl.pop(record_id, None)

    def start_cleaner(self) -> None:
        """启动后台超时清理线程。"""
        if self._cleaner_thread is not None and self._cleaner_thread.is_alive():
            return
        self._cleaner_stop.clear()
        self._cleaner_thread = threading.Thread(
            target=self._cleaner_loop,
            name="sub-agent-cleaner",
            daemon=True,
        )
        self._cleaner_thread.start()
        logger.info("sub_agent_cleaner_started interval=%d", self._cleanup_interval)

    def stop_cleaner(self, timeout: float = 5.0) -> None:
        """停止后台清理线程。"""
        self._cleaner_stop.set()
        if self._cleaner_thread is not None:
            self._cleaner_thread.join(timeout=timeout)
            self._cleaner_thread = None
            logger.info("sub_agent_cleaner_stopped")

    # ── Session 绑定 ──

    def set_session(self, session_db: Any, session_id: str) -> None:
        """绑定会话上下文，后续 create/update 自动持久化。"""
        self._session_db = session_db
        self._session_id = session_id

    def restore(self, session_id: str) -> int:
        """从数据库恢复该会话的历史子 Agent 记录。

        Returns:
            恢复的记录条数
        """
        if not self._session_db:
            return 0
        rows = self._session_db.get_session_sub_agents(session_id)
        count = 0
        for row in rows:
            rid = row["id"]
            if rid in self._by_id:
                continue
            record = SubAgentRecord(
                id=rid,
                agent_name=row["agent_name"],
                task=row["task"],
                started_at=row["started_at"],
                status=AgentStatus(row["status"]),
                completed_at=row.get("completed_at"),
                output=row.get("output", ""),
                error=row.get("error", ""),
                prompt_tokens=row.get("prompt_tokens", 0),
                completion_tokens=row.get("completion_tokens", 0),
                tool_calls=row.get("tool_calls", []),
            )
            self._by_id[rid] = record
            agent_name = record.agent_name
            if agent_name not in self._agents:
                self._agents[agent_name] = []
            self._agents[agent_name].append(record)
            count += 1
        self._session_id = session_id
        return count

    def _persist(self, record: SubAgentRecord) -> None:
        """将记录写入数据库（如果已绑定 session）。"""
        if not self._session_db or not self._session_id:
            return
        try:
            d = asdict(record)
            d["status"] = record.status.value
            d["session_id"] = self._session_id
            self._session_db.save_sub_agent(d)
        except Exception as exc:
            logger.error("sub_agent_persist_failed id=%s error=%s", record.id, exc)

    def _log_event(
        self, record_id: str, from_status: str, to_status: str
    ) -> None:
        """记录状态变更事件到 DB。"""
        if not self._session_db:
            return
        try:
            self._session_db.save_sub_agent_event(record_id, from_status, to_status)
        except Exception as exc:
            logger.error("sub_agent_event_failed id=%s error=%s", record_id, exc)

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

        # 写入 DB
        self._persist(record)

        # 触发钩子
        self._emit("created", record)

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

        # 状态转换校验 + 事件记录
        old_status: str | None = None
        if "status" in fields:
            new_status = fields["status"]
            if isinstance(new_status, str):
                new_status = AgentStatus(new_status)
            AgentStatus.validate_transition(record.status, new_status)
            old_status = record.status.value
            fields["status"] = new_status

        for k, v in fields.items():
            if hasattr(record, k):
                setattr(record, k, v)

        # 持久化 + 事件 + 钩子
        self._persist(record)
        if old_status is not None:
            self._log_event(id, old_status, record.status.value)
            self._emit(record.status.value, record)
            # 到达终态 → 移除 TTL
            if record.status in (AgentStatus.COMPLETED, AgentStatus.FAILED, AgentStatus.CANCELLED):
                self._remove_ttl(id)

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
        """转成 list 所用的精简输出（跳过 messages，枚举转值）。"""
        d = {}
        for k, v in asdict(r).items():
            if k == "messages":
                continue
            if isinstance(v, AgentStatus):
                v = v.value
            d[k] = v
        return d
