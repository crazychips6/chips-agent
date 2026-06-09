#

## skills 规范不统一 
  ┌──────────────┬────────────────────────────────────────────┐
  │     平台     │               技能/扩展机制                │
  ├──────────────┼────────────────────────────────────────────┤
  │ Claude Code  │ 自定义 .claude/skills/，register(ctx) 协议 │
  ├──────────────┼────────────────────────────────────────────┤
  │ Cursor       │ .cursor/rules/，自定义 AI 规则文件         │
  ├──────────────┼────────────────────────────────────────────┤
  │ Windsurf     │ .windsurf/rules/，类似                     │
  ├──────────────┼────────────────────────────────────────────┤
  │ Copilot      │ .github/copilot-instructions.md，纯 prompt │
  ├──────────────┼────────────────────────────────────────────┤
  │ Continue.dev │ ~/.continue/，JSON 配置                    │
  ├──────────────┼────────────────────────────────────────────┤
  │ chips        │ 自定义 Skill dataclass                     │
  └──────────────┴────────────────────────────────────────────┘

  不标准化的原因很简单：技能在每家 agent 里的语义不同。有的只是 prompt
  片段，有的是工具集合，有的是斜杠命令，有的是生命周期钩子，有的全都有。

## skills的文件式规范

## chips 两套 skill 系统

### 架构总结

chips 中有两套并行的 skill 系统，底层机制不同但最终都通过 skill_tools 暴露给 LLM：

| | 文件 skill | 插件 skill |
|---|---|---|
| 注册方式 | 文件存在即被 `SkillManager.scan()` 发现 | 插件 `register()` 内主动调 `register_skill()` |
| 存储位置 | `SkillManager._index` | `PluginManager._plugin_skills`（qualified key） |
| 命名 | 扁平 `name` | 命名空间 `plugin:name` |
| 发现路径 | 直接在 `<available_skills>` 索引中可见 | 通过 `skills_list` 发现 |
| 加载方式 | `skill_view("name")` | `skill_view("plugin:name")` |
| 文件读取 | 懒加载，调用 `skill_view` 时才读 file content | 同左 |
| 归属管理 | `SkillManager` 负责 | `PluginManager` 负责 |

### Wiring 流程

```
文件 skill:
  SkillManager.scan() → _index → get_skills_index_prompt() → agent.skills_index（入 system prompt）

插件 skill:
  PluginManager.load_all() → 执行 register(ctx) → register_skill() → _plugin_skills["p:n"] = {...}
  → cli.py: wire_skill_manager(skill_mgr) + wire_plugin_manager(plugin_mgr) → skill_tools

LLM 接口统一由 skill_tools 的 3 个工具暴露：
  - skills_list: 列出文件 + 插件技能（带 qualified name 提示）
  - skill_view:   ":" 路由到 PluginManager，否则路由到 SkillManager
  - skill_manage: 仅操作文件系统技能
```

### 关键设计决策

1. **注册时不读文件** —— `register_skill()` 只存 path 元数据，`skill_view` 调用时才 `read_text()`
2. **渐进式披露** —— 文件技能在 system prompt 只放 name + 60字描述，不是全文
3. **插件技能不在索引中** —— 插件技能不进入 `<available_skills>`，需要 LLM 先调 `skills_list` 发现
4. **tool/ 零依赖** —— `skill_tools.py` 通过 TYPE_CHECKING + wiring 注入避免 import agent 模块

### 与 Hermes 对齐

- 命名空间分离：文件扁平，插件带 `:` 前缀
- plugin 技能 opt-in 显式加载，不污染全局索引
- `skill_view` 统一入口，按名字格式路由
