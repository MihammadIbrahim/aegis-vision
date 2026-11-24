"""Test doubles."""

import re

from src.application.interfaces.i_email_service import EmailDeliveryError, EmailMessage

_CODE = re.compile(r"verification code is: ([0-9]{6})")


class FakeEmailSender:
    """Keeps messages in memory instead of sending them. ``fail=True`` makes every send fail,
    like a provider that refuses the message."""

    def __init__(self, *, configured: bool = True, fail: bool = False) -> None:
        self.messages: list[EmailMessage] = []
        self.is_configured = configured
        self.fail = fail
        self.delivery_channel = "EMAIL"

    async def send(self, message: EmailMessage) -> None:
        if self.fail or not self.is_configured:
            raise EmailDeliveryError("The fake provider refused the message.")
        self.messages.append(message)

    def last_code(self, to_email: str | None = None) -> str:
        """The 6-digit code in the latest message (to ``to_email`` if given)."""
        messages = [m for m in self.messages if to_email is None or m.to_email.lower() == to_email.lower()]
        assert messages, "no email was sent"
        match = _CODE.search(messages[-1].text_body)
        assert match, "the email has no code"
        return match.group(1)
