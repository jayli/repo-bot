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


class TextBlock:
    def __init__(self, text):
        self.text = text
        self.type = "text"


class ToolUseBlock:
    def __init__(self, tool_id, name, input_args):
        self.id = tool_id
        self.name = name
        self.input = input_args
        self.type = "tool_use"


class Response:
    def __init__(self, content):
        self.content = content


class FakeMessages:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            return Response([
                TextBlock("需要读取文件"),
                ToolUseBlock("tool-1", "local_tool_read", {"repo": "demo", "path": "README.md"}),
            ])
        return Response([TextBlock("最终答案")])


class FakeClient:
    def __init__(self):
        self.messages = FakeMessages()


class FakeStream:
    def __init__(self, parts):
        self.text_stream = parts

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class StreamingMessages:
    def __init__(self):
        self.create_calls = []
        self.stream_calls = []

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        if len(self.create_calls) == 1:
            return Response([
                TextBlock("需要读取文件"),
                ToolUseBlock("tool-1", "local_tool_read", {"repo": "demo", "path": "README.md"}),
            ])
        return Response([TextBlock("draft answer")])

    def stream(self, **kwargs):
        self.stream_calls.append(kwargs)
        return FakeStream(["最终", "答案"])


class StreamingClient:
    def __init__(self):
        self.messages = StreamingMessages()


class FailingStreamMessages(StreamingMessages):
    def stream(self, **kwargs):
        self.stream_calls.append(kwargs)
        raise RuntimeError("stream unavailable")


class FailingStreamClient:
    def __init__(self):
        self.messages = FailingStreamMessages()


def test_run_answer_tool_loop_executes_tool_and_returns_final_answer():
    answer_loop = load_module("retrieval.answer_loop")
    client = FakeClient()
    dispatched = []

    def dispatch_tool(name, args):
        dispatched.append((name, args))
        return "README 内容"

    answer = answer_loop.run_answer_tool_loop(
        client=client,
        model="test-model",
        system="system prompt",
        messages=[{"role": "user", "content": "question"}],
        tools=[{"name": "local_tool_read"}],
        dispatch_tool=dispatch_tool,
        max_tokens=100,
        max_rounds=3,
    )

    assert answer == "最终答案"
    assert dispatched == [("local_tool_read", {"repo": "demo", "path": "README.md"})]
    assert client.messages.calls[0]["tools"] == [{"name": "local_tool_read"}]
    assert client.messages.calls[1]["messages"][-1]["content"] == [
        {"type": "tool_result", "tool_use_id": "tool-1", "content": "README 内容"}
    ]


def test_run_answer_tool_loop_reports_tool_start_before_dispatch():
    answer_loop = load_module("retrieval.answer_loop")
    client = FakeClient()
    events = []

    def dispatch_tool(name, args):
        events.append(("dispatch", name, args))
        return "README 内容"

    answer_loop.run_answer_tool_loop(
        client=client,
        model="test-model",
        system="system prompt",
        messages=[{"role": "user", "content": "question"}],
        tools=[{"name": "local_tool_read"}],
        dispatch_tool=dispatch_tool,
        max_tokens=100,
        max_rounds=3,
        on_tool_start=lambda name, args: events.append(("start", name, args)),
    )

    assert events == [
        ("start", "local_tool_read", {"repo": "demo", "path": "README.md"}),
        ("dispatch", "local_tool_read", {"repo": "demo", "path": "README.md"}),
    ]


def test_run_answer_tool_loop_reports_tool_error_without_success_callback():
    answer_loop = load_module("retrieval.answer_loop")
    client = FakeClient()
    events = []

    def dispatch_tool(name, args):
        raise RuntimeError("boom")

    answer_loop.run_answer_tool_loop(
        client=client,
        model="test-model",
        system="system prompt",
        messages=[{"role": "user", "content": "question"}],
        tools=[{"name": "local_tool_read"}],
        dispatch_tool=dispatch_tool,
        max_tokens=100,
        max_rounds=3,
        on_tool_call=lambda name, args, result: events.append(("success", name, result)),
        on_tool_error=lambda name, args, error: events.append(("error", name, error)),
    )

    assert events == [("error", "local_tool_read", "错误: boom")]


def test_run_answer_tool_loop_streams_final_answer_only_after_tools_finish():
    answer_loop = load_module("retrieval.answer_loop")
    client = StreamingClient()
    deltas = []
    dispatched = []

    def dispatch_tool(name, args):
        dispatched.append((name, args))
        return "README 内容"

    answer = answer_loop.run_answer_tool_loop(
        client=client,
        model="test-model",
        system="system prompt",
        messages=[{"role": "user", "content": "question"}],
        tools=[{"name": "local_tool_read"}],
        dispatch_tool=dispatch_tool,
        max_tokens=100,
        max_rounds=3,
        on_final_delta=deltas.append,
    )

    assert answer == "最终答案"
    assert deltas == ["最终", "答案"]
    assert dispatched == [("local_tool_read", {"repo": "demo", "path": "README.md"})]
    assert len(client.messages.stream_calls) == 1
    assert "tools" not in client.messages.stream_calls[0]
    assert client.messages.stream_calls[0]["messages"][-1]["role"] == "user"
    assert client.messages.stream_calls[0]["messages"][-1]["content"] == [
        {"type": "tool_result", "tool_use_id": "tool-1", "content": "README 内容"}
    ]


def test_run_answer_tool_loop_falls_back_to_non_stream_text_when_stream_fails():
    answer_loop = load_module("retrieval.answer_loop")
    client = FailingStreamClient()
    deltas = []

    answer = answer_loop.run_answer_tool_loop(
        client=client,
        model="test-model",
        system="system prompt",
        messages=[{"role": "user", "content": "question"}],
        tools=[{"name": "local_tool_read"}],
        dispatch_tool=lambda name, args: "README 内容",
        max_tokens=100,
        max_rounds=3,
        on_final_delta=deltas.append,
    )

    assert answer == "draft answer"
    assert deltas == []
    assert len(client.messages.stream_calls) == 1
