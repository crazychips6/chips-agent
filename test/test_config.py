"""ConfigStore 单元测试"""

import os
from pathlib import Path

import pytest
import yaml

from config.store import ConfigStore, KNOWN_KEYS


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

    def test_apply_to_env(self, tmp_config, monkeypatch):
        store = ConfigStore(tmp_config)
        store.set("model", "test-model")
        monkeypatch.delenv("CHIPS_MODEL", raising=False)
        store.apply_to_env()
        assert os.environ["CHIPS_MODEL"] == "test-model"

    def test_apply_to_env_does_not_override(self, tmp_config, monkeypatch):
        store = ConfigStore(tmp_config)
        store.set("model", "test-model")
        monkeypatch.setenv("CHIPS_MODEL", "existing")
        store.apply_to_env()
        assert os.environ["CHIPS_MODEL"] == "existing"

    def test_known_keys_structure(self):
        assert "model" in KNOWN_KEYS
        assert "base_url" in KNOWN_KEYS
        assert KNOWN_KEYS["model"] == "CHIPS_MODEL"
        assert KNOWN_KEYS["base_url"] == "CHIPS_BASE_URL"


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
