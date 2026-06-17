"""Test QuickApp 系统

Tests:
  - 数据模型
  - Schema 构建
  - Known-good 索引关键词匹配
  - 代码生成（pattern 填空、skeleton）
  - Verifier（语法检查、错误检测）
  - QuickAppManager（创建、列表、删除、run 的 mock 环境）
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

from quick_app.models import (
    QUICK_APP_TEMPLATE,
    Draft,
    QuickApp,
    SourceType,
    VenvLevel,
    VerificationResult,
    build_schema,
    _to_json_type,
)
from quick_app.known_good import KNOWN_GOOD_INDEX, CLI_TOOLS, PIP_PACKAGES
from quick_app.codegen import _find_matching_entry, _render_from_pattern, _render_skeleton
from quick_app.verifier import verify_quick_app, _run_checks


# ── 模型 ──


class TestModels:
    def test_source_type_values(self):
        assert SourceType.CLI_TOOL.value == "cli_tool"
        assert SourceType.PIP_PACKAGE.value == "pip_package"
        assert SourceType.GITHUB.value == "github"
        assert SourceType.WRITE_CODE.value == "write_code"

    def test_venv_level_values(self):
        assert VenvLevel.NONE.value == 0
        assert VenvLevel.SHARED.value == 1
        assert VenvLevel.ISOLATED.value == 2

    def test_quick_app_template_format(self):
        code = QUICK_APP_TEMPLATE.format(
            name="test_tool",
            description="A test tool",
            handler_body="    return 'hello'",
        )
        assert "test_tool" in code
        assert "A test tool" in code
        assert "return 'hello'" in code
        assert "if __name__" in code
        assert "json.loads(sys.argv[1])" in code


# ── Schema 构建 ──


class TestBuildSchema:
    def test_basic_schema(self):
        params = [
            {"name": "input", "type": "string", "description": "Input file"},
            {"name": "count", "type": "integer", "description": "Count"},
        ]
        schema = build_schema("test_tool", "A test tool", params)
        func = schema["function"]
        assert func["name"] == "test_tool"
        assert func["description"] == "A test tool"
        props = func["parameters"]["properties"]
        assert props["input"]["type"] == "string"
        assert props["count"]["type"] == "integer"
        assert func["parameters"]["required"] == ["input", "count"]

    def test_optional_params(self):
        params = [
            {"name": "required_field", "type": "string", "required": True},
            {"name": "optional_field", "type": "string", "required": False},
        ]
        schema = build_schema("test", "desc", params)
        assert schema["function"]["parameters"]["required"] == ["required_field"]

    def test_empty_params(self):
        schema = build_schema("no_params", "No params", [])
        assert schema["function"]["parameters"]["required"] == []

    def test_to_json_type(self):
        assert _to_json_type("integer") == "integer"
        assert _to_json_type("int") == "integer"
        assert _to_json_type("boolean") == "boolean"
        assert _to_json_type("bool") == "boolean"
        assert _to_json_type("float") == "number"
        assert _to_json_type("unknown") == "string"


# ── Known-Good 索引 ──


class TestKnownGoodIndex:
    def test_index_not_empty(self):
        assert len(KNOWN_GOOD_INDEX) > 0

    def test_cli_tools_populated(self):
        assert len(CLI_TOOLS) >= 3
        names = [e.name for e in CLI_TOOLS]
        assert "ffmpeg" in names

    def test_pip_packages_populated(self):
        assert len(PIP_PACKAGES) >= 3
        names = [e.name for e in PIP_PACKAGES]
        assert "requests" in names

    def test_each_entry_has_keywords(self):
        for e in KNOWN_GOOD_INDEX:
            assert len(e.keywords) > 0, f"{e.name} has no keywords"
            assert e.description, f"{e.name} has no description"
            assert e.source_type in (SourceType.CLI_TOOL, SourceType.PIP_PACKAGE)

    def test_ffmpeg_params(self):
        ffmpeg = [e for e in CLI_TOOLS if e.name == "ffmpeg"][0]
        assert len(ffmpeg.params_template) >= 2
        assert ffmpeg.handler_pattern
        assert "subprocess.run" in ffmpeg.handler_pattern

    def test_tesseract_weak_tags(self):
        tesseract = [e for e in CLI_TOOLS if e.name == "tesseract"][0]
        assert "表格提取" in tesseract.weak_scene_tags


# ── 代码生成 ──


class TestCodegen:
    def test_find_matching_entry_ffmpeg(self):
        draft = Draft(
            name="video_to_gif",
            description="将视频转为 GIF",
            source_type=SourceType.CLI_TOOL,
            source_name="ffmpeg",
            params=[],
        )
        entry = _find_matching_entry(draft)
        assert entry is not None
        assert entry.name == "ffmpeg"

    def test_find_matching_entry_no_match(self):
        draft = Draft(
            name="unknown_tool",
            description="some completely unknown thing",
            source_type=SourceType.WRITE_CODE,
            source_name="python",
            params=[],
        )
        entry = _find_matching_entry(draft)
        assert entry is None

    def test_render_from_pattern(self):
        ffmpeg = [e for e in CLI_TOOLS if e.name == "ffmpeg"][0]
        draft = Draft(
            name="video_to_gif",
            description="Convert video to GIF",
            source_type=SourceType.CLI_TOOL,
            source_name="ffmpeg",
            params=[
                {"name": "input", "type": "string", "description": "Input"},
            ],
        )
        # Does it produce valid template output?
        code = _render_from_pattern(draft, ffmpeg)
        assert "video_to_gif" in code
        assert "def handler" in code
        assert "subprocess" in code or "return" in code
        # handler body should be indented at 4 spaces (same as docstring)
        assert "    subprocess" in code or "    return" in code

    def test_render_skeleton(self):
        draft = Draft(
            name="test_skel",
            description="A test skeleton",
            source_type=SourceType.WRITE_CODE,
            source_name="python",
            params=[{"name": "x", "type": "string"}],
        )
        code = _render_skeleton(draft)
        assert "test_skel" in code
        assert "# TODO" in code
        assert 'def handler(args: dict) -> str:' in code


# ── Verifier ──


class TestVerifier:
    def test_run_checks_passes_with_valid_output(self):
        result = _run_checks(0, "Hello World", "")
        assert result["passed"] is True

    def test_run_checks_fails_on_nonzero_returncode(self):
        result = _run_checks(1, "some output", "")
        assert result["passed"] is False
        assert "非零" in result["reason"]

    def test_run_checks_fails_on_empty_output(self):
        result = _run_checks(0, "", "")
        assert result["passed"] is False
        assert "空" in result["reason"]

    def test_run_checks_fails_on_error_keyword(self):
        result = _run_checks(0, "Error: something broke", "")
        assert result["passed"] is False

    def test_run_checks_fails_on_too_short(self):
        result = _run_checks(0, "ab", "")
        assert result["passed"] is False
        assert "过短" in result["reason"]

    def test_verify_quick_app_syntax(self, tmp_path):
        """Verify that a valid QuickApp file passes syntax check."""
        app_file = tmp_path / "app.py"
        code = QUICK_APP_TEMPLATE.format(
            name="test_app",
            description="Test",
            handler_body='    return "hello world"',
        )
        app_file.write_text(code, encoding="utf-8")

        result = verify_quick_app(app_file, sys.executable, {})
        # With {} args, json.loads(sys.argv[1]) should work, and handler returns "hello world"
        assert result.passed

    def test_verify_quick_app_syntax_error(self, tmp_path):
        """Verify that a QuickApp with syntax error fails."""
        app_file = tmp_path / "app.py"
        app_file.write_text("this is not valid python {{(", encoding="utf-8")

        result = verify_quick_app(app_file, sys.executable, {"test": 1})
        assert not result.passed


# ── QuickAppManager ──


class TestQuickAppManager:
    def test_dirs(self):
        from quick_app.manager import QUICK_APPS_DIR
        assert str(QUICK_APPS_DIR).endswith(".chips/quick_apps")

    def test_infer_venv_level(self):
        from quick_app.manager import QuickAppManager
        assert QuickAppManager.infer_venv_level(SourceType.CLI_TOOL) == VenvLevel.NONE
        assert QuickAppManager.infer_venv_level(SourceType.PIP_PACKAGE) == VenvLevel.SHARED
        assert QuickAppManager.infer_venv_level(SourceType.GITHUB) == VenvLevel.ISOLATED
        assert QuickAppManager.infer_venv_level(SourceType.WRITE_CODE) == VenvLevel.SHARED

    def test_ensure_dirs(self):
        from quick_app.manager import QuickAppManager, QUICK_APPS_DIR
        mgr = QuickAppManager()
        mgr.ensure_dirs()
        assert QUICK_APPS_DIR.exists()

    def test_create_and_list(self):
        """Test create and list in a real .chips/quick_apps/ directory."""
        from quick_app.manager import QuickAppManager
        from tool.registry import registry

        mgr = QuickAppManager(registry=registry)
        mgr.ensure_dirs()

        code = QUICK_APP_TEMPLATE.format(
            name="test_create",
            description="Test create",
            handler_body='    return "create ok"',
        )
        schema = build_schema("test_create", "Test create", [])

        mock_entry = mgr.create(
            name="test_create",
            code=code,
            description="Test create",
            schema=schema,
            source_type=SourceType.WRITE_CODE,
            venv_level=VenvLevel.NONE,
            sample_args={"_": "test"},  # triggers verification
        )

        assert mock_entry.name == "test_create"
        assert mock_entry.venv_level == VenvLevel.NONE

        # list should include it
        names = [qa.name for qa in mgr.list()]
        assert "test_create" in names

        # cleanup
        mgr.delete("test_create")
        assert "test_create" not in [qa.name for qa in mgr.list()]

    def test_delete_nonexistent(self):
        from quick_app.manager import QuickAppManager
        mgr = QuickAppManager()
        assert not mgr.delete("nonexistent_tool")

    def test_run(self):
        """Test that run() can execute a QuickApp."""
        from quick_app.manager import QuickAppManager
        from tool.registry import registry

        mgr = QuickAppManager(registry=registry)
        mgr.ensure_dirs()

        code = QUICK_APP_TEMPLATE.format(
            name="test_run",
            description="Test run",
            handler_body=(
                '    name = args.get("name", "world")\n'
                '    return f"Hello, {name}!"'
            ),
        )
        schema = build_schema("test_run", "Test run", [{"name": "name", "type": "string"}])

        mgr.create(
            name="test_run",
            code=code,
            description="Test run",
            schema=schema,
            source_type=SourceType.WRITE_CODE,
            venv_level=VenvLevel.NONE,
        )

        result = mgr.run("test_run", {"name": "QuickApp"})
        assert "Hello, QuickApp!" in result

        mgr.delete("test_run")

    def test_duplicate_name_raises(self):
        from quick_app.manager import QuickAppManager
        from tool.registry import registry

        mgr = QuickAppManager(registry=registry)
        mgr.ensure_dirs()

        code = QUICK_APP_TEMPLATE.format(
            name="test_dup",
            description="Dup",
            handler_body='    return "ok"',
        )
        schema = build_schema("test_dup", "Dup", [])

        mgr.create(
            name="test_dup", code=code, description="Dup",
            schema=schema, source_type=SourceType.WRITE_CODE,
            venv_level=VenvLevel.NONE,
        )

        with pytest.raises(RuntimeError, match="已存在"):
            mgr.create(
                name="test_dup", code=code, description="Dup",
                schema=schema, source_type=SourceType.WRITE_CODE,
                venv_level=VenvLevel.NONE,
            )

        mgr.delete("test_dup")


# ── 工具 Handler ──


class TestToolHandlers:
    def test_handle_draft_matches_ffmpeg(self):
        from tool.builtins.quick_app_tools import _handle_draft
        result = _handle_draft({"description": "帮我做一个视频转 GIF 的工具"})
        data = json.loads(result)
        assert data["source_type"] == "cli_tool"
        assert "ffmpeg" in data["source_name"] or "ffmpeg" in data.get("source_reason", "")

    def test_handle_draft_no_match(self):
        from tool.builtins.quick_app_tools import _handle_draft
        result = _handle_draft({"description": "xylophone_melody_generator"})
        data = json.loads(result)
        assert data["source_type"] == "write_code"

    def test_handle_list_empty(self):
        from tool.builtins.quick_app_tools import _handle_list
        result = _handle_list({})
        # Should return message "当前没有" or similar
        assert isinstance(result, str)
        assert len(result) > 0

    def test_handle_delete_nonexistent(self):
        from tool.builtins.quick_app_tools import _handle_delete
        result = _handle_delete({"name": "does_not_exist"})
        data = json.loads(result)
        assert "error" in data

    def test_build_sample_args(self):
        from tool.builtins.quick_app_tools import (
            _build_sample_args,
        )
        from quick_app.models import Draft

        draft = Draft(
            name="t", description="t",
            source_type=SourceType.CLI_TOOL, source_name="ffmpeg",
            params=[
                {"name": "input", "type": "string"},
                {"name": "count", "type": "integer"},
                {"name": "ratio", "type": "float"},
                {"name": "flag", "type": "boolean"},
            ],
        )
        samples = _build_sample_args(draft)
        assert samples["input"] == "test"
        assert samples["count"] == 1
        assert samples["ratio"] == 1.0
        assert samples["flag"] is True

    def test_check_source_validity_blocks_write_code(self):
        """WRITE_CODE with matching known-good entry should be rejected."""
        from tool.builtins.quick_app_tools import _check_source_validity
        from quick_app.models import Draft

        draft = Draft(
            name="my_converter",
            description="视频转 GIF 工具",
            source_type=SourceType.WRITE_CODE,
            source_name="python",
            params=[],
        )
        error = _check_source_validity(draft)
        assert error is not None  # ffmpeg should match
        assert "ffmpeg" in error

    def test_check_source_validity_allows_genuine(self):
        """Actual write_code with no matching entry should pass."""
        from tool.builtins.quick_app_tools import _check_source_validity
        from quick_app.models import Draft

        draft = Draft(
            name="my_tool",
            description="a completely novel thing no one has built",
            source_type=SourceType.WRITE_CODE,
            source_name="python",
            params=[],
        )
        error = _check_source_validity(draft)
        assert error is None
