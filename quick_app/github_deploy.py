"""GitHub 项目部署 — clone + 安装依赖"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger("chips.quick_app.github_deploy")

# 克隆超时（秒）
_CLONE_TIMEOUT = 120
_INSTALL_TIMEOUT = 300


def deploy_github_project(
    repo_url: str,
    target_dir: Path,
    venv_python: str | None = None,
) -> dict:
    """克隆 GitHub 项目到本地并安装依赖。

    Args:
        repo_url: GitHub 仓库 URL（如 https://github.com/user/repo）。
        target_dir: 克隆目标目录（如 .chips/quick_apps/cookbook/repo/）。
        venv_python: venv 的 Python 路径（用于安装依赖到 venv）。

    Returns:
        {"success": True/False, "error": str, "repo_path": str}。
    """
    from quick_app.trace_log import write as tw
    tw("DEPLOY", f"开始部署 {repo_url}", {"url": repo_url, "target": str(target_dir)})
    logger.info("github_deploy start url=%s target=%s", repo_url, target_dir)

    repo_path = _clone_repo(repo_url, target_dir)
    if not repo_path:
        tw("DEPLOY", "克隆失败", {"url": repo_url})
        return {"success": False, "error": "克隆失败", "repo_path": ""}

    tw("DEPLOY", "克隆完成", {"path": str(repo_path)})

    install_ok = _install_deps(repo_path, venv_python)
    if not install_ok:
        tw("DEPLOY", "依赖安装失败", {"path": str(repo_path)})
        return {"success": False, "error": "依赖安装失败，请手动检查", "repo_path": str(repo_path)}

    tw("DEPLOY", "部署完成", {"path": str(repo_path)})
    logger.info("github_deploy done path=%s", repo_path)
    return {"success": True, "error": "", "repo_path": str(repo_path)}


def _clone_repo(repo_url: str, target_dir: Path) -> Path | None:
    """git clone --depth 1，返回 repo 路径。"""
    # 清理 URL：支持 user/repo 或完整 URL
    if "/" in repo_url and not repo_url.startswith("http"):
        repo_url = f"https://github.com/{repo_url}"

    from quick_app.trace_log import write as tw
    repo_path = target_dir
    if repo_path.exists():
        tw("DEPLOY", "repo 已存在，跳过克隆", {"path": str(repo_path)})
        return repo_path

    repo_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        tw("DEPLOY", "执行 git clone", {"url": repo_url, "target": str(repo_path)})
        result = subprocess.run(
            ["git", "clone", "--depth", "1", repo_url, str(repo_path)],
            capture_output=True, text=True, timeout=_CLONE_TIMEOUT,
        )
        if result.returncode != 0:
            tw("DEPLOY", "git clone 失败", {"error": result.stderr[:200]})
            return None
        tw("DEPLOY", "git clone 成功", {"path": str(repo_path)})
        return repo_path
    except subprocess.TimeoutExpired:
        tw("DEPLOY", "git clone 超时", {"url": repo_url})
        return None
    except Exception as e:
        tw("DEPLOY", "git clone 异常", {"error": str(e)})
        return None


def _pip_cmd(venv_python: str | None) -> list[str]:
    """获取合适的 pip 命令（优先 venv pip，其次 uv pip，最后 pip）。"""
    if venv_python:
        return [venv_python, "-m", "pip"]
    # 检查是否在 uv 环境中
    try:
        import shutil
        if shutil.which("uv"):
            return ["uv", "pip"]
    except Exception:
        pass
    return ["pip3"]


def _install_deps(repo_path: Path, venv_python: str | None) -> bool:
    """安装 requirements.txt 和可选的 setup.py/pyproject.toml。"""
    from quick_app.trace_log import write as tw

    pip = _pip_cmd(venv_python)
    tw("DEPLOY", f"使用 pip 命令", {"cmd": " ".join(pip), "venv": venv_python or "system"})

    # 1. requirements.txt
    req_file = repo_path / "requirements.txt"
    if req_file.exists():
        tw("DEPLOY", "安装 requirements.txt", {"file": str(req_file)})
        try:
            result = subprocess.run(
                pip + ["install", "-r", str(req_file)],
                capture_output=True, text=True, timeout=_INSTALL_TIMEOUT,
            )
            if result.returncode != 0:
                tw("DEPLOY", "pip install -r 失败", {"error": result.stderr[:200]})
                # 尝试用 uv pip 重试
                if "externally-managed" in result.stderr:
                    try:
                        tw("DEPLOY", "重试 uv pip install", {})
                        result2 = subprocess.run(
                            ["uv", "pip", "install", "-r", str(req_file)],
                            capture_output=True, text=True, timeout=_INSTALL_TIMEOUT,
                        )
                        if result2.returncode == 0:
                            tw("DEPLOY", "uv pip install -r 成功", {})
                        else:
                            tw("DEPLOY", "uv pip install -r 也失败", {"error": result2.stderr[:200]})
                    except Exception:
                        pass
            else:
                tw("DEPLOY", "requirements.txt 安装完成", {})
        except subprocess.TimeoutExpired:
            tw("DEPLOY", "pip install -r 超时", {})
    else:
        tw("DEPLOY", "无 requirements.txt", {})

    # 2. setup.py / pyproject.toml（可编辑安装）
    has_setup = (repo_path / "setup.py").exists()
    has_pyproject = (repo_path / "pyproject.toml").exists()
    if has_setup or has_pyproject:
        pkg_type = "setup.py" if has_setup else "pyproject.toml"
        tw("DEPLOY", f"安装项目（{pkg_type}）", {"path": str(repo_path)})
        try:
            result = subprocess.run(
                pip + ["install", "-e", str(repo_path)],
                capture_output=True, text=True, timeout=_INSTALL_TIMEOUT,
            )
            if result.returncode != 0:
                tw("DEPLOY", "pip install -e 失败", {"error": result.stderr[:200]})
                # uv 重试
                if "externally-managed" in result.stderr:
                    try:
                        result2 = subprocess.run(
                            ["uv", "pip", "install", "-e", str(repo_path)],
                            capture_output=True, text=True, timeout=_INSTALL_TIMEOUT,
                        )
                        if result2.returncode == 0:
                            tw("DEPLOY", "uv pip install -e 成功", {})
                            return True
                    except Exception:
                        pass
                return False
            tw("DEPLOY", "项目安装完成", {"type": pkg_type})
        except subprocess.TimeoutExpired:
            tw("DEPLOY", "pip install -e 超时", {})
            return False
        except Exception as e:
            tw("DEPLOY", "pip install -e 异常", {"error": str(e)})
            return False
    else:
        tw("DEPLOY", "无可编辑安装（无 setup.py/pyproject.toml）", {})

    return True


def scan_repo_structure(repo_path: Path, max_files: int = 30) -> str:
    """扫描 repo 文件结构，返回可读的目录树描述（供 LLM 理解项目）。"""
    from quick_app.trace_log import write as tw
    lines = [f"项目根目录: {repo_path.name}", "文件结构:"]
    count = 0
    for root, dirs, files in os.walk(str(repo_path)):
        if count >= max_files:
            lines.append("  ...（更多文件省略）")
            break
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "node_modules", ".venv")]
        rel = os.path.relpath(root, str(repo_path))
        indent = "  " * (rel.count(os.sep) + 1) if rel != "." else "  "
        for f in sorted(files):
            if count >= max_files:
                break
            if any(f.endswith(ext) for ext in (".py", ".js", ".ts", ".md", ".txt", ".toml", ".cfg", ".yaml", ".yml", ".json")):
                fpath = os.path.join(rel, f) if rel != "." else f
                lines.append(f"{indent}{fpath}")
                count += 1
        if count >= max_files:
            break
    tw("DEPLOY", "扫描 repo 结构", {"files": count, "structure": "\n".join(lines)})
    return "\n".join(lines)
