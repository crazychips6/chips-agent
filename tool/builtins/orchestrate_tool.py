"""orchestrate 工具 — 多 Agent 编排模式

Phase 3: 三种编排模式：

  1. supervisor — 多子任务并发/串行执行，汇总结果
  2. pipeline — 链式接力，上一步输出作为下一步上下文
  3. debate — 多 Agent 独立回答同一问题，返回对比

共享 agent_tools 的工厂函数 resolve_agent_config / build_sub_agent。
Agent Pool 控制每个角色的并发上限。
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from agent.pool import acquire as pool_acquire, release as pool_release
from tool.registry import registry
from tool.builtins.agent_tools import get_parent, resolve_agent_config, build_sub_agent

logger = logging.getLogger("chips.tool.orchestrate")


def _handle(args: dict[str, Any]) -> str:
    parent = get_parent()
    if parent is None:
        return json.dumps({"error": "parent agent not initialized"})

    mode = args.get("mode", "supervisor")

    if mode == "supervisor":
        return _run_supervisor(args, parent)
    if mode == "pipeline":
        return _run_pipeline(args, parent)
    if mode == "debate":
        return _run_debate(args, parent)

    return json.dumps({"error": f"未知编排模式: {mode}（支持: supervisor, pipeline, debate）"})


# ── Supervisor ──


def _run_supervisor(args: dict, parent) -> str:
    steps = args.get("steps", [])
    if not steps:
        return json.dumps({"error": "steps 不能为空"})
    parallel = args.get("parallel", False)
    goal = args.get("goal", "")

    results: list[dict] = []

    if parallel:
        results = _run_parallel(steps, parent)
    else:
        results = _run_sequential(steps, parent)

    summary = {
        "mode": "supervisor",
        "goal": goal,
        "total_steps": len(steps),
        "results": results,
    }
    return json.dumps(summary, ensure_ascii=False)


# ── Pipeline ──


def _run_pipeline(args: dict, parent) -> str:
    steps = args.get("steps", [])
    if not steps:
        return json.dumps({"error": "steps 不能为空"})

    previous_output = ""
    results: list[dict] = []

    for i, step in enumerate(steps):
        agent_name = step.get("agent", "")
        raw_task = step.get("task", "")
        if not raw_task:
            results.append({
                "step": i,
                "agent": agent_name,
                "error": "task 不能为空",
            })
            continue

        # 将上一步输出注入当前步骤
        if previous_output and i > 0:
            task = f"{raw_task}\n\n[上一步输出]\n{previous_output}"
        else:
            task = raw_task

        result = _run_single_step(step, task, parent, f"pipeline/{i}")
        results.append(result)

        # 提取文本输出作为下一步输入
        output = _extract_text(result)
        if output:
            previous_output = output

    return json.dumps({
        "mode": "pipeline",
        "total_steps": len(steps),
        "results": results,
    }, ensure_ascii=False)


# ── Debate ──


def _run_debate(args: dict, parent) -> str:
    agents = args.get("agents", [])
    task = args.get("task", "")
    if not agents:
        return json.dumps({"error": "agents 不能为空"})
    if not task:
        return json.dumps({"error": "task 不能为空"})

    # 并发执行：同一 task 多个 Agent
    steps = [{"agent": a, "task": task} for a in agents]
    results = _run_parallel(steps, parent, tag_prefix="debate")

    return json.dumps({
        "mode": "debate",
        "task": task,
        "total_responses": len(results),
        "results": results,
    }, ensure_ascii=False)


# ── 执行辅助 ──


def _run_sequential(
    steps: list[dict[str, Any]],
    parent,
    tag_prefix: str = "supervisor",
) -> list[dict]:
    results = []
    for i, step in enumerate(steps):
        result = _run_single_step(step, step.get("task", ""), parent, f"{tag_prefix}/{i}")
        results.append(result)
    return results


def _run_parallel(
    steps: list[dict[str, Any]],
    parent,
    tag_prefix: str = "supervisor",
) -> list[dict]:
    results = [None] * len(steps)

    with ThreadPoolExecutor(max_workers=min(len(steps), 8)) as pool:
        fut_to_idx = {}
        for i, step in enumerate(steps):
            task_text = step.get("task", "")
            fut = pool.submit(_run_single_step, step, task_text, parent, f"{tag_prefix}/{i}")
            fut_to_idx[fut] = i

        for fut in as_completed(fut_to_idx):
            idx = fut_to_idx[fut]
            try:
                results[idx] = fut.result()
            except Exception as e:
                step = steps[idx]
                results[idx] = {
                    "step": idx,
                    "agent": step.get("agent", ""),
                    "error": str(e),
                }

    return results


def _run_single_step(
    step: dict[str, Any],
    task_text: str,
    parent,
    tag: str,
) -> dict:
    """执行单个步骤，返回 {step, agent, output/error}。"""
    agent_name = step.get("agent", "")
    if not agent_name:
        return {"step": tag, "error": "step 缺少 agent 字段"}

    try:
        config = resolve_agent_config(step, parent)
    except (RuntimeError, ValueError) as e:
        return {"step": tag, "agent": agent_name, "error": str(e)}

    # Agent Pool — 角色级并发限流
    pool_size = config.get("pool_size")
    if pool_size:
        pool_acquire(agent_name, pool_size=pool_size)

    try:
        sub, final_task, max_iterations = build_sub_agent(
            task=task_text,
            parent=parent,
            **config,
            session_db=parent.session_db,
            session_id=parent.session_id or "",
        )
        output = sub.run_conversation(final_task, max_iterations=max_iterations)
        return {
            "step": tag,
            "agent": agent_name,
            "output": output,
        }
    except Exception as e:
        logger.error("orchestrate[%s] failed: %s", tag, e, exc_info=True)
        return {
            "step": tag,
            "agent": agent_name,
            "error": str(e),
        }
    finally:
        if pool_size:
            pool_release(agent_name)


def _extract_text(result: dict) -> str:
    """从步骤结果中提取纯文本输出。"""
    if "error" in result:
        return ""
    output = result.get("output", "")
    # 如果是 JSON 且包含 results 字段（嵌套编排），取最后一步输出
    if output.startswith("{"):
        try:
            data = json.loads(output)
            if "results" in data and isinstance(data["results"], list):
                last = data["results"][-1]
                return _extract_text(last)
        except (json.JSONDecodeError, IndexError):
            pass
    return output


# ── Schema & 注册 ──


ORCHESTRATE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "orchestrate",
        "description": (
            "多 Agent 编排执行。支持三种模式：\n\n"
            "1. supervisor（推荐）：定义多个子任务，并发或串行执行，汇总结果\n"
            '   例: {"mode":"supervisor","steps":[{"agent":"researcher","task":"搜索A"},...],"parallel":true}\n\n'
            "2. pipeline：链式执行，上一步的输出自动注入下一步的上下文\n"
            '   例: {"mode":"pipeline","steps":[{"agent":"coder","task":"写代码"},{"agent":"reviewer","task":"审查"}]}\n\n'
            "3. debate：多个 Agent 独立回答同一问题，返回对比\n"
            '   例: {"mode":"debate","agents":["coder","researcher"],"task":"这个设计有什么问题？"}\n\n'
            "适用场景：需要多个子 Agent 协作完成的复杂任务。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["supervisor", "pipeline", "debate"],
                    "description": "编排模式",
                },
                "steps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "agent": {"type": "string", "description": "Agent 角色名（可选值见 enum）"},
                            "task": {"type": "string", "description": "子任务描述"},
                            "model": {"type": "string", "description": "可选，覆盖模型"},
                        },
                        "required": ["agent", "task"],
                    },
                    "description": "步骤列表（supervisor/pipeline 使用）",
                },
                "agents": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Agent 角色名列表（debate 使用）",
                },
                "task": {
                    "type": "string",
                    "description": "任务描述（debate 使用，所有 Agent 回答同一问题）",
                },
                "goal": {
                    "type": "string",
                    "description": "总体目标描述（supervisor 使用，可选）",
                },
                "parallel": {
                    "type": "boolean",
                    "description": "是否并发执行子任务（supervisor 使用，默认 false）",
                },
            },
            "required": ["mode"],
        },
    },
}

registry.register(
    name="orchestrate",
    toolset="core",
    schema=ORCHESTRATE_SCHEMA,
    handler=_handle,
)
