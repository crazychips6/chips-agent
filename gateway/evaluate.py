"""CassetteEvaluator — 结构化评价 + 诊断

基于录制 cassette 的回归测试评价框架。
在回放后对 Agent 的行为做断言，输出结构化结果。

用法
----
:: 在 cassette 中嵌入期望（YAML，可手动修改）
    meta:
      expect:
        final_output:
          contains: ["晴天", "北京"]
        tool_calls:
          min_count: 1
          max_count: 5
          names: ["get_weather"]
          sequence_contains: ["get_weather"]

:: 代码评价
    from gateway.evaluate import CassetteEvaluator

    evaluator = CassetteEvaluator("test/cassettes/weather.yaml")
    result = evaluator.evaluate(agent)  # 传入已运行的 agent
    # 或基于 trace 数据评价：
    result = evaluator.evaluate_trace(trace)

    if not result.passed:
        print(result.diagnosis)  # "第 2 步：期望 get_weather，实际调用了 search_web"
        for check in result.checks:
            print(f"  [{ '✓' if check.passed else '✗' }] {check.name}")
            if not check.passed:
                print(f"    期望: {check.expected}")
                print(f"    实际: {check.actual}")
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

import yaml


# ── 评价结果类型 ──


@dataclass
class CheckResult:
    """单项断言结果。"""
    name: str                           # 检查项名称（如"最终输出包含'晴天'"）
    passed: bool                        # 是否通过
    expected: str = ""                  # 期望值描述
    actual: str = ""                    # 实际值描述
    details: dict | None = None         # 额外诊断信息


@dataclass
class EvaluationResult:
    """一次评价的完整结果。"""
    passed: bool                        # 是否全部通过
    checks: list[CheckResult] = field(default_factory=list)
    total: int = 0
    passed_count: int = 0
    failed_count: int = 0
    diagnosis: str = ""                 # 人工可读的诊断结论
    trace: list[dict] | None = None     # 完整 trace（用于深度排查）
    latency_ms: int = 0

    @property
    def summary(self) -> str:
        """简要一行结果。"""
        return f"{'✓ PASS' if self.passed else '✗ FAIL'}  {self.passed_count}/{self.total} 通过"

    def print_report(self):
        """打印完整报告。"""
        status = "✓ PASS" if self.passed else "✗ FAIL"
        print(f"\n{'='*50}")
        print(f"  评价结果: {status}")
        print(f"  通过: {self.passed_count}/{self.total}")
        if self.latency_ms:
            print(f"  耗时: {self.latency_ms}ms")
        print(f"{'='*50}")

        for check in self.checks:
            icon = "✓" if check.passed else "✗"
            print(f"  [{icon}] {check.name}")
            if not check.passed and check.expected:
                print(f"       期望: {check.expected}")
                print(f"       实际: {check.actual}")

        if self.diagnosis:
            print(f"\n  🔍 诊断: {self.diagnosis}")

        return self.passed


# ── 期望规则 ──


@dataclass
class ExpectRules:
    """从 cassette 解析的期望规则。"""

    # 最终输出断言
    final_contains: list[str] = field(default_factory=list)
    final_not_contains: list[str] = field(default_factory=list)
    final_regex: str = ""
    final_exact: str = ""

    # 工具调用断言
    tool_min_count: int | None = None
    tool_max_count: int | None = None
    tool_names: list[str] = field(default_factory=list)          # 必须出现的工具名
    tool_names_exact: list[str] = field(default_factory=list)    # 工具必须完全匹配此集合
    tool_sequence_contains: list[str] = field(default_factory=list)  # 调用顺序包含此子序列
    tool_args_match: dict[str, dict[str, Any]] = field(default_factory=dict)  # {工具名: {参数key: 期望值}}

    # 性能断言
    max_llm_calls: int | None = None
    max_latency_ms: int | None = None
    max_total_tokens: int | None = None

    # 自定义断言（Python 表达式，evaluate 时注入 trace 上下文）
    custom_checks: list[dict] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict | None) -> ExpectRules:
        """从 YAML expect 字段解析。"""
        if not data:
            return cls()

        final = data.get("final_output", {})
        tool = data.get("tool_calls", {})

        return cls(
            # 最终输出
            final_contains=final.get("contains", []),
            final_not_contains=final.get("not_contains", []),
            final_regex=final.get("regex", ""),
            final_exact=final.get("exact", ""),

            # 工具调用
            tool_min_count=tool.get("min_count"),
            tool_max_count=tool.get("max_count"),
            tool_names=tool.get("names", []),
            tool_names_exact=tool.get("names_exact", []),
            tool_sequence_contains=tool.get("sequence_contains", []),
            tool_args_match=tool.get("args_match", {}),

            # 性能
            max_llm_calls=data.get("max_llm_calls"),
            max_latency_ms=data.get("max_latency_ms"),
            max_total_tokens=data.get("max_total_tokens"),

            custom_checks=data.get("custom_checks", []),
        )


# ── Trace 提取 ──


def extract_trace(agent) -> dict:
    """从 AIAgent 实例提取本轮对话的完整 trace。

    Returns
    -------
    {
        "final_output": "最终回复文本",
        "llm_calls": [...],       # 所有 LLM call（含 request/response）
        "tool_calls": [...],       # 所有工具调用（name, args, result）
        "total_llm_calls": N,
        "latency_ms": N,
        "token_usage": { prompt_tokens: N, completion_tokens: N },
    }
    """
    messages = getattr(agent, "messages", [])
    trace_tool_calls = []
    llm_calls = []

    for msg in messages:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                trace_tool_calls.append({
                    "name": tc["function"]["name"],
                    "args": _safe_parse_json(tc["function"]["arguments"]),
                    "step": len(trace_tool_calls) + 1,
                })

    # 从 session_db 取 tool call 记录（如果有）
    tool_records = []
    if hasattr(agent, "session_db") and agent.session_db:
        try:
            tool_records = agent.session_db.get_tool_calls(
                session_id=agent.session_id,
                turn_number=getattr(agent, "turn_count", 0),
            )
        except Exception:
            pass

    # LLM call trace：从 debug_context 或 round 记录提取
    if hasattr(agent, "_AIAgent__rounds"):
        rounds = agent._AIAgent__rounds
    else:
        rounds = getattr(agent, "debug_context", None) or []

    for r in rounds:
        req = r.get("request", {})
        resp = r.get("response", {})
        llm_calls.append({
            "model": req.get("model", ""),
            "messages_len": len(req.get("messages", [])),
            "tools_count": len(req.get("tools", []) or []),
            "response_content": (resp.get("content", "") or "")[:200],
            "response_tool_calls": resp.get("tool_calls"),
            "reasoning": resp.get("reasoning_content"),
        })

    final_output = ""
    for msg in reversed(messages):
        if msg.get("role") == "assistant" and msg.get("content"):
            final_output = msg["content"]
            break

    return {
        "final_output": final_output,
        "llm_calls": llm_calls,
        "tool_calls": trace_tool_calls,
        "tool_records": tool_records,
        "total_llm_calls": len(llm_calls),
        "latency_ms": getattr(agent, "_last_latency_ms", 0),
        "messages": messages,
    }


# ── CassetteEvaluator ──


class CassetteEvaluator:
    """基于录制 cassette 的结构化评价器。

    工作方式：
      1. 加载 cassette 文件（含 expect 规则）
      2. 接收 trace（来自 agent 运行结果）
      3. 逐项断言并生成结构化报告
      4. 找出第一处偏离点并给出诊断

    两种使用方式：
      - evaluator.evaluate(agent)     — 传入已运行的 agent，自动提取 trace
      - evaluator.evaluate_trace(trace) — 传入预提取的 trace 数据
    """

    def __init__(self, cassette_path: str):
        if not os.path.exists(cassette_path):
            raise FileNotFoundError(f"cassette 文件不存在: {cassette_path}")

        with open(cassette_path, encoding="utf-8") as f:
            self._data = yaml.safe_load(f)

        if not self._data or "interactions" not in self._data:
            raise ValueError(f"cassette 文件格式错误: {cassette_path}")

        self._path = cassette_path
        self._expect = ExpectRules.from_dict(
            self._data.get("meta", {}).get("expect")
        )
        self._interactions = self._data.get("interactions", [])

    @property
    def expect(self) -> ExpectRules:
        return self._expect

    def evaluate(self, agent) -> EvaluationResult:
        """从 AIAgent 实例提取 trace 并评价。"""
        trace = extract_trace(agent)
        return self.evaluate_trace(trace)

    def evaluate_trace(self, trace: dict) -> EvaluationResult:
        """对 trace 数据执行断言评价。"""
        checks: list[CheckResult] = []
        failed = []
        latency = trace.get("latency_ms", 0)
        final = trace.get("final_output", "")
        tool_calls = trace.get("tool_calls", [])
        llm_calls = trace.get("llm_calls", [])

        rules = self._expect

        # ── 1. 最终输出内容检查 ──

        for keyword in rules.final_contains:
            c = CheckResult(
                name=f"最终输出包含'{keyword}'",
                passed=keyword in final,
                expected=f"应包含 '{keyword}'",
                actual=f"实际输出: {final[:200]}",
            )
            checks.append(c)
            if not c.passed:
                failed.append(c)

        for keyword in rules.final_not_contains:
            c = CheckResult(
                name=f"最终输出不包含'{keyword}'",
                passed=keyword not in final,
                expected=f"不应包含 '{keyword}'",
                actual=f"实际输出: {final[:200]}",
            )
            checks.append(c)
            if not c.passed:
                failed.append(c)

        if rules.final_regex:
            match = re.search(rules.final_regex, final)
            c = CheckResult(
                name=f"最终输出匹配正则 /{rules.final_regex}/",
                passed=bool(match),
                expected=f"应匹配 /{rules.final_regex}/",
                actual=f"实际输出: {final[:200]}",
            )
            checks.append(c)
            if not c.passed:
                failed.append(c)

        if rules.final_exact:
            c = CheckResult(
                name=f"最终输出精确匹配",
                passed=final == rules.final_exact,
                expected=rules.final_exact,
                actual=final[:200],
            )
            checks.append(c)
            if not c.passed:
                failed.append(c)

        # ── 2. 工具调用数量检查 ──

        call_count = len(tool_calls)
        if rules.tool_min_count is not None:
            c = CheckResult(
                name=f"工具调用次数 >= {rules.tool_min_count}",
                passed=call_count >= rules.tool_min_count,
                expected=f"≥ {rules.tool_min_count}",
                actual=f"实际 {call_count} 次",
            )
            checks.append(c)
            if not c.passed:
                failed.append(c)

        if rules.tool_max_count is not None:
            c = CheckResult(
                name=f"工具调用次数 <= {rules.tool_max_count}",
                passed=call_count <= rules.tool_max_count,
                expected=f"≤ {rules.tool_max_count}",
                actual=f"实际 {call_count} 次",
            )
            checks.append(c)
            if not c.passed:
                failed.append(c)

        # ── 3. 工具名称检查 ──

        actual_names = [tc["name"] for tc in tool_calls]
        unique_names = list(dict.fromkeys(actual_names))

        for expected_name in rules.tool_names:
            c = CheckResult(
                name=f"调用了工具 '{expected_name}'",
                passed=expected_name in unique_names,
                expected=f"应调用 '{expected_name}'",
                actual=f"实际调用: {unique_names}",
            )
            checks.append(c)
            if not c.passed:
                failed.append(c)

        if rules.tool_names_exact:
            c = CheckResult(
                name=f"工具调用集合完全匹配",
                passed=sorted(actual_names) == sorted(rules.tool_names_exact),
                expected=f"调用工具: {sorted(rules.tool_names_exact)}",
                actual=f"实际: {sorted(actual_names)}",
            )
            checks.append(c)
            if not c.passed:
                failed.append(c)

        # ── 4. 工具调用顺序检查 ──

        if rules.tool_sequence_contains:
            seq = rules.tool_sequence_contains
            # 检查子序列
            match = _is_subsequence(seq, actual_names)
            c = CheckResult(
                name=f"工具调用顺序包含 {seq}",
                passed=match,
                expected=f"顺序中出现 {seq}",
                actual=f"实际顺序: {actual_names}",
            )
            checks.append(c)
            if not c.passed:
                failed.append(c)

        # ── 5. 工具参数检查 ──

        for tool_name, expected_args in rules.tool_args_match.items():
            # 找到第一次调用该工具的 args
            actual_args = None
            for tc in tool_calls:
                if tc["name"] == tool_name:
                    actual_args = tc.get("args", {})
                    break

            matched = True
            mismatch_detail = ""
            if actual_args is None:
                matched = False
                mismatch_detail = f"未调用工具 '{tool_name}'"
            else:
                arg_mismatches = []
                for key, expected_val in expected_args.items():
                    actual_val = actual_args.get(key)
                    if actual_val != expected_val:
                        arg_mismatches.append(f"  args.{key}: 期望={expected_val!r}, 实际={actual_val!r}")
                if arg_mismatches:
                    matched = False
                    mismatch_detail = "\n".join(arg_mismatches)

            c = CheckResult(
                name=f"工具 '{tool_name}' 参数正确",
                passed=matched,
                expected=str(expected_args),
                actual=mismatch_detail or str(actual_args),
            )
            checks.append(c)
            if not c.passed:
                failed.append(c)

        # ── 6. LLM 调用次数检查 ──

        llm_count = len(llm_calls)
        if rules.max_llm_calls is not None:
            c = CheckResult(
                name=f"LLM 调用次数 <= {rules.max_llm_calls}",
                passed=llm_count <= rules.max_llm_calls,
                expected=f"≤ {rules.max_llm_calls}",
                actual=f"实际 {llm_count} 次",
            )
            checks.append(c)
            if not c.passed:
                failed.append(c)

        # ── 7. 延迟检查 ──

        if rules.max_latency_ms is not None:
            c = CheckResult(
                name=f"总延迟 <= {rules.max_latency_ms}ms",
                passed=latency <= rules.max_latency_ms,
                expected=f"≤ {rules.max_latency_ms}ms",
                actual=f"实际 {latency}ms",
            )
            checks.append(c)
            if not c.passed:
                failed.append(c)

        # ── 汇总 ──

        passed_count = sum(1 for c in checks if c.passed)
        total = len(checks)
        passed_all = len(failed) == 0

        diagnosis = _build_diagnosis(
            failed_checks=[c for c in checks if not c.passed],
            trace=trace,
            rules=rules,
        )

        return EvaluationResult(
            passed=passed_all,
            checks=checks,
            total=total,
            passed_count=passed_count,
            failed_count=total - passed_count,
            diagnosis=diagnosis,
            trace=trace,
            latency_ms=latency,
        )

    def evaluate_interactions(self, expected_interactions: list[dict],
                              actual_interactions: list[dict]) -> EvaluationResult:
        """对比期望的和实际发生的交互序列（逐步骤 diff）。"""
        checks = []
        max_steps = max(len(expected_interactions), len(actual_interactions))

        for i in range(max_steps):
            step = i + 1
            exp = expected_interactions[i] if i < len(expected_interactions) else None
            act = actual_interactions[i] if i < len(actual_interactions) else None

            if exp is None:
                checks.append(CheckResult(
                    name=f"第 {step} 步：不应有额外调用",
                    passed=False,
                    expected="（无）",
                    actual=f"意外调用了模型",
                ))
                continue
            if act is None:
                checks.append(CheckResult(
                    name=f"第 {step} 步：缺少调用",
                    passed=False,
                    expected=f"模型调用（含 {len(exp.get('messages',[]))} 条消息）",
                    actual="（未发生）",
                ))
                continue

            # 比较 tool_calls
            exp_tools = [tc["function"]["name"] for tc in (exp.get("response", {}).get("tool_calls") or [])]
            act_tools = [tc["function"]["name"] for tc in (act.get("response", {}).get("tool_calls") or [])]

            if exp_tools != act_tools:
                checks.append(CheckResult(
                    name=f"第 {step} 步：工具调用",
                    passed=False,
                    expected=f"工具: {exp_tools}",
                    actual=f"工具: {act_tools}",
                ))
            else:
                checks.append(CheckResult(
                    name=f"第 {step} 步：工具调用",
                    passed=True,
                    expected=str(exp_tools),
                    actual=str(act_tools),
                ))

        passed_count = sum(1 for c in checks if c.passed)
        return EvaluationResult(
            passed=passed_count == len(checks),
            checks=checks,
            total=len(checks),
            passed_count=passed_count,
            failed_count=len(checks) - passed_count,
        )


# ── 诊断 ──


def _build_diagnosis(failed_checks: list[CheckResult], trace: dict,
                     rules: ExpectRules) -> str:
    """根据失败的断言和 trace 生成诊断信息。"""
    if not failed_checks:
        return ""

    parts = []
    tool_calls = trace.get("tool_calls", [])
    llm_calls = trace.get("llm_calls", [])

    # 第一处失败
    first = failed_checks[0]
    if "最终输出" in first.name:
        parts.append(f"最终输出不符合预期")
        if rules.final_contains:
            missing = [k for k in rules.final_contains if k not in (trace.get("final_output", ""))]
            if missing:
                parts.append(f"  缺少关键词: {missing}")
    elif "工具" in first.name:
        parts.append(f"工具行为偏离")
        if tool_calls:
            parts.append(f"  实际调用顺序: {' → '.join(t['name'] for t in tool_calls)}")
        else:
            parts.append(f"  未调用任何工具")
    elif "LLM 调用" in first.name:
        actual = len(llm_calls)
        expected = rules.max_llm_calls
        if actual > (expected or 999):
            parts.append(f"LLM 调用次数超标（{actual} > {expected}），可能陷入死循环")
        if actual >= 3:
            # 看看最后几步在做什么
            last_few = llm_calls[-3:]
            for i, lc in enumerate(last_few):
                tcs = lc.get("response_tool_calls")
                if tcs:
                    names = [tc["function"]["name"] for tc in tcs]
                    parts.append(f"  第 {len(llm_calls)-2+i} 步: LLM 返回工具 {names}")
                else:
                    content = (lc.get("response_content") or "")[:80]
                    parts.append(f"  第 {len(llm_calls)-2+i} 步: LLM 返回文本 '{content}'")

    # 是否跟 tool 执行失败有关？
    for msg in reversed(trace.get("messages", [])):
        if msg.get("role") == "tool" and ("错误" in (msg.get("content") or "")):
            parts.append(f"  工具执行出错: {msg['content'][:100]}")
            break

    diagnosis = "; ".join(parts) if parts else first.name
    return diagnosis


# ── 辅助 ──


def _safe_parse_json(s: str) -> Any:
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return {}


def _is_subsequence(seq: list[str], full: list[str]) -> bool:
    """检查 seq 是否是 full 的子序列（保持顺序，可不连续）。"""
    it = iter(full)
    return all(item in it for item in seq)


def print_trace(trace: dict):
    """打印 trace 的步骤摘要，方便人工排查。"""
    print(f"\n{'─'*50}")
    print(f"  Trace 摘要")
    print(f"{'─'*50}")
    print(f"  最终输出: {(trace.get('final_output') or '')[:100]}")
    print(f"  LLM 调用: {len(trace.get('llm_calls', []))} 次")

    tool_calls = trace.get("tool_calls", [])
    if tool_calls:
        print(f"  工具调用 ({len(tool_calls)} 次):")
        for tc in tool_calls:
            args_str = json.dumps(tc.get("args", {}), ensure_ascii=False)
            print(f"    [{tc['step']}] {tc['name']}({args_str})")

    for i, lc in enumerate(trace.get("llm_calls", [])):
        inc = ""
        if lc.get("response_tool_calls"):
            names = [tc["function"]["name"] for tc in lc["response_tool_calls"]]
            inc = f" → {names}"
        print(f"  LLM[{i}]: {lc.get('response_content', '')[:60]}{inc}")

    print(f"{'─'*50}")
