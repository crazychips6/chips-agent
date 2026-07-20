"""知识闭环测试"""

import json
import tempfile
from pathlib import Path
import pytest


class TestExperience:
    """Experience 数据结构测试。"""

    def test_experience_creation(self):
        """测试：创建 Experience 对象。"""
        from knowledge.models import Experience, ToolStep

        exp = Experience(
            id="test-123",
            task="天气查询",
            triggers=["天气", "温度"],
            intent="web_search",
            pattern="用户问天气相关问题",
            steps=[
                ToolStep(tool="web", args_pattern="query=天气", result_pattern="天气信息"),
            ],
            summary="直接用 web 工具搜索天气",
            anti_patterns=["不要用 file 工具"],
            pitfalls=["注意时区"],
            confidence="candidate",
        )

        assert exp.id == "test-123"
        assert exp.task == "天气查询"
        assert exp.confidence_level == 1
        assert exp.icon == "💡"

    def test_experience_to_dict(self):
        """测试：Experience 序列化。"""
        from knowledge.models import Experience, ToolStep

        exp = Experience(
            id="test-123",
            task="天气查询",
            steps=[ToolStep(tool="web")],
        )

        d = exp.to_dict()
        assert d["id"] == "test-123"
        assert d["task"] == "天气查询"
        assert len(d["steps"]) == 1

    def test_experience_from_dict(self):
        """测试：Experience 反序列化。"""
        from knowledge.models import Experience

        data = {
            "id": "test-123",
            "task": "天气查询",
            "triggers": ["天气"],
            "intent": "web_search",
            "summary": "直接搜索",
            "steps": [{"tool": "web", "args_pattern": "", "result_pattern": "", "is_optional": False}],
            "confidence": "candidate",
        }

        exp = Experience.from_dict(data)
        assert exp.id == "test-123"
        assert exp.task == "天气查询"
        assert len(exp.steps) == 1

    def test_experience_format_for_prompt(self):
        """测试：Experience 格式化为 prompt。"""
        from knowledge.models import Experience

        exp = Experience(
            id="test-123",
            task="天气查询",
            summary="直接用 web 工具搜索",
            anti_patterns=["不要用 file 工具"],
            confidence="recommended",
        )

        text = exp.format_for_prompt()
        assert "天气查询" in text
        assert "直接用 web 工具搜索" in text
        assert "不要用 file 工具" in text
        assert "📌" in text

    def test_experience_stats_update(self):
        """测试：Experience 统计更新。"""
        from knowledge.models import Experience

        exp = Experience(id="test-123", task="天气查询")

        exp.update_stats(success=True)
        assert exp.success_count == 1
        assert exp.fail_count == 0

        exp.update_stats(success=False)
        assert exp.success_count == 1
        assert exp.fail_count == 1
        assert exp.success_rate == 0.5

    def test_experience_auto_upgrade(self):
        """测试：Experience 自动升级置信度。"""
        from knowledge.models import Experience

        exp = Experience(id="test-123", task="天气查询", confidence="observed")

        # 5 次成功，应该升级
        for _ in range(5):
            exp.update_stats(success=True)
        exp.auto_upgrade_confidence(min_samples=5)

        assert exp.confidence == "candidate"

    def test_experience_auto_downgrade(self):
        """测试：Experience 自动降级置信度。"""
        from knowledge.models import Experience

        exp = Experience(id="test-123", task="天气查询", confidence="recommended")

        # 5 次失败，应该降级
        for _ in range(5):
            exp.update_stats(success=False)
        exp.auto_upgrade_confidence(min_samples=5)

        assert exp.confidence == "candidate"


