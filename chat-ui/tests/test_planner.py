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


def load_planner():
    return load_module("retrieval.planner")


def test_dependency_query_extracts_subject_and_object():
    planner = load_planner()

    plan = planner.plan_query("block-proxy 是怎样依赖 anyproxy 的")

    assert plan.intent == "dependency_relation"
    assert plan.template == "dependency_relation"
    assert plan.entities["subject"] == "block-proxy"
    assert plan.entities["object"] == "anyproxy"
    assert "anyproxy" in plan.queries["sourcebot"]
    assert plan.precision["enabled"] is True


def test_location_query_skips_precision_by_default():
    planner = load_planner()

    plan = planner.plan_query("登录逻辑在哪里")

    assert plan.intent == "implementation_location"
    assert plan.precision["enabled"] is False


def test_validate_llm_planner_rejects_non_json():
    planner = load_planner()

    assert planner.validate_llm_plan("not json") == {}


def test_merge_llm_plan_adds_queries_without_replacing_intent():
    planner = load_planner()
    base = planner.plan_query("block-proxy 是怎样依赖 anyproxy 的")
    merged = planner.merge_llm_plan(base, {"query_rewrites": {"sourcebot": ["ProxyServer"]}})

    assert merged.intent == "dependency_relation"
    assert "ProxyServer" in merged.queries["sourcebot"]


def test_merge_llm_plan_preserves_entity_hints():
    planner = load_planner()
    base = planner.plan_query("koa 是怎样依赖 koa-router 的")

    merged = planner.merge_llm_plan(base, {"entity_hints": {"likely_repo": "koa", "likely_dependency": "koa-router"}})

    assert merged.entities["entity_hints"]["likely_repo"] == "koa"
    assert merged.entities["entity_hints"]["likely_dependency"] == "koa-router"
