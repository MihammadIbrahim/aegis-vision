"""The AI chatbot engine chosen by AI_LLM_PROVIDER (decision D-027). The engine is the ``assistant_engine`` package of
``backend/services/ai_rag_and_agent`` (installed with ``pip install -e``); it holds no database connection."""

from assistant_engine import AssistantEngine, build_provider

from src.config.settings import Settings


def build_assistant_engine(settings: Settings) -> AssistantEngine:
    key = settings.ai_llm_api_key.get_secret_value() if settings.ai_llm_api_key else None
    provider = build_provider(
        settings.ai_llm_provider,
        base_url=settings.ai_llm_base_url,
        model_name=settings.ai_llm_model if settings.ai_llm_provider != "deterministic" else "rules-v1",
        api_key=key,
        timeout_seconds=settings.ai_llm_timeout_seconds,
        allow_external=settings.ai_llm_allow_external,
    )
    return AssistantEngine(provider)
