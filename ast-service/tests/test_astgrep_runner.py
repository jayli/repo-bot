from pathlib import Path

from astgrep_runner import run_rule_file


def test_python_symbol_rule_finds_fixture_symbols():
    source = Path("tests/fixtures/sample-python/app.py")

    matches = run_rule_file(source, Path("rules/python-functions.yml"))
    texts = [match.text for match in matches]

    assert any("def load_user" in text for text in texts)
    assert any("def handler" in text for text in texts)
    assert any(match.captures.get("NAME") == "load_user" for match in matches)
    assert all(match.entity_kind == "function" for match in matches)

    class_matches = run_rule_file(source, Path("rules/python-classes.yml"))
    assert any("class UserService" in match.text for match in class_matches)
    assert all(match.entity_kind == "class" for match in class_matches)


def test_python_call_rule_finds_fixture_calls():
    matches = run_rule_file(
        Path("tests/fixtures/sample-python/app.py"),
        Path("rules/python-calls.yml"),
    )

    assert any("load_user(user_id)" in match.text for match in matches)
    assert any('service.get_user("42")' in match.text for match in matches)
    assert any(match.captures.get("CALLEE") == "load_user" for match in matches)


def test_runner_uses_node_range_for_repeated_matches(tmp_path):
    source = tmp_path / "repeat.py"
    source.write_text("def a():\n    foo()\n\ndef b():\n    foo()\n", encoding="utf-8")
    matches = run_rule_file(source, Path("rules/python-calls.yml"))
    foo_lines = [match.start_line for match in matches if match.text == "foo()"]
    assert foo_lines == [2, 5]
