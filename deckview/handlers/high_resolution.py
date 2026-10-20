"""Explicit high resolution document delivery via /hq CODE [title]."""

import asyncio
from collections.abc import Callable

from aiogram import Router, types
from aiogram.filters import Command, CommandObject

from deckview.services.high_resolution_service import HighResolutionService


def create_high_resolution_router(
    *,
    service: HighResolutionService | None = None,
    image_theme_provider: Callable | None = None,
) -> Router:
    router = Router(name="high_resolution")
    renderer = service or HighResolutionService()

    @router.message(Command("hq"))
    async def high_resolution_command(
        message: types.Message, command: CommandObject
    ) -> None:
        args = (command.args or "").strip().split(maxsplit=1)
        if not args:
            await message.reply(
                "Отправьте <code>/hq КОД_КОЛОДЫ</code> — пришлю JPG до 3840 px "
                "файлом без сжатия Telegram. После кода можно добавить название."
            )
            return
        user = message.from_user
        theme = (
            await asyncio.to_thread(
                image_theme_provider,
                user.id if user else None,
                message.chat.id,
            )
            if image_theme_provider
            else {}
        )
        status = await message.reply("Собираю колоду в высоком разрешении…")
        payload = {
            "deck_code": args[0],
            "deck_name": args[1] if len(args) > 1 else None,
            "chat_id": message.chat.id,
            "chat_type": message.chat.type,
            "message_thread_id": message.message_thread_id,
            "reply_to_message_id": message.message_id,
            "status_chat_id": message.chat.id,
            "status_message_id": status.message_id,
            "image_style": theme.get("style", "classic"),
            "cache_style": theme.get("cache_style", "classic"),
            "theme_revision": theme.get("personalization_revision", 0),
            "button_layout": theme.get("button_layout"),
            "user": {"id": user.id} if user else {},
        }
        for setting in (
            "background",
            "font",
            "text_size",
            "dust_display",
            "class_art",
            "layout",
            "mana_curve",
        ):
            if setting in theme:
                payload[f"image_{setting}"] = theme[setting]
        try:
            job_id = await renderer.submit(payload)
        except (ValueError, TypeError, IndexError):
            await status.edit_text(
                "Не удалось прочитать код колоды. Проверьте код после /hq."
            )
            return
        if not job_id:
            await status.edit_text(
                "Очередь генерации сейчас недоступна. Повторите /hq чуть позже."
            )

    return router
