"""LLM 提示词模板 — wiki 三个子图所需的所有 prompt"""

from __future__ import annotations

# ── Ingest 提取 ──

EXTRACT_ARTICLE = """你是一个信息提取器。从给定的文章/笔记中提取结构化信息。

返回 JSON 格式（不要 markdown 代码块，只输出纯 JSON）：

{
  "title": "文章标题",
  "slug": "kebab-case-标识符",
  "summary": "一句话摘要（20字以内）",
  "type": "article",
  "concepts": [
    {"name": "概念名", "slug": "concept-slug", "definition": "一句话定义", "details": "详细说明（2-3句）", "source_context": "原文中的相关段落"}
  ],
  "entities": [
    {"name": "实体名", "slug": "entity-slug", "category": "person|project|tool|organization", "description": "一句话介绍"}
  ],
  "quotes": ["值得保留的原文引用"],
  "tags": ["标签1", "标签2"]
}

要求：
1. 概念名用中文或原文，slug 用英文 kebab-case
2. 如果文章提到了多个概念，提取 2-5 个最核心的
3. definition 控制在 30 字以内
4. quotes 只提取最有价值的 1-3 句
5. 如果是笔记类文章，tags 包含分类标签
"""

EXTRACT_JOURNAL = """你是一个信息提取器。从给定的日志/日记中提取结构化信息。

返回 JSON 格式（不要 markdown 代码块，只输出纯 JSON）：

{
  "title": "日志标题（如 Daily Log: YYYY-MM-DD）",
  "slug": "kebab-case-标识符",
  "summary": "一句话摘要",
  "type": "journal",
  "concepts": [
    {"name": "概念名", "slug": "concept-slug", "definition": "一句话定义", "details": "详细说明", "source_context": "原文"}
  ],
  "entities": [
    {"name": "实体名", "slug": "entity-slug", "category": "person|project|tool|organization", "description": "一句话介绍"}
  ],
  "decisions": [
    {"title": "决策标题", "context": "决策背景", "rationale": "选择理由", "alternatives": "备选方案"}
  ],
  "lessons": [
    {"title": "教训/经验标题", "problem": "遇到的问题", "solution": "解决方法", "key_takeaway": "核心收获"}
  ],
  "quotes": ["值得保留的原文引用"]
}

要求：
1. decisions 和 lessons 是 journal 特有的字段
2. decisions 提取用户明确做的决策（含 rationale）
3. lessons 提取技术/经验教训（含 problem → solution）
"""

EXTRACT_PAPER = """你是一个信息提取器。从给定的论文/技术报告中提取结构化信息。

返回 JSON 格式（不要 markdown 代码块，只输出纯 JSON）：

{
  "title": "论文标题",
  "slug": "kebab-case-标识符",
  "summary": "一句话摘要",
  "type": "paper",
  "concepts": [
    {"name": "概念名", "slug": "concept-slug", "definition": "一句话定义", "details": "详细说明", "source_context": "原文"}
  ],
  "entities": [
    {"name": "实体名", "slug": "entity-slug", "category": "person|project|tool|organization", "description": "一句话介绍"}
  ],
  "methodology": "核心方法描述",
  "findings": ["发现1", "发现2"],
  "quotes": ["值得保留的原文引用"],
  "tags": ["标签1", "标签2"]
}

要求：
1. methodology 控制在 100 字以内
2. findings 提取 2-4 个核心发现
3. concepts 提取论文提出的新概念或关键技术点
"""

# ── Query 路由 + 综合 ──

ROUTE_QUESTION = """你是一个知识库路由员。根据用户的提问，从 index.md 条目中选出最相关的 2-5 个页面。

index.md 内容：
{index_content}

用户提问：{question}

返回 JSON（只输出纯 JSON）：
{{
  "relevant_pages": ["page-path-1", "page-path-2"],
  "reasoning": "简要说明为什么选择这些页面"
}}

要求：
1. 只返回确实相关（能帮助回答问题的）的页面
2. 如果完全不相关，返回空列表
3. 页面路径是 index.md 条目中的 [[链接]] 内容
"""

SYNTHESIZE_ANSWER = """你是一个知识综合回答员。基于提供的 wiki 页面内容，回答用户的问题。

用户问题：{question}

相关页面内容：
{page_contents}

要求：
1. 引用相关页面时使用 [[wikilink]] 格式，如 [[self-attention]]
2. 如果信息来自多个页面，综合它们的观点
3. 如果信息不足以回答，明确说明
4. 语言风格和用户提问一致（中文/英文）
5. 不要编造信息，只根据已有内容回答

回答：
"""

# ── Lint 矛盾检测 ──

DETECT_CONTRADICTIONS = """你是一个矛盾检测员。检查以下两段内容是否有冲突的声明。

页面 A（{page_a}）：
{content_a}

页面 B（{page_b}）：
{content_b}

返回 JSON（只输出纯 JSON）：
{{
  "has_contradiction": true/false,
  "claim_a": "页面 A 中的声明",
  "claim_b": "页面 B 中的声明",
  "severity": "high|medium|low"
}}

要求：
1. 只有两处明确冲突（不能同时为真）时才标记
2. 措辞不同但意思相同的不算矛盾
3. severity：high=完全矛盾，medium=部分不一致，low=表述偏差
"""


def get_extract_prompt(file_type: str) -> str:
    """根据文件类型返回对应的提取 prompt。"""
    mapping = {
        "article": EXTRACT_ARTICLE,
        "journal": EXTRACT_JOURNAL,
        "paper": EXTRACT_PAPER,
    }
    return mapping.get(file_type, EXTRACT_ARTICLE)
