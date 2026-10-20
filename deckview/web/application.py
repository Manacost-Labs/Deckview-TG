# Gunicorn runs this app with sync workers. Gevent monkey-patching sockets here
# can hang the isolated asyncio/thread generation path used by create_picture().

from flask import Flask, render_template, request, url_for, jsonify
import asyncio
import hmac
import json
import os
import re
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from deckview.config import (
    API_TOKEN,
    CHANNEL_ID,
    DASHBOARD_SECRET,
    DECKVIEW_SOURCE_CODE_URL,
    PUBLIC_API_AUTH_REQUIRED,
    WEB_HOST,
    WEB_PORT,
)
from deckview.web.dashboard import bp as dashboard_bp, admin_bp
from deckview.integrations.hsguru_archetype import recognize_archetype
from deckview.integrations.hsguru_fetch import (
    is_publishable_deck,
    load_archetypes,
    mark_deck_published,
    parse_decks,
    payloads_from_decks,
    payloads_from_html,
    translate_deck_name,
)
from image_creator import create_picture
from image_creator.jpeg_output import write_rendered_jpeg
from image_creator.output_resolution import normalize_image_resolution
from deckview.infrastructure.perf_telemetry import emit_render_timing
from deckview.bot.publishing import publish_deck
from deckview.infrastructure.render_cache import (
    acquire_render_lock,
    build_render_cache_key,
    lookup_render_cache,
    release_render_lock,
    store_render_cache,
)
from deckview.workers.queue import (
    api_render_job_snapshot,
    enqueue_api_render,
)
from deckview.repositories.web import (
    init_db,
    find_cached,
    add_generated,
    add_deck_cards,
    get_history as db_get_history,
    add_to_library as db_add_to_library,
    get_all_archetypes,
)
from framework.bgs_manager import BGSManager
from image_creator.bgs_placer import place_bgs_board

if os.getenv("DECKVIEW_WEB_PRELOAD_CARDS", "0").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}:
    # Gunicorn runs with --preload in production, so the master pays this cost
    # once and the two web workers inherit the read-mostly indexes via COW.
    from deckview.workers.worker import _preload_shared_card_catalog

    _preload_shared_card_catalog()

PROJECT_ROOT = Path(__file__).resolve().parents[2]

app = Flask(
    __name__,
    template_folder=str(PROJECT_ROOT / "templates"),
    static_folder=str(PROJECT_ROOT / "static"),
)
bgs_manager = BGSManager()

# Папки для хранения
GENERATED_DIR = str(PROJECT_ROOT / "static" / "generated")
LIBRARY_DIR = str(PROJECT_ROOT / "library")

for d in [GENERATED_DIR, LIBRARY_DIR]:
    os.makedirs(d, exist_ok=True)

# Инициализация БД при старте
init_db()

# Дашборд бота: /dashboard
app.register_blueprint(dashboard_bp)
# Панель администратора: /admin (без авторизации)
app.register_blueprint(admin_bp)


@app.after_request
def add_deckview_api_headers(response):
    if request.path.startswith("/deckview-api/"):
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type, X-API-Key"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    if request.path.startswith("/static/generated/render-cache/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response


def get_history():
    """Последние сгенерированные колоды из БД (с полем url для шаблона)."""
    items = db_get_history(limit=12)
    return [
        {
            "name": it["name"],
            "url": url_for("static", filename=f"generated/{it['name']}"),
            "time": it["time"],
        }
        for it in items
    ]

@app.route("/")
def index():
    return _render_index()

# Таймаут генерации (секунды): защита от зависания воркера
GENERATE_TIMEOUT = int(os.getenv("WEB_GENERATE_TIMEOUT", "120"))


def _run_create_picture(
    deck_code,
    deck_name,
    timings=None,
    *,
    image_style="classic",
    image_resolution="standard",
):
    """
    Запускает create_picture в отдельном OS-потоке с собственным asyncio event loop.
    Это отделяет долгую генерацию от Flask-воркера и даёт жёсткий таймаут.
    """
    result_holder = []
    exc_holder = []
    worker_timings = {}

    def _worker():
        # SelectorEventLoop явно создаётся для этого фонового потока.
        loop = asyncio.SelectorEventLoop()
        asyncio.set_event_loop(loop)
        try:
            res = loop.run_until_complete(
                create_picture(
                    deck_code,
                    deck_name=deck_name,
                    timings=worker_timings,
                    image_style=image_style,
                    **({"image_resolution": image_resolution} if image_resolution != "standard" else {}),
                )
            )
            result_holder.append(res)
        except Exception as e:
            exc_holder.append(e)
        finally:
            loop.close()

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout=GENERATE_TIMEOUT)

    if t.is_alive():
        raise asyncio.TimeoutError()
    if exc_holder:
        raise exc_holder[0]
    if not result_holder:
        raise RuntimeError("Генерация не вернула результат")
    if timings is not None:
        timings.update(worker_timings)
    return result_holder[0]


def _deckview_api_payload():
    return request.get_json(silent=True) or {}


def _request_api_key(data=None):
    data = data or {}
    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header.split(None, 1)[1].strip()

    provided = (
        request.headers.get("X-API-Key")
        or request.args.get("api_key")
        or data.get("api_key")
        or ""
    )
    return str(provided).strip()


