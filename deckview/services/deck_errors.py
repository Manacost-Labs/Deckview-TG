"""User-facing texts for deck render failures.

Exception details go to logs only: they are meaningless to users, may mention
configuration, and can contain characters that break Telegram HTML parsing.
"""

from __future__ import annotations

UNREADABLE_DECK_CODE = (
    "⚠️ Не удалось прочитать колоду по этому коду.\n"
    "Скопируйте код заново в клиенте Hearthstone и пришлите его ещё раз."
)
CARD_SOURCE_UNAVAILABLE = (
    "⚠️ Сервис карт сейчас не отвечает.\n"
    "Попробуйте прислать код ещё раз через пару минут."
)
RENDER_FAILED = (
    "⚠️ Не получилось нарисовать колоду.\n"
    "Попробуйте прислать код ещё раз через минуту."
)
RENDER_TIMED_OUT = (
    "⚠️ Колода рисовалась слишком долго, попытка остановлена.\n"
    "Пришлите код ещё раз."
)


def friendly_deck_error(exc: BaseException) -> str:
    """Map a render exception to a short HTML-safe message for the user."""
    err_text = str(exc)
    err_lower = err_text.lower()
    if "invalid rune costs" in err_lower:
        return (
            "⚠️ Этот код колоды содержит недействительные руны.\n"
            "Скорее всего, колода создана в старом патче — обновите её в клиенте и скопируйте код заново."
        )
    if "invalid hero" in err_lower:
        return (
            "⚠️ Герой в этом коде колоды больше не существует в API.\n"
            "Возможно, колода устарела — попробуйте обновить её в клиенте Hearthstone."
        )
    if "unsupported string format or version" in err_lower:
        return (
            "⚠️ Этот формат кода колоды не поддерживается.\n"
            "Убедитесь, что код скопирован из актуальной версии Hearthstone."
        )
    if "BATTLE_NET_TOKEN" in err_text or "401" in err_text or "403" in err_text:
        return CARD_SOURCE_UNAVAILABLE
    return RENDER_FAILED
