## 重要：Phase 10 B2 — DockerEnvironment

- 新建 `environment/docker.py` — DockerEnvironment 实现

### 设计

**容器生命周期：**
- `__init__` → `docker run -d --rm IMAGE tail -f /dev/null`（后台常驻容器）
- `execute()` → `docker exec CONTAINER command`（复用同一容器）
- `close()` → `docker rm -f CONTAINER`（清理容器）

**安全与一致性：**
- `shell=False`（通过 `shlex.split` 拆参数，与 LocalEnvironment 一致）
- 子进程（`docker exec`）PID 跟踪，超时自动 kill
- `close()` 先杀本地子进程，再删容器

**依赖：** 不需要 Python Docker SDK，通过 `docker` CLI 通信。镜像默认 `alpine:latest`（最小拉取）。

### 测试

- `test/test_docker_environment.py` — 10 个测试
- `pytest.mark.skipif(_docker_available())` — 无 Docker 时自动跳过
- 覆盖：echo、stderr、不存在命令、超时、空命令、解析错误、容器清理、重复 close、多次执行、init 后容器状态
- 全量测试从 218 → 228 通过
