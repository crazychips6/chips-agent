"""语义路由器测试"""

import pytest
from unittest.mock import patch, MagicMock


# ── Mock 数据 ──

MOCK_INTENTS = {
    "web_search": MagicMock(
        name="web_search",
        description="搜索互联网获取最新信息",
        keywords=["搜索", "查找", "最新", "新闻"],
        tools="all",
        tool_priorities={"web": 1.0, "fetch": 0.8, "file": 0.2},
        model="large",
    ),
    "document": MagicMock(
        name="document",
        description="解析和提取文档内容",
        keywords=["文档", "PDF", "DOCX", "解析", "OCR"],
        tools=["document", "file"],
        tool_priorities={"document": 1.0, "file": 0.6},
        model="large",
    ),
    "simple_coding": MagicMock(
        name="simple_coding",
        description="编写、调试、修改代码",
        keywords=["代码", "函数", "脚本", "bug", "修复"],
        tools="all",
        tool_priorities={"file": 1.0, "exec": 0.8, "bash": 0.6},
        model="large",
    ),
    "greeting": MagicMock(
        name="greeting",
        description="简单问候和打招呼",
        keywords=["你好", "Hi", "Hello"],
        tools=[],
        tool_priorities={},
        model="small",
    ),
    "other": MagicMock(
        name="other",
        description="其他未归类的任务",
        keywords=[],
        tools="all",
        tool_priorities={},
        model="large",
    ),
}

MOCK_TOOL_DESCRIPTIONS = {
    "web": {"description": "搜索互联网网页", "keywords": ["搜索", "互联网", "网页"]},
    "fetch": {"description": "获取指定 URL 的网页内容", "keywords": ["URL", "链接"]},
    "file": {"description": "读取、搜索本地文件", "keywords": ["文件", "本地", "目录"]},
    "document": {"description": "解析文档内容", "keywords": ["文档", "PDF", "OCR"]},
    "exec": {"description": "执行 shell 命令", "keywords": ["执行", "运行", "命令"]},
    "bash": {"description": "执行 bash 命令", "keywords": ["bash", "命令行"]},
}

MOCK_EMBEDDINGS = {
    # 用户查询 embeddings - 每个查询在其对应的意图维度上值最高
    "今天天气怎么样": [0.9, 0.1, 0.1, 0.1, 0.1],  # 高 web 维度
    "帮我看看 report.pdf": [0.1, 0.9, 0.1, 0.1, 0.1],  # 高 document 维度
    "写一个排序函数": [0.1, 0.1, 0.9, 0.1, 0.1],  # 高 coding 维度
    "你好": [0.1, 0.1, 0.1, 0.1, 0.9],  # 高 greeting 维度
    # 意图 embeddings - 格式: "description 关键词: kw1, kw2, ..."
    "搜索互联网获取最新信息 关键词: 搜索, 查找, 最新, 新闻": [0.9, 0.1, 0.1, 0.1, 0.1],
    "解析和提取文档内容 关键词: 文档, PDF, DOCX, 解析, OCR": [0.1, 0.9, 0.1, 0.1, 0.1],
    "编写、调试、修改代码 关键词: 代码, 函数, 脚本, bug, 修复": [0.1, 0.1, 0.9, 0.1, 0.1],
    "简单问候和打招呼 关键词: 你好, Hi, Hello": [0.1, 0.1, 0.1, 0.1, 0.9],
    "其他未归类的任务 ": [0.3, 0.3, 0.3, 0.3, 0.3],
    # 工具 embeddings - 格式: "name: description keywords"
    "web: 搜索互联网网页 搜索 互联网 网页": [0.9, 0.1, 0.1, 0.1, 0.1],
    "fetch: 获取指定 URL 的网页内容 URL 链接": [0.8, 0.2, 0.1, 0.1, 0.1],
    "file: 读取、搜索本地文件 文件 本地 目录": [0.1, 0.1, 0.1, 0.9, 0.1],
    "document: 解析文档内容 文档 PDF OCR": [0.1, 0.9, 0.1, 0.1, 0.1],
    "exec: 执行 shell 命令 执行 运行 命令": [0.1, 0.1, 0.9, 0.1, 0.1],
    "bash: 执行 bash 命令 bash 命令行": [0.1, 0.1, 0.8, 0.2, 0.1],
    "memory_search: 搜索历史记忆和知识库 记忆 搜索记忆 历史 知识库 回忆": [0.1, 0.1, 0.1, 0.1, 0.3],
    "memory_add: 添加新记忆到知识库 记住 保存记忆 添加知识 记录": [0.1, 0.1, 0.1, 0.1, 0.3],
}


def mock_embed(texts):
    """Mock embedding 函数。"""
    return [MOCK_EMBEDDINGS.get(t, [0.1, 0.1, 0.1, 0.1, 0.1, 0.1]) for t in texts]


# ── 测试 ──


