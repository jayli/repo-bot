import importlib.util
from pathlib import Path
import sys


def load_planner():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("retrieval.planner", root / "retrieval/planner.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


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
