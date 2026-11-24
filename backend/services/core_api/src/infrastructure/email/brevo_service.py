"""Real email delivery through Brevo's transactional email HTTP API.

POST https://api.brevo.com/v3/smtp/email with the ``api-key`` header. Brevo answers
201 once it has accepted the message for delivery. Any other answer, or no answer,
raises ``EmailDeliveryError``. Error messages never include the API key, the
recipient, or the message body.
"""

import httpx

from src.application.interfaces.i_email_service import EmailDeliveryError, EmailMessage

BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"


class BrevoEmailSender:
    is_configured = True
    delivery_channel = "EMAIL"

    def __init__(
        self,
        *,
        api_key: str,
        sender_email: str,
        sender_name: str,
        api_url: str = BREVO_API_URL,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,  # tests pass a mock transport
    ) -> None:
        self._api_key = api_key
        self._sender = {"email": sender_email, "name": sender_name}
        self._api_url = api_url
        self._timeout = timeout_seconds
        self._transport = transport

    def __repr__(self) -> str:
        return f"BrevoEmailSender(sender={self._sender['email']!r}, api_key=<hidden>)"

    async def send(self, message: EmailMessage) -> None:
        payload = {
            "sender": self._sender,
            "to": [{"email": message.to_email}],
            "subject": message.subject,
            "textContent": message.text_body,
            "htmlContent": message.html_body,
        }
        headers = {"api-key": self._api_key, "accept": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                response = await client.post(self._api_url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise EmailDeliveryError(f"The email provider could not be reached ({type(exc).__name__}).") from None
        if not 200 <= response.status_code < 300:
            raise EmailDeliveryError(f"The email provider refused the message (HTTP {response.status_code}).")
