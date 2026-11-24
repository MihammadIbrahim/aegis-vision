"""PII scrubbing for exported chatbot logs before they become training data (decision D-027).

Real campus questions and answers (``ai_chat_messages``) may only be used for fine-tuning after this scrub: emails,
phone numbers, Pakistani CNIC numbers, tokens, long secrets, internal IDs and the names of staff, Guards and other
people given in ``names`` are replaced by placeholders. Case references (INC-…), times, counts, place and camera names
stay, because the model must learn them.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from assistant_engine.guardrails import redact

_CNIC = re.compile(r"\b\d{5}-\d{7}-\d\b")
_STREET = re.compile(
    r"\b(?:house|h\.?\s?no\.?|flat|street|st\.|road|rd\.)\s*#?\s*[\w-]+(?:[ ,]+[\w-]+){0,4}", re.IGNORECASE
)


def scrub(text: str, names: Iterable[str] = ()) -> str:
    """``text`` without contact details, identifiers, secrets or the given people's names."""
    text = _CNIC.sub("[cnic]", text)
    text = redact(text)
    text = _STREET.sub("[address]", text)
    for name in sorted({n.strip() for n in names if n and len(n.strip()) >= 3}, key=len, reverse=True):
        text = re.sub(rf"(?<!\w){re.escape(name)}(?!\w)", "[person]", text, flags=re.IGNORECASE)
    return text


def scrub_record(record: dict, names: Iterable[str] = ()) -> dict:  # type: ignore[type-arg]
    """A ``{"question", "answer", ...}`` log record with every text field scrubbed and IDs dropped."""
    names = list(names)
    clean = {}
    for key, value in record.items():
        if key in ("id", "session_id", "user_id", "organization_id", "campus_id"):
            continue
        clean[key] = scrub(value, names) if isinstance(value, str) else value
    return clean
