from __future__ import annotations

import asyncio
import contextlib
import html
import os
import time
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any

from pathlib import Path

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile
from rq import get_current_job

PROJECT_ROOT = Path(__file__).resolve().parents[2]

from deckview.repositories.card_ratings import init_ratings_db
from deckview.keyboards.deck_actions import build_deck_action_keyboard
from deckview.config import (
    TELEGRAM_API_BASE_URL,
    TOKEN,
    build_deck_caption,
    normalize_deck_class_name,
)
from deckview.integrations.hsguru_archetype import get_cached_archetype, recognize_archetype
from deckview.integrations.hsguru_fetch import get_new_decks, mark_deck_published
from image_creator import create_picture
from image_creator.jpeg_output import write_rendered_jpeg, write_telegram_photo_jpeg
from image_creator.output_resolution import normalize_image_resolution
from image_creator.font_catalog import normalize_font_key
from image_creator.personalization import (
    normalize_class_art_mode,
    normalize_dust_display,
    normalize_mana_curve_mode,
)
from image_creator.text_size import normalize_title_size
from deckview.infrastructure.perf_telemetry import emit_render_timing
from deckview.services.deck_errors import (
    RENDER_FAILED,
    RENDER_TIMED_OUT,
    UNREADABLE_DECK_CODE,
    friendly_deck_error,
)
from deckview.bot.publishing import publish_deck
from deckview.infrastructure.render_cache import (
    acquire_render_lock,
    lookup_render_cache,
    materialize_render_cache,
    release_render_lock,
    store_render_cache,
)
from deckview.infrastructure.telegram_photo_cache import (
    delete_telegram_photo_file_id,
    get_telegram_photo_file_id,
    store_telegram_photo_file_id,
)
from deckview.services.deck_download_service import build_download_reference
from deckview.repositories.web import (
    add_bot_event,
    add_generated_with_cards,
    add_publish_log,
    ensure_bot_user,
    get_user_image_settings,
    init_db as init_web_db,
)

_TMP_DIR = "tmp_decks"
_GENERATED_DIR = PROJECT_ROOT / "static" / "generated"
_BAD_DECK_ERRORS = (
    "invalid rune costs",
    "invalid hero",
    "unsupported string format or version",
)
_ARCHETYPE_BUDGET_SECONDS = max(
    0.0,
    float(os.getenv("DECKVIEW_ARCHETYPE_BUDGET_SECONDS", "0.35").replace(",", ".")),
)


def _build_bot() -> Bot:
    default = DefaultBotProperties(parse_mode="HTML")
    if TELEGRAM_API_BASE_URL:
        session = AiohttpSession(
            api=TelegramAPIServer.from_base(TELEGRAM_API_BASE_URL, is_local=True)
        )
        return Bot(token=TOKEN, session=session, default=default)
    return Bot(token=TOKEN, default=default)


def _caption_with_archetype(caption: str, archetype_info: dict | None) -> str:
    if not isinstance(archetype_info, dict) or not archetype_info.get("success"):
        return caption
    archetype_name = str(archetype_info.get("archetype") or "").strip()
    if not archetype_name:
        return caption
    raw = str(archetype_info.get("archetype_raw") or "").strip()
    suffix = ""
    if raw and raw.lower() != archetype_name.lower():
        suffix = f" <i>({html.escape(raw)})</i>"
    return f"<b>Архетип:</b> {html.escape(archetype_name)}{suffix}\n{caption}"


def _message_kwargs(message_thread_id: int | None) -> dict[str, Any]:
    if message_thread_id is None:
        return {}
    return {"message_thread_id": message_thread_id}


def _delivery_kwargs(payload: dict[str, Any]) -> dict[str, Any]:
    kwargs = _message_kwargs(payload.get("message_thread_id"))
    reply_to_message_id = payload.get("reply_to_message_id")
    if reply_to_message_id:
        kwargs.update(
            {
                "reply_to_message_id": int(reply_to_message_id),
                "allow_sending_without_reply": True,
            }
        )
    return kwargs


