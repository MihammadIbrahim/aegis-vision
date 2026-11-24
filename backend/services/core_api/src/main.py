"""Core API entry point.

Run with:  uvicorn src.main:create_app --factory
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime

from assistant_engine import AssistantEngine
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from src.application.interfaces.i_campus_media_storage import ICampusMediaStorage
from src.application.interfaces.i_email_service import IEmailSender
from src.application.interfaces.i_push_sender import IPushSender
from src.application.interfaces.i_voice_call_provider import IVoiceCallProvider
from src.config.settings import Settings, load_settings
from src.infrastructure.assistant.factory import build_assistant_engine
from src.infrastructure.background import alert_escalation, push_dispatch
from src.infrastructure.database.connection import create_engine, create_session_factory
from src.infrastructure.email.factory import build_email_sender
from src.infrastructure.push.factory import build_push_sender
from src.infrastructure.security.otp import HmacOtpCodec, derive_otp_key
from src.infrastructure.security.rate_limiter import SlidingWindowRateLimiter
from src.infrastructure.storage.local_ssd_storage import LocalCampusMediaStorage
from src.infrastructure.voice.factory import build_voice_provider
from src.presentation.api.v1.api_router import api_router
from src.presentation.error_handlers import register_error_handlers
from src.presentation.realtime.hub import RealtimeHub


def create_app(
    settings: Settings | None = None,
    engine: AsyncEngine | None = None,
    platform_engine: AsyncEngine | None = None,
    email_sender: IEmailSender | None = None,
    media_storage: ICampusMediaStorage | None = None,
    voice_provider: IVoiceCallProvider | None = None,
    push_sender: IPushSender | None = None,
    assistant_engine: AssistantEngine | None = None,
) -> FastAPI:
    settings = settings or load_settings()  # fails fast with a readable list of missing variables
    engine = engine or create_engine(settings.database_url)
    platform_engine = platform_engine or create_engine(settings.platform_database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        escalation: asyncio.Task[None] | None = None
        if settings.alert_escalation_poll_seconds > 0:
            escalation = asyncio.create_task(
                alert_escalation.run_forever(app.state.session_factory, settings.alert_escalation_poll_seconds)
            )
        pushes: asyncio.Task[None] | None = None
        if app.state.push_sender.mode != "none":
            pushes = asyncio.create_task(
                push_dispatch.run_forever(
                    app.state.session_factory,
                    app.state.push_sender,
                    settings.push_poll_seconds,
                    settings.push_max_age_seconds,
                )
            )
        await app.state.realtime_hub.ensure_started()
        yield
        await app.state.realtime_hub.stop()
        await app.state.assistant_engine.aclose()
        for task in (escalation, pushes):
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        await engine.dispose()
        await platform_engine.dispose()

    app = FastAPI(title="CCTV Security - Core API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.started_at = datetime.now(UTC)
    app.state.platform_engine = platform_engine
    app.state.session_factory = create_session_factory(engine)
    app.state.platform_session_factory = create_session_factory(platform_engine)
    # Chosen by EMAIL_DELIVERY_MODE unless given (tests pass a fake).
    app.state.email_sender = email_sender or build_email_sender(settings)
    app.state.media_storage = media_storage or LocalCampusMediaStorage(settings.cctv_storage_root)
    app.state.otp_codec = HmacOtpCodec(derive_otp_key(settings))
    # Decision D-025: voice calls (VOICE_CALL_PROVIDER), push (PUSH_DELIVERY_MODE) and the real-time hub.
    app.state.voice_provider = voice_provider or build_voice_provider(settings)
    app.state.push_sender = push_sender or build_push_sender(settings)
    app.state.realtime_hub = RealtimeHub(engine, app.state.session_factory, settings)
    # Decision D-027: the read-only AI chatbot engine (AI_LLM_PROVIDER; tests pass one with a mocked model).
    app.state.assistant_engine = assistant_engine or build_assistant_engine(settings)
    app.state.registration_rate_limiter = SlidingWindowRateLimiter(
        settings.org_registration_ip_limit_per_hour, window_seconds=3600
    )
    app.state.login_rate_limiter = SlidingWindowRateLimiter(settings.org_login_ip_limit_per_hour, window_seconds=3600)
    app.state.password_reset_rate_limiter = SlidingWindowRateLimiter(
        settings.password_reset_ip_limit_per_hour, window_seconds=3600
    )
    register_error_handlers(app)
    app.include_router(api_router)
    return app
