from __future__ import annotations

from pathlib import Path
import re

from .models import RetrievalHit


MANIFESTS = ["package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "pyproject.toml", "requirements.txt"]


def _repo_root(repos_root: str, repo: str) -> Path:
    root = Path(repos_root).resolve()
    path = (root / repo).resolve()
    if root != path and root not in path.parents:
        raise ValueError("repo path is outside REPOS_ROOT")
    return path


def _line_range(start: int, end: int | None = None) -> str:
    return f"L{start}" if end is None or end == start else f"L{start}-L{end}"


def read_manifest(repos_root: str, repo: str) -> list[RetrievalHit]:
    repo_path = _repo_root(repos_root, repo)
    hits: list[RetrievalHit] = []
    for name in MANIFESTS:
        path = repo_path / name
        if not path.exists() or not path.is_file():
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        line_count = max(1, len(content.splitlines()))
        hits.append(RetrievalHit("precision_search", repo, name, _line_range(1, line_count), content, "file_confirmed"))
    return hits


def grep_repo(repos_root: str, repo: str, pattern: str, max_matches: int = 20) -> list[RetrievalHit]:
    repo_path = _repo_root(repos_root, repo)
    regex = re.compile(pattern)
    hits: list[RetrievalHit] = []
    for path in repo_path.rglob("*"):
        if len(hits) >= max_matches:
            break
        if not path.is_file() or path.suffix.lower() not in {".js", ".ts", ".tsx", ".jsx", ".json", ".py", ".toml", ".yaml", ".yml", ".md"}:
            continue
        rel = path.relative_to(repo_path).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        for idx, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                hits.append(RetrievalHit("precision_search", repo, rel, _line_range(idx), line, "file_confirmed"))
                break
    return hits


def read_file_window(repos_root: str, repo: str, path: str, start_line: int, end_line: int) -> RetrievalHit | None:
    repo_path = _repo_root(repos_root, repo)
    file_path = (repo_path / path).resolve()
    if repo_path != file_path and repo_path not in file_path.parents:
        raise ValueError("file path is outside REPOS_ROOT")
    if not file_path.exists() or not file_path.is_file():
        return None
    lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(1, start_line)
    end = min(len(lines), end_line)
    content = "\n".join(lines[start - 1:end])
    return RetrievalHit("precision_search", repo, path, _line_range(start, end), content, "file_confirmed")
