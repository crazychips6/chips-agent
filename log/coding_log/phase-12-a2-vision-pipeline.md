## 重要：Phase 12 A2 — 多模态消息管道

将 `to_openai_messages()` 集成到 LLM 调用路径，并添加 vision 能力检测。

### loop.py 改动
- `run_conversation()` 中构建 API 请求时，使用 `to_openai_messages()` 序列化所有消息
- 新增 `_VISION_MODELS` 类常量，列出已知 vision 模型（gpt-4o/claude-3-5-sonnet/gemini 等）
- 新增 `_is_vision_model()` — 当前模型是否支持 vision
- 新增 `_check_vision_capability()` — 遍历 API 消息，发现 image_url 块后检查模型支持，不支持则抛 ValueError

### 行为
- 纯文本消息不受影响（全部 373 条已有测试通过）
- 含 ContentBlock 的消息自动正确序列化为 OpenAI API 格式
- 非 vision 模型收到图片消息 → 明确错误提示

### 测试 (4 条新增)
- `test_non_vision_model_rejects_image_block` — deepseek-chat + ImageBlock → ValueError
- `test_vision_model_allows_image_block` — gpt-4o + ImageBlock → 正常返回
- `test_non_vision_model_text_only_ok` — deepseek-chat + 纯文本 → 正常
- `test_is_vision_model_helper` — 不同模型的 vision 检测结果

### 文件变动
- 修改 `agent/loop.py`（+40 行）
- 修改 `agent/message.py`（+16 行，新增 `contains_image_block()`）
- 修改 `test/test_message.py`（+31 行，5 条 contains_image_block 测试）
- 修改 `test/test_loop.py`（+66 行，4 条 vision 测试）
