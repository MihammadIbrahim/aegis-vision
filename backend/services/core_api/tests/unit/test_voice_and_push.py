"""Decision D-025: LiveKit room tokens and room closing, the disabled provider, the FCM HTTP v1 sender (service-account
assertion, access-token cache, high-priority Android message, dead tokens), push texts without names, and the
settings rules for VOICE_CALL_PROVIDER and PUSH_DELIVERY_MODE. No network is used."""

import json

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import ValidationError

from src.application.interfaces.i_push_sender import PushMessage, PushOutcome
from src.config.settings import Settings
from src.domain.exceptions.domain_errors import ServiceUnavailable
from src.infrastructure.background.push_dispatch import message_for
from src.infrastructure.push.fcm_sender import FcmPushSender, NoPushSender
from src.infrastructure.voice.factory import build_voice_provider
from src.infrastructure.voice.livekit_provider import DisabledVoiceProvider, LiveKitVoiceProvider

SECRET = "unit-test-livekit-secret-0123456789"
BASE = {
    "database_url": "postgresql+asyncpg://a:b@127.0.0.1:1/x",
    "platform_database_url": "postgresql+asyncpg://a:b@127.0.0.1:1/x",
    "migration_database_url": "postgresql+psycopg://a:b@127.0.0.1:1/x",
    "jwt_secret_key": "j" * 40,
    "_env_file": None,
}


def test_disabled_provider_refuses_with_a_clear_code() -> None:
    provider = DisabledVoiceProvider()
    assert (provider.provider, provider.configured) == ("none", False)
    with pytest.raises(ServiceUnavailable) as refused:
        provider.grant(room="r", identity="i", name="n", metadata={})
    assert refused.value.code == "VOICE_CALL_NOT_CONFIGURED"


def test_livekit_tokens_allow_one_room_with_the_microphone_only() -> None:
    provider = LiveKitVoiceProvider(
        url="wss://x.livekit.cloud/", api_key="APIkey", api_secret=SECRET, token_ttl_seconds=600
    )
    grant = provider.grant(room="cctv-call-1", identity="user-1", name="Gul A", metadata={"role": "GUARD"})
    claims = jwt.decode(grant.token, SECRET, algorithms=["HS256"])
    assert (claims["iss"], claims["sub"], claims["name"]) == ("APIkey", "user-1", "Gul A")
    assert json.loads(claims["metadata"]) == {"role": "GUARD"}
    assert claims["video"]["room"] == "cctv-call-1" and claims["video"]["canPublishSources"] == ["microphone"]
    assert claims["video"]["canPublishData"] is False and "roomAdmin" not in claims["video"]
    assert claims["exp"] - claims["iat"] == 600 and grant.url == "wss://x.livekit.cloud"
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(grant.token, "another-secret-of-sufficient-length", algorithms=["HS256"])


@pytest.mark.parametrize(("status", "closed"), [(200, True), (404, True), (500, False)])
async def test_closing_a_livekit_room_is_best_effort(status: int, closed: bool) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json={})

    provider = LiveKitVoiceProvider(
        url="wss://x.livekit.cloud",
        api_key="APIkey",
        api_secret=SECRET,
        token_ttl_seconds=600,
        transport=httpx.MockTransport(handler),
    )
    assert await provider.close_room("cctv-call-1") is closed
    request = seen[0]
    assert str(request.url) == "https://x.livekit.cloud/twirp/livekit.RoomService/DeleteRoom"
    admin = jwt.decode(request.headers["Authorization"].removeprefix("Bearer "), SECRET, algorithms=["HS256"])
    assert admin["video"] == {"roomCreate": True} and json.loads(request.content) == {"room": "cctv-call-1"}


async def test_closing_a_room_survives_a_network_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    provider = LiveKitVoiceProvider(
        url="ws://localhost:7880",
        api_key="k",
        api_secret=SECRET,
        token_ttl_seconds=60,
        transport=httpx.MockTransport(handler),
    )
    assert await provider.close_room("cctv-call-1") is False


