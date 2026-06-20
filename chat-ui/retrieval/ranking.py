from __future__ import annotations

from .models import RetrievalHit, RetrievalPlan


def _hit_score(hit: RetrievalHit) -> int:
    text = f"{hit.path}\n{hit.content}".lower()
    if hit.source == "sourcebot" and any(token in text for token in ["require(", "import ", "dependencies", "devdependencies"]):
        return 10
    if hit.source == "ast" and hit.strength == "structure":
        return 8
    if hit.source == "sourcebot":
        return 7
    if hit.source == "neo4j":
        return 6
    if hit.source == "qdrant" and (hit.score or 0) >= 0.75:
        return 4
    if hit.source == "qdrant":
        return 2
    return 1


def rank_repositories(hits: list[RetrievalHit]) -> list[dict]:
    scores: dict[str, int] = {}
    reasons: dict[str, list[str]] = {}
    for hit in hits:
        if not hit.repo:
            continue
        score = _hit_score(hit)
        if "/readme" in hit.path.lower() or hit.path.lower().endswith("readme.md"):
            score -= 5
        scores[hit.repo] = scores.get(hit.repo, 0) + score
        reasons.setdefault(hit.repo, []).append(f"{hit.source}:{hit.path}:{hit.line_range}")
    return [
        {"repo": repo, "score": score, "reasons": reasons.get(repo, [])}
        for repo, score in sorted(scores.items(), key=lambda item: item[1], reverse=True)
    ]


def should_run_precision_search(plan: RetrievalPlan, ranked_repos: list[dict]) -> bool:
    return bool(plan.precision.get("enabled") and ranked_repos)
