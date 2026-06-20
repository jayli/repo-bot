import importlib.util
from pathlib import Path
import sys


def load_precision():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("retrieval.precision", root / "retrieval/precision.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_read_manifest_finds_package_json(tmp_path):
    precision = load_precision()
    repo = tmp_path / "block-proxy"
    repo.mkdir()
    (repo / "package.json").write_text('{"dependencies":{"anyproxy":"1.0.0"}}\n', encoding="utf-8")

    hits = precision.read_manifest(str(tmp_path), "block-proxy")

    assert hits[0].path == "package.json"
    assert "anyproxy" in hits[0].content


def test_grep_repo_rejects_path_escape(tmp_path):
    precision = load_precision()
    try:
        precision.grep_repo(str(tmp_path), "../outside", "anyproxy")
    except ValueError as exc:
        assert "outside REPOS_ROOT" in str(exc)
    else:
        raise AssertionError("expected path escape to fail")
