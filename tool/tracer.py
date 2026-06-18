"""Tracer — 分布式追踪：TraceID + Span 树

设计：
  - 每个 conversation 对应一个 trace（trace_id）
  - 每个操作单元（LLM call、tool exec）是一个 span
  - span 通过 parent_span_id 形成因果树
  - tracer 是模块级单例，agent loop 在每轮对话前创建新 trace

Span 树结构：
  trace
  └── agent.run                          (一次 run_conversation)
      ├── iteration.0
      │   ├── llm.call                   (LLM API 调用，含 token)
      │   ├── tool.exec                  (工具执行，含 args/result)
      │   └── ...
      ├── iteration.1
      │   ├── llm.call
      │   └── ...
      └── ...
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("chips.tool.tracer")

# 内存中保留的最大 trace 数（环形缓冲区）
_MAX_TRACES = 100

# Span 输入/输出字段截断长度
_SPAN_DATA_MAX_LEN = 2000


@dataclass
class Span:
    """一个操作单元。"""
    trace_id: str
    span_id: str
    parent_span_id: str | None
    operation: str            # "llm.call", "tool.exec", "agent.run", "iteration"
    start_time: float
    end_time: float | None = None
    status: str = "ok"        # "ok" | "error"
    # 输入/输出（截断后）
    input: str = ""
    output: str = ""
    # 元数据
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_ms(self) -> int:
        if self.end_time is None:
            return 0
        return int((self.end_time - self.start_time) * 1000)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "operation": self.operation,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "input_truncated": self.input[:_SPAN_DATA_MAX_LEN] if self.input else "",
            "output_truncated": self.output[:_SPAN_DATA_MAX_LEN] if self.output else "",
            "metadata": self.metadata,
        }


class Tracer:
    """追踪器。模块级单例，管理 trace 生命周期。"""

    def __init__(self) -> None:
        self.trace_id: str = ""
        self._spans: list[Span] = []
        self._trace_metadata: dict[str, Any] = {}
        # trace 历史（环形缓冲区），按 trace_id 索引
        self._trace_history: dict[str, list[Span]] = {}
        self._trace_meta: dict[str, dict[str, Any]] = {}
        self._trace_order: list[str] = []

    # ── Trace 生命周期 ──

    def new_trace(self, metadata: dict[str, Any] | None = None) -> str:
        """创建新 trace，可选附加元数据（如 session_id）。返回 trace_id。"""
        self.trace_id = uuid.uuid4().hex[:16]
        self._spans = []
        self._trace_metadata = metadata or {}
        return self.trace_id

    def end_trace(self) -> str | None:
        """结束当前 trace，归档到历史。"""
        if not self.trace_id:
            return None
        tid = self.trace_id
        self._trace_history[tid] = list(self._spans)
        self._trace_meta[tid] = dict(self._trace_metadata)
        self._trace_order.append(tid)
        # 环形缓冲区：超过上限时淘汰最旧 trace
        if len(self._trace_order) > _MAX_TRACES:
            old = self._trace_order.pop(0)
            self._trace_history.pop(old, None)
            self._trace_meta.pop(old, None)
        self._spans = []
        self._trace_metadata = {}
        return tid

    # ── Span 管理 ──

    def start_span(
        self,
        operation: str,
        *,
        parent_span_id: str | None = None,
        input: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """开始一个 span，返回 span_id。"""
        span_id = uuid.uuid4().hex[:12]
        span = Span(
            trace_id=self.trace_id,
            span_id=span_id,
            parent_span_id=parent_span_id,
            operation=operation,
            start_time=time.time(),
            input=input,
            metadata=metadata or {},
        )
        self._spans.append(span)
        return span_id

    def end_span(
        self,
        span_id: str,
        *,
        status: str = "ok",
        output: str = "",
        metadata: dict[str, Any] | None = None,
    ):
        """结束一个 span，记录结束时间和输出。"""
        for span in self._spans:
            if span.span_id == span_id and span.end_time is None:
                span.end_time = time.time()
                span.status = status
                span.output = output
                if metadata:
                    span.metadata.update(metadata)
                return
        logger.warning("end_span not_found span_id=%s", span_id)

    # ── 查询 ──

    def get_trace(self, trace_id: str) -> list[dict[str, Any]] | None:
        """获取指定 trace 的 span 列表（已格式化为 dict）。"""
        spans = self._trace_history.get(trace_id)
        if spans is None:
            return None
        return [s.to_dict() for s in spans]

    def get_trace_tree(self, trace_id: str) -> list[dict[str, Any]] | None:
        """获取指定 trace 的 span 树（按 parent 关系嵌套）。"""
        spans = self._trace_history.get(trace_id)
        if not spans:
            return None
        # 构建孩子索引
        children: dict[str, list[dict[str, Any]]] = {}
        root: dict[str, Any] | None = None
        for s in spans:
            d = s.to_dict()
            d["children"] = []
            pid = d["parent_span_id"]
            if pid:
                children.setdefault(pid, []).append(d)
            else:
                root = d
        # 递归嵌套
        def _attach(parent: dict[str, Any]):
            parent["children"] = children.get(parent["span_id"], [])
            for c in parent["children"]:
                _attach(c)
        if root:
            _attach(root)
            return [root]
        return list(children.values())

    def list_traces(self, limit: int = 20) -> list[dict[str, Any]]:
        """列出最近的 trace 摘要。"""
        result = []
        for tid in reversed(self._trace_order[-limit:]):
            spans = self._trace_history.get(tid, [])
            if not spans:
                continue
            root = next((s for s in spans if s.parent_span_id is None), spans[0])
            duration = int((spans[-1].start_time - spans[0].start_time) * 1000) if len(spans) > 1 else 0
            errors = sum(1 for s in spans if s.status == "error")
            entry: dict[str, Any] = {
                "trace_id": tid,
                "span_count": len(spans),
                "duration_ms": duration,
                "errors": errors,
                "started_at": spans[0].start_time,
            }
            meta = self._trace_meta.get(tid)
            if meta:
                entry["metadata"] = meta
            result.append(entry)
        return result

    # ── 活跃 span 引用（供日志 filter 使用） ──

    @property
    def current_trace_id(self) -> str:
        return self.trace_id


# 模块级单例
tracer = Tracer()
