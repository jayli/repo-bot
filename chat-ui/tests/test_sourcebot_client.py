import importlib.util
from pathlib import Path
import sys


def load_sourcebot_client():
    module_path = Path(__file__).resolve().parents[1] / "sourcebot_client.py"
    spec = importlib.util.spec_from_file_location("sourcebot_client", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    status_code = 404
    text = "<html><p>Page not found</p></html>"

    def json(self):
        raise ValueError("not json")


class FakeRequests:
    last_call = None

    def post(self, url, json, headers, timeout):
        self.last_call = {"url": url, "json": json, "timeout": timeout, "headers": headers}
        return FakeResponse()


def test_search_sourcebot_reports_non_json_http_error(monkeypatch):
    client = load_sourcebot_client()
    monkeypatch.setenv("SOURCEBOT_URL", "http://sourcebot:3000")

    result = client.search_sourcebot("passwall 节点配置", top_k=10, requests_module=FakeRequests())

    assert result.items == []
    assert "HTTP 404" in result.error
    assert "/api/search" in result.error


class SuccessfulResponse:
    status_code = 200
    text = "{}"

    def json(self):
        return {
            "files": [
                {
                    "repository": "github.com/jayli/passwall-any",
                    "fileName": {"text": "README.md"},
                    "language": "Markdown",
                    "chunks": [
                        {
                            "content": "节点配置示例",
                            "contentStart": {"lineNumber": 12},
                            "matchRanges": [],
                        }
                    ],
                }
            ]
        }


class SuccessfulRequests:
    def __init__(self):
        self.last_call = None

    def post(self, url, json, headers, timeout):
        self.last_call = {"url": url, "json": json, "headers": headers, "timeout": timeout}
        return SuccessfulResponse()


def test_search_sourcebot_uses_v4_api_and_parses_files(monkeypatch):
    client = load_sourcebot_client()
    fake_requests = SuccessfulRequests()
    monkeypatch.setenv("SOURCEBOT_URL", "http://sourcebot:3000")
    monkeypatch.setenv("SOURCEBOT_ORG_DOMAIN", "~")
    monkeypatch.setenv("SOURCEBOT_API_KEY", "sb_test")

    result = client.search_sourcebot("passwall 节点配置", top_k=10, requests_module=fake_requests)

    assert fake_requests.last_call["url"] == "http://sourcebot:3000/api/search"
    assert fake_requests.last_call["json"] == {
        "query": "passwall 节点配置",
        "matches": 10,
        "contextLines": 3,
        "whole": False,
    }
    assert fake_requests.last_call["headers"]["X-Org-Domain"] == "~"
    assert fake_requests.last_call["headers"]["X-Sourcebot-Api-Key"] == "sb_test"
    assert result.error is None
    assert result.items == [
        {
            "source": "sourcebot",
            "repo": "github.com/jayli/passwall-any",
            "path": "README.md",
            "line": "L12",
            "start_line": 12,
            "end_line": 12,
            "language": "Markdown",
            "content": "节点配置示例",
        }
    ]


class FallbackRequests:
    def __init__(self):
        self.queries = []

    def post(self, url, json, headers, timeout):
        self.queries.append(json["query"])
        if json["query"] == "passwall":
            return SuccessfulResponse()
        return EmptyResponse()


class EmptyResponse:
    status_code = 200
    text = "{}"

    def json(self):
        return {"files": []}


def test_search_sourcebot_falls_back_to_keywords_for_natural_language(monkeypatch):
    client = load_sourcebot_client()
    fake_requests = FallbackRequests()
    monkeypatch.setenv("SOURCEBOT_URL", "http://sourcebot:3000")
    monkeypatch.setenv("SOURCEBOT_ORG_DOMAIN", "~")

    result = client.search_sourcebot("passwall 是什么仓库", top_k=10, requests_module=fake_requests)

    assert fake_requests.queries == ["passwall 是什么仓库", "passwall"]
    assert len(result.items) == 1
    assert result.items[0]["repo"] == "github.com/jayli/passwall-any"
