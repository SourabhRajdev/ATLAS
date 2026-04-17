"""Gemini vision wrapper — semantic 'what is on screen' descriptions.

Hard caps: max 1 call/minute, response cached by (app, window_title, image_hash).
Never on the hot path — always called explicitly by reasoner or on-demand.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from atlas.perception.screen import ScreenPipeline

logger = logging.getLogger("atlas.perception.vision")

MIN_INTERVAL_S = 60.0  # max 1 call/minute


class VisionEngine:
    def __init__(self, gemini_client, model: str = "gemini-2.5-flash") -> None:
        self.client = gemini_client
        self.model = model
        self._last_call_at: float = 0.0
        self._cache: dict[str, str] = {}

    async def describe(
        self,
        image_path: Path,
        context: str = "",
        cache_key: str | None = None,
    ) -> str | None:
        """Describe a screenshot. Returns None if rate-limited or failed."""
        if not image_path or not image_path.exists():
            return None

        # Cache hit
        key = cache_key or ScreenPipeline.hash_path(image_path)
        if key and key in self._cache:
            return self._cache[key]

        # Rate gate
        now = time.time()
        if now - self._last_call_at < MIN_INTERVAL_S:
            logger.debug("vision rate-limited (%.0fs since last)", now - self._last_call_at)
            return None
        self._last_call_at = now

        prompt = (
            "Describe what's on screen in 1-2 sentences. "
            "Focus on what the user appears to be doing. "
            "Be concrete: app names, document names, code/text visible, dialog state. "
            "Do not include any text that looks like a password, API key, or token.\n\n"
        )
        if context:
            prompt += f"Context: {context}\n"

        try:
            from google.genai import types  # type: ignore
            image_bytes = image_path.read_bytes()
            response = await asyncio.to_thread(
                self.client.models.generate_content,
                model=self.model,
                contents=[
                    types.Content(role="user", parts=[
                        types.Part(text=prompt),
                        types.Part(inline_data=types.Blob(
                            mime_type="image/png", data=image_bytes,
                        )),
                    ]),
                ],
                config=types.GenerateContentConfig(
                    temperature=0.2,
                    max_output_tokens=200,
                ),
            )
            if response and response.candidates:
                text = response.candidates[0].content.parts[0].text or ""
                text = text.strip()
                if key:
                    self._cache[key] = text
                return text
        except Exception as e:
            logger.warning("vision call failed: %s", e)
        return None
