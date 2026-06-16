"""
agents.py — Builds the Strands agent: model + system prompt + tools.

Model choice goes through LiteLLM, which is a thin adapter that speaks to many
providers with one interface. That's deliberate: you said you want to A/B test
Groq vs OpenAI. With LiteLLM that's a one-line change (MODEL_PROVIDER in .env),
not a code rewrite. LiteLLM reads the API key from the matching env var
(GROQ_API_KEY / OPENAI_API_KEY) automatically.

Why an agent (not a fixed pipeline)? The agent runs a loop: it reads the
question, decides whether to ask you a clarifying question or call query_tracker,
reads the result, maybe queries again, then answers. That loop is what lets it
"answer anything" and recover from a wrong first guess — the thing a rigid
3-step pipeline couldn't do.
"""

import os
from dotenv import load_dotenv

from strands import Agent
from strands.models.litellm import LiteLLMModel

from prompts import build_system_prompt
from tools import query_tracker
from db import get_schema_text

# override=True so THIS project's .env wins over any machine-wide OS env vars.
# (This user has global OPENAI_API_KEY / OPENAI_API_BASE / OPENAI_MODEL pointing
# at OpenRouter from another project; without override those silently hijack our
# OpenAI calls — that was the "No endpoints found for mixtral" error.)
load_dotenv(override=True)

# "groq" (default, free) or "openai". This is the only line to change for the
# bake-off. The model id per provider also comes from .env.
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "groq").lower()


def build_model() -> LiteLLMModel:
    """
    Construct the LiteLLM-backed model for the chosen provider.

    temperature=0 → deterministic. For a CEO-facing data tool we want the same
    question to produce the same SQL and the same answer every time.
    """
    # temperature=0 for determinism; num_retries so a transient rate-limit (429)
    # waits and retries instead of crashing the whole answer.
    params = {"temperature": 0.0, "num_retries": 3}

    if MODEL_PROVIDER == "openai":
        model_id = os.getenv("OPENAI_MODEL", "gpt-4o")
        litellm_id = f"openai/{model_id}"
        # Pass key + endpoint EXPLICITLY so we never inherit a stray global
        # OPENAI_API_BASE (e.g. an OpenRouter base). Defaults to real OpenAI;
        # set OPENAI_BASE_URL in .env only if you truly want a custom endpoint.
        params["api_key"] = os.getenv("OPENAI_API_KEY")
        params["api_base"] = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    else:  # groq
        # llama-3.3-70b-versatile, not 8b-instant: tool-calling + correct SQL are
        # reasoning tasks. The old project found 8b broke formats and wrote buggy
        # code; the 70b is far more reliable. Both are free on Groq.
        model_id = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
        litellm_id = f"groq/{model_id}"
        params["api_key"] = os.getenv("GROQ_API_KEY")

    return LiteLLMModel(model_id=litellm_id, params=params)


def create_agent() -> Agent:
    """
    Create a fresh agent. Call this ONCE per chat session and reuse the instance
    so Strands' built-in conversation memory accumulates across turns.

    The schema is read live and baked into the system prompt here, so the agent
    starts every session already knowing the tracker's columns and sample values
    — no wasted tool call just to discover the schema.
    """
    schema_text = get_schema_text()
    system_prompt = build_system_prompt(schema_text)

    return Agent(
        model=build_model(),
        system_prompt=system_prompt,
        tools=[query_tracker],
        # Silence Strands' default token-streaming printout to the server console;
        # Chainlit (or our CLI) handles displaying the answer.
        callback_handler=None,
    )


def answer_text(result) -> str:
    """
    Pull the plain-text answer out of a Strands AgentResult.

    A result's .message is a dict like {"role": "assistant", "content": [...]} —
    we concatenate its text blocks. Kept here so both the CLI and Chainlit use
    the exact same extraction logic.
    """
    try:
        blocks = result.message.get("content", [])
        parts = [b["text"] for b in blocks if isinstance(b, dict) and "text" in b]
        return "\n".join(parts).strip() or str(result)
    except Exception:
        return str(result)
