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


def test_synthesizer_call_chain_template_has_chain_sections():
    synthesizer = load_module("prompts.synthesizer")

    system = synthesizer.build_system_prompt("call_chain")

    assert "调用链路" in system
    assert "分段说明" in system
    assert "引用来源" in system


def test_synthesizer_implementation_location_template_has_file_map():
    synthesizer = load_module("prompts.synthesizer")

    system = synthesizer.build_system_prompt("implementation_location")

    assert "文件地图" in system
    assert "阅读顺序" in system
    assert "引用来源" in system


def test_synthesizer_troubleshooting_template_has_fix_suggestions():
    synthesizer = load_module("prompts.synthesizer")

    system = synthesizer.build_system_prompt("troubleshooting")

    assert "排查路径" in system
    assert "修复建议" in system
    assert "引用来源" in system


def test_synthesizer_symbol_explanation_template():
    synthesizer = load_module("prompts.synthesizer")

    system = synthesizer.build_system_prompt("symbol_explanation")

    assert "职责与行为" in system
    assert "调用关系" in system
    assert "引用来源" in system


def test_synthesizer_impact_analysis_template():
    synthesizer = load_module("prompts.synthesizer")

    system = synthesizer.build_system_prompt("impact_analysis")

    assert "直接影响" in system
    assert "间接影响" in system
    assert "引用来源" in system


def test_synthesizer_comparison_template():
    synthesizer = load_module("prompts.synthesizer")

    system = synthesizer.build_system_prompt("comparison")

    assert "对比" in system
    assert "详细说明" in system
    assert "引用来源" in system


def test_synthesizer_architecture_overview_template():
    synthesizer = load_module("prompts.synthesizer")

    system = synthesizer.build_system_prompt("architecture_overview")

    assert "组件与职责" in system
    assert "交互流程" in system
    assert "引用来源" in system


def test_synthesizer_generic_fallback_template_has_sources():
    synthesizer = load_module("prompts.synthesizer")

    system = synthesizer.build_system_prompt("generic_code_answer")

    assert "依据" in system
    assert "引用来源" in system


def test_synthesizer_unknown_intent_falls_back_to_generic():
    synthesizer = load_module("prompts.synthesizer")

    system = synthesizer.build_system_prompt("nonexistent_intent")

    assert "依据" in system
    assert "引用来源" in system


def test_synthesizer_evidence_rules_includes_sources_instruction():
    synthesizer = load_module("prompts.synthesizer")

    system = synthesizer.build_system_prompt("generic_code_answer")

    assert "引用来源" in system
    assert "按相关度从高到低排列" in system

