from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import Any

from .models import RetrievalPlan


TOKEN_RE = re.compile(r"@[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+|[A-Za-z0-9_./:-]*[A-Za-z][A-Za-z0-9_./:-]*")
VALID_INTENTS = {"dependency_relation", "call_chain", "implementation_location", "troubleshooting", "generic_code_answer"}


def extract_terms(query: str) -> list[str]:
    terms: list[str] = []
    for token in TOKEN_RE.findall(query):
        if len(token) < 2:
            continue
        if token not in terms:
            terms.append(token)
    return terms


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
    entities: dict[str, object] = {"raw_terms": terms, "symbols": terms}
    if intent == "dependency_relation" and len(terms) >= 2:
        entities["subject"] = terms[0]
        entities["object"] = terms[1]
    elif terms:
        entities["subject"] = terms[0]

    sourcebot_queries = terms[:5] or [query]
    qdrant_queries = [query]
    ast_queries = terms[:5]
    graph_queries = terms[:5]
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
            "patterns": terms[:5] or [query],
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
    intent = llm_plan.get("intent")
    if isinstance(intent, str) and intent in VALID_INTENTS:
        return replace(base, intent=intent, template=intent, queries=queries, precision=precision, entities=entities)
    return replace(base, queries=queries, precision=precision, entities=entities)
