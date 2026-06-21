from __future__ import annotations

import os

from .models import EvidenceItem, RetrievalHit, RetrievalPlan
from .ranking import SYNTHETIC_REPOS


def evidence_tier(hit: RetrievalHit) -> str:
    if hit.source == "precision_search" and hit.content:
        return "strong"
    if hit.source == "sourcebot" and hit.strength == "exact_text" and hit.content:
        return "strong"
    if hit.source == "ast" and hit.line_range:
        return "strong"
    if hit.source in {"neo4j", "sourcebot", "ast"}:
        return "supporting"
    return "weak"


def _claim_for(hit: RetrievalHit) -> str:
    if hit.source in {"precision_search", "local_tool"}:
        return "代码内容匹配"
    if hit.source == "sourcebot":
        return "代码内容匹配"
    if hit.source == "ast":
        return "结构符号/导入/调用事实"
    if hit.source == "neo4j":
        return "图关系事实"
    if hit.source == "qdrant":
        return "语义相似匹配"
    return "检索匹配"


def _confidence(items: list[EvidenceItem]) -> str:
    strong_sources = {item.source for item in items if item.tier == "strong"}
    if len(strong_sources) >= 2:
        return "high"
    if strong_sources and any(item.tier == "supporting" for item in items):
        return "medium"
    if strong_sources:
        return "medium"
    if items:
        return "low"
    return "unconfirmed"


def _repo_roots(hits: list[RetrievalHit], ranked_repos: list[dict]) -> dict[str, str]:
    repos_root = os.path.expanduser(os.environ.get("REPOS_ROOT", "/repos"))
    repos: list[str] = []
    for item in ranked_repos:
        repo = item.get("repo")
        if repo and repo not in repos:
            repos.append(repo)
    for hit in hits:
        if hit.repo and hit.repo not in repos and hit.repo not in SYNTHETIC_REPOS:
            repos.append(hit.repo)
    return {repo: os.path.join(repos_root, repo) for repo in repos[:10]}


def build_evidence_pack(query: str, plan: RetrievalPlan, hits: list[RetrievalHit], ranked_repos: list[dict]) -> dict:
    evidence: list[EvidenceItem] = []
    for idx, hit in enumerate(hits[:30], start=1):
        evidence.append(
            EvidenceItem(
                id=f"E{idx}",
                tier=evidence_tier(hit),
                source=hit.source,
                repo=hit.repo,
                path=hit.path,
                line_range=hit.line_range,
                claim=_claim_for(hit),
                content=hit.content[:4000],
            )
        )
    return {
        "query": query,
        "intent": plan.intent,
        "answer_template": plan.template,
        "entities": plan.entities,
        "candidate_repos": ranked_repos,
        "repo_roots": _repo_roots(hits, ranked_repos),
        "evidence": [item.to_dict() for item in evidence],
        "confidence": _confidence(evidence),
    }
