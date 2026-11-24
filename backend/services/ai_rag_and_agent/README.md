# ai_rag_and_agent — read-only AI Investigation & Security Analytics Chatbot engine (decision D-027)

The D-027 design replaced the planned SOP/PDF RAG assistant and the approval-to-execute LangGraph agent with one
**read-only, zero-upload, database-driven chatbot**. This folder is its engine, the Python package
`assistant_engine`. It holds no database connection: `core_api` imports it in-process
(`pip install -e backend/services/ai_rag_and_agent`), gives it the caller's campus vocabulary, and runs its read-only
tools inside the caller's own request transaction, so PostgreSQL Row-Level Security applies to every query exactly as
on the web pages. The API is `/api/v1/assistant/*` (see `backend/docs/api_contracts.md`).

| Module | Role |
|---|---|
| `catalog.py` | the 13 read-only tools of the six capability areas, their JSON schemas and validation (single source of truth for the planner, the LLM prompts, the audit records and `ml_training/chatbot`) |
| `dates.py` | natural-language periods → exact ranges in the organization's time zone; comparison baselines (month-over-month, year-over-year) |
| `planner.py` | deterministic intent and entity extraction, follow-up context ("those", "there", "what about …"); normalisation of an LLM planner's simple arguments |
| `guardrails.py` | refusals before any model (accusations / identity claims, state-changing requests, uploads, excluded data); redaction of what is sent to a model; numeric grounding and unsafe-claim checks of model output |
| `synthesizer.py` | the grounded answer, blocks (metrics, tables, bars, timelines, notices) and citations, from tool results only |
| `providers.py` | `AI_LLM_PROVIDER=deterministic` (default) \| `ollama` (local Qwen2.5) \| `openai_compatible`; non-local endpoints refused unless `AI_LLM_ALLOW_EXTERNAL=true` |
| `prompts.py` | planner and phraser prompts, the Qwen2.5 ChatML format (shared with the fine-tuning pipeline) |
| `engine.py` | guardrails → plan (LLM, validated, or deterministic) → tools → grounded synthesis → optional verified rephrasing |

```bash
cd backend/services/ai_rag_and_agent
../../.venv/bin/python -m pytest -q        # 32 tests, no database, no model, no network (HTTP is mocked)
```

Fine-tuning and evaluation of Qwen2.5 for this engine: `backend/ml_training/chatbot/README.md`.
