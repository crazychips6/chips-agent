"""配置存储 — ~/.chips/config.yaml 读写封装

读取策略：命令行参数 > 环境变量 > config.yaml > 写死默认值
职责边界：
  - config.yaml: 持久化的结构化配置（定价、工具集、非敏感选项）
  - 环境变量: 敏感凭证（API Key、Token）、部署环境差异
  - 命令行参数: 一次性临时覆盖

凭证（API Key 等）只从环境变量读取，config.yaml 不存任何敏感信息。
"""

import os
from pathlib import Path

import yaml

_CONFIG_PATH = Path.home() / ".chips" / "config.yaml"


class ConfigStore:
    """读写 ~/.chips/config.yaml，提供 get/set/list/get_effective。"""

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
        """读取配置键值（仅 config.yaml，不查环境变量）。"""
        data = self._read()
        return str(data[key]) if key in data else None

    def get_effective(self, key: str, env_name: str = "", default: str = "") -> str:
        """统一读取：环境变量 > config.yaml > 默认值。

        Args:
            key: config.yaml 中的键名
            env_name: 环境变量名（可选）
            default: 兜底默认值

        Returns:
            第一个非空值，按优先级: env > yaml > default
        """
        if env_name:
            val = os.getenv(env_name)
            if val:
                return val
        data = self._read()
        if key in data:
            return str(data[key])
        return default

    def set(self, key: str, value: str):
        data = self._read()
        data[key] = value
        self._write(data)

    def list_all(self) -> dict:
        return self._read()

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

    def read_model_backends(self) -> list[dict]:
        """读取模型后端配置（主用 + 备用）。

        主用模型和备用模型用同一套字段结构：

            model: 模型名
            base_url: API 地址（可选）
            api_key: 直接填 API Key（个人使用最方便，.chips/ 已在 .gitignore）
            api_key_env: 指定环境变量名（共享/部署环境推荐）

        优先级: api_key（直接值）> api_key_env（环境变量）> DEEPSEEK_API_KEY（兜底）

        格式（config.yaml）::

            model: deepseek-chat
            base_url: https://api.deepseek.com
            api_key: sk-xxx                          # 主模型方式1：直接填
            # api_key_env: DEEPSEEK_API_KEY           # 主模型方式2：引用环境变量

            fallback_models:
              - model: gpt-4o-mini
                base_url: https://api.openai.com/v1
                api_key_env: OPENAI_API_KEY           # 备用模型引用环境变量

              - model: claude-sonnet-4-20250514
                api_key: sk-ant-xxx                   # 备用模型直接填

              - model: deepseek-reasoner               # 都不填 → DEEPSEEK_API_KEY

        Returns:
            按优先级排序的列表: [{"model": ..., "base_url": ..., "api_key": ...}, ...]
        """
        import logging
        logger = logging.getLogger("chips.config.store")

        data = self._read()
        backends: list[dict] = []

        def _resolve_key(entry: dict) -> str:
            """从条目中解析 API Key，统一逻辑供主用和备用使用。"""
            key = entry.get("api_key") or ""
            if key:
                return key
            env_name = entry.get("api_key_env") or ""
            if env_name:
                return os.getenv(env_name, "")
            return os.getenv("DEEPSEEK_API_KEY", "")

        # ── 主用模型（支持 api_key / api_key_env，统一走 _resolve_key） ──
        primary = {
            "model": str(data.get("model", "deepseek-chat")),
            "base_url": str(data.get("base_url", "")),
            "api_key": data.get("api_key", ""),
            "api_key_env": data.get("api_key_env", ""),
        }
        api_key = _resolve_key(primary)
        if api_key:
            backends.append({
                "model": primary["model"],
                "base_url": primary["base_url"],
                "api_key": api_key,
            })
        else:
            # 主模型没有 key 是致命问题，日志警告但继续（让调用方失败）
            logger.error("primary_model_no_key model=%s", primary["model"])
            backends.append({
                "model": primary["model"],
                "base_url": primary["base_url"],
                "api_key": "",
            })

        # ── 备用模型 ──
        fb_list = data.get("fallback_models", [])
        if isinstance(fb_list, list):
            for i, fb in enumerate(fb_list):
                if not isinstance(fb, dict):
                    continue
                fb_name = fb.get("model", "")
                if not fb_name:
                    continue
                api_key = _resolve_key(fb)
                if not api_key:
                    logger.info("fallback_model_skip_no_key model=%s idx=%d", fb_name, i)
                    continue
                backends.append({
                    "model": fb_name,
                    "base_url": fb.get("base_url", "") or "",
                    "api_key": api_key,
                })

        return backends
