# CLI 层 vs Manager 层的职责分离

`PluginManager`（manager.py）负责运行时：发现 `.py` 文件 → 加载 → 注册工具/hook → 调度钩子。

`plugins/cli.py` 只负责用户交互和文件管理：复制、删除、扫描展示。因为插件实际上不需要"安装"，放对位置就能被自动发现，所以 `install` 本质只是 `cp` 的封装，属于 CLI 便利工具，不是运行时的职责。

**原则**：CLI 层做文件管理和用户交互，Manager 层做加载和调度，不混在一起。
