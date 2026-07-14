"""内置工具 — 导入即触发自注册

通过 import 的副作用触发各工具的 registry.register()。
agent/cli.py 中 import tool.builtins 即可完成所有内置工具注册，
agent 代码无需直接引用任何具体工具实现。"""

import tool.builtins.terminal  # noqa: F401
import tool.builtins.file      # noqa: F401
import tool.builtins.web       # noqa: F401
import tool.builtins.screenshot   # noqa: F401
import tool.builtins.skill_tools  # noqa: F401
import tool.builtins.todo_tool  # noqa: F401
import tool.builtins.clarify_tool  # noqa: F401
import tool.builtins.system_info  # noqa: F401
import tool.builtins.calendar_tool  # noqa: F401
import tool.builtins.process_tool  # noqa: F401
import tool.builtins.agent_tools  # noqa: F401
import tool.builtins.orchestrate_tool  # noqa: F401
import tool.builtins.sub_agent_tools  # noqa: F401
import tool.builtins.deferred_tools  # noqa: F401
import tool.builtins.document  # noqa: F401
