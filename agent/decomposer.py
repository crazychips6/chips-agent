"""decompose_and_execute — 任务分解与多 Agent 协作执行

三步流程：
  1. 调 LLM 将复杂任务分解为子任务 DAG（含依赖关系）
  2. 按拓扑排序执行子任务（无依赖的并发，有依赖的等待）
  3. 调 LLM 合并所有子结果，合成最终回复

用法::
    from agent.decomposer import decompose_and_execute
    result = decompose_and_execute("写一个微服务", parent_agent)
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("chips.agent.decomposer")

_DECOMPOSE_PROMPT = """你是一个任务规划专家。分析用户的任务，将其分解为可并行/串行执行的子任务。

当前可用的 Agent 角色及其能力：
{agent_descriptions}

输出 JSON（不要 markdown 代码块，只输出纯 JSON）：
{{
    "goal": "总体目标简述",
    "steps": [
        {{
            "id": 1,
            "agent": "角色名",
            "task": "给该 Agent 的详细任务描述",
            "depends_on": [],
            "context": "额外上下文（可选）"
        }}
    ]
}}

规则：
1. 每个步骤的 task 要足够详细，让子 Agent 不需要额外上下文就能独立完成
2. depends_on 填写前置步骤的 id 数组，无依赖则为 []
3. 尽量让无依赖的步骤可以并发执行
4. 步骤数控制在 2-5 个，不要过度分解
5. goal 控制在 50 字以内"""

_MERGE_PROMPT = """你是一个结果汇总专家。以下是原始任务和各子任务的执行结果，请合成一份完整的回复。

原始任务：
{original_task}

执行结果：
{results_text}

