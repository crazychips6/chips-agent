"""geo 工具 — 获取当前位置信息

使用 IP 地理定位服务获取用户的大致位置。
零外部依赖（stdlib urllib），无需 API key。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from tool.registry import registry

_GEO_API_URL = "http://ip-api.com/json/?fields=status,message,country,regionName,city,lat,lon,timezone,isp,query,org,as"

_REQUEST_TIMEOUT = 10


def _handle(args: dict) -> str:
    try:
        req = urllib.request.Request(
            _GEO_API_URL,
            headers={"User-Agent": "chips-agent/0.3.0"},
        )
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")

        data = json.loads(raw)

        if data.get("status") != "success":
            msg = data.get("message", "unknown")
            return json.dumps({"error": f"定位失败: {msg}"})

        return json.dumps({
            "city": data.get("city", ""),
            "region": data.get("regionName", ""),
            "country": data.get("country", ""),
            "latitude": data.get("lat"),
            "longitude": data.get("lon"),
            "timezone": data.get("timezone", ""),
            "isp": data.get("isp", ""),
            "ip": data.get("query", ""),
        }, ensure_ascii=False)

    except urllib.error.HTTPError as e:
        return json.dumps({"error": f"定位服务 HTTP {e.code}"})
    except urllib.error.URLError as e:
        return json.dumps({"error": f"无法访问定位服务: {e.reason}"})
    except json.JSONDecodeError:
        return json.dumps({"error": "定位服务返回了无法解析的响应"})
    except Exception as e:
        return json.dumps({"error": f"定位失败: {e}"})


GEO_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_location",
        "description": "获取你的当前位置信息（城市、地区、国家、经纬度、时区）。"
                       "基于 IP 地理定位，无精度要求。不需要任何参数。",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
}

registry.register(
    name="get_location",
    toolset="geo",
    schema=GEO_SCHEMA,
    handler=_handle,
)