def _require_deckview_api_auth(data=None, public_endpoint=False):
    if public_endpoint and not PUBLIC_API_AUTH_REQUIRED:
        return None
    if not API_TOKEN:
        return jsonify({"success": False, "error": "API_TOKEN is not configured"}), 503
    provided = _request_api_key(data)
    if not provided or not hmac.compare_digest(provided, API_TOKEN):
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    return None


def _bool_from_payload(data, key, default=False):
    value = data.get(key, default)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


_PUBLIC_RENDER_STYLES = {"classic", "parchment"}


def _public_render_style(data, *, force_parchment=False):
    """Validate and return the canonical public render style."""
    if force_parchment:
        return "parchment"
    raw = (
        data.get("image_style")
        or data.get("style")
        or request.args.get("image_style")
        or request.args.get("style")
        or "classic"
    )
    style = str(raw).strip().lower()
    aliases = {
        "pergament": "parchment",
        "пергамент": "parchment",
    }
    style = aliases.get(style, style)
    if style not in _PUBLIC_RENDER_STYLES:
        raise ValueError("image_style must be classic or parchment")
    return style


def _wants_async_render(data) -> bool:
    prefer = request.headers.get("Prefer", "").lower()
    return "respond-async" in prefer or _bool_from_payload(
        {"async": data.get("async", request.args.get("async"))},
        "async",
        False,
    )


def _legacy_render_cache_read_enabled() -> bool:
    return os.getenv(
        "DECKVIEW_LEGACY_RENDER_CACHE_READ",
        "0",
    ).strip().lower() in {"1", "true", "yes", "on"}


def _request_latency_ms(started_ns):
    return round((time.perf_counter_ns() - started_ns) / 1_000_000, 3)


def _api_json_response(payload, *, started_ns, status=200, cacheable=False):
    body = dict(payload)
    body["latency_ms"] = _request_latency_ms(started_ns)
    response = jsonify(body)
    response.status_code = status
    response.headers["Server-Timing"] = f'app;dur={body["latency_ms"]}'
    response.headers["Cache-Control"] = (
        "public, max-age=60, stale-while-revalidate=300"
        if cacheable
        else "no-store"
    )
    return response


def _generated_image_urls(filename):
    return {
        "image_url": url_for("static", filename=f"generated/{filename}", _external=True),
        "image_path": url_for("static", filename=f"generated/{filename}"),
    }


def _generated_preview_urls(filename):
    value = str(filename or "").strip()
    if not value:
        return {}
    return {
        "preview_filename": value,
        "preview_image_url": url_for(
            "static",
            filename=f"generated/{value}",
            _external=True,
        ),
        "preview_image_path": url_for("static", filename=f"generated/{value}"),
    }


def _deck_payload_from_api(data):
    streamer = data.get("streamer") or "Неизвестный"
    return {
        "deck_code": (data.get("deck_code") or "").strip(),
        "deck_name": data.get("deck_name") or "Deck",
        "streamer": streamer,
        "player": data.get("player") or streamer,
        "wins": data.get("wins", 0),
        "losses": data.get("losses", 0),
        "format": data.get("format"),
        "deck_class": data.get("deck_class"),
        "deck_mode": data.get("deck_mode"),
        "peak": data.get("peak", ""),
        "latest": data.get("latest", ""),
        "worst": data.get("worst", ""),
        "legend_rank": data.get("legend_rank", ""),
        "source_url": data.get("source_url", ""),
    }