async def _prepare_photo_upload(tmp_path: str, timings: dict[str, Any]) -> str | None:
    """Encode a lighter ``send_photo`` copy; ``tmp_path`` stays the download artifact."""
    upload_path = f"{os.path.splitext(tmp_path)[0]}_tg.jpg"
    started = time.perf_counter_ns()
    try:
        written = await asyncio.to_thread(write_telegram_photo_jpeg, tmp_path, upload_path)
    except Exception as exc:
        print(f"[Deckview worker] Telegram photo copy failed, uploading source: {exc}")
        with contextlib.suppress(OSError):
            os.remove(upload_path)
        written = False
    timings["telegram_photo_ms"] = round((time.perf_counter_ns() - started) / 1_000_000, 3)
    return upload_path if written else None


def _is_stale_telegram_file_id(exc: TelegramBadRequest) -> bool:
    message = str(exc).lower()
    return "file identifier" in message or "file_id" in message


async def _delete_status(bot: Bot, payload: dict[str, Any]) -> None:
    chat_id = payload.get("status_chat_id")
    message_id = payload.get("status_message_id")
    if not chat_id or not message_id:
        return
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


async def _send_or_edit_error(bot: Bot, payload: dict[str, Any], text: str) -> None:
    status_chat_id = payload.get("status_chat_id")
    status_message_id = payload.get("status_message_id")
    if status_chat_id and status_message_id:
        try:
            await bot.edit_message_text(
                text,
                chat_id=status_chat_id,
                message_id=status_message_id,
            )
            return
        except Exception:
            pass
    await bot.send_message(
        payload["chat_id"],
        text,
        **_delivery_kwargs(payload),
    )


async def _recognize_archetype(deck_code: str) -> dict:
    try:
        cached = get_cached_archetype(deck_code)
        if cached:
            return cached
        # Keep live recognition bounded so a Cloudflare slowdown cannot hold
        # an otherwise ready deck image for many seconds.
        return await asyncio.to_thread(
            recognize_archetype,
            deck_code,
            use_cache=False,
            network_timeout=0.8,
        )
    except Exception as exc:
        print(f"[Deckview worker] HSGuru archetype error: {exc}")
        return {"success": False, "error": str(exc)}


