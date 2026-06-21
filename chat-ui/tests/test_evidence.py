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


def test_retrieval_hit_to_dict_keeps_location():
    models = load_module("retrieval.models")
    hit = models.RetrievalHit(
        source="sourcebot",
        repo="block-proxy",
        path="src/proxy/server.js",
        line_range="L3-L12",
        content="const anyproxy = require('anyproxy')",
        strength="exact_text",
    )

    assert hit.to_dict()["repo"] == "block-proxy"
    assert hit.to_dict()["line_range"] == "L3-L12"


def test_build_evidence_pack_assigns_high_confidence_for_two_strong_layers():
    models = load_module("retrieval.models")
    evidence = load_module("retrieval.evidence")
    plan = models.RetrievalPlan(
        "dependency_relation",
        "dependency_relation",
        entities={"subject": "block-proxy", "object": "anyproxy"},
    )
    hits = [
        models.RetrievalHit("precision_search", "block-proxy", "package.json", "L1-L10", "anyproxy", "file_confirmed"),
        models.RetrievalHit("sourcebot", "block-proxy", "src/server.js", "L3", "require('anyproxy')", "exact_text"),
    ]

    pack = evidence.build_evidence_pack("block-proxy 是怎样依赖 anyproxy 的", plan, hits, [{"repo": "block-proxy", "score": 20}])

    assert pack["confidence"] == "high"
    assert pack["evidence"][0]["tier"] == "strong"
    assert "retrieval_coverage" not in pack
    assert "known_gaps" not in pack


def test_build_evidence_pack_exposes_candidate_repo_roots(monkeypatch):
    models = load_module("retrieval.models")
    evidence = load_module("retrieval.evidence")
    monkeypatch.setenv("REPOS_ROOT", "/Users/hfy/jayli")
    plan = models.RetrievalPlan("dependency_relation", "dependency_relation")
    hits = [
        models.RetrievalHit("sourcebot", "block-proxy", "package.json", "L9", "@bachi/anyproxy", "exact_text"),
        models.RetrievalHit("sourcebot", "anyproxy", "package.json", "L2", "@bachi/anyproxy", "exact_text"),
    ]

    pack = evidence.build_evidence_pack(
        "block-proxy 是怎样依赖 anyproxy 的",
        plan,
        hits,
        [{"repo": "block-proxy", "score": 10}, {"repo": "anyproxy", "score": 8}],
    )

    assert pack["repo_roots"] == {
        "block-proxy": "/Users/hfy/jayli/block-proxy",
        "anyproxy": "/Users/hfy/jayli/anyproxy",
    }
