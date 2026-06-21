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


def test_progress_log_keeps_latest_six_records():
    progress = load_module("retrieval.progress")
    log = progress.ProgressLog(max_items=6)

    for index in range(8):
        log.start(f"tool-{index}", "detail")

    lines = log.lines()

    assert len(lines) == 6
    assert "tool-0" not in "\n".join(lines)
    assert "tool-1" not in "\n".join(lines)
    assert "tool-2" in lines[0]
    assert "tool-7" in lines[-1]


def test_progress_log_marks_started_record_done_without_adding_result_line():
    progress = load_module("retrieval.progress")
    log = progress.ProgressLog(max_items=6)

    record_id = log.start("LLM tool local_tool_read", '{"repo": "demo"}')
    log.complete(record_id)

    assert log.lines() == ['✓ **LLM tool local_tool_read**：{"repo": "demo"}']


def test_progress_log_appends_error_record_after_failed_call():
    progress = load_module("retrieval.progress")
    log = progress.ProgressLog(max_items=6)

    record_id = log.start("LLM tool local_tool_read", '{"repo": "demo"}')
    log.fail(record_id, "文件不存在")

    assert log.lines() == [
        '→ **LLM tool local_tool_read**：{"repo": "demo"}',
        "× **LLM tool local_tool_read**：文件不存在",
    ]


def test_progress_log_can_append_standalone_error_record():
    progress = load_module("retrieval.progress")
    log = progress.ProgressLog(max_items=6)

    log.error("LLM tool local_tool_read", "文件不存在")

    assert log.lines() == ["× **LLM tool local_tool_read**：文件不存在"]
