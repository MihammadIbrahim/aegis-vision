"""LLM providers (decision D-027): ``AI_LLM_PROVIDER=deterministic|ollama|openai_compatible``.

* ``deterministic`` — no model: the planner and the synthesizer are rule-based, 100 % offline, no keys, no downloads.
  The default for development, CI and tests.
* ``ollama`` — a local Ollama server (``AI_LLM_BASE_URL``, default http://127.0.0.1:11434) running Qwen2.5-7B-Instruct
  (``qwen2.5:7b``; ``qwen2.5:14b`` optional) or the fine-tuned adapter exported by ``ml_training/chatbot``. Nothing
  leaves the server.
* ``openai_compatible`` — any ``/v1/chat/completions`` endpoint (vLLM, SGLang, or an external service with
  ``AI_LLM_API_KEY``). A non-local endpoint is refused unless ``AI_LLM_ALLOW_EXTERNAL=true``.

Whatever the provider, only redacted text is sent (``guardrails.redact``): no video, frames, snapshots, passwords,
codes, tokens, emails or phone numbers. Failures raise ``LLMUnavailable``; the engine then answers deterministically
and says so.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx


class LLMUnavailable(Exception):
    """The configured model could not be reached or gave no usable answer."""


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    available: bool
    detail: str


class LLMProvider:
    name = "deterministic"
    is_llm = False

    def __init__(self, model_name: str = "rules-v1") -> None:
        self.model_name = model_name

    @property
    def label(self) -> str:
        return "Local deterministic engine"

    async def status(self) -> ProviderStatus:
        return ProviderStatus(True, "Built-in rule-based planner and grounded answer writer (no model, no network).")

    async def chat_json(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        raise LLMUnavailable("The deterministic provider has no language model.")

    async def chat_text(self, messages: list[dict[str, str]]) -> str:
        raise LLMUnavailable("The deterministic provider has no language model.")

    async def aclose(self) -> None:
        return None


DeterministicProvider = LLMProvider


def is_local_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1") or host.endswith(".local") or host.startswith(("10.", "192.168."))


class _HttpProvider(LLMProvider):
    is_llm = True

    def __init__(
        self,
        base_url: str,
        model_name: str,
        *,
        api_key: str | None = None,
        timeout_seconds: float = 30.0,
        allow_external: bool = False,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(model_name)
        self.base_url = base_url.rstrip("/")
        self.blocked = not allow_external and not is_local_url(self.base_url)
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.AsyncClient(timeout=timeout_seconds, headers=headers, transport=transport)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        if self.blocked:
            raise LLMUnavailable("The LLM endpoint is not local and AI_LLM_ALLOW_EXTERNAL is false.")
        try:
            response = await self._client.post(self.base_url + path, json=body)
        except httpx.HTTPError as error:
            raise LLMUnavailable(f"{self.label} is unreachable ({type(error).__name__}).") from None
        if response.status_code >= 400:
            raise LLMUnavailable(f"{self.label} answered HTTP {response.status_code}.")
        try:
            value: dict[str, Any] = response.json()
        except ValueError:
            raise LLMUnavailable(f"{self.label} sent an unreadable answer.") from None
        return value

    async def _get(self, path: str) -> dict[str, Any]:
        try:
            response = await self._client.get(self.base_url + path)
        except httpx.HTTPError as error:
            raise LLMUnavailable(f"unreachable ({type(error).__name__})") from None
        if response.status_code >= 400:
            raise LLMUnavailable(f"HTTP {response.status_code}")
        try:
            value: dict[str, Any] = response.json()
        except ValueError:
            raise LLMUnavailable("unreadable answer") from None
        return value

    @staticmethod
    def _json(text: str) -> dict[str, Any]:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise LLMUnavailable("The model did not return JSON.")
        try:
            value = json.loads(text[start : end + 1])
        except ValueError:
            raise LLMUnavailable("The model returned invalid JSON.") from None
        if not isinstance(value, dict):
            raise LLMUnavailable("The model returned JSON that is not an object.")
        return value


class OllamaProvider(_HttpProvider):
    name = "ollama"

    @property
    def label(self) -> str:
        return f"Ollama {self.model_name}"

    async def status(self) -> ProviderStatus:
        if self.blocked:
            return ProviderStatus(False, "AI_LLM_BASE_URL is not local and AI_LLM_ALLOW_EXTERNAL is false.")
        try:
            tags = await self._get("/api/tags")
        except LLMUnavailable as error:
            return ProviderStatus(False, f"Ollama at {self.base_url} is {error}.")
        names = {m.get("name") for m in tags.get("models", []) if isinstance(m, dict)}
        wanted = {self.model_name, f"{self.model_name}:latest"}
        if not names & wanted:
            return ProviderStatus(False, f"Ollama is running but the model {self.model_name} is not pulled.")
        return ProviderStatus(
            True, f"Ollama at {self.base_url} with {self.model_name} (local; no data leaves the server)."
        )

    async def _chat(self, messages: list[dict[str, str]], json_mode: bool) -> str:
        body: dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "stream": False,
            "options": {"temperature": 0, "seed": 7},
        }
        if json_mode:
            body["format"] = "json"
        answer = await self._post("/api/chat", body)
        content = (answer.get("message") or {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise LLMUnavailable("Ollama returned an empty answer.")
        return content

    async def chat_json(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        return self._json(await self._chat(messages, True))

    async def chat_text(self, messages: list[dict[str, str]]) -> str:
        return (await self._chat(messages, False)).strip()


class OpenAICompatibleProvider(_HttpProvider):
    name = "openai_compatible"

    @property
    def label(self) -> str:
        return f"OpenAI-compatible {self.model_name}"

    async def status(self) -> ProviderStatus:
        if self.blocked:
            return ProviderStatus(False, "AI_LLM_BASE_URL is not local and AI_LLM_ALLOW_EXTERNAL is false.")
        try:
            await self._get("/models")
        except LLMUnavailable as error:
            return ProviderStatus(False, f"The endpoint {self.base_url} is {error}.")
        where = "local" if is_local_url(self.base_url) else "external (redacted summaries only)"
        return ProviderStatus(True, f"{self.base_url} with {self.model_name} ({where}).")

    async def _chat(self, messages: list[dict[str, str]], json_mode: bool) -> str:
        body: dict[str, Any] = {"model": self.model_name, "messages": messages, "temperature": 0, "seed": 7}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        answer = await self._post("/chat/completions", body)
        try:
            content = answer["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise LLMUnavailable("The endpoint returned no message.") from None
        if not isinstance(content, str) or not content.strip():
            raise LLMUnavailable("The endpoint returned an empty answer.")
        return content

    async def chat_json(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        return self._json(await self._chat(messages, True))

    async def chat_text(self, messages: list[dict[str, str]]) -> str:
        return (await self._chat(messages, False)).strip()


def build_provider(
    provider: str,
    *,
    base_url: str | None = None,
    model_name: str = "qwen2.5:7b",
    api_key: str | None = None,
    timeout_seconds: float = 30.0,
    allow_external: bool = False,
    transport: httpx.AsyncBaseTransport | None = None,
) -> LLMProvider:
    if provider == "ollama":
        return OllamaProvider(
            base_url or "http://127.0.0.1:11434",
            model_name,
            timeout_seconds=timeout_seconds,
            allow_external=allow_external,
            transport=transport,
        )
    if provider == "openai_compatible":
        if not base_url:
            raise ValueError("AI_LLM_BASE_URL is required for AI_LLM_PROVIDER=openai_compatible.")
        return OpenAICompatibleProvider(
            base_url,
            model_name,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            allow_external=allow_external,
            transport=transport,
        )
    if provider == "deterministic":
        return LLMProvider()
    raise ValueError(f"Unknown AI_LLM_PROVIDER {provider!r}.")