def _safe_int(value, default=0):
    try:
        return int(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def _normalize_hsguru_raw_decks(raw_decks):
    archetypes = load_archetypes()
    normalized_decks = []
    for deck in raw_decks:
        if not isinstance(deck, dict):
            continue
        wins = _safe_int(deck.get("wins"))
        losses = _safe_int(deck.get("losses"))
        raw_name = deck.get("deck_name") or deck.get("name") or "Deck"
        translated_name = translate_deck_name(str(raw_name).strip(), archetypes) or raw_name
        normalized_decks.append({
            "deck_code": (deck.get("deck_code") or deck.get("deckcode") or deck.get("code") or "").strip(),
            "deck_name": translated_name,
            "deck_name_source": raw_name,
            "streamer": deck.get("streamer") or deck.get("player") or "",
            "format": deck.get("format") or "",
            "wins": wins,
            "losses": losses,
            "total_games": _safe_int(deck.get("total_games"), wins + losses),
            "peak": deck.get("peak", ""),
            "latest": deck.get("latest", ""),
            "worst": deck.get("worst", ""),
            "legend_rank": deck.get("legend_rank", ""),
            "source_url": deck.get("source_url", ""),
        })
    return normalized_decks


@app.route("/deckview-api/v1/health")
def deckview_api_health():
    return jsonify({
        "success": True,
        "service": "deckview",
        "status": "ok",
        "public_api": True,
        "license": "AGPL-3.0-or-later",
        "source_code": DECKVIEW_SOURCE_CODE_URL,
        "auth_required": bool(PUBLIC_API_AUTH_REQUIRED),
        "publish_auth_required": True,
        "endpoints": {
            "render": "/deckview-api/v1/render",
            "render_parchment": "/deckview-api/v1/render/parchment",
            "translate": "/deckview-api/v1/translate",
            "archetype": "/deckview-api/v1/archetype",
            "archetypes": "/deckview-api/v1/archetypes",
            "hsguru_ingest": "/deckview-api/v1/hsguru/ingest",
            "hsguru_stage": "/deckview-api/v1/hsguru/stage",
            "publish": "/deckview-api/v1/publish",
        },
    })


@app.route("/deckview-api/v1/translate", methods=["GET", "POST"])
@app.route("/deckview-api/v1/decks/translate", methods=["GET", "POST"])
def deckview_api_translate():
    data = _deckview_api_payload()
    auth_error = _require_deckview_api_auth(data, public_endpoint=True)
    if auth_error:
        return auth_error

    names = data.get("names")
    if names is None:
        names = request.args.getlist("name") or request.args.getlist("deck_name")
    if isinstance(names, str):
        names = [names]

    single_name = data.get("name") or data.get("deck_name") or request.args.get("name") or request.args.get("deck_name")
    if not names and single_name:
        names = [single_name]

    if not names:
        return jsonify({"success": False, "error": "name or names required"}), 400

    archetypes = load_archetypes()
    items = []
    for name in names:
        source = str(name or "").strip()
        if not source:
            continue
        items.append({
            "source": source,
            "translated": translate_deck_name(source, archetypes),
        })

    if not items:
        return jsonify({"success": False, "error": "name or names required"}), 400

    response = {
        "success": True,
        "count": len(items),
        "items": items,
    }
    if len(items) == 1:
        response["translated"] = items[0]["translated"]
    return jsonify(response)


@app.route("/deckview-api/v1/archetypes")
def deckview_api_archetypes():
    auth_error = _require_deckview_api_auth({}, public_endpoint=True)
    if auth_error:
        return auth_error

    search = (request.args.get("search") or "").strip() or None
    try:
        limit = int(request.args.get("limit", "200"))
    except ValueError:
        limit = 200
    limit = min(max(limit, 1), 500)
    rows = get_all_archetypes(search=search)[:limit]
    return jsonify({
        "success": True,
        "count": len(rows),
        "items": rows,
    })


@app.route("/deckview-api/v1/archetype", methods=["GET", "POST"])
@app.route("/deckview-api/v1/decks/archetype", methods=["GET", "POST"])
def deckview_api_archetype():
    data = _deckview_api_payload()
    auth_error = _require_deckview_api_auth(data, public_endpoint=True)
    if auth_error:
        return auth_error

    deck_code = (
        data.get("deck_code")
        or request.args.get("deck_code")
        or request.args.get("deck")
        or ""
    ).strip()
    if not deck_code:
        return jsonify({"success": False, "error": "deck_code required"}), 400

    return jsonify(recognize_archetype(deck_code))


@app.route("/deckview-api/v1/render", methods=["GET", "POST"])
@app.route("/deckview-api/v1/decks/render", methods=["GET", "POST"])
@app.route("/deckview-api/v1/render/parchment", methods=["GET", "POST"])
@app.route("/deckview-api/v1/decks/render/parchment", methods=["GET", "POST"])
def deckview_api_render():
    handler_started = time.perf_counter_ns()
    data = _deckview_api_payload()
    auth_error = _require_deckview_api_auth(data, public_endpoint=True)
    if auth_error:
        return auth_error

    try:
        image_style = _public_render_style(
            data,
            force_parchment=request.path.endswith("/parchment"),
        )
    except ValueError as exc:
        return _api_json_response(
            {
                "success": False,
                "error": str(exc),
                "error_code": "INVALID_IMAGE_STYLE",
            },
            started_ns=handler_started,
            status=400,
        )

    try:
        image_resolution = normalize_image_resolution(
            data.get("image_resolution", request.args.get("image_resolution"))
        )
    except ValueError as exc:
        return _api_json_response(
            {"success": False, "error": str(exc), "error_code": "INVALID_IMAGE_RESOLUTION"},
            started_ns=handler_started,
            status=400,
        )
    resolution_kwargs = (
        {"image_resolution": image_resolution} if image_resolution != "standard" else {}
    )

    deck_code = (
        data.get("deck_code")
        or request.args.get("deck_code")
        or request.args.get("deck")
        or ""
    ).strip()
    deck_name = (
        data.get("deck_name")
        or request.args.get("deck_name")
        or request.args.get("name")
        or ""
    ).strip() or None

    if not deck_code:
        return _api_json_response(
            {
                "success": False,
                "error": "deck_code required",
                "error_code": "DECK_CODE_REQUIRED",
            },
            started_ns=handler_started,
            status=400,
        )

    timings = {}
    trace_id = os.urandom(8).hex()

    def lookup_cached_result():
        cached_result = lookup_render_cache(
            deck_code,
            deck_name,
            scope="api",
            image_style=image_style,
            **resolution_kwargs,
        )
        source = "render_cache"
        if (
            cached_result is None
            and image_resolution == "standard"
            and image_style == "classic"
            and _legacy_render_cache_read_enabled()
        ):
            # The old generated-decks cache predates render styles and is safe
            # only for the classic renderer.
            cached_result = find_cached(deck_code, deck_name)
            source = "legacy"
        if not cached_result:
            return None, source
        filepath = cached_result.get("artifact_path") or os.path.join(
            GENERATED_DIR,
            cached_result["filename"],
        )
        if not os.path.isfile(filepath):
            return None, source
        return cached_result, source

    def cached_response(cached_result, source):
        layer = cached_result.get("cache_layer")
        cache_status = f"{source}_{layer}_hit" if layer else f"{source}_hit"
        timings["cache_status"] = cache_status
        if cached_result.get("preview_prepare_ms") is not None:
            timings["preview_ms"] = cached_result["preview_prepare_ms"]
            timings["preview_bytes"] = cached_result.get("preview_size_bytes")
        timings["handler_total_ms"] = _request_latency_ms(handler_started)
        emit_render_timing(
            source="web_api",
            result="ok",
            timings=timings,
            deck_code=deck_code,
            trace_id=trace_id,
        )
        timings["_telemetry_emitted"] = True
        return _api_json_response(
            {
                "success": True,
                "cached": True,
                "cache_layer": layer or source,
                "image_style": image_style,
                "image_resolution": image_resolution,
                "deck_code": cached_result["deck_code"],
                "deck_name": cached_result.get("deck_name"),
                "cost": cached_result["cost"],
                "deck_class": cached_result.get("deck_class"),
                "deck_mode": cached_result.get("deck_mode"),
                "filename": cached_result["filename"],
                **_generated_image_urls(cached_result["filename"]),
                **_generated_preview_urls(cached_result.get("preview_filename")),
            },
            started_ns=handler_started,
            cacheable=True,
        )

    started = time.perf_counter_ns()
    cached, cache_source = lookup_cached_result()
    timings["cache_lookup_ms"] = round((time.perf_counter_ns() - started) / 1_000_000, 3)
    if cached:
        return cached_response(cached, cache_source)

    if _wants_async_render(data):
        cache_key = build_render_cache_key(deck_code, deck_name, image_style, **resolution_kwargs)
        job_id = enqueue_api_render(
            {
                "deck_code": deck_code,
                "deck_name": deck_name,
                "image_style": image_style,
                "image_resolution": image_resolution,
                "trace_id": trace_id,
            },
            job_id=f"api-render-{cache_key}",
        )
        if job_id:
            response = _api_json_response(
                {
                    "success": True,
                    "ready": False,
                    "state": "queued",
                    "job_id": job_id,
                    "image_style": image_style,
                    "image_resolution": image_resolution,
                    "status_url": url_for(
                        "deckview_api_render_job",
                        job_id=job_id,
                        _external=True,
                    ),
                    "retry_after_ms": 150,
                },
                started_ns=handler_started,
                status=202,
            )
            response.headers["Retry-After"] = "1"
            return response

    timings["cache_status"] = "miss"
    result_status = "error"
    render_lock = None
    try:
        render_lock = acquire_render_lock(deck_code, deck_name, image_style, **resolution_kwargs)
        if render_lock is not None:
            # Another worker may have completed the same image while this
            # request was waiting for the Redis lock.
            started = time.perf_counter_ns()
            cached, cache_source = lookup_cached_result()
            timings["cache_lookup_ms"] += round(
                (time.perf_counter_ns() - started) / 1_000_000,
                3,
            )
            if cached:
                result_status = "ok"
                return cached_response(cached, cache_source)

        image, cost, deck_class_name, deck_mode_name, card_dbf_ids = _run_create_picture(
            deck_code,
            deck_name,
            timings=timings,
            image_style=image_style,
            **resolution_kwargs,
        )

        if image is None:
            result_status = "empty"
            return _api_json_response(
                {
                    "success": False,
                    "error": "Failed to generate image. Check deck_code.",
                    "error_code": "RENDER_FAILED",
                },
                started_ns=handler_started,
                status=422,
            )

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"deck_{image_style}_{timestamp}.jpg"
        filepath = os.path.join(GENERATED_DIR, filename)
        started = time.perf_counter_ns()
        reused_native_jpeg = write_rendered_jpeg(
            image,
            filepath,
            quality=90,
            optimize=False,
        )
        timings["jpeg_ms"] = round((time.perf_counter_ns() - started) / 1_000_000, 3)
        timings["jpeg_reused_native"] = reused_native_jpeg
        started = time.perf_counter_ns()
        cache_entry = store_render_cache(
            deck_code=deck_code,
            deck_name=deck_name,
            source_path=filepath,
            cost=cost,
            deck_class=deck_class_name,
            deck_mode=deck_mode_name,
            card_dbf_ids=card_dbf_ids,
            image_style=image_style,
            **resolution_kwargs,
            generate_preview=True,
        )
        timings["cache_store_ms"] = round((time.perf_counter_ns() - started) / 1_000_000, 3)
        timings["cache_store_result"] = "stored" if cache_entry else "disabled_or_miss"
        if cache_entry and cache_entry.get("preview_prepare_ms") is not None:
            timings["preview_ms"] = cache_entry["preview_prepare_ms"]
            timings["preview_bytes"] = cache_entry.get("preview_size_bytes")
        started = time.perf_counter_ns()
        gen_id = add_generated(
            deck_code,
            deck_name,
            cost,
            filename,
            source=f"api:{image_style}",
            deck_class=deck_class_name,
            deck_mode=deck_mode_name,
        )
        add_deck_cards(gen_id, card_dbf_ids)
        timings["db_ms"] = round((time.perf_counter_ns() - started) / 1_000_000, 3)

        response_entry = cache_entry or {"filename": filename}
        response_filename = response_entry["filename"]
        result_status = "ok"
        return _api_json_response(
            {
                "success": True,
                "cached": False,
                "cache_layer": None,
                "image_style": image_style,
                "image_resolution": image_resolution,
                "deck_code": deck_code,
                "deck_name": deck_name,
                "cost": cost,
                "deck_class": deck_class_name,
                "deck_mode": deck_mode_name,
                "filename": response_filename,
                **_generated_image_urls(response_filename),
                **_generated_preview_urls(response_entry.get("preview_filename")),
            },
            started_ns=handler_started,
        )

    except asyncio.TimeoutError:
        result_status = "timeout"
        return _api_json_response(
            {
                "success": False,
                "error": "Generation timed out. Try again later.",
                "error_code": "RENDER_TIMEOUT",
            },
            started_ns=handler_started,
            status=504,
        )
    except Exception as e:
        timings.setdefault("error_type", type(e).__name__)
        return _api_json_response(
            {
                "success": False,
                "error": str(e)[:300],
                "error_code": "INTERNAL_ERROR",
            },
            started_ns=handler_started,
            status=500,
        )
    finally:
        release_render_lock(render_lock)
        timings["handler_total_ms"] = round(
            (time.perf_counter_ns() - handler_started) / 1_000_000,
            3,
        )
        if not timings.get("_telemetry_emitted"):
            emit_render_timing(
                source="web_api",
                result=result_status,
                timings=timings,
                deck_code=deck_code,
                trace_id=trace_id,
            )


@app.route("/deckview-api/v1/render/jobs/<job_id>", methods=["GET"])
def deckview_api_render_job(job_id):
    handler_started = time.perf_counter_ns()
    auth_error = _require_deckview_api_auth({}, public_endpoint=True)
    if auth_error:
        return auth_error
    if not re.fullmatch(r"api-render-[a-f0-9]{64}", str(job_id or "")):
        return _api_json_response(
            {"success": False, "error_code": "INVALID_JOB_ID"},
            started_ns=handler_started,
            status=400,
        )
    snapshot = api_render_job_snapshot(job_id)
    if snapshot is None:
        return _api_json_response(
            {"success": False, "error_code": "JOB_NOT_FOUND"},
            started_ns=handler_started,
            status=404,
        )
    state = snapshot["state"]
    result = snapshot.get("result") or {}
    if state == "finished":
        if not result.get("success"):
            return _api_json_response(
                {
                    "success": False,
                    "ready": False,
                    "state": "failed",
                    "job_id": job_id,
                    "error_code": result.get("error_code") or "RENDER_FAILED",
                },
                started_ns=handler_started,
                status=422,
            )
        filename = str(result.get("filename") or "")
        if not filename:
            return _api_json_response(
                {"success": False, "error_code": "RENDER_RESULT_INVALID"},
                started_ns=handler_started,
                status=500,
            )
        return _api_json_response(
            {
                "success": True,
                "ready": True,
                "state": "done",
                "job_id": job_id,
                "cached": bool(result.get("cached")),
                "image_style": result.get("image_style") or "parchment",
                "image_resolution": result.get("image_resolution") or "standard",
                "deck_code": result.get("deck_code"),
                "deck_name": result.get("deck_name"),
                "cost": result.get("cost"),
                "deck_class": result.get("deck_class"),
                "deck_mode": result.get("deck_mode"),
                "filename": filename,
                **_generated_image_urls(filename),
                **_generated_preview_urls(result.get("preview_filename")),
            },
            started_ns=handler_started,
            cacheable=True,
        )
    if state in {"failed", "stopped", "canceled"}:
        return _api_json_response(
            {
                "success": False,
                "ready": False,
                "state": "failed",
                "job_id": job_id,
                "error_code": "RENDER_JOB_FAILED",
            },
            started_ns=handler_started,
            status=500,
        )
    response = _api_json_response(
        {
            "success": True,
            "ready": False,
            "state": state,
            "job_id": job_id,
            "retry_after_ms": 150,
        },
        started_ns=handler_started,
        status=202,
    )
    response.headers["Retry-After"] = "1"
    return response


@app.route("/deckview-api/v1/publish", methods=["POST"])
@app.route("/deckview-api/v1/decks/publish", methods=["POST"])
def deckview_api_publish():
    data = _deckview_api_payload()
    auth_error = _require_deckview_api_auth(data)
    if auth_error:
        return auth_error

    payload = _deck_payload_from_api(data)
    if not payload["deck_code"]:
        return jsonify({
            "success": False,
            "telegram_sent": False,
            "wordpress_posted": False,
            "error": "deck_code required",
        }), 400

    to_telegram = _bool_from_payload(data, "to_telegram", bool(CHANNEL_ID))
    to_wordpress = _bool_from_payload(data, "to_wordpress", True)

    if _bool_from_payload(data, "dry_run", False):
        return jsonify({
            "success": True,
            "dry_run": True,
            "to_telegram": to_telegram,
            "to_wordpress": to_wordpress,
            "payload": payload,
        })

    try:
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                publish_deck(payload, to_telegram=to_telegram, to_wordpress=to_wordpress)
            )
        finally:
            loop.close()
    except Exception as e:
        return jsonify({
            "success": False,
            "telegram_sent": False,
            "wordpress_posted": False,
            "error": str(e),
        }), 500

    telegram_sent = bool(result.get("telegram_sent", result.get("telegram", False)))
    wordpress_posted = bool(result.get("wordpress_posted", result.get("wordpress", False)))
    channels_ok = (not to_telegram or telegram_sent) and (not to_wordpress or wordpress_posted)
    success = bool(result.get("image_generated")) and result.get("error") is None and channels_ok
    return jsonify({
        "success": success,
        "image_generated": bool(result.get("image_generated")),
        "telegram_sent": telegram_sent,
        "wordpress_posted": wordpress_posted,
        "error": result.get("error"),
    })


