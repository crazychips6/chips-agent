"""CassetteGateway — LLM 调用录制/回放网关

用途
----
录制回放用于回归测试：
  1. 先以 record 模式跑一遍真实场景，将 LLM request/response 存为 YAML cassette。
  2. 后续 CI / 本地回归时以 replay 模式加载 cassette，
     匹配请求并返回录制的响应，不依赖真实 API。

模式
----
- record:  包装真实 ModelGateway，透传调用并记录所有交互，调用 save() 写入文件
- replay:  从 YAML 加载 cassette，根据匹配策略返回录制的响应

匹配策略
--------
- exact:      messages + model 精确匹配（含顺序、内容完全一致）
- sequential: 按顺序依次返回，不比较请求内容（适合确定性测试流程）
- fuzzy:      按 model + 最后一条 user message 内容匹配（容忍时间戳等微小变化）

用法示例
--------
:: 记录模式（真实调用，生成 cassette）
    gw = CassetteGateway(
        gateway=OpenAIProvider(api_key="..."),
        mode="record",
        path="test/cassettes/weather.yaml",
    )
    agent = AIAgent(model="deepseek-chat", gateway=gw)
    agent.chat("北京天气怎么样？")
    gw.save()

:: 回放模式（加载 cassette，无 API 调用）
    gw = CassetteGateway.load("test/cassettes/weather.yaml")
    agent = AIAgent(model="deepseek-chat", gateway=gw)
    result = agent.chat("北京天气怎么样？")
    assert "北京" in result
"""

from __future__ import annotations

import copy
import dataclasses
import logging
import os
from collections.abc import Callable
from typing import Any, Literal

import yaml

from gateway.protocol import ModelGateway
from gateway.types import ChatResult

logger = logging.getLogger("chips.gateway.cassette")

# ── 匹配策略 ──

MatchStrategy = Literal["exact", "sequential", "fuzzy"]

# ── 单个交互记录 ──


class Interaction:
    """一次 LLM 调用请求与响应的记录。"""

    def __init__(self, request: dict, response: dict):
        self.request = request
        self.response = response

    def to_dict(self) -> dict:
        return {"request": self.request, "response": self.response}

    @staticmethod
    def from_dict(d: dict) -> Interaction:
        return Interaction(d["request"], d["response"])


# ── CassetteGateway ──


