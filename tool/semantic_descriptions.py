"""工具语义描述 — 为每个工具提供语义文本，用于 embedding 匹配

语义路由需要知道每个工具"做什么"，而不仅仅是工具名。
这些描述会在启动时被 embedding，构建工具语义索引。

格式：
  tool_name: {
      "description": "工具功能描述",
      "keywords": ["关键词1", "关键词2"],
  }
"""

# ── 工具语义描述 ──

TOOL_DESCRIPTIONS: dict[str, dict] = {
    # ── 搜索类 ──
    "web": {
        "description": "搜索互联网网页，获取最新信息、新闻、文档、代码示例",
        "keywords": ["搜索", "查找", "互联网", "网页", "最新", "新闻", "在线"],
    },
    "fetch": {
        "description": "获取指定 URL 的网页内容",
        "keywords": ["网页", "URL", "链接", "获取", "下载"],
    },

    # ── 文件类 ──
    "file": {
        "description": "读取、搜索、操作本地文件系统中的文件",
        "keywords": ["文件", "读取", "本地", "目录", "路径", "搜索文件", "查找文件"],
    },
    "read": {
        "description": "读取本地文件内容",
        "keywords": ["读取", "打开", "查看", "文件内容"],
    },
    "write": {
        "description": "写入或创建本地文件",
        "keywords": ["写入", "创建", "保存", "新建文件"],
    },
    "edit": {
        "description": "编辑本地文件的指定内容",
        "keywords": ["编辑", "修改", "替换", "更新文件"],
    },

    # ── 文档类 ──
    "document": {
        "description": "解析和提取文档内容，支持 PDF/DOCX/TXT/EML/MSG/OCR",
        "keywords": ["文档", "PDF", "DOCX", "解析", "提取", "OCR", "识别文字", "文档内容"],
    },

    # ── 代码执行类 ──
    "exec": {
        "description": "执行 shell 命令和脚本，运行代码",
        "keywords": ["执行", "运行", "命令", "脚本", "代码", "运行代码"],
    },
    "bash": {
        "description": "执行 bash 命令，系统操作，进程管理",
        "keywords": ["bash", "命令行", "终端", "系统", "进程"],
    },

    # ── 记忆类 ──
    "memory_search": {
        "description": "搜索历史记忆和知识库",
        "keywords": ["记忆", "搜索记忆", "历史", "知识库", "回忆"],
    },
    "memory_add": {
        "description": "添加新记忆到知识库",
        "keywords": ["记住", "保存记忆", "添加知识", "记录"],
    },

    # ── Wiki 类 ──
    "wiki_search_pages": {
        "description": "搜索个人 Wiki 知识库",
        "keywords": ["Wiki", "知识库", "搜索文档", "个人文档"],
    },
    "wiki_read_page": {
        "description": "读取 Wiki 页面内容",
        "keywords": ["Wiki页面", "读取文档", "查看文档"],
    },
    "wiki_ingest": {
        "description": "导入文件到 Wiki 知识库",
        "keywords": ["导入", "添加到Wiki", "知识库导入"],
    },

    # ── Agent 类 ──
    "orchestrate": {
        "description": "多 Agent 编排，支持分解、链式、辩论等协作模式",
        "keywords": ["编排", "多Agent", "协作", "分解任务", "并行"],
    },
    "sub_agent": {
        "description": "创建子 Agent 执行子任务",
        "keywords": ["子Agent", "子任务", "委托", "委派"],
    },
    "delegate_task": {
        "description": "委托任务给专门的 Agent 角色",
        "keywords": ["委托", "委派", "交给", "分配任务"],
    },

    # ── 工具管理类 ──
    "toolset": {
        "description": "管理工具集，查看、启用、禁用工具",
        "keywords": ["工具集", "工具管理", "启用工具", "禁用工具"],
    },
    "tool_request": {
        "description": "请求激活特定工具集",
        "keywords": ["激活工具", "需要工具", "请求工具"],
    },

    # ── 其他 ──
    "clarify": {
        "description": "向用户提问以澄清模糊请求",
        "keywords": ["提问", "澄清", "确认", "询问"],
    },
    "todo": {
        "description": "管理待办事项和任务列表",
        "keywords": ["待办", "任务", "TODO", "计划", "清单"],
    },
}


def get_tool_description(tool_name: str) -> dict | None:
    """获取工具的语义描述。"""
    return TOOL_DESCRIPTIONS.get(tool_name)


def get_all_tool_descriptions() -> dict[str, dict]:
    """获取所有工具的语义描述。"""
    return dict(TOOL_DESCRIPTIONS)
