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
