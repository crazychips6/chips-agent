"""chips plugin 子命令处理

支持 list / install / remove / info 四个操作。
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from pathlib import Path



def handle_plugin(args):
    if args.plugin_action == "list":
        _list_plugins()
    elif args.plugin_action == "install":
        _install_plugin(args.path_or_package)
    elif args.plugin_action == "remove":
        _remove_plugin(args.name)
    elif args.plugin_action == "info":
        _show_plugin_info(args.name)


# ── 路径 ──


def _get_user_plugin_dir() -> str:
    """返回 ~/.chips/plugins/ 作为标准安装位置。"""
    d = os.path.expanduser("~/.chips/plugins")
    os.makedirs(d, exist_ok=True)
    return d


def _get_scan_dirs() -> list[str]:
    """返回所有插件扫描目录（已展开，仅存在的非包目录）。"""
    dirs: list[str] = []
    for raw in ("~/.chips/plugins", "./plugins"):
        resolved = os.path.abspath(os.path.expanduser(raw))
        if os.path.isdir(resolved) and not os.path.isfile(os.path.join(resolved, "__init__.py")):
            dirs.append(resolved)
    return dirs


def _find_plugin_file(name: str) -> str | None:
    """在扫描目录中查找同名插件文件（不含 .py 后缀）。"""
    for scan_dir in _get_scan_dirs():
        fpath = os.path.join(scan_dir, f"{name}.py")
        if os.path.isfile(fpath):
            return fpath
    return None


# ── 只读检查（加载插件但不注册） ──


def _inspect(filepath: str) -> dict | None:
    """加载插件文件并返回元数据，不注册到任何 registry（dry_run 模式）。

    通过 ``PluginContext(dry_run=True)`` 调用 ``register(ctx)`` 收集信息。
    """
    try:
        module_name = Path(filepath).stem
        spec = importlib.util.spec_from_file_location(module_name, filepath)
        if not spec or not spec.loader:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        register_fn = getattr(module, "register", None)
        if not callable(register_fn):
            return None

        from plugins.protocol import PluginContext
        ctx = PluginContext(dry_run=True)
        register_fn(ctx)

        info: dict = {
            "filepath": os.path.abspath(filepath),
            "tools": [],
            "hooks": False,
            "skills": [],
            "errors": [],
        }

        for t in ctx._tools_info:
            info["tools"].append({
                "name": t["name"],
                "description": t["schema"]
                    .get("function", t["schema"])
                    .get("description", ""),
                "definition_count": t["definition_count"],
                "definitions": [t["schema"]],
            })

        if ctx._hook_plugins:
            info["hooks"] = True

        for s in ctx._skills:
            info["skills"].append({
                "name": s["name"],
                "description": s["description"],
                "path": s.get("path"),
            })

        return info
    except Exception as e:
        return {"filepath": filepath, "tools": [], "hooks": False, "errors": [str(e)]}


# ── list ──


def _list_plugins():
    """列出所有可发现的插件。"""
    all_files: list[str] = []
    seen: set[str] = set()
    for scan_dir in _get_scan_dirs():
        for fname in sorted(os.listdir(scan_dir)):
            if not fname.endswith(".py") or fname == "__init__.py":
                continue
            fpath = os.path.join(scan_dir, fname)
            if fpath not in seen:
                all_files.append(fpath)
                seen.add(fpath)

    if not all_files:
        print("(无插件)")
        return

    print(f"发现 {len(all_files)} 个插件:\n")
    for fpath in all_files:
        name = Path(fpath).stem
        info = _inspect(fpath)
        type_tags = []
        if info and info.get("tools"):
            type_tags.append("tool")
        if info and info.get("hooks"):
            type_tags.append("hook")
        if info and info.get("skills"):
            type_tags.append("skill")
        if not type_tags:
            type_tags.append("?" if info and not info.get("errors") else "err")
        tag = "/".join(type_tags)

        desc = ""
        if info and info.get("tools"):
            desc = info["tools"][0].get("description", "")
        elif info and info.get("errors"):
            desc = f"加载失败: {info['errors'][0]}"

        location = "user" if fpath.startswith(os.path.expanduser("~/.chips/plugins")) else "local"
        print(f"  {name:20} [{tag:6}] {desc:40} ({location})")


# ── info ──


def _show_plugin_info(name: str):
    """显示指定插件的详细信息。"""
    fpath = _find_plugin_file(name)
    if not fpath:
        print(f"插件 '{name}' 未找到。")
        print(f"搜索路径: {', '.join(_get_scan_dirs())}")
        sys.exit(1)

    info = _inspect(fpath)
    if not info:
        print(f"无法加载插件: {fpath}")
        sys.exit(1)

    print(f"插件: {name}")
    print(f"路径: {info['filepath']}")
    print()

    if info["errors"]:
        print(f"错误: {info['errors'][0]}")
        sys.exit(1)

    if info["tools"]:
        print(f"工具 ({len(info['tools'])} 个):")
        for t in info["tools"]:
            print(f"  - {t['name']}: {t['description']}")
            for d in t.get("definitions", []):
                fn = d.get("function", d)
                p = fn.get("parameters", {})
                props = list(p.get("properties", {}).keys()) if p else []
                print(f"    参数: {', '.join(props) if props else '无'}")
            print()

    if info["hooks"]:
        print(f"挂钩点: 是")

    if info["skills"]:
        print(f"技能 ({len(info['skills'])} 个):")
        for s in info["skills"]:
            print(f"  {s['name']:<15} {s['description']}")
            if s.get("path"):
                print(f"  {'':15} SKILL.md: {s['path']}")


# ── install ──


def _install_plugin(src: str):
    """安装插件：复制 .py 文件到 ~/.chips/plugins/。"""
    src_path = os.path.abspath(os.path.expanduser(src))

    if not os.path.isfile(src_path):
        print(f"错误: 文件不存在: {src}")
        sys.exit(1)

    if not src_path.endswith(".py"):
        print("错误: 仅支持 .py 文件安装")
        sys.exit(1)

    dest_dir = _get_user_plugin_dir()
    dest_path = os.path.join(dest_dir, os.path.basename(src_path))

    if os.path.exists(dest_path):
        print(f"错误: 插件已存在: {os.path.basename(src_path)}")
        print(f"路径: {dest_path}")
        sys.exit(1)

    shutil.copy2(src_path, dest_path)
    print(f"已安装: {os.path.basename(src_path)}")
    print(f"路径: {dest_path}")

    # 验证可加载
    info = _inspect(dest_path)
    if not info:
        print("警告: 文件已复制但无法加载为有效插件")
        return
    if info.get("errors"):
        print(f"警告: 加载异常 — {info['errors'][0]}")
        return
    types = []
    if info["tools"]:
        types.append(f"工具({len(info['tools'])})")
    if info["hooks"]:
        types.append("挂钩")
    print(f"类型: {', '.join(types)}")


# ── remove ──


def _remove_plugin(name: str):
    """卸载插件：删除插件文件。"""
    fpath = _find_plugin_file(name)
    if not fpath:
        print(f"错误: 插件 '{name}' 未找到")
        sys.exit(1)

    os.remove(fpath)
    print(f"已删除: {name} ({fpath})")
