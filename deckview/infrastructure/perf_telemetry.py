"""Small, failure-safe JSONL sink for DeckView render timings."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any, Mapping


_SAFE_FIELDS = {
    "queue_wait_ms",
    "cache_lookup_ms",
    "cache_store_ms",
    "cache_store_result",
    "cache_materialize_ms",
    "deck_resolve_ms",
    "card_sources_ms",
    "art_prepare_ms",
    "card_index_ms",
    "dust_cost_ms",
    "image_compose_ms",
    "generator_total_ms",
    "archetype_ms",
    "jpeg_ms",
    "db_ms",
    "delivery_ms",
    "telegram_photo_ms",
    "handler_total_ms",
    "card_count",
    "unique_card_count",
    "generator_result",
    "failed_stage",
    "error_type",
    "cache_status",
}


def performance_logging_enabled() -> bool:
    return os.getenv("DECKVIEW_PERF_LOG", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _deck_key(deck_code: str | None) -> str | None:
    if not deck_code:
        return None
    return hashlib.sha256(deck_code.encode("utf-8", errors="ignore")).hexdigest()[:16]


def emit_render_timing(
    *,
    source: str,
    result: str,
    timings: Mapping[str, Any],
    deck_code: str | None = None,
    trace_id: str | None = None,
) -> bool:
    """Append one sanitized event. Telemetry failures never affect a request."""
    if not performance_logging_enabled():
        return False

    try:
        event: dict[str, Any] = {
            "event": "deck_render_completed",
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source": str(source),
            "result": str(result),
        }
        if trace_id:
            event["trace_id"] = str(trace_id)[:64]
        deck_key = _deck_key(deck_code)
        if deck_key:
            event["deck_key"] = deck_key
        for key in _SAFE_FIELDS:
            if key in timings:
                event[key] = timings[key]

        line = (json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        path = os.getenv("DECKVIEW_PERF_LOG_PATH", "cache/deckview_performance.jsonl").strip()
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o640)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
        print(f"[Deckview Perf] {line.decode('utf-8').rstrip()}")
        return True
    except Exception as exc:
        print(f"[Deckview Perf] telemetry disabled for event: {type(exc).__name__}")
        return False
