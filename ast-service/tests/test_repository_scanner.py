from pathlib import Path

from repository_scanner import discover_source_files, language_for_path


def test_language_for_path_detects_initial_languages():
    assert language_for_path(Path("a.py")) == "python"
    assert language_for_path(Path("a.ts")) == "typescript"
    assert language_for_path(Path("a.tsx")) == "typescript"
    assert language_for_path(Path("a.js")) == "javascript"
    assert language_for_path(Path("a.jsx")) == "javascript"
    assert language_for_path(Path("README.md")) is None


def test_discover_source_files_skips_ignored_dirs(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("def foo(): pass\n")
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "bad.py").write_text("def bad(): pass\n")

    files = list(discover_source_files(tmp_path))

    assert len(files) == 1
    item = files[0]
    assert item.repo == "repo"
    assert item.rel_path == "app.py"
    assert item.language == "python"
