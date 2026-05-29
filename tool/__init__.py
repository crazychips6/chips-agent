# tool — 工具系统目录
# 零内部依赖，通过 ToolRegistry 单例实现自注册。builtins/ 下的工具模块通过
# "import tool.builtins" 的副作用触发 registry.register()。
