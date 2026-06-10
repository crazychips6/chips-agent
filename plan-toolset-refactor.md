# Toolset 系统重构计划

## 现状 vs 目标

```
当前:        set[str]         扁平集合                 一个 core 装所有
             ↓                ↓                        ↓
改进:   dict 含语义描述    toolset 级 check_fn     复合工具集 + 分类
         + prompt 注入    + 插件/MCP 动态注册       + LLM 可用性感知
```

---

## 改动详情

### 1. `tool/registry.py` — 新增 toolset 查询接口

已有 `check_fn` + `toolset` 字段，新增：

```python
class ToolRegistry:
    def get_tool_names_for_toolset(self, toolset: str) -> list[str]: ...
    def get_registered_toolset_names(self) -> list[str]: ...
    def get_toolset_for_tool(self, name: str) -> str | None: ...
    def check_toolset_availability(self, toolset: str) -> bool: ...
```

`check_toolset_availability` 逻辑：该 toolset 下只要有**任意一个工具**的 `check_fn` 通过（或无 check_fn），就视为可用。完全无工具返回 False。

### 2. `tool/toolsets.py` — 定义格式 + 可用性门控

```python
TOOLSET_SCHEMA = {
    "core": {
        "description": "核心工具集",
        "tools": ["echo"],
        "includes": ["terminal", "file", "web", "vision", "skills"],
    },
    "terminal": {
        "description": "终端命令执行",
        "tools": ["terminal"],
    },
    "file": {
        "description": "文件读写与搜索",
        "tools": ["file_read", "file_write", "file_search"],
    },
    "web": {
        "description": "网页搜索与抓取（需 TAVILY_API_KEY）",
        "tools": ["web_search", "web_fetch"],
    },
    "vision": {
        "description": "屏幕截图与图像分析",
        "tools": ["screenshot"],
    },
    "skills": {
        "description": "技能系统管理",
        "tools": ["skills_list", "skill_view", "skill_manage"],
    },
    "all": {
        "description": "全部工具",
        "tools": [],
        "includes": ["core"],
    },
}
```

新增 API：

| 函数 | 作用 |
|------|------|
| `get_toolset(name)` | 查静态定义，找不到则去 registry 查动态注册的 toolset（插件/MCP） |
| `resolve_toolset(name)` | 递归展平 includes |
| `resolve_multiple_toolsets(names)` | 合并多个 |
| `get_all_toolsets()` | 返回 statis + 动态注册的合并 |
| `get_toolset_names()` | 所有可用 toolset 名 |
| `is_toolset_available(name)` | 委托 registry.check_toolset_availability() |
| `build_availability_table()` | 返回 markdown 表格（供 prompt 注入） |

`get_toolset()` 的 fallback 逻辑确保插件和 MCP 注册的新 toolset 也能被识别。

### 3. `agent/prompt.py` — 注入可用性表

在 system prompt 中新增一个层：

````
## 可用工具集
| 工具集 | 状态 | 用途 |
|--------|------|------|
| terminal | ✓ | 终端命令执行 |
| file | ✓ | 文件读写与搜索 |
| web | ✗ 需配置 TAVILY_API_KEY | 网页搜索与抓取 |
| vision | ✓ | 屏幕截图与图像分析 |

不可用的工具集也可以请求用户帮助配置。
````

`PromptBuilder.build()` 新增参数 `toolset_availability: str = ""`。

### 4. 工具注册迁移

| 当前工具 | 当前 toolset | 改为 |
|----------|-------------|------|
| echo | core | → core（保留） |
| terminal | core | → terminal |
| file_read/write/search | core | → file |
| web_fetch/search | core | → web（加 check_fn: TAVILY_API_KEY?） |
| screenshot | core | → vision |
| skills_list/view/manage | core | → skills |

新增 check_fn 注册：

```python
# web.py
registry.register(
    name="web_search",
    toolset="web",
    check_fn=lambda: bool(os.getenv("TAVILY_API_KEY")),
    ...
)
```

`core` 不再直接持有这些工具，改为 `includes: ["terminal", "file", ...]`，保证 `--toolset core` 行为不变。

### 5. `agent/cli.py` — 多 toolset 选择

```python
parser.add_argument("--toolset", default="core", 
                    help="工具集名（可指定多个如: core,web）")
```

解析逻辑：
```python
toolset_names = args.toolset.split(",")
all_tool_names = resolve_multiple_toolsets(toolset_names)
agent.tool_names = all_tool_names & registry.tool_names
```

启动信息改进，显示各 toolset 状态：
```
工具集: core  |  终端 ✓ 文件 ✓ 网页 ✗(需 TAVILY_API_KEY)  技能 ✓  |  已加载工具: 8
```

### 6. `web/server.py` — 同步

`get_agent()` 中写死 `resolve_toolset("core")` → 改为可配置（环境变量 `CHIPS_TOOLSET`），默认 `"core"`。

### 7. 不做的（越界）

- `chips tools enable/disable` 子命令 — 以后需要再加
- 场景化复合工具集（`debugging`, `safe`）— 等工具多了再加
- toolset 缓存热更新 — TTL 30s 够用了

---

## 与 Hermes 的差距

| 维度 | Hermes | 本计划 |
|------|--------|--------|
| prompt 可用性表 | ✅ 完整 | ✅ 同 |
| check_fn 注册链 | 首个工具注册自动建立 toolset gate | 每个工具独立 check_fn，查询时聚合 |
| MCP/插件注册 toolset | ✅ `get_toolset()` fallback | ✅ 同 |
| 复合工具集 | `debugging`, `safe`, `hermes-cli` 等 | 仅 `core`, `all`，TODO |
| 用户开关 | `hermes tools enable/disable` | ❌ 以后再做 |
| per-toolset env 提示 | setup_url 指引 | ❌ 先不做 |