def _service_account() -> dict[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    return {
        "client_email": "push@test-project.iam.gserviceaccount.com",
        "private_key": pem,
        "token_uri": "https://oauth.test/token",
    }


async def test_fcm_sends_a_high_priority_alarm_notification_and_caches_the_access_token() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "oauth.test":
            return httpx.Response(200, json={"access_token": "ya29.test", "expires_in": 3600})
        return httpx.Response(200, json={"name": "projects/p/messages/1"})

    sender = FcmPushSender(project_id="p", service_account=_service_account(), transport=httpx.MockTransport(handler))
    message = PushMessage(
        title="New security alert", body="Open the app.", data={"kind": "ALERT_NEW", "alert_id": "a1"}
    )
    assert await sender.send("token-1", message) is PushOutcome.SENT
    assert await sender.send("token-2", message) is PushOutcome.SENT
    assert [r.url.host for r in requests] == ["oauth.test", "fcm.googleapis.com", "fcm.googleapis.com"]
    sent = json.loads(requests[1].content)["message"]
    assert sent["token"] == "token-1" and sent["data"] == {"kind": "ALERT_NEW", "alert_id": "a1"}
    assert sent["android"]["priority"] == "HIGH" and sent["android"]["notification"]["channel_id"] == "security-alarm"
    assert requests[1].headers["Authorization"] == "Bearer ya29.test"
    assertion = dict(x.split("=", 1) for x in requests[0].content.decode().split("&"))["assertion"]
    assert jwt.decode(assertion, options={"verify_signature": False})["scope"].endswith("firebase.messaging")


@pytest.mark.parametrize(
    ("status", "body", "outcome"),
    [
        (404, {"error": {"status": "NOT_FOUND"}}, PushOutcome.INVALID_TOKEN),
        (
            400,
            {"error": {"status": "INVALID_ARGUMENT", "details": [{"errorCode": "UNREGISTERED"}]}},
            PushOutcome.INVALID_TOKEN,
        ),
        (503, {"error": {"status": "UNAVAILABLE"}}, PushOutcome.FAILED),
    ],
)
async def test_fcm_reports_dead_tokens(status: int, body: dict[str, object], outcome: PushOutcome) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth.test":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        return httpx.Response(status, json=body)

    sender = FcmPushSender(project_id="p", service_account=_service_account(), transport=httpx.MockTransport(handler))
    assert await sender.send("x", PushMessage(title="t", body="b")) is outcome


async def test_no_push_sender_sends_nothing() -> None:
    assert NoPushSender().mode == "none"
    assert await NoPushSender().send("x", PushMessage(title="t", body="b")) is PushOutcome.FAILED


def test_push_texts_name_no_place_or_person() -> None:
    for kind in (
        "ALERT_NEW",
        "ALERT_ESCALATED",
        "ALERT_AUTO_ESCALATED",
        "ALERT_ASSISTANCE_BROADCAST",
        "INCIDENT_INVITED",
    ):
        message = message_for(kind, "n1", "a1", None)
        assert message.data["kind"] == kind and message.data["alert_id"] == "a1" and "incident_id" not in message.data
        assert message.title and message.body and message.channel_id == "security-alarm"


def test_voice_and_push_settings_must_be_complete() -> None:
    assert Settings(**BASE).voice_call_provider == "none" and Settings(**BASE).push_delivery_mode == "none"  # type: ignore[arg-type]
    assert isinstance(build_voice_provider(Settings(**BASE)), DisabledVoiceProvider)  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="LIVEKIT_API_KEY and LIVEKIT_API_SECRET"):
        Settings(**BASE, voice_call_provider="livekit", livekit_url="wss://x")  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="LIVEKIT_URL must start with wss://"):
        Settings(
            **BASE, voice_call_provider="livekit", livekit_url="https://x", livekit_api_key="k", livekit_api_secret="s"
        )  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="FCM_PROJECT_ID and FCM_SERVICE_ACCOUNT_FILE"):
        Settings(**BASE, push_delivery_mode="fcm")  # type: ignore[arg-type]
    livekit = Settings(
        **BASE,
        voice_call_provider="livekit",
        livekit_url="wss://x",
        livekit_api_key="k",
        livekit_api_secret="s",  # type: ignore[arg-type]
    )
    assert build_voice_provider(livekit).configured
    # Empty values in .env mean "not set".
    assert Settings(**BASE, livekit_url="", fcm_project_id="").livekit_url is None  # type: ignore[arg-type]
