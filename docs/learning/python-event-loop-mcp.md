## Python 事件循环详解（结合 chips MCP 代码）

### 什么是事件循环

事件循环是一个**永不停歇的调度器**，它的核心逻辑：

```python
# 事件循环的简化本质
def run_until_complete(self, coro):
    task = Task(coro, loop=self)
    while not task.done():
        self._run_once()  # 执行一轮调度
    return task.result()

def _run_once(self):
    # 1. 检查有待执行的协程 → 执行一步
    # 2. 检查有完成的 IO（网络/文件）→ 唤醒对应的 await
    # 3. 检查有到期的定时器/Event/sleep → 放行
    # 4. 没事做就阻塞等待 IO 事件
```

它不是业务逻辑，而是**执行协程的运行时**。协程通过 `await` 让出控制权，事件循环在等待期间去调度其他协程。

### 事件循环如何"驱动"协程

```python
async def _run_session(self):
    async with stdio_client(params) as (read, write):  # ①
        async with ClientSession(read, write) as session:  # ②
            await session.initialize()  # ③
            self._ready.set()
            await self._stop_event.wait()  # ④
```

事件循环的执行轨迹（全部在同一个线程中）：

```
① async with 进入 → 启动子进程，挂起等待
   事件循环：没别的事可做，阻塞等 IO
   子进程就绪 → 事件循环唤醒协程 → 返回 (read, write)

② async with ClientSession → 挂起等待 session 就绪
   就绪后继续往下

③ initialize() → 发 JSON-RPC，挂起等响应
   事件循环阻塞在 stdout 读取上
   子进程返回响应 → 唤醒 → initialize 完成

④ _stop_event.wait() → 协程挂起，等待 event 被 set
   事件循环无事可做，阻塞等 IO
```

每一步 `await` 都是协程在说："我现在没事干，你先去跑别的"，事件循环就进入 `_run_once()` 的等待阶段。没有其他协程时，就是阻塞等 IO。

### 为什么事件循环必须绑到子线程

因为主线程已被 REPL 占用：

```python
# cli.py 主线程
loop.run()  # ← 同步阻塞的 while 循环：用户输入 → LLM → 输出
```

**一个线程同一时间只能跑一个 while 循环。** 所以必须：

```
主线程: while 用户输入 → LLM 调用 → 输出
                             ↕ future.result()（阻塞等待）
子线程: while 事件循环 → 驱动协程 → await IO
```

主线程跑 REPL，子线程跑事件循环，二者通过 `future` 通信。

### run_coroutine_threadsafe — 跨线程投递协程

```python
def call_tool(self, name, arguments):
    future = Future()
    asyncio.run_coroutine_threadsafe(
        self._async_call_tool(name, arguments, future),
        self._loop,  # 指定在哪个事件循环执行
    )
    return future.result(timeout=120)  # 主线程阻塞等结果
```

`run_coroutine_threadsafe(coro, loop)` 做的事：
1. 把 coro 包装成 Task
2. 放入 loop 的待执行队列
3. 返回 concurrent.futures.Future

子线程的事件循环在下一轮 `_run_once()` 时从队列取出 Task 执行，执行完后通过 Future 通知主线程。

### 串行本质

尽管有两个线程，调用链路仍然是串行的：

```
主线程 dispatch → handler → call_tool → future.result()
                                            ↓ 阻塞
子线程                        → _async_call_tool → session.call_tool()
                                                      ↓ 执行完
                                            ← result ←
主线程  ← 拿到结果 ←
```

主线程阻塞等待，子线程执行。没有并发收益，纯串行。

### 根本原因

当前这套复杂度的根源只有一个：**选择了 async 的 MCP SDK，而 chips 是同步架构**。用同步 MCP 客户端的话，直接 `proc.stdout.readline()` 阻塞读，不需要事件循环，不需要子线程，不需要 `run_coroutine_threadsafe`，不需要 Future。