class CassetteGateway(ModelGateway):
    """录制/回放 LLM 调用。

    record 模式包装真实 gateway；replay 模式使用已保存的数据。

    Parameters
    ----------
    mode: "record" | "replay"
    path: str
        cassette 文件路径（YAML）。
    gateway: ModelGateway | None
        record 模式时需要提供真实 gateway。
    strategy: MatchStrategy
        回放时请求匹配策略。默认 "exact"。
    """

    def __init__(
        self,
        mode: Literal["record", "replay"] = "replay",
        path: str = "",
        gateway: ModelGateway | None = None,
        strategy: MatchStrategy = "exact",
    ):
        if mode == "record" and gateway is None:
            raise ValueError("record 模式必须提供真实 gateway")
        if mode == "replay" and not path:
            raise ValueError("replay 模式必须提供 cassette 路径")

        self._mode = mode
        self._path = path
        self._gateway = gateway
        self._strategy = strategy

        self._interactions: list[Interaction] = []
        self._replay_index = 0  # sequential 模式用

        self._meta: dict[str, Any] = {}

        # replay 模式 → 加载
        if mode == "replay" and os.path.exists(path):
            self._load_file(path)

    # ── 公开接口 ──

    @property
    def interaction_count(self) -> int:
        return len(self._interactions)

    def save(self, path: str | None = None) -> str:
        """将录制的交互写入 YAML 文件。

        Returns
        -------
        写入的文件路径。
        """
        out_path = path or self._path
        if not out_path:
            raise ValueError("未指定 cassette 路径")

        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        data = {
            "meta": {
                "recorded_at": self._meta.get("recorded_at", ""),
                "model": self._meta.get("model", ""),
                "interaction_count": len(self._interactions),
                "strategy": self._strategy,
            },
            "interactions": [i.to_dict() for i in self._interactions],
        }
        with open(out_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        logger.info("cassette_saved path=%s interactions=%d", out_path, len(self._interactions))
        return out_path

    @classmethod
    def load(cls, path: str, strategy: MatchStrategy = "exact") -> CassetteGateway:
        """从 YAML 文件加载 cassette，返回 replay 模式的 CassetteGateway。"""
        gw = cls(mode="replay", path=path, strategy=strategy)
        return gw

    def clear(self):
        """清空所有录制交互（测试清理用）。"""
        self._interactions.clear()
        self._replay_index = 0

    # ── ModelGateway 协议 ──

    def chat(self, messages: list[dict[str, Any]], model: str = "",
             **kwargs: Any) -> ChatResult:
        if self._mode == "record":
            return self._record(messages, model, False, None, **kwargs)
        return self._replay(messages, model, **kwargs)

    def chat_stream(self, messages: list[dict[str, Any]], model: str = "",
                    *, on_chunk: Callable[[str], None] | None = None,
                    **kwargs: Any) -> ChatResult:
        if self._mode == "record":
            return self._record(messages, model, True, on_chunk, **kwargs)
        return self._replay(messages, model, **kwargs)

    # ── 内部：录制 ──

    def _record(self, messages: list[dict], model: str, stream: bool,
                on_chunk: Callable | None, **kwargs) -> ChatResult:
        """真实调用并记录。"""
        if self._gateway is None:
            raise RuntimeError("record 模式需要真实 gateway")

        result = self._gateway.chat_stream(
            messages=messages, model=model, on_chunk=on_chunk, **kwargs,
        ) if stream else self._gateway.chat(
            messages=messages, model=model, **kwargs,
        )

        self._meta["model"] = model
        if not self._meta.get("recorded_at"):
            from datetime import datetime, timezone
            self._meta["recorded_at"] = datetime.now(timezone.utc).isoformat()

        interaction = Interaction(
            request={
                "messages": _sanitize_messages(messages),
                "model": model,
                "stream": stream,
                "kwargs": _strip_sensitive(kwargs),
            },
            response=_chat_result_to_dict(result),
        )
        self._interactions.append(interaction)
        return result

    # ── 内部：回放 ──

    def _replay(self, messages: list[dict], model: str, **kwargs) -> ChatResult:
        """在 cassette 中查找匹配的响应。"""
        if self._strategy == "sequential":
            return self._replay_sequential()
        if self._strategy == "fuzzy":
            return self._replay_fuzzy(messages, model)

        return self._replay_exact(messages, model, **kwargs)

    def _replay_exact(self, messages: list[dict], model: str, **kwargs) -> ChatResult:
        """精确匹配 messages + model + kwargs。"""
        sanitized = _sanitize_messages(messages)
        for i, interaction in enumerate(self._interactions):
            req = interaction.request
            if (req.get("model") == model
                    and req.get("messages") == sanitized
                    and _kwargs_match(req.get("kwargs", {}), kwargs)):
                return _dict_to_chat_result(interaction.response)

        _raise_no_match(messages, model, self._path, self._interactions)

    def _replay_sequential(self) -> ChatResult:
        """按顺序返回下一个响应。"""
        if self._replay_index >= len(self._interactions):
            raise RuntimeError(
                f"cassette 耗尽：已使用 {self._replay_index}/{len(self._interactions)} 个交互，"
                f"但需要更多响应"
            )
        result = _dict_to_chat_result(self._interactions[self._replay_index].response)
        self._replay_index += 1
        return result

    def _replay_fuzzy(self, messages: list[dict], model: str) -> ChatResult:
        """模糊匹配：同 model + 最后一条 user message。"""
        last_user = _last_user_message(messages)
        if not last_user:
            return self._replay_exact(messages, model)

        candidates = []
        for i, interaction in enumerate(self._interactions):
            req = interaction.request
            if req.get("model") != model:
                continue
            recorded_last = _last_user_message(req.get("messages", []))
            if recorded_last and _fuzzy_match_text(last_user, recorded_last):
                candidates.append(interaction)

        if len(candidates) == 1:
            return _dict_to_chat_result(candidates[0].response)
        if len(candidates) > 1:
            # 多个候选 → 用精确匹配缩小
            sanitized = _sanitize_messages(messages)
            for c in candidates:
                if c.request.get("messages") == sanitized:
                    return _dict_to_chat_result(c.response)
            # 取第一个
            return _dict_to_chat_result(candidates[0].response)

        _raise_no_match(messages, model, self._path, self._interactions)

    # ── 文件加载 ──

    def _load_file(self, path: str):
        """从 YAML 加载 cassette。"""
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not data or "interactions" not in data:
            raise ValueError(f"cassette 文件格式错误：{path}")

        self._meta = data.get("meta", {})
        self._interactions = [
            Interaction.from_dict(d) for d in data["interactions"]
        ]
        self._replay_index = 0
        logger.info("cassette_loaded path=%s interactions=%d", path, len(self._interactions))


# ── 辅助函数 ──


def _sanitize_messages(messages: list[dict]) -> list[dict]:
    """清洗消息列表，移除不可序列化或非关键的字段。"""
    cleaned = []
    for msg in messages:
        m = {}
        for key in ("role", "content", "name", "tool_calls", "tool_call_id"):
            if key in msg:
                m[key] = copy.deepcopy(msg[key])
        # content 可能为 None（tool_call 消息）
        if "content" in m and m["content"] is None:
            m["content"] = ""
        cleaned.append(m)
    return cleaned


def _strip_sensitive(kwargs: dict) -> dict:
    """移除 kwargs 中的敏感参数（如 api_key），保留匹配用参数。"""
    allowed = {"temperature", "max_tokens", "top_p", "stream"}
    return {k: v for k, v in kwargs.items() if k in allowed}


def _kwargs_match(a: dict, b: dict) -> bool:
    """比较两套 kwargs 是否近似匹配（只比较共有参数）。"""
    if not a and not b:
        return True
    # 只检查 a 中有的 key（b 多出的不比较）
    for k in a:
        if k in b and a[k] != b[k]:
            return False
    return True


def _last_user_message(messages: list[dict]) -> str:
    """取最后一条 role=user 的内容。"""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, list):
                # 多模态消息，取所有 text 片段拼接
                texts = [p.get("text", "") for p in content if isinstance(p, dict)]
                return " ".join(texts)
            return str(content)
    return ""


