import importlib.util
from pathlib import Path
import sys


def load_module(name):
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    spec = importlib.util.spec_from_file_location(name, root / (name.replace(".", "/") + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def make_backends(agent_loop, fake):
    return agent_loop.RetrievalBackends(
        fake.search_sourcebot,
        fake.search_qdrant,
        fake.search_ast_structure,
        fake.search_graph_relations,
        fake.read_file_content,
        fake.read_manifest,
        fake.local_tool_list,
        fake.local_tool_grep,
        fake.local_tool_read,
        fake.llm_plan,
    )


def test_expand_queries_for_dependency_relation_adds_backend_specific_sourcebot_terms():
    models = load_module("retrieval.models")
    agent_loop = load_module("retrieval.agent_loop")
    plan = models.RetrievalPlan(
        "dependency_relation",
        "dependency_relation",
        entities={"subject": "block-proxy", "object": "anyproxy", "raw_terms": ["block-proxy", "anyproxy"]},
        queries={"sourcebot": ["block-proxy", "anyproxy"], "qdrant": ["block-proxy 是怎样依赖 anyproxy 的"], "ast": [], "graph": []},
        precision={"enabled": True, "patterns": ["block-proxy", "anyproxy"], "read_manifests": True},
    )

    queries = agent_loop.expand_queries("block-proxy 是怎样依赖 anyproxy 的", plan)

    assert "require('anyproxy')" in queries["sourcebot"]
    assert 'require("anyproxy")' in queries["sourcebot"]
    assert "from 'anyproxy'" in queries["sourcebot"]
    assert "dependencies" in queries["sourcebot"]
    assert "block-proxy 是怎样依赖 anyproxy 的" in queries["qdrant"]


def test_unique_keep_order_dedupes_without_reordering():
    agent_loop = load_module("retrieval.agent_loop")
    assert agent_loop.unique_keep_order(["a", "b", "a", "", "c"]) == ["a", "b", "c"]


def test_expand_queries_handles_no_object():
    models = load_module("retrieval.models")
    agent_loop = load_module("retrieval.agent_loop")
    plan = models.RetrievalPlan(
        "implementation_location",
        "implementation_location",
        entities={"subject": "login", "raw_terms": ["login"]},
        queries={"sourcebot": ["login"], "qdrant": ["登录逻辑在哪里"], "ast": [], "graph": []},
        precision={"enabled": False},
    )

    queries = agent_loop.expand_queries("登录逻辑在哪里", plan)

    assert "login" in queries["sourcebot"]
    assert "登录逻辑在哪里" in queries["qdrant"]
