from __future__ import annotations

from dataclasses import dataclass
import os
import re
from typing import Any


@dataclass
class SourcebotSearchResult:
    items: list[dict[str, Any]]
    error: str | None = None


def _snippet(text: str, max_len: int = 160) -> str:
    text = " ".join((text or "").split())
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "..."


def search_sourcebot(
    query: str,
    top_k: int = 10,
    requests_module: Any | None = None,
) -> SourcebotSearchResult:
    if requests_module is None:
        import requests as requests_module

    base_url = os.environ.get("SOURCEBOT_URL", "http://sourcebot:3000").rstrip("/")
    url = f"{base_url}/api/search"
    headers = {
        "X-Org-Domain": os.environ.get("SOURCEBOT_ORG_DOMAIN", "~"),
    }
    api_key = os.environ.get("SOURCEBOT_API_KEY")
    if api_key:
        headers["X-Sourcebot-Api-Key"] = api_key
    result = _search_sourcebot_once(requests_module, url, headers, query, top_k)
    if result.items or result.error:
        return result

    fallback_query = _fallback_query(query)
    if fallback_query and fallback_query != query:
        return _search_sourcebot_once(requests_module, url, headers, fallback_query, top_k)
    return result


def _search_sourcebot_once(
    requests_module: Any,
    url: str,
    headers: dict[str, str],
    query: str,
    top_k: int,
) -> SourcebotSearchResult:
    try:
        resp = requests_module.post(
            url,
            json={"query": query, "matches": top_k, "contextLines": 3, "whole": False},
            headers=headers,
            timeout=10,
        )
    except Exception as exc:
        return SourcebotSearchResult([], f"Sourcebot request failed: {exc}")

    try:
        data = resp.json()
    except Exception:
        status = getattr(resp, "status_code", "?")
        body = _snippet(getattr(resp, "text", ""))
        return SourcebotSearchResult([], f"Sourcebot {url} returned HTTP {status}, non-JSON body: {body}")

    if getattr(resp, "status_code", 200) >= 400:
        return SourcebotSearchResult([], f"Sourcebot {url} returned HTTP {resp.status_code}: {_snippet(str(data))}")

    return SourcebotSearchResult(_parse_search_files(data, top_k))


def _fallback_query(query: str) -> str | None:
    tokens = re.findall(r"[A-Za-z0-9_.:/#-]{3,}", query)
    stopwords = {
        "the", "and", "for", "with", "from", "what", "how", "why", "where",
        "是什么", "仓库", "什么", "怎么", "如何", "为什么", "哪里",
    }
    keywords = [token for token in tokens if token.lower() not in stopwords]
    return " ".join(dict.fromkeys(keywords)) or None


def _parse_search_files(data: dict[str, Any], top_k: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for file_match in data.get("files", []):
        file_name = file_match.get("fileName", {})
        path = file_name.get("text", "") if isinstance(file_name, dict) else str(file_name)
        repo = file_match.get("repository", "")
        language = file_match.get("language", "")
        for chunk in file_match.get("chunks", []):
            start_line = chunk.get("contentStart", {}).get("lineNumber", 1)
            content = chunk.get("content", "")
            line_count = max(1, len(content.rstrip("\n").splitlines()))
            results.append({
                "source": "sourcebot",
                "repo": repo,
                "path": path,
                "line": f"L{start_line}",
                "start_line": start_line,
                "end_line": start_line + line_count - 1,
                "language": language,
                "content": content,
            })
            if len(results) >= top_k:
                return results
    return results
