## 重要：Phase 12 A1 — Content Block 抽象

创建 `agent/message.py` 模块，为多模态消息提供类型抽象：

- `TextBlock`（纯文本内容块）+ `ImageBlock`（image_url 图片块）
- `ContentBlock = TextBlock | ImageBlock` 联合类型
- `to_openai_content()` — 内部 content 转 OpenAI API 格式
- `to_openai_messages()` — 消息列表转 OpenAI API 格式（处理 ContentBlock 序列化，兼容纯文本）

### 设计要点
- `TextBlock` / `ImageBlock` 使用 `frozen=True` dataclass，不可变安全
- `ImageBlock.field` 使用 `url` 而非 `image_url`，避免与 type 值混淆
- `to_openai_messages()` 接受 `list[dict]`（现有消息格式），不要求迁移到 Message 类
- `ContentBlock` 消息的 content 为 `list[ContentBlock]`，纯文本仍为 `str`，互不干扰

### 测试覆盖 (21 条)
- `test_message.py`：TextBlock / ImageBlock 构造、ContentBlock 类型兼容、to_openai_content（纯文本/TextBlock/ImageBlock/混合/空列表）、to_openai_messages（纯文本/system/assistant+tool/tool result/ContentBlock/混合/缺省 content）

### 文件变动
- 新增 `agent/message.py` (41 行)
- 新增 `test/test_message.py` (120 行)
- 无其他文件修改