@app.route("/deckview-api/v1/hsguru/ingest", methods=["POST", "OPTIONS"])
def deckview_api_hsguru_ingest():
    """Receive authorized HSGuru streamer-decks HTML/JSON, parse it and publish filtered decks."""
    if request.method == "OPTIONS":
        return jsonify({"success": True})

    data = _deckview_api_payload()
    auth_error = _require_deckview_api_auth(data)
    if auth_error:
        return auth_error

    html = data.get("html") or data.get("document") or ""
    raw_decks = data.get("decks")
    include_seen = _bool_from_payload(data, "include_seen", False)
    dry_run = _bool_from_payload(data, "dry_run", False)
    to_telegram = _bool_from_payload(data, "to_telegram", bool(CHANNEL_ID))
    to_wordpress = _bool_from_payload(data, "to_wordpress", True)

    try:
        limit = int(data.get("limit", 0) or 0)
    except (TypeError, ValueError):
        limit = 0

    try:
        if isinstance(raw_decks, list):
            normalized_decks = _normalize_hsguru_raw_decks(raw_decks)
            parsed_count = len(normalized_decks)
            publishable_count = sum(1 for deck in normalized_decks if is_publishable_deck(deck))
            payloads = payloads_from_decks(
                normalized_decks,
                limit=limit,
                include_seen=include_seen,
            )
        elif isinstance(html, str) and html.strip():
            archetypes = load_archetypes()
            parsed_decks = parse_decks(html, archetypes)
            parsed_count = len(parsed_decks)
            publishable_count = sum(1 for deck in parsed_decks if is_publishable_deck(deck))
            payloads = payloads_from_decks(
                parsed_decks,
                limit=limit,
                include_seen=include_seen,
            )
        else:
            return jsonify({
                "success": False,
                "error": "html or decks required",
            }), 400
    except Exception as e:
        return jsonify({
            "success": False,
            "error": f"HSGuru parse failed: {str(e)[:300]}",
        }), 422

    if dry_run:
        return jsonify({
            "success": True,
            "dry_run": True,
            "parsed_count": parsed_count,
            "publishable_count": publishable_count,
            "queued_count": len(payloads),
            "items": payloads,
        })

    results = []
    success_count = 0
    for payload in payloads:
        try:
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(
                    publish_deck(payload, to_telegram=to_telegram, to_wordpress=to_wordpress)
                )
            finally:
                loop.close()
            telegram_sent = bool(result.get("telegram_sent", result.get("telegram", False)))
            wordpress_posted = bool(result.get("wordpress_posted", result.get("wordpress", False)))
            channels_ok = (not to_telegram or telegram_sent) and (not to_wordpress or wordpress_posted)
            ok = bool(result.get("image_generated")) and result.get("error") is None and channels_ok
            if ok:
                mark_deck_published(payload)
                success_count += 1
            results.append({
                "deck_code": payload.get("deck_code"),
                "deck_name": payload.get("deck_name"),
                "success": ok,
                "telegram_sent": telegram_sent,
                "wordpress_posted": wordpress_posted,
                "error": result.get("error"),
            })
        except Exception as e:
            results.append({
                "deck_code": payload.get("deck_code"),
                "deck_name": payload.get("deck_name"),
                "success": False,
                "error": str(e)[:300],
            })

    return jsonify({
        "success": success_count == len(payloads),
        "parsed_count": parsed_count,
        "publishable_count": publishable_count,
        "queued_count": len(payloads),
        "published_count": success_count,
        "items": results,
    })


