"""QuickAppManager — 快应用核心管理器

职责：
  - create / load_all / run / delete / list
  - 三级 venv 自动探测（级别 0/1/2）
  - 子进程 spawn 包装
  - 自注册到 ToolRegistry

Manager 不关心代码生成（由 create_app 工具调用前完成），只关心写文件、验证、注册。"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from tool.registry import registry

from quick_app.models import (
    QUICK_APP_TEMPLATE,
    Draft,
    QuickApp,
    SourceType,
    VenvLevel,
    VerificationResult,
    build_schema,
)
from quick_app.verifier import verify_quick_app

logger = logging.getLogger("chips.quick_app.manager")

# ── 目录约定 ──

QUICK_APPS_DIR = Path(".chips") / "quick_apps"
BASE_VENV_DIR = QUICK_APPS_DIR / ".venv_base"

# 共享 base venv 预装包（已知兼容集）
BASE_VENV_PACKAGES = [
    "requests",
    "beautifulsoup4",
    "Pillow",
    "openpyxl",
    "pandas",
    "camelot-py",
]


class QuickAppManager:
    """快应用管理器。

    Args:
        registry: ToolRegistry 实例（用于注册/注销）。
    """

    def __init__(self, registry: Any = None):
        self._registry = registry or registry  # 使用模块级单例兜底
        self._entries: dict[str, QuickApp] = {}
        # 缓存"已注册的 spawn wrapper"函数，按 name 索引
        self._spawn_handlers: dict[str, Any] = {}

    # ── 目录初始化 ──

    def ensure_dirs(self) -> None:
        """确保 quick_apps/ 目录存在。"""
        QUICK_APPS_DIR.mkdir(parents=True, exist_ok=True)

    # ── Venv 管理 ──

    def ensure_base_venv(self) -> Path | None:
        """确保共享 base venv 存在，不存在则创建。

        返回 Python 解释器路径，失败返回 None。
        """
        python_path = BASE_VENV_DIR / "bin" / "python"
        if python_path.exists():
            return python_path

        logger.info("base_venv creating venv at %s", BASE_VENV_DIR)
        try:
            subprocess.run(
                [sys.executable, "-m", "venv", str(BASE_VENV_DIR)],
                capture_output=True, text=True, timeout=60,
            )
        except Exception as e:
            logger.warning("base_venv creation failed: %s", e)
            return None

        # 安装预装包
        pip = BASE_VENV_DIR / "bin" / "pip"
        try:
            subprocess.run(
                [str(pip), "install"] + BASE_VENV_PACKAGES,
                capture_output=True, text=True, timeout=120,
            )
            logger.info("base_venv packages installed: %s", BASE_VENV_PACKAGES)
        except Exception as e:
            logger.warning("base_venv pip install failed: %s", e)

        return python_path

    def resolve_python(self, venv_level: VenvLevel, app_name: str = "") -> str:
        """按三级策略解析 Python 解释器路径。

        Args:
            venv_level: VenvLevel 枚举（0/1/2）。
            app_name: 级别 2 时需要，用于找独立 venv。

        返回:
            Python 解释器的绝对路径字符串。
        """
        if venv_level == VenvLevel.ISOLATED and app_name:
            local_venv = QUICK_APPS_DIR / app_name / ".venv" / "bin" / "python"
            if local_venv.exists():
                return str(local_venv.resolve())

        if venv_level == VenvLevel.SHARED:
            base_python = BASE_VENV_DIR / "bin" / "python"
            if base_python.exists():
                return str(base_python.resolve())

        # 级别 0 或 fallback（无 venv 就用系统 Python）
        return sys.executable

    def _ensure_venv_for_level(self, venv_level: VenvLevel, app_name: str) -> str:
        """确保 venv 存在，返回 Python 路径。

        级别 0：直接返回系统 Python。
        级别 1：确保共享 base venv 存在。
        级别 2：确保独立 venv 存在。
        """
        if venv_level == VenvLevel.NONE:
            return sys.executable

        if venv_level == VenvLevel.SHARED:
            result = self.ensure_base_venv()
            return str(result) if result else sys.executable

        # 级别 2
        local_venv = QUICK_APPS_DIR / app_name / ".venv"
        venv_python = local_venv / "bin" / "python"
        if not venv_python.exists():
            logger.info("creating isolated venv for %s at %s", app_name, local_venv)
            try:
                subprocess.run(
                    [sys.executable, "-m", "venv", str(local_venv)],
                    capture_output=True, text=True, timeout=60,
                )
            except Exception as e:
                logger.warning("isolated venv creation failed for %s: %s", app_name, e)
                return sys.executable

        return str(venv_python.resolve())

    @staticmethod
    def infer_venv_level(source_type: SourceType) -> VenvLevel:
        """根据来源类型推断默认 venv 级别。"""
        if source_type == SourceType.CLI_TOOL:
            return VenvLevel.NONE
        if source_type == SourceType.PIP_PACKAGE:
            return VenvLevel.SHARED
        if source_type == SourceType.GITHUB:
            return VenvLevel.ISOLATED
        # WRITE_CODE：走共享 venv（因为不知道依赖，共享兜底）
        return VenvLevel.SHARED

    # ── 核心操作 ──

    def create(
        self,
        name: str,
        code: str,
        description: str,
        schema: dict,
        source_type: SourceType,
        venv_level: VenvLevel,
        sample_args: dict | None = None,
        source_name: str = "",
    ) -> QuickApp:
        """创建快应用：部署（如 GitHub clone）→ 写文件 → 验证 → 注册。

        Args:
            name: 工具名。
            code: app.py 完整代码。
            description: 工具描述。
            schema: function-calling schema。
            source_type: 来源类型。
            venv_level: venv 级别。
            sample_args: 验证用的样例参数（不传则不验证）。
            source_name: 来源名（GitHub 时为 repo 名如 "user/repo"）。

        Returns:
            注册后的 QuickApp 实例。

        Raises:
            RuntimeError: 验证不通过时抛出，调用方应捕获并重试/回滚。
        """
        self.ensure_dirs()
        app_dir = QUICK_APPS_DIR / name
        app_path = app_dir / "app.py"
        meta_path = app_dir / "meta.json"

        # 写之前先检查是否已有同名工具
        if self._entries.get(name) or self._registry._entries.get(name):
            raise RuntimeError(f"工具「{name}」已存在")

        # 写文件
        app_dir.mkdir(parents=True, exist_ok=True)
        app_path.write_text(code, encoding="utf-8")

        # 写 meta.json（用于启动时恢复注册）
        meta = {
            "name": name,
            "description": description,
            "schema": schema,
            "source_type": source_type.value,
            "venv_level": venv_level.value,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        # 确保 venv 存在
        python_path = self._ensure_venv_for_level(venv_level, name)

        # GitHub 项目部署（clone + 安装依赖）
        if source_type == SourceType.GITHUB and source_name:
            from quick_app.trace_log import write as tw
            tw("DEPLOY", f"开始部署 {source_name}", {"url": source_name, "target": str(app_dir / "repo")})
            repo_dir = app_dir / "repo"
            from quick_app.github_deploy import deploy_github_project
            deploy_result = deploy_github_project(source_name, repo_dir, python_path)
            if not deploy_result["success"]:
                self._rollback(app_dir, name)
                raise RuntimeError(f"GitHub 项目部署失败：{deploy_result['error']}")
            tw("DEPLOY", f"部署完成 {source_name}", deploy_result)

        # 自我验证
        if sample_args is not None:
            verify_result = verify_quick_app(app_path, python_path, sample_args)
            if not verify_result.passed:
                # 回滚
                self._rollback(app_dir, name)
                raise RuntimeError(f"验证失败：{verify_result.error}")

        # 注册
        qa = QuickApp(
            name=name,
            description=description,
            app_dir=app_dir,
            schema=schema,
            source_type=source_type,
            venv_level=venv_level,
            created_at=meta["created_at"],
        )
        self._register(qa, python_path)
        self._entries[name] = qa

        logger.info("quick_app_created name=%s source=%s venv=%d", name, source_type.value, venv_level.value)
        return qa

    def load_all(self) -> list[QuickApp]:
        """扫描 .chips/quick_apps/ 并注册所有已存在的快应用。

        按 app_dir 下的 meta.json 恢复注册。
        跳过隐藏目录（以 . 开头）和没有 meta.json 的目录。
        """
        self.ensure_dirs()
        loaded: list[QuickApp] = []

        for item in sorted(QUICK_APPS_DIR.iterdir()):
            if not item.is_dir():
                continue
            if item.name.startswith("."):
                continue  # 跳过 .venv_base 等隐藏目录

            meta_path = item / "meta.json"
            app_path = item / "app.py"
            if not meta_path.exists() or not app_path.exists():
                continue

            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                logger.warning("quick_app_load failed to read meta for %s", item.name)
                continue

            name = meta["name"]
            schema = meta["schema"]
            description = meta.get("description", "")
            source_type = SourceType(meta.get("source_type", "write_code"))
            venv_level = VenvLevel(meta.get("venv_level", 0))
            created_at = meta.get("created_at", "")

            python_path = self.resolve_python(venv_level, name)

            qa = QuickApp(
                name=name,
                description=description,
                app_dir=item,
                schema=schema,
                source_type=source_type,
                venv_level=venv_level,
                created_at=created_at,
            )
            self._register(qa, python_path)
            self._entries[name] = qa
            loaded.append(qa)

        logger.info("quick_app_loaded count=%d", len(loaded))

        # 如果有 SHARED 级别的快应用，确保 base venv 就绪
        has_shared = any(qa.venv_level == VenvLevel.SHARED for qa in loaded)
        if has_shared:
            logger.info("quick_app_load shared_apps_found triggering_base_venv")
            self.ensure_base_venv()

        return loaded

    def run(self, name: str, args: dict) -> str:
        """运行快应用（spawn 子进程）。

        Args:
            name: 工具名。
            args: 参数 dict。

        Returns:
            子进程 stdout 字符串。
        """
        from quick_app.exec_log import record

        entry = self._entries.get(name)
        if not entry:
            record(name, args, 0, False, f"工具不存在")
            return json.dumps({"error": f"快应用「{name}」不存在"})

        app_path = entry.app_dir / "app.py"
        if not app_path.exists():
            self.delete(name)
            record(name, args, 0, False, f"文件已丢失（已自动清理）")
            return json.dumps({"error": f"快应用「{name}」文件已丢失"})

        python_path = self.resolve_python(entry.venv_level, name)
        t0 = time.monotonic()

        try:
            result = subprocess.run(
                [python_path, str(app_path), json.dumps(args, ensure_ascii=False)],
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            elapsed = int((time.monotonic() - t0) * 1000)
            record(name, args, elapsed, False, "执行超时（120 秒），请尝试简化输入或检查工具逻辑")
            return json.dumps({"error": "快应用执行超时（120 秒）"})
        except Exception as e:
            elapsed = int((time.monotonic() - t0) * 1000)
            record(name, args, elapsed, False, f"执行失败：{e}")
            return json.dumps({"error": f"快应用执行失败：{e}"})

        elapsed = int((time.monotonic() - t0) * 1000)

        if result.returncode != 0:
            error_msg = (result.stderr or result.stdout or "未知错误")[:200]
            record(name, args, elapsed, False, f"返回错误：{error_msg}")
            return json.dumps({
                "error": f"快应用返回错误：{(result.stderr or result.stdout)[:500]}",
            })

        record(name, args, elapsed, True, "执行成功")
        return result.stdout or ""

    def delete(self, name: str) -> bool:
        """删除快应用（注销 + 删文件）。"""
        if name not in self._entries:
            logger.warning("quick_app_delete not_found name=%s", name)
            return False

        # 注销
        try:
            self._registry.deregister(name)
        except Exception:
            pass

        # 删文件
        app_dir = self._entries[name].app_dir
        self._entries.pop(name, None)
        self._spawn_handlers.pop(name, None)

        try:
            import shutil
            shutil.rmtree(app_dir)
            logger.info("quick_app_deleted name=%s", name)
            return True
        except Exception as e:
            logger.warning("quick_app_delete failed to remove dir %s: %s", app_dir, e)
            return False

    def list(self) -> list[QuickApp]:
        """列出所有快应用。"""
        return list(self._entries.values())

    def get(self, name: str) -> QuickApp | None:
        """按名称查找快应用。"""
        return self._entries.get(name)

    # ── 内部 ──

    def _register(self, qa: QuickApp, python_path: str) -> None:
        """注册到 ToolRegistry。

        handler 使用 spawn wrapper，内部调用 self.run()。
        """
        name = qa.name

        def spawn_handler(args: dict) -> str:
            return self.run(name, args)

        self._spawn_handlers[name] = spawn_handler
        self._registry.register(
            name=name,
            toolset="quick_apps",
            schema=qa.schema,
            handler=spawn_handler,
        )

    @staticmethod
    def _rollback(app_dir: Path, name: str) -> None:
        """验证失败时回滚——清除已写的文件和目录。"""
        try:
            import shutil
            if app_dir.exists():
                shutil.rmtree(app_dir)
        except Exception:
            pass
        logger.info("quick_app_rollback name=%s dir=%s", name, app_dir)


# ── 模块级已就绪标记（供 import 方判断） ──

manager_ready = False
_manager_instance: QuickAppManager | None = None


def get_manager() -> QuickAppManager:
    global _manager_instance
    if _manager_instance is None:
        _manager_instance = QuickAppManager(registry=registry)
    return _manager_instance


def init_manager() -> QuickAppManager:
    """初始化并扫描已存在的快应用。

    在 CLI 和 Web 启动时各调用一次。
    """
    mgr = get_manager()
    mgr.load_all()

    # 扫描本地已安装的 CLI 工具（仅记录日志）
    from quick_app.cli_detector import get_installed_cli_tools
    from quick_app.known_good import CLI_TOOLS
    cli_names = [e.name for e in CLI_TOOLS]
    installed = get_installed_cli_tools(cli_names)
    if installed:
        logger.info("cli_tools_installed count=%d tools=%s", len(installed), installed)
    else:
        logger.info("cli_tools_installed none_found")

    global manager_ready
    manager_ready = True
    return mgr
