import importlib.util
from pathlib import Path
import sys


def load_module(name):
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(name, root / (name.replace(".", "/") + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_rank_repositories_prefers_exact_import_hits():
    models = load_module("retrieval.models")
    ranking = load_module("retrieval.ranking")
    hits = [
        models.RetrievalHit("qdrant", "other", "README.md", "L1-L5", strength="semantic", score=0.9),
        models.RetrievalHit("sourcebot", "block-proxy", "src/server.js", "L3", "require('anyproxy')", "exact_text"),
    ]

    ranked = ranking.rank_repositories(hits)

    assert ranked[0]["repo"] == "block-proxy"
    assert ranked[0]["score"] > ranked[1]["score"]


def test_should_precision_search_requires_selected_repo():
    models = load_module("retrieval.models")
    ranking = load_module("retrieval.ranking")
    plan = models.RetrievalPlan("dependency_relation", "dependency_relation", precision={"enabled": True})

    assert ranking.should_run_precision_search(plan, []) is False


def test_rank_code_repositories_excludes_synthetic_repos():
    models = load_module("retrieval.models")
    ranking = load_module("retrieval.ranking")
    hits = [
        models.RetrievalHit("ast", "ast-service", "structure", "", "block-proxy/proxy/proxy.js:L1", "structure"),
        models.RetrievalHit("sourcebot", "block-proxy", "proxy/proxy.js", "L1", "require('@bachi/anyproxy')", "exact_text"),
    ]

    ranked = ranking.rank_code_repositories(hits)

    assert [item["repo"] for item in ranked] == ["block-proxy"]


def test_rank_repositories_keeps_existing_behavior_for_all_evidence():
    models = load_module("retrieval.models")
    ranking = load_module("retrieval.ranking")
    hits = [
        models.RetrievalHit("ast", "ast-service", "structure", "", "symbol info", "structure"),
        models.RetrievalHit("sourcebot", "block-proxy", "proxy/proxy.js", "L1", "require('@bachi/anyproxy')", "exact_text"),
    ]

    ranked = ranking.rank_repositories(hits)

    repos = [item["repo"] for item in ranked]
    assert "ast-service" in repos
    assert "block-proxy" in repos
