#!/usr/bin/env python3
"""chips 智能启动脚本 — 自动管理后端服务"""

import os
import sys
import signal
import subprocess
import time
import socket
from pathlib import Path

# 配置
BACKEND_HOST = os.getenv("CHIPS_HOST", "0.0.0.0")
BACKEND_PORT = int(os.getenv("CHIPS_PORT", "8648"))
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

# 项目根目录
ROOT_DIR = Path(__file__).parent
VENV_DIR = ROOT_DIR / ".venv"


def get_python() -> str:
    """获取项目虚拟环境的 Python 路径。"""
    venv_python = VENV_DIR / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def is_port_in_use(port: int) -> bool:
    """检查端口是否被占用。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("localhost", port)) == 0


def is_backend_running() -> bool:
    """检查后端是否正在运行。"""
    return is_port_in_use(BACKEND_PORT)


def wait_for_backend(timeout: int = 30) -> bool:
    """等待后端启动。"""
    start = time.time()
    while time.time() - start < timeout:
        if is_backend_running():
            return True
        time.sleep(0.5)
    return False


def start_backend():
    """启动后端服务。"""
    python = get_python()
    print(f"🚀 启动后端服务 (port={BACKEND_PORT})...")
    print(f"   Python: {python}")
    print(f"   Redis: {REDIS_URL}")
    print(f"   提示: 如果 Redis 不可用，将自动降级到内存存储")
    
    # 启动后端
    env = os.environ.copy()
    env["REDIS_URL"] = REDIS_URL
    
    process = subprocess.Popen(
        [python, "-m", "web.server"],
        cwd=str(ROOT_DIR),
        env=env,
    )
    
    print(f"   PID: {process.pid}")
    
    # 等待后端启动
    if wait_for_backend():
        print("✅ 后端服务已启动")
        return process
    else:
        print("❌ 后端服务启动失败")
        process.terminate()
        return None


def start_cli():
    """启动 CLI 前端。"""
    cli_dir = ROOT_DIR / "chips-tui"
    
    if not cli_dir.exists():
        print("❌ CLI 目录不存在: chips-tui/")
        return
    
    print("🖥️  启动 CLI 前端...")
    
    # 检查 node_modules
    if not (cli_dir / "node_modules").exists():
        print("   安装依赖...")
        subprocess.run(["npm", "install"], cwd=str(cli_dir), check=True)
    
    # 启动 CLI
    env = os.environ.copy()
    env["CHIPS_BACKEND_URL"] = f"http://localhost:{BACKEND_PORT}"
    
    subprocess.run(
        ["npm", "run", "dev"],
        cwd=str(cli_dir),
        env=env,
    )


def start_web():
    """启动 Web 前端。"""
    web_dir = ROOT_DIR / "chips-agent-web"
    
    if not web_dir.exists():
        print("❌ Web 目录不存在: chips-agent-web/")
        return
    
    print("🌐 启动 Web 前端...")
    
    # 检查 node_modules
    if not (web_dir / "node_modules").exists():
        print("   安装依赖...")
        subprocess.run(["pnpm", "install"], cwd=str(web_dir), check=True)
    
    # 启动 Web
    subprocess.run(
        ["pnpm", "dev"],
        cwd=str(web_dir),
    )


def main():
    """主函数。"""
    import argparse
    
    parser = argparse.ArgumentParser(description="chips 智能启动脚本")
    parser.add_argument(
        "frontend",
        choices=["cli", "web", "backend", "all"],
        help="启动类型: cli/web/backend/all",
    )
    parser.add_argument(
        "--no-backend",
        action="store_true",
        help="不启动后端（假设后端已运行）",
    )
    
    args = parser.parse_args()
    
    backend_process = None
    
    try:
        # 启动后端（如果需要）
        if args.frontend != "backend" and not args.no_backend:
            if is_backend_running():
                print(f"✅ 后端已在运行 (port={BACKEND_PORT})")
            else:
                backend_process = start_backend()
                if not backend_process:
                    sys.exit(1)
        
        # 启动前端
        if args.frontend == "cli":
            start_cli()
        elif args.frontend == "web":
            start_web()
        elif args.frontend == "backend":
            if is_backend_running():
                print(f"✅ 后端已在运行 (port={BACKEND_PORT})")
            else:
                backend_process = start_backend()
                if backend_process:
                    print("   按 Ctrl+C 停止后端")
                    backend_process.wait()
        elif args.frontend == "all":
            print("   启动所有服务...")
            start_cli()
    
    except KeyboardInterrupt:
        print("\n🛑 停止服务...")
    finally:
        if backend_process:
            backend_process.terminate()
            backend_process.wait()
            print("✅ 后端已停止")


if __name__ == "__main__":
    main()
