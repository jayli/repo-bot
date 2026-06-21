from __future__ import annotations

import json

from .templates import BASE_SYSTEM, EVIDENCE_RULES, TOOL_CATALOG, template_for


def build_system_prompt(template: str) -> str:
    return "\n\n".join([BASE_SYSTEM, TOOL_CATALOG, EVIDENCE_RULES, template_for(template)])


def build_user_message(question: str, evidence_pack: dict) -> str:
    return "Evidence Pack:\n" + json.dumps(evidence_pack, ensure_ascii=False, indent=2) + "\n\n问题: " + question
