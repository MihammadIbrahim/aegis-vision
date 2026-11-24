from src.application.interfaces.i_voice_call_provider import IVoiceCallProvider
from src.config.settings import Settings
from src.infrastructure.voice.livekit_provider import DisabledVoiceProvider, LiveKitVoiceProvider


def build_voice_provider(settings: Settings) -> IVoiceCallProvider:
    """Chosen by VOICE_CALL_PROVIDER (settings validation guarantees the LiveKit values when selected)."""
    if settings.voice_call_provider == "livekit":
        assert settings.livekit_url and settings.livekit_api_key and settings.livekit_api_secret
        return LiveKitVoiceProvider(
            url=settings.livekit_url,
            api_key=settings.livekit_api_key.get_secret_value(),
            api_secret=settings.livekit_api_secret.get_secret_value(),
            token_ttl_seconds=settings.livekit_token_ttl_seconds,
            request_timeout_seconds=settings.livekit_request_timeout_seconds,
        )
    return DisabledVoiceProvider()
