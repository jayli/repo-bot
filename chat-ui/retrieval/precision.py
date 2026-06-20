from __future__ import annotations

import fnmatch
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


def local_tool_grep(
    repos_root: str,
    repo: str,
    pattern: str,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    max_matches: int = 30,
    context_lines: int = 0,
) -> list[RetrievalHit]:
    """在仓库内执行 grep 搜索，支持 glob 过滤和上下文行。

    Args:
        repos_root: 仓库根目录
        repo: 仓库名
        pattern: 正则表达式
        include: 文件白名单 glob，如 ['*.js', '*.ts', 'src/**/*.py']。None 表示不限制。
        exclude: 文件黑名单 glob，如 ['node_modules/*', '*.min.js']。
        max_matches: 最多返回匹配行数
        context_lines: 每个匹配行前后的上下文行数
    """
    repo_path = _repo_root(repos_root, repo)
    regex = re.compile(pattern)
    hits: list[RetrievalHit] = []
    exclude = exclude or []

    for file_path in repo_path.rglob("*"):
        if len(hits) >= max_matches:
            break
        if not file_path.is_file():
            continue

        rel = file_path.relative_to(repo_path).as_posix()

        if any(fnmatch.fnmatch(rel, pat) for pat in exclude):
            continue

        if include is not None:
            if not any(fnmatch.fnmatch(rel, pat) for pat in include):
                continue

        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        lines = text.splitlines()
        for idx, line in enumerate(lines, start=1):
            if regex.search(line):
                ctx_start = max(1, idx - context_lines)
                ctx_end = min(len(lines), idx + context_lines)
                out_lines: list[str] = []
                for ln in range(ctx_start, ctx_end + 1):
                    marker = ">" if ln == idx else " "
                    out_lines.append(f"{marker} L{ln}: {lines[ln - 1]}")
                content = "\n".join(out_lines)
                hits.append(RetrievalHit(
                    "local_tool",
                    repo,
                    rel,
                    _line_range(idx),
                    content,
                    "file_confirmed",
                    metadata={"match_line": idx, "context_start": ctx_start, "context_end": ctx_end},
                ))
                if len(hits) >= max_matches:
                    break

    return hits


def local_tool_read(
    repos_root: str,
    repo: str,
    path: str,
    start_line: int | None = None,
    end_line: int | None = None,
    max_lines: int = 500,
) -> RetrievalHit | None:
    """读取仓库内某个文件，可选行范围。

    Args:
        repos_root: 仓库根目录
        repo: 仓库名
        path: 仓库内相对路径
        start_line: 起始行（1-based），None 从头开始
        end_line: 结束行（1-based，包含），None 到末尾
        max_lines: 最大返回行数上限
    """
    repo_path = _repo_root(repos_root, repo)
    file_path = (repo_path / path).resolve()
    if repo_path != file_path and repo_path not in file_path.parents:
        raise ValueError("file path is outside REPOS_ROOT")
    if not file_path.exists() or not file_path.is_file():
        return None

    text = file_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    total = len(lines)
    start = max(1, start_line or 1)
    end = min(total, end_line or total)
    if end - start + 1 > max_lines:
        end = start + max_lines - 1
    content = "\n".join(lines[start - 1:end])
    return RetrievalHit(
        "local_tool",
        repo,
        path,
        _line_range(start, end),
        content,
        "file_confirmed",
        metadata={"total_lines": total},
    )
