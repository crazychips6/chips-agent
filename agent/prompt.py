class PromptBuilder:
    """System prompt 组装器。

    当前仅拼接 2 层：核心身份 + 记忆快照。
    后续阶段将实现 7 层组装：
    (1) 核心身份 (2) 当前日期 (3) 用户偏好 (4) 记忆快照 (5) 项目上下文 (6) 工具规则 (7) 调用约定
    """

    def build(self, memory_snapshot: str = "") -> str:
        prompt = DEFAULT_SYSTEM_PROMPT
        if memory_snapshot:
            prompt += f"\n\n{memory_snapshot}"
        return prompt


DEFAULT_SYSTEM_PROMPT = """你是 chips，一个通用 AI agent。
你由多个解耦模块组成，当前处于开发阶段。
你的目标是帮助用户完成各种任务。

## 行为准则
- 使用中文回答，技术术语不强行翻译
- 如果缺少完成任务所需的信息，主动询问用户
- 如果遇到错误，说明原因并提供解决方案"""
