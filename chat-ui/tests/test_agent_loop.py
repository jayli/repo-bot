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


class FakeBackends:
    def __init__(self):
        self.sourcebot_queries = []
        self.qdrant_queries = []
        self.ast_queries = []
        self.graph_queries = []
        self.llm_plan_result = None

    def search_sourcebot(self, query, top_k):
        self.sourcebot_queries.append(query)
        return []

    def search_qdrant(self, query, top_k):
        self.qdrant_queries.append(query)
        return []

    def search_ast_structure(self, query, results, limit):
        self.ast_queries.append(query)
        return []

    def search_graph_relations(self, query, results, limit):
        self.graph_queries.append(query)
        return []

    def read_file_content(self, repo, path, start_line, end_line):
        return ""

    def read_manifest(self, repos_root, repo):
        return []

    def local_tool_list(self, *args, **kwargs):
        return []

    def local_tool_grep(self, *args, **kwargs):
        return []

    def local_tool_read(self, *args, **kwargs):
        return None

    def llm_plan(self, question, plan):
        return self.llm_plan_result or {"query_rewrites": {"sourcebot": ["ProxyServer"], "qdrant": ["MITM proxy engine"]}}


def test_run_retrieval_loop_executes_llm_plan_rewrites():
    agent_loop = load_module("retrieval.agent_loop")
    fake = FakeBackends()
    backends = agent_loop.RetrievalBackends(
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

    agent_loop.run_retrieval_loop("block-proxy 是怎样依赖 anyproxy 的", repos_root="/tmp/repos", backends=backends, max_rounds=1)

    assert "ProxyServer" in fake.sourcebot_queries
    assert "MITM proxy engine" in fake.qdrant_queries


def test_run_retrieval_loop_searches_expanded_sourcebot_queries():
    agent_loop = load_module("retrieval.agent_loop")
    fake = FakeBackends()
    backends = agent_loop.RetrievalBackends(
        fake.search_sourcebot,
        fake.search_qdrant,
        fake.search_ast_structure,
        fake.search_graph_relations,
        fake.read_file_content,
        fake.read_manifest,
        fake.local_tool_list,
        fake.local_tool_grep,
        fake.local_tool_read,
    )

    result = agent_loop.run_retrieval_loop("block-proxy 是怎样依赖 anyproxy 的", repos_root="/tmp/repos", backends=backends, max_rounds=1)

    assert "block-proxy 是怎样依赖 anyproxy 的" not in fake.sourcebot_queries
    assert "anyproxy" in fake.sourcebot_queries
    assert "require('anyproxy')" in fake.sourcebot_queries
    assert result.confirmed_repos == set()
    assert len(result.rounds) == 1


def test_likely_repo_hint_does_not_trigger_local_tools_until_confirmed():
    agent_loop = load_module("retrieval.agent_loop")
    fake = FakeBackends()
    fake.llm_plan_result = {"entity_hints": {"likely_repo": "koa"}}
    backends = make_backends(agent_loop, fake)

    result = agent_loop.run_retrieval_loop("koa 是怎样依赖 koa-router 的", repos_root="/tmp/repos", backends=backends, max_rounds=1)

    actions = [action for r in result.rounds for action in r.local_actions]
    assert result.confirmed_repos == set()
    assert actions == []


class FakeBackendsWithHits:
    def __init__(self):
        self.sourcebot_queries = []
        self.qdrant_queries = []
        self.ast_queries = []
        self.graph_queries = []
        self.grep_calls = []
        self.manifest_calls = []
        self.llm_plan_result = None

    def search_sourcebot(self, query, top_k):
        self.sourcebot_queries.append(query)
        if "anyproxy" in query:
            return [{"repo": "anyproxy", "path": "package.json", "line": "L1", "start_line": 1, "end_line": 5, "content": '{"name":"@bachi/anyproxy"}'}]
        if "block-proxy" in query:
            return [{"repo": "block-proxy", "path": "proxy/proxy.js", "line": "L1", "start_line": 1, "end_line": 3, "content": "const AnyProxy = require('@bachi/anyproxy')"}]
        return []

    def search_qdrant(self, query, top_k):
        self.qdrant_queries.append(query)
        return []

    def search_ast_structure(self, query, results, limit):
        self.ast_queries.append(query)
        return []

    def search_graph_relations(self, query, results, limit):
        self.graph_queries.append(query)
        return []

    def read_file_content(self, repo, path, start_line, end_line):
        return ""

    def read_manifest(self, repos_root, repo):
        self.manifest_calls.append(repo)
        return []

    def local_tool_list(self, *args, **kwargs):
        return []

    def local_tool_grep(self, *args, **kwargs):
        self.grep_calls.append(kwargs)
        return []

    def local_tool_read(self, *args, **kwargs):
        return None

    def llm_plan(self, question, plan):
        return self.llm_plan_result or {}


def test_precision_targets_include_confirmed_likely_repos():
    agent_loop = load_module("retrieval.agent_loop")
    fake = FakeBackendsWithHits()
    fake.llm_plan_result = {"entity_hints": {"likely_repo": "anyproxy"}}
    backends = make_backends(agent_loop, fake)

    result = agent_loop.run_retrieval_loop("block-proxy 是怎样依赖 anyproxy 的", repos_root="/tmp/repos", backends=backends, max_rounds=1)

    assert "block-proxy" in result.confirmed_repos
    assert "anyproxy" in result.confirmed_repos
    actions = [action for r in result.rounds for action in r.local_actions]
    assert agent_loop.LocalAction("read_manifest", "block-proxy") in actions
    assert agent_loop.LocalAction("read_manifest", "anyproxy") in actions


def test_synthetic_repo_not_a_precision_target():
    agent_loop = load_module("retrieval.agent_loop")
    fake = FakeBackends()
    backends = make_backends(agent_loop, fake)

    result = agent_loop.run_retrieval_loop("block-proxy 是怎样依赖 anyproxy 的", repos_root="/tmp/repos", backends=backends, max_rounds=1)

    for r in result.rounds:
        for action in r.local_actions:
            assert action.repo != "ast-service"


def test_observe_gaps_emits_missing_manifest_for_confirmed_repo():
    models = load_module("retrieval.models")
    agent_loop = load_module("retrieval.agent_loop")
    plan = models.RetrievalPlan(
        "dependency_relation",
        "dependency_relation",
        entities={"subject": "block-proxy", "object": "anyproxy"},
        precision={"enabled": True, "read_manifests": True},
    )

    actions = agent_loop.observe_gaps(plan, hits=[], ranked_repos=[{"repo": "block-proxy", "score": 10}], confirmed_repos={"block-proxy"})

    assert agent_loop.GapAction("MissingManifest", repo="block-proxy", priority=10) in actions


def test_observe_gaps_stops_emitting_manifest_after_hit_exists():
    models = load_module("retrieval.models")
    agent_loop = load_module("retrieval.agent_loop")
    plan = models.RetrievalPlan(
        "dependency_relation",
        "dependency_relation",
        entities={"subject": "block-proxy", "object": "anyproxy"},
        precision={"enabled": True, "read_manifests": True},
    )
    hits = [models.RetrievalHit("precision_search", "block-proxy", "package.json", "L1-L10", "{}", "file_confirmed")]

    actions = agent_loop.observe_gaps(plan, hits=hits, ranked_repos=[{"repo": "block-proxy", "score": 10}], confirmed_repos={"block-proxy"})

    assert all(action.kind != "MissingManifest" for action in actions)


def test_observe_gaps_emits_missing_import_for_dependency():
    models = load_module("retrieval.models")
    agent_loop = load_module("retrieval.agent_loop")
    plan = models.RetrievalPlan(
        "dependency_relation",
        "dependency_relation",
        entities={"subject": "block-proxy", "object": "anyproxy"},
        precision={"enabled": True, "read_manifests": True},
    )

    actions = agent_loop.observe_gaps(plan, hits=[], ranked_repos=[{"repo": "block-proxy", "score": 10}], confirmed_repos={"block-proxy"})

    assert agent_loop.GapAction("MissingImport", repo="block-proxy", package_name="anyproxy", priority=20) in actions


def test_extract_discovered_terms_from_strong_hits():
    models = load_module("retrieval.models")
    agent_loop = load_module("retrieval.agent_loop")
    hits = [
        models.RetrievalHit("sourcebot", "block-proxy", "proxy/proxy.js", "L1", "const AnyProxy = require('@bachi/anyproxy'); new AnyProxy.ProxyServer(options);", "exact_text"),
        models.RetrievalHit("qdrant", "block-proxy", "README.md", "L1", "semantic mention of ignoredTerm", "semantic"),
    ]

    terms = agent_loop.extract_discovered_terms(hits)

    assert "@bachi/anyproxy" in terms
    assert "ignoredTerm" not in terms


def test_run_retrieval_loop_calls_ast_and_graph_with_candidate_symbols():
    agent_loop = load_module("retrieval.agent_loop")
    fake = FakeBackendsWithHits()
    fake.llm_plan_result = {"query_rewrites": {"sourcebot": ["block-proxy"], "qdrant": ["block-proxy dependency"]}}
    backends = make_backends(agent_loop, fake)

    result = agent_loop.run_retrieval_loop("block-proxy 是怎样依赖 anyproxy 的", repos_root="/tmp/repos", backends=backends, max_rounds=1, use_ast=True, use_graph=True)

    assert fake.ast_queries
    assert fake.graph_queries


def test_run_retrieval_loop_stops_at_max_rounds():
    agent_loop = load_module("retrieval.agent_loop")
    fake = FakeBackendsWithHits()
    backends = make_backends(agent_loop, fake)

    result = agent_loop.run_retrieval_loop("block-proxy 是怎样依赖 anyproxy 的", repos_root="/tmp/repos", backends=backends, max_rounds=2)

    assert len(result.rounds) <= 2


def test_run_retrieval_loop_stops_early_when_no_new_work():
    agent_loop = load_module("retrieval.agent_loop")
    fake = FakeBackends()
    backends = make_backends(agent_loop, fake)

    result = agent_loop.run_retrieval_loop("登录逻辑在哪里", repos_root="/tmp/repos", backends=backends, max_rounds=3)

    assert len(result.rounds) == 1
