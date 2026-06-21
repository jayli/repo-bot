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


class Hit:
    def __init__(self, path, content="", line_range="", metadata=None):
        self.path = path
        self.content = content
        self.line_range = line_range
        self.metadata = metadata or {}


def test_dispatch_tool_resolves_repo_and_reads_local_file(tmp_path):
    tool_dispatch = load_module("retrieval.tool_dispatch")
    repo_root = tmp_path / "repos"
    repo_dir = repo_root / "demo"
    repo_dir.mkdir(parents=True)

    calls = []

    def local_read(repos_root, repo, **kwargs):
        calls.append((repos_root, repo, kwargs))
        return Hit("README.md", "file body")

    result = tool_dispatch.dispatch_tool(
        "local_tool_read",
        {"repo": "github.com/acme/demo", "path": "README.md"},
        evidence_pack={"repo_roots": {"demo": str(repo_dir)}, "evidence": []},
        repos_root=str(repo_root),
        local_tool_read=local_read,
    )

    assert result == "file body"
    assert calls == [(str(repo_root), "demo", {"path": "README.md", "start_line": None, "end_line": None, "max_lines": 200})]