async def _render_deck_message_job(payload: dict[str, Any]) -> dict[str, Any]:
    handler_started = time.perf_counter_ns()
    timings: dict[str, Any] = {"cache_status": "miss"}
    result_status = "error"
    trace_id = None
    precise_queued_at_ns = payload.get("_queued_at_ns")
    try:
        precise_queued_at_ns = int(precise_queued_at_ns)
    except (TypeError, ValueError):
        precise_queued_at_ns = 0
    if precise_queued_at_ns > 0:
        timings["queue_wait_ms"] = round(
            max(0.0, (time.time_ns() - precise_queued_at_ns) / 1_000_000),
            3,
        )
    try:
        current_job = get_current_job()
        if current_job is not None:
            trace_id = current_job.id
            enqueued_at = current_job.enqueued_at
            if enqueued_at is not None and not precise_queued_at_ns:
                if enqueued_at.tzinfo is None:
                    enqueued_at = enqueued_at.replace(tzinfo=timezone.utc)
                timings["queue_wait_ms"] = round(
                    max(0.0, (datetime.now(timezone.utc) - enqueued_at).total_seconds() * 1000),
                    3,
                )
    except Exception:
        pass

    os.chdir(PROJECT_ROOT)
    os.makedirs(_TMP_DIR, exist_ok=True)
    init_ratings_db()
    init_web_db()

    user = payload.get("user") or {}
    user_id = user.get("id")
    payload_revision = int(payload.get("theme_revision") or 0)
    if user_id and str(payload.get("chat_type") or "") == "private":
        current_settings = get_user_image_settings(int(user_id))
        current_revision = int(
            current_settings.get("personalization_revision") or 0
        )
        if current_revision != payload_revision:
            current_style = str(
                current_settings.get("style") or "classic"
            ).strip().lower()
            current_background = None
            if current_style == "custom":
                kind = current_settings.get("background_kind")
                value = current_settings.get("background_value")
                if kind and value:
                    current_background = {
                        "kind": kind,
                        "value": value,
                        "blur": (
                            current_settings.get("blur", 0)
                            if kind == "image"
                            else 0
                        ),
                    }
                else:
                    current_style = "classic"
            payload["image_style"] = current_style
            payload["image_background"] = current_background
            payload["image_font"] = current_settings.get("font") or "auto"
            payload["image_text_size"] = (
                current_settings.get("text_size") or "normal"
            )
            payload["image_dust_display"] = (
                current_settings.get("dust_display") or "normal"
            )
            payload["image_class_art"] = {
                "mode": current_settings.get("class_art_mode") or "class",
                "path": current_settings.get("custom_logo_path"),
            }
            payload["image_layout"] = {
                "normal": 0,
                "extended": 0,
                "highlander": 0,
            }
            payload["image_mana_curve"] = {
                "mode": current_settings.get("mana_curve_mode") or "chart",
                "path": current_settings.get("mana_curve_image_path"),
            }
            payload["cache_style"] = (
                f"{current_style}:prefs:{current_revision}:layout:auto-v7"
            )
            payload["theme_revision"] = current_revision

    deck_code = str(payload.get("deck_code") or "").strip()
    deck_name = str(payload.get("deck_name") or "").strip() or None
    image_style = str(payload.get("image_style") or "classic").strip().lower()
    if image_style not in {"classic", "parchment", "custom"}:
        image_style = "classic"
    image_background = payload.get("image_background")
    if not isinstance(image_background, dict):
        image_background = None
    image_font = normalize_font_key(payload.get("image_font"))
    image_text_size = normalize_title_size(payload.get("image_text_size"))
    image_dust_display = normalize_dust_display(
        payload.get("image_dust_display")
    )
    image_class_art = payload.get("image_class_art")
    if not isinstance(image_class_art, dict):
        image_class_art = {"mode": "class", "path": None}
    image_class_art = {
        "mode": normalize_class_art_mode(image_class_art.get("mode")),
        "path": image_class_art.get("path"),
    }
    image_layout = {"normal": 0, "extended": 0, "highlander": 0}
    raw_curve = payload.get("image_mana_curve")
    image_mana_curve = {
        "mode": normalize_mana_curve_mode(
            raw_curve.get("mode") if isinstance(raw_curve, dict) else "chart"
        ),
        "path": raw_curve.get("path") if isinstance(raw_curve, dict) else None,
    }
    cache_style = str(payload.get("cache_style") or image_style).strip().lower()
    if ":layout:auto-v7" not in cache_style:
        cache_style = f"{cache_style}:layout:auto-v7"
    image_resolution = normalize_image_resolution(payload.get("image_resolution"))
    render_kwargs = (
        {"image_resolution": image_resolution} if image_resolution != "standard" else {}
    )
    if image_style != "classic":
        render_kwargs.update(
            {
                "image_style": image_style,
                "image_background": image_background,
            }
        )
    if image_font != "auto":
        render_kwargs["image_font"] = image_font
    if image_text_size != "normal":
        render_kwargs["image_text_size"] = image_text_size
    if image_dust_display != "normal":
        render_kwargs["image_dust_display"] = image_dust_display
    if (
        image_class_art["mode"] != "class"
        or image_class_art.get("path")
    ):
        render_kwargs["image_class_art"] = image_class_art
    if any(image_layout.values()):
        render_kwargs["image_layout"] = image_layout
    if image_mana_curve["mode"] != "chart" or image_mana_curve.get("path"):
        render_kwargs["image_mana_curve"] = image_mana_curve
    cache_kwargs = {"image_style": cache_style} if cache_style != "classic" else {}
    if image_resolution != "standard":
        cache_kwargs["image_resolution"] = image_resolution
    chat_id = payload["chat_id"]
    message_thread_id = payload.get("message_thread_id")
    download_key = uuid.uuid4().hex[:12]
    tmp_path = os.path.join(_TMP_DIR, f"_tmp_dl_{download_key}.jpg")
    bot = _build_bot()
    archetype_started = time.perf_counter_ns()
    archetype_task = asyncio.create_task(_recognize_archetype(deck_code))
    render_lock = None
    try:
        started = time.perf_counter_ns()
        cache_entry = lookup_render_cache(
            deck_code,
            deck_name,
            scope="telegram",
            **cache_kwargs,
        )
        timings["cache_lookup_ms"] = round((time.perf_counter_ns() - started) / 1_000_000, 3)
        telegram_photo_file_id = None
        if cache_entry is not None:
            if image_resolution == "standard":
                telegram_photo_file_id = get_telegram_photo_file_id(
                    cache_entry.get("cache_key")
                )
            if telegram_photo_file_id:
                # Telegram already owns this exact image. Avoid touching the
                # render artifact on disk unless Telegram rejects a stale ID.
                timings["cache_status"] = "telegram_file_id_hit"
            else:
                started = time.perf_counter_ns()
                materialized = materialize_render_cache(cache_entry, tmp_path)
                timings["cache_materialize_ms"] = round(
                    (time.perf_counter_ns() - started) / 1_000_000,
                    3,
                )
                if materialized:
                    timings["cache_status"] = "render_cache_hit"
                else:
                    cache_entry = None

        image = None
        if cache_entry is None:
            lock_started = time.perf_counter_ns()
            render_lock = await asyncio.to_thread(
                acquire_render_lock,
                deck_code,
                deck_name,
                cache_style,
                **({"image_resolution": image_resolution} if image_resolution != "standard" else {}),
            )
            timings["render_lock_ms"] = round(
                (time.perf_counter_ns() - lock_started) / 1_000_000,
                3,
            )
            # Another worker may have completed this exact render while we
            # waited for the distributed singleflight lock.
            if render_lock is not None:
                cache_entry = lookup_render_cache(
                    deck_code,
                    deck_name,
                    scope="telegram",
                    **cache_kwargs,
                )
                if cache_entry is not None and image_resolution == "standard":
                    telegram_photo_file_id = get_telegram_photo_file_id(
                        cache_entry.get("cache_key")
                    )
                if telegram_photo_file_id:
                    timings["cache_status"] = "telegram_file_id_hit"
                else:
                    started = time.perf_counter_ns()
                    materialized = (
                        materialize_render_cache(cache_entry, tmp_path)
                        if cache_entry is not None
                        else None
                    )
                    timings["cache_materialize_ms"] = round(
                        (time.perf_counter_ns() - started) / 1_000_000,
                        3,
                    )
                    if materialized:
                        timings["cache_status"] = "singleflight_cache_hit"
                    else:
                        cache_entry = None

        if cache_entry is not None:
            cost = cache_entry["cost"]
            deck_class = cache_entry.get("deck_class")
            deck_mode = cache_entry.get("deck_mode")
            card_dbf_ids = cache_entry.get("card_dbf_ids") or []
        else:
            timings["cache_status"] = "miss"
            try:
                image, cost, deck_class, deck_mode, card_dbf_ids = await create_picture(
                    deck_code,
                    deck_name=deck_name,
                    timings=timings,
                    **render_kwargs,
                )
            except Exception as exc:
                result_status = "invalid" if any(error in str(exc).lower() for error in _BAD_DECK_ERRORS) else "error"
                await _send_or_edit_error(bot, payload, friendly_deck_error(exc))
                add_bot_event(
                    "error",
                    payload.get("chat_type") or "",
                    {"context": "deck_code_worker", "error": str(exc)[:300]},
                )
                return {"ok": False, "error": str(exc)}

        if cache_entry is None and not image:
            result_status = "empty"
            await _send_or_edit_error(bot, payload, UNREADABLE_DECK_CODE)
            add_bot_event(
                "error",
                payload.get("chat_type") or "",
                {"context": "deck_code_worker", "error": "image None"},
            )
            return {"ok": False, "error": "image None"}

        normalized_class = normalize_deck_class_name(deck_class)

        if cache_entry is None:
            started = time.perf_counter_ns()
            reused_native_jpeg = write_rendered_jpeg(
                image,
                tmp_path,
                quality=92,
                optimize=True,
            )
            timings["jpeg_ms"] = round((time.perf_counter_ns() - started) / 1_000_000, 3)
            timings["jpeg_reused_native"] = reused_native_jpeg
            started = time.perf_counter_ns()
            stored_entry = store_render_cache(
                deck_code=deck_code,
                deck_name=deck_name,
                source_path=tmp_path,
                cost=cost,
                deck_class=normalized_class,
                deck_mode=deck_mode,
                card_dbf_ids=card_dbf_ids,
                **cache_kwargs,
            )
            timings["cache_store_ms"] = round((time.perf_counter_ns() - started) / 1_000_000, 3)
            timings["cache_store_result"] = "stored" if stored_entry else "disabled_or_miss"
            cache_entry = stored_entry

        if render_lock is not None:
            await asyncio.to_thread(release_render_lock, render_lock)
            render_lock = None

        gen_id = None
        started = time.perf_counter_ns()
        try:
            if user_id:
                ensure_bot_user(
                    int(user_id),
                    username=user.get("username"),
                    first_name=user.get("first_name"),
                )
            bot_filename = f"bot:{uuid.uuid4().hex}"
            gen_id = add_generated_with_cards(
                deck_code=deck_code,
                deck_name=deck_name,
                cost=cost,
                filename=bot_filename,
                dbf_ids=card_dbf_ids,
                source="telegram",
                deck_class=normalized_class,
                deck_mode=deck_mode,
                user_id=int(user_id) if user_id else None,
            )
        except Exception as exc:
            print(f"[Deckview worker] DB save failed: {exc}")
        finally:
            timings["db_ms"] = round((time.perf_counter_ns() - started) / 1_000_000, 3)

        if archetype_task.done():
            archetype_info = await archetype_task
        elif _ARCHETYPE_BUDGET_SECONDS > 0:
            try:
                archetype_info = await asyncio.wait_for(
                    asyncio.shield(archetype_task),
                    timeout=_ARCHETYPE_BUDGET_SECONDS,
                )
            except asyncio.TimeoutError:
                archetype_info = {"success": False, "error": "timeout"}
                timings["archetype_budget_exceeded"] = True
        else:
            archetype_info = {"success": False, "error": "disabled"}
        timings["archetype_ms"] = round(
            (time.perf_counter_ns() - archetype_started) / 1_000_000,
            3,
        )
        caption = _caption_with_archetype(
            build_deck_caption(deck_class, deck_mode, cost),
            archetype_info,
        )

        download_reference = build_download_reference(
            cache_entry,
            fallback_reference=download_key,
        )
        action_keyboard = build_deck_action_keyboard(
            deck_code,
            download_reference,
            gen_id,
            payload.get("button_layout"),
        )

        started = time.perf_counter_ns()
        if image_resolution == "high":
            await bot.send_document(
                chat_id,
                FSInputFile(tmp_path, filename="deck-high.jpg"),
                caption=caption,
                reply_markup=action_keyboard,
                **_delivery_kwargs(payload),
            )
        else:
            upload_path = None
            try:
                if not telegram_photo_file_id:
                    upload_path = await _prepare_photo_upload(tmp_path, timings)
                photo = telegram_photo_file_id or FSInputFile(upload_path or tmp_path)
                try:
                    sent_message = await bot.send_photo(
                        chat_id,
                        photo,
                        caption=caption,
                        reply_markup=action_keyboard,
                        **_delivery_kwargs(payload),
                    )
                except TelegramBadRequest as exc:
                    if (
                        not telegram_photo_file_id
                        or not cache_entry
                        or not _is_stale_telegram_file_id(exc)
                    ):
                        raise
                    materialize_started = time.perf_counter_ns()
                    materialized = materialize_render_cache(cache_entry, tmp_path)
                    timings["stale_file_materialize_ms"] = round(
                        (time.perf_counter_ns() - materialize_started) / 1_000_000,
                        3,
                    )
                    if not materialized or not os.path.isfile(tmp_path):
                        raise
                    delete_telegram_photo_file_id(cache_entry.get("cache_key"))
                    telegram_photo_file_id = None
                    timings["cache_status"] = "telegram_file_id_stale"
                    upload_path = await _prepare_photo_upload(tmp_path, timings)
                    sent_message = await bot.send_photo(
                        chat_id,
                        FSInputFile(upload_path or tmp_path),
                        caption=caption,
                        reply_markup=action_keyboard,
                        **_delivery_kwargs(payload),
                    )
            finally:
                if upload_path:
                    with contextlib.suppress(OSError):
                        os.remove(upload_path)
            if not telegram_photo_file_id and cache_entry is not None:
                photos = getattr(sent_message, "photo", None)
                largest_photo = photos[-1] if photos else None
                file_id = getattr(largest_photo, "file_id", None)
                if isinstance(file_id, str) and file_id:
                    store_telegram_photo_file_id(cache_entry.get("cache_key"), file_id)
        timings["delivery_ms"] = round((time.perf_counter_ns() - started) / 1_000_000, 3)
        status_started = time.perf_counter_ns()
        await _delete_status(bot, payload)
        timings["status_delete_ms"] = round(
            (time.perf_counter_ns() - status_started) / 1_000_000,
            3,
        )
        add_bot_event(
            "deck_code",
            payload.get("chat_type") or "",
            {"source": payload.get("source") or "queue"},
        )
        result_status = "ok"
        return {"ok": True, "gen_id": gen_id, "download_key": download_key}
    except Exception as exc:
        print(f"[Deckview worker] Unhandled render error: {exc}\n{traceback.format_exc()}")
        try:
            await _send_or_edit_error(bot, payload, RENDER_FAILED)
        except Exception:
            pass
        return {"ok": False, "error": str(exc)}
    finally:
        if render_lock is not None:
            await asyncio.to_thread(release_render_lock, render_lock)
        timings["handler_total_ms"] = round(
            (time.perf_counter_ns() - handler_started) / 1_000_000,
            3,
        )
        emit_render_timing(
            source="telegram_rq",
            result=result_status,
            timings=timings,
            deck_code=deck_code,
            trace_id=trace_id,
        )
        if not archetype_task.done():
            archetype_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await archetype_task
        await bot.session.close()


