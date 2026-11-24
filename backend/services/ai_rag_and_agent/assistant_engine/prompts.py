"""Prompts and the Qwen2.5 chat format, shared by the runtime and ``backend/ml_training/chatbot`` (decision D-027).

The LLM never sees database rows or raw IDs: the planner prompt holds the question, the conversation context (labels
only) and the tool catalogue; the phrasing prompt holds the deterministic, already-grounded answer, which the model
may only rephrase. Both outputs are checked by the engine before use.
"""

from __future__ import annotations

import json
from typing import Any

from assistant_engine.catalog import TOOLS

PLANNER_SYSTEM = """You are the planner of a read-only security analytics assistant for one campus.
Choose the tools that answer the user's question. You never answer from memory and never change anything.
Reply with JSON only: {"tool_calls": [{"name": "<tool>", "arguments": {...}}]}. Use [] when no tool fits.
Arguments are simple strings: "period" and "previous_period" in words ("last month", "October 2025",
"between 2026-10-01 and 2026-10-15"), "location" and "camera" as the place or camera name the user wrote,
"case" as a case reference like "INC-000012". Enumerations must use the listed values exactly.
For follow-up questions ("those", "there", "what about ..."), reuse the period, place and case of the context.
Never call a tool to accuse a person or to confirm an identity.
Tools:
"""

PHRASER_SYSTEM = """You rewrite a security analytics answer so it reads naturally for a campus security supervisor.
Rules: keep every number, time, name and case reference exactly as given; do not add any number, fact or
recommendation that is not in the facts; never say who is guilty or responsible and never treat a Re-ID match as a
confirmed identity; keep it short (at most 6 sentences). Reply with the rewritten answer only."""


def planner_tools_text() -> str:
    simple = []
    for tool in TOOLS:
        params = {
            key: (value.get("enum") if "enum" in value else value.get("type", "string"))
            for key, value in tool.parameters["properties"].items()
        }
        simple.append(
            {
                "name": tool.name,
                "description": tool.description,
                "arguments": params,
                "required": tool.parameters.get("required", []),
            }
        )
    return json.dumps(simple, ensure_ascii=False)


def planner_messages(question: str, context_summary: str) -> list[dict[str, str]]:
    user = f"Context: {context_summary or 'none'}\nQuestion: {question}"
    return [{"role": "system", "content": PLANNER_SYSTEM + planner_tools_text()}, {"role": "user", "content": user}]


def phraser_messages(question: str, facts: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": PHRASER_SYSTEM},
        {"role": "user", "content": f"Question: {question}\nFacts:\n{facts}"},
    ]


def context_summary(context: dict[str, Any]) -> str:
    """Labels only (no IDs) of what the conversation is about."""
    parts = []
    if context.get("period"):
        parts.append(f"period: {context['period'].get('label')}")
    if context.get("location"):
        parts.append(f"place: {context['location'].get('name')}")
    if context.get("focus_location"):
        parts.append(f"top place in the last answer: {context['focus_location'].get('name')}")
    if context.get("case"):
        parts.append(f"case: {context['case'].get('reference')}")
    if context.get("source"):
        parts.append(f"records: {context['source']}")
    if context.get("tools"):
        parts.append(f"last tools: {', '.join(context['tools'])}")
    return "; ".join(parts)


def chatml(messages: list[dict[str, str]], *, add_generation_prompt: bool = False) -> str:
    """The Qwen2.5 chat template (ChatML), without needing a tokenizer: used for SFT data and tests."""
    text = "".join(f"<|im_start|>{m['role']}\n{m['content']}<|im_end|>\n" for m in messages)
    return text + ("<|im_start|>assistant\n" if add_generation_prompt else "")