要求：
1. 去除各子任务结果中的冗余内容
2. 按逻辑顺序组织（不是按执行顺序）
3. 如果有子任务失败，在回复末尾说明
4. 直接输出最终回复，不要加额外前缀"""


def decompose_and_execute(
    task: str,
    parent: Any,
    agents: list[str] | None = None,
) -> str:
    """完整的分解 → 执行 → 合并流程。

    Args:
        task: 用户的任务描述
        parent: 父 AIAgent 实例
        agents: 可用的 Agent 角色名列表（默认使用 registry 中所有角色）

    Returns:
        合并后的最终回复
    """
    plan = _decompose(task, parent, agents)
    if not plan or "steps" not in plan:
        return f"任务分解失败，请重试或手动分解。\n分解结果: {json.dumps(plan, ensure_ascii=False)}"

    logger.info("decompose plan goal=%s steps=%d", plan.get("goal", ""), len(plan["steps"]))

    results = _execute_plan(plan, parent)
    merged = _merge(task, results, parent)
    return merged


# ── 步骤 1：分解 ──


def _build_agent_descriptions(parent: Any) -> str:
    """从 AgentRegistry 构建角色描述文本。"""
    try:
        from tool.builtins.agent_tools import get_registry
        registry = get_registry()
        if registry and registry.names:
            lines = []
            for name in registry.names:
                entry = registry.get(name)
                if entry:
                    tools = ", ".join(entry.get("tools", []))
                    desc = entry.get("description", "")
                    line = f"  - {name}"
                    if desc:
                        line += f": {desc}"
                    line += f" (tools: {tools})"
                    lines.append(line)
                else:
                    lines.append(f"  - {name}")
            return "\n".join(lines) if lines else "  (无已注册 Agent)"
    except Exception:
        pass
    # Fallback: 从 parent 的工具名推断
    tool_names = ", ".join(sorted(parent.tool_names)[:10]) if parent.tool_names else "bash, file"
    return f"  - default (tools: {tool_names})"


def _decompose(task: str, parent: Any, agents: list[str] | None = None) -> dict:
    """调 LLM 分解任务，返回 TaskPlan dict。"""
    agent_desc = _build_agent_descriptions(parent)
    if agents:
        agent_desc_lines = [f"  - {a}" for a in agents]
        agent_desc = "\n".join(agent_desc_lines) or agent_desc

    prompt = _DECOMPOSE_PROMPT.format(agent_descriptions=agent_desc)

    try:
        result = parent.gateway.chat(
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": task},
            ],
            model=parent.model,
            max_tokens=4096,
        )
        content = (result.content or "").strip()
        # 清理可能的 markdown 代码块
        if content.startswith("```"):
            lines = content.split("\n")
            if len(lines) > 2:
                content = "\n".join(lines[1:-1])
            else:
                content = lines[-1]
        plan = json.loads(content)
        if not isinstance(plan, dict) or "steps" not in plan:
            raise ValueError("plan missing 'steps' field")
        return plan
    except Exception as e:
        logger.error("decompose_failed: %s", e, exc_info=True)
        return {"goal": "", "steps": [], "_error": str(e)}


# ── 步骤 2：执行 ──


def _topological_sort(steps: list[dict]) -> list[int]:
    """拓扑排序，返回排好序的 step id 列表。"""
    step_ids = {s["id"] for s in steps}
    # 校验所有依赖都在 steps 中
    for s in steps:
        for dep in s.get("depends_on", []):
            if dep not in step_ids:
                logger.warning("step %d depends on unknown step %d, ignoring", s["id"], dep)

    # Kahn 算法
    in_degree: dict[int, int] = {s["id"]: 0 for s in steps}
    children: dict[int, list[int]] = {s["id"]: [] for s in steps}

    for s in steps:
        for dep in s.get("depends_on", []):
            if dep in step_ids:
                in_degree[s["id"]] = in_degree.get(s["id"], 0) + 1
                children.setdefault(dep, []).append(s["id"])

    queue = [sid for sid, deg in in_degree.items() if deg == 0]
    sorted_ids: list[int] = []
    while queue:
        sid = queue.pop(0)
        sorted_ids.append(sid)
        for child in children.get(sid, []):
            in_degree[child] -= 1
            if in_degree[child] == 0:
                queue.append(child)

    if len(sorted_ids) != len(steps):
        logger.warning("topological_sort: cycle detected, %d/%d steps sorted", len(sorted_ids), len(steps))

    return sorted_ids


def _execute_plan(plan: dict, parent: Any) -> list[dict]:
    """执行 TaskPlan，返回每个步骤的结果列表。"""
    steps = plan.get("steps", [])
    if not steps:
        return []

    # 按拓扑排序分组：同层可并发，不同层串行
    sorted_ids = _topological_sort(steps)
    step_map = {s["id"]: s for s in steps}

    # 按层级分组（依赖层数）
    depth: dict[int, int] = {}
    for sid in sorted_ids:
        s = step_map[sid]
        deps = [d for d in s.get("depends_on", []) if d in step_map]
        if not deps:
            depth[sid] = 0
        else:
            depth[sid] = max(depth.get(d, 0) for d in deps) + 1

    max_depth = max(depth.values()) if depth else 0
    layers: list[list[int]] = [[] for _ in range(max_depth + 1)]
    for sid in sorted_ids:
        layers[depth[sid]].append(sid)

    results: dict[int, dict] = {}

    for layer_idx, layer in enumerate(layers):
        # 每层内并发执行
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _run_step(sid: int) -> tuple[int, dict]:
            s = step_map[sid]
            agent_name = s.get("agent", "")
            sub_task = s.get("task", "")
            context = s.get("context", "")

            if not agent_name or not sub_task:
                return sid, {"step": sid, "error": "缺少 agent 或 task 字段"}

            if not sub_task:
                return sid, {"step": sid, "error": "task 为空"}

            try:
                record_id = parent.fork_sub_agent(
                    agent_name=agent_name,
                    task=sub_task,
                    context=context,
                )
                result = parent.get_sub_agent_result(record_id)
                if result:
                    return sid, {
                        "step": sid,
                        "agent": agent_name,
                        "output": result.get("output", ""),
                        "record_id": record_id,
                        "iterations": result.get("iterations", 0),
                        "error": result.get("error"),
                    }
                return sid, {"step": sid, "agent": agent_name, "error": "无法获取子 Agent 结果"}
            except Exception as e:
                logger.error("decompose_step_%d_failed: %s", sid, e)
                return sid, {"step": sid, "agent": agent_name, "error": str(e)}

        logger.info("decompose executing layer %d/%d steps=%s", layer_idx + 1, len(layers), layer)

        with ThreadPoolExecutor(max_workers=min(len(layer), 8)) as pool:
            futures = {pool.submit(_run_step, sid): sid for sid in layer}
            for future in as_completed(futures):
                sid, result = future.result()
                results[sid] = result

    # 按原始顺序返回
    return [results.get(sid, {"step": sid, "error": "未执行"}) for sid in sorted_ids]


# ── 步骤 3：合并 ──


def _merge(original_task: str, results: list[dict], parent: Any) -> str:
    """调 LLM 合并所有子任务结果。"""
    # 构建结果文本
    parts = []
    for i, r in enumerate(results, 1):
        agent = r.get("agent", "?")
        output = r.get("output", "")
        error = r.get("error")
        if error:
            parts.append(f"[步骤 {i} ({agent}) 失败]\n错误: {error}")
        elif output:
            # 只保留前 3000 字符，太长的话 LLM 看不完
            truncated = output[:3000]
            if len(output) > 3000:
                truncated += "\n...(截断)"
            parts.append(f"[步骤 {i} ({agent}) 结果]\n{truncated}")
        else:
            parts.append(f"[步骤 {i} ({agent})]\n(无输出)")

    results_text = "\n\n".join(parts)
    prompt = _MERGE_PROMPT.format(original_task=original_task, results_text=results_text)

    try:
        result = parent.gateway.chat(
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": "请汇总以上结果。"},
            ],
            model=parent.model,
            max_tokens=4096,
        )
        return result.content or "(无输出)"
    except Exception as e:
        logger.error("merge_failed: %s", e)
        # 合并失败，返回原始结果的拼接
        merged = f"任务分解执行完成，共 {len(results)} 个子任务。\n\n"
        for i, r in enumerate(results, 1):
            agent = r.get("agent", "?")
            output = r.get("output", "")
            error = r.get("error")
            if error:
                merged += f"步骤 {i} ({agent}): 失败 - {error}\n"
            else:
                merged += f"步骤 {i} ({agent}): 完成\n"
        return merged