class TestKnowledgeManager:
    """KnowledgeManager 测试。"""

    def test_match_by_intent(self):
        """测试：按意图匹配知识。"""
        from knowledge.manager import KnowledgeManager

        with tempfile.TemporaryDirectory() as tmpdir:
            km = KnowledgeManager(knowledge_dir=tmpdir)
            km._entries = [
                {
                    "id": "1",
                    "task": "天气查询",
                    "intent": "web_search",
                    "triggers": ["天气"],
                    "confidence": "recommended",
                },
                {
                    "id": "2",
                    "task": "文档解析",
                    "intent": "document",
                    "triggers": ["文档"],
                    "confidence": "candidate",
                },
            ]

            # 按意图匹配
            results = km.match("今天天气怎么样", intent="web_search")
            assert len(results) == 1
            assert results[0]["task"] == "天气查询"

            # 按关键词匹配
            results = km.match("帮我看看文档", intent="web_search")
            # 应该匹配到文档解析（关键词"文档"命中）
            assert any(r["task"] == "文档解析" for r in results)

    def test_match_scoring(self):
        """测试：匹配评分逻辑。"""
        from knowledge.manager import KnowledgeManager

        with tempfile.TemporaryDirectory() as tmpdir:
            km = KnowledgeManager(knowledge_dir=tmpdir)
            km._entries = [
                {
                    "id": "1",
                    "task": "天气查询",
                    "intent": "web_search",
                    "triggers": ["天气", "温度"],
                    "pattern": "用户问天气",
                    "confidence": "recommended",
                },
            ]

            # 意图 + 关键词 + 模式全命中
            results = km.match("今天天气怎么样", intent="web_search")
            assert len(results) == 1

            # 只有关键词命中
            results = km.match("今天温度怎么样", intent="other")
            assert len(results) == 1

    def test_format_knowledge_new_format(self):
        """测试：新格式知识格式化。"""
        from knowledge.manager import KnowledgeManager

        with tempfile.TemporaryDirectory() as tmpdir:
            km = KnowledgeManager(knowledge_dir=tmpdir)
            entries = [
                {
                    "task": "天气查询",
                    "summary": "直接用 web 工具搜索",
                    "anti_patterns": ["不要用 file 工具"],
                    "steps": [{"tool": "web"}],
                    "confidence": "recommended",
                },
            ]

            text = km.format_knowledge(entries)
            assert "天气查询" in text
            assert "直接用 web 工具搜索" in text
            assert "不要用 file 工具" in text
            assert "web" in text

    def test_save_to_staging_new_format(self):
        """测试：新格式保存到 staging。"""
        from knowledge.manager import KnowledgeManager

        with tempfile.TemporaryDirectory() as tmpdir:
            km = KnowledgeManager(knowledge_dir=tmpdir)

            analysis = {
                "task": "天气查询",
                "triggers": ["天气"],
                "intent": "web_search",
                "pattern": "用户问天气",
                "steps": [{"tool": "web", "args_pattern": "", "result_pattern": "", "is_optional": False}],
                "summary": "直接用 web 工具搜索",
                "anti_patterns": ["不要用 file 工具"],
                "pitfalls": ["注意时区"],
                "quality_score": 0.9,
            }

            staging_id = km.save_to_staging(analysis)
            assert staging_id is not None

            # 验证 staging 文件内容
            staging_dir = Path(tmpdir) / "staging"
            assert staging_dir.exists()
            staging_files = list(staging_dir.glob("*.json"))
            assert len(staging_files) == 1

            with open(staging_files[0]) as f:
                saved = json.load(f)
            assert saved["task"] == "天气查询"
            assert saved["summary"] == "直接用 web 工具搜索"
            assert saved["quality_score"] == 0.9

    def test_update_stats_and_auto_upgrade(self):
        """测试：更新统计并自动升级。"""
        from knowledge.manager import KnowledgeManager

        with tempfile.TemporaryDirectory() as tmpdir:
            km = KnowledgeManager(knowledge_dir=tmpdir)
            km._entries = [
                {
                    "id": "test-123",
                    "task": "天气查询",
                    "confidence": "observed",
                    "stats": {"success_count": 0, "fail_count": 0},
                },
            ]

            # 5 次成功
            for _ in range(5):
                km.update_stats("test-123", success=True)

            assert km._entries[0]["confidence"] == "candidate"
            assert km._entries[0]["stats"]["success_count"] == 5

    def test_get_by_intent(self):
        """测试：按意图获取条目。"""
        from knowledge.manager import KnowledgeManager

        with tempfile.TemporaryDirectory() as tmpdir:
            km = KnowledgeManager(knowledge_dir=tmpdir)
            km._entries = [
                {"id": "1", "task": "天气查询", "intent": "web_search"},
                {"id": "2", "task": "文档解析", "intent": "document"},
                {"id": "3", "task": "新闻搜索", "intent": "web_search"},
            ]

            results = km.get_by_intent("web_search")
            assert len(results) == 2
            assert all(r["intent"] == "web_search" for r in results)