@app.route("/deckview-api/v1/hsguru/stage", methods=["POST", "OPTIONS"])
def deckview_api_hsguru_stage():
    """Receive HSGuru data without publishing; used by trusted browser bridge when no API token is available."""
    if request.method == "OPTIONS":
        return jsonify({"success": True})

    data = _deckview_api_payload()
    html = data.get("html") or data.get("document") or ""
    raw_decks = data.get("decks")

    try:
        limit = int(data.get("limit", 0) or 0)
    except (TypeError, ValueError):
        limit = 0

    try:
        if isinstance(raw_decks, list):
            normalized_decks = _normalize_hsguru_raw_decks(raw_decks)
        elif isinstance(html, str) and html.strip():
            normalized_decks = parse_decks(html, load_archetypes())
        else:
            return jsonify({"success": False, "error": "html or decks required"}), 400
    except Exception as e:
        return jsonify({
            "success": False,
            "error": f"HSGuru parse failed: {str(e)[:300]}",
        }), 422

    publishable_count = sum(1 for deck in normalized_decks if is_publishable_deck(deck))
    payloads = payloads_from_decks(normalized_decks, limit=limit, include_seen=False)
    stage = {
        "received_at": datetime.now().isoformat(),
        "remote_addr": request.headers.get("X-Forwarded-For", request.remote_addr),
        "source": "hsguru_browser_bridge",
        "parsed_count": len(normalized_decks),
        "publishable_count": publishable_count,
        "queued_count": len(payloads),
        "decks": normalized_decks,
        "payloads": payloads,
    }
    os.makedirs("cache", exist_ok=True)
    with open("cache/hsguru_stage_latest.json", "w", encoding="utf-8") as f:
        json.dump(stage, f, ensure_ascii=False, indent=2)

    return jsonify({
        "success": True,
        "staged": True,
        "parsed_count": len(normalized_decks),
        "publishable_count": publishable_count,
        "queued_count": len(payloads),
        "message": "Saved to cache/hsguru_stage_latest.json; nothing was published.",
        "items": payloads[:20],
    })


