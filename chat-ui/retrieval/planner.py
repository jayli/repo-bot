from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import Any

from .models import RetrievalPlan


TOKEN_RE = re.compile(r"@[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+|[A-Za-z0-9_./:-]*[A-Za-z][A-Za-z0-9_./:-]*")
VALID_INTENTS = {"dependency_relation", "call_chain", "implementation_location", "troubleshooting", "generic_code_answer"}

DOMAIN_FACETS = [
    (
        ("科学上网", "passwall", "openwrt", "代理", "节点", "订阅"),
        {
            "facets": [
                "passwall",
                "openwrt-passwall",
                "luci-app-passwall",
                "0_default_config",
                "subscribe.lua",
                "节点",
                "订阅",
                "代理",
                "透明代理",
            ],
            "qdrant": ["OpenWrt PassWall 科学上网 配置 节点 订阅 透明代理"],
            "repo_candidates": ["passwall-any"],
        },
    ),
]

PASSWALL_CONFIG_FACETS = ("config", "uci", "global", "node")


def extract_terms(query: str) -> list[str]:
    terms: list[str] = []
    for token in TOKEN_RE.findall(query):
        if len(token) < 2:
            continue
        if token not in terms:
            terms.append(token)
    return terms


def expand_domain_facets(query: str) -> dict[str, list[str]]:
    facets: list[str] = []
    qdrant: list[str] = []
    repo_candidates: list[str] = []
    query_lower = query.lower()
    for triggers, expansion in DOMAIN_FACETS:
        if not any(trigger.lower() in query_lower for trigger in triggers):
            continue
        facets = _extend_unique(facets, expansion["facets"], limit=20)
        qdrant = _extend_unique(qdrant, expansion["qdrant"], limit=8)
        repo_candidates = _extend_unique(repo_candidates, expansion["repo_candidates"], limit=8)
    if facets and any(trigger in query_lower for trigger in ("配置", "config", "uci")):
        facets = _extend_unique(facets, list(PASSWALL_CONFIG_FACETS), limit=20)
    return {
        "facets": facets,
        "qdrant": qdrant,
        "repo_candidates": repo_candidates,
    }


def classify_query(query: str) -> str:
    if any(word in query for word in ["怎样依赖", "依赖", "引入", "使用了", "什么关系", "关系"]):
        return "dependency_relation"
    if any(word in query for word in ["调用链", "怎么调用", "传到哪里", "流程"]):
        return "call_chain"
    if any(word in query for word in ["在哪里", "哪个文件", "实现位置"]):
        return "implementation_location"
    if any(word in query for word in ["为什么", "报错", "没结果", "怎么修"]):
        return "troubleshooting"
    return "generic_code_answer"


def plan_query(query: str) -> RetrievalPlan:
    intent = classify_query(query)
    terms = extract_terms(query)
    domain = expand_domain_facets(query)
    search_facets = domain["facets"]
    raw_terms = _extend_unique(terms, search_facets, limit=20)
    entities: dict[str, object] = {"raw_terms": raw_terms, "symbols": raw_terms}
    if search_facets:
        entities["search_facets"] = search_facets
    if domain["repo_candidates"]:
        entities["repo_candidates"] = domain["repo_candidates"]
    if intent == "dependency_relation" and len(terms) >= 2:
        entities["subject"] = terms[0]
        entities["object"] = terms[1]
    elif raw_terms:
        entities["subject"] = raw_terms[0]

    sourcebot_queries = _extend_unique(terms[:5] or [query], search_facets, limit=8)
    qdrant_queries = _extend_unique([query], domain["qdrant"], limit=4)
    ast_queries = raw_terms[:5]
    graph_queries = raw_terms[:5]
    precision_enabled = intent in {"dependency_relation", "call_chain", "troubleshooting"}

    return RetrievalPlan(
        intent=intent,
        template=intent,
        entities=entities,
        queries={
            "sourcebot": sourcebot_queries,
            "qdrant": qdrant_queries,
            "ast": ast_queries,
            "graph": graph_queries,
        },
        precision={
            "enabled": precision_enabled,
            "patterns": raw_terms[:8] or [query],
            "read_manifests": intent == "dependency_relation",
        },
    )


def validate_llm_plan(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def _extend_unique(current: list[Any], extra: list[Any], limit: int = 8) -> list[Any]:
    result = list(current)
    for item in extra:
        if item not in result:
            result.append(item)
        if len(result) >= limit:
            break
    return result


def merge_llm_plan(base: RetrievalPlan, llm_plan: dict[str, Any]) -> RetrievalPlan:
    queries = {key: list(value) for key, value in base.queries.items()}
    rewrites = llm_plan.get("query_rewrites", {})
    if isinstance(rewrites, dict):
        for key in ["sourcebot", "qdrant"]:
            extra = rewrites.get(key)
            if isinstance(extra, list):
                queries[key] = _extend_unique(queries.get(key, []), extra)
    precision = dict(base.precision)
    extra_precision = llm_plan.get("precision_search", {})
    if isinstance(extra_precision, dict) and isinstance(extra_precision.get("extra_patterns"), list):
        precision["patterns"] = _extend_unique(list(precision.get("patterns", [])), extra_precision["extra_patterns"])
    entities = dict(base.entities)
    entity_hints = llm_plan.get("entity_hints")
    if isinstance(entity_hints, dict):
        entities["entity_hints"] = entity_hints
    repo_candidates = llm_plan.get("repo_candidates")
    if isinstance(repo_candidates, list):
        existing = entities.get("repo_candidates", [])
        if not isinstance(existing, list):
            existing = []
        entities["repo_candidates"] = _extend_unique(existing, repo_candidates)
    search_facets = llm_plan.get("search_facets")
    if isinstance(search_facets, list):
        existing = entities.get("search_facets", [])
        if not isinstance(existing, list):
            existing = []
        entities["search_facets"] = _extend_unique(existing, search_facets, limit=20)
    intent = llm_plan.get("intent")
    if isinstance(intent, str) and intent in VALID_INTENTS:
        return replace(base, intent=intent, template=intent, queries=queries, precision=precision, entities=entities)
    return replace(base, queries=queries, precision=precision, entities=entities)
