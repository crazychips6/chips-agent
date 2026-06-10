"""process 工具 — 进程管理（列出/查看/终止）

支持 Linux 和 Windows，通过 ps/tasklist/kill/taskkill 等系统命令实现。
所有操作经过安全审批（通过 terminal tool 的 approval 层）。
"""

from __future__ import annotations

import json
import os
import platform
import shlex
import subprocess

from tool.registry import registry


def _run(cmd: list[str], timeout: int = 15) -> tuple[int, str, str]:
    """运行系统命令，返回 (returncode, stdout, stderr)。"""
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        stdout, stderr = proc.communicate(timeout=timeout)
        return proc.returncode or 0, stdout or "", stderr or ""
    except subprocess.TimeoutExpired:
        proc.kill()
        return -1, "", f"命令执行超时 ({timeout}s)"
    except FileNotFoundError:
        return -1, "", f"命令未找到: {cmd[0]}"
    except Exception as e:
        return -1, "", str(e)


def _handle(args: dict) -> str:
    action = args.get("action", "list")
    pid = args.get("pid")
    name = args.get("name", "")
    signal = args.get("signal", "TERM")

    system = platform.system()

    if action == "list":
        return _list_processes(system, args)
    if action == "get":
        return _get_process(system, pid, name)
    if action == "kill":
        return _kill_process(system, pid, name, signal)

    return json.dumps({"error": f"未知操作: {action}（支持: list, get, kill）"})


# ── list ──


def _list_processes(system: str, args: dict) -> str:
    sort_by = args.get("sort", "cpu")  # cpu, mem, pid, name
    limit = min(args.get("limit", 30), 200)

    if system == "Linux":
        # ps 输出解析
        sort_flag = {"cpu": "-pcpu", "mem": "-pmem", "pid": "pid", "name": "comm"}.get(sort_by, "-pcpu")
        code, out, err = _run(
            ["ps", f"--sort={sort_flag}", "axo", "pid,ppid,user,%cpu,%mem,rss,etime,comm"],
        )
        if code != 0:
            return json.dumps({"error": f"ps 失败: {err}"})

        lines = out.strip().splitlines()
        if len(lines) < 2:
            return json.dumps({"processes": [], "count": 0})

        header = lines[0].split()
        processes = []
        for line in lines[1:]:
            parts = line.rsplit(maxsplit=7)
            if len(parts) < 8:
                continue
            try:
                processes.append({
                    "pid": int(parts[0]),
                    "ppid": int(parts[1]),
                    "user": parts[2],
                    "cpu_pct": float(parts[3]),
                    "mem_pct": float(parts[4]),
                    "rss_mb": round(int(parts[5]) / 1024, 1) if parts[5].isdigit() else 0,
                    "etime": parts[6],
                    "name": parts[7],
                })
            except (ValueError, IndexError):
                continue

    elif system == "Windows":
        code, out, err = _run(["tasklist", "/FO", "CSV", "/NH"])
        if code != 0:
            return json.dumps({"error": f"tasklist 失败: {err}"})

        processes = []
        for line in out.strip().splitlines():
            try:
                # tasklist CSV: "name","pid","session","session#","mem"
                parts = _parse_csv_line(line)
                if len(parts) < 5:
                    continue
                mem_str = parts[4].replace(",", "").replace(" K", "").strip()
                mem_kb = int(mem_str) if mem_str.isdigit() else 0
                processes.append({
                    "name": parts[0].strip('"'),
                    "pid": int(parts[1].strip('"')),
                    "session_name": parts[2].strip('"'),
                    "session_num": parts[3].strip('"'),
                    "rss_mb": round(mem_kb / 1024, 1),
                })
            except (ValueError, IndexError):
                continue
    else:
        return json.dumps({"error": f"不支持的系统: {system}"})

    # 排序
    if sort_by == "cpu" and "cpu_pct" in (processes[0] if processes else {}):
        processes.sort(key=lambda p: p.get("cpu_pct", 0), reverse=True)
    elif sort_by == "mem":
        processes.sort(key=lambda p: p.get("rss_mb", 0), reverse=True)
    elif sort_by == "pid":
        processes.sort(key=lambda p: p.get("pid", 0))

    processes = processes[:limit]
    return json.dumps({
        "processes": processes,
        "count": len(processes),
        "total": len(processes),  # 实际总数可能更多，受 limit 限制
        "system": system,
    }, ensure_ascii=False)


