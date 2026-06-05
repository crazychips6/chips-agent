"""web 工具 — web_fetch（HTTP GET）+ web_search（搜索）

使用 stdlib urllib 实现，零外部依赖。
自带 HTML 内容提取、SSRF 防护、超时和大小限制。

web_search 后端选择（按优先级）：
1. TAVILY_API_KEY 环境变量 → Tavily Search API
2. 否则 → DuckDuckGo Lite"""

import ipaddress
import json as _json
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

from tool.registry import registry

# 最大响应字节数
_MAX_RESPONSE_BYTES = 5 * 1024 * 1024  # 5MB
_MAX_OUTPUT_CHARS = 10_000
_REQUEST_TIMEOUT = 15

_USER_AGENT = "Mozilla/5.0 (compatible; chips-agent/0.2.0)"

_TAVILY_URL = "https://api.tavily.com/search"

# 禁止访问的内网/本地地址
_BLOCKED_NETWORKS = [
    "127.0.0.0/8",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "::1/128",
    "fc00::/7",
]


# ── HTML 内容提取 ──


class _HTMLTextExtractor(HTMLParser):
    """提取 HTML 中的可见文本。"""

    def __init__(self):
        super().__init__()
        self._parts = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            text = data.strip()
            if text:
                self._parts.append(text)

    def get_text(self) -> str:
        return " ".join(self._parts)


# ── SSRF 防护 ──


def _check_ssrf(url: str) -> str | None:
    """检查目标 URL 是否为内网地址。"""
    try:
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname
        if not host:
            return "错误：无法解析主机名"
        # 解析 IP 地址
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            # 域名：尝试解析后再判断
            try:
                ip = ipaddress.ip_address(socket.gethostbyname(host))
            except Exception:
                return None  # 无法解析，放行（后续连接会失败）
        for net in _BLOCKED_NETWORKS:
            if ipaddress.ip_address(ip) in ipaddress.ip_network(net):
                return f"错误：拒绝访问内网地址 {ip}"
    except Exception:
        pass
    return None


# ── Handler ──


def _fetch_handler(args) -> str:
    url = args.get("url", "").strip()
    if not url:
        return "错误：URL 不能为空"

    # 补全 scheme
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    # SSRF 检查
    blocked = _check_ssrf(url)
    if blocked:
        return blocked

    try:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
            raw = resp.read(_MAX_RESPONSE_BYTES + 1)
            if len(raw) > _MAX_RESPONSE_BYTES:
                return f"错误：响应超过 {_MAX_RESPONSE_BYTES // 1024 // 1024}MB 限制"
            content_type = resp.headers.get("Content-Type", "")

            # 检测编码
            charset = "utf-8"
            charset_match = re.search(r"charset=([\w-]+)", content_type, re.I)
            if charset_match:
                charset = charset_match.group(1)

            try:
                text = raw.decode(charset, errors="replace")
            except (LookupError, UnicodeDecodeError):
                text = raw.decode("utf-8", errors="replace")

            # 非 HTML 内容直接返回
            if "text/html" not in content_type:
                return text[:_MAX_OUTPUT_CHARS]

            # HTML → 纯文本提取
            extractor = _HTMLTextExtractor()
            extractor.feed(text)
            clean = extractor.get_text()

            if len(clean) > _MAX_OUTPUT_CHARS:
                clean = clean[:_MAX_OUTPUT_CHARS] + f"\n...(截断，共 {len(clean)} 字符)"
            return clean

    except urllib.error.HTTPError as e:
        return f"错误：HTTP {e.code} {e.reason}"
    except urllib.error.URLError as e:
        return f"错误：无法访问：{e.reason}"
    except ValueError as e:
        return f"错误：无效的 URL：{e}"
    except Exception as e:
        return f"错误：请求失败：{e}"


def _search_handler(args) -> str:
    """搜索互联网。优先使用 Tavily（若 TAVILY_API_KEY 已设置），否则回退 DuckDuckGo。"""
    query = args.get("query", "").strip()
    if not query:
        return "错误：搜索关键词不能为空"

    api_key = os.environ.get("TAVILY_API_KEY", "").strip()
    if api_key:
        return _tavily_search(query, api_key, args)

    return _ddg_search(query, args)


