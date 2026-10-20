"""Prepare HQ deck jobs without doing rendering on the Telegram event loop."""

import asyncio

from deckview.workers.queue import enqueue_deck_render
from image_creator.deck_retriever import _decode_deckstring


class HighResolutionService:
    async def submit(self, payload: dict) -> str | None:
        # Reject malformed strings locally, before using queue or network capacity.
        _decode_deckstring(payload["deck_code"])
        return await asyncio.to_thread(
            enqueue_deck_render,
            {**payload, "image_resolution": "high", "source": "hq_command"},
        )
