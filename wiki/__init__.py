"""Wiki — 个人知识库引擎（Ingest / Query / Lint）

核心工作流：
  Ingest: raw/ 新文件 → LLM 提取 → wiki 页面 → index.md + log.md
  Query:  用户提问 → 读 index.md → 选相关页面 → 综合回答
  Lint:   断链 / 孤儿页 / 索引一致 / 过期 / 矛盾检测
"""