def _fuzzy_match_text(a: str, b: str) -> bool:
    """模糊文本匹配：去除首尾空白后比较。"""
    return a.strip() == b.strip()


def _chat_result_to_dict(result: ChatResult) -> dict:
    """ChatResult → dict（用于序列化）。"""
    d = {"content": result.content}
    if result.tool_calls:
        d["tool_calls"] = result.tool_calls
    if result.reasoning_content:
        d["reasoning_content"] = result.reasoning_content
    if result.usage:
        d["usage"] = dict(result.usage)
    if result.model:
        d["model"] = result.model
    return d


def _dict_to_chat_result(d: dict) -> ChatResult:
    """dict → ChatResult（从序列化恢复）。"""
    return ChatResult(
        content=d.get("content", ""),
        tool_calls=d.get("tool_calls"),
        reasoning_content=d.get("reasoning_content"),
        usage=d.get("usage"),
        model=d.get("model", ""),
        latency_ms=0,
    )


def _raise_no_match(messages: list[dict], model: str, path: str,
                    interactions: list[Interaction]):
    """找不到匹配时抛异常，附带排查信息。"""
    last = _last_user_message(messages)
    hint = f"最后一条 user 消息: {last[:80]}" if last else "(无 user 消息)"
    available = "\n".join(
        f"  [{i}] model={it.request.get('model')!r} "
        f"last_user={_last_user_message(it.request.get('messages', []))[:60]}"
        for i, it in enumerate(interactions)
    )
    raise RuntimeError(
        f"cassette 中未找到匹配的请求\n"
        f"  cassette: {path}\n"
        f"  model: {model!r}\n"
        f"  {hint}\n"
        f"  cassette 中有 {len(interactions)} 条记录:\n"
        f"{available}"
    )