def _render_index(deck_code=None, image_url=None, cost=None, error_message=None):
    """Единый рендер главной страницы (успех или ошибка — всегда 200, без вылета)."""
    return render_template(
        "index.html",
        deck_code=deck_code or "",
        image_url=image_url,
        cost=cost,
        error_message=error_message,
        history=get_history(),
    )


@app.route("/generate", methods=["POST"])
def generate():
    deck_code = request.form.get("deck_code", "").strip()
    deck_name = request.form.get("deck_name", "").strip() or None

    if not deck_code:
        return _render_index(error_message="Введите код колоды (начинается с AA)."), 400

    # Кэш: если недавно уже генерировали эту колоду — отдаём сохранённый результат
    cached = find_cached(deck_code, deck_name)
    if cached:
        filepath = os.path.join(GENERATED_DIR, cached["filename"])
        if os.path.isfile(filepath):
            return _render_index(
                deck_code=deck_code,
                image_url=url_for("static", filename=f"generated/{cached['filename']}"),
                cost=cached["cost"],
            )

    try:
        image, cost, deck_class_name, deck_mode_name, card_dbf_ids = _run_create_picture(deck_code, deck_name)

        if image is None:
            return _render_index(deck_code=deck_code, error_message="Не удалось создать изображение. Проверьте код колоды и BATTLE_NET_TOKEN.")

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"deck_{timestamp}.jpg"
        filepath = os.path.join(GENERATED_DIR, filename)
        image.save(filepath, format="JPEG", quality=92, optimize=True)
        gen_id = add_generated(deck_code, deck_name, cost, filename, source="web", deck_class=deck_class_name, deck_mode=deck_mode_name)
        add_deck_cards(gen_id, card_dbf_ids)

        return _render_index(
            deck_code=deck_code,
            image_url=url_for("static", filename=f"generated/{filename}"),
            cost=cost,
        )

    except asyncio.TimeoutError:
        return _render_index(
            deck_code=deck_code,
            error_message="Генерация заняла слишком много времени. Попробуйте ещё раз или другой код колоды.",
        )
    except Exception as e:
        return _render_index(
            deck_code=deck_code,
            error_message=f"Ошибка: {str(e)[:300]}",
        )

