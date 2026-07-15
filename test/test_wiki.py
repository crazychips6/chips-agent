"""Wiki 端到端测试 — 模拟 LLM 提取验证 Ingest / Query / Lint 流水线。"""
from __future__ import annotations

import os
import shutil
import tempfile
from unittest.mock import MagicMock, PropertyMock

import pytest


@pytest.fixture
def wiki_project():
    """创建测试用的 wiki 项目目录。"""
    tmp = tempfile.mkdtemp()
    # raw/
    os.makedirs(os.path.join(tmp, "raw", "articles"))
    os.makedirs(os.path.join(tmp, "raw", "journal"))
    # wiki/（含默认 index.md + log.md）
    for sub in ("concepts", "entities", "sources", "decisions", "lessons", "answers"):
        os.makedirs(os.path.join(tmp, "wiki", sub))
    # index.md
    with open(os.path.join(tmp, "wiki", "index.md"), "w") as f:
        f.write("# Wiki Index\n\n## Concepts\n（暂无条目）\n\n## Sources\n（暂无条目）\n")
    # log.md（在 wiki/ 目录内）
    with open(os.path.join(tmp, "wiki", "log.md"), "w") as f:
        f.write("# Operation Log\n\n[2026-07-15 00:00] INIT — 测试仓库\n")
    yield tmp
    shutil.rmtree(tmp)


def _make_mock_gateway(extracted: dict):
    """创建返回固定提取结果的 mock gateway。"""
    gw = MagicMock()
    result = MagicMock()
    import json
    result.content = json.dumps(extracted, ensure_ascii=False)
    gw.chat.return_value = result
    return gw


class TestDetectNewFiles:
    def test_detect_new_files(self, wiki_project):
        from wiki.ingest import _detect_new_files
        pages_dir = os.path.join(wiki_project, "wiki")
        result = _detect_new_files(os.path.join(wiki_project, "raw"), pages_dir, force=False)
        assert result == []

    def test_detect_with_new_file(self, wiki_project):
        from wiki.ingest import _detect_new_files
        # 创建一个新文件
        test_file = os.path.join(wiki_project, "raw", "articles", "test-article.md")
        with open(test_file, "w") as f:
            f.write("# Test Article\n内容")
        pages_dir = os.path.join(wiki_project, "wiki")
        result = _detect_new_files(os.path.join(wiki_project, "raw"), pages_dir, force=False)
        assert len(result) == 1
        assert result[0].endswith("test-article.md")

    def test_force_reprocess(self, wiki_project):
        from wiki.ingest import _detect_new_files
        test_file = os.path.join(wiki_project, "raw", "articles", "test-article.md")
        with open(test_file, "w") as f:
            f.write("# Test Article\n内容")
        pages_dir = os.path.join(wiki_project, "wiki")
        result = _detect_new_files(os.path.join(wiki_project, "raw"), pages_dir, force=True)
        assert len(result) == 1


class TestClassifyFile:
    def test_article(self):
        from wiki.ingest import _classify_file
        assert _classify_file("/x/raw/articles/foo.md") == "article"

    def test_journal(self):
        from wiki.ingest import _classify_file
        assert _classify_file("/x/raw/journal/2026-07-15.md") == "journal"


