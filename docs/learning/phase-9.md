## config 

- `agent/cli.py` 新增 `_load_config()`：读取 `~/.chips/config.yaml`（YAML 格式）
- 优先级：CLI 参数 > 配置文件 > 环境变量

