from __future__ import annotations

import time
from typing import Any

from deckview.config import (
    DECKVIEW_QUEUE_ENABLED,
    DECKVIEW_API_QUEUE_NAME,
    DECKVIEW_QUEUE_JOB_TIMEOUT,
    DECKVIEW_QUEUE_NAME,
    DECKVIEW_REDIS_URL,
)

try:
    from redis import Redis
    from rq import Callback, Queue
except Exception as exc:  # pragma: no cover - exercised on hosts without RQ deps.
    Redis = None
    Callback = None
    Queue = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None

_QUEUE = None
_API_QUEUE = None
_RENDER_FAILED_HOOK = "deckview.workers.jobs.render_deck_message_failed"


def _normalized_job_status(job: Any) -> str:
    """Normalize RQ 1.x strings and RQ 2.x JobStatus enum values."""
    raw_status = job.get_status(refresh=True)
    return str(getattr(raw_status, "value", raw_status) or "unknown").lower()


def _queue():
    global _QUEUE
    if not DECKVIEW_QUEUE_ENABLED:
        return None
    if Redis is None or Queue is None:
        print(f"[Deckview queue] RQ unavailable: {_IMPORT_ERROR}")
        return None
    if _QUEUE is None:
        connection = Redis.from_url(
            DECKVIEW_REDIS_URL,
            socket_connect_timeout=1,
            socket_timeout=2,
        )
        connection.ping()
        _QUEUE = Queue(DECKVIEW_QUEUE_NAME, connection=connection)
    return _QUEUE


def _api_queue():
    global _API_QUEUE
    if not DECKVIEW_QUEUE_ENABLED:
        return None
    if Redis is None or Queue is None:
        print(f"[Deckview queue] RQ unavailable: {_IMPORT_ERROR}")
        return None
    if _API_QUEUE is None:
        connection = Redis.from_url(
            DECKVIEW_REDIS_URL,
            socket_connect_timeout=1,
            socket_timeout=2,
        )
        connection.ping()
        _API_QUEUE = Queue(DECKVIEW_API_QUEUE_NAME, connection=connection)
    return _API_QUEUE


def queue_available() -> bool:
    try:
        return _queue() is not None
    except Exception as exc:
        print(f"[Deckview queue] Redis unavailable: {exc}")
        return False


def enqueue_deck_render(payload: dict[str, Any]) -> str | None:
    try:
        q = _queue()
        if q is None:
            return None
        queued_payload = dict(payload)
        # RQ 2.x serializes enqueued_at with second precision. Keep a precise
        # enqueue timestamp in the payload so queue latency telemetry is useful.
        queued_payload["_queued_at_ns"] = time.time_ns()
        # The job reports its own errors; these hooks replace the status
        # message when RQ aborts the job (timeout or stop) instead.
        failure_hook = Callback(_RENDER_FAILED_HOOK, timeout=15)
        job = q.enqueue(
            "deckview.workers.jobs.render_deck_message_job",
            queued_payload,
            job_timeout=DECKVIEW_QUEUE_JOB_TIMEOUT,
            result_ttl=3600,
            failure_ttl=86400,
            on_failure=failure_hook,
            on_stopped=failure_hook,
        )
        return job.id
    except Exception as exc:
        print(f"[Deckview queue] Failed to enqueue deck render: {exc}")
        return None


def enqueue_api_render(payload: dict[str, Any], *, job_id: str) -> str | None:
    """Queue one deterministic API render, coalescing concurrent callers."""
    try:
        q = _api_queue()
        if q is None:
            return None
        existing = q.fetch_job(job_id)
        if existing is not None:
            status = _normalized_job_status(existing)
            if status in {"queued", "started", "deferred", "scheduled"}:
                return existing.id
            # A request reaches this function only after the render cache missed.
            # A finished RQ result can therefore point at an evicted/corrupt
            # artifact and must not be reused as if it were the image cache.
            existing.delete()
        queued_payload = dict(payload)
        queued_payload["_queued_at_ns"] = time.time_ns()
        job = q.enqueue(
            "deckview.workers.jobs.render_api_deck_job",
            queued_payload,
            job_id=job_id,
            job_timeout=DECKVIEW_QUEUE_JOB_TIMEOUT,
            result_ttl=3600,
            failure_ttl=3600,
        )
        return job.id
    except Exception as exc:
        # A simultaneous process may have won the deterministic job-id race.
        try:
            q = _api_queue()
            existing = q.fetch_job(job_id) if q is not None else None
            if existing is not None:
                return existing.id
        except Exception:
            pass
        print(f"[Deckview queue] Failed to enqueue API render: {exc}")
        return None


def api_render_job_snapshot(job_id: str) -> dict[str, Any] | None:
    """Return a small, transport-safe snapshot without exposing RQ tracebacks."""
    try:
        q = _api_queue()
        if q is None:
            return None
        job = q.fetch_job(job_id)
        if job is None:
            return None
        status = _normalized_job_status(job)
        result = job.result if status == "finished" and isinstance(job.result, dict) else None
        return {"job_id": job.id, "state": status, "result": result}
    except Exception as exc:
        print(f"[Deckview queue] Failed to read API render job: {exc}")
        return None


def enqueue_hsguru_publish(payload: dict[str, Any], *, to_telegram: bool) -> str | None:
    try:
        q = _queue()
        if q is None:
            return None
        clean_payload = dict(payload)
        clean_payload.pop("_deck", None)
        job = q.enqueue(
            "deckview.workers.jobs.publish_hsguru_payload_job",
            clean_payload,
            bool(to_telegram),
            job_timeout=DECKVIEW_QUEUE_JOB_TIMEOUT,
            result_ttl=3600,
            failure_ttl=86400,
        )
        return job.id
    except Exception as exc:
        print(f"[Deckview queue] Failed to enqueue HSGuru publish: {exc}")
        return None


def enqueue_hsguru_cycle(*, to_telegram: bool) -> str | None:
    try:
        q = _queue()
        if q is None:
            return None
        job = q.enqueue(
            "deckview.workers.jobs.publish_hsguru_cycle_job",
            bool(to_telegram),
            job_timeout=DECKVIEW_QUEUE_JOB_TIMEOUT,
            result_ttl=3600,
            failure_ttl=86400,
        )
        return job.id
    except Exception as exc:
        print(f"[Deckview queue] Failed to enqueue HSGuru cycle: {exc}")
        return None
