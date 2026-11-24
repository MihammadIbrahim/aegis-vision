"""LiveKit voice calls (decision D-025): LiveKit Cloud's free tier or a self-hosted LiveKit server.

The Core API only signs participant tokens (HS256 JWTs with the API key as issuer and the API secret as key, the
format LiveKit documents for access tokens) and, when a call is ended, asks the server to close the room. Rooms are
created by the server when the first participant joins. Audio flows between the clients and LiveKit.
"""

import json
import logging
from datetime import UTC, datetime, timedelta

import httpx
import jwt

from src.application.interfaces.i_voice_call_provider import VoiceGrant
from src.domain.exceptions.domain_errors import ServiceUnavailable

logger = logging.getLogger(__name__)

NOT_CONFIGURED = "VOICE_CALL_NOT_CONFIGURED"


class DisabledVoiceProvider:
    """VOICE_CALL_PROVIDER=none: calls are refused with 503 VOICE_CALL_NOT_CONFIGURED."""

    @property
    def provider(self) -> str:
        return "none"

    @property
    def configured(self) -> bool:
        return False

    def grant(self, *, room: str, identity: str, name: str, metadata: dict[str, str]) -> VoiceGrant:
        raise ServiceUnavailable(
            "In-app voice calls are not configured on this server yet. Use the chat, or call by phone.",
            code=NOT_CONFIGURED,
        )

    async def close_room(self, room: str) -> bool:
        return False


class LiveKitVoiceProvider:
    def __init__(
        self,
        *,
        url: str,
        api_key: str,
        api_secret: str,
        token_ttl_seconds: int,
        request_timeout_seconds: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._url = url.rstrip("/")
        self._api_key = api_key
        self._api_secret = api_secret
        self._ttl = token_ttl_seconds
        self._timeout = request_timeout_seconds
        self._transport = transport

    @property
    def provider(self) -> str:
        return "livekit"

    @property
    def configured(self) -> bool:
        return True

    def _token(
        self,
        *,
        identity: str,
        video: dict[str, object],
        ttl: int,
        name: str | None = None,
        metadata: str | None = None,
        now: datetime | None = None,
    ) -> str:
        issued = now or datetime.now(UTC)
        claims: dict[str, object] = {
            "iss": self._api_key,
            "sub": identity,
            "nbf": int((issued - timedelta(seconds=5)).timestamp()),
            "iat": int(issued.timestamp()),
            "exp": int((issued + timedelta(seconds=ttl)).timestamp()),
            "video": video,
        }
        if name is not None:
            claims["name"] = name
        if metadata is not None:
            claims["metadata"] = metadata
        return jwt.encode(claims, self._api_secret, algorithm="HS256")

    def grant(self, *, room: str, identity: str, name: str, metadata: dict[str, str]) -> VoiceGrant:
        token = self._token(
            identity=identity,
            name=name,
            metadata=json.dumps(metadata, separators=(",", ":")),
            ttl=self._ttl,
            video={
                "room": room,
                "roomJoin": True,
                "canPublish": True,
                "canSubscribe": True,
                "canPublishData": False,
                # Audio only: the microphone is the only source a participant may publish.
                "canPublishSources": ["microphone"],
            },
        )
        return VoiceGrant(
            provider="livekit",
            url=self._url,
            room=room,
            identity=identity,
            token=token,
            expires_in_seconds=self._ttl,
        )

    def _http_base(self) -> str:
        if self._url.startswith("wss://"):
            return "https://" + self._url[len("wss://") :]
        if self._url.startswith("ws://"):
            return "http://" + self._url[len("ws://") :]
        return self._url

    async def close_room(self, room: str) -> bool:
        """RoomService.DeleteRoom (Twirp): every participant is disconnected. Best effort: a failure is logged
        (without the token) and the clients still leave when they receive the call's ENDED signal."""
        token = self._token(identity="cctv-core-api", ttl=60, video={"roomCreate": True})
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                response = await client.post(
                    f"{self._http_base()}/twirp/livekit.RoomService/DeleteRoom",
                    json={"room": room},
                    headers={"Authorization": f"Bearer {token}"},
                )
        except httpx.HTTPError as error:
            logger.warning("LiveKit room could not be closed: %s", type(error).__name__)
            return False
        if response.status_code == 200:
            return True
        # 404: nobody ever joined, so there is no room to close.
        if response.status_code != 404:
            logger.warning("LiveKit room could not be closed: HTTP %s", response.status_code)
        return response.status_code == 404
