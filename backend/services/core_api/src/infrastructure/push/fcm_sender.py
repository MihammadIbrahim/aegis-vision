"""Push delivery (decision D-025).

``PUSH_DELIVERY_MODE=fcm`` uses the Firebase Cloud Messaging HTTP v1 API with a service-account key: a JWT signed
with the key (RS256) is exchanged for a short-lived OAuth access token, which is cached until shortly before it
expires. Messages go to Android phones as high-priority notifications on the Guard app's ``security-alarm`` channel.
Live delivery needs a Firebase project and a development build of the app with ``google-services.json``; it has not
been tested against Firebase from this repository.
"""

import json
import logging
import time
from pathlib import Path

import httpx
import jwt

from src.application.interfaces.i_push_sender import PushMessage, PushOutcome

logger = logging.getLogger(__name__)

FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
DEFAULT_TOKEN_URI = "https://oauth2.googleapis.com/token"
FCM_SEND_URL = "https://fcm.googleapis.com/v1/projects/{project}/messages:send"


class NoPushSender:
    """PUSH_DELIVERY_MODE=none: nothing leaves the server."""

    @property
    def mode(self) -> str:
        return "none"

    async def send(self, token: str, message: PushMessage) -> PushOutcome:
        return PushOutcome.FAILED


class FcmPushSender:
    def __init__(
        self,
        *,
        project_id: str,
        service_account: dict[str, str],
        request_timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        for key in ("client_email", "private_key"):
            if not service_account.get(key):
                raise ValueError(f"The FCM service-account key has no {key}")
        self._project = project_id
        self._email = service_account["client_email"]
        self._private_key = service_account["private_key"]
        self._token_uri = service_account.get("token_uri") or DEFAULT_TOKEN_URI
        self._timeout = request_timeout_seconds
        self._transport = transport
        self._access_token: str | None = None
        self._access_expires = 0.0

    @classmethod
    def from_file(cls, project_id: str, path: str, request_timeout_seconds: float = 10.0) -> "FcmPushSender":
        file = Path(path)
        if not file.is_file():
            raise ValueError("FCM_SERVICE_ACCOUNT_FILE does not point to a readable file")
        return cls(
            project_id=project_id,
            service_account=json.loads(file.read_text(encoding="utf-8")),
            request_timeout_seconds=request_timeout_seconds,
        )

    @property
    def mode(self) -> str:
        return "fcm"

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self._timeout, transport=self._transport)

    async def _bearer(self) -> str | None:
        if self._access_token and time.monotonic() < self._access_expires - 60:
            return self._access_token
        now = int(time.time())
        assertion = jwt.encode(
            {"iss": self._email, "scope": FCM_SCOPE, "aud": self._token_uri, "iat": now, "exp": now + 3600},
            self._private_key,
            algorithm="RS256",
        )
        try:
            async with self._client() as client:
                response = await client.post(
                    self._token_uri,
                    data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion},
                )
        except httpx.HTTPError as error:
            logger.warning("FCM access token request failed: %s", type(error).__name__)
            return None
        if response.status_code != 200:
            logger.warning("FCM access token request refused: HTTP %s", response.status_code)
            return None
        body = response.json()
        self._access_token = str(body["access_token"])
        self._access_expires = time.monotonic() + float(body.get("expires_in", 3600))
        return self._access_token

    async def send(self, token: str, message: PushMessage) -> PushOutcome:
        bearer = await self._bearer()
        if bearer is None:
            return PushOutcome.FAILED
        payload = {
            "message": {
                "token": token,
                "notification": {"title": message.title, "body": message.body},
                "data": message.data,
                "android": {
                    "priority": "HIGH",
                    "ttl": "300s",
                    "notification": {
                        "channel_id": message.channel_id,
                        "sound": "alarm",
                        "notification_priority": "PRIORITY_MAX",
                        "visibility": "PUBLIC",
                        "default_vibrate_timings": True,
                    },
                },
            }
        }
        try:
            async with self._client() as client:
                response = await client.post(
                    FCM_SEND_URL.format(project=self._project),
                    json=payload,
                    headers={"Authorization": f"Bearer {bearer}"},
                )
        except httpx.HTTPError as error:
            logger.warning("FCM send failed: %s", type(error).__name__)
            return PushOutcome.FAILED
        if response.status_code == 200:
            return PushOutcome.SENT
        if response.status_code == 401:
            self._access_token = None  # retried with a fresh access token next time
        if response.status_code in (400, 404) and _is_dead_token(response):
            return PushOutcome.INVALID_TOKEN
        logger.warning("FCM send refused: HTTP %s", response.status_code)
        return PushOutcome.FAILED


def _is_dead_token(response: httpx.Response) -> bool:
    try:
        error = response.json().get("error", {})
    except ValueError:
        return False
    if error.get("status") in ("NOT_FOUND", "UNREGISTERED"):
        return True
    details = error.get("details") or []
    return any(d.get("errorCode") in ("UNREGISTERED", "INVALID_ARGUMENT") for d in details if isinstance(d, dict))