@app.route("/api/v1/generate", methods=["POST"])
def api_generate():
    """JSON API: генерация колоды. Используется React-фронтендом."""
    data = request.get_json(silent=True) or {}
    deck_code = (data.get("deck_code") or "").strip()
    deck_name = (data.get("deck_name") or "").strip() or None

    if not deck_code:
        return jsonify({"success": False, "error": "Введите код колоды (начинается с AA)."}), 400

    cached = find_cached(deck_code, deck_name)
    if cached:
        filepath = os.path.join(GENERATED_DIR, cached["filename"])
        if os.path.isfile(filepath):
            return jsonify({
                "success": True,
                "cached": True,
                "image_url": url_for("static", filename=f"generated/{cached['filename']}"),
                "cost": cached["cost"],
                "deck_class": cached.get("deck_class"),
                "deck_mode": cached.get("deck_mode"),
                "filename": cached["filename"],
            })

    try:
        image, cost, deck_class_name, deck_mode_name, card_dbf_ids = _run_create_picture(deck_code, deck_name)

        if image is None:
            return jsonify({"success": False, "error": "Не удалось создать изображение. Проверьте код колоды."}), 422

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"deck_{timestamp}.jpg"
        filepath = os.path.join(GENERATED_DIR, filename)
        image.save(filepath, format="JPEG", quality=92, optimize=True)
        gen_id = add_generated(deck_code, deck_name, cost, filename, source="web",
                               deck_class=deck_class_name, deck_mode=deck_mode_name)
        add_deck_cards(gen_id, card_dbf_ids)

        return jsonify({
            "success": True,
            "cached": False,
            "image_url": url_for("static", filename=f"generated/{filename}"),
            "cost": cost,
            "deck_class": deck_class_name,
            "deck_mode": deck_mode_name,
            "filename": filename,
        })

    except asyncio.TimeoutError:
        return jsonify({"success": False, "error": "Генерация заняла слишком много времени. Попробуйте ещё раз."}), 504
    except Exception as e:
        return jsonify({"success": False, "error": str(e)[:300]}), 500