class TestIngestPipeline:
    def test_dry_run(self, wiki_project):
        from wiki.ingest import run_ingest
        gw = _make_mock_gateway({
            "title": "测试文章",
            "slug": "test-article",
            "summary": "这是一篇测试文章",
            "type": "article",
            "concepts": [{"name": "测试概念", "slug": "test-concept", "definition": "概念的测试定义", "details": "详细说明"}],
            "entities": [],
            "quotes": ["测试引用"],
        })
        # 创建测试文件
        test_file = os.path.join(wiki_project, "raw", "articles", "test-article.md")
        with open(test_file, "w") as f:
            f.write("# Test Article\n测试内容")
        result = run_ingest(gw, "test-model", wiki_dir=wiki_project, dry_run=True)
        assert "预览" in result or "dry" in result.lower() or "预览模式" in result
        # 不应写任何文件
        concepts_dir = os.path.join(wiki_project, "wiki", "concepts")
        assert len(os.listdir(concepts_dir)) == 0

    def test_full_ingest(self, wiki_project):
        from wiki.ingest import run_ingest
        gw = _make_mock_gateway({
            "title": "测试文章",
            "slug": "test-article",
            "summary": "这是一篇测试文章",
            "type": "article",
            "concepts": [{"name": "自注意力机制", "slug": "self-attention", "definition": "一种注意力机制", "details": "Transformer 的核心组件"}],
            "entities": [{"name": "Google", "slug": "google", "category": "organization", "description": "科技公司"}],
            "quotes": ["Attention is all you need"],
        })
        test_file = os.path.join(wiki_project, "raw", "articles", "test-article.md")
        with open(test_file, "w") as f:
            f.write("# Test Article\n测试内容")
        result = run_ingest(gw, "test-model", wiki_dir=wiki_project, dry_run=False)
        # 验证页面文件被创建
        sources_dir = os.path.join(wiki_project, "wiki", "sources")
        concepts_dir = os.path.join(wiki_project, "wiki", "concepts")
        entities_dir = os.path.join(wiki_project, "wiki", "entities")
        assert os.listdir(sources_dir), f"source pages not created: {os.listdir(sources_dir)}"
        assert os.listdir(concepts_dir), f"concept pages not created: {os.listdir(concepts_dir)}"
        assert os.listdir(entities_dir), f"entity pages not created: {os.listdir(entities_dir)}"
        # 验证 index.md 被更新
        index_path = os.path.join(wiki_project, "wiki", "index.md")
        index_content = open(index_path).read()
        assert "self-attention" in index_content
        assert "test-article" in index_content
        # 验证 log.md 被追加
        log_path = os.path.join(wiki_project, "wiki", "log.md")
        log_content = open(log_path).read()
        assert "INGEST" in log_content
        assert "test-article" in log_content


class TestQuery:
    def test_read_index(self, wiki_project):
        from wiki.query import read_index
        content = read_index(wiki_project)
        assert "# Wiki Index" in content
        assert "Concepts" in content

    def test_read_page_not_exists(self, wiki_project):
        from wiki.query import read_page
        result = read_page("concepts/nonexistent", wiki_project)
        assert result is None

    def test_search(self, wiki_project):
        # 写一个测试页面
        test_path = os.path.join(wiki_project, "wiki", "concepts", "test-concept.md")
        with open(test_path, "w") as f:
            f.write("# Test Concept\n这是一个测试概念，用于验证搜索功能。")
        from wiki.query import search_pages
        results = search_pages("测试", wiki_project)
        assert len(results) >= 1
        assert any("test-concept" in r["name"] for r in results)

    def test_route_question(self, wiki_project):
        from wiki.query import route_question
        index = "# Wiki Index\n\n## Concepts\n- [[self-attention]] — 自注意力机制\n- [[transformer]] — Transformer 架构\n"
        gw = MagicMock()
        result = MagicMock()
        result.content = '{"relevant_pages": ["concepts/self-attention"], "reasoning": "用户问了 attention"}'
        gw.chat.return_value = result
        pages = route_question("attention 是什么", index, gw, "test")
        assert pages == ["concepts/self-attention"]


class TestLint:
    def test_empty_wiki(self, wiki_project):
        from wiki.lint import run_lint
        report = run_lint(wiki_dir=wiki_project, skip_contradictions=True)
        assert "Lint 报告" in report

    def test_broken_link_detection(self, wiki_project):
        # 创建一个有断链的页面
        test_path = os.path.join(wiki_project, "wiki", "concepts", "test-concept.md")
        with open(test_path, "w") as f:
            f.write("# Test Concept\n参照 [[nonexistent-page]] 了解更多。\n**更新**: 2026-07-15\n")
        from wiki.lint import run_lint
        report = run_lint(wiki_dir=wiki_project, skip_contradictions=True)
        assert "断链" in report or "🔴" in report


class TestNormalizeFilename:
    def test_kebab_case(self):
        from wiki.files import normalize_filename
        assert normalize_filename("Transformer Architecture") == "transformer-architecture"
        assert normalize_filename(" Self-Attention ") == "self-attention"
        assert normalize_filename("中文名词") == "中文名词"
        assert normalize_filename("test@#$name") == "test-name"

    def test_wikilink_re(self):
        from wiki.files import extract_wikilinks
        content = "参考 [[self-attention]] 和 [[concepts/transformer]]"
        links = extract_wikilinks(content)
        assert "self-attention" in links
        assert "concepts/transformer" in links
