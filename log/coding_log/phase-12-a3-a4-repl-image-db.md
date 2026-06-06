## 重要：Phase 12 A3（REPL 集成）+ A4（存储与回放）

### A3 REPL 集成 — 用户输入图片检测

- `agent/message.py` 新增 `parse_user_content(text: str)` — 解析用户输入，检测图片 URL（http）和本地文件路径
  - 有图片 → 返回 `list[TextBlock | ImageBlock]`
  - 纯文本 → 返回原 `str`
  - 支持 URL query params、jpg/png/gif/webp 格式
  - 本地路径存在时读为 data URI，不存在时降级为 TextBlock
- `agent/loop.py` `run_conversation` 中用户消息经 `parse_user_content()` 处理后追加

### A4 存储 — ContentBlock JSON 序列化

- `session/db.py` 新增 `_serialize_content()` / `_deserialize_content()` 静态方法
  - str → 原样存
  - list[dict]（ContentBlock 的 OpenAI API 格式）→ JSON 序列化
  - 恢复时 JSON 解析回 list[dict]（通过 `[{type, ...}, ...]` 结构检测）
  - 以 `[` 开头的纯文本不会被误解析
- `save_message()`、`save_messages()`、`get_history()` 全部使用序列化方法

### A4 回放 — 兼容性

- `loop.py` `_total_chars()` 改为 `_content_len()` 兼容 list content（用于上下文压缩）
- 恢复会话时 `get_history()` 自动反序列化，`to_openai_messages()` 已支持 dict 格式的 block
- `_check_vision_capability()` 在 dict 格式下正常工作

### 文件变动
| 文件 | 改动 |
|------|------|
| `agent/message.py` | 新增 `parse_user_content()`、`serialize_content()`、`deserialize_content()`；`to_openai_content()` 支持 dict block |
| `agent/loop.py` | `run_conversation` 使用 parse_user_content；新增 `_content_len()` |
| `session/db.py` | 新增 `_serialize_content()` / `_deserialize_content()`；存读全部使用 |

### 测试 (新增 19 条)
- `test_message.py` — `TestParseUserContent` (7)、`TestSerializeDeserialize` (6)、`TestImageFileToDataURI` (2)
- `test_session.py` — `test_save_content_block_list`、`test_save_mixed_content_in_batch`、`test_content_roundtrip_preserves_data`、`test_plain_text_unaffected`

### 全量测试
404 条，全部通过。
