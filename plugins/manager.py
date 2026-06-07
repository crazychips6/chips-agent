"""PluginManager — 插件发现、加载、钩子调度

用法::

    from plugins import PluginManager
    from tool.registry import registry

    pm = PluginManager(registry=registry)
    pm.add_default_paths()
    count = pm.load_all()
    print(f"已加载 {count} 个插件")

插件文件需导出 ``register(ctx: PluginContext)`` 函数，
内部调用 ``ctx.register_tool()`` / ``ctx.register_hook()``。
"""

from __future__ import annotations

import importlib.util
import logging
import os
from pathlib import Path

from plugins.protocol import HookPlugin, PluginContext
from tool.registry import ToolRegistry

logger = logging.getLogger("chips.plugins.manager")


class PluginManager:
    """插件管理器。

    职责：
    1. 扫描指定目录发现插件文件
    2. 加载插件并调用 ``register(ctx)`` 将工具注册到 ToolRegistry
    3. 管理 HookPlugin 列表并分发生命周期钩子
    """

    def __init__(self, registry: ToolRegistry | None = None):
        self._registry = registry
        self._scan_paths: list[str] = []
        self._plugin_tool_names: set[str] = set()
        self._hook_plugins: list[HookPlugin] = []
        self._loaded_files: set[str] = set()

    # ── 路径管理 ──

    def add_scan_path(self, path: str) -> None:
        """添加插件扫描目录。路径支持 ``~`` 展开。

        跳过 Python 包目录（含 ``__init__.py``），避免将系统包当作插件目录。
        """
        resolved = os.path.abspath(os.path.expanduser(path))
        if not os.path.isdir(resolved) or resolved in self._scan_paths:
            return
        # Python 包目录有 __init__.py，不是插件放置目录
        if os.path.isfile(os.path.join(resolved, "__init__.py")):
            logger.debug("scan_path_skipped_package dir=%s", resolved)
            return
        self._scan_paths.append(resolved)

    def add_default_paths(self) -> None:
        """添加默认扫描路径：``~/.chips/plugins/`` 和 ``./plugins/``。"""
        self.add_scan_path("~/.chips/plugins")
        self.add_scan_path("./plugins")

    # ── 发现与加载 ──

    def discover(self) -> list[str]:
        """扫描所有路径，返回尚未加载的 ``.py`` 文件路径。"""
        found: list[str] = []
        for scan_dir in self._scan_paths:
            if not os.path.isdir(scan_dir):
                continue
            for fname in sorted(os.listdir(scan_dir)):
                if not fname.endswith(".py") or fname == "__init__.py":
                    continue
                fpath = os.path.join(scan_dir, fname)
                if fpath not in self._loaded_files:
                    found.append(fpath)
        return found

    def load(self, filepath: str) -> bool:
        """加载单个插件文件。成功返回 True。

        文件需导出 ``register(ctx: PluginContext)`` 函数。
        """
        filepath = os.path.abspath(filepath)
        if filepath in self._loaded_files:
            return True

        if not os.path.isfile(filepath):
            logger.warning("plugin_file_not_found path=%s", filepath)
            return False

        try:
            module_name = Path(filepath).stem
            spec = importlib.util.spec_from_file_location(module_name, filepath)
            if not spec or not spec.loader:
                logger.warning("plugin_load_failed no_spec path=%s", filepath)
                return False

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            register_fn = getattr(module, "register", None)
            if not callable(register_fn):
                logger.warning("plugin_no_register_fn path=%s", filepath)
                return False

            ctx = PluginContext(registry=self._registry)
            register_fn(ctx)

            self._plugin_tool_names.update(ctx._tool_names)
            self._hook_plugins.extend(ctx._hook_plugins)
            self._loaded_files.add(filepath)

            logger.info(
                "plugin_loaded path=%s tools=%d hooks=%d",
                filepath, len(ctx._tool_names), len(ctx._hook_plugins),
            )

            # 对新注册的 HookPlugin 调用 on_register
            for hook in ctx._hook_plugins:
                if hasattr(hook, "on_register"):
                    try:
                        hook.on_register(self._registry)
                    except Exception:
                        logger.exception(
                            "hook_on_register_failed plugin=%s",
                            getattr(hook, "name", type(hook).__name__),
                        )

            return True
        except Exception:
            logger.exception("plugin_load_error path=%s", filepath)
            return False

    def load_all(self) -> int:
        """发现并加载所有插件，返回成功加载的数量。"""
        count = 0
        for fpath in self.discover():
            if self.load(fpath):
                count += 1
        return count

    # ── 工具查询 ──

    def has_tool(self, tool_name: str) -> bool:
        """是否有插件注册了此工具。"""
        return tool_name in self._plugin_tool_names

    # ── Hook 调度 ──

    def dispatch_tool_call_pre(
        self, tool_name: str, args: dict,
    ) -> dict:
        """依次调用各 HookPlugin 的 ``on_tool_call_pre``。

        每个钩子可修改 args，返回修改后的参数。
        """
        for hook in self._hook_plugins:
            try:
                result = hook.on_tool_call_pre(tool_name, args)
                if result is not None:
                    args = result
            except Exception:
                logger.exception(
                    "hook_tool_call_pre_failed plugin=%s",
                    getattr(hook, "name", type(hook).__name__),
                )
        return args

    def dispatch_tool_call_post(self, tool_name: str, result: str) -> str:
        """依次调用各 HookPlugin 的 ``on_tool_call_post``。"""
        for hook in self._hook_plugins:
            try:
                modified = hook.on_tool_call_post(tool_name, result)
                if modified is not None:
                    result = modified
            except Exception:
                logger.exception(
                    "hook_tool_call_post_failed plugin=%s",
                    getattr(hook, "name", type(hook).__name__),
                )
        return result

    def dispatch_response(self, response: str) -> str:
        """依次调用各 HookPlugin 的 ``on_response``。"""
        for hook in self._hook_plugins:
            try:
                modified = hook.on_response(response)
                if modified is not None:
                    response = modified
            except Exception:
                logger.exception(
                    "hook_response_failed plugin=%s",
                    getattr(hook, "name", type(hook).__name__),
                )
        return response

    def dispatch_session_end(self, messages: list[dict]) -> None:
        """依次调用各 HookPlugin 的 ``on_session_end``。"""
        for hook in self._hook_plugins:
            try:
                hook.on_session_end(messages)
            except Exception:
                logger.exception(
                    "hook_session_end_failed plugin=%s",
                    getattr(hook, "name", type(hook).__name__),
                )

    # ── 查询 ──

    @property
    def plugin_tool_names(self) -> set[str]:
        """已注册插件工具的名称集合。"""
        return set(self._plugin_tool_names)

    @property
    def hook_count(self) -> int:
        """已加载 HookPlugin 的数量。"""
        return len(self._hook_plugins)

    @property
    def loaded_count(self) -> int:
        """已加载插件文件的数量。"""
        return len(self._loaded_files)
