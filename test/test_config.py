"""ConfigStore 单元测试"""

import os
from pathlib import Path

import pytest
import yaml

from config.store import ConfigStore


@pytest.fixture
def tmp_config(tmp_path: Path) -> Path:
    """返回一个临时配置文件路径，测试前保证文件不存在。"""
    path = tmp_path / "config.yaml"
    if path.exists():
        path.unlink()
    return path


class TestConfigStore:
    def test_init_empty(self, tmp_config):
        store = ConfigStore(tmp_config)
        assert store.list_all() == {}

    def test_set_and_get(self, tmp_config):
        store = ConfigStore(tmp_config)
        store.set("model", "gpt-4")
        assert store.get("model") == "gpt-4"

    def test_get_nonexistent(self, tmp_config):
        store = ConfigStore(tmp_config)
        assert store.get("nonexistent") is None

    def test_set_overwrites(self, tmp_config):
        store = ConfigStore(tmp_config)
        store.set("model", "gpt-4")
        store.set("model", "gpt-5")
        assert store.get("model") == "gpt-5"

    def test_list_all(self, tmp_config):
        store = ConfigStore(tmp_config)
        store.set("a", "1")
        store.set("b", "2")
        assert store.list_all() == {"a": "1", "b": "2"}

    def test_persistence(self, tmp_config):
        store = ConfigStore(tmp_config)
        store.set("key", "val")
        store2 = ConfigStore(tmp_config)
        assert store2.get("key") == "val"

    def test_yaml_format(self, tmp_config):
        store = ConfigStore(tmp_config)
        store.set("model", "deepseek-chat")
        with open(tmp_config) as f:
            data = yaml.safe_load(f)
        assert data == {"model": "deepseek-chat"}

    def test_get_effective_env_overrides_yaml(self, tmp_config, monkeypatch):
        """环境变量 > config.yaml。"""
        store = ConfigStore(tmp_config)
        store.set("model", "from-yaml")
        monkeypatch.setenv("CHIPS_MODEL", "from-env")
        assert store.get_effective("model", "CHIPS_MODEL") == "from-env"

    def test_get_effective_yaml_fallback(self, tmp_config, monkeypatch):
        """config.yaml 在无环境变量时生效。"""
        store = ConfigStore(tmp_config)
        store.set("model", "from-yaml")
        monkeypatch.delenv("CHIPS_MODEL", raising=False)
        assert store.get_effective("model", "CHIPS_MODEL") == "from-yaml"

    def test_get_effective_default(self, tmp_config, monkeypatch):
        """无环境变量无 yaml 时返回默认值。"""
        store = ConfigStore(tmp_config)
        monkeypatch.delenv("CHIPS_MODEL", raising=False)
        assert store.get_effective("model", "CHIPS_MODEL", "deepseek-chat") == "deepseek-chat"

    def test_get_effective_no_env_name(self, tmp_config, monkeypatch):
        """不传 env_name 时只查 yaml。"""
        store = ConfigStore(tmp_config)
        store.set("key", "val")
        assert store.get_effective("key") == "val"

    def test_read_pricing_none(self, tmp_config):
        """没有配置 pricing 时返回 None。"""
        store = ConfigStore(tmp_config)
        assert store.read_pricing() is None

    def test_read_pricing(self, tmp_config):
        """正确读取嵌套的 models.pricing 配置。"""
        cfg = {"models": {"pricing": {"deepseek-chat": {"input": 0.001, "output": 0.002}}}}
        with open(tmp_config, "w") as f:
            yaml.dump(cfg, f)
        store = ConfigStore(tmp_config)
        pricing = store.read_pricing()
        assert pricing is not None
        assert pricing["deepseek-chat"]["input"] == 0.001

    def test_read_model_backends_primary(self, tmp_config, monkeypatch):
        """主用模型从 model/base_url 读取，API Key 从环境变量。"""
        cfg = {"model": "deepseek-chat", "base_url": "https://api.deepseek.com"}
        with open(tmp_config, "w") as f:
            yaml.dump(cfg, f)
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
        store = ConfigStore(tmp_config)
        backends = store.read_model_backends()
        assert len(backends) >= 1
        assert backends[0]["model"] == "deepseek-chat"
        assert backends[0]["api_key"] == "sk-test"

    def test_read_primary_inline_key(self, tmp_config):
        """主模型 api_key 直接填值。"""
        cfg = {"model": "gpt-4o", "api_key": "sk-inline"}
        with open(tmp_config, "w") as f:
            yaml.dump(cfg, f)
        store = ConfigStore(tmp_config)
        backends = store.read_model_backends()
        assert backends[0]["api_key"] == "sk-inline"

    def test_read_primary_key_env(self, tmp_config, monkeypatch):
        """主模型 api_key_env 引用环境变量。"""
        cfg = {"model": "claude-sonnet", "api_key_env": "ANTHROPIC_API_KEY"}
        with open(tmp_config, "w") as f:
            yaml.dump(cfg, f)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        store = ConfigStore(tmp_config)
        backends = store.read_model_backends()
        assert backends[0]["api_key"] == "sk-ant-test"

    def test_read_fallback_backends_env(self, tmp_config, monkeypatch):
        """备用模型通过 api_key_env 引用环境变量。"""
        cfg = {
            "fallback_models": [
                {"model": "gpt-4o-mini", "base_url": "https://api.openai.com/v1", "api_key_env": "OPENAI_API_KEY"},
            ]
        }
        with open(tmp_config, "w") as f:
            yaml.dump(cfg, f)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
        store = ConfigStore(tmp_config)
        backends = store.read_model_backends()
        assert len(backends) == 2
        assert backends[1]["api_key"] == "sk-openai-test"

    def test_read_fallback_backends_inline(self, tmp_config):
        """备用模型 api_key 直接填值。"""
        cfg = {
            "fallback_models": [
                {"model": "gpt-4o-mini", "base_url": "https://api.openai.com/v1", "api_key": "sk-inline"},
            ]
        }
        with open(tmp_config, "w") as f:
            yaml.dump(cfg, f)
        store = ConfigStore(tmp_config)
        backends = store.read_model_backends()
        assert len(backends) == 2
        assert backends[1]["api_key"] == "sk-inline"

    def test_read_fallback_backends_inline_overrides_env(self, tmp_config, monkeypatch):
        """api_key 直接值优先于 api_key_env。"""
        cfg = {
            "fallback_models": [
                {"model": "gpt-4o-mini", "api_key": "sk-inline", "api_key_env": "SHOULD_NOT_READ"},
            ]
        }
        with open(tmp_config, "w") as f:
            yaml.dump(cfg, f)
        monkeypatch.setenv("SHOULD_NOT_READ", "sk-from-env")
        store = ConfigStore(tmp_config)
        backends = store.read_model_backends()
        assert backends[1]["api_key"] == "sk-inline"

    def test_read_fallback_skipped_no_key(self, tmp_config, monkeypatch):
        """api_key_env 指向的环境变量不存在时跳过该备用模型。"""
        cfg = {
            "fallback_models": [
                {"model": "gpt-4o-mini", "api_key_env": "MISSING_VAR"},
            ]
        }
        with open(tmp_config, "w") as f:
            yaml.dump(cfg, f)
        monkeypatch.delenv("MISSING_VAR", raising=False)
        store = ConfigStore(tmp_config)
        backends = store.read_model_backends()
        assert len(backends) == 1  # 只有主用


