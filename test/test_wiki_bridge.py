"""Wiki Bridge 测试 — 上下文注入 + 自动捕获"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def wiki_project():
    """创建测试用的 wiki 项目目录（含 index.md 和几个页面）。"""
    tmp = tempfile.mkdtemp()
    for sub in ("concepts", "entities", "sources", "decisions", "lessons", "answers"):
        os.makedirs(os.path.join(tmp, "wiki", sub))

    # index.md
    with open(os.path.join(tmp, "wiki", "index.md"), "w") as f:
        f.write("# Wiki Index\n\n## Concepts\n"
                "- [[concepts/self-attention]] — 一种基于注意力权重的序列建模方法 (2026-07-15)\n"
                "- [[concepts/transformer]] — Transformer 整体架构 (2026-07-15)\n"
                "- [[concepts/positional-encoding]] — 位置编码 (2026-07-15)\n")

    # log.md
    with open(os.path.join(tmp, "wiki", "log.md"), "w") as f:
        f.write("# Operation Log\n\n[2026-07-15 00:00] INIT — Test\n")

    # 一个实际的概念页面
    os.makedirs(os.path.join(tmp, "wiki", "concepts"), exist_ok=True)
    with open(os.path.join(tmp, "wiki", "concepts", "self-attention.md"), "w") as f:
        f.write("# 自注意力机制\n"
                "**类型**: concept\n"
                "**创建**: 2026-07-15\n"
                "**更新**: 2026-07-15\n\n"
                "## 定义\n"
                "一种基于注意力权重的序列建模方法。\n\n"
                "## 详细说明\n"
                "Transformer 的核心组件，用 QKV 矩阵计算注意力分数。\n\n"
                "## 关键要点\n"
                "- 输入序列每个位置与其他位置计算注意力权重\n"
                "- 权重决定了模型关注输入的哪些部分\n")

    with open(os.path.join(tmp, "wiki", "concepts", "transformer.md"), "w") as f:
        f.write("# Transformer 架构\n"
                "**类型**: concept\n"
                "**创建**: 2026-07-15\n"
                "**更新**: 2026-07-15\n\n"
                "## 定义\n"
                "一种基于自注意力的序列到序列架构。\n\n"
                "## 详细说明\n"
                "由 Encoder 和 Decoder 组成。\n")

    yield tmp
    shutil.rmtree(tmp)


class TestMatchRelevantPages:
    def test_match_by_keyword(self, wiki_project):
        from wiki.bridge import match_relevant_pages
        results = match_relevant_pages("transformer 架构", wiki_project, top_k=3)
        assert len(results) >= 1
        paths = [r["path"] for r in results]
        assert any("transformer" in p for p in paths)

    def test_match_attention(self, wiki_project):
        from wiki.bridge import match_relevant_pages
        results = match_relevant_pages("attention 是什么", wiki_project, top_k=3)
        assert len(results) >= 1
        paths = [r["path"] for r in results]
        assert any("self-attention" in p for p in paths)

    def test_no_match(self, wiki_project):
        from wiki.bridge import match_relevant_pages
        results = match_relevant_pages("andfhasdkjhasdkjh", wiki_project)
        assert len(results) == 0

    def test_empty_index(self):
        import tempfile, os
        tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(tmp, "wiki"), exist_ok=True)
        from wiki.bridge import match_relevant_pages
        results = match_relevant_pages("test", tmp)
        assert results == []
        shutil.rmtree(tmp)

    def test_summary_content(self, wiki_project):
        from wiki.bridge import match_relevant_pages
        results = match_relevant_pages("self-attention", wiki_project)
        assert len(results) >= 1
        r = results[0]
        assert "summary" in r
        assert "自注意力" in r["summary"] or "注意力" in r["summary"]


class TestInjectWikiContext:
    def test_inject_with_match(self, wiki_project):
        from wiki.bridge import inject_wiki_context
        ctx = inject_wiki_context("attention 机制", wiki_project)
        assert "<wiki-context>" in ctx
        assert "self-attention" in ctx
        assert "</wiki-context>" in ctx

    def test_inject_no_match(self, wiki_project):
        from wiki.bridge import inject_wiki_context
        ctx = inject_wiki_context("地球是圆的", wiki_project)
        assert ctx == ""

    def test_inject_format(self, wiki_project):
        from wiki.bridge import inject_wiki_context
        ctx = inject_wiki_context("transformer", wiki_project)
        assert "<page path=" in ctx
        assert "<title>" in ctx
        assert "<summary>" in ctx
        assert "<content>" in ctx


class TestAutoCapture:
    def _make_gateway(self, response: dict):
        gw = MagicMock()
        r = MagicMock()
        r.content = json.dumps(response, ensure_ascii=False)
        gw.chat.return_value = r
        return gw

    def test_should_capture_true(self):
        from wiki.bridge import _should_capture
        messages = [
            {"role": "user", "content": "帮我选数据库"},
            {"role": "assistant", "content": "我推荐使用 PostgreSQL", "tool_calls": [{"function": {"name": "web"}}]},
            {"role": "tool", "content": "结果"},
            {"role": "assistant", "content": "决定使用 PostgreSQL 因为性能更好"},
        ]
        assert _should_capture(messages) is True

    def test_should_capture_false_no_tools(self):
        from wiki.bridge import _should_capture
        messages = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！有什么可以帮你的？"},
        ]
        assert _should_capture(messages) is False

    def test_should_capture_skip_simple_qa(self):
        from wiki.bridge import _should_capture
        assert _should_capture([], intent="simple_qa") is False
        assert _should_capture([], intent="greeting") is False

    def test_write_captured(self, wiki_project):
        from wiki.bridge import _write_captured
        extracted = {
            "decisions": [{"title": "选择 PostgreSQL", "context": "需要数据库", "rationale": "性能好"}],
            "lessons": [{"title": "连接池大小", "problem": "连接数过多", "solution": "限制最大连接数", "key_takeaway": "连接池要设上限"}],
            "concepts": [{"name": "连接池", "definition": "复用数据库连接的机制"}],
        }
        result = _write_captured(extracted, wiki_project)
        assert result["captured"] is True
        assert result["decisions"] == 1
        assert result["lessons"] == 1
        assert result["concepts"] == 1

        # 验证文件被创建
        assert os.path.isfile(os.path.join(wiki_project, "wiki", "decisions", "选择-postgresql.md"))
        assert os.path.isfile(os.path.join(wiki_project, "wiki", "lessons", "连接池大小.md"))
        assert os.path.isfile(os.path.join(wiki_project, "wiki", "concepts", "连接池.md"))

        # 验证 index.md 被更新
        index = open(os.path.join(wiki_project, "wiki", "index.md")).read()
        assert "选择-postgresql" in index
        assert "连接池大小" in index
        assert "连接池" in index

        # 验证 log.md 被更新
        log = open(os.path.join(wiki_project, "wiki", "log.md")).read()
        assert "CAPTURE" in log

    def test_write_captured_dedup(self, wiki_project):
        """相同内容不重复写入。"""
        from wiki.bridge import _write_captured
        extracted = {
            "decisions": [{"title": "选择 PostgreSQL", "context": "需要数据库", "rationale": "性能好"}],
            "lessons": [],
            "concepts": [],
        }
        r1 = _write_captured(extracted, wiki_project)
        assert r1["captured"] is True
        r2 = _write_captured(extracted, wiki_project)
        assert r2["captured"] is False  # 已存在，不重复

    def test_auto_capture_pipeline(self, wiki_project):
        """完整的 auto_capture 端到端。"""
        from wiki.bridge import auto_capture_from_session
        gw = self._make_gateway({
            "has_content": True,
            "decisions": [{"title": "选 Go", "context": "写 CLI", "rationale": "编译快"}],
            "lessons": [{"title": "Go 的错误处理", "problem": "容易忘检查 error", "solution": "用 lint", "key_takeaway": "errcheck 要开"}],
            "concepts": [],
        })
        messages = [
            {"role": "user", "content": "我想选个语言写 CLI"},
            {"role": "assistant", "content": "决定使用 Go", "tool_calls": [{"function": {"name": "web"}}]},
            {"role": "tool", "content": "结果"},
            {"role": "assistant", "content": "推荐使用 Go，编译快且跨平台"},
        ]
        result = auto_capture_from_session(messages, gw, "test", wiki_project)
        assert result["captured"] is True
        assert result["decisions"] == 1
        assert result["lessons"] == 1
        assert os.path.isfile(os.path.join(wiki_project, "wiki", "decisions", "选-go.md"))

    def test_auto_capture_no_content(self, wiki_project):
        """LLM 返回 has_content=false → 不写入。"""
        from wiki.bridge import auto_capture_from_session
        gw = self._make_gateway({"has_content": False, "decisions": [], "lessons": [], "concepts": []})
        messages = [
            {"role": "user", "content": "今天天气怎么样"},
            {"role": "assistant", "content": "晴天", "tool_calls": [{"function": {"name": "web"}}]},
        ]
        result = auto_capture_from_session(messages, gw, "test", wiki_project)
        assert result["captured"] is False

    def test_extract_conversation_context(self):
        from wiki.bridge import _extract_conversation_context
        msgs = [
            {"role": "user", "content": "帮我选数据库"},
            {"role": "assistant", "content": "推荐 PostgreSQL", "tool_calls": [{"function": {"name": "web_search"}}]},
            {"role": "tool", "content": "结果..."},
            {"role": "assistant", "content": "决定用 PostgreSQL"},
        ]
        ctx = _extract_conversation_context(msgs)
        assert "用户: 帮我选数据库" in ctx
        assert "推荐 PostgreSQL" in ctx
        assert "工具调用: web_search" in ctx
