"""geo 工具单元测试"""

import json
from unittest.mock import patch

import tool.builtins  # noqa: F401


MOCK_SUCCESS = json.dumps({
    "status": "success",
    "country": "中国",
    "regionName": "广东省",
    "city": "深圳",
    "lat": 22.5431,
    "lon": 114.0579,
    "timezone": "Asia/Shanghai",
    "isp": "China Telecom",
    "query": "1.2.3.4",
    "org": "CT Guangdong",
    "as": "AS4134 CHINANET",
})

MOCK_FAILURE = json.dumps({
    "status": "fail",
    "message": "invalid query",
})


class TestGeoTool:
    def test_success(self):
        from tool.registry import registry

        with patch("tool.builtins.geo_tool.urllib.request") as mock_request:
            mock_request.Request.return_value = None
            mock_response = mock_request.urlopen.return_value.__enter__.return_value
            mock_response.read.return_value = MOCK_SUCCESS.encode("utf-8")

            result = registry.dispatch("get_location", {})
            data = json.loads(result)

        assert data["country"] == "中国"
        assert data["region"] == "广东省"
        assert data["city"] == "深圳"
        assert data["latitude"] == 22.5431
        assert data["longitude"] == 114.0579
        assert data["timezone"] == "Asia/Shanghai"
        assert data["isp"] == "China Telecom"
        assert data["ip"] == "1.2.3.4"

    def test_api_failure(self):
        from tool.registry import registry

        with patch("tool.builtins.geo_tool.urllib.request") as mock_request:
            mock_request.Request.return_value = None
            mock_response = mock_request.urlopen.return_value.__enter__.return_value
            mock_response.read.return_value = MOCK_FAILURE.encode("utf-8")

            result = registry.dispatch("get_location", {})
            data = json.loads(result)

        assert "error" in data

    def test_http_error(self):
        from tool.registry import registry
        from urllib.error import HTTPError

        with patch("tool.builtins.geo_tool.urllib.request") as mock_request:
            mock_request.Request.return_value = None
            mock_request.urlopen.side_effect = HTTPError(
                url="", code=429, msg="Too Many Requests", hdrs={}, fp=None
            )

            result = registry.dispatch("get_location", {})
            data = json.loads(result)

        assert "error" in data
        assert "429" in data["error"]

    def test_network_error(self):
        from tool.registry import registry
        from urllib.error import URLError

        with patch("tool.builtins.geo_tool.urllib.request") as mock_request:
            mock_request.Request.return_value = None
            mock_request.urlopen.side_effect = URLError("Network unreachable")

            result = registry.dispatch("get_location", {})
            data = json.loads(result)

        assert "error" in data
        assert "无法访问" in data["error"]

    def test_json_decode_error(self):
        from tool.registry import registry

        with patch("tool.builtins.geo_tool.urllib.request") as mock_request:
            mock_request.Request.return_value = None
            mock_response = mock_request.urlopen.return_value.__enter__.return_value
            mock_response.read.return_value = b"not json"

            result = registry.dispatch("get_location", {})
            data = json.loads(result)

        assert "error" in data