class TestConfigCli:
    """测试 handle_config 函数的输出行为。"""

    def test_list_empty(self, tmp_config, capsys, monkeypatch):
        from config.cli import handle_config
        monkeypatch.setattr("config.cli.ConfigStore", lambda: ConfigStore(tmp_config))
        args = _fake_args("list")
        handle_config(args)
        captured = capsys.readouterr()
        assert captured.out.strip() == "(空)"

    def test_list_with_items(self, tmp_config, capsys, monkeypatch):
        from config.cli import handle_config
        store = ConfigStore(tmp_config)
        store.set("model", "deepseek-chat")
        store.set("base_url", "https://api.deepseek.com")
        monkeypatch.setattr("config.cli.ConfigStore", lambda: ConfigStore(tmp_config))
        args = _fake_args("list")
        handle_config(args)
        captured = capsys.readouterr()
        assert "model: deepseek-chat" in captured.out
        assert "base_url: https://api.deepseek.com" in captured.out

    def test_get_existing(self, tmp_config, capsys, monkeypatch):
        from config.cli import handle_config
        ConfigStore(tmp_config).set("model", "test")
        monkeypatch.setattr("config.cli.ConfigStore", lambda: ConfigStore(tmp_config))
        args = _fake_args("get", key="model")
        handle_config(args)
        assert capsys.readouterr().out.strip() == "test"

    def test_get_nonexistent(self, tmp_config, monkeypatch):
        from config.cli import handle_config
        monkeypatch.setattr("config.cli.ConfigStore", lambda: ConfigStore(tmp_config))
        args = _fake_args("get", key="nonexistent")
        with pytest.raises(SystemExit):
            handle_config(args)

    def test_set(self, tmp_config, capsys, monkeypatch):
        from config.cli import handle_config
        monkeypatch.setattr("config.cli.ConfigStore", lambda: ConfigStore(tmp_config))
        args = _fake_args("set", key="model", value="gpt-4")
        handle_config(args)
        assert capsys.readouterr().out.strip() == "已设置 model = gpt-4"
        assert ConfigStore(tmp_config).get("model") == "gpt-4"


def _fake_args(action: str, **kwargs):
    """构造一个类似 argparse.Namespace 的对象。"""
    from types import SimpleNamespace
    base = {"config_action": action}
    base.update(kwargs)
    return SimpleNamespace(**base)
