from astgrep_runner import AstGrepMatch
from normalizer import normalize_calls, normalize_imports, normalize_symbols


def test_normalize_python_symbols_extracts_basic_names():
    matches = [
        AstGrepMatch("def load_user(user_id: str):\n    pass", 10, 11, 4, 13, {"NAME": "load_user", "PARAMS": "user_id: str"}, "python-functions", "function"),
        AstGrepMatch("class UserService:\n    pass", 1, 2, 6, 17, {"NAME": "UserService"}, "python-classes", "class"),
    ]

    symbols = normalize_symbols("repo", "repo/app.py", "python", matches)

    assert symbols[0].name == "load_user"
    assert symbols[0].kind == "function"
    assert symbols[1].name == "UserService"
    assert symbols[1].kind == "class"


def test_normalize_calls_uses_callee_capture():
    matches = [
        AstGrepMatch("load_user(user_id)", 5, 5, 4, 13, {"CALLEE": "load_user"}, "python-calls"),
    ]

    calls = normalize_calls("repo", "repo/app.py", matches)

    assert [call.callee_name for call in calls] == ["load_user"]


def test_normalize_imports_extracts_modules():
    matches = [
        AstGrepMatch("import os", 1, 1, 7, 9, {"MODULE": "os"}, "python-imports"),
        AstGrepMatch("from fastapi import APIRouter", 2, 2, 5, 12, {"MODULE": "fastapi", "NAMES": "APIRouter"}, "python-imports"),
    ]

    imports = normalize_imports("repo", "repo/app.py", "python", matches)

    assert [item.module_path for item in imports] == ["os", "fastapi"]
