"""orchestrate 工具 — 多 Agent 编排

四种模式：
  1. single     — 单步委派（原 delegate_task）
  2. supervisor — 多子任务并发/串行执行，汇总结果
  3. pipeline   — 链式接力，上一步输出作为下一步上下文
  4. debate     — 多 Agent 独立回答同一问题，返回对比

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

    mode = args.get("mode", "single")

    if mode == "single":
        return _run_single(args, parent)
    if mode == "supervisor":
        return _run_supervisor(args, parent)
    if mode == "pipeline":
        return _run_pipeline(args, parent)
    if mode == "debate":
        return _run_debate(args, parent)

    return json.dumps({"error": f"未知编排模式: {mode}（支持: single, supervisor, pipeline, debate）"})


# ── Single（原 delegate_task）──


def _run_single(args: dict, parent) -> str:
    task = args.get("task", "")
    if not task:
        return json.dumps({"error": "task 不能为空"})

    try:
        config = resolve_agent_config(args, parent)
    except (RuntimeError, ValueError) as e:
        return json.dumps({"error": str(e)})

    sub, final_task, max_iterations = build_sub_agent(
        task=task,
        parent=parent,
        **config,
        session_db=parent.session_db,
        session_id=parent.session_id or "",
    )

    try:
        tag = config["agent_name"] or "(inline)"
        logger.info("sub_agent[%s] task=%r model=%s tools=%s iter=%d",
                     tag, final_task[:80], config["model"], config["tools"], max_iterations)
        result = sub.run_conversation(final_task, max_iterations=max_iterations)
        logger.info("sub_agent[%s] done len=%d", tag, len(result))
        return result
    except Exception as e:
        logger.error("sub_agent[%s] failed: %s", config.get("agent_name", "?"), e, exc_info=True)
        return json.dumps({"error": f"子任务执行失败: {e}"})


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
        "description": "多 Agent 编排（single/supervisor/pipeline/debate）",
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["single", "supervisor", "pipeline", "debate"],
                    "description": "single=单步委派, supervisor=多子任务, pipeline=链式, debate=对比",
                },
                # ── single 独有 ──
                "agent": {
                    "type": "string",
                    "description": "注册角色名（single 使用）",
                },
                "task": {
                    "type": "string",
                    "description": "任务描述（single/debate 使用）",
                },
                "tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "可用工具集（single 使用）",
                },
                "model": {
                    "type": "string",
                    "description": "模型名（single 使用）",
                },
                "max_iterations": {
                    "type": "integer",
                    "description": "最大迭代次数（single 使用，默认 10，最大 30）",
                },
                "context": {
                    "type": "string",
                    "description": "附加上下文（single 使用）",
                },
                # ── supervisor/pipeline 独有 ──
                "steps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "agent": {"type": "string", "description": "Agent 角色名"},
                            "task": {"type": "string", "description": "子任务描述"},
                            "model": {"type": "string", "description": "可选，覆盖模型"},
                        },
                        "required": ["agent", "task"],
                    },
                    "description": "子任务步骤（supervisor/pipeline 使用）",
                },
                # ── debate 独有 ──
                "agents": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Agent 列表（debate 使用）",
                },
                # ── supervisor 独有 ──
                "goal": {
                    "type": "string",
                    "description": "总目标（supervisor 可选）",
                },
                "parallel": {
                    "type": "boolean",
                    "description": "并发执行（supervisor，默认 false）",
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
