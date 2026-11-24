"""Choose the email sender from EMAIL_DELIVERY_MODE."""

from src.application.interfaces.i_email_service import (
    EmailDeliveryError,
    EmailMessage,
    IEmailSender,
)
from src.config.settings import Settings
from src.infrastructure.email.brevo_service import BrevoEmailSender
from src.infrastructure.email.dev_outbox import DevOutboxEmailSender


class UnconfiguredEmailSender:
    """EMAIL_DELIVERY_MODE=none: no provider. Endpoints that must send email answer 503."""

    is_configured = False
    delivery_channel = "NONE"

    async def send(self, message: EmailMessage) -> None:
        raise EmailDeliveryError("No email provider is configured.")


def build_email_sender(settings: Settings) -> IEmailSender:
    if settings.email_delivery_mode == "brevo":
        # Settings validation guarantees both values are present in this mode.
        assert settings.brevo_api_key is not None and settings.brevo_sender_email is not None
        return BrevoEmailSender(
            api_key=settings.brevo_api_key.get_secret_value(),
            sender_email=settings.brevo_sender_email,
            sender_name=settings.brevo_sender_name,
            api_url=settings.brevo_api_url,
            timeout_seconds=settings.email_send_timeout_seconds,
        )
    if settings.email_delivery_mode == "dev_outbox":
        return DevOutboxEmailSender(settings.dev_outbox_dir)
    return UnconfiguredEmailSender()
