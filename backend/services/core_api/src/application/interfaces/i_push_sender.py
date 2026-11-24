"""Push delivery to locked phones (decision D-025). Pushes carry no names, places or message text: only the kind of
event and an ID, so the phone wakes up and the app reads the details over the API."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol


class PushOutcome(StrEnum):
    SENT = "SENT"
    INVALID_TOKEN = "INVALID_TOKEN"  # the token is no longer valid: forget it
    FAILED = "FAILED"  # temporary or configuration problem


@dataclass(frozen=True, slots=True)
class PushMessage:
    title: str
    body: str
    data: dict[str, str] = field(default_factory=dict)
    channel_id: str = "security-alarm"  # the Android channel the Guard app creates (loud, bypasses Do Not Disturb)


class IPushSender(Protocol):
    @property
    def mode(self) -> str: ...  # "none" or "fcm"

    async def send(self, token: str, message: PushMessage) -> PushOutcome: ...