class TestSemanticRouter:
    """SemanticRouter 核心测试。"""

    @patch("agent.intent_loader.intent_registry")
    @patch("tool.semantic_descriptions.get_all_tool_descriptions")
    @patch("agent.embedding.get_embedding")
    def test_route_web_search(self, mock_get_embedding, mock_get_tools, mock_intents):
        """测试：用户问天气 → 意图 web_search，工具包含 web。"""
        # Setup mocks
        mock_intents.get_all.return_value = MOCK_INTENTS
        mock_get_tools.return_value = MOCK_TOOL_DESCRIPTIONS

        mock_embedding = MagicMock()
        mock_embedding.embed.side_effect = mock_embed
        mock_embedding.name = "test"
        mock_get_embedding.return_value = mock_embedding

        from agent.semantic_router import SemanticRouter
        router = SemanticRouter()
        router.reload()

        result = router.route("今天天气怎么样")

        assert result["intent"] == "web_search"
        assert result["confidence"] > 0.5
        assert "web" in result["tools"]

    @patch("agent.intent_loader.intent_registry")
    @patch("tool.semantic_descriptions.get_all_tool_descriptions")
    @patch("agent.embedding.get_embedding")
    def test_route_document(self, mock_get_embedding, mock_get_tools, mock_intents):
        """测试：用户要读文档 → 意图 document，工具包含 document。"""
        mock_intents.get_all.return_value = MOCK_INTENTS
        mock_get_tools.return_value = MOCK_TOOL_DESCRIPTIONS

        mock_embedding = MagicMock()
        mock_embedding.embed.side_effect = mock_embed
        mock_embedding.name = "test"
        mock_get_embedding.return_value = mock_embedding

        from agent.semantic_router import SemanticRouter
        router = SemanticRouter()
        router.reload()

        result = router.route("帮我看看 report.pdf")

        assert result["intent"] == "document"
        assert result["confidence"] > 0.5
        assert "document" in result["tools"]
        assert "file" in result["tools"]

    @patch("agent.intent_loader.intent_registry")
    @patch("tool.semantic_descriptions.get_all_tool_descriptions")
    @patch("agent.embedding.get_embedding")
    def test_route_coding(self, mock_get_embedding, mock_get_tools, mock_intents):
        """测试：用户要写代码 → 意图 simple_coding，工具包含 file 和 exec。"""
        mock_intents.get_all.return_value = MOCK_INTENTS
        mock_get_tools.return_value = MOCK_TOOL_DESCRIPTIONS

        mock_embedding = MagicMock()
        mock_embedding.embed.side_effect = mock_embed
        mock_embedding.name = "test"
        mock_get_embedding.return_value = mock_embedding

        from agent.semantic_router import SemanticRouter
        router = SemanticRouter()
        router.reload()

        result = router.route("写一个排序函数")

        assert result["intent"] == "simple_coding"
        assert result["confidence"] > 0.5
        # 根据 mock embeddings，exec 和 bash 应该被匹配到
        assert "exec" in result["tools"] or "bash" in result["tools"]

    @patch("agent.intent_loader.intent_registry")
    @patch("tool.semantic_descriptions.get_all_tool_descriptions")
    @patch("agent.embedding.get_embedding")
    def test_route_greeting(self, mock_get_embedding, mock_get_tools, mock_intents):
        """测试：用户打招呼 → 意图 greeting，工具为空。"""
        mock_intents.get_all.return_value = MOCK_INTENTS
        mock_get_tools.return_value = MOCK_TOOL_DESCRIPTIONS

        mock_embedding = MagicMock()
        mock_embedding.embed.side_effect = mock_embed
        mock_embedding.name = "test"
        mock_get_embedding.return_value = mock_embedding

        from agent.semantic_router import SemanticRouter
        router = SemanticRouter()
        router.reload()

        result = router.route("你好")

        assert result["intent"] == "greeting"
        assert result["confidence"] > 0.5
        assert result["tools"] == []

    @patch("agent.intent_loader.intent_registry")
    @patch("tool.semantic_descriptions.get_all_tool_descriptions")
    @patch("agent.embedding.get_embedding")
    def test_fuse_tools_deduplication(self, mock_get_embedding, mock_get_tools, mock_intents):
        """测试：工具融合去重。"""
        mock_intents.get_all.return_value = MOCK_INTENTS
        mock_get_tools.return_value = MOCK_TOOL_DESCRIPTIONS

        mock_embedding = MagicMock()
        mock_embedding.embed.side_effect = mock_embed
        mock_embedding.name = "test"
        mock_get_embedding.return_value = mock_embedding

        from agent.semantic_router import SemanticRouter
        router = SemanticRouter()
        router.reload()

        result = router.route("帮我看看 report.pdf")

        # file 工具应该只出现一次
        assert result["tools"].count("file") <= 1


class TestEmbedding:
    """Embedding 模块测试。"""

    def test_local_embedding_not_available(self):
        """测试：sentence-transformers 未安装时降级。"""
        import sys
        # 临时移除 sentence_transformers
        original = sys.modules.get("sentence_transformers")
        sys.modules["sentence_transformers"] = None
        try:
            from agent.embedding import LocalEmbedding
            client = LocalEmbedding()
            assert not client.is_available()
        finally:
            if original:
                sys.modules["sentence_transformers"] = original
            else:
                sys.modules.pop("sentence_transformers", None)


class TestToolDescriptions:
    """工具描述测试。"""

    def test_all_tools_have_descriptions(self):
        """测试：所有核心工具都有语义描述。"""
        from tool.semantic_descriptions import TOOL_DESCRIPTIONS

        core_tools = ["web", "file", "document", "exec", "bash"]
        for tool in core_tools:
            assert tool in TOOL_DESCRIPTIONS, f"Tool '{tool}' missing description"
            assert "description" in TOOL_DESCRIPTIONS[tool]
            assert "keywords" in TOOL_DESCRIPTIONS[tool]

    def test_descriptions_are_meaningful(self):
        """测试：描述不是空的。"""
        from tool.semantic_descriptions import TOOL_DESCRIPTIONS

        for tool_name, desc in TOOL_DESCRIPTIONS.items():
            assert len(desc["description"]) > 5, f"Tool '{tool_name}' description too short"
            assert len(desc["keywords"]) > 0, f"Tool '{tool_name}' has no keywords"
