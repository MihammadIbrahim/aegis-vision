"""The AI Investigation & Security Analytics Chatbot engine (decision D-027).

``AssistantEngine.answer``:
1. Guardrails first (deterministic, before any model): accusations / identity claims, state-changing requests,
   uploads and excluded data are refused.
2. Planning: an LLM provider proposes tool calls (validated and normalised against the catalogue, the campus
   vocabulary and ``dates.py``); the deterministic planner is used when there is no model, when the model's plan is
   invalid or empty, or when the model cannot be reached (the answer then says so).
3. The caller's executor runs each read-only tool with the caller's database session (Row-Level Security).
4. The deterministic synthesizer writes the grounded answer, blocks and citations from the results only.
5. With a model, the answer may be rephrased; the rephrasing is used only if it adds no number and makes no
   accusation or identity claim (``guardrails``); otherwise the grounded text stays.
"""

from __future__ import annotations

from typing import Any
from zoneinfo import ZoneInfo

from assistant_engine import guardrails, planner, prompts, synthesizer
from assistant_engine.catalog import CAPABILITIES, ArgumentError
from assistant_engine.providers import LLMProvider, LLMUnavailable
from assistant_engine.types import Citation, EngineAnswer, ToolExecutor, ToolResult, Vocabulary

MAX_CALLS = 6
SAFETY_NOTE = (
    "Decision support only: figures come from this campus's records; people and Re-ID matches must be verified by "
    "your staff."
)


def help_text() -> str:
    lines = ["I answer questions from this campus's records. I can help with:"]
    for c in CAPABILITIES:
        lines.append(f'• {c.title} — e.g. "{c.examples[0]}"')
    return "\n".join(lines)


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001
        return ZoneInfo("UTC")


class AssistantEngine:
    def __init__(self, provider: LLMProvider | None = None) -> None:
        self.provider = provider or LLMProvider()

    async def aclose(self) -> None:
        await self.provider.aclose()

    async def answer(
        self,
        question: str,
        *,
        context: dict[str, Any] | None,
        vocabulary: Vocabulary,
        executor: ToolExecutor,
        context_case: dict[str, str] | None = None,
    ) -> EngineAnswer:
        question = question.strip()
        context = dict(context or {})
        provider = self.provider
        notices: list[dict[str, Any]] = []

        deterministic_plan = planner.plan(question, context, vocabulary, context_case=context_case)
        calls = deterministic_plan.calls
        if provider.is_llm and deterministic_plan.refusal is None:
            try:
                raw = await provider.chat_json(
                    prompts.planner_messages(guardrails.redact(question), prompts.context_summary(context))
                )
                proposed = raw.get("tool_calls")
                if isinstance(proposed, list) and proposed:
                    calls = (
                        planner.normalise_llm_calls(proposed, question, context, vocabulary, context_case=context_case)
                        or calls
                    )
            except LLMUnavailable as error:
                notices.append(
                    {
                        "type": "notice",
                        "tone": "warning",
                        "text": f"The configured language model could not be used ({error}); "
                        "this answer was produced by "
                        "the built-in deterministic engine from the same records.",
                    }
                )
            except ArgumentError:
                pass  # the model's plan did not match the catalogue: the deterministic plan stands
        calls = calls[:MAX_CALLS]

        results: list[ToolResult] = [await executor.run(call) for call in calls]
        tz = _zone(vocabulary.time_zone)
        texts: list[str] = []
        blocks: list[dict[str, Any]] = []
        citations: list[Citation] = []
        focus: dict[str, Any] | None = None
        refusal = deterministic_plan.refusal
        if refusal is not None:
            texts.append(refusal.text)
        texts.extend(deterministic_plan.notes)
        for result in results:
            part = synthesizer.section(result, tz)
            texts.append(part.text)
            blocks.extend(part.blocks)
            for c in part.citations:
                if c not in citations:
                    citations.append(c)
            focus = focus or part.focus_location
        if deterministic_plan.help and not results:
            texts.append(help_text())

        missing = [r for r in results if r.outcome != "OK"]
        insufficient = bool(missing) or (not results and refusal is None and not deterministic_plan.help)
        if missing and len(results) > 1:
            names = ", ".join(r.name.replace("_", " ") for r in missing)
            notices.append(
                {"type": "notice", "tone": "info", "text": f"No matching records for part of this answer ({names})."}
            )
        facts = "\n".join(t for t in texts if t)
        content, phrasing = facts, "deterministic"
        if provider.is_llm and results and not missing and refusal is None and not notices:
            try:
                candidate = await provider.chat_text(prompts.phraser_messages(guardrails.redact(question), facts))
                if (
                    candidate
                    and guardrails.grounded(candidate, facts, question)
                    and not guardrails.unsafe_claims(candidate)
                ):
                    content, phrasing = candidate, "llm"
                else:
                    phrasing = "deterministic_fallback"
            except LLMUnavailable:
                phrasing = "deterministic_fallback"

        new_context = {**context, **{k: v for k, v in deterministic_plan.context.items()}}
        if calls is not deterministic_plan.calls:
            new_context["tools"] = [c.name for c in calls]
            for call in calls:
                for key in ("period", "location", "case"):
                    if call.arguments.get(key):
                        new_context[key] = call.arguments[key]
        if focus:
            new_context["focus_location"] = focus
        return EngineAnswer(
            content=content,
            blocks=notices + blocks,
            citations=citations[:40],
            results=results,
            insufficient_evidence=insufficient,
            context=new_context,
            provider=provider.name,
            model_name=provider.model_name,
            refusal=refusal.kind if refusal else None,
            phrasing=phrasing,
        )