# ── get（单进程详情）──


def _get_process(system: str, pid: int | None, name: str) -> str:
    if not pid and not name:
        return json.dumps({"error": "需要 pid 或 name 参数"})

    if system == "Linux":
        if pid:
            return _get_linux_process_by_pid(pid)
        return _find_linux_process_by_name(name)
    elif system == "Windows":
        return _get_windows_process(pid, name)

    return json.dumps({"error": f"不支持的系统: {system}"})


def _get_linux_process_by_pid(pid: int) -> str:
    """读取 /proc/[pid]/status + /proc/[pid]/cmdline 获取进程详情。"""
    try:
        status = {}
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if ":" in line:
                    k, v = line.split(":", 1)
                    status[k.strip()] = v.strip()

        cmdline = ""
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                raw = f.read()
                cmdline = raw.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
        except (FileNotFoundError, PermissionError):
            pass

        # 内存
        mem_info = {}
        try:
            with open(f"/proc/{pid}/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        mem_info["rss_kb"] = int(line.split()[1])
                    elif line.startswith("VmSize:"):
                        mem_info["size_kb"] = int(line.split()[1])
        except (FileNotFoundError, PermissionError):
            pass

        return json.dumps({
            "pid": pid,
            "name": status.get("Name", ""),
            "state": status.get("State", ""),
            "ppid": int(status.get("PPid", 0)),
            "uid": status.get("Uid", ""),
            "threads": int(status.get("Threads", 0)),
            "rss_mb": round(mem_info.get("rss_kb", 0) / 1024, 1),
            "vsize_mb": round(mem_info.get("size_kb", 0) / 1024, 1),
            "cmdline": cmdline[:500] if cmdline else "",
        }, ensure_ascii=False)

    except FileNotFoundError:
        return json.dumps({"error": f"进程 {pid} 不存在"})
    except PermissionError:
        return json.dumps({"error": f"无权限访问进程 {pid}"})
    except Exception as e:
        return json.dumps({"error": str(e)})


def _find_linux_process_by_name(name: str) -> str:
    """通过 pgrep 或 ps 搜索进程名。"""
    # 先尝试 pgrep（精确）
    code, out, _ = _run(["pgrep", "-x", name])
    if code == 0 and out.strip():
        pids = [int(p) for p in out.strip().splitlines()[:20]]
        # 对每个 PID 获取详情
        results = []
        for pid in pids[:10]:  # 最多取前 10 个
            try:
                results.append(json.loads(_get_linux_process_by_pid(pid)))
            except (json.JSONDecodeError, ValueError):
                continue
        return json.dumps({"matches": results, "count": len(results)}, ensure_ascii=False)

    # 降级：模糊搜索
    code, out, _ = _run(["ps", "-eo", "pid,comm", "--sort=-%cpu"])
    if code != 0:
        return json.dumps({"error": "搜索失败"})

    matches = []
    for line in out.strip().splitlines()[1:]:
        parts = line.strip().split(maxsplit=1)
        if len(parts) == 2 and name.lower() in parts[1].lower():
            matches.append({"pid": int(parts[0]), "name": parts[1]})

    return json.dumps({"matches": matches[:20], "count": len(matches)}, ensure_ascii=False)


def _get_windows_process(pid: int | None, name: str) -> str:
    """通过 tasklist 获取 Windows 进程详情。"""
    if pid:
        code, out, err = _run(["tasklist", "/FO", "CSV", "/FI", f"PID eq {pid}"])
    else:
        code, out, err = _run(["tasklist", "/FO", "CSV", "/FI", f"IMAGENAME eq {name}"])

    if code != 0:
        return json.dumps({"error": f"查询失败: {err}"})

    lines = [l for l in out.strip().splitlines() if l]
    if len(lines) < 2:
        return json.dumps({"error": f"未找到进程" + (f" PID={pid}" if pid else f" name={name}")})

    parts = _parse_csv_line(lines[1])
    if len(parts) < 5:
        return json.dumps({"error": "解析失败"})

    mem_str = parts[4].replace(",", "").replace(" K", "").strip()
    mem_kb = int(mem_str) if mem_str.isdigit() else 0

    return json.dumps({
        "name": parts[0].strip('"'),
        "pid": int(parts[1].strip('"')),
        "session_name": parts[2].strip('"'),
        "session_num": parts[3].strip('"'),
        "rss_mb": round(mem_kb / 1024, 1),
    }, ensure_ascii=False)


# ── kill ──


def _kill_process(system: str, pid: int | None, name: str, signal: str) -> str:
    if not pid and not name:
        return json.dumps({"error": "需要 pid 或 name 参数"})
    if pid and name:
        return json.dumps({"error": "pid 和 name 只能二选一"})

    if system == "Linux":
        if name:
            # 按名杀进程：先查 PID，再 kill
            code, out, _ = _run(["pgrep", "-x", name])
            if code != 0 or not out.strip():
                return json.dumps({"error": f"未找到进程: {name}"})
            pids = [int(p) for p in out.strip().splitlines()]
            results = []
            for pid_to_kill in pids:
                result = _do_kill_linux(pid_to_kill, signal)
                results.append(result)
            return json.dumps({"results": results, "total": len(results)}, ensure_ascii=False)
        else:
            result = _do_kill_linux(pid, signal)
            return json.dumps(result, ensure_ascii=False)

    elif system == "Windows":
        return _do_kill_windows(pid=pid, name=name, force=(signal == "KILL"))

    return json.dumps({"error": f"不支持的系统: {system}"})


def _do_kill_linux(pid: int, signal: str) -> dict:
    """向 Linux 进程发送信号。"""
    sig_map = {
        "TERM": "15", "KILL": "9", "HUP": "1", "INT": "2", "QUIT": "3",
        "STOP": "19", "CONT": "18",
    }
    sig_num = sig_map.get(signal.upper(), signal)

    code, _, err = _run(["kill", f"-{sig_num}", str(pid)])
    return {
        "pid": pid,
        "signal": signal.upper(),
        "success": code == 0,
        "error": err if code != 0 else None,
    }


def _do_kill_windows(pid: int | None, name: str | None, force: bool) -> str:
    """终止 Windows 进程。"""
    cmd = ["taskkill"]
    if force:
        cmd.append("/F")
    if pid:
        cmd.extend(["/PID", str(pid)])
    elif name:
        cmd.extend(["/IM", name])
    cmd.extend(["/T"])  # 终止子进程

    code, _, err = _run(cmd)
    if code == 0:
        target = f"PID={pid}" if pid else f"name={name}"
        return json.dumps({"success": True, "target": target})
    return json.dumps({"error": f"终止失败: {err}"})


# ── CSV 解析辅助 ──


def _parse_csv_line(line: str) -> list[str]:
    """简易 CSV 行解析（仅处理双引号包裹的字段）。"""
    fields = []
    current = ""
    in_quotes = False
    for ch in line:
        if ch == '"':
            in_quotes = not in_quotes
        elif ch == "," and not in_quotes:
            fields.append(current)
            current = ""
        else:
            current += ch
    fields.append(current)
    return fields


# ── Schema & 注册 ──


PROCESS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "process",
        "description": (
            "进程管理工具。支持三个操作：\n\n"
            "1. list — 列出当前进程（默认按 CPU 排序）\n"
            "   参数: sort=cpu|mem|pid|name, limit=30\n\n"
            "2. get — 查看单个进程详情\n"
            "   参数: pid=1234 或 name='nginx'\n\n"
            "3. kill — 终止进程\n"
            "   参数: pid=1234 或 name='nginx', signal=TERM|KILL|HUP|INT\n"
            "   Linux 支持多种信号；Windows 下 KILL 强制终止\n\n"
            "注意：kill 操作会经过安全审批，部分操作需要确认。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "get", "kill"],
                    "description": "操作类型",
                },
                "pid": {
                    "type": "integer",
                    "description": "进程 PID",
                },
                "name": {
                    "type": "string",
                    "description": "进程名（get/kill 按名搜索）",
                },
                "sort": {
                    "type": "string",
                    "enum": ["cpu", "mem", "pid", "name"],
                    "description": "排序方式（list 操作使用，默认 cpu）",
                },
                "limit": {
                    "type": "integer",
                    "description": "返回条数上限（list 操作使用，默认 30，最大 200）",
                },
                "signal": {
                    "type": "string",
                    "enum": ["TERM", "KILL", "HUP", "INT", "QUIT", "STOP", "CONT"],
                    "description": "信号类型（kill 操作使用，默认 TERM）",
                },
            },
            "required": ["action"],
        },
    },
}

registry.register(
    name="process",
    toolset="process",
    schema=PROCESS_SCHEMA,
    handler=_handle,
)