def render_deck_message_job(payload: dict[str, Any]) -> dict[str, Any]:
    return asyncio.run(_render_deck_message_job(payload))


async def _replace_status_after_failure(payload: dict[str, Any]) -> None:
    bot = _build_bot()
    try:
        await _send_or_edit_error(bot, payload, RENDER_TIMED_OUT)
    finally:
        await bot.session.close()


def render_deck_message_failed(job: Any, connection: Any, *exc_info: Any) -> None:
    """RQ ``on_failure``/``on_stopped`` hook for failures the job cannot catch.

    The job reports its own errors; this only fires when RQ aborts it (timeout,
    stop), which would otherwise leave the "Собираю колоду" status forever.
    """
    payload = job.args[0] if getattr(job, "args", None) else None
    if not isinstance(payload, dict) or not payload.get("chat_id"):
        return
    try:
        asyncio.run(_replace_status_after_failure(payload))
    except Exception as exc:
        print(f"[Deckview worker] Failed to report aborted render {job.id}: {exc}")


async def _render_api_deck_job(payload: dict[str, Any]) -> dict[str, Any]:
    """Render one website/API artifact outside the synchronous HTTP worker."""
    os.chdir(PROJECT_ROOT)
    init_web_db()
    _GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    deck_code = str(payload.get("deck_code") or "").strip()
    deck_name = str(payload.get("deck_name") or "").strip() or None
    image_style = str(payload.get("image_style") or "parchment").strip().lower()
    if not deck_code:
        return {"success": False, "error_code": "DECK_CODE_REQUIRED"}
    if image_style not in {"classic", "parchment"}:
        return {"success": False, "error_code": "INVALID_IMAGE_STYLE"}

    try:
        image_resolution = normalize_image_resolution(payload.get("image_resolution"))
    except ValueError:
        return {"success": False, "error_code": "INVALID_IMAGE_RESOLUTION"}
    resolution_kwargs = (
        {"image_resolution": image_resolution} if image_resolution != "standard" else {}
    )

    timings: dict[str, Any] = {"cache_status": "miss"}
    queued_at_ns = int(payload.get("_queued_at_ns") or 0)
    if queued_at_ns > 0:
        timings["queue_wait_ms"] = round(
            max(0.0, (time.time_ns() - queued_at_ns) / 1_000_000),
            3,
        )
    trace_id = str(payload.get("trace_id") or "") or None
    render_lock = None
    result_status = "error"
    started_total = time.perf_counter_ns()
    try:
        cached = lookup_render_cache(
            deck_code,
            deck_name,
            scope="api",
            image_style=image_style,
            **resolution_kwargs,
        )
        if cached is not None:
            timings["cache_status"] = "worker_cache_hit"
            result_status = "ok"
            return {"success": True, "cached": True, **cached, "image_resolution": image_resolution}

        lock_started = time.perf_counter_ns()
        render_lock = await asyncio.to_thread(
            acquire_render_lock,
            deck_code,
            deck_name,
            image_style,
            **resolution_kwargs,
        )
        timings["render_lock_ms"] = round(
            (time.perf_counter_ns() - lock_started) / 1_000_000,
            3,
        )
        if render_lock is not None:
            cached = lookup_render_cache(
                deck_code,
                deck_name,
                scope="api",
                image_style=image_style,
                **resolution_kwargs,
            )
            if cached is not None:
                timings["cache_status"] = "singleflight_cache_hit"
                result_status = "ok"
                return {"success": True, "cached": True, **cached, "image_resolution": image_resolution}

        image, cost, deck_class, deck_mode, card_dbf_ids = await create_picture(
            deck_code,
            deck_name=deck_name,
            timings=timings,
            image_style=image_style,
            **resolution_kwargs,
        )
        if image is None:
            result_status = "empty"
            return {"success": False, "error_code": "RENDER_FAILED"}

        filename = (
            f"deck_{image_style}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
            ".jpg"
        )
        filepath = _GENERATED_DIR / filename
        jpeg_started = time.perf_counter_ns()
        timings["jpeg_reused_native"] = await asyncio.to_thread(
            write_rendered_jpeg,
            image,
            filepath,
            quality=90,
            optimize=False,
        )
        timings["jpeg_ms"] = round(
            (time.perf_counter_ns() - jpeg_started) / 1_000_000,
            3,
        )
        stored = await asyncio.to_thread(
            store_render_cache,
            deck_code=deck_code,
            deck_name=deck_name,
            source_path=filepath,
            cost=cost,
            deck_class=deck_class,
            deck_mode=deck_mode,
            card_dbf_ids=card_dbf_ids,
            image_style=image_style,
            **resolution_kwargs,
            generate_preview=True,
        )
        if stored and stored.get("preview_prepare_ms") is not None:
            timings["preview_ms"] = stored["preview_prepare_ms"]
            timings["preview_bytes"] = stored.get("preview_size_bytes")
        entry = stored or {
            "deck_code": deck_code,
            "deck_name": deck_name,
            "image_style": image_style,
            "filename": filename,
            "artifact_path": str(filepath),
            "cost": int(cost or 0),
            "deck_class": deck_class,
            "deck_mode": deck_mode,
            "card_dbf_ids": card_dbf_ids,
        }
        add_generated_with_cards(
            deck_code=deck_code,
            deck_name=deck_name,
            cost=cost,
            filename=filename,
            dbf_ids=card_dbf_ids,
            source=f"api:async:{image_style}",
            deck_class=deck_class,
            deck_mode=deck_mode,
            user_id=None,
        )
        result_status = "ok"
        return {"success": True, "cached": False, **entry, "image_resolution": image_resolution}
    except Exception as exc:
        timings["error_type"] = type(exc).__name__
        return {
            "success": False,
            "error_code": "INTERNAL_ERROR",
            "error": str(exc)[:300],
        }
    finally:
        if render_lock is not None:
            await asyncio.to_thread(release_render_lock, render_lock)
        timings["handler_total_ms"] = round(
            (time.perf_counter_ns() - started_total) / 1_000_000,
            3,
        )
        emit_render_timing(
            source="web_api_async",
            result=result_status,
            timings=timings,
            deck_code=deck_code,
            trace_id=trace_id,
        )


