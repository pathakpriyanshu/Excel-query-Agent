"""Custom HTTP endpoint for speech-to-text, mounted on Chainlit's FastAPI app.

The browser (public/mic.js) records audio and POSTs the raw bytes here. We
transcribe server-side (where the API key lives) and return plain text, which
the frontend drops into the chat input box for the user to edit/send.

This file is imported once from app.py; importing it registers the route.
"""

import asyncio
import logging

from chainlit.server import app
from fastapi import Request
from fastapi.responses import JSONResponse

from stt import transcribe

logger = logging.getLogger("voice_api")

# Reject obviously-too-large uploads early (Groq's hard limit is 25 MB).
_MAX_AUDIO_BYTES = 25 * 1024 * 1024


@app.post("/transcribe")
async def transcribe_endpoint(request: Request):
    """Receive raw audio bytes, return {"text": "..."}."""
    audio_bytes = await request.body()

    if not audio_bytes:
        return JSONResponse({"error": "No audio received."}, status_code=400)

    if len(audio_bytes) > _MAX_AUDIO_BYTES:
        return JSONResponse(
            {"error": "Recording too long. Please keep it under ~25 MB."},
            status_code=413,
        )

    content_type = request.headers.get("content-type", "audio/webm")

    try:
        # transcribe() is a blocking network call; keep the event loop free.
        text = await asyncio.to_thread(transcribe, audio_bytes, content_type)
    except Exception as e:  # noqa: BLE001 - surface a clean message to the UI
        logger.exception("Transcription failed")
        return JSONResponse(
            {"error": f"Transcription failed: {e}"}, status_code=500
        )

    return JSONResponse({"text": text})