def _tavily_search(query: str, api_key: str, args) -> str:
    """通过 Tavily Search API 搜索。"""
    num_results = min(args.get("max_results", 10), 20)
    payload = _json.dumps({
        "api_key": api_key,
        "query": query,
        "search_depth": "basic",
        "max_results": num_results,
    }).encode()

    try:
        req = urllib.request.Request(
            _TAVILY_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")

        data = _json.loads(raw)
        results = data.get("results", [])

        if not results:
            return f"未找到 '{query}' 的搜索结果"

        lines = []
        for i, r in enumerate(results[:num_results], 1):
            title = r.get("title", "")
            url = r.get("url", "")
            content = r.get("content", "")
            lines.append(f"{i}. {title}\n   链接: {url}\n   摘要: {content[:200]}")

        total = len(results)
        suffix = f"\n... 共 {total} 条结果" if total > num_results else ""
        return "\n\n".join(lines) + suffix

    except urllib.error.HTTPError as e:
        return f"错误：搜索服务 HTTP {e.code}"
    except urllib.error.URLError as e:
        return f"错误：无法访问搜索服务：{e.reason}"
    except _json.JSONDecodeError:
        return "错误：搜索服务返回了无法解析的响应"
    except Exception as e:
        return f"错误：搜索失败：{e}"


def _ddg_search(query: str, args) -> str:
    """使用 DuckDuckGo Lite 搜索作为回退。"""
    search_url = f"https://lite.duckduckgo.com/lite/?q={urllib.parse.quote(query)}"
    num_results = min(args.get("max_results", 10), 20)

    try:
        req = urllib.request.Request(search_url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        results = _parse_ddg_results(html)
        if not results:
            return f"未找到 '{query}' 的搜索结果"

        lines = []
        for i, (title, snippet, link) in enumerate(results[:num_results], 1):
            lines.append(f"{i}. {title}\n   链接: {link}\n   摘要: {snippet[:200]}")

        total = len(results)
        suffix = f"\n... 共 {total} 条结果" if total > num_results else ""
        return "\n\n".join(lines) + suffix

    except urllib.error.HTTPError as e:
        return f"错误：搜索服务 HTTP {e.code}"
    except urllib.error.URLError as e:
        return f"错误：无法访问搜索服务：{e.reason}"
    except Exception as e:
        return f"错误：搜索失败：{e}"


def _parse_ddg_results(html: str) -> list[tuple[str, str, str]]:
    """解析 DuckDuckGo Lite 搜索结果页。

    返回 [(title, snippet, url), ...]。
    """
    results: list[tuple[str, str, str]] = []
    # DDG Lite 用 <tr class="result"> 包裹每条结果
    # 每个 result 行内: <a rel="nofollow" href="URL">TITLE</a> 后跟摘要文本
    result_blocks = re.findall(
        r'<tr[^>]*class="result"[^>]*>(.*?)</tr>',
        html,
        re.DOTALL | re.I,
    )

    for block in result_blocks:
        # 提取 URL 和标题
        link_match = re.search(r'<a[^>]+href="([^"]+)"[^>]*>([^<]+)</a>', block)
        if not link_match:
            continue
        url = link_match.group(1)
        title = link_match.group(2).strip()

        # 提取摘要（<span style="...">...</span>）
        snippet_match = re.search(r'<span[^>]*class="result-snippet"[^>]*>(.*?)</span>', block, re.DOTALL | re.I)
        if not snippet_match:
            snippet_match = re.search(r"<br\s*/?>(.*?)(?:</td>|$)", block, re.DOTALL)
        snippet = ""
        if snippet_match:
            snippet = re.sub(r"<[^>]+>", "", snippet_match.group(1)).strip()

        # 相对 URL 补全
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("/"):
            url = "https://lite.duckduckgo.com" + url

        results.append((title, snippet, url))

    return results


# ── Register ──

registry.register(
    name="web_fetch",
    toolset="core",
    schema={
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "获取网页内容，返回纯文本。自动提取 HTML 正文、跳过内网地址、限制响应大小为 5MB。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "要获取的 URL（http/https），可不带 scheme 自动补全 https://",
                    },
                },
                "required": ["url"],
            },
        },
    },
    handler=_fetch_handler,
)

registry.register(
    name="web_search",
    toolset="core",
    schema={
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "搜索互联网，返回标题、链接和摘要。使用 DuckDuckGo 搜索，无需配置 API Key。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "最大返回结果数（1-20，默认 10）",
                    },
                },
                "required": ["query"],
            },
        },
    },
    handler=_search_handler,
)
