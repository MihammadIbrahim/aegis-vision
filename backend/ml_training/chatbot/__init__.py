"""Offline fine-tuning and evaluation of the AI Investigation & Security Analytics Chatbot (decision D-027).

Qwen2.5-7B-Instruct (or 14B) with 4-bit QLoRA on an RTX 5080, trained on the runtime's own tool catalogue and prompts
(``assistant_engine`` from ``backend/services/ai_rag_and_agent``), evaluated with grounded metrics, and exported to
Ollama for ``AI_LLM_PROVIDER=ollama``. Nothing here downloads weights or needs a GPU until ``train`` / ``export``.
"""
