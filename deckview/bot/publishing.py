"""
Publish deck image to Telegram channel and/or WordPress.
Single entry point: publish_deck(payload, to_telegram=..., to_wordpress=...).
"""
from __future__ import annotations

import asyncio
from io import BytesIO
from typing import Any, Dict, Optional

from aiogram import Bot
from aiogram.types import BufferedInputFile, CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup

from deckview.config import (
    CHANNEL_BOT_TOKEN, CHANNEL_ID,
    CLASS_EMOJI_ID_MAP, MODE_EMOJI_ID_MAP, PREMIUM_EMOJI_ID,
    PUBLISH_IMAGE_STYLE,
    normalize_deck_class_name,
)
from deckview.integrations.hsguru_archetype import recognize_archetype
from image_creator import create_picture
from deckview.integrations.wordpress import create_hs_deck_post

# Format (English) -> Russian mode name for caption and WordPress
FORMAT_MAP = {
    "Standard": "Стандарт",
    "Wild": "Вольный",
    "Classic": "Классический",
    "Twist": "Потасовка",
}

STREAMER_EMOJI_ID = "5195238702334364849"
GAMES_EMOJI_ID = "5330079338031249666"
WINRATE_EMOJI_ID = "5427362464704529291"
DUST_EMOJI_ID = PREMIUM_EMOJI_ID  # один и тот же emoji


def _format_to_mode(payload_format: Optional[str]) -> str:
    if not payload_format:
        return "Стандарт"
    return FORMAT_MAP.get(payload_format, payload_format)


def _build_channel_caption(
    deck_name: str,
    streamer: str,
    wins: int,
    losses: int,
    deck_code: str,
    deck_class: Optional[str] = None,
    deck_mode: Optional[str] = None,
    dust_cost: Optional[int] = None,
) -> str:
    total_games = wins + losses
    winrate = (wins / total_games * 100) if total_games > 0 else 0
    if winrate >= 60:
        winrate_emoji = "🔥"
    elif winrate >= 50:
        winrate_emoji = "✅"
    else:
        winrate_emoji = "📊"
    normalized_class = normalize_deck_class_name(deck_class)
    class_emoji = CLASS_EMOJI_ID_MAP.get(normalized_class or "")
    title_prefix = f'<tg-emoji emoji-id="{class_emoji}">🛡️</tg-emoji> ' if class_emoji else "⚔️ "
    parts = [f"<b>{title_prefix}{deck_name}</b>", ""]
    if deck_mode:
        mode_emoji = MODE_EMOJI_ID_MAP.get(deck_mode)
        mode_emoji_html = f'<tg-emoji emoji-id="{mode_emoji}">🎮</tg-emoji> ' if mode_emoji else ""
        parts.append(f"{mode_emoji_html}<b>Режим:</b> {deck_mode}")
    parts.append(f'<tg-emoji emoji-id="{STREAMER_EMOJI_ID}">👤</tg-emoji> <b>Стример:</b> {streamer}')
    parts.append(f'<tg-emoji emoji-id="{GAMES_EMOJI_ID}">🎮</tg-emoji> <b>Игр:</b> {total_games}')
    parts.append(
        f'<tg-emoji emoji-id="{WINRATE_EMOJI_ID}">{winrate_emoji}</tg-emoji> '
        f"<b>Винрейт:</b> {winrate:.1f}% <i>({wins}–{losses})</i>"
    )
    if dust_cost and dust_cost > 0:
        parts.append(f'<b>Пыль:</b> {dust_cost:,} <tg-emoji emoji-id="{DUST_EMOJI_ID}">💎</tg-emoji>')
    return "\n".join(parts)


def _build_channel_keyboard(deck_code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Скопировать",
                    style="primary",
                    icon_custom_emoji_id="5877301185639091664",
                    copy_text=CopyTextButton(text=deck_code),
                )
            ]
        ]
    )


async def publish_deck_to_telegram_channel(
    image_bytes: BytesIO,
    deck_name: str,
    streamer: str,
    wins: int,
    losses: int,
    deck_code: str,
    deck_class: Optional[str] = None,
    deck_mode: Optional[str] = None,
    dust_cost: Optional[int] = None,
) -> bool:
    if not CHANNEL_ID:
        return False
    try:
        caption = _build_channel_caption(
            deck_name=deck_name,
            streamer=streamer,
            wins=wins,
            losses=losses,
            deck_code=deck_code,
            deck_class=deck_class,
            deck_mode=deck_mode,
            dust_cost=dust_cost,
        )
        image_bytes.seek(0)
        photo = BufferedInputFile(image_bytes.read(), filename="deck.png")
        bot = Bot(token=CHANNEL_BOT_TOKEN)
        await bot.send_photo(
            chat_id=CHANNEL_ID,
            photo=photo,
            caption=caption,
            parse_mode="HTML",
            reply_markup=_build_channel_keyboard(deck_code),
        )
        await bot.session.close()
        return True
    except Exception as e:
        import traceback
        print(f"[Publish] ❌ Ошибка отправки в канал {CHANNEL_ID}: {type(e).__name__}: {e}")
        traceback.print_exc()
        return False


