from __future__ import annotations

import json

from .templates import BASE_SYSTEM, EVIDENCE_RULES, TOOL_CATALOG, template_for


def build_system_prompt(template: str) -> str:
    return "\n\n".join([BASE_SYSTEM, TOOL_CATALOG, EVIDENCE_RULES, template_for(template)])


def build_user_message(question: str, evidence_pack: dict) -> str:
    return (
        "最初的问题："
        + question
        + "\n\n请一定要围绕最初的问题进行回答。Evidence Pack 只作为证据来源，"
        + "不要把检索过程、工具调用或与问题无关的中间信息当作最终结论。\n\n"
        + "Evidence Pack:\n"
        + json.dumps(evidence_pack, ensure_ascii=False, indent=2)
    )
