# Bad Case: DeepSeek reasoning_content 含 Surrogate 字符导致序列化崩溃

一句话原因：
deepseek 的reasoning_content(思维链)传回了一个被截断的 utf-16，utf-8处理器无法解决。
一句话解决：
使用re.sub 替换utf-16的字符

## 问题

运行时抛 `UnicodeEncodeError: surrogates not allowed`，栈顶在 OpenAI SDK 的 JSON 序列化函数 `openapi_dumps`。

```
File "openai/_base_client.py", line 586, in _build_request
    kwargs["content"] = openapi_dumps(json_data)
                        ~~~~~~~~~~~~~^^^^^^^^^^^
File "openai/_utils/_json.py", line 25, in openapi_dumps
    ).encode()
UnicodeEncodeError: 'utf-8' codec can't encode character '\udce4'
in position 3613: surrogates not allowed
```

## 触发条件

- 使用 DeepSeek API（`reasoning_content` 字段生效的模型）
- LLM 返回的 `reasoning_content` 包含 surrogate 字符（`\ud800`–`\udfff` 范围）
- 该消息存入 `self.messages`，下一轮请求时连同历史一起序列化，导致崩溃

## 根因分析

| 层次 | 说明 |
|------|------|
| **表层** | JSON 序列化时遇到 surrogate 字符，UTF-8 编码拒绝 |
| **直接原因** | `_build_assistant_msg` 将 API 返回的 `reasoning_content` 原样存储到 `self.messages`，未做清洗 |
| **深层原因** | Python 允许 surrogate 字符存在于 `str` 对象中（CPython 使用 Latin-1 而非 UCS-4 编译时），但 JSON 序列化（`json.dumps` / `orjson.dumps`）和 UTF-8 编码都禁止它们 |
| **外部原因** | DeepSeek API 的部分模型在 `reasoning_content` 中会输出不合规的 UTF-16 surrogate，推测是模型训练数据或 tokenizer 引入了未收敛的编码片段 |

## 修复

在 `agent/loop.py` 中新增 `_sanitize()` 函数，用正则全局剥离 surrogate 字符：

```python
_SURROGATE_RE = re.compile('[\ud800-\udfff]')

def _sanitize(text: str) -> str:
    return _SURROGATE_RE.sub("", text)
```

所有进入 `self.messages` 的字符串都经过 `_sanitize()`：

1. `msg.content` — API 响应的文本内容
2. `msg.reasoning_content` — DeepSeek 思考链（主要肇事者）
3. `tc.function.name` 和 `tc.function.arguments` — tool_call 参数
4. `user_message` — 用户输入（防御性）

## 为什么测试没发现

测试中使用 MagicMock 模拟 API 响应，返回的都是普通 ASCII 字符串，没有覆盖含有 surrogate 字符的边缘情况。需要在 `test_loop.py` 中增加一个含 surrogate 的 reasoning_content 测试用例。

## 教训

1. 从外部 API 读入的字符串不能信任其编码合规性，进入消息历史前必须清洗
2. 此类问题在 mock 测试中极难暴露，除非在测试中显式构造非法编码
3. 序列化错误集中在数据流的输出边界（消息 → API 请求体），应在输入边界（API 响应 → 消息存储）就做防御
