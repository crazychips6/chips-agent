## 重要：Phase 12 A3 — 截图工具

新增 `screenshot` 工具，支持截屏保存为图片文件。

### 实现
- `tool/builtins/screenshot.py` — 多策略回退截屏（ImageMagick → GNOME Screenshot → KDE Spectacle → macOS screencapture）
- 截图保存到 `.chips/screenshots/screenshot-{YYYYMMDD-HHMMSS}.png`
- 零外部依赖，仅使用 subprocess 调用系统工具
- 注册为 core 工具集

### 测试 (7 条)
- 注册验证、成功截屏、所有方法失败、超时回退、目录自动创建、文件名时间戳、无参调用

### 文件变动
- 新增 `tool/builtins/screenshot.py`
- 修改 `tool/builtins/__init__.py`（添加 import）
- 修改 `tool/toolsets.py`（core 集添加 screenshot）
- 修改 `test/test_toolsets.py`（core 集合添加 screenshot）
- 新增 `test/test_screenshot.py`

### 备注
A3 的第二部分（REPL 拖入/粘贴图像 → image_url content block）未实现，需在后续阶段完成。
