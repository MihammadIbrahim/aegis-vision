"""In-app voice calls (decision D-025). The provider only signs room tokens and may close a room; audio flows
between the phones / browsers and the provider (LiveKit), never through the Core API."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class VoiceGrant:
    """What a client needs to join one room as one participant."""

    provider: str  # "livekit"
    url: str  # e.g. wss://my-project.livekit.cloud
    room: str
    identity: str
    token: str = ""  # a short-lived JWT; never logged or stored
    expires_in_seconds: int = 0


class IVoiceCallProvider(Protocol):
    @property
    def provider(self) -> str: ...  # "none" or "livekit"

    @property
    def configured(self) -> bool: ...

    def grant(self, *, room: str, identity: str, name: str, metadata: dict[str, str]) -> VoiceGrant:
        """A token that lets one participant join ``room`` with audio only. Raises ServiceUnavailable
        (VOICE_CALL_NOT_CONFIGURED) when no provider is configured."""
        ...

    async def close_room(self, room: str) -> bool:
        """Disconnect everyone from ``room`` (best effort; False when it could not be done)."""
        ...