@app.route("/api/v1/recent")
def api_recent():
    """JSON API: последние сгенерированные колоды (только web, с реальными файлами)."""
    from deckview.repositories.web import get_all_generated_decks
    result = get_all_generated_decks(page=1, per_page=18, source="web",
                                      sort_by="created_at", sort_dir="desc")
    items = []
    for r in result.get("items", []):
        fn = r.get("filename", "")
        if not fn or fn.startswith("bot:"):
            continue
        filepath = os.path.join(GENERATED_DIR, fn)
        if not os.path.isfile(filepath):
            continue
        items.append({
            "id": r["id"],
            "image_url": url_for("static", filename=f"generated/{fn}"),
            "deck_class": r.get("deck_class"),
            "deck_mode": r.get("deck_mode"),
            "cost": r.get("cost"),
            "deck_name": r.get("deck_name"),
            "created_at": r.get("created_at"),
        })
    return jsonify(items)


@app.route('/publish', methods=['POST'])
def publish():
    """Publish deck to Telegram channel and/or WordPress. Expects JSON body with deck_code and optional metadata."""
    # Проверяем авторизацию: ключ в заголовке или теле запроса
    data = request.get_json(silent=True) or {}
    if DASHBOARD_SECRET:
        provided = request.headers.get("X-Publish-Secret") or data.get("secret", "")
        if provided != DASHBOARD_SECRET:
            return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    deck_code = (data.get('deck_code') or '').strip()
    if not deck_code:
        return jsonify({'success': False, 'telegram': False, 'wordpress': False, 'error': 'deck_code required'}), 400
    payload = {
        'deck_code': deck_code,
        'deck_name': data.get('deck_name') or 'Deck',
        'streamer': data.get('streamer') or 'Неизвестный',
        'player': data.get('player') or data.get('streamer'),
        'wins': data.get('wins', 0),
        'losses': data.get('losses', 0),
        'format': data.get('format'),
        'deck_class': data.get('deck_class'),
        'deck_mode': data.get('deck_mode'),
        'peak': data.get('peak', ''),
        'latest': data.get('latest', ''),
        'worst': data.get('worst', ''),
        'legend_rank': data.get('legend_rank', ''),
        'source_url': data.get('source_url', ''),
    }
    to_telegram = data.get('to_telegram', bool(CHANNEL_ID))
    to_wordpress = data.get('to_wordpress', True)
    try:
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                publish_deck(payload, to_telegram=to_telegram, to_wordpress=to_wordpress)
            )
        finally:
            loop.close()
    except Exception as e:
        return jsonify({'success': False, 'telegram': False, 'wordpress': False, 'error': str(e)}), 500
    success = result.get('image_generated') and result.get('error') is None
    return jsonify({
        'success': success,
        'telegram': result.get('telegram', False),
        'wordpress': result.get('wordpress', False),
        'error': result.get('error'),
    })


@app.route("/bgs")
def bgs_index():
    loop = asyncio.new_event_loop()
    try:
        data = loop.run_until_complete(bgs_manager.get_grouped_cards())
    finally:
        loop.close()
    return render_template("bgs_index.html", heroes=data["heroes"], minions=data["minions"])


@app.route("/bgs/generate", methods=["POST"])
def bgs_generate():
    data = request.get_json(silent=True) or {}
    hero_id = data.get("hero_id")
    minion_ids = data.get("minions", []) # List of 7 IDs

    if not hero_id:
        return jsonify({"success": False, "error": "Hero required"}), 400

    try:
        loop = asyncio.new_event_loop()
        try:
            # Look up card objects
            all_cards = loop.run_until_complete(bgs_manager.get_cards())
            cards_by_id = {str(c["id"]): c for c in all_cards}

            hero_data = cards_by_id.get(str(hero_id))
            if not hero_data:
                return jsonify({"success": False, "error": f"Hero ID {hero_id} not found"}), 404

            minions_data = [cards_by_id.get(str(mid)) if mid else None for mid in minion_ids]

            # Generate image
            image = loop.run_until_complete(place_bgs_board(hero_data, minions_data))
        finally:
            loop.close()

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"bgs_{timestamp}.png"
        filepath = os.path.join(GENERATED_DIR, filename)
        image.save(filepath)

        return jsonify({
            "success": True,
            "image_url": url_for("static", filename=f"generated/{filename}")
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/save_to_library", methods=["POST"])
def save_to_library():
    data = request.get_json(silent=True) or {}
    filename = os.path.basename((data.get("filename") or "").strip())
    if not filename:
        return jsonify({"status": "error", "message": "No filename"}), 400

    src = os.path.join(GENERATED_DIR, filename)
    dst = os.path.join(LIBRARY_DIR, filename)

    if os.path.isfile(src):
        shutil.copy(src, dst)
        db_add_to_library(filename)
        return jsonify({"status": "success", "message": f"Сохранено в {LIBRARY_DIR}"})
    return jsonify({"status": "error", "message": "File not found"}), 404


if __name__ == "__main__":
    import sys
    debug = "--debug" in sys.argv or os.getenv("FLASK_DEBUG", "").lower() in ("1", "true")
    app.run(host=WEB_HOST, port=WEB_PORT, debug=debug)
