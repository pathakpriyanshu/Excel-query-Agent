"""Speech-to-text transcription.

Both Groq and OpenAI expose the exact same OpenAI-compatible transcription
endpoint, so we use the `openai` SDK for both and just point it at a different
base URL + key. The provider switches with a single env var, no code change:

    STT_PROVIDER=groq    -> whisper-large-v3-turbo   (free tier, default)
    STT_PROVIDER=openai  -> gpt-4o-mini-transcribe

(We deliberately do NOT route this through LiteLLM: litellm.transcription drops
the API key on the OpenAI audio endpoint and 401s. The OpenAI SDK is reliable.)

The API key never reaches the browser. The browser sends raw audio bytes to
our own /transcribe endpoint (see voice_api.py); this module is what actually
calls the provider, server-side.
"""

import io
import os

from openai import OpenAI
from dotenv import load_dotenv

load_dotenv(override=True)

STT_PROVIDER = os.getenv("STT_PROVIDER", "groq").lower()

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

# Map of audio MIME types -> filename extension. Whisper/OpenAI infer the
# audio format from the filename, so we must hand it a sensible extension.
_EXT_BY_MIME = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/mp4": "mp4",
    "audio/m4a": "m4a",
    "audio/x-m4a": "m4a",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/flac": "flac",
}


def _client_and_model() -> tuple[OpenAI, str]:
    """Return (configured OpenAI-compatible client, model_id) for the provider."""
    if STT_PROVIDER == "openai":
        client = OpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=OPENAI_BASE_URL,
        )
        model_id = os.getenv("OPENAI_STT_MODEL", "gpt-4o-mini-transcribe")
        return client, model_id

    client = OpenAI(
        api_key=os.getenv("GROQ_API_KEY"),
        base_url=GROQ_BASE_URL,
    )
    model_id = os.getenv("GROQ_STT_MODEL", "whisper-large-v3-turbo")
    return client, model_id


def transcribe(audio_bytes: bytes, content_type: str = "audio/webm") -> str:
    """Transcribe raw audio bytes to text.

    Args:
        audio_bytes: The recorded audio file, as bytes.
        content_type: The MIME type reported by the browser (e.g. "audio/webm").

    Returns:
        The transcribed text (stripped). Empty string if nothing was heard.
    """
    if not audio_bytes:
        return ""

    client, model_id = _client_and_model()

    # Normalise the MIME type (browsers send e.g. "audio/webm;codecs=opus").
    base_mime = content_type.split(";")[0].strip().lower()
    ext = _EXT_BY_MIME.get(base_mime, "webm")
    filename = f"audio.{ext}"

    buf = io.BytesIO(audio_bytes)
    buf.name = filename  # OpenAI/Groq read this to detect the audio format.

    response = client.audio.transcriptions.create(
        model=model_id,
        file=(filename, audio_bytes, base_mime or "audio/webm"),
    )

    return (getattr(response, "text", "") or "").strip()
