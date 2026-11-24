"""Read-only AI Investigation & Security Analytics Chatbot engine (decision D-027).

Pure Python (no database): core_api supplies the vocabulary of the caller's campus and runs the catalogue's read-only
tools with the caller's Row-Level Security; this package plans, guards and writes grounded answers.
"""

from assistant_engine.engine import SAFETY_NOTE, AssistantEngine, help_text
from assistant_engine.providers import LLMProvider, LLMUnavailable, ProviderStatus, build_provider
from assistant_engine.types import (
    CameraRef,
    Citation,
    EngineAnswer,
    LocationRef,
    ToolCall,
    ToolExecutor,
    ToolResult,
    Vocabulary,
)

__all__ = [
    "SAFETY_NOTE",
    "AssistantEngine",
    "CameraRef",
    "Citation",
    "EngineAnswer",
    "LLMProvider",
    "LLMUnavailable",
    "LocationRef",
    "ProviderStatus",
    "ToolCall",
    "ToolExecutor",
    "ToolResult",
    "Vocabulary",
    "build_provider",
    "help_text",
]
