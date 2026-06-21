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
        self.llm_plan_context = plan.entities.get("round1_context", "") if hasattr(plan, "entities") else ""
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

    result = agent_loop.run_retrieval_loop("block-proxy 是怎样依赖 anyproxy 的", repos_root="/tmp/repos", backends=backends, max_rounds=2)

    # LLM rewrites ("ProxyServer") appear in round 2 follow-up sourcebot queries
    assert "ProxyServer" in fake.sourcebot_queries
    # LLM is called after round 1, so it received a plan with round1_context
    assert hasattr(fake, "llm_plan_context")
    assert len(result.rounds) <= 2


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
        self.llm_plan_context = plan.entities.get("round1_context", "") if hasattr(plan, "entities") else ""
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

    assert len(result.rounds) <= 2  # round 2 may start but stops with 0 new_hits


def test_observe_gaps_emits_missing_term_for_non_dependency():
    models = load_module("retrieval.models")
    agent_loop = load_module("retrieval.agent_loop")
    plan = models.RetrievalPlan(
        "generic_code_answer",
        "generic_code_answer",
        entities={"raw_terms": ["科学上网", "配置", "原理"], "symbols": ["科学上网", "配置", "原理"]},
        precision={"enabled": False},
    )

    actions = agent_loop.observe_gaps(plan, hits=[], ranked_repos=[{"repo": "passwall-any", "score": 10}], confirmed_repos={"passwall-any"})

    assert agent_loop.GapAction("MissingTerm", repo="passwall-any", package_name="科学上网", priority=30) in actions
    assert agent_loop.GapAction("MissingManifest", repo="passwall-any", priority=10) in actions


def test_should_run_precision_search_when_repos_exist_regardless_of_enabled():
    models = load_module("retrieval.models")
    ranking = load_module("retrieval.ranking")
    plan = models.RetrievalPlan(
        "generic_code_answer",
        "generic_code_answer",
        entities={"raw_terms": ["passwall"]},
        precision={"enabled": False},
    )

    assert ranking.should_run_precision_search(plan, [{"repo": "passwall-any", "score": 10}]) is True
    assert ranking.should_run_precision_search(plan, []) is False


def test_missing_term_gap_converts_to_local_tool_grep_in_loop():
    agent_loop = load_module("retrieval.agent_loop")
    fake = FakeBackendsWithHits()
    fake.llm_plan_result = {"intent": "generic_code_answer", "entity_hints": {"likely_repo": "passwall-any"}}

    # Override sourcebot to return a hit for passwall-any so it enters confirmed_repos
    original_search = fake.search_sourcebot
    def custom_search(q, top_k):
        if "passwall" in q.lower():
            return [{"repo": "passwall-any", "path": "README.md", "line": "L1", "start_line": 1, "end_line": 5, "content": "# passwall-any config"}]
        return original_search(q, top_k)
    fake.search_sourcebot = custom_search

    backends = make_backends(agent_loop, fake)

    result = agent_loop.run_retrieval_loop("passwall-any 的 server 配置原理是什么", repos_root="/tmp/repos", backends=backends, max_rounds=2)

    # raw_terms include "passwall-any" and "server" → MissingTerm gaps → local_tool_grep
    grep_patterns = [c.get("pattern") for c in fake.grep_calls]
    assert grep_patterns  # should have called grep for raw_terms
    # Should have local_actions for MissingManifest + MissingTerm
    all_actions = [a for r in result.rounds for a in r.local_actions]
    assert len(all_actions) > 0


def test_llm_plan_receives_round1_context():
    agent_loop = load_module("retrieval.agent_loop")
    fake = FakeBackendsWithHits()
    fake.llm_plan_result = {"query_rewrites": {"sourcebot": ["refined-query"]}}
    backends = make_backends(agent_loop, fake)

    result = agent_loop.run_retrieval_loop("block-proxy 是怎样依赖 anyproxy 的", repos_root="/tmp/repos", backends=backends, max_rounds=2)

    # LLM should have been called after round 1 with context about confirmed repos
    assert hasattr(fake, "llm_plan_context")
    assert "block-proxy" in fake.llm_plan_context
    assert "anyproxy" in fake.llm_plan_context
    # LLM's refined query should appear in sourcebot searches
    assert "refined-query" in fake.sourcebot_queries
    assert len(result.rounds) == 2


def test_round2_uses_llm_enriched_queries():
    agent_loop = load_module("retrieval.agent_loop")
    fake = FakeBackendsWithHits()
    fake.llm_plan_result = {
        "intent": "call_chain",
        "query_rewrites": {"sourcebot": ["ProxyServer.call", "MITM_handler"], "qdrant": ["proxy server call chain"]},
    }
    backends = make_backends(agent_loop, fake)

    result = agent_loop.run_retrieval_loop("block-proxy 是怎样依赖 anyproxy 的", repos_root="/tmp/repos", backends=backends, max_rounds=2)

    # Round 1: rule-based queries (original terms)
    # Round 2: LLM-enriched queries
    assert "ProxyServer.call" in fake.sourcebot_queries
    assert "MITM_handler" in fake.sourcebot_queries
    # Intent should be updated by LLM
    assert result.plan.intent == "call_chain"
