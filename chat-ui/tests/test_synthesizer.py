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

    assert "Qdrant" in system
    assert "不能单独证明直接依赖" in system
    assert "repo/path:Lx" in system