def render_api_deck_job(payload: dict[str, Any]) -> dict[str, Any]:
    return asyncio.run(_render_api_deck_job(payload))


async def _publish_hsguru_payload_job(payload: dict[str, Any], to_telegram: bool) -> dict[str, Any]:
    os.chdir(PROJECT_ROOT)
    init_web_db()

    deck_code = payload["deck_code"]
    deck_name = payload.get("deck_name", "Deck")
    normalized_format = payload.pop("_normalized_format", None)
    payload.pop("_deck", None)
    print(f"[Deckview worker] Publishing HSGuru deck: {deck_name!r} ({deck_code[:20]}...)")

    result = await publish_deck(payload, to_telegram=to_telegram, to_wordpress=True)
    tg_sent = result.get("telegram_sent", False)
    wp_posted = result.get("wordpress_posted", False)
    if result.get("image_generated"):
        if wp_posted:
            mark_deck_published(
                {**payload, "deck_code": deck_code, "_normalized_format": normalized_format or "Стандарт"}
            )
        else:
            print(f"[Deckview worker] WordPress rejected {deck_name!r}; deck is left for retry")
        add_publish_log(
            deck_name=deck_name,
            deck_class=payload.get("deck_class"),
            deck_mode=normalized_format or payload.get("deck_mode"),
            deck_code=deck_code,
            telegram_sent=tg_sent,
            wordpress_posted=wp_posted,
        )
        return {"ok": bool(wp_posted), "telegram_sent": tg_sent, "wordpress_posted": wp_posted}

    err_msg = result.get("error") or "Изображение не создано"
    if any(keyword in err_msg.lower() for keyword in _BAD_DECK_ERRORS):
        mark_deck_published(
            {**payload, "deck_code": deck_code, "_normalized_format": normalized_format or "Стандарт"}
        )
    add_publish_log(
        deck_name=deck_name,
        deck_class=payload.get("deck_class"),
        deck_mode=normalized_format or payload.get("deck_mode"),
        deck_code=deck_code,
        telegram_sent=False,
        wordpress_posted=False,
        error=err_msg,
    )
    return {"ok": False, "error": err_msg}


