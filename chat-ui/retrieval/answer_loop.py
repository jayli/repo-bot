from __future__ import annotations

from collections.abc import Callable
from typing import Any


DispatchTool = Callable[[str, dict[str, Any]], str]


def _text_and_tool_uses(content: list[Any]) -> tuple[list[str], list[dict[str, Any]]]:
    text_parts: list[str] = []
    tool_uses: list[dict[str, Any]] = []
    for block in content:
        if hasattr(block, "text") and block.text:
            text_parts.append(block.text)
        if getattr(block, "type", "") == "tool_use":
            tool_uses.append({"id": block.id, "name": block.name, "input": dict(block.input)})
    return text_parts, tool_uses


def run_answer_tool_loop(
    *,
    client: Any,
    model: str,
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    dispatch_tool: DispatchTool,
    max_tokens: int = 3000,
    max_rounds: int = 12,
    max_tool_uses_per_round: int = 15,
    final_instruction: str = "请基于以上的所有工具调用结果，给出最终回答。不要再调用工具。",
    on_tool_start: Callable[[str, dict[str, Any]], None] | None = None,
    on_tool_call: Callable[[str, dict[str, Any], str], None] | None = None,
    on_tool_error: Callable[[str, dict[str, Any], str], None] | None = None,
    on_max_rounds: Callable[[], None] | None = None,
) -> str:
    """Run an Anthropic-style answer loop with model tool calls.

    The caller owns the concrete client, tool schemas, and dispatch function so
    this module stays reusable across CLI and Streamlit entry points.
    """
    conversation = list(messages)
    last_text = ""

    for _ in range(max_rounds):
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=conversation,
            tools=tools,
        )
        text_parts, tool_uses = _text_and_tool_uses(resp.content)
        last_text = "\n".join(text_parts) if text_parts else ""

        if not tool_uses:
            return last_text if last_text else "(模型未生成文本回答)"
        if len(tool_uses) > max_tool_uses_per_round:
            break

        assistant_content: list[dict[str, Any]] = []
        if text_parts:
            assistant_content.append({"type": "text", "text": "\n".join(text_parts)})

        tool_results: list[dict[str, str]] = []
        for tool_use in tool_uses:
            name = tool_use["name"]
            args = tool_use["input"]
            if on_tool_start:
                on_tool_start(name, args)
            try:
                result_text = dispatch_tool(name, args)
            except Exception as exc:
                result_text = f"错误: {exc}"
                if on_tool_error:
                    on_tool_error(name, args, result_text)
            else:
                if on_tool_call:
                    on_tool_call(name, args, result_text)
            tool_results.append({"tool_use_id": tool_use["id"], "content": result_text})

        for tool_use in tool_uses:
            assistant_content.append(
                {
                    "type": "tool_use",
                    "id": tool_use["id"],
                    "name": tool_use["name"],
                    "input": tool_use["input"],
                }
            )
        conversation.append({"role": "assistant", "content": assistant_content})
        conversation.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_result["tool_use_id"],
                        "content": tool_result["content"],
                    }
                    for tool_result in tool_results
                ],
            }
        )

    if on_max_rounds:
        on_max_rounds()
    conversation.append({"role": "user", "content": final_instruction})
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=conversation,
        )
        for block in resp.content:
            if hasattr(block, "text") and block.text:
                return block.text
    except Exception:
        pass
    return last_text if last_text else "(达到最大对话轮次)"
