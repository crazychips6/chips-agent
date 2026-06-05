# Phase 10 E 学习笔记

## 文件操作增强

### file_read 行范围

- `start_line` / `end_line` 可选参数，无参数时行为不变（全文返回）
- 行号输出格式：`行号 | 内容`，宽度自适应对齐
- 越界自动截断，不抛异常

### file_write patch

- `mode="patch"` 做搜索替换，`search` + `replace` 参数
- 只替换第一个匹配，不动的部分不受影响
- 找不到匹配时返回错误，不修改文件

### file_search

- 类似 grep，支持 `text`（默认不区分大小写）和 `regex` 两种模式
- 自动跳过 `.git/`、`node_modules/`、`.venv/`、`__pycache__/` 等目录
- 跳过敏感文件（.env、密钥等）和二进制文件

## Web 工具本质

**web_fetch** 和 **web_search** 底层都是同一个东西：`urllib.request` 发 HTTP GET。

| 工具 | 请求目标 | 返回处理 |
|------|---------|---------|
| web_fetch | 用户给的 URL | 解码 → 剥 HTML 标签 → 纯文本返回 |
| web_search | `https://lite.duckduckgo.com/lite/?q=关键词` | 解析结果页 HTML 表格 → 提取标题/链接/摘要 |

区别只是目标 URL 不同、对返回结果的处理方式不同，底层都是同一个 HTTP GET。

## SSRF 防护

### 为什么要禁止访问内网地址

SSRF（Server-Side Request Forgery，服务端请求伪造）是指攻击者利用服务端发起的网络请求来访问本不该暴露的资源。

AI agent 有 `web_fetch` 工具后，LLM 可以请求任意 URL。如果不加限制：

1. **内网服务探测** — `web_fetch("http://localhost:9200")` 可访问本地 Elasticsearch，`http://127.0.0.1:3000` 可访问本地开发服务器
2. **无认证管理接口** — Redis（6379）、Docker socket（2375）、数据库（5432/3306）等本地服务通常没有认证
3. **云元数据泄露** — 云服务器的 `http://169.254.169.254/latest/meta-data/` 可拿到临时凭据

### 这是什么同源策略

与浏览器的**同源策略**是同一思路——不让外部发出的请求随意访问你的本地资源。浏览器保护用户，SSRF 防护保护运行 agent 的主机。

### 当前实现

用 `ipaddress` 模块拦截内网网段，域名也会 DNS 解析后检查：

```python
_BLOCKED_NETWORKS = [
    "127.0.0.0/8",      # localhost
    "10.0.0.0/8",       # 私有 A 类
    "172.16.0.0/12",    # 私有 B 类
    "192.168.0.0/16",   # 私有 C 类
    "::1/128",          # IPv6 localhost
    "fc00::/7",         # IPv6 私有
]
```
