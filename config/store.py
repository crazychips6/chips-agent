"""配置存储 — ~/.chips/config.yaml 读写封装"""

import os
from pathlib import Path

import yaml

_CONFIG_PATH = Path.home() / ".chips" / "config.yaml"

# 已知配置键 → 对应环境变量
KNOWN_KEYS = {
    "model": "CHIPS_MODEL",
    "base_url": "CHIPS_BASE_URL",
    "embedding_provider": "CHIPS_EMBEDDING_PROVIDER",
    "embedding_model": "CHIPS_EMBEDDING_MODEL",
    "embedding_base_url": "CHIPS_EMBEDDING_BASE_URL",
}


class ConfigStore:
    """读写 ~/.chips/config.yaml，提供 get/set/list/apply_to_env。"""

    def __init__(self, path: Path | None = None):
        self._path = path or _CONFIG_PATH

    def _ensure_dir(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self) -> dict:
        if not self._path.exists():
            return {}
        with open(self._path) as f:
            data = yaml.safe_load(f) or {}
        return {k: v for k, v in data.items() if isinstance(k, str)}

    def _write(self, data: dict):
        self._ensure_dir()
        with open(self._path, "w") as f:
            yaml.safe_dump(data, f, default_flow_style=False)

    def get(self, key: str) -> str | None:
        data = self._read()
        return str(data[key]) if key in data else None

    def set(self, key: str, value: str):
        data = self._read()
        data[key] = value
        self._write(data)

    def list_all(self) -> dict:
        return self._read()

    def apply_to_env(self):
        """将已配置的键值写入环境变量（仅当该环境变量未设置时）。"""
        data = self._read()
        for key, env_name in KNOWN_KEYS.items():
            if key in data and not os.getenv(env_name):
                os.environ[env_name] = str(data[key])

    def read_pricing(self) -> dict | None:
        """读取 ~/.chips/config.yaml 中的 models.pricing 配置。"""
        data = self._read()
        pricing = data.get("models", {}).get("pricing")
        return pricing if isinstance(pricing, dict) else None

    def read_mcp_servers(self) -> dict[str, dict]:
        """读取 ~/.chips/config.yaml 中的 mcp_servers 配置。"""
        data = self._read()
        servers = data.get("mcp_servers", {})
        return servers if isinstance(servers, dict) else {}
