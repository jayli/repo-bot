from __future__ import annotations

from .models import EvidenceItem, RetrievalHit, RetrievalPlan


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
    if hit.source == "precision_search":
        return "confirmed by repository-local file read/search"
    if hit.source == "sourcebot":
        return "exact code search match"
    if hit.source == "qdrant":
        return "semantic code search match"
    if hit.source == "ast":
        return "structural symbol/import/call fact"
    if hit.source == "neo4j":
        return "graph relation fact"
    return "retrieval match"


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
    coverage = {}
    for source in ["sourcebot", "qdrant", "ast", "neo4j", "precision_search"]:
        used = any(hit.source == source for hit in hits)
        coverage[source] = {"used": used, "summary": "provided evidence" if used else "未提供有效证据"}
    return {
        "query": query,
        "intent": plan.intent,
        "answer_template": plan.template,
        "entities": plan.entities,
        "candidate_repos": ranked_repos,
        "evidence": [item.to_dict() for item in evidence],
        "retrieval_coverage": coverage,
        "confidence": _confidence(evidence),
        "known_gaps": [],
    }
