# Protocol + runtime_checkable — 结构子类型 vs 名义子类型

## 问题

判断一个对象"是不是某种类型"，传统方式（名义子类型）要求显式继承：

```python
class ToolPluginABC(ABC): ...
class MyPlugin(ToolPluginABC): ...  # 必须继承
```

这对插件系统不友好：第三方插件需要 import 基类、显式继承，耦合大。

## 解法：Protocol（结构子类型）

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class ToolPlugin(Protocol):
    name: str
    description: str
    def tool_definitions(self) -> list[dict]: ...
    def execute(self, tool_name: str, args: dict) -> str: ...
```

任何对象只要包含 `name`/`description`/`tool_definitions()`/`execute()`，就被视为 ToolPlugin：

```python
class MyPlugin:  # 不需要继承
    name = "hello"
    description = ""
    def tool_definitions(self): return []
    def execute(self, *args): return ""

isinstance(MyPlugin(), ToolPlugin)  # True
```

## runtime_checkable 的作用

- Protocol **默认不支持** `isinstance()` 检查
- `@runtime_checkable` 开启 runtime 的结构检查：遍历对象所有属性和方法，看是否匹配 Protocol 声明
- 对**方法**的检查可靠，对**属性类型注解**（如 `name: str`）只检查有/无，不检查类型是否正确

## 插件系统的收益

1. **免继承** — 插件作者不需要 import chips 的基类，只要按约定实现方法
2. **免注册** — PluginManager 通过 `isinstance` 自动识别并注册到 ToolRegistry

## 适用场景

| 方式 | 适用 |
|------|------|
| 名义子类型（ABC/继承） | 框架内部、需要共享实现、需要基类提供默认行为 |
| 结构子类型（Protocol） | 插件系统、接口在第三方实现、松耦合场景 |
