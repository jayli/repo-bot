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


def test_synthesizer_system_prompt_contains_evidence_rules():
    synthesizer = load_module("prompts.synthesizer")

    system = synthesizer.build_system_prompt("dependency_relation")

    assert "repo/path:Lx" in system
    assert "不要编造" in system
    assert "实事求是" in system
    assert "依赖链路" in system
    assert "local_tool_grep" in system
    assert "search_sourcebot" in system
    assert "search_qdrant" in system
    assert "read_manifest" in system


def test_synthesizer_system_prompt_constrains_agent_tool_loop():
    synthesizer = load_module("prompts.synthesizer")

    system = synthesizer.build_system_prompt("dependency_relation")

    assert "不要输出检索过程" in system
    assert "local_tool_* 只能在已确认 repo 后使用" in system
    assert "repo_roots" in system
    assert "如果需要本地路径，先用全局检索确认候选 repo" in system
    assert "不要建议已经由 Evidence Pack 覆盖的工具调用" in system


def test_synthesizer_user_message_leads_with_original_question():
    synthesizer = load_module("prompts.synthesizer")

    message = synthesizer.build_user_message(
        "passwall-any 这个仓库的 README.md 文档中不是给了好几个节点链接吗？",
        {"items": []},
    )

    assert message.startswith(
        "最初的问题：passwall-any 这个仓库的 README.md 文档中不是给了好几个节点链接吗？"
    )
    assert "一定要围绕最初的问题进行回答" in message
    assert message.index("最初的问题：") < message.index("Evidence Pack:")
