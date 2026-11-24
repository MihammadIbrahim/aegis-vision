import json
import stat

import httpx
import pytest

from src.application.interfaces.i_email_service import EmailDeliveryError, EmailMessage
from src.config.settings import Settings
from src.infrastructure.email.brevo_service import BREVO_API_URL, BrevoEmailSender
from src.infrastructure.email.dev_outbox import DevOutboxEmailSender
from src.infrastructure.email.factory import UnconfiguredEmailSender, build_email_sender

API_KEY = "xkeysib-test-only-not-a-real-key"
MESSAGE = EmailMessage(
    to_email="maryam@example.com",
    subject="Your organization registration code",
    text_body="Your verification code is: 123456",
    html_body="<p>Your verification code is: <strong>123456</strong></p>",
)


def _brevo(handler) -> BrevoEmailSender:  # type: ignore[no-untyped-def]
    return BrevoEmailSender(
        api_key=API_KEY,
        sender_email="no-reply@example.com",
        sender_name="AI CCTV Security",
        transport=httpx.MockTransport(handler),
    )


async def test_brevo_sends_one_request_in_the_documented_shape() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201, json={"messageId": "<abc@smtp-relay.mailin.fr>"})

    await _brevo(handler).send(MESSAGE)
    [request] = requests
    assert request.method == "POST" and str(request.url) == BREVO_API_URL
    assert request.headers["api-key"] == API_KEY
    assert json.loads(request.content) == {
        "sender": {"email": "no-reply@example.com", "name": "AI CCTV Security"},
        "to": [{"email": "maryam@example.com"}],
        "subject": "Your organization registration code",
        "textContent": "Your verification code is: 123456",
        "htmlContent": "<p>Your verification code is: <strong>123456</strong></p>",
    }


@pytest.mark.parametrize("status", [400, 401, 429, 500])
async def test_brevo_refusal_raises_without_revealing_secrets(status: int) -> None:
    sender = _brevo(lambda request: httpx.Response(status, json={"code": "unauthorized", "message": "Key not found"}))
    with pytest.raises(EmailDeliveryError) as error:
        await sender.send(MESSAGE)
    text = str(error.value)
    assert str(status) in text
    for secret in (API_KEY, "123456", "maryam@example.com"):
        assert secret not in text


async def test_brevo_network_failure_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(EmailDeliveryError, match="could not be reached"):
        await _brevo(handler).send(MESSAGE)


def test_brevo_sender_never_shows_its_key() -> None:
    assert API_KEY not in repr(_brevo(lambda request: httpx.Response(201)))


async def test_dev_outbox_writes_a_private_file_and_says_it_was_not_emailed(tmp_path) -> None:
    outbox = tmp_path / "outbox"
    sender = DevOutboxEmailSender(outbox)
    await sender.send(MESSAGE)
    [file] = list(outbox.iterdir())
    content = file.read_text()
    assert "To: maryam@example.com" in content and "123456" in content and "NOT emailed" in content
    assert stat.S_IMODE(file.stat().st_mode) == 0o600
    assert stat.S_IMODE(outbox.stat().st_mode) == 0o700
    assert sender.delivery_channel == "DEV_OUTBOX"


async def test_dev_outbox_failure_raises(tmp_path) -> None:
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("")
    with pytest.raises(EmailDeliveryError):
        await DevOutboxEmailSender(blocker).send(MESSAGE)


async def test_unconfigured_sender_never_claims_to_send() -> None:
    sender = UnconfiguredEmailSender()
    assert sender.is_configured is False
    with pytest.raises(EmailDeliveryError):
        await sender.send(MESSAGE)


def _settings(**extra: object) -> Settings:
    return Settings(
        database_url="x",
        platform_database_url="x",
        migration_database_url="x",
        jwt_secret_key="j" * 40,
        _env_file=None,  # type: ignore[call-arg]
        **extra,  # type: ignore[arg-type]
    )


def test_factory_follows_email_delivery_mode(tmp_path) -> None:
    assert isinstance(build_email_sender(_settings()), UnconfiguredEmailSender)
    brevo = build_email_sender(
        _settings(email_delivery_mode="brevo", brevo_api_key=API_KEY, brevo_sender_email="no-reply@example.com")
    )
    assert isinstance(brevo, BrevoEmailSender)
    outbox = build_email_sender(
        _settings(email_delivery_mode="dev_outbox", app_env="development", dev_outbox_dir=tmp_path)
    )
    assert isinstance(outbox, DevOutboxEmailSender)
