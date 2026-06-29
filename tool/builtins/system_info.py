"""system_info 工具 — 获取操作系统、CPU、内存、磁盘等系统信息

设计原则：
  - 零外部依赖（仅 Python stdlib）
  - 跨平台：Linux + Windows（平台差异在内部适配）
  - 结构化 JSON 输出，便于 LLM 解析
  - 所有异常内部消化，不抛到上层

数据来源：
  - Linux: /proc/cpuinfo, /proc/meminfo, /proc/uptime, /etc/os-release
  - Windows: wmic（CPU / 内存 / 磁盘）, ctypes（版本号）
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys

from tool.registry import registry


# ── OS 信息 ──


def _get_os_info() -> dict:
    """跨平台操作系统信息。"""
    uname = platform.uname()
    info: dict = {
        "system": uname.system,
        "hostname": uname.node,
        "kernel_release": uname.release,
        "kernel_version": uname.version,
        "architecture": uname.machine,
    }

    if uname.system == "Linux":
        release = _read_os_release()
        if release:
            info.update(release)
    elif uname.system == "Windows":
        info["display_version"] = _get_windows_display_version()

    return info


def _read_os_release() -> dict | None:
    """读取 /etc/os-release 获取 Linux 发行版信息。"""
    for path in ("/etc/os-release", "/usr/lib/os-release"):
        try:
            with open(path, encoding="utf-8") as f:
                data: dict[str, str] = {}
                for line in f:
                    line = line.strip()
                    if "=" in line:
                        k, v = line.split("=", 1)
                        data[k] = v.strip('"')
                if data:
                    return {
                        "distro": data.get("NAME", ""),
                        "version_id": data.get("VERSION_ID", ""),
                        "pretty_name": data.get("PRETTY_NAME", ""),
                    }
        except (FileNotFoundError, PermissionError):
            continue
    return None


def _get_windows_display_version() -> str:
    """Windows 版本号（主版本.次版本.构建号）。"""
    try:
        import ctypes

        class OSVERSIONINFOEXW(ctypes.Structure):
            _fields_ = [
                ("dwOSVersionInfoSize", ctypes.c_ulong),
                ("dwMajorVersion", ctypes.c_ulong),
                ("dwMinorVersion", ctypes.c_ulong),
                ("dwBuildNumber", ctypes.c_ulong),
                ("dwPlatformId", ctypes.c_ulong),
                ("szCSDVersion", ctypes.c_wchar * 128),
            ]

        ver = OSVERSIONINFOEXW()
        ver.dwOSVersionInfoSize = ctypes.sizeof(OSVERSIONINFOEXW)
        ctypes.windll.ntdll.RtlGetVersion(ctypes.byref(ver))
        return f"{ver.dwMajorVersion}.{ver.dwMinorVersion}.{ver.dwBuildNumber}"
    except Exception:
        return platform.version()


# ── CPU 信息 ──


def _get_cpu_info() -> dict:
    """CPU 信息。"""
    info: dict = {
        "logical_cores": os.cpu_count() or 0,
    }

    system = platform.system()
    if system == "Linux":
        _fill_linux_cpu(info)
    elif system == "Windows":
        _fill_windows_cpu(info)
    else:
        # 兜底：platform.processor() 可能为空
        proc = platform.processor()
        if proc:
            info["model"] = proc

    return info


def _fill_linux_cpu(info: dict) -> None:
    """从 /proc/cpuinfo 读取 Linux CPU 信息。"""
    try:
        with open("/proc/cpuinfo") as f:
            content = f.read()
    except (FileNotFoundError, PermissionError):
        return

    model_seen = False
    for line in content.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k, v = k.strip(), v.strip()

        if k == "model name" and not model_seen:
            info["model"] = v
            model_seen = True
        elif k == "cpu cores":
            info["physical_cores"] = int(v)


def _fill_windows_cpu(info: dict) -> None:
    """通过 wmic 获取 Windows CPU 信息。"""
    try:
        result = subprocess.run(
            ["wmic", "cpu", "get", "Name,NumberOfCores,NumberOfLogicalProcessors",
             "/format:csv"],
            capture_output=True, text=True, timeout=10,
        )
        lines = [l for l in result.stdout.strip().splitlines() if l]
        if len(lines) < 2:
            return
        parts = lines[1].split(",")
        if len(parts) >= 2 and parts[1].strip():
            info["model"] = parts[1].strip()
        if len(parts) >= 3 and parts[2].strip().isdigit():
            info["physical_cores"] = int(parts[2].strip())
        if len(parts) >= 4 and parts[3].strip().isdigit():
            info["logical_cores"] = int(parts[3].strip())
    except Exception:
        pass


# ── 内存信息 ──


def _get_memory_info() -> dict:
    """内存信息（总量 / 已用 / 可用 / 使用率）。"""
    system = platform.system()
    if system == "Linux":
        return _get_linux_memory()
    if system == "Windows":
        return _get_windows_memory()
    return {}


def _get_linux_memory() -> dict:
    """从 /proc/meminfo 读取 Linux 内存。"""
    info: dict = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if ":" not in line:
                    continue
                k, v = line.split(":", 1)
                v = v.strip()
                # kB 值转为 GB
                if k == "MemTotal":
                    info["total_gb"] = _kb_to_gb(v)
                elif k == "MemAvailable":
                    info["available_gb"] = _kb_to_gb(v)
                elif k == "SwapTotal":
                    info["swap_total_gb"] = _kb_to_gb(v)
                elif k == "SwapFree":
                    info["swap_free_gb"] = _kb_to_gb(v)
    except (FileNotFoundError, PermissionError):
        return info

    if "total_gb" in info:
        info["used_gb"] = round(info["total_gb"] - info.get("available_gb", 0), 1)
        info["usage_pct"] = round(info["used_gb"] / info["total_gb"] * 100, 1)
    return info


def _get_windows_memory() -> dict:
    """通过 wmic 获取 Windows 内存信息。"""
    info: dict = {}
    try:
        result = subprocess.run(
            ["wmic", "OS", "get", "TotalVisibleMemorySize,FreePhysicalMemory",
             "/format:csv"],
            capture_output=True, text=True, timeout=10,
        )
        lines = [l for l in result.stdout.strip().splitlines() if l]
        if len(lines) >= 2:
            parts = lines[1].split(",")
            if len(parts) >= 3 and parts[1].strip().isdigit() and parts[2].strip().isdigit():
                total_kb = int(parts[1].strip())
                free_kb = int(parts[2].strip())
                info["total_gb"] = round(total_kb / 1024 / 1024, 1)
                info["available_gb"] = round(free_kb / 1024 / 1024, 1)
                info["used_gb"] = round(info["total_gb"] - info["available_gb"], 1)
                info["usage_pct"] = round(info["used_gb"] / info["total_gb"] * 100, 1)
    except Exception:
        pass
    return info


def _kb_to_gb(raw: str) -> float:
    """将 '/proc/meminfo' 的 kB 值转为 GB。"""
    try:
        return round(int(raw.split()[0]) / 1024 / 1024, 1)
    except (ValueError, IndexError):
        return 0.0


# ── 磁盘信息 ──


def _get_disk_info() -> list[dict]:
    """磁盘分区使用信息。"""
    system = platform.system()
    mounts = _get_disk_mounts(system)
    disks: list[dict] = []
    seen: set[str] = set()

    for mount in mounts:
        real = os.path.realpath(mount)
        if real in seen:
            continue
        seen.add(real)
        try:
            usage = shutil.disk_usage(mount)
            total_gb = round(usage.total / 1024**3, 1)
            free_gb = round(usage.free / 1024**3, 1)
            used_gb = round(total_gb - free_gb, 1)
            disks.append({
                "mount": mount,
                "total_gb": total_gb,
                "used_gb": used_gb,
                "free_gb": free_gb,
                "usage_pct": round(used_gb / total_gb * 100, 1) if total_gb > 0 else 0,
            })
        except (FileNotFoundError, PermissionError, OSError):
            continue
        if len(disks) >= 5:  # 最多 5 个分区
            break

    return disks


def _get_disk_mounts(system: str) -> list[str]:
    """获取待检测的磁盘挂载点列表。"""
    if system == "Linux":
        mounts = ["/"]
        try:
            with open("/proc/mounts") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) < 3:
                        continue
                    fstype = parts[2]
                    mount = parts[1]
                    if fstype in ("ext4", "ext3", "xfs", "btrfs", "zfs", "ntfs") \
                            and mount not in mounts and mount.startswith("/"):
                        mounts.append(mount)
        except (FileNotFoundError, PermissionError):
            pass
        return mounts

    if system == "Windows":
        drives: list[str] = []
        try:
            result = subprocess.run(
                ["wmic", "logicaldisk", "where", "DriveType=3",
                 "get", "DeviceID", "/format:csv"],
                capture_output=True, text=True, timeout=10,
            )
            for line in result.stdout.strip().splitlines()[1:]:
                parts = line.split(",")
                if len(parts) >= 2 and parts[1].strip():
                    drives.append(parts[1].strip() + "\\")
        except Exception:
            pass
        return drives or ["C:\\"]

    return ["/"]


# ── 运行时间 ──


def _get_uptime() -> str | None:
    """系统运行时间，格式如 '3d 12h 34m'。"""
    system = platform.system()
    if system == "Linux":
        try:
            with open("/proc/uptime") as f:
                seconds = float(f.read().split()[0])
            return _format_uptime(seconds)
        except (FileNotFoundError, PermissionError, ValueError):
            pass
    elif system == "Windows":
        try:
            result = subprocess.run(
                ["wmic", "OS", "get", "LastBootUpTime", "/format:csv"],
                capture_output=True, text=True, timeout=10,
            )
            lines = [l for l in result.stdout.strip().splitlines() if l]
            if len(lines) >= 2:
                parts = lines[1].split(",")
                if len(parts) >= 2 and parts[1].strip():
                    # WMIC 时间格式: YYYYMMDDHHMMSS.mmm+OOO
                    import datetime as dt
                    wmic_time = parts[1].strip().split(".")[0]
                    boot = dt.datetime.strptime(wmic_time, "%Y%m%d%H%M%S")
                    now = dt.datetime.now()
                    delta = now - boot
                    return _format_uptime(delta.total_seconds())
        except Exception:
            pass
    return None


def _format_uptime(seconds: float) -> str:
    """将秒数转为人类可读格式。"""
    days, rem = divmod(int(seconds), 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


# ── Python 运行时 ──


def _get_python_info() -> dict:
    """Python 解释器信息。"""
    return {
        "version": sys.version.split()[0],
        "executable": sys.executable,
    }


# ── 主入口 ──


def _handle(args: dict) -> str:
    """系统信息主入口，聚合所有子系统信息。"""
    info: dict = {}
    info["os"] = _get_os_info()
    info["cpu"] = _get_cpu_info()
    info["memory"] = _get_memory_info()
    info["disk"] = _get_disk_info()
    info["python"] = _get_python_info()

    uptime = _get_uptime()
    if uptime:
        info["uptime"] = uptime

    return json.dumps(info, ensure_ascii=False)


# ── Schema & 注册 ──

SYSTEM_INFO_SCHEMA = {
    "type": "function",
    "function": {
        "name": "system_info",
        "description": "获取系统信息",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
}

registry.register(
    name="system_info",
    toolset="system",
    schema=SYSTEM_INFO_SCHEMA,
    handler=_handle,
)
