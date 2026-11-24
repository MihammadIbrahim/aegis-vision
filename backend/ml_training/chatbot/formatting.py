"""Training records in the runtime's exact prompt format (``assistant_engine.prompts``), so a fine-tuned model sees
in training what core_api sends it in production."""

from __future__ import annotations

import json
from typing import Any

from assistant_engine import prompts


def planner_record(question: str, context: dict[str, Any], target: dict[str, Any]) -> list[dict[str, str]]:
    messages = prompts.planner_messages(question, prompts.context_summary(context))
    return [*messages, {"role": "assistant", "content": json.dumps(target, ensure_ascii=False)}]


def phrasing_record(question: str, facts: str, target: str) -> list[dict[str, str]]:
    return [*prompts.phraser_messages(question, facts), {"role": "assistant", "content": target}]


def to_chatml(messages: list[dict[str, str]]) -> str:
    """The full training text (Qwen2.5 ChatML); the loss is taken on the last assistant turn only."""
    return prompts.chatml(messages)


def prompt_and_completion(messages: list[dict[str, str]]) -> tuple[str, str]:
    """Split a record into the prompt (up to the generation prompt) and the completion the model must produce."""
    *context, answer = messages
    if answer["role"] != "assistant":
        raise ValueError("the last message must be the assistant's answer")
    return prompts.chatml(context, add_generation_prompt=True), answer["content"] + "<|im_end|>\n"


def approx_tokens(text: str) -> int:
    """A tokenizer-free estimate (≈ 3.6 characters per Qwen token for this English/JSON mix), used by ``prepare``
    to warn when a record may exceed ``max_seq_length`` before any download."""
    return int(len(text) / 3.6) + 1