def _int_or_zero(val: Any) -> int:
    if val is None:
        return 0
    try:
        return int(val)
    except (ValueError, TypeError):
        return 0


def _str_or_empty(val: Any) -> str:
    if val is None:
        return ""
    return str(val).strip()


def _is_placeholder_deck_name(deck_name: str) -> bool:
    return deck_name.strip().lower() in {"", "deck", "колода"}


async def publish_deck(
    payload: Dict[str, Any],
    *,
    to_telegram: bool = True,
    to_wordpress: bool = True,
) -> Dict[str, Any]:
    """
    Generate deck image from payload["deck_code"], then optionally publish to
    Telegram channel and/or WordPress. All deck metadata comes from payload.
    Returns: { "image_generated": bool, "telegram": bool, "wordpress": bool, "error": str | None }
    """
    deck_code = (payload.get("deck_code") or "").strip()
    if not deck_code:
        return {"image_generated": False, "telegram": False, "wordpress": False, "error": "deck_code required"}

    requested_deck_name = _str_or_empty(payload.get("deck_name"))
    archetype_info = payload.get("archetype_info")
    if not isinstance(archetype_info, dict) and _is_placeholder_deck_name(requested_deck_name):
        archetype_info = await asyncio.to_thread(recognize_archetype, deck_code)

    deck_name = requested_deck_name or "Deck"
    if isinstance(archetype_info, dict) and _is_placeholder_deck_name(deck_name):
        archetype_name = _str_or_empty(archetype_info.get("archetype"))
        if archetype_name:
            deck_name = archetype_name

    try:
        image, cost, deck_class_from_api, deck_mode_from_api, _ = await create_picture(
            deck_code,
            deck_name,
            image_style=PUBLISH_IMAGE_STYLE,
        )
    except Exception as e:
        return {"image_generated": False, "telegram": False, "wordpress": False, "error": str(e)}

    if image is None:
        return {"image_generated": False, "telegram": False, "wordpress": False, "error": "Failed to generate image"}

    dust_cost = payload.get("dust")
    if dust_cost is None:
        dust_cost = cost
    else:
        dust_cost = _int_or_zero(dust_cost)

    buf = BytesIO()
    image.save(buf, format="JPEG", quality=92, optimize=True)
    buf.seek(0)

    streamer = payload.get("streamer") or "Неизвестный"
    player = payload.get("player") or streamer
    wins = _int_or_zero(payload.get("wins"))
    losses = _int_or_zero(payload.get("losses"))
    payload_format = payload.get("format")
    deck_mode = payload.get("deck_mode") or deck_mode_from_api or _format_to_mode(payload_format)
    deck_class = normalize_deck_class_name(payload.get("deck_class") or deck_class_from_api)
    peak = _str_or_empty(payload.get("peak"))
    latest = _str_or_empty(payload.get("latest"))
    worst = _str_or_empty(payload.get("worst"))
    legend_rank = _str_or_empty(payload.get("legend_rank"))
    source_url = payload.get("source_url") or ""

    tg_ok = False
    if to_telegram and CHANNEL_ID:
        tg_ok = await publish_deck_to_telegram_channel(
            image_bytes=BytesIO(buf.getvalue()),
            deck_name=deck_name,
            streamer=streamer,
            wins=wins,
            losses=losses,
            deck_code=deck_code,
            deck_class=deck_class,
            deck_mode=deck_mode,
            dust_cost=dust_cost,
        )

    wp_ok = False
    if to_wordpress:
        buf.seek(0)
        wp_ok = create_hs_deck_post(
            deck_code=deck_code,
            deck_name=deck_name,
            streamer=streamer,
            player=player,
            dust_cost=dust_cost,
            source_url=source_url or None,
            image_bytes=BytesIO(buf.getvalue()),
            deck_class=deck_class,
            deck_mode=deck_mode,
            wins=wins,
            losses=losses,
            peak=peak,
            latest=latest,
            worst=worst,
            legend_rank=legend_rank,
        )

    return {
        "image_generated": True,
        "telegram": tg_ok,
        "wordpress": wp_ok,
        "telegram_sent": tg_ok,
        "wordpress_posted": wp_ok,
        "deck_name": deck_name,
        "archetype": archetype_info if isinstance(archetype_info, dict) else None,
        "error": None,
    }
