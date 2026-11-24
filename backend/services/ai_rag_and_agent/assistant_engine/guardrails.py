"""Safety, scope and privacy rules of the chatbot (decision D-027), applied before and after any LLM.

* ``classify_request``: questions the chatbot must refuse — accusations / identity claims, any state-changing action
  (read-only), file uploads, and data outside the chatbot's scope (personnel, restricted-area access notes, user
  records, campus requests, organization settings, credentials). Supervisors are told the data is not available in
  their role; Admins are told the chatbot does not cover it.
* ``redact``: removes emails, phone numbers, tokens and long secrets before text is sent to an LLM.
* ``grounded``: every number in an LLM-written answer must already be in the facts the tools returned.
* ``unsafe_claims``: an LLM-written answer may not declare guilt or confirm an identity.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from assistant_engine.types import Role

RefusalKind = Literal["ACCUSATION", "READ_ONLY", "NO_UPLOADS", "EXCLUDED"]

_ACCUSATION = re.compile(
    r"\b(who\s+(?:did\s+(?:it|this)|is\s+(?:responsible|guilty|to\s+blame)|committed|was\s+it|stole|attacked)"
    r"|(?:is|was)\s+(?:he|she|they|this\s+(?:person|man|woman|guy)|that\s+(?:person|man|woman|guy))\s+"
    r"(?:guilty|the\s+(?:culprit|thief|attacker|suspect|criminal|offender))"
    r"|guilty|culprit|punish|arrest|"
    r"identify\s+(?:him|her|them|the\s+(?:person|man|woman|suspect|intruder|attacker))"
    r"|(?:confirm|prove|verify)\s+(?:his|her|their|the\s+person'?s?|the\s+suspect'?s?)\s+identity"
    r"|(?:is|are)\s+(?:this|these|that|those)\s+(?:the\s+)?same\s+(?:person|people|man|woman)"
    r"|(?:name|who)\s+(?:of|is)\s+the\s+(?:suspect|intruder|attacker|person\s+in))",
    re.IGNORECASE,
)
_ACTION = re.compile(
    r"^\s*(?:(?:hey|hi|ok|okay|quickly|please|kindly|now|so)[,!]?\s+|(?:can|could|would|will)\s+you\s+(?:please\s+)?|"
    r"i\s+(?:need|want|would\s+like)\s+you\s+to\s+|go\s+ahead\s+and\s+)*"
    r"(?:close|resolve|dismiss|escalate|assign|reassign|unassign|delete|remove|mark|send|broadcast|notify|"
    r"open\s+a\s+(?:new\s+)?case|create\s+a\s+(?:new\s+)?case|add\s+(?:a\s+)?note|write\s+(?:a\s+)?note|"
    r"edit|link|unlink|approve|reject|acknowledge|deactivate|reactivate|invite|"
    r"(?:update|change|set)\s+(?:the\s+|its\s+|their\s+)?(?:status|priority|assignee|handler|case|alert))\b",
    re.IGNORECASE,
)
_UPLOAD = re.compile(
    r"\b(upload|attach|import|ingest)\b.*\b(pdf|sop|manual|document|file|policy|policies)\b"
    r"|\b(pdf|sop|policy\s+document)s?\b",
    re.IGNORECASE,
)
_EXCLUDED = re.compile(
    r"\b(personnel|employee\s+records?|staff\s+records?|students?\s+records?|restricted[- ]area\s+access|"
    r"access\s+notes?|"
    r"who\s+is\s+authori[sz]ed|authori[sz]ed\s+persons?|user\s+records?|rejoin|permanent(?:ly)?\s+delet|"
    r"campus\s+requests?|campus\s+approvals?|organi[sz]ation\s+settings|billing|passwords?|otp|one[- ]time\s+codes?|"
    r"tokens?|secrets?|home\s+address(?:es)?|phone\s+numbers?|email\s+address(?:es)?)\b",
    re.IGNORECASE,
)

ACCUSATION_TEXT = (
    "I can't say who is responsible or confirm anyone's identity. I support investigations with records only: "
    "Re-ID and Person Search results are possible matches that a person must review, never confirmed identities, "
    "and conclusions about people are for your investigators."
)
READ_ONLY_TEXT = (
    "I'm read-only: I can't change statuses, write notes, assign, escalate, send messages or broadcasts. Please make "
    "that change on the alert or case page. I can help you look into the records first."
)
NO_UPLOADS_TEXT = (
    "I don't take file uploads (no SOP or PDF documents). I answer only from this campus's operational records: "
    "alerts, cases, manual reports, cameras, locations, Guard responses, evidence and audit records."
)


def excluded_text(role: Role) -> str:
    if role == "SUPERVISOR":
        return (
            "That information is not available to Supervisors in the chatbot (personnel records, restricted-area "
            "access notes, user records, campus requests, organization settings and credentials are Admin-only or "
            "never shared). I can help with alerts, cases, manual reports, cameras, locations, Guard responses and "
            "evidence."
        )
    return (
        "The chatbot does not cover personnel records, restricted-area access, user records, campus requests, "
        "organization settings or credentials; please use their pages. I can help with alerts, cases, manual "
        "reports, cameras, locations, Guard responses, evidence and audit activity."
    )


@dataclass(frozen=True, slots=True)
class Refusal:
    kind: RefusalKind
    text: str


def classify_request(question: str, role: Role) -> Refusal | None:
    if _ACCUSATION.search(question):
        return Refusal("ACCUSATION", ACCUSATION_TEXT)
    if _UPLOAD.search(question):
        return Refusal("NO_UPLOADS", NO_UPLOADS_TEXT)
    if _ACTION.search(question):
        return Refusal("READ_ONLY", READ_ONLY_TEXT)
    if _EXCLUDED.search(question):
        return Refusal("EXCLUDED", excluded_text(role))
    return None


# --- Privacy: what may be sent to an LLM ----------------------------------------------------------------------------

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"\+\d[\d\s-]{8,15}\d\b|\b0\d{9,11}\b")
_JWT = re.compile(r"\beyJ[\w-]+\.[\w-]+\.[\w-]+\b")
_SECRET = re.compile(r"\b[A-Za-z0-9_\-]{32,}\b")
_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE)


def redact(text: str) -> str:
    """Text safe to send to an LLM: no emails, phone numbers, JWTs, long secrets or internal IDs."""
    text = _JWT.sub("[token]", text)
    text = _EMAIL.sub("[email]", text)
    text = _UUID.sub("[id]", text)
    text = _SECRET.sub("[secret]", text)
    return _PHONE.sub("[phone]", text)


# --- Grounding and safety of LLM-written answers --------------------------------------------------------------------

_NUMBER = re.compile(r"(?<![\w.])-?\d+(?:[.,]\d+)?(?![\w])")


def numbers_in(text: str) -> set[str]:
    return {n.replace(",", ".").lstrip("-").rstrip("0").rstrip(".") or "0" for n in _NUMBER.findall(text)}


def grounded(answer: str, facts: str, question: str = "") -> bool:
    """True when every number in ``answer`` appears in ``facts`` (or was in the question itself)."""
    return numbers_in(answer) <= numbers_in(facts) | numbers_in(question)


_UNSAFE = re.compile(
    r"\b(is|was|are|were)\s+(?:the\s+)?(?:guilty|culprit|criminal|thief|attacker|offender|perpetrator)\b"
    r"|\b(confirmed|positively)\s+identif|\bidentity\s+(?:is|was)\s+confirmed\b|\bit\s+was\s+[A-Z][a-z]+\b",
)


def unsafe_claims(answer: str) -> bool:
    return bool(_UNSAFE.search(answer))
