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
