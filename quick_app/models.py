"""QuickApp 数据模型"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class SourceType(str, Enum):
    """检索来源类型"""
    CLI_TOOL = "cli_tool"       # known-good CLI 工具（ffmpeg 等）
    PIP_PACKAGE = "pip_package" # known-good pip 包（requests 等）
    GITHUB = "github"           # GitHub 项目
    WRITE_CODE = "write_code"   # 自己写代码兜底


class VenvLevel(int, Enum):
    """三级 venv 策略"""
    NONE = 0     # 级别 0：无 venv，用系统 Python（纯 CLI 包装）
    SHARED = 1   # 级别 1：共享 .venv_base（常用 pip 包）
    ISOLATED = 2 # 级别 2：独立 .venv（特殊依赖）


class TemplateType(str, Enum):
    """输入/输出模板类型，决定使用时的 UI 控件"""
    STR = "str"            # 纯文本输入框/输出
    LINK = "link"          # URL/链接输入
    FILE_UPLOAD = "file_upload"    # 文件上传
    FILE_SELECT = "file_select"    # 从 chips 指定目录选文件
    FILE_OUTPUT = "file_output"    # 文件路径输出（写入 chips 指定目录）


@dataclass
class KnownGoodEntry:
    """known-good 索引条目"""
    keywords: list[str]                     # 匹配关键词（用户描述命中即推荐）
    source_type: SourceType                 # CLI_TOOL | PIP_PACKAGE
    name: str                               # 项目名
    description: str                        # 一句话描述
    install_hint: str = ""                  # 安装命令提示
    params_template: list[dict] = field(default_factory=list)  # 参数模板（name/type/description）
    fixed_params: list[str] = field(default_factory=list)       # 写死的参数列表（防幻觉），含 {input} 等占位符
    handler_pattern: str = ""               # 代码片段示例
    scene_tags: list[str] = field(default_factory=list)         # 擅长场景
    weak_scene_tags: list[str] = field(default_factory=list)    # 不擅长场景


@dataclass
class ExpandedDescription:
    """LLM 展开后的结构化描述，用于检索和模板渲染"""
    name: str                                # 工具名（英文 snake_case）
    summary: str                             # 一句话概述
    description: str                         # 详细功能描述
    input_spec: list[dict] = field(default_factory=list)  # [{name, type, template, description}]
    output_spec: list[dict] = field(default_factory=list) # [{name, type, template, description}]
    keywords: list[str] = field(default_factory=list)     # 搜索关键词


@dataclass
class Draft:
    """草案卡片——Agent 返回给用户确认的内容"""
    name: str
    description: str
    source_type: SourceType
    source_name: str                        # 具体的项目/包名
    source_reason: str = ""                 # 为什么选这个方案
    params: list[dict] = field(default_factory=list)  # 参数列表
    output_description: str = ""            # 输出描述
    venv_level: VenvLevel = VenvLevel.NONE  # 建议的 venv 级别
    install_hint: str = ""                  # 安装提示（仅对 CLI 工具需要）
    # 模板相关
    expanded: ExpandedDescription | None = None
    input_templates: list[dict] = field(default_factory=list)   # [{param, template_type, label}]
    output_templates: list[dict] = field(default_factory=list)  # [{name, template_type, label}]


@dataclass
class QuickApp:
    """已注册的快应用——Manager 维护的内部模型"""
    name: str
    description: str
    app_dir: Path                           # .chips/quick_apps/{name}/
    schema: dict                            # 注册到 registry 的 schema
    source_type: SourceType
    venv_level: VenvLevel
    created_at: str = ""


@dataclass
class VerificationResult:
    """自我验证结果"""
    passed: bool
    stdout: str = ""
    stderr: str = ""
    error: str = ""                         # 用户可读的错误说明


# ── 模板占位符 ──

QUICK_APP_TEMPLATE = '''"""QuickApp: {name} — {description}"""

import json
import subprocess
import sys


def handler(args: dict) -> str:
    """{description}"""
{handler_body}


if __name__ == "__main__":
    print(handler(json.loads(sys.argv[1])))
'''


# ── Schema 构建 ──


def build_schema(name: str, description: str, params: list[dict]) -> dict:
    """从 draft 参数列表构建 function-calling schema。"""
    properties = {}
    required = []

    for p in params:
        pname = p["name"]
        ptype = p.get("type", "string")
        pdesc = p.get("description", "")

        json_type = _to_json_type(ptype)
        properties[pname] = {"type": json_type, "description": pdesc}

        if p.get("required", True):
            required.append(pname)

    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


def _to_json_type(py_type: str) -> str:
    mapping = {
        "string": "string",
        "integer": "integer",
        "int": "integer",
        "float": "number",
        "number": "number",
        "boolean": "boolean",
        "bool": "boolean",
        "array": "array",
        "object": "object",
    }
    return mapping.get(py_type.lower(), "string")
