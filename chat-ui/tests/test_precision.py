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


def test_local_tool_grep_finds_matching_lines(tmp_path):
    precision = load_precision()
    repo = tmp_path / "test-repo"
    repo.mkdir()
    (repo / "main.js").write_text("const x = 1;\nrequire('anyproxy');\nconst y = 2;\n", encoding="utf-8")
    (repo / "README.md").write_text("# Test\nanyproxy docs\n", encoding="utf-8")

    hits = precision.local_tool_grep(str(tmp_path), "test-repo", "anyproxy", include=["*.js"])

    assert len(hits) == 1
    assert hits[0].path == "main.js"
    assert "require('anyproxy')" in hits[0].content
    assert "> L2:" in hits[0].content


def test_local_tool_grep_respects_exclude_globs(tmp_path):
    precision = load_precision()
    repo = tmp_path / "test-repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "vendor").mkdir()
    (repo / "src/main.js").write_text("require('anyproxy');\n", encoding="utf-8")
    (repo / "vendor/lib.js").write_text("require('anyproxy');\n", encoding="utf-8")

    hits = precision.local_tool_grep(str(tmp_path), "test-repo", "anyproxy", exclude=["vendor/*"])

    assert len(hits) == 1
    assert hits[0].path == "src/main.js"


def test_local_tool_grep_with_context_lines(tmp_path):
    precision = load_precision()
    repo = tmp_path / "test-repo"
    repo.mkdir()
    (repo / "main.js").write_text("// line 1\nconst x = require('anyproxy');\n// line 3\n", encoding="utf-8")

    hits = precision.local_tool_grep(str(tmp_path), "test-repo", "anyproxy", context_lines=1)

    assert len(hits) == 1
    content = hits[0].content
    assert "L1:" in content
    assert "> L2:" in content
    assert "L3:" in content


def test_local_tool_grep_rejects_path_escape(tmp_path):
    precision = load_precision()
    try:
        precision.local_tool_grep(str(tmp_path), "../outside", "anyproxy")
    except ValueError as exc:
        assert "outside REPOS_ROOT" in str(exc)
    else:
        raise AssertionError("expected path escape to fail")


def test_local_tool_read_returns_file_content(tmp_path):
    precision = load_precision()
    repo = tmp_path / "test-repo"
    repo.mkdir()
    (repo / "main.js").write_text("line1\nline2\nline3\n", encoding="utf-8")

    hit = precision.local_tool_read(str(tmp_path), "test-repo", "main.js")

    assert hit is not None
    assert hit.content == "line1\nline2\nline3"
    assert hit.line_range == "L1-L3"
    assert hit.metadata["total_lines"] == 3


def test_local_tool_read_line_range(tmp_path):
    precision = load_precision()
    repo = tmp_path / "test-repo"
    repo.mkdir()
    (repo / "main.js").write_text("a\nb\nc\nd\ne\n", encoding="utf-8")

    hit = precision.local_tool_read(str(tmp_path), "test-repo", "main.js", start_line=2, end_line=4)

    assert hit is not None
    assert hit.content == "b\nc\nd"
    assert hit.line_range == "L2-L4"


def test_local_tool_read_rejects_path_escape(tmp_path):
    precision = load_precision()
    try:
        precision.local_tool_read(str(tmp_path), "test-repo", "../outside.js")
    except ValueError as exc:
        assert "outside REPOS_ROOT" in str(exc)
    else:
        raise AssertionError("expected path escape to fail")


def test_local_tool_read_returns_none_for_missing_file(tmp_path):
    precision = load_precision()
    repo = tmp_path / "test-repo"
    repo.mkdir()

    hit = precision.local_tool_read(str(tmp_path), "test-repo", "nonexistent.js")

    assert hit is None
