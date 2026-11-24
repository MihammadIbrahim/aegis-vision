"""AI Investigation & Security Analytics Chatbot use cases (decision D-027).

A question is stored, answered by the ``assistant_engine`` with the read-only tools of the caller's own request
transaction (Row-Level Security), stored with its citations, tool calls and context, and audited: one
``ASSISTANT_QUERY`` record and one ``ASSISTANT_TOOL_CALL`` record per tool (tool name, sanitised arguments, outcome,
rows, latency — never secrets). Nothing else is written: the chatbot never changes an alert, case or anything else.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from typing import Any
from uuid import UUID

from assistant_engine import AssistantEngine
from assistant_engine.guardrails import redact

from src.domain.entities.user import User
from src.domain.exceptions.domain_errors import Conflict, NotFound
from src.infrastructure.database.repositories.assistant_repository import (
    MessageRow,
    SessionRow,
    SqlAssistantRepository,
    SqlAssistantTools,
)
from src.infrastructure.database.repositories.audit_log_repository import SqlOrganizationAuditLog
from src.infrastructure.database.unit_of_work import SqlTransaction

MAX_MESSAGES = 400
DEFAULT_TITLE = "New chat"
_TARGET = "ASSISTANT_SESSION"


def _title_from(question: str) -> str:
    words = " ".join(question.split())
    return words if len(words) <= 80 else words[:79].rstrip() + "…"


def _sanitised_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """What an audit record keeps of a tool call: labels and filters, not IDs of places or cameras."""
    clean: dict[str, Any] = {}
    for key, value in arguments.items():
        if key in ("period", "previous_period") and isinstance(value, dict):
            clean[key] = value.get("label")
        elif key in ("location", "camera") and isinstance(value, dict):
            clean[key] = value.get("name")
        elif key == "case" and isinstance(value, dict):
            clean[key] = value.get("reference") or "case"
        else:
            clean[key] = value
    return clean


@dataclass(frozen=True, slots=True)
class Exchange:
    session: SessionRow
    user_message: MessageRow
    assistant_message: MessageRow


class AssistantService:
    def __init__(
        self,
        engine: AssistantEngine,
        repo: SqlAssistantRepository,
        audit: SqlOrganizationAuditLog,
        transaction: SqlTransaction,
        actor: User,
        session: Any,
    ) -> None:
        self._engine = engine
        self._repo = repo
        self._audit = audit
        self._tx = transaction
        self._actor = actor
        self._db = session

    async def sessions(self) -> list[SessionRow]:
        return await self._repo.sessions()

    async def create(self, title: str | None, incident_id: UUID | None) -> SessionRow:
        reference = None
        if incident_id is not None:
            found = await self._repo.incident(incident_id)
            if found is None:
                raise NotFound("Case not found.", code="INCIDENT_NOT_FOUND")
            reference = found[1]
        session = await self._repo.create_session(
            (title or "").strip() or (f"Investigate {reference}" if reference else DEFAULT_TITLE), incident_id
        )
        await self._tx.commit()
        return session

    async def get(self, session_id: UUID) -> tuple[SessionRow, list[MessageRow]]:
        session = await self._repo.session(session_id)
        if session is None:
            raise NotFound("Chat session not found.", code="ASSISTANT_SESSION_NOT_FOUND")
        return session, await self._repo.messages(session_id)

    async def delete(self, session_id: UUID) -> None:
        if not await self._repo.delete_session(session_id):
            raise NotFound("Chat session not found.", code="ASSISTANT_SESSION_NOT_FOUND")
        await self._audit.record(self._actor, "ASSISTANT_SESSION_DELETED", _TARGET, session_id, {})
        await self._tx.commit()

    async def ask(self, session_id: UUID, question: str) -> Exchange:
        session = await self._repo.session(session_id)
        if session is None:
            raise NotFound("Chat session not found.", code="ASSISTANT_SESSION_NOT_FOUND")
        if session.message_count >= MAX_MESSAGES:
            raise Conflict("This chat is full. Start a new chat.", code="ASSISTANT_SESSION_FULL")
        exchange = await self._answer(session, question)
        await self._tx.commit()
        return exchange

    async def investigate(self, incident_id: UUID) -> Exchange:
        found = await self._repo.incident(incident_id)
        if found is None:
            raise NotFound("Case not found.", code="INCIDENT_NOT_FOUND")
        _, reference, _ = found
        session = await self._repo.latest_case_session(incident_id)
        if session is None or session.message_count >= MAX_MESSAGES:
            session = await self._repo.create_session(f"Investigate {reference}", incident_id)
        question = f"Investigate {reference}: full timeline, related events within ±30 minutes and evidence gaps."
        exchange = await self._answer(session, question)
        await self._tx.commit()
        return exchange

    async def _answer(self, session: SessionRow, question: str) -> Exchange:
        started = perf_counter()
        context = await self._repo.last_context(session.id)
        user_message = await self._repo.add_message(session.id, "USER", question)
        time_zone, enabled, high, standard = await self._repo.settings()
        now = datetime.now(UTC)
        vocabulary = await self._repo.vocabulary(now, time_zone)
        tools = SqlAssistantTools(self._db, self._actor, time_zone=time_zone, thresholds=(enabled, high, standard))
        context_case = (
            {"id": str(session.context_incident_id), "reference": session.context_incident_reference or ""}
            if session.context_incident_id
            else None
        )
        answer = await self._engine.answer(
            question, context=context, vocabulary=vocabulary, executor=tools, context_case=context_case
        )
        assistant_message = await self._repo.add_message(
            session.id,
            "ASSISTANT",
            answer.content,
            citations=[c.to_dict() for c in answer.citations],
            tool_calls=answer.tool_calls(),
            blocks=answer.blocks,
            context=answer.context,
            insufficient_evidence=answer.insufficient_evidence,
            provider=answer.provider,
            model_name=answer.model_name,
        )
        await self._repo.touch(session.id, _title_from(question) if session.title == DEFAULT_TITLE else None)
        for result in answer.results:
            await self._audit.record(
                self._actor,
                "ASSISTANT_TOOL_CALL",
                _TARGET,
                session.id,
                {
                    "tool": result.name,
                    "arguments": _sanitised_arguments(result.arguments),
                    "outcome": result.outcome,
                    "rows": result.rows,
                    "latency_ms": round(result.latency_ms, 1),
                },
                outcome="DENIED"
                if result.outcome == "DENIED"
                else "FAILURE"
                if result.outcome == "ERROR"
                else "SUCCESS",
            )
        await self._audit.record(
            self._actor,
            "ASSISTANT_QUERY",
            _TARGET,
            session.id,
            {
                "question": redact(question)[:300],
                "tools": [r.name for r in answer.results],
                "provider": answer.provider,
                "model": answer.model_name,
                "phrasing": answer.phrasing,
                "refusal": answer.refusal,
                "insufficient_evidence": answer.insufficient_evidence,
                "latency_ms": round((perf_counter() - started) * 1000, 1),
            },
            outcome="DENIED" if answer.refusal == "EXCLUDED" else "SUCCESS",
        )
        refreshed = await self._repo.session(session.id)
        assert refreshed is not None
        return Exchange(refreshed, user_message, assistant_message)
