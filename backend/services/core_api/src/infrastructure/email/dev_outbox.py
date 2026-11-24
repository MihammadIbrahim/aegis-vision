"""Development-only "outbox": writes each email to a file instead of sending it.

Allowed only when APP_ENV=development (enforced in settings). Lets you read OTP emails
locally without an email provider. The files contain the codes, so the folder is
created readable by your user only, and it is git-ignored. Nothing is written to logs.
"""

import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from src.application.interfaces.i_email_service import EmailDeliveryError, EmailMessage


class DevOutboxEmailSender:
    is_configured = True
    delivery_channel = "DEV_OUTBOX"

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    async def send(self, message: EmailMessage) -> None:
        now = datetime.now(UTC)
        path = self._directory / f"{now:%Y%m%dT%H%M%S%fZ}_{uuid4().hex[:8]}.txt"
        content = (
            f"To: {message.to_email}\n"
            f"Subject: {message.subject}\n"
            f"Date: {now.isoformat()}\n"
            "X-Delivery: development outbox only; this message was NOT emailed.\n"
            "\n"
            f"{message.text_body}"
        )
        try:
            self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                file.write(content)
        except OSError as exc:
            raise EmailDeliveryError(f"The development outbox could not be written ({type(exc).__name__}).") from None