def publish_hsguru_payload_job(payload: dict[str, Any], to_telegram: bool) -> dict[str, Any]:
    return asyncio.run(_publish_hsguru_payload_job(payload, to_telegram))


async def _publish_hsguru_cycle_job(to_telegram: bool) -> dict[str, Any]:
    os.chdir(PROJECT_ROOT)
    init_web_db()
    payloads = await asyncio.to_thread(get_new_decks)
    if not payloads:
        print("[Deckview worker] HSGuru cycle: no new decks")
        return {"ok": True, "count": 0, "published": 0}

    published = 0
    errors = 0
    print(f"[Deckview worker] HSGuru cycle: {len(payloads)} payloads")
    for payload in payloads:
        try:
            result = await _publish_hsguru_payload_job(dict(payload), to_telegram)
            if result.get("ok"):
                published += 1
        except Exception as exc:
            errors += 1
            print(f"[Deckview worker] HSGuru cycle item failed: {exc}\n{traceback.format_exc()}")
            try:
                add_publish_log(
                    deck_name=payload.get("deck_name"),
                    deck_class=payload.get("deck_class"),
                    deck_mode=payload.get("_normalized_format") or payload.get("deck_mode"),
                    deck_code=payload.get("deck_code"),
                    telegram_sent=False,
                    wordpress_posted=False,
                    error=str(exc)[:500],
                )
            except Exception:
                pass
    return {"ok": errors == 0, "count": len(payloads), "published": published, "errors": errors}


def publish_hsguru_cycle_job(to_telegram: bool) -> dict[str, Any]:
    return asyncio.run(_publish_hsguru_cycle_job(to_telegram))
