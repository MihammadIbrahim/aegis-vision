"""Outgoing email, behind an interface so the provider can be replaced.

Implementations:
- Brevo HTTP API (``infrastructure/email/brevo_service.py``) for real delivery.
- Development outbox (``infrastructure/email/dev_outbox.py``): writes messages to local
  files instead of sending them. Allowed only when APP_ENV=development.
- "Not configured" (``infrastructure/email/factory.py``): refuses every message, so the API
  answers 503 instead of pretending an email was sent.
- Tests use an in-memory fake.
"""

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True, slots=True)
class EmailMessage:
    to_email: str
    subject: str
    # The bodies may contain a one-time code: never log them (``repr=False``).
    text_body: str = field(repr=False)
    html_body: str = field(repr=False)


class EmailDeliveryError(Exception):
    """The message was not accepted for delivery. The text never contains secrets."""


class IEmailSender(Protocol):
    @property
    def is_configured(self) -> bool:
        """False when no provider is set up; ``send`` would always fail."""
        ...

    @property
    def delivery_channel(self) -> str:
        """Shown to clients: "EMAIL" (a real provider accepted it) or "DEV_OUTBOX"."""
        ...

    async def send(self, message: EmailMessage) -> None:
        """Return only once the provider has accepted the message; otherwise raise
        ``EmailDeliveryError``."""
        ...
