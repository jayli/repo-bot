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


def test_synthesizer_system_prompt_contains_evidence_rules():
    synthesizer = load_module("prompts.synthesizer")

    system = synthesizer.build_system_prompt("dependency_relation")

    assert "repo/path:Lx" in system
    assert "不要编造" in system
    assert "实事求是" in system
    assert "依赖链路" in system
