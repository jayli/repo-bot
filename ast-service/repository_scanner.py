import os
from dataclasses import dataclass
from pathlib import Path


SKIP_DIRS = {
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    "target",
    "dist",
    "build",
    ".next",
    "vendor",
    "vendor_",
}

EXT_TO_LANGUAGE = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
}


@dataclass(frozen=True)
class SourceFile:
    repo: str
    abs_path: Path
    rel_path: str
    language: str
    size: int
    mtime: float


def language_for_path(path: Path) -> str | None:
    return EXT_TO_LANGUAGE.get(path.suffix)


def discover_source_files(repos_root: str | Path) -> list[SourceFile]:
    root = Path(repos_root)
    discovered: list[SourceFile] = []
    for current, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS]
        current_path = Path(current)
        for filename in files:
            abs_path = current_path / filename
            language = language_for_path(abs_path)
            if language is None:
                continue
            rel = abs_path.relative_to(root)
            parts = rel.parts
            if len(parts) < 2:
                continue
            stat = abs_path.stat()
            discovered.append(
                SourceFile(
                    repo=parts[0],
                    abs_path=abs_path,
                    rel_path="/".join(parts[1:]),
                    language=language,
                    size=stat.st_size,
                    mtime=stat.st_mtime,
                )
            )
    return discovered
