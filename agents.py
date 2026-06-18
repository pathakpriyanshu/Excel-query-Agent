import os
import litellm
from dotenv import load_dotenv

from strands import Agent
from strands.models.litellm import LiteLLMModel

litellm.drop_params = True

from prompts import build_system_prompt
from tools import query_tracker, find_entity
from db import get_schema_text

load_dotenv(override=True)

MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "groq").lower()


def build_model() -> LiteLLMModel:
    params = {"temperature": 0.0, "num_retries": 3}

    if MODEL_PROVIDER == "openai":
        model_id = os.getenv("OPENAI_MODEL", "gpt-4o")
        litellm_id = f"openai/{model_id}"
        params["api_key"] = os.getenv("OPENAI_API_KEY")
        params["api_base"] = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        if model_id.startswith(("gpt-5", "o1", "o3", "o4")):
            params.pop("temperature", None)
    else:
        model_id = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
        litellm_id = f"groq/{model_id}"
        params["api_key"] = os.getenv("GROQ_API_KEY")

    return LiteLLMModel(model_id=litellm_id, params=params)


def create_agent() -> Agent:
    schema_text = get_schema_text()
    system_prompt = build_system_prompt(schema_text)

    return Agent(
        model=build_model(),
        system_prompt=system_prompt,
        tools=[query_tracker, find_entity],
        callback_handler=None,
    )


def answer_text(result) -> str:
    try:
        blocks = result.message.get("content", [])
        parts = [b["text"] for b in blocks if isinstance(b, dict) and "text" in b]
        return "\n".join(parts).strip() or str(result)
    except Exception:
        return str(result)
