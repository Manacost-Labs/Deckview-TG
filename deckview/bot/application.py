# Gevent monkey patch disabled: conflicts with aiogram's asyncio (LoopExit in thread pool).
# from gevent.monkey import patch_all
# patch_all(thread=False, select=False)

import asyncio
import glob as _glob
import html
import json
import os
import re
import time
import traceback

_BOT_START_TIME = time.monotonic()
import uuid
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

from aiohttp import web
from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router, types
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    BotCommand,
    CopyTextButton,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    KeyboardButton,
    LinkPreviewOptions,
    ReplyKeyboardMarkup,
)

from deckview.config import (
    ADMIN_IDS, CHANNEL_ID, DISCUSSION_GROUP_ID, HSJSON_CARDS_URL, HSGURU_INTERVAL_SECONDS, TOKEN,
    TELEGRAM_API_BASE_URL,
    DECKVIEW_SOURCE_CODE_URL,
    DECKVIEW_UPDATE_MODE,
    DECKVIEW_WEBHOOK_DROP_PENDING_UPDATES,
    DECKVIEW_WEBHOOK_HOST,
    DECKVIEW_WEBHOOK_MAX_BODY_BYTES,
    DECKVIEW_WEBHOOK_PATH,
    DECKVIEW_WEBHOOK_PORT,
    DECKVIEW_WEBHOOK_SECRET,
    DECKVIEW_WEBHOOK_URL,
    CLASS_EMOJI_ID_MAP, MODE_EMOJI_ID_MAP, PREMIUM_EMOJI_ID,
    build_deck_caption, normalize_deck_class_name,
)
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from deckview.middlewares.flood_protection import TelegramFloodProtectionMiddleware
from PIL import Image, ImageOps, UnidentifiedImageError
from deckview.workers.queue import enqueue_deck_render, enqueue_hsguru_cycle, enqueue_hsguru_publish
from deckview.keyboards.deck_actions import (
    DECK_BUTTON_LAYOUT_OPTIONS,
    build_deck_action_keyboard,
    normalize_deck_button_layout,
)
from deckview.bot.routers import create_modular_router
from image_creator import create_picture
from image_creator.background_preview import build_gradient_preview
from image_creator.card_showcase import build_card_showcase, build_full_art_showcase
from image_creator.custom_background import (
    BLUR_STRENGTHS,
    GRADIENT_PRESETS,
    normalize_background_blur,
    normalize_gradient,
)
from image_creator.font_catalog import FONT_OPTIONS, font_label, normalize_font_key
from image_creator.font_preview import build_font_preview
from image_creator.personalization import (
    CLASS_ART_OPTIONS,
    DUST_DISPLAY_OPTIONS,
    MANA_CURVE_OPTIONS,
    normalize_class_art_mode,
    normalize_dust_display,
    normalize_mana_curve_mode,
)
from image_creator.text_size import (
    TITLE_SIZE_OPTIONS,
    normalize_title_size,
    title_size_label,
)
from image_creator.cost_getter import get_cost_of_deck
from image_creator.deck_retriever import retrieve_deck
from deckview.bot.publishing import publish_deck
from deckview.integrations.hsguru_archetype import get_cached_archetype, recognize_archetype
from deckview.integrations.hsguru_fetch import get_new_decks, get_one_new_deck, mark_deck_published
from deckview.integrations.manacost_api import (
    api_deck_to_bot as manacost_api_deck_to_bot,
    best_decks_by_archetype as manacost_best_decks_by_archetype,
    card_web_url as manacost_card_web_url,
    find_decks_with_cards as manacost_find_decks_with_cards,
    get_card as manacost_get_card,
    get_card_by_dbf_id as manacost_get_card_by_dbf_id,
    get_card_bundle_with_fallback as manacost_get_card_bundle_with_fallback,
    get_card_full_art as manacost_get_card_full_art,
    get_card_image as manacost_get_card_image,
    get_card_with_fallback as manacost_get_card_with_fallback,
    get_deck as manacost_get_deck,
    get_decks as manacost_get_decks,
    get_meta as manacost_get_meta,
    remember_search as manacost_remember_search,
    remembered_search as manacost_remembered_search,
    search_cards as manacost_search_cards,
    search_cards_flexible as manacost_search_cards_flexible,
)
from deckview.integrations.manacost_identity import (
    AuthorizationPending,
    ManacostIdentityError,
    exchange_device_code as manacost_exchange_device_code,
    get_authorized_profile as manacost_get_authorized_profile,
    revoke_refresh_token as manacost_revoke_refresh_token,
    start_device_authorization as manacost_start_device_authorization,
)
from deckview.infrastructure.render_cache import (
    lookup_render_cache,
    materialize_render_cache,
    store_render_cache,
)
from deckview.services.deck_download_service import (
    build_download_reference,
    resolve_cached_download,
)
from deckview.services.meta_service import (
    META_SOURCE_LIMIT,
    META_VIEW_LIMIT,
    rank_meta_by_winrate,
)
from framework.hearthstonejson_api import (
    configure as hsjson_configure,
    find_cards_by_query,
    get_card_by_dbfid,
    get_card_en_name,
    get_random_card,
    search_cards_fuzzy,
    suggest_cards_by_name,
)
from deckview.repositories.card_ratings import init_ratings_db
from deckview.repositories.web import (
    add_bot_event,
    add_generated_with_cards,
    add_publish_log,
    apply_user_image_design,
    apply_user_image_design_to_chat,
    delete_user_image_design,
    ensure_bot_user,
    get_all_bot_user_ids,
    get_bot_events_count,
    get_bot_stats_for_dashboard,
    get_bot_users_count,
    get_deck_by_id,
    get_saved_decks_for_user,
    get_managed_chat,
    get_managed_chats_for_user,
    get_manacost_identity,
    get_user_image_settings,
    get_user_image_designs,
    get_user_image_style,
    init_db as init_web_db,
    is_managed_chat_command_enabled,
    register_managed_chat,
    remove_manacost_identity,
    remove_saved_deck,
    save_deck_for_user,
    save_managed_chat_image_design,
    save_user_image_design,
    set_managed_chat_custom_background,
    set_managed_chat_background_blur,
    set_managed_chat_class_art_mode,
    set_managed_chat_custom_logo,
    set_managed_chat_disabled_commands,
    set_managed_chat_dust_display,
    set_managed_chat_image_font,
    set_managed_chat_image_style,
    set_managed_chat_image_text_size,
    set_managed_chat_mana_curve_image,
    set_managed_chat_mana_curve_mode,
    set_managed_chat_deck_button_layout,
    set_user_background_blur,
    set_user_class_art_mode,
    set_user_custom_background,
    set_user_custom_logo,
    set_user_dust_display,
    set_user_image_font,
    set_user_image_style,
    set_user_image_text_size,
    set_user_mana_curve_image,
    set_user_mana_curve_mode,
    save_manacost_identity,
    TELEGRAM_GROUP_ANONYMOUS_BOT_ID,
)

def _build_bot() -> Bot:
    default = DefaultBotProperties(parse_mode="HTML")
    if TELEGRAM_API_BASE_URL:
        session = AiohttpSession(
            api=TelegramAPIServer.from_base(TELEGRAM_API_BASE_URL, is_local=True)
        )
        return Bot(token=TOKEN, session=session, default=default)
    return Bot(token=TOKEN, default=default)


bot = _build_bot()
dp = Dispatcher()
router = Router()

# Папка для временных файлов (создаётся при старте)
_TMP_DIR = "tmp_decks"
os.makedirs(_TMP_DIR, exist_ok=True)

# Максимальный возраст временного файла: 2 часа
_TMP_MAX_AGE_SEC = 2 * 3600


def _cleanup_tmp_files(max_age_sec: int = _TMP_MAX_AGE_SEC) -> int:
    """Удаляет временные файлы колод старше max_age_sec секунд. Возвращает число удалённых файлов."""
    now = time.time()
    removed = 0
    # Новые файлы в tmp_decks/
    for path in _glob.glob(os.path.join(_TMP_DIR, "_tmp_dl_*.jpg")):
        try:
            if now - os.path.getmtime(path) > max_age_sec:
                os.remove(path)
                removed += 1
        except OSError:
            pass
    # Старые файлы в корне проекта (накопились до переезда в tmp_decks)
    for path in _glob.glob("_tmp_dl_*.jpg"):
        try:
            if now - os.path.getmtime(path) > max_age_sec:
                os.remove(path)
                removed += 1
        except OSError:
            pass
    return removed


async def _recognize_archetype_async(deck_code: str) -> dict:
    try:
        cached = get_cached_archetype(deck_code)
        if cached:
            return cached
        return await asyncio.to_thread(
            recognize_archetype,
            deck_code,
            use_cache=False,
            network_timeout=0.8,
        )
    except Exception as e:
        print(f"[Deckview] Ошибка распознавания архетипа HSGuru: {e}")
        return {"success": False, "error": str(e)}


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


async def _periodic_cleanup_task():
    """Каждые 2 часа удаляет устаревшие временные файлы колод."""
    while True:
        await asyncio.sleep(_TMP_MAX_AGE_SEC)
        try:
            n = _cleanup_tmp_files()
            if n:
                print(f"[Deckview] Очистка tmp: удалено {n} файлов")
        except Exception as e:
            print(f"[Deckview] Ошибка очистки tmp: {e}")


async def _prewarm_manacost_cache() -> None:
    """Warm API data in the background so the first user does not pay cold latency."""
    try:
        await asyncio.gather(
            asyncio.to_thread(manacost_get_decks, "standard", all_pages=True),
            asyncio.to_thread(manacost_get_decks, "wild", all_pages=True),
            asyncio.to_thread(
                manacost_get_meta,
                "standard",
                limit=META_SOURCE_LIMIT,
            ),
            asyncio.to_thread(
                manacost_get_meta,
                "wild",
                limit=META_SOURCE_LIMIT,
            ),
        )
        await asyncio.gather(
            _load_manacost_meta(1),
            _load_manacost_meta(2),
        )
        print("[Deckview] Manacost API cache warmed")
    except Exception as e:
        print(f"[Deckview] Manacost API cache warmup skipped: {str(e)[:200]}")


MAIN_REPLY_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="Мой профиль"),
            KeyboardButton(text="О боте"),
        ],
        [
            KeyboardButton(text="🏆 Посмотреть мету"),
            KeyboardButton(text="⚙️ Настройки"),
        ],
        [
            KeyboardButton(text="🏟️ Арена"),
            KeyboardButton(text="🎮 Поля сражений"),
        ],
    ],
    resize_keyboard=True,
    input_field_placeholder="Отправьте код колоды или выберите действие",
)


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


async def _set_bot_commands() -> None:
    """Обновляет список команд в меню Telegram."""
    try:
        await bot.set_my_commands([
            BotCommand(command="start", description="Запустить бота"),
            BotCommand(command="menu", description="Показать кнопки меню"),
            BotCommand(command="help", description="Все команды"),
            BotCommand(command="meta", description="Мета Стандарт/Вольный"),
            BotCommand(command="arena", description="Винрейты классов Арены"),
            BotCommand(command="comps", description="Поля сражений"),
            BotCommand(command="card", description="Карта по названию или id"),
            BotCommand(command="compare", description="Сравнить две колоды"),
            BotCommand(command="findwith", description="Найти колоды по картам"),
            BotCommand(command="profile", description="Профиль и Manacost ID"),
            BotCommand(command="settings", description="Оформление картинок"),
            BotCommand(command="hq", description="Колода файлом до 3840 px без сжатия"),
            BotCommand(command="healt", description="Диагностика бота"),
        ])
    except Exception as e:
        print(f"[Deckview] Не удалось обновить меню команд: {e}")


def _log_bot_event(event_type: str, chat_type: str | None = None, payload: dict | None = None) -> None:
    """Логирование события бота для дашборда. Не прерывает работу при ошибке."""
    try:
        add_bot_event(event_type, chat_type or "", payload)
    except Exception as e:
        print(f"[Deckview] log event: {e}")


# /findwith: колод на странице и кнопок в ряд
FINDWITH_PER_PAGE = 8
FINDWITH_BUTTONS_PER_ROW = 2
_MANACOST_AUTH_PENDING: dict[int, dict] = {}


def _build_profile_display(
    decks: list,
    manacost_identity: dict | None = None,
    *,
    show_manacost: bool = True,
) -> tuple[str, list]:
    """Строит текст и клавиатуру для отображения сохранённых колод.

    Возвращает (html_text, inline_keyboard_rows).
    """
    _DECK_EMOJI_ID  = "5440521926671886884"
    _DUST_EMOJI_ID  = "5440749199161322936"
    _DEL_EMOJI_ID   = "5879915802815107172"

    dust_icon = f'<tg-emoji emoji-id="{_DUST_EMOJI_ID}">💨</tg-emoji>'
    deck_icon = f'<tg-emoji emoji-id="{_DECK_EMOJI_ID}">🃏</tg-emoji>'

    lines = ["👤 <b>Мой профиль</b>"]
    buttons: list[list[InlineKeyboardButton]] = []

    if show_manacost:
        if manacost_identity:
            access_label = (
                "✅ активна"
                if manacost_identity.get("has_access")
                else "не активна"
            )
            if manacost_identity.get("subscription_stale"):
                access_label += " · данные устарели"
            lines.extend(
                [
                    "",
                    "🪪 <b>Manacost ID подключён</b>",
                    f"Имя: <b>{html.escape(str(manacost_identity.get('display_name') or ''))}</b>",
                    "ID: "
                    f"<code>{html.escape(str(manacost_identity.get('public_profile_id') or ''))}</code>",
                    f"Доступ к подписке: <b>{access_label}</b>",
                ]
            )
        else:
            lines.extend(
                [
                    "",
                    "🪪 Manacost ID: <b>не подключён</b>",
                    "Вход подтверждается на официальном сайте Manacost. "
                    "Пароль и токены бот не хранит.",
                ]
            )

    lines.extend(["", "🃏 <b>Сохранённые колоды</b>"])
    if not decks:
        lines.append(
            "Пока пусто. Нажмите «Сохранить» под понравившейся колодой."
        )

    for i, deck in enumerate(decks, 1):
        name = deck.get("deck_name") or f"Колода {i}"
        cost = deck.get("cost")
        cls  = deck.get("deck_class")

        # Текст строки в сообщении (HTML, поддерживает tg-emoji)
        if cls and cost is not None:
            line = f"{i}. {deck_icon} <b>{html.escape(name)}</b> · {html.escape(cls)} · {cost:,} {dust_icon}"
        elif cost is not None:
            line = f"{i}. {deck_icon} <b>{html.escape(name)}</b> · {cost:,} {dust_icon}"
        else:
            line = f"{i}. {deck_icon} <b>{html.escape(name)}</b>"
        lines.append(line)

        # Текст кнопки — plain text (без HTML)
        if cls and cost is not None:
            btn_label = f"{name} · {cls} · {cost:,}"
        elif cost is not None:
            btn_label = f"{name} · {cost:,}"
        else:
            btn_label = name
        if len(btn_label) > 38:
            btn_label = btn_label[:35] + "…"

        buttons.append([
            InlineKeyboardButton(
                text=btn_label,
                icon_custom_emoji_id=_DECK_EMOJI_ID,
                callback_data=f"profile_deck:{deck['id']}",
            ),
            InlineKeyboardButton(
                text=" ",
                style="danger",
                icon_custom_emoji_id=_DEL_EMOJI_ID,
                callback_data=f"profile_remove:{deck['id']}",
            ),
        ])

    if show_manacost:
        if manacost_identity:
            buttons.append(
                [
                    InlineKeyboardButton(
                        text="🌐 Профиль Manacost",
                        url=str(manacost_identity["profile_url"]),
                    ),
                    InlineKeyboardButton(
                        text="🔄 Обновить",
                        callback_data="profile_manacost_login",
                    ),
                ]
            )
            buttons.append(
                [
                    InlineKeyboardButton(
                        text="🔓 Отвязать Manacost ID",
                        callback_data="profile_manacost_unlink",
                    )
                ]
            )
        else:
            buttons.append(
                [
                    InlineKeyboardButton(
                        text="🔐 Войти через Manacost ID",
                        callback_data="profile_manacost_login",
                    )
                ]
            )

    return "\n".join(lines), buttons


@router.message(Command("profile"))
async def cmd_profile(message: types.Message):
    """Показать сохранённые колоды пользователя."""
    user = message.from_user
    if not user:
        return
    decks = get_saved_decks_for_user(user.id, limit=20)
    is_private = message.chat.type == "private"
    identity = get_manacost_identity(user.id) if is_private else None
    text, buttons = _build_profile_display(
        decks,
        identity,
        show_manacost=is_private,
    )
    await message.answer(
        text,
        reply_markup=(
            InlineKeyboardMarkup(inline_keyboard=buttons)
            if buttons
            else MAIN_REPLY_KEYBOARD if is_private else None
        ),
    )


def _manage_chat_start_payload(chat_id: int) -> str:
    marker = "n" if int(chat_id) < 0 else "p"
    return f"{_MANAGE_CHAT_START_PREFIX}{marker}{abs(int(chat_id))}"


def _manage_chat_id_from_payload(payload: str) -> int | None:
    match = re.fullmatch(r"manage_([np])(\d{1,20})", str(payload or ""))
    if not match:
        return None
    value = int(match.group(2))
    return -value if match.group(1) == "n" else value


def _private_chat_claim_url(chat_id: int) -> str:
    return (
        "https://t.me/manacostcard_bot?start="
        + _manage_chat_start_payload(chat_id)
    )


async def _connect_group_settings(message: types.Message) -> bool:
    """Register a group selected through the Telegram startgroup deep link."""
    if str(message.chat.type).lower() not in {"group", "supergroup"}:
        return False
    sender_chat = getattr(message, "sender_chat", None)
    anonymous_admin = bool(
        sender_chat
        and getattr(sender_chat, "id", None) == message.chat.id
        and getattr(message.from_user, "id", None)
        == TELEGRAM_GROUP_ANONYMOUS_BOT_ID
    )
    if anonymous_admin:
        # Telegram hides the real account behind GroupAnonymousBot. Sending as
        # the group proves admin rights, but a private deep link is needed to
        # associate the group with that administrator's settings profile.
        register_managed_chat(
            message.chat.id,
            message.chat.title or str(message.chat.id),
            str(message.chat.type),
            None,
        )
        await message.answer(
            "✅ Вижу, что команда отправлена анонимным администратором.\n\n"
            "Нажмите кнопку ниже: в личном диалоге бот проверит ваши права "
            "и добавит этот чат в раздел <b>Мои чаты</b>.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(
                        text="🔗 Привязать чат к моему профилю",
                        url=_private_chat_claim_url(message.chat.id),
                    )
                ]]
            ),
        )
        return True
    user = message.from_user
    if not user or not await _can_manage_chat(user.id, message.chat.id):
        await message.answer(
            "Настройки чата доступны только его администраторам."
        )
        return True
    ensure_bot_user(
        user.id,
        username=user.username,
        first_name=user.first_name,
    )
    register_managed_chat(
        message.chat.id,
        message.chat.title or str(message.chat.id),
        str(message.chat.type),
        user.id,
    )
    await message.answer(
        "✅ Чат подключён.\n\n"
        "Теперь он доступен в разделе <b>Мои чаты</b> в личном диалоге "
        "с ботом.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(
                    text="⚙️ Настроить чат",
                    url=_PRIVATE_SETTINGS_URL,
                )
            ]]
        ),
    )
    return True


async def _claim_managed_chat(
    message: types.Message,
    chat_id: int,
) -> None:
    user = message.from_user
    if not user or not await _can_manage_chat(user.id, chat_id):
        await message.answer(
            "❌ Telegram не подтвердил права администратора в этом чате."
        )
        return
    chat = get_managed_chat(chat_id)
    if chat is None:
        try:
            telegram_chat = await bot.get_chat(chat_id)
            title = telegram_chat.title or str(chat_id)
            chat_type = str(telegram_chat.type)
        except Exception:
            await message.answer(
                "❌ Не удалось получить данные чата. Добавьте бота "
                "администратором и попробуйте снова."
            )
            return
    else:
        title = str(chat.get("title") or chat_id)
        chat_type = str(chat.get("chat_type") or "supergroup")
    register_managed_chat(
        chat_id,
        title,
        chat_type,
        user.id,
    )
    await message.answer(
        f"✅ Чат <b>{html.escape(title)}</b> привязан к вашему профилю.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⚙️ Настроить чат",
                        callback_data=f"settings_chat:{chat_id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="👥 Мои чаты",
                        callback_data="settings_chats",
                    )
                ],
            ]
        ),
    )


@router.message(Command('start'))
async def process_start_command(message: types.Message):
    if message.chat.type == "private" and message.from_user:
        ensure_bot_user(
            message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
        )
    start_argument = ""
    if message.text:
        parts = message.text.split(maxsplit=1)
        start_argument = parts[1].strip().lower() if len(parts) > 1 else ""
    claimed_chat_id = _manage_chat_id_from_payload(start_argument)
    if message.chat.type == "private" and claimed_chat_id is not None:
        await _claim_managed_chat(message, claimed_chat_id)
        return
    if message.chat.type == "private" and start_argument == "settings":
        await message.answer(
            _settings_home_text(message.from_user.id),
            reply_markup=_settings_home_keyboard(),
        )
        return
    if start_argument == "settings" and await _connect_group_settings(message):
        return
    text = (
        f"Привет, {message.chat.first_name}!\n\n"
        "Отправь код колоды (начинается с <code>AA</code>), и я пришлю картинку.\n"
        "• /help — все команды\n"
        "• /card — картинка карты по названию или id\n"
        "• /compare — сравнить две колоды\n"
        "• /findwith — поиск колод по картам\n\n"
        f'<a href="{html.escape(DECKVIEW_SOURCE_CODE_URL, quote=True)}">Исходный код</a>'
        " · AGPL-3.0-or-later"
    )
    if message.chat.type == "private":
        await message.answer(text, reply_markup=MAIN_REPLY_KEYBOARD)
    else:
        await message.answer(text)


@router.message(Command("menu"))
async def cmd_menu(message: types.Message):
    """Обновить клавиатуру (показать кнопки меню)."""
    if message.chat.type == "private":
        await message.answer("Меню обновлено 👇", reply_markup=MAIN_REPLY_KEYBOARD)
    else:
        await message.answer("Кнопки меню доступны только в личном чате.")


_IMAGE_STYLE_LABELS = {
    "classic": "Классический",
    "parchment": "Пергамент и дерево",
    "custom": "Классический · свой фон",
}
_CUSTOM_GRADIENT_PRESETS = GRADIENT_PRESETS
_MANAGED_COMMANDS = {
    "card": "🃏 Карты",
    "meta": "📊 Мета",
    "arena": "🏟 Арена",
    "comps": "🎮 Поля сражений",
    "compare": "⚖️ Сравнение",
    "findwith": "🔎 Поиск колод",
}
_BACKGROUND_DIR = PROJECT_ROOT / "user_assets" / "backgrounds"
_LOGO_DIR = PROJECT_ROOT / "user_assets" / "logos"
_ADD_TO_CHAT_URL = (
    "https://t.me/manacostcard_bot"
    "?startgroup=settings&admin=manage_chat"
)
_PRIVATE_SETTINGS_URL = "https://t.me/manacostcard_bot?start=settings"
_MANAGE_CHAT_START_PREFIX = "manage_"


class SettingsState(StatesGroup):
    waiting_background_image = State()
    waiting_gradient = State()
    waiting_logo_image = State()
    waiting_mana_curve_image = State()
    waiting_design_name = State()


async def _show_settings_text(
    callback: types.CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup,
) -> types.Message:
    """Keep the settings wizard to one message across text/photo screens."""
    message = callback.message
    if message and getattr(message, "text", None) is not None:
        try:
            return await message.edit_text(text, reply_markup=reply_markup)
        except Exception:
            pass
    if message:
        try:
            await message.delete()
        except Exception:
            pass
        return await bot.send_message(
            message.chat.id,
            text,
            reply_markup=reply_markup,
        )
    return await bot.send_message(
        callback.from_user.id,
        text,
        reply_markup=reply_markup,
    )


async def _show_settings_photo(
    callback: types.CallbackQuery,
    image_bytes: bytes,
    *,
    filename: str,
    caption: str,
    reply_markup: InlineKeyboardMarkup,
) -> types.Message:
    """Show or replace a settings preview without leaving stale messages."""
    message = callback.message
    media = InputMediaPhoto(
        media=BufferedInputFile(image_bytes, filename=filename),
        caption=caption,
    )
    if message and getattr(message, "photo", None):
        try:
            return await message.edit_media(
                media,
                reply_markup=reply_markup,
            )
        except Exception:
            pass
    if message:
        chat_id = message.chat.id
        try:
            await message.delete()
        except Exception:
            pass
    else:
        chat_id = callback.from_user.id
    return await bot.send_photo(
        chat_id,
        BufferedInputFile(image_bytes, filename=filename),
        caption=caption,
        reply_markup=reply_markup,
    )


class ManagedChatCommandMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        chat = getattr(event, "chat", None)
        text = str(getattr(event, "text", "") or "").strip()
        if chat and getattr(chat, "type", None) != "private" and text.startswith("/"):
            command = text[1:].split(maxsplit=1)[0].split("@", 1)[0].lower()
            if command and not is_managed_chat_command_enabled(chat.id, command):
                await event.answer("Эта команда отключена администратором чата.")
                return None
        return await handler(event, data)


_flood_protection = TelegramFloodProtectionMiddleware()
router.message.outer_middleware(_flood_protection)
router.channel_post.outer_middleware(_flood_protection)
router.callback_query.outer_middleware(_flood_protection)
router.message.outer_middleware(ManagedChatCommandMiddleware())


def _image_style_for_user_id(user_id: int | None) -> str:
    if not user_id:
        return "classic"
    try:
        return get_user_image_style(int(user_id))
    except Exception as exc:
        print(f"[Deckview] image style lookup failed: {exc}")
        return "classic"


def _image_theme_for_user_id(user_id: int | None) -> dict:
    if not user_id:
        return {
            "style": "classic",
            "background": None,
            "cache_style": "classic:layout:auto-v7",
            "font": "auto",
            "text_size": "normal",
            "dust_display": "normal",
            "class_art": {"mode": "class", "path": None},
            "layout": {"normal": 0, "extended": 0, "highlander": 0},
            "mana_curve": {"mode": "chart", "path": None},
            "button_layout": "full",
            "personalization_revision": 0,
            "blur": 0,
        }
    try:
        settings = get_user_image_settings(int(user_id))
    except Exception as exc:
        print(f"[Deckview] image theme lookup failed: {exc}")
        settings = {"style": "classic", "revision": 0}
    style = str(settings.get("style") or "classic")
    image_font = normalize_font_key(settings.get("font"))
    image_text_size = normalize_title_size(settings.get("text_size"))
    dust_display = normalize_dust_display(settings.get("dust_display"))
    class_art_mode = normalize_class_art_mode(settings.get("class_art_mode"))
    custom_logo_path = str(settings.get("custom_logo_path") or "").strip()
    if class_art_mode == "logo" and not custom_logo_path:
        class_art_mode = "class"
    personalization_revision = int(
        settings.get("personalization_revision") or 0
    )
    image_blur = normalize_background_blur(settings.get("blur"))
    # Card-row customization was removed from the user interface. Always use
    # the automatic layout, including for accounts that stored old values.
    image_layout = {"normal": 0, "extended": 0, "highlander": 0}
    mana_curve_mode = normalize_mana_curve_mode(settings.get("mana_curve_mode"))
    mana_curve_path = str(settings.get("mana_curve_image_path") or "").strip()
    if mana_curve_mode == "image" and not mana_curve_path:
        mana_curve_mode = "chart"
    background = None
    cache_style = style
    if style == "custom":
        kind = settings.get("background_kind")
        value = settings.get("background_value")
        if kind and value:
            background = {
                "kind": kind,
                "value": value,
                "blur": image_blur if kind == "image" else 0,
            }
            cache_style = (
                f"custom:user:{int(user_id)}:{int(settings.get('revision') or 0)}"
            )
        else:
            style = "parchment"
            cache_style = "parchment"
    if image_font != "auto":
        cache_style = f"{cache_style}:font:{image_font}"
    if image_text_size != "normal":
        cache_style = f"{cache_style}:text:{image_text_size}"
    cache_style = (
        f"{cache_style}:prefs:{personalization_revision}:layout:auto-v7"
    )
    return {
        "style": style,
        "background": background,
        "cache_style": cache_style,
        "font": image_font,
        "text_size": image_text_size,
        "dust_display": dust_display,
        "class_art": {
            "mode": class_art_mode,
            "path": custom_logo_path or None,
        },
        "layout": image_layout,
        "mana_curve": {
            "mode": mana_curve_mode,
            "path": mana_curve_path or None,
        },
        "button_layout": "full",
        "personalization_revision": personalization_revision,
        "blur": image_blur,
    }


def _image_theme_for_context(user_id: int | None, chat_id: int | None) -> dict:
    sender_theme = _image_theme_for_user_id(user_id)
    if not chat_id:
        return sender_theme
    chat = get_managed_chat(int(chat_id))
    if not chat or not chat.get("is_active"):
        return sender_theme
    # Chat-level inheritance must be stable for every participant.  Resolve
    # "Как у меня" against the administrator who connected/configured the
    # chat, not against the author of the current deck-code message.
    owner_id = chat.get("added_by")
    user_theme = (
        _image_theme_for_user_id(int(owner_id))
        if owner_id is not None
        else sender_theme
    )
    style = str(chat.get("image_style") or "inherit")
    chat_font = str(chat.get("image_font") or "inherit")
    image_font = (
        user_theme["font"]
        if chat_font == "inherit"
        else normalize_font_key(chat_font)
    )
    chat_text_size = normalize_title_size(
        chat.get("image_text_size") or "inherit",
        allow_inherit=True,
    )
    image_text_size = (
        user_theme["text_size"]
        if chat_text_size == "inherit"
        else chat_text_size
    )
    chat_dust = str(chat.get("image_dust_display") or "inherit")
    dust_display = (
        user_theme["dust_display"]
        if chat_dust == "inherit"
        else normalize_dust_display(chat_dust)
    )
    chat_class_art = str(chat.get("class_art_mode") or "inherit")
    if chat_class_art == "inherit":
        class_art = dict(user_theme["class_art"])
    else:
        class_art = {
            "mode": normalize_class_art_mode(chat_class_art),
            "path": chat.get("custom_logo_path"),
        }
        if class_art["mode"] == "logo" and not class_art["path"]:
            class_art = {"mode": "class", "path": None}
    image_layout = {"normal": 0, "extended": 0, "highlander": 0}
    chat_curve_mode = normalize_mana_curve_mode(
        chat.get("mana_curve_mode") or "inherit",
        allow_inherit=True,
    )
    mana_curve = (
        dict(user_theme.get("mana_curve") or {"mode": "chart", "path": None})
        if chat_curve_mode == "inherit"
        else {
            "mode": chat_curve_mode,
            "path": chat.get("mana_curve_image_path"),
        }
    )
    if mana_curve["mode"] == "image" and not mana_curve.get("path"):
        mana_curve = {"mode": "chart", "path": None}
    chat_revision = int(chat.get("personalization_revision") or 0)
    cache_suffix = (
        f":chatprefs:{chat_revision}"
        f":userprefs:{user_theme['personalization_revision']}"
        f":font:{image_font}:text:{image_text_size}:dust:{dust_display}"
        f":art:{class_art['mode']}"
        f":curve:{mana_curve['mode']}"
    )
    common = {
        "font": image_font,
        "text_size": image_text_size,
        "dust_display": dust_display,
        "class_art": class_art,
        "layout": image_layout,
        "mana_curve": mana_curve,
        "button_layout": normalize_deck_button_layout(
            chat.get("deck_button_layout")
        ),
        "personalization_revision": user_theme[
            "personalization_revision"
        ],
        "chat_personalization_revision": chat_revision,
    }
    if style == "inherit":
        return {
            **user_theme,
            **common,
            "cache_style": user_theme["cache_style"] + cache_suffix,
        }
    if style == "custom":
        kind = chat.get("custom_background_kind")
        value = chat.get("custom_background_value")
        if not kind or not value:
            return {
                **user_theme,
                **common,
                "cache_style": user_theme["cache_style"] + cache_suffix,
            }
        return {
            "style": "custom",
            "background": {
                "kind": kind,
                "value": value,
                "blur": (
                    normalize_background_blur(
                        chat.get("custom_background_blur")
                    )
                    if kind == "image"
                    else 0
                ),
            },
            "cache_style": (
                f"custom:chat:{int(chat_id)}:"
                f"{int(chat.get('custom_background_revision') or 0)}"
                + cache_suffix
            ),
            **common,
            "blur": normalize_background_blur(
                chat.get("custom_background_blur")
            ),
        }
    return {
        "style": style,
        "background": None,
        "cache_style": style + cache_suffix,
        **common,
        "blur": 0,
    }


def _settings_home_text(user_id: int) -> str:
    settings = get_user_image_settings(user_id)
    label = _IMAGE_STYLE_LABELS.get(settings.get("style"), "Классический")
    background_labels = {
        "gradient": "градиент",
        "image": "своё изображение",
    }
    background = background_labels.get(
        settings.get("background_kind"),
        "не загружен",
    )
    chats_count = len(_managed_group_chats_for_user(user_id))
    curve_label = MANA_CURVE_OPTIONS[
        normalize_mana_curve_mode(settings.get("mana_curve_mode"))
    ]
    return (
        "⚙️ <b>Настройки</b>\n\n"
        f"🎨 Стиль: <b>{label}</b>\n"
        f"🖼 Фон: <b>{background}</b>\n"
        f"🔤 Шрифт: <b>{html.escape(font_label(settings.get('font')))}</b>\n"
        "↕️ Размер заголовка: "
        f"<b>{html.escape(title_size_label(settings.get('text_size')))}</b>\n"
        "💎 Стоимость пыли: "
        f"<b>{DUST_DISPLAY_OPTIONS[normalize_dust_display(settings.get('dust_display'))]}</b>\n"
        "🛡 Нижний арт: "
        f"<b>{CLASS_ART_OPTIONS[normalize_class_art_mode(settings.get('class_art_mode'))]}</b>\n"
        f"📊 Нижняя область: <b>{html.escape(curve_label)}</b>\n"
        f"🫧 Размытие: <b>{normalize_background_blur(settings.get('blur'))}%</b>\n"
        f"👥 Подключённые чаты: <b>{chats_count}</b>\n\n"
        "Выберите раздел:"
    )


def _settings_home_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎨 Оформление",
                    callback_data="settings_design",
                ),
                InlineKeyboardButton(
                    text="👥 Мои чаты",
                    callback_data="settings_chats",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🖼 Загрузить фон",
                    callback_data="settings_background:user:image",
                ),
                InlineKeyboardButton(
                    text="🔤 Выбрать шрифт",
                    callback_data="settings_fonts",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="↕️ Размер заголовка",
                    callback_data="settings_text_size_menu:user:0",
                ),
            ],
        ]
    )


def _settings_design_keyboard(
    settings: dict,
) -> InlineKeyboardMarkup:
    active_style = str(settings.get("style") or "classic")
    has_custom = bool(
        settings.get("background_kind")
        and settings.get("background_value")
    )
    custom_label = (
        f"{'✅ ' if active_style == 'custom' else ''}Свой фон"
        if has_custom
        else "Свой фон · нет"
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{'✅ ' if active_style == 'classic' else ''}Классика",
                    callback_data="settings_style:classic",
                ),
                InlineKeyboardButton(
                    text=f"{'✅ ' if active_style == 'parchment' else ''}Пергамент",
                    callback_data="settings_style:parchment",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=custom_label,
                    callback_data="settings_style:custom",
                ),
                InlineKeyboardButton(
                    text="🖼 Загрузить",
                    callback_data="settings_background:user:image",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🌈 Градиенты",
                    callback_data="settings_background:user:gradient",
                ),
                InlineKeyboardButton(
                    text="🔤 Шрифты",
                    callback_data="settings_fonts",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="↕️ Размер текста",
                    callback_data="settings_text_size_menu:user:0",
                ),
                InlineKeyboardButton(
                    text=(
                        "🫧 Blur"
                        f" · {normalize_background_blur(settings.get('blur'))}%"
                    ),
                    callback_data="settings_blur_menu:user:0",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="💎 Стоимость пыли",
                    callback_data="settings_dust",
                ),
                InlineKeyboardButton(
                    text="🛡 Арт или логотип",
                    callback_data="settings_class_art",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📊 Нижняя область",
                    callback_data="settings_mana_curve:user:0",
                ),
                InlineKeyboardButton(
                    text="🔘 Кнопки в чатах",
                    callback_data="settings_buttons_chats",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="💾 Сохранить дизайн",
                    callback_data="settings_design_save",
                ),
                InlineKeyboardButton(
                    text="📚 Мои дизайны",
                    callback_data="settings_designs",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="← Назад",
                    callback_data="settings_home",
                )
            ],
        ]
    )


def _settings_design_text(user_id: int) -> str:
    settings = get_user_image_settings(user_id)
    saved_count = len(get_user_image_designs(user_id))
    label = _IMAGE_STYLE_LABELS.get(settings.get("style"), "Классический")
    curve_label = MANA_CURVE_OPTIONS[
        normalize_mana_curve_mode(settings.get("mana_curve_mode"))
    ]
    return (
        "🎨 <b>Оформление картинок</b>\n\n"
        f"Активный стиль: <b>{label}</b>\n"
        f"Шрифт заголовка: <b>{html.escape(font_label(settings.get('font')))}</b>\n"
        "Размер заголовка: "
        f"<b>{html.escape(title_size_label(settings.get('text_size')))}</b>\n"
        "Стоимость пыли: "
        f"<b>{DUST_DISPLAY_OPTIONS[normalize_dust_display(settings.get('dust_display'))]}</b>\n"
        "Нижний арт: "
        f"<b>{CLASS_ART_OPTIONS[normalize_class_art_mode(settings.get('class_art_mode'))]}</b>\n"
        f"Манакривая: <b>{curve_label}</b>\n"
        f"Сохранённые дизайны: <b>{saved_count}</b>\n"
        f"Размытие фото: <b>{normalize_background_blur(settings.get('blur'))}%</b>\n"
        "\nСвой фон автоматически подгоняется под размер колоды. "
        "Выбор фото или градиента сразу активирует этот стиль."
    )


_PERSONALIZATION_PREVIEW_CODE = (
    "AAECAa0GDqn1BsP/BvKDB4OKB6iWB4KYB/ypB4CqB4SqB4utB+SyB+eyB4O/"
    "B8nHBwjwnwSg+wb3gQeFhgedrQeixAeyxQeW/AcAAA=="
)
_PERSONALIZATION_PREVIEW_NAME = "Контроль Жрец"
_PERSONALIZATION_PREVIEW_CACHE: dict[str, bytes] = {}
_PERSONALIZATION_PREVIEW_GENERATION: dict[tuple[str, int, int], int] = {}


def _next_personalization_preview(
    scope: str,
    target_id: int,
    viewer_id: int | None = None,
) -> tuple[tuple[str, int, int], int]:
    key = (
        str(scope),
        int(target_id),
        int(viewer_id if viewer_id is not None else target_id),
    )
    generation = _PERSONALIZATION_PREVIEW_GENERATION.get(key, 0) + 1
    _PERSONALIZATION_PREVIEW_GENERATION[key] = generation
    if len(_PERSONALIZATION_PREVIEW_GENERATION) > 2048:
        for old_key in list(_PERSONALIZATION_PREVIEW_GENERATION)[:256]:
            if old_key != key:
                _PERSONALIZATION_PREVIEW_GENERATION.pop(old_key, None)
    return key, generation


def _is_latest_personalization_preview(
    key: tuple[str, int, int],
    generation: int,
) -> bool:
    return _PERSONALIZATION_PREVIEW_GENERATION.get(key) == generation


async def _build_personalization_preview(
    user_id: int,
    chat_id: int | None = None,
) -> bytes:
    theme = (
        _image_theme_for_context(user_id, chat_id)
        if chat_id is not None
        else _image_theme_for_user_id(user_id)
    )
    cache_key = (
        f"chat:{chat_id}:{theme['cache_style']}"
        if chat_id is not None
        else f"user:{user_id}:{theme['cache_style']}"
    )
    cached = _PERSONALIZATION_PREVIEW_CACHE.get(cache_key)
    if cached is not None:
        return cached
    preview_layout = dict(theme["layout"])
    image, *_ = await create_picture(
        _PERSONALIZATION_PREVIEW_CODE,
        deck_name=_PERSONALIZATION_PREVIEW_NAME,
        image_style=theme["style"],
        image_background=theme["background"],
        image_font=theme["font"],
        image_text_size=theme["text_size"],
        image_dust_display=theme["dust_display"],
        image_class_art=theme["class_art"],
        image_layout=preview_layout,
        image_mana_curve=theme["mana_curve"],
    )
    output = BytesIO()
    image.convert("RGB").save(
        output,
        format="JPEG",
        quality=88,
        # The preview is sent through the local Telegram API. Disabling the
        # expensive second-pass optimizer cuts encoding latency substantially
        # for a negligible size increase and does not affect the final deck.
        optimize=False,
        progressive=False,
    )
    result = output.getvalue()
    if len(_PERSONALIZATION_PREVIEW_CACHE) >= 24:
        _PERSONALIZATION_PREVIEW_CACHE.pop(
            next(iter(_PERSONALIZATION_PREVIEW_CACHE))
        )
    _PERSONALIZATION_PREVIEW_CACHE[cache_key] = result
    return result


async def _show_personalization_preview(
    callback: types.CallbackQuery,
) -> types.Message:
    request_key, generation = _next_personalization_preview(
        "user",
        callback.from_user.id,
    )
    # Collapse a burst of clicks into the last selected setting.
    await asyncio.sleep(0.06)
    if not _is_latest_personalization_preview(request_key, generation):
        return callback.message
    try:
        preview = await _build_personalization_preview(callback.from_user.id)
        if not _is_latest_personalization_preview(request_key, generation):
            return callback.message
        settings = get_user_image_settings(callback.from_user.id)
        return await _show_settings_photo(
            callback,
            preview,
            filename="deckview-personalization-preview.jpg",
            caption=(
                _settings_design_text(callback.from_user.id)
                + "\n\n👁 Изменения показаны на примере «Контроль Жрец»."
            ),
            reply_markup=_settings_design_keyboard(settings),
        )
    except Exception as exc:
        if not _is_latest_personalization_preview(request_key, generation):
            return callback.message
        print(
            "[Deckview settings] preview failed: "
            f"{type(exc).__name__}: {str(exc)[:160]}"
        )
        settings = get_user_image_settings(callback.from_user.id)
        return await _show_settings_text(
            callback,
            _settings_design_text(callback.from_user.id),
            _settings_design_keyboard(settings),
        )


async def _show_chat_personalization_preview(
    callback: types.CallbackQuery,
    chat_id: int,
) -> types.Message:
    request_key, generation = _next_personalization_preview(
        "chat",
        chat_id,
        callback.from_user.id,
    )
    await asyncio.sleep(0.06)
    if not _is_latest_personalization_preview(request_key, generation):
        return callback.message
    chat = get_managed_chat(chat_id)
    if not chat:
        return await _show_settings_text(
            callback,
            "Чат больше не найден в настройках.",
            _settings_home_keyboard(),
        )
    try:
        preview = await _build_personalization_preview(
            callback.from_user.id,
            chat_id,
        )
        if not _is_latest_personalization_preview(request_key, generation):
            return callback.message
        return await _show_settings_photo(
            callback,
            preview,
            filename="deckview-chat-personalization-preview.jpg",
            caption=(
                _managed_chat_text(chat)
                + "\n\n👁 Так будут выглядеть колоды в этом чате."
            ),
            reply_markup=_managed_chat_keyboard(chat),
        )
    except Exception as exc:
        if not _is_latest_personalization_preview(request_key, generation):
            return callback.message
        print(
            "[Deckview settings] chat preview failed: "
            f"{type(exc).__name__}: {str(exc)[:160]}"
        )
        return await _show_settings_text(
            callback,
            _managed_chat_text(chat),
            _managed_chat_keyboard(chat),
        )


async def _answer_personalization_preview(
    message: types.Message,
    *,
    notice: str | None = None,
    chat_id: int | None = None,
) -> types.Message:
    """Send the live personalization example after a file/text upload."""
    preview = await _build_personalization_preview(
        message.from_user.id,
        chat_id,
    )
    if chat_id is None:
        settings = get_user_image_settings(message.from_user.id)
        caption = _settings_design_text(message.from_user.id)
        keyboard = _settings_design_keyboard(settings)
    else:
        settings = get_managed_chat(chat_id)
        caption = _managed_chat_text(settings)
        keyboard = _managed_chat_keyboard(settings)
    if notice:
        caption = f"{html.escape(notice)}\n\n{caption}"
    caption += "\n\n👁 Изменения показаны на примере «Контроль Жрец»."
    return await message.answer_photo(
        BufferedInputFile(
            preview,
            filename="deckview-personalization-preview.jpg",
        ),
        caption=caption,
        reply_markup=keyboard,
    )


def _gradient_keyboard(
    scope: str,
    target_id: int,
    active_value: str | None = None,
) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=f"{'✅ ' if value == active_value else ''}{label}",
            callback_data=f"settings_gradient:{scope}:{target_id}:{key}",
        )
        for key, (label, value) in _CUSTOM_GRADIENT_PRESETS.items()
    ]
    rows = [
        buttons[index : index + 2]
        for index in range(0, len(buttons), 2)
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text="← Назад",
                callback_data=(
                    f"settings_background_back:{scope}:{target_id}"
                ),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _blur_keyboard(
    scope: str,
    target_id: int,
    active_blur: int,
) -> InlineKeyboardMarkup:
    active = normalize_background_blur(active_blur)
    buttons = [
        InlineKeyboardButton(
            text=f"{'✅ ' if strength == active else ''}{strength}%",
            callback_data=f"settings_blur:{scope}:{target_id}:{strength}",
        )
        for strength in BLUR_STRENGTHS
    ]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            buttons[:2],
            buttons[2:],
            [
                InlineKeyboardButton(
                    text="← Назад",
                    callback_data=(
                        f"settings_chat:{target_id}"
                        if scope == "chat"
                        else "settings_design"
                    ),
                )
            ],
        ]
    )


def _dust_display_keyboard(active_display: str) -> InlineKeyboardMarkup:
    active = normalize_dust_display(active_display)
    buttons = [
        InlineKeyboardButton(
            text=f"{'✅ ' if active == key else ''}{label}",
            callback_data=f"settings_dust:{key}",
        )
        for key, label in DUST_DISPLAY_OPTIONS.items()
    ]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            buttons[:2],
            buttons[2:],
            [InlineKeyboardButton(text="← Назад", callback_data="settings_design")],
        ]
    )


def _class_art_keyboard(settings: dict) -> InlineKeyboardMarkup:
    active = normalize_class_art_mode(settings.get("class_art_mode"))
    has_logo = bool(settings.get("custom_logo_path"))
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{'✅ ' if active == 'class' else ''}Арт класса",
                    callback_data="settings_class_art_mode:class",
                ),
                InlineKeyboardButton(
                    text=(
                        f"{'✅ ' if active == 'logo' else ''}Свой логотип"
                        if has_logo
                        else "Свой логотип · нет"
                    ),
                    callback_data="settings_class_art_mode:logo",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⬆️ Загрузить или заменить логотип",
                    callback_data="settings_logo_upload",
                )
            ],
            [InlineKeyboardButton(text="← Назад", callback_data="settings_design")],
        ]
    )


def _saved_designs_keyboard(
    designs: list[dict],
    *,
    confirm_delete_id: int | None = None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for design in designs:
        design_id = int(design["id"])
        name = str(design.get("name") or f"Дизайн {design_id}")[:32]
        if confirm_delete_id == design_id:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"Удалить «{name[:18]}»?",
                        callback_data=f"settings_design_delete_confirm:{design_id}",
                    ),
                    InlineKeyboardButton(
                        text="Отмена",
                        callback_data="settings_designs",
                    ),
                ]
            )
            continue
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🎨 {name}",
                    callback_data=f"settings_design_apply:{design_id}",
                ),
                InlineKeyboardButton(
                    text="🗑",
                    callback_data=f"settings_design_delete:{design_id}",
                ),
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="💾 Сохранить текущий",
                callback_data="settings_design_save",
            ),
            InlineKeyboardButton(
                text="← Назад",
                callback_data="settings_design",
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _saved_designs_text(designs: list[dict]) -> str:
    if not designs:
        return (
            "📚 <b>Мои дизайны</b>\n\n"
            "Сохранённых вариантов пока нет. Настройте оформление и "
            "сохраните его под своим названием."
        )
    return (
        "📚 <b>Мои дизайны</b>\n\n"
        "Нажмите на название, чтобы применить дизайн целиком. "
        "Сохраняются фон, стиль, шрифт, размер текста, размытие, "
        "стоимость пыли и нижний арт."
    )


@router.callback_query(F.data == "settings_design_save")
async def cb_settings_design_save(
    callback: types.CallbackQuery,
    state: FSMContext,
):
    await state.set_state(SettingsState.waiting_design_name)
    await state.set_data(
        {"scope": "user", "target_id": callback.from_user.id}
    )
    await _show_settings_text(
        callback,
        "💾 <b>Сохранение дизайна</b>\n\n"
        "Отправьте название длиной до 32 символов, например "
        "<code>Тёмная классика</code> или <code>Фон сайта</code>.\n\n"
        "Если такое название уже существует, его настройки обновятся.",
        InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(
                    text="← Отмена",
                    callback_data="settings_design",
                )
            ]]
        ),
    )
    await callback.answer()


@router.message(SettingsState.waiting_design_name, F.text)
async def msg_settings_design_name(
    message: types.Message,
    state: FSMContext,
):
    name = " ".join(str(message.text or "").strip().split())
    if not name:
        await message.answer("Введите непустое название дизайна.")
        return
    if len(name) > 32:
        await message.answer(
            "Название слишком длинное. Используйте не больше 32 символов."
        )
        return
    try:
        state_data = await state.get_data()
        scope = str(state_data.get("scope") or "user")
        target_id = int(
            state_data.get("target_id") or message.from_user.id
        )
        if scope == "chat":
            if not await _can_manage_chat(message.from_user.id, target_id):
                await state.clear()
                await message.answer("Нужны права администратора.")
                return
            design = save_managed_chat_image_design(
                message.from_user.id,
                target_id,
                name,
            )
        else:
            design = save_user_image_design(message.from_user.id, name)
    except ValueError:
        await message.answer("Не удалось сохранить: проверьте название.")
        return
    await state.clear()
    await _answer_personalization_preview(
        message,
        notice=f"✅ Дизайн «{design['name']}» сохранён.",
        chat_id=target_id if scope == "chat" else None,
    )


@router.message(SettingsState.waiting_design_name)
async def msg_settings_design_name_invalid(message: types.Message):
    await message.answer("Отправьте название дизайна обычным текстом.")


@router.callback_query(F.data == "settings_designs")
async def cb_settings_designs(callback: types.CallbackQuery):
    designs = get_user_image_designs(callback.from_user.id)
    await _show_settings_text(
        callback,
        _saved_designs_text(designs),
        _saved_designs_keyboard(designs),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("settings_design_apply:"))
async def cb_settings_design_apply(callback: types.CallbackQuery):
    raw_id = callback.data.removeprefix("settings_design_apply:")
    if not raw_id.isdigit():
        await callback.answer("Некорректный дизайн.", show_alert=True)
        return
    applied = apply_user_image_design(
        callback.from_user.id,
        int(raw_id),
    )
    if not applied:
        await callback.answer("Дизайн не найден.", show_alert=True)
        return
    await callback.answer("Дизайн применён")
    await _show_personalization_preview(callback)


@router.callback_query(F.data.startswith("settings_design_delete:"))
async def cb_settings_design_delete(callback: types.CallbackQuery):
    raw_id = callback.data.removeprefix("settings_design_delete:")
    if not raw_id.isdigit():
        await callback.answer("Некорректный дизайн.", show_alert=True)
        return
    designs = get_user_image_designs(callback.from_user.id)
    if int(raw_id) not in {int(item["id"]) for item in designs}:
        await callback.answer("Дизайн не найден.", show_alert=True)
        return
    await _show_settings_text(
        callback,
        _saved_designs_text(designs),
        _saved_designs_keyboard(
            designs,
            confirm_delete_id=int(raw_id),
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("settings_design_delete_confirm:"))
async def cb_settings_design_delete_confirm(callback: types.CallbackQuery):
    raw_id = callback.data.removeprefix(
        "settings_design_delete_confirm:"
    )
    if not raw_id.isdigit() or not delete_user_image_design(
        callback.from_user.id,
        int(raw_id),
    ):
        await callback.answer("Дизайн не найден.", show_alert=True)
        return
    designs = get_user_image_designs(callback.from_user.id)
    await _show_settings_text(
        callback,
        "✅ Дизайн удалён.\n\n" + _saved_designs_text(designs),
        _saved_designs_keyboard(designs),
    )
    await callback.answer("Удалено")


@router.callback_query(F.data == "settings_dust")
async def cb_settings_dust_menu(callback: types.CallbackQuery):
    settings = get_user_image_settings(callback.from_user.id)
    await _show_settings_text(
        callback,
        "💎 <b>Стоимость колоды</b>\n\n"
        "Обычная — текущий размер. Крупная — заметнее. "
        "Скрыта — полностью убирает число и значок пыли.",
        _dust_display_keyboard(settings.get("dust_display")),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("settings_dust:"))
async def cb_settings_dust(callback: types.CallbackQuery):
    requested = callback.data.removeprefix("settings_dust:")
    selected = set_user_dust_display(callback.from_user.id, requested)
    await callback.answer(
        f"Стоимость пыли: {DUST_DISPLAY_OPTIONS[selected]}"
    )
    await _show_personalization_preview(callback)


@router.callback_query(F.data == "settings_class_art")
async def cb_settings_class_art_menu(callback: types.CallbackQuery):
    settings = get_user_image_settings(callback.from_user.id)
    await _show_settings_text(
        callback,
        "🛡 <b>Нижний арт картинки</b>\n\n"
        "Оставьте персонажа выбранного класса или загрузите свой PNG, "
        "JPEG либо WEBP — например, логотип сайта. Фон вокруг логотипа "
        "будет удалён, если файл уже содержит прозрачность.",
        _class_art_keyboard(settings),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("settings_class_art_mode:"))
async def cb_settings_class_art_mode(callback: types.CallbackQuery):
    requested = callback.data.removeprefix("settings_class_art_mode:")
    try:
        selected = set_user_class_art_mode(callback.from_user.id, requested)
    except ValueError:
        await callback.answer(
            "Сначала загрузите свой логотип.",
            show_alert=True,
        )
        return
    await callback.answer(CLASS_ART_OPTIONS[selected])
    await _show_personalization_preview(callback)


@router.callback_query(F.data == "settings_logo_upload")
async def cb_settings_logo_upload(
    callback: types.CallbackQuery,
    state: FSMContext,
):
    await state.set_state(SettingsState.waiting_logo_image)
    await state.set_data(
        {"scope": "user", "target_id": callback.from_user.id}
    )
    await _show_settings_text(
        callback,
        "⬆️ <b>Загрузка логотипа</b>\n\n"
        "Отправьте логотип фотографией или файлом PNG, JPEG, WEBP "
        "до 20 МБ. Лучше всего выглядит PNG с прозрачным фоном.",
        InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(
                    text="← Отмена",
                    callback_data="settings_design",
                )
            ]]
        ),
    )
    await callback.answer()


def _save_logo_image_sync(data: bytes, user_id: int) -> str:
    if len(data) > 20 * 1024 * 1024:
        raise ValueError("Файл больше 20 МБ")
    try:
        with Image.open(BytesIO(data)) as source:
            source.load()
            image = source.convert("RGBA")
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("Файл не похож на изображение") from exc
    if image.width < 64 or image.height < 64:
        raise ValueError("Логотип слишком маленький — минимум 64×64")
    alpha_box = image.getchannel("A").getbbox()
    if alpha_box:
        image = image.crop(alpha_box)
    image.thumbnail((1600, 1600), getattr(Image, "Resampling", Image).LANCZOS)
    _LOGO_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"user_{int(user_id)}_{uuid.uuid4().hex[:12]}.png"
    path = _LOGO_DIR / filename
    image.save(path, format="PNG", optimize=True)
    return path.relative_to(PROJECT_ROOT).as_posix()


@router.message(SettingsState.waiting_logo_image, F.photo)
@router.message(SettingsState.waiting_logo_image, F.document)
async def msg_settings_logo_image(message: types.Message, state: FSMContext):
    state_data = await state.get_data()
    scope = str(state_data.get("scope") or "user")
    target_id = int(
        state_data.get("target_id") or message.from_user.id
    )
    if scope == "chat" and not await _can_manage_chat(
        message.from_user.id,
        target_id,
    ):
        await state.clear()
        await message.answer("Нужны права администратора этого чата.")
        return
    file_object = message.photo[-1] if message.photo else message.document
    if not file_object:
        await message.answer("Пришлите логотип фотографией или изображением-файлом.")
        return
    if int(getattr(file_object, "file_size", 0) or 0) > 20 * 1024 * 1024:
        await message.answer("Файл больше 20 МБ. Выберите изображение поменьше.")
        return
    buffer = BytesIO()
    try:
        await asyncio.wait_for(
            bot.download(file_object, destination=buffer),
            timeout=45,
        )
        path = await asyncio.to_thread(
            _save_logo_image_sync,
            buffer.getvalue(),
            message.from_user.id,
        )
        if scope == "chat":
            set_managed_chat_custom_logo(target_id, path)
        else:
            set_user_custom_logo(message.from_user.id, path)
    except ValueError as exc:
        await message.answer(f"Не удалось принять логотип: {html.escape(str(exc))}")
        return
    except Exception as exc:
        print(
            "[Deckview] logo upload failed: "
            f"{type(exc).__name__}: {str(exc)[:160]}"
        )
        await message.answer(
            "Не удалось скачать или сохранить логотип. Попробуйте отправить "
            "его как обычную фотографию либо PNG-файл."
        )
        return
    await state.clear()
    await _answer_personalization_preview(
        message,
        notice="✅ Логотип загружен и включён.",
        chat_id=target_id if scope == "chat" else None,
    )


@router.message(SettingsState.waiting_logo_image)
async def msg_settings_logo_invalid(message: types.Message):
    await message.answer(
        "Сейчас я жду изображение логотипа: фотографию или файл PNG, "
        "JPEG либо WEBP до 20 МБ."
    )


@router.message(SettingsState.waiting_mana_curve_image, F.photo)
@router.message(SettingsState.waiting_mana_curve_image, F.document)
async def msg_settings_mana_curve_image(
    message: types.Message, state: FSMContext
):
    state_data = await state.get_data()
    scope = str(state_data.get("scope") or "user")
    target_id = int(state_data.get("target_id") or message.from_user.id)
    if scope == "chat" and not await _can_manage_chat(message.from_user.id, target_id):
        await state.clear()
        await message.answer("Нужны права администратора этого чата.")
        return
    file_object = message.photo[-1] if message.photo else message.document
    if int(getattr(file_object, "file_size", 0) or 0) > 20 * 1024 * 1024:
        await message.answer("Файл больше 20 МБ. Выберите изображение поменьше.")
        return
    buffer = BytesIO()
    try:
        await asyncio.wait_for(
            bot.download(file_object, destination=buffer),
            timeout=45,
        )
        path = await asyncio.to_thread(
            _save_logo_image_sync,
            buffer.getvalue(),
            message.from_user.id,
        )
        if scope == "chat":
            set_managed_chat_mana_curve_image(target_id, path)
        else:
            set_user_mana_curve_image(message.from_user.id, path)
    except ValueError as exc:
        await message.answer(f"Не удалось принять картинку: {html.escape(str(exc))}")
        return
    except Exception as exc:
        print(
            "[Deckview] mana curve image upload failed: "
            f"{type(exc).__name__}: {str(exc)[:160]}"
        )
        await message.answer(
            "Не удалось скачать или сохранить картинку. Отправьте её как "
            "обычную фотографию либо PNG-файл."
        )
        return
    await state.clear()
    await _answer_personalization_preview(
        message,
        notice="✅ Картинка загружена и поставлена вместо манакривой.",
        chat_id=target_id if scope == "chat" else None,
    )


@router.message(SettingsState.waiting_mana_curve_image)
async def msg_settings_mana_curve_image_invalid(message: types.Message):
    await message.answer(
        "Сейчас я жду изображение для левой области: фотографию или файл "
        "PNG, JPEG либо WEBP до 20 МБ."
    )


async def _show_settings_scope(
    callback: types.CallbackQuery,
    scope: str,
    target_id: int,
) -> None:
    if scope == "chat":
        chat = get_managed_chat(target_id)
        if not chat:
            await _show_settings_text(
                callback,
                "Чат больше не найден в настройках.",
                _settings_home_keyboard(),
            )
            return
        await _show_chat_personalization_preview(callback, target_id)
        return
    await _show_personalization_preview(callback)


@router.message(Command("settings"))
async def cmd_settings(message: types.Message):
    user = message.from_user
    if not user:
        return
    ensure_bot_user(user.id, username=user.username, first_name=user.first_name)
    if message.chat.type != "private":
        await _connect_group_settings(message)
        return
    await message.answer(
        _settings_home_text(user.id),
        reply_markup=_settings_home_keyboard(),
    )


@router.message(F.text == "⚙️ Настройки")
async def msg_settings_button(message: types.Message):
    await cmd_settings(message)


@router.callback_query(F.data == "settings_home")
async def cb_settings_home(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await _show_settings_text(
        callback,
        _settings_home_text(callback.from_user.id),
        _settings_home_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "settings_design")
async def cb_settings_design(
    callback: types.CallbackQuery,
    state: FSMContext,
):
    await state.clear()
    await callback.answer("Обновляю пример…")
    await _show_personalization_preview(callback)


def _font_settings_keyboard(active_font: str) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=f"{'✅ ' if active_font == 'auto' else ''}Авто",
            callback_data="settings_font:auto",
        ),
        *[
        InlineKeyboardButton(
            text=f"{'✅ ' if active_font == key else ''}{option['label']}",
            callback_data=f"settings_font:{key}",
        )
        for key, option in FONT_OPTIONS.items()
        ],
    ]
    rows = [buttons[index : index + 2] for index in range(0, len(buttons), 2)]
    rows.append(
        [InlineKeyboardButton(text="← Назад", callback_data="settings_fonts_back")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _font_settings_caption(active_font: str) -> str:
    return (
        "🔤 <b>Шрифт заголовка колоды</b>\n\n"
        f"Выбран: <b>{html.escape(font_label(active_font))}</b>\n"
        "На картинке показан реальный вид всех доступных шрифтов."
    )


@router.callback_query(F.data == "settings_fonts")
async def cb_settings_fonts(callback: types.CallbackQuery):
    active_font = get_user_image_settings(callback.from_user.id).get("font") or "auto"
    preview = await asyncio.to_thread(build_font_preview, active_font)
    await _show_settings_photo(
        callback,
        preview,
        filename="deckview-fonts.jpg",
        caption=_font_settings_caption(active_font),
        reply_markup=_font_settings_keyboard(active_font),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("settings_font:"))
async def cb_settings_font(callback: types.CallbackQuery):
    requested = normalize_font_key(callback.data.removeprefix("settings_font:"))
    selected = set_user_image_font(callback.from_user.id, requested)
    await callback.answer(f"Шрифт: {font_label(selected)}")
    await _show_personalization_preview(callback)


@router.callback_query(F.data == "settings_fonts_back")
async def cb_settings_fonts_back(callback: types.CallbackQuery):
    await callback.answer()
    await _show_personalization_preview(callback)


def _text_size_keyboard(
    scope: str,
    target_id: int,
    active_size: str,
) -> InlineKeyboardMarkup:
    allow_inherit = scope == "chat"
    active = normalize_title_size(
        active_size,
        allow_inherit=allow_inherit,
    )
    options = list(TITLE_SIZE_OPTIONS.items())
    buttons = [
        InlineKeyboardButton(
            text=f"{'✅ ' if active == key else ''}{option['label']}",
            callback_data=f"settings_text_size:{scope}:{target_id}:{key}",
        )
        for key, option in options
    ]
    if allow_inherit:
        buttons.insert(
            0,
            InlineKeyboardButton(
                text=f"{'✅ ' if active == 'inherit' else ''}Как у меня",
                callback_data=(
                    f"settings_text_size:{scope}:{target_id}:inherit"
                ),
            ),
        )
    rows = [buttons[index : index + 2] for index in range(0, len(buttons), 2)]
    rows.append(
        [
            InlineKeyboardButton(
                text="← Назад",
                callback_data=(
                    f"settings_chat:{target_id}"
                    if scope == "chat"
                    else "settings_design"
                ),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _mana_curve_keyboard(scope: str, target_id: int, settings: dict):
    allow_inherit = scope == "chat"
    active = normalize_mana_curve_mode(
        settings.get("mana_curve_mode") or ("inherit" if allow_inherit else "chart"),
        allow_inherit=allow_inherit,
    )
    options = [("inherit", "Как у меня")] if allow_inherit else []
    options.extend((key, label) for key, label in MANA_CURVE_OPTIONS.items())
    buttons = [InlineKeyboardButton(
        text=f"{'✅ ' if active == key else ''}{label}",
        callback_data=f"settings_mana_curve_set:{scope}:{target_id}:{key}",
    ) for key, label in options]
    rows = [buttons[index:index + 2] for index in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton(
        text="⬆️ Загрузить картинку",
        callback_data=f"settings_mana_curve_upload:{scope}:{target_id}",
    )])
    rows.append([InlineKeyboardButton(
        text="← Назад",
        callback_data=(
            f"settings_chat:{target_id}" if scope == "chat" else "settings_design"
        ),
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("settings_mana_curve:"))
async def cb_settings_mana_curve_menu(callback: types.CallbackQuery):
    _, scope, raw_target = callback.data.split(":", 2)
    target_id = callback.from_user.id if scope == "user" else int(raw_target)
    if scope == "chat" and not await _can_manage_chat(callback.from_user.id, target_id):
        await callback.answer("Нужны права администратора.", show_alert=True)
        return
    settings = (
        get_managed_chat(target_id)
        if scope == "chat"
        else get_user_image_settings(target_id)
    ) or {}
    await _show_settings_text(
        callback,
        "📊 <b>Манакривая и левая область</b>\n\n"
        "Можно оставить диаграмму, полностью скрыть её или поставить свою "
        "картинку. Загруженное изображение вписывается по центру без искажений.",
        _mana_curve_keyboard(scope, target_id, settings),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("settings_mana_curve_set:"))
async def cb_settings_mana_curve_set(callback: types.CallbackQuery):
    _, scope, raw_target, requested = callback.data.split(":", 3)
    target_id = callback.from_user.id if scope == "user" else int(raw_target)
    if scope == "chat" and not await _can_manage_chat(callback.from_user.id, target_id):
        await callback.answer("Нужны права администратора.", show_alert=True)
        return
    try:
        selected = (
            set_managed_chat_mana_curve_mode(target_id, requested)
            if scope == "chat"
            else set_user_mana_curve_mode(target_id, requested)
        )
    except ValueError:
        await callback.answer("Сначала загрузите картинку.", show_alert=True)
        return
    label = "Как у меня" if selected == "inherit" else MANA_CURVE_OPTIONS[selected]
    await callback.answer(label)
    if scope == "chat":
        await _show_chat_personalization_preview(callback, target_id)
    else:
        await _show_personalization_preview(callback)


@router.callback_query(F.data.startswith("settings_mana_curve_upload:"))
async def cb_settings_mana_curve_upload(
    callback: types.CallbackQuery, state: FSMContext
):
    _, scope, raw_target = callback.data.split(":", 2)
    target_id = callback.from_user.id if scope == "user" else int(raw_target)
    if scope == "chat" and not await _can_manage_chat(callback.from_user.id, target_id):
        await callback.answer("Нужны права администратора.", show_alert=True)
        return
    await state.set_state(SettingsState.waiting_mana_curve_image)
    await state.set_data({"scope": scope, "target_id": target_id})
    await _show_settings_text(
        callback,
        "⬆️ <b>Картинка вместо манакривой</b>\n\n"
        "Отправьте PNG, JPEG или WEBP до 20 МБ. Лучше использовать PNG с "
        "прозрачностью — бот сам обрежет поля и расположит изображение по центру.",
        InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
            text="← Отмена",
            callback_data=(
                f"settings_chat:{target_id}" if scope == "chat" else "settings_design"
            ),
        )]]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("settings_text_size_menu:"))
async def cb_settings_text_size_menu(callback: types.CallbackQuery):
    _, scope, raw_target = callback.data.split(":", 2)
    target_id = (
        callback.from_user.id
        if scope == "user" and raw_target == "0"
        else int(raw_target)
    )
    if scope == "chat" and not await _can_manage_chat(
        callback.from_user.id,
        target_id,
    ):
        await callback.answer("Нужны права администратора.", show_alert=True)
        return
    settings = (
        get_managed_chat(target_id)
        if scope == "chat"
        else get_user_image_settings(target_id)
    ) or {}
    active = settings.get(
        "image_text_size" if scope == "chat" else "text_size",
        "inherit" if scope == "chat" else "normal",
    ) or ("inherit" if scope == "chat" else "normal")
    await _show_settings_text(
        callback,
        "↕️ <b>Размер заголовка колоды</b>\n\n"
        "Настройка меняет только название колоды над картами. "
        "Длинное название при необходимости автоматически уменьшается.",
        _text_size_keyboard(scope, target_id, str(active)),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("settings_text_size:"))
async def cb_settings_text_size(callback: types.CallbackQuery):
    _, scope, raw_target, requested = callback.data.split(":", 3)
    target_id = (
        callback.from_user.id
        if scope == "user" and raw_target == "0"
        else int(raw_target)
    )
    if scope == "chat" and not await _can_manage_chat(
        callback.from_user.id,
        target_id,
    ):
        await callback.answer("Нужны права администратора.", show_alert=True)
        return
    if scope == "chat":
        selected = set_managed_chat_image_text_size(target_id, requested)
        await callback.answer(
            f"Размер: {title_size_label(selected, allow_inherit=True)}"
        )
        await _show_chat_personalization_preview(callback, target_id)
    else:
        selected = set_user_image_text_size(target_id, requested)
        await callback.answer(f"Размер: {title_size_label(selected)}")
        await _show_personalization_preview(callback)


@router.callback_query(F.data.startswith("settings_style:"))
async def cb_settings_style(callback: types.CallbackQuery):
    requested = callback.data.removeprefix("settings_style:").strip()
    if requested not in _IMAGE_STYLE_LABELS:
        await callback.answer("Неизвестный стиль.", show_alert=True)
        return
    if requested == "custom":
        settings = get_user_image_settings(callback.from_user.id)
        if not settings.get("background_kind") or not settings.get("background_value"):
            await callback.answer(
                "Сначала загрузите фон или выберите градиент.",
                show_alert=True,
            )
            return
    style = set_user_image_style(callback.from_user.id, requested)
    await callback.answer(f"Выбран стиль: {_IMAGE_STYLE_LABELS[style]}")
    await _show_personalization_preview(callback)


@router.callback_query(F.data.startswith("settings_background:"))
async def cb_settings_background(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    if len(parts) not in {3, 4}:
        await callback.answer("Некорректная настройка.", show_alert=True)
        return
    _, scope, kind, *target = parts
    target_id = int(target[0]) if target else callback.from_user.id
    if scope == "chat" and not await _can_manage_chat(callback.from_user.id, target_id):
        await callback.answer("Нужны права администратора.", show_alert=True)
        return
    await state.set_data({"scope": scope, "target_id": target_id})
    if kind == "image":
        await state.set_state(SettingsState.waiting_background_image)
        prompt = await _show_settings_text(
            callback,
            "🖼 <b>Загрузка своего фона</b>\n\n"
            "Отправьте фотографию или файл JPEG, PNG, WEBP до 20 МБ.\n\n"
            "Бот проверит изображение, подгонит его под колоду и сразу "
            "активирует. После загрузки можно выбрать силу размытия.",
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="← Отмена",
                            callback_data=(
                                f"settings_background_back:{scope}:{target_id}"
                            ),
                        )
                    ]
                ]
            ),
        )
        await state.update_data(
            prompt_chat_id=getattr(getattr(prompt, "chat", None), "id", None),
            prompt_message_id=getattr(prompt, "message_id", None),
        )
    else:
        await state.set_state(SettingsState.waiting_gradient)
        current = (
            get_managed_chat(target_id)
            if scope == "chat"
            else get_user_image_settings(target_id)
        ) or {}
        active_value = (
            str(current.get("custom_background_value") or "")
            if scope == "chat"
            else str(current.get("background_value") or "")
        )
        preview = await asyncio.to_thread(
            build_gradient_preview,
            active_value,
        )
        prompt = await _show_settings_photo(
            callback,
            preview,
            filename="deckview-gradients.jpg",
            caption=(
                "🌈 <b>Выберите фон или градиент</b>\n\n"
                "На картинке показан реальный вид всех готовых расцветок. "
                "Можно также отправить два своих цвета сообщением: "
                "<code>#1E163D #A15BB4</code>"
            ),
            reply_markup=_gradient_keyboard(scope, target_id, active_value),
        )
        await state.update_data(
            prompt_chat_id=getattr(getattr(prompt, "chat", None), "id", None),
            prompt_message_id=getattr(prompt, "message_id", None),
        )
    await callback.answer()


def _save_background_image_sync(data: bytes, scope: str, target_id: int) -> str:
    if len(data) > 20 * 1024 * 1024:
        raise ValueError("Файл больше 20 МБ")
    try:
        with Image.open(BytesIO(data)) as source:
            source.load()
            image = source.convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("Файл не похож на изображение") from exc
    if image.width < 320 or image.height < 320:
        raise ValueError("Изображение слишком маленькое — минимум 320×320")
    image.thumbnail((2400, 2400), getattr(Image, "Resampling", Image).LANCZOS)
    _BACKGROUND_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{scope}_{int(target_id)}_{uuid.uuid4().hex[:12]}.jpg"
    path = _BACKGROUND_DIR / filename
    image.save(path, format="JPEG", quality=91, optimize=True, progressive=True)
    return path.relative_to(PROJECT_ROOT).as_posix()


def _apply_custom_background(scope: str, target_id: int, kind: str, value: str):
    if scope == "chat":
        return set_managed_chat_custom_background(target_id, kind, value)
    return set_user_custom_background(target_id, kind, value)


@router.message(SettingsState.waiting_background_image, F.photo)
@router.message(SettingsState.waiting_background_image, F.document)
async def msg_settings_background_image(message: types.Message, state: FSMContext):
    state_data = await state.get_data()
    scope = str(state_data.get("scope") or "user")
    target_id = int(state_data.get("target_id") or message.from_user.id)
    if scope == "chat" and not await _can_manage_chat(message.from_user.id, target_id):
        await state.clear()
        await message.answer("Нужны права администратора этого чата.")
        return
    file_object = message.photo[-1] if message.photo else message.document
    if not file_object:
        await message.answer("Пришлите фотографию или изображение файлом.")
        return
    file_size = int(getattr(file_object, "file_size", 0) or 0)
    if file_size > 20 * 1024 * 1024:
        await message.answer("Файл больше 20 МБ. Выберите изображение поменьше.")
        return
    buffer = BytesIO()
    try:
        await asyncio.wait_for(
            bot.download(file_object, destination=buffer),
            timeout=45,
        )
    except Exception as exc:
        print(
            "[Deckview] Не удалось скачать пользовательский фон из Telegram: "
            f"{type(exc).__name__}, errno={getattr(exc, 'errno', None)}"
        )
        await message.answer(
            "Не удалось скачать файл из Telegram. Отправьте изображение "
            "как обычную фотографию или попробуйте ещё раз."
        )
        return
    try:
        value = await asyncio.to_thread(
            _save_background_image_sync,
            buffer.getvalue(),
            scope,
            target_id,
        )
        settings = _apply_custom_background(scope, target_id, "image", value)
    except ValueError as exc:
        await message.answer(f"Не удалось принять фон: {html.escape(str(exc))}")
        return
    except Exception:
        await message.answer(
            "Не удалось сохранить фон. Настройки не изменены — "
            "попробуйте другое изображение."
        )
        return
    prompt_chat_id = state_data.get("prompt_chat_id")
    prompt_message_id = state_data.get("prompt_message_id")
    if prompt_chat_id and prompt_message_id:
        try:
            await bot.delete_message(
                int(prompt_chat_id),
                int(prompt_message_id),
            )
        except Exception:
            pass
    await state.clear()
    active_blur = normalize_background_blur(
        (settings or {}).get(
            "custom_background_blur" if scope == "chat" else "blur",
            0,
        )
    )
    if scope == "user":
        await _answer_personalization_preview(
            message,
            notice="✅ Фон загружен и активирован.",
        )
    else:
        await _answer_personalization_preview(
            message,
            notice="✅ Фон чата загружен и активирован.",
            chat_id=target_id,
        )


@router.message(SettingsState.waiting_background_image)
async def msg_settings_background_invalid(message: types.Message):
    await message.answer(
        "Сейчас я жду изображение. Отправьте его как фотографию "
        "или файл JPEG, PNG либо WEBP до 20 МБ."
    )


@router.message(SettingsState.waiting_gradient, F.text)
async def msg_settings_gradient(message: types.Message, state: FSMContext):
    match = re.findall(r"#[0-9a-fA-F]{6}", message.text or "")
    if len(match) != 2:
        await message.answer(
            "Нужно два цвета в формате <code>#RRGGBB #RRGGBB</code>."
        )
        return
    state_data = await state.get_data()
    scope = str(state_data.get("scope") or "user")
    target_id = int(state_data.get("target_id") or message.from_user.id)
    if scope == "chat" and not await _can_manage_chat(message.from_user.id, target_id):
        await state.clear()
        await message.answer("Нужны права администратора этого чата.")
        return
    value = normalize_gradient(",".join(match))
    settings = _apply_custom_background(scope, target_id, "gradient", value)
    prompt_chat_id = state_data.get("prompt_chat_id")
    prompt_message_id = state_data.get("prompt_message_id")
    if prompt_chat_id and prompt_message_id:
        try:
            await bot.delete_message(
                int(prompt_chat_id),
                int(prompt_message_id),
            )
        except Exception:
            pass
    await state.clear()
    if scope == "user":
        await _answer_personalization_preview(
            message,
            notice="✅ Градиент сохранён и активирован.",
        )
    else:
        await _answer_personalization_preview(
            message,
            notice="✅ Градиент чата сохранён и активирован.",
            chat_id=target_id,
        )


@router.message(SettingsState.waiting_gradient)
async def msg_settings_gradient_invalid(message: types.Message):
    await message.answer(
        "Отправьте два цвета текстом, например "
        "<code>#1E163D #A15BB4</code>, или выберите готовый вариант кнопкой."
    )


@router.callback_query(F.data.startswith("settings_gradient:"))
async def cb_settings_gradient(callback: types.CallbackQuery, state: FSMContext):
    _, scope, raw_target, preset = callback.data.split(":", 3)
    target_id = int(raw_target)
    if preset not in _CUSTOM_GRADIENT_PRESETS:
        await callback.answer("Неизвестный градиент.", show_alert=True)
        return
    if scope == "chat" and not await _can_manage_chat(callback.from_user.id, target_id):
        await callback.answer("Нужны права администратора.", show_alert=True)
        return
    value = _CUSTOM_GRADIENT_PRESETS[preset][1]
    _apply_custom_background(scope, target_id, "gradient", value)
    await state.clear()
    if scope == "user":
        await callback.answer("Градиент применён")
        await _show_personalization_preview(callback)
        return
    await callback.answer("Градиент применён")
    await _show_chat_personalization_preview(callback, target_id)


@router.callback_query(F.data.startswith("settings_background_back:"))
async def cb_settings_background_back(
    callback: types.CallbackQuery,
    state: FSMContext,
):
    _, scope, raw_target = callback.data.split(":", 2)
    target_id = (
        callback.from_user.id
        if scope == "user"
        else int(raw_target)
    )
    await state.clear()
    await _show_settings_scope(callback, scope, target_id)
    await callback.answer()


@router.callback_query(F.data.startswith("settings_blur_menu:"))
async def cb_settings_blur_menu(callback: types.CallbackQuery):
    _, scope, raw_target = callback.data.split(":", 2)
    target_id = (
        callback.from_user.id
        if scope == "user" and raw_target == "0"
        else int(raw_target)
    )
    if scope == "chat" and not await _can_manage_chat(
        callback.from_user.id,
        target_id,
    ):
        await callback.answer("Нужны права администратора.", show_alert=True)
        return
    settings = (
        get_managed_chat(target_id)
        if scope == "chat"
        else get_user_image_settings(target_id)
    ) or {}
    kind = (
        settings.get("custom_background_kind")
        if scope == "chat"
        else settings.get("background_kind")
    )
    if kind != "image":
        await callback.answer(
            "Размытие доступно после загрузки своего изображения.",
            show_alert=True,
        )
        return
    active = normalize_background_blur(
        settings.get(
            "custom_background_blur" if scope == "chat" else "blur",
            0,
        )
    )
    await _show_settings_text(
        callback,
        "🫧 <b>Размытие пользовательского фона</b>\n\n"
        "0% сохраняет исходную резкость, 100% даёт максимально мягкий фон. "
        "Карты, подписи и манакривая не размываются.",
        _blur_keyboard(scope, target_id, active),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("settings_blur:"))
async def cb_settings_blur(callback: types.CallbackQuery):
    _, scope, raw_target, raw_strength = callback.data.split(":", 3)
    target_id = int(raw_target)
    if scope == "user" and target_id == 0:
        target_id = callback.from_user.id
    if scope == "chat" and not await _can_manage_chat(
        callback.from_user.id,
        target_id,
    ):
        await callback.answer("Нужны права администратора.", show_alert=True)
        return
    strength = normalize_background_blur(raw_strength)
    if scope == "chat":
        selected = set_managed_chat_background_blur(target_id, strength)
        await callback.answer(f"Размытие: {selected}%")
        await _show_chat_personalization_preview(callback, target_id)
    else:
        selected = set_user_background_blur(target_id, strength)
        await callback.answer(f"Размытие: {selected}%")
        await _show_personalization_preview(callback)


async def _can_manage_chat(user_id: int, chat_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        status = str(getattr(member, "status", "")).lower()
        return status in {"creator", "administrator", "chatmemberstatus.creator", "chatmemberstatus.administrator"}
    except Exception:
        chat = get_managed_chat(chat_id)
        return bool(chat and int(chat.get("added_by") or 0) == int(user_id))


def _managed_group_chats_for_user(user_id: int) -> list[dict]:
    """Return only group chats; channel management is intentionally excluded."""
    return [
        chat
        for chat in get_managed_chats_for_user(user_id)
        if str(chat.get("chat_type") or "").lower() in {"group", "supergroup"}
    ]


def _managed_chats_keyboard(user_id: int) -> InlineKeyboardMarkup:
    chat_buttons = [
        InlineKeyboardButton(
            text=f"⚙️ {str(chat.get('title') or chat['chat_id'])[:22]}",
            callback_data=f"settings_chat:{chat['chat_id']}",
        )
        for chat in _managed_group_chats_for_user(user_id)
    ]
    rows = [
        chat_buttons[index : index + 2]
        for index in range(0, len(chat_buttons), 2)
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text="➕ Добавить администратором",
                url=_ADD_TO_CHAT_URL,
            ),
            InlineKeyboardButton(
                text="← Назад",
                callback_data="settings_home",
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _managed_chat_buttons_hub_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Open a group's button presets directly from personal design settings."""
    chat_buttons = [
        InlineKeyboardButton(
            text=f"🔘 {str(chat.get('title') or chat['chat_id'])[:22]}",
            callback_data=f"settings_chat_buttons:{chat['chat_id']}",
        )
        for chat in _managed_group_chats_for_user(user_id)
    ]
    rows = [
        chat_buttons[index : index + 2]
        for index in range(0, len(chat_buttons), 2)
    ]
    if not chat_buttons:
        rows.append([
            InlineKeyboardButton(
                text="➕ Добавить бота администратором",
                url=_ADD_TO_CHAT_URL,
            )
        ])
    rows.append([
        InlineKeyboardButton(
            text="← К оформлению",
            callback_data="settings_design",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "settings_chats")
async def cb_settings_chats(callback: types.CallbackQuery):
    chats = _managed_group_chats_for_user(callback.from_user.id)
    suffix = (
        "\n\nВыберите чат, чтобы изменить его оформление и команды."
        if chats
        else (
            "\n\nНажмите «Добавить администратором» и выберите группу. "
            "Telegram запросит минимальное право управления чатом, поэтому "
            "Shieldy не удалит бота. После подключения группа появится здесь."
        )
    )
    await _show_settings_text(
        callback,
        "👥 <b>Мои групповые чаты</b>\n"
        "Для каждого чата можно задать своё оформление и набор команд."
        + suffix,
        _managed_chats_keyboard(callback.from_user.id),
    )
    await callback.answer()


@router.callback_query(F.data == "settings_buttons_chats")
async def cb_settings_buttons_chats(callback: types.CallbackQuery):
    chats = _managed_group_chats_for_user(callback.from_user.id)
    description = (
        "Выберите группу — откроются макеты кнопок под готовой колодой. "
        "Изменение применяется сразу и не запускает повторную генерацию картинки."
        if chats
        else
        "Сначала добавьте бота администратором в групповой чат. После "
        "подключения группа появится в этом списке."
    )
    await _show_settings_text(
        callback,
        "🔘 <b>Кнопки под колодой</b>\n\n" + description,
        _managed_chat_buttons_hub_keyboard(callback.from_user.id),
    )
    await callback.answer()


def _managed_chat_text(chat: dict) -> str:
    chat_id = int(chat["chat_id"])
    style = str(chat.get("image_style") or "inherit")
    style_labels = {
        "inherit": "как в личных настройках",
        **_IMAGE_STYLE_LABELS,
    }
    background_labels = {
        "image": "своё изображение",
        "gradient": "градиент",
    }
    background = background_labels.get(
        chat.get("custom_background_kind"),
        "не загружен",
    )
    disabled_count = len(chat.get("disabled_commands") or [])
    chat_font = str(chat.get("image_font") or "inherit")
    chat_dust = str(chat.get("image_dust_display") or "inherit")
    chat_art = str(chat.get("class_art_mode") or "inherit")
    font_text = (
        "как у меня"
        if chat_font == "inherit"
        else font_label(chat_font)
    )
    dust_text = (
        "как у меня"
        if chat_dust == "inherit"
        else DUST_DISPLAY_OPTIONS[normalize_dust_display(chat_dust)]
    )
    art_text = (
        "как у меня"
        if chat_art == "inherit"
        else CLASS_ART_OPTIONS[normalize_class_art_mode(chat_art)]
    )
    chat_curve = str(chat.get("mana_curve_mode") or "inherit")
    curve_text = (
        "как у меня"
        if chat_curve == "inherit"
        else MANA_CURVE_OPTIONS[normalize_mana_curve_mode(chat_curve)]
    )
    button_layout = normalize_deck_button_layout(
        chat.get("deck_button_layout")
    )
    return (
        f"⚙️ <b>{html.escape(str(chat.get('title') or chat_id))}</b>\n\n"
        f"🎨 Стиль: <b>{html.escape(style_labels.get(style, style))}</b>\n"
        f"🖼 Свой фон: <b>{background}</b>\n"
        "🫧 Размытие: "
        f"<b>{normalize_background_blur(chat.get('custom_background_blur'))}%</b>\n"
        "↕️ Размер заголовка: "
        f"<b>{html.escape(title_size_label(chat.get('image_text_size') or 'inherit', allow_inherit=True))}</b>\n"
        f"🔤 Шрифт: <b>{html.escape(font_text)}</b>\n"
        f"💎 Стоимость пыли: <b>{html.escape(dust_text)}</b>\n"
        f"🛡 Нижний арт: <b>{html.escape(art_text)}</b>\n"
        f"📊 Манакривая: <b>{html.escape(curve_text)}</b>\n"
        "🔘 Кнопки под колодой: "
        f"<b>{html.escape(DECK_BUTTON_LAYOUT_OPTIONS[button_layout])}</b>\n"
        f"🧩 Отключено команд: <b>{disabled_count}</b>"
    )


def _managed_chat_keyboard(chat: dict) -> InlineKeyboardMarkup:
    chat_id = int(chat["chat_id"])
    active = str(chat.get("image_style") or "inherit")
    has_custom = bool(
        chat.get("custom_background_kind")
        and chat.get("custom_background_value")
    )
    rows = [
        [
            InlineKeyboardButton(
                text=f"{'✅ ' if active == 'inherit' else ''}Как у меня",
                callback_data=f"settings_chat_style:{chat_id}:inherit",
            ),
            InlineKeyboardButton(
                text=f"{'✅ ' if active == 'classic' else ''}Классика",
                callback_data=f"settings_chat_style:{chat_id}:classic",
            ),
        ],
        [
            InlineKeyboardButton(
                text=f"{'✅ ' if active == 'parchment' else ''}Пергамент",
                callback_data=f"settings_chat_style:{chat_id}:parchment",
            ),
            InlineKeyboardButton(
                text=(
                    f"{'✅ ' if active == 'custom' else ''}Свой фон"
                    if has_custom
                    else "Свой фон · нет"
                ),
                callback_data=f"settings_chat_style:{chat_id}:custom",
            ),
        ],
        [
            InlineKeyboardButton(
                text="🖼 Загрузить",
                callback_data=f"settings_background:chat:image:{chat_id}",
            ),
            InlineKeyboardButton(
                text="🌈 Градиенты",
                callback_data=f"settings_background:chat:gradient:{chat_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                text=(
                    "🫧 Blur"
                    f" · {normalize_background_blur(chat.get('custom_background_blur'))}%"
                ),
                callback_data=f"settings_blur_menu:chat:{chat_id}",
            ),
            InlineKeyboardButton(
                text="↕️ Размер текста",
                callback_data=f"settings_text_size_menu:chat:{chat_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                text="🔤 Шрифт",
                callback_data=f"settings_chat_fonts:{chat_id}",
            ),
            InlineKeyboardButton(
                text="💎 Стоимость пыли",
                callback_data=f"settings_chat_dust:{chat_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                text="🛡 Арт или логотип",
                callback_data=f"settings_chat_class_art:{chat_id}",
            ),
            InlineKeyboardButton(
                text="📚 Мои дизайны",
                callback_data=f"settings_chat_designs:{chat_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                text="📊 Нижняя область",
                callback_data=f"settings_mana_curve:chat:{chat_id}",
            ),
            InlineKeyboardButton(
                text="🔘 Кнопки под колодой",
                callback_data=f"settings_chat_buttons:{chat_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                text="🧩 Команды",
                callback_data=f"settings_chat_commands:{chat_id}",
            ),
            InlineKeyboardButton(
                text="💾 Сохранить дизайн",
                callback_data=f"settings_chat_design_save:{chat_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                text="← К чатам",
                callback_data="settings_chats",
            )
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _chat_font_keyboard(
    chat_id: int,
    active_font: str,
) -> InlineKeyboardMarkup:
    active = str(active_font or "inherit")
    buttons = [
        InlineKeyboardButton(
            text=f"{'✅ ' if active == 'inherit' else ''}Как у меня",
            callback_data=f"settings_chat_font:{chat_id}:inherit",
        ),
        InlineKeyboardButton(
            text=f"{'✅ ' if active == 'auto' else ''}Авто",
            callback_data=f"settings_chat_font:{chat_id}:auto",
        ),
        *[
            InlineKeyboardButton(
                text=f"{'✅ ' if active == key else ''}{option['label']}",
                callback_data=f"settings_chat_font:{chat_id}:{key}",
            )
            for key, option in FONT_OPTIONS.items()
        ],
    ]
    rows = [
        buttons[index : index + 2]
        for index in range(0, len(buttons), 2)
    ]
    rows.append([
        InlineKeyboardButton(
            text="← Назад",
            callback_data=f"settings_chat:{chat_id}",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _chat_dust_keyboard(
    chat_id: int,
    active_display: str,
) -> InlineKeyboardMarkup:
    active = str(active_display or "inherit")
    buttons = [
        InlineKeyboardButton(
            text=f"{'✅ ' if active == 'inherit' else ''}Как у меня",
            callback_data=f"settings_chat_dust_set:{chat_id}:inherit",
        ),
        *[
            InlineKeyboardButton(
                text=f"{'✅ ' if active == key else ''}{label}",
                callback_data=f"settings_chat_dust_set:{chat_id}:{key}",
            )
            for key, label in DUST_DISPLAY_OPTIONS.items()
        ],
    ]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            buttons[:2],
            buttons[2:],
            [InlineKeyboardButton(
                text="← Назад",
                callback_data=f"settings_chat:{chat_id}",
            )],
        ]
    )


def _chat_class_art_keyboard(chat: dict) -> InlineKeyboardMarkup:
    chat_id = int(chat["chat_id"])
    active = str(chat.get("class_art_mode") or "inherit")
    has_logo = bool(chat.get("custom_logo_path"))
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{'✅ ' if active == 'inherit' else ''}Как у меня",
                    callback_data=(
                        f"settings_chat_class_art_set:{chat_id}:inherit"
                    ),
                ),
                InlineKeyboardButton(
                    text=f"{'✅ ' if active == 'class' else ''}Арт класса",
                    callback_data=(
                        f"settings_chat_class_art_set:{chat_id}:class"
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    text=(
                        f"{'✅ ' if active == 'logo' else ''}Логотип чата"
                        if has_logo
                        else "Логотип чата · нет"
                    ),
                    callback_data=(
                        f"settings_chat_class_art_set:{chat_id}:logo"
                    ),
                ),
                InlineKeyboardButton(
                    text="⬆️ Загрузить",
                    callback_data=f"settings_chat_logo_upload:{chat_id}",
                ),
            ],
            [InlineKeyboardButton(
                text="← Назад",
                callback_data=f"settings_chat:{chat_id}",
            )],
        ]
    )


def _chat_deck_buttons_keyboard(chat: dict) -> InlineKeyboardMarkup:
    chat_id = int(chat["chat_id"])
    active = normalize_deck_button_layout(chat.get("deck_button_layout"))
    rows = [
        [InlineKeyboardButton(
            text=f"{'✅ ' if active == key else ''}{label}",
            callback_data=f"settings_chat_buttons_set:{chat_id}:{key}",
        )]
        for key, label in DECK_BUTTON_LAYOUT_OPTIONS.items()
    ]
    rows.append([InlineKeyboardButton(
        text="← Назад",
        callback_data=f"settings_chat:{chat_id}",
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _chat_saved_designs_keyboard(
    chat_id: int,
    designs: list[dict],
) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            text=f"🎨 {str(design.get('name') or 'Дизайн')[:28]}",
            callback_data=(
                f"settings_chat_design_apply:{chat_id}:{int(design['id'])}"
            ),
        )]
        for design in designs
    ]
    rows.append([
        InlineKeyboardButton(
            text="💾 Сохранить текущий",
            callback_data=f"settings_chat_design_save:{chat_id}",
        ),
        InlineKeyboardButton(
            text="← Назад",
            callback_data=f"settings_chat:{chat_id}",
        ),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("settings_chat_fonts:"))
async def cb_settings_chat_fonts(callback: types.CallbackQuery):
    chat_id = int(callback.data.rsplit(":", 1)[1])
    if not await _can_manage_chat(callback.from_user.id, chat_id):
        await callback.answer("Нужны права администратора.", show_alert=True)
        return
    chat = get_managed_chat(chat_id)
    active = str(chat.get("image_font") or "inherit")
    preview_font = (
        _image_theme_for_context(callback.from_user.id, chat_id)["font"]
        if active == "inherit"
        else active
    )
    preview = await asyncio.to_thread(build_font_preview, preview_font)
    await _show_settings_photo(
        callback,
        preview,
        filename="deckview-chat-fonts.jpg",
        caption=(
            "🔤 <b>Шрифт для чата</b>\n\n"
            "«Как у меня» использует личную настройку администратора."
        ),
        reply_markup=_chat_font_keyboard(chat_id, active),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("settings_chat_font:"))
async def cb_settings_chat_font(callback: types.CallbackQuery):
    _, raw_chat_id, requested = callback.data.split(":", 2)
    chat_id = int(raw_chat_id)
    if not await _can_manage_chat(callback.from_user.id, chat_id):
        await callback.answer("Нужны права администратора.", show_alert=True)
        return
    selected = set_managed_chat_image_font(chat_id, requested)
    await callback.answer(
        "Шрифт: как у меня"
        if selected == "inherit"
        else f"Шрифт: {font_label(selected)}"
    )
    await _show_chat_personalization_preview(callback, chat_id)


@router.callback_query(F.data.startswith("settings_chat_dust:"))
async def cb_settings_chat_dust(callback: types.CallbackQuery):
    chat_id = int(callback.data.rsplit(":", 1)[1])
    if not await _can_manage_chat(callback.from_user.id, chat_id):
        await callback.answer("Нужны права администратора.", show_alert=True)
        return
    chat = get_managed_chat(chat_id)
    await _show_settings_text(
        callback,
        "💎 <b>Стоимость пыли в этом чате</b>\n\n"
        "Можно использовать личную настройку или задать отдельный размер.",
        _chat_dust_keyboard(
            chat_id,
            chat.get("image_dust_display") or "inherit",
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("settings_chat_dust_set:"))
async def cb_settings_chat_dust_set(callback: types.CallbackQuery):
    _, raw_chat_id, requested = callback.data.split(":", 2)
    chat_id = int(raw_chat_id)
    if not await _can_manage_chat(callback.from_user.id, chat_id):
        await callback.answer("Нужны права администратора.", show_alert=True)
        return
    selected = set_managed_chat_dust_display(chat_id, requested)
    await callback.answer(
        "Пыль: как у меня"
        if selected == "inherit"
        else f"Пыль: {DUST_DISPLAY_OPTIONS[selected]}"
    )
    await _show_chat_personalization_preview(callback, chat_id)


@router.callback_query(F.data.startswith("settings_chat_class_art:"))
async def cb_settings_chat_class_art(callback: types.CallbackQuery):
    chat_id = int(callback.data.rsplit(":", 1)[1])
    if not await _can_manage_chat(callback.from_user.id, chat_id):
        await callback.answer("Нужны права администратора.", show_alert=True)
        return
    chat = get_managed_chat(chat_id)
    await _show_settings_text(
        callback,
        "🛡 <b>Нижний арт для чата</b>\n\n"
        "Можно наследовать личный выбор, оставить персонажа класса или "
        "загрузить отдельный логотип этой группы.",
        _chat_class_art_keyboard(chat),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("settings_chat_class_art_set:"))
async def cb_settings_chat_class_art_set(callback: types.CallbackQuery):
    _, raw_chat_id, requested = callback.data.split(":", 2)
    chat_id = int(raw_chat_id)
    if not await _can_manage_chat(callback.from_user.id, chat_id):
        await callback.answer("Нужны права администратора.", show_alert=True)
        return
    try:
        selected = set_managed_chat_class_art_mode(chat_id, requested)
    except ValueError:
        await callback.answer(
            "Сначала загрузите логотип этого чата.",
            show_alert=True,
        )
        return
    await callback.answer(
        "Арт: как у меня"
        if selected == "inherit"
        else f"Арт: {CLASS_ART_OPTIONS[selected]}"
    )
    await _show_chat_personalization_preview(callback, chat_id)


@router.callback_query(F.data.startswith("settings_chat_logo_upload:"))
async def cb_settings_chat_logo_upload(
    callback: types.CallbackQuery,
    state: FSMContext,
):
    chat_id = int(callback.data.rsplit(":", 1)[1])
    if not await _can_manage_chat(callback.from_user.id, chat_id):
        await callback.answer("Нужны права администратора.", show_alert=True)
        return
    await state.set_state(SettingsState.waiting_logo_image)
    await state.set_data({"scope": "chat", "target_id": chat_id})
    await _show_settings_text(
        callback,
        "⬆️ <b>Логотип чата</b>\n\n"
        "Отправьте PNG, JPEG или WEBP до 20 МБ. После загрузки "
        "логотип будет включён только для выбранной группы.",
        InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="← Отмена",
                callback_data=f"settings_chat:{chat_id}",
            )
        ]]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("settings_chat_designs:"))
async def cb_settings_chat_designs(callback: types.CallbackQuery):
    chat_id = int(callback.data.rsplit(":", 1)[1])
    if not await _can_manage_chat(callback.from_user.id, chat_id):
        await callback.answer("Нужны права администратора.", show_alert=True)
        return
    designs = get_user_image_designs(callback.from_user.id)
    await _show_settings_text(
        callback,
        "📚 <b>Дизайны для чата</b>\n\n"
        "Выберите личный сохранённый дизайн — все его параметры будут "
        "скопированы в настройки этой группы.",
        _chat_saved_designs_keyboard(chat_id, designs),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("settings_chat_design_apply:"))
async def cb_settings_chat_design_apply(callback: types.CallbackQuery):
    _, raw_chat_id, raw_design_id = callback.data.split(":", 2)
    chat_id = int(raw_chat_id)
    if not await _can_manage_chat(callback.from_user.id, chat_id):
        await callback.answer("Нужны права администратора.", show_alert=True)
        return
    applied = apply_user_image_design_to_chat(
        callback.from_user.id,
        int(raw_design_id),
        chat_id,
    )
    if not applied:
        await callback.answer("Дизайн не найден.", show_alert=True)
        return
    await callback.answer("Дизайн применён к чату")
    await _show_chat_personalization_preview(callback, chat_id)


@router.callback_query(F.data.startswith("settings_chat_design_save:"))
async def cb_settings_chat_design_save(
    callback: types.CallbackQuery,
    state: FSMContext,
):
    chat_id = int(callback.data.rsplit(":", 1)[1])
    if not await _can_manage_chat(callback.from_user.id, chat_id):
        await callback.answer("Нужны права администратора.", show_alert=True)
        return
    await state.set_state(SettingsState.waiting_design_name)
    await state.set_data({"scope": "chat", "target_id": chat_id})
    await _show_settings_text(
        callback,
        "💾 <b>Сохранить дизайн чата</b>\n\n"
        "Отправьте название до 32 символов. Эффективное оформление этой "
        "группы сохранится в ваших личных дизайнах.",
        InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="← Отмена",
                callback_data=f"settings_chat:{chat_id}",
            )
        ]]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("settings_chat:"))
async def cb_settings_chat(
    callback: types.CallbackQuery,
    state: FSMContext,
):
    await state.clear()
    chat_id = int(callback.data.rsplit(":", 1)[1])
    if not await _can_manage_chat(callback.from_user.id, chat_id):
        await callback.answer("Нужны права администратора.", show_alert=True)
        return
    chat = get_managed_chat(chat_id)
    if not chat:
        await callback.answer("Чат ещё не зарегистрирован.", show_alert=True)
        return
    await callback.answer("Обновляю пример…")
    await _show_chat_personalization_preview(callback, chat_id)


@router.callback_query(F.data.startswith("settings_chat_style:"))
async def cb_settings_chat_style(callback: types.CallbackQuery):
    _, raw_chat_id, style = callback.data.split(":", 2)
    chat_id = int(raw_chat_id)
    if not await _can_manage_chat(callback.from_user.id, chat_id):
        await callback.answer("Нужны права администратора.", show_alert=True)
        return
    if style not in {"inherit", "classic", "parchment", "custom"}:
        await callback.answer("Неизвестный стиль.", show_alert=True)
        return
    current = get_managed_chat(chat_id)
    if style == "custom" and not (
        current
        and current.get("custom_background_kind")
        and current.get("custom_background_value")
    ):
        await callback.answer(
            "Сначала загрузите фон или выберите градиент.",
            show_alert=True,
        )
        return
    set_managed_chat_image_style(chat_id, style)
    await callback.answer("Оформление обновлено.")
    await _show_chat_personalization_preview(callback, chat_id)


def _managed_commands_keyboard(chat: dict) -> InlineKeyboardMarkup:
    chat_id = int(chat["chat_id"])
    disabled = set(chat.get("disabled_commands") or [])
    buttons = [
        InlineKeyboardButton(
            text=f"{'❌' if command in disabled else '✅'} {label}",
            callback_data=f"settings_chat_command:{chat_id}:{command}",
        )
        for command, label in _MANAGED_COMMANDS.items()
    ]
    rows = [buttons[index : index + 2] for index in range(0, len(buttons), 2)]
    rows.append(
        [InlineKeyboardButton(text="← Назад", callback_data=f"settings_chat:{chat_id}")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("settings_chat_commands:"))
async def cb_settings_chat_commands(callback: types.CallbackQuery):
    chat_id = int(callback.data.rsplit(":", 1)[1])
    if not await _can_manage_chat(callback.from_user.id, chat_id):
        await callback.answer("Нужны права администратора.", show_alert=True)
        return
    chat = get_managed_chat(chat_id)
    await _show_settings_text(
        callback,
        "🧩 <b>Команды чата</b>\n\n✅ доступна · ❌ отключена",
        _managed_commands_keyboard(chat),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("settings_chat_buttons:"))
async def cb_settings_chat_buttons(callback: types.CallbackQuery):
    chat_id = int(callback.data.rsplit(":", 1)[1])
    if not await _can_manage_chat(callback.from_user.id, chat_id):
        await callback.answer("Нужны права администратора.", show_alert=True)
        return
    chat = get_managed_chat(chat_id)
    await _show_settings_text(
        callback,
        "🔘 <b>Кнопки под колодой</b>\n\n"
        "Выберите макет для всех новых колод в этой группе. Изменение "
        "начинает действовать сразу и не требует перегенерации картинок.",
        _chat_deck_buttons_keyboard(chat),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("settings_chat_buttons_set:"))
async def cb_settings_chat_buttons_set(callback: types.CallbackQuery):
    _, raw_chat_id, requested = callback.data.split(":", 2)
    chat_id = int(raw_chat_id)
    if not await _can_manage_chat(callback.from_user.id, chat_id):
        await callback.answer("Нужны права администратора.", show_alert=True)
        return
    selected = set_managed_chat_deck_button_layout(chat_id, requested)
    chat = get_managed_chat(chat_id)
    await callback.answer("Макет кнопок сохранён.")
    await _show_settings_text(
        callback,
        _managed_chat_text(chat),
        _managed_chat_keyboard(chat),
    )


@router.callback_query(F.data.startswith("settings_chat_command:"))
async def cb_settings_chat_command(callback: types.CallbackQuery):
    _, raw_chat_id, command = callback.data.split(":", 2)
    chat_id = int(raw_chat_id)
    if command not in _MANAGED_COMMANDS:
        await callback.answer("Неизвестная команда.", show_alert=True)
        return
    if not await _can_manage_chat(callback.from_user.id, chat_id):
        await callback.answer("Нужны права администратора.", show_alert=True)
        return
    chat = get_managed_chat(chat_id)
    disabled = set(chat.get("disabled_commands") or [])
    if command in disabled:
        disabled.remove(command)
    else:
        disabled.add(command)
    set_managed_chat_disabled_commands(chat_id, list(disabled))
    chat = get_managed_chat(chat_id)
    await callback.message.edit_reply_markup(
        reply_markup=_managed_commands_keyboard(chat)
    )
    await callback.answer("Настройка сохранена.")


@router.my_chat_member()
async def on_bot_chat_member_update(event: types.ChatMemberUpdated):
    if str(event.chat.type).lower() not in {"group", "supergroup"}:
        return
    status = str(getattr(event.new_chat_member, "status", "")).lower()
    active = status not in {"left", "kicked", "chatmemberstatus.left", "chatmemberstatus.kicked"}
    actor_id = getattr(event.from_user, "id", None)
    if actor_id == TELEGRAM_GROUP_ANONYMOUS_BOT_ID:
        actor_id = None
    register_managed_chat(
        event.chat.id,
        event.chat.title or str(event.chat.id),
        str(event.chat.type),
        actor_id,
        is_active=active,
    )


@router.message(Command("help"))
async def cmd_help(message: types.Message):
    """Справка по всем пользовательским командам."""
    text = (
        "📖 <b>Команды бота</b>\n"
        "\n"
        "🃏 <b>Колоды</b>\n"
        "• Отправьте код колоды (<code>AA…</code>) — пришлю изображение\n"
        "• <b>/hq</b> <code>AA…</code> — JPG до 3840 px файлом без сжатия Telegram\n"
        "• <b>/meta</b> — текущая мета Стандарт/Вольный: архетипы, винрейт, коды колод\n"
        "• <b>/arena</b> — актуальные винрейты классов на Арене\n"
        "• <b>/compare</b> <code>AA… AA…</code> — сравнить две колоды по составу карт\n"
        "\n"
        "🔍 <b>Карты</b>\n"
        "• <b>/card</b> <code>Название</code> — найти и показать карту\n"
        "• <b>/card random</b> — случайная карта\n"
        "• <b>/card random mage</b> — случайная карта мага (любой класс)\n"
        "• <b>/card random legendary</b> — случайная легендарная\n"
        "• <b>/card random spell</b> — случайное заклинание\n"
        "  Карты ищутся по-русски и по-английски.\n"
        "  Если карта не найдена — бот предложит похожие варианты.\n"
        "\n"
        "🗂 <b>Поиск колод из базы</b>\n"
        "• <b>/findwith</b> <code>Карта</code> — колоды с этой картой\n"
        "• <b>/findwith</b> <code>Карта1; Карта2</code> — колоды со всеми указанными картами\n"
        "\n"
        "👤 <b>Профиль</b>\n"
        "• <b>/profile</b> — Manacost ID и сохранённые колоды\n"
        "• <b>/settings</b> — выбрать оформление изображений колод\n"
        "• Кнопка <b>«Сохранить»</b> под колодой — добавить её в профиль\n"
        "• Кнопка <b>«Скачать»</b> — сохранить изображение колоды файлом\n"
        "• <b>/healt</b> — диагностика источников и токенов (админ)\n"
        "\n"
        "💡 <i>Совет:</i> в личном чате используйте кнопки «Мой профиль» и «О боте»."
    )
    await message.answer(
        text,
        reply_markup=MAIN_REPLY_KEYBOARD if message.chat.type == "private" else None,
    )


# Русские названия для типа и редкости карты (подпись /card)
_CARD_TYPE_RU = {
    "MINION": "Существо",
    "SPELL": "Заклинание",
    "WEAPON": "Оружие",
    "HERO": "Герой",
    "LOCATION": "Локация",
    "HERO_POWER": "Способность героя",
}
_RARITY_RU = {
    "FREE": "Бесплатная",
    "COMMON": "Обычная",
    "RARE": "Редкая",
    "EPIC": "Эпическая",
    "LEGENDARY": "Легендарная",
}
# Официальные русские названия дополнений (set id → русское название).
# Сгруппировано по годам Hearthstone (Standard). Логика: set_raw = set.upper().replace("-", "_").
_SET_ID_TO_RU = {
    # ——— Базовые наборы (всегда в игре) ———
    "CORE": "Базовый набор",
    "EXPERT1": "Классика",
    "PROMO": "Промо",
    "REWARD": "Награда",
    # ——— 2014–2015: до введения «года» (Классика, приключения, первые дополнения) ———
    "NAXX": "Проклятие Наксрамаса",
    "GVG": "Гоблины и гномы",
    "BRM": "Чёрная гора",
    "TGT": "Великий турнир",
    "LOE": "Лига исследователей",
    # ——— 2016 — Год Кракена ———
    "WOG": "Утроба богов",
    "KARA": "Каразан",
    "MSG": "Храм Яшмарового Змея",
    # ——— 2017 — Год Мамонта ———
    "UNGORO": "Путешествие в Ун'Горо",
    "ICECROWN": "Рыцари Ледяного Трона",
    "LOOTAPALOOZA": "Кобольды и подземелья",
    # ——— 2018 — Год Ворона ———
    "GILNEAS": "Ведьмин лес",
    "BOOMSDAY": "День Судного дня",
    "TROLL": "Рыцари Тёмного Рифа",
    # ——— 2019 — Год Дракона ———
    "DALARAN": "Возрождение Теней",
    "ULDUM": "Спасители Ульдума",
    "DRAGONS": "Сны о Драконе",
    "YEAR_OF_THE_DRAGON": "Год Дракона",
    # ——— 2020 — Год Феникса ———
    "OUTLAND": "Руины Запределья",
    "ASHES_OF_OUTLAND": "Руины Запределья",
    "SCHOLOMANCE": "Университет Шоломансии",
    "SCHOLOMANCE_ACADEMY": "Университет Шоломансии",
    "DARKMOON_FAIRE": "Ярмарка Тёмной Луны",
    "THE_BARRENS": "Кованые в Бесплодных землях",
    # ——— 2021 — Год Грифона ———
    "FORGED_IN_THE_BARRENS": "Кованые в Бесплодных землях",
    "STORMWIND": "Королевский двор Штормграда",
    "ALTERAC": "Альтеракская долина",
    # ——— 2022 — Год Гидры ———
    "THE_SUNKEN_CITY": "Затонувший город",
    "REVENDRETH": "Убийство в Замке Нафрия",
    "PATH_OF_ARTHAS": "Путь Артаса",
    # ——— 2023 — Год Волка ———
    "MARCH_OF_THE_LICH_KING": "Марш Короля-лича",
    "FESTIVAL_OF_LEGENDS": "Фестиваль легенд",
    "TITANS": "Титаны",
    # ——— 2024 — Год Пегаса ———
    "WILD_WEST": "Дикий Запад",
    "BLASTED_LANDS": "Выжженные земли",
    # ——— 2025 — Год Ящера ———
    "THE_LOST_CITY_OF_UNGORO": "Затерянный город Ун'Горо",
    "INTO-THE-EMERALD_DREAM": "Изумрудный Сон",
    "ACROSS_THE_TIMEWAYS": "Сквозь потоки времени",
    # ——— 2026 — Год Скарабея ———
    "CATACLYSM": "Катаклизм",
    # ——— Служебные / особые ———
    "EVENT": "Событие",
    "BATTLEGROUNDS": "Поля Сражений",
    "TB": "Потасовка",
    "LETTUCE": "Наёмники",
}


_MECHANIC_RU = {
    "BATTLECRY": "Боевой клич",
    "TAUNT": "Провокация",
    "DEATHRATTLE": "Предсмертный хрип",
    "RUSH": "Натиск",
    "CHARGE": "Рывок",
    "DIVINE_SHIELD": "Божественный щит",
    "LIFESTEAL": "Похищение жизни",
    "POISONOUS": "Яд",
    "WINDFURY": "Неистовство ветра",
    "SECRET": "Секрет",
    "DISCOVER": "Раскопка",
    "REBORN": "Перерождение",
    "TRADEABLE": "Обмен",
    "TITAN": "Титан",
    "COLOSSAL": "Колосс",
    "OVERLOAD": "Перегрузка",
    "COMBO": "Серия приёмов",
    "FREEZE": "Заморозка",
    "SILENCE": "Немота",
    "SPELLPOWER": "Сила заклинаний",
}
_HIDDEN_MECHANICS = {"TRIGGER_VISUAL", "APPEAR_FUNCTIONALLY_DEAD"}


def _localized(value, lang: str = "ru") -> str:
    if isinstance(value, dict):
        return str(value.get(lang) or value.get("en") or "").strip()
    return str(value or "").strip()


def _clean_card_text(value) -> str:
    text = _localized(value)
    text = re.sub(r"<br\\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\[x\]", "", text, flags=re.I)
    return html.escape(html.unescape(text).replace("\xa0", " ").strip())


def _card_caption(card: dict, likes: int = 0, dislikes: int = 0) -> str:
    """Compact, reader-facing card description from the Public API."""
    name = _localized(card.get("name")) or "Карта"
    en_name = _localized(card.get("name"), "en")
    dbf_id = card.get("dbfId")
    card_id = card.get("id") or "—"
    type_value = card.get("type") or {}
    type_raw = _localized(type_value.get("id") if isinstance(type_value, dict) else type_value).upper()
    type_ru = (
        _localized(type_value.get("nameRu")) if isinstance(type_value, dict) else ""
    ) or _CARD_TYPE_RU.get(type_raw, type_raw or "—")
    rarity_raw = str(card.get("rarity") or "").upper()
    rarity_ru = _RARITY_RU.get(rarity_raw, rarity_raw or "—")

    lines = [
        f"<b>{html.escape(name)}</b>",
        f"{html.escape(type_ru)} · {html.escape(rarity_ru)}",
    ]
    if en_name and en_name.lower() != name.lower():
        lines.append(f"<i>{html.escape(en_name)}</i>")

    description = _clean_card_text(card.get("text"))
    lines.extend(["", "<b>Описание</b>", description or "—"])

    mechanics = []
    for mechanic in card.get("mechanics") or []:
        raw = str(mechanic or "").upper()
        if not raw or raw in _HIDDEN_MECHANICS:
            continue
        mechanics.append(_MECHANIC_RU.get(raw, raw.replace("_", " ").title()))
    if mechanics:
        lines.extend(["", f"<b>Механики:</b> {html.escape(', '.join(mechanics))}"])

    flavor = _clean_card_text(card.get("flavor"))
    if flavor:
        lines.extend(["", f"🎨 <i>{flavor}</i>"])

    identity = f"{card_id}"
    if dbf_id is not None:
        identity += f" · dbfId {dbf_id}"
    lines.extend(["", f"<code>{html.escape(identity)}</code>"])
    caption = "\n".join(lines)
    return caption if len(caption) <= 1024 else caption[:1018].rstrip() + "…"


def _load_manacost_card_data_sync(
    dbf_id: int, local_card: dict | None = None
) -> dict:
    return manacost_get_card_with_fallback(
        dbf_id,
        local_card,
        card_format="wild",
    )


def _load_manacost_card_sync(
    dbf_id: int,
    local_card: dict | None = None,
    view: str = "card",
) -> tuple[dict, bytes]:
    candidate = local_card or {}
    card_id = str(candidate.get("cardId") or "").strip()
    if not card_id and isinstance(candidate.get("id"), str):
        card_id = str(candidate["id"]).strip()
    if view == "card":
        api_card, image_bytes = manacost_get_card_bundle_with_fallback(
            dbf_id,
            candidate,
            card_format="wild",
        )
    else:
        api_card = _load_manacost_card_data_sync(dbf_id, candidate)
        image_bytes = (
            manacost_get_card_full_art(str(api_card["id"]))
            if view == "art"
            else manacost_get_card_image(str(api_card["id"]), "full")
        )
    name = _localized(api_card.get("name")) or "Карта"
    return (
        api_card,
        build_full_art_showcase(image_bytes, name)
        if view == "art"
        else build_card_showcase(image_bytes, name),
    )


async def _send_one_card(
    target: types.Message | types.CallbackQuery,
    dbf_id: int,
    card: dict | None = None,
    status_message: types.Message | None = None,
    view: str = "card",
):
    """Show one card and allow switching between card render and full art."""
    local_card = card or get_card_by_dbfid(dbf_id)
    loop = asyncio.get_running_loop()
    try:
        api_card, image_bytes = await loop.run_in_executor(
            None, _load_manacost_card_sync, dbf_id, local_card, view
        )
    except Exception as e:
        detail = str(e).lower()
        msg = (
            "Эта версия карты недоступна в базе данных Манакоста. "
            "Попробуйте снова через /card и выберите другой вариант."
            if "404" in detail or "not found" in detail or "не найдена" in detail
            else "Сервис карт временно недоступен. Попробуйте ещё раз чуть позже."
        )
        if isinstance(target, types.CallbackQuery):
            await target.answer(msg, show_alert=True)
        else:
            if status_message:
                try:
                    await status_message.delete()
                except Exception:
                    pass
            await target.answer(msg)
        return
    caption = _card_caption(api_card)
    card_prefix = "✅ " if view == "card" else ""
    art_prefix = "✅ " if view == "art" else ""
    keyboard_rows = [
        [
            InlineKeyboardButton(
                text=f"{card_prefix}Карта",
                callback_data=f"card_view:card:{dbf_id}",
            ),
            InlineKeyboardButton(
                text=f"{art_prefix}Арт карты",
                callback_data=f"card_view:art:{dbf_id}",
            ),
        ],
    ]
    if not api_card.get("_metadataFallback"):
        keyboard_rows.append([
            InlineKeyboardButton(
                text="Подробнее о карте",
                url=manacost_card_web_url(api_card),
            )
        ])
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
    photo = BufferedInputFile(image_bytes, filename=f"{api_card['id']}.jpg")
    if isinstance(target, types.CallbackQuery):
        await target.message.edit_media(InputMediaPhoto(media=photo, caption=caption), reply_markup=reply_markup)
        await target.answer()
    else:
        if status_message:
            try:
                await status_message.delete()
            except Exception:
                pass
        await target.answer_photo(photo, caption=caption, reply_markup=reply_markup)


def _card_search_option(card: dict) -> tuple[int | None, str, str]:
    """Return dbfId, reader-facing name and short disambiguation details."""
    try:
        dbf_id = int(card.get("dbfId"))
    except (TypeError, ValueError):
        dbf_id = None
    name = _localized(card.get("name")) or "Без имени"
    set_raw = str(card.get("set") or "").upper().replace("-", "_")
    set_name = _SET_ID_TO_RU.get(set_raw, set_raw.replace("_", " ").title())
    formats = {
        str(value or "").strip().lower()
        for value in (card.get("formats") or [])
    }
    if "standard" in formats:
        mode = "Стандарт"
    elif "wild" in formats:
        mode = "Вольный"
    elif not card.get("collectible"):
        mode = "Особый режим"
    else:
        mode = ""
    details = " · ".join(value for value in (set_name, mode) if value)
    return dbf_id, name, details


@router.message(Command("card"))
async def cmd_card(message: types.Message):
    """Команда /card: строгий поиск, при отсутствии — нечёткий; одна карта — показ, несколько — кнопки выбора; поддержка /card random [класс|редкость]."""
    text = (message.text or "").strip()
    parts = text.split(maxsplit=1)
    query = (parts[1].strip() if len(parts) > 1 else "").strip()
    if not query:
        await message.answer(
            "Напишите название карты или id:\n"
            "• <code>/card Терран</code>\n"
            "• <code>/card 12345</code> (dbfId)\n"
            "• <code>/card CORE_CS3_027</code>\n"
            "• <code>/card random</code>, <code>/card random mage</code>, <code>/card random legendary</code>\n"
            "• <code>/card random minion</code>, <code>/card random spell</code>"
        )
        return

    hsjson_configure(HSJSON_CARDS_URL)
    loop = asyncio.get_running_loop()
    q_lower = query.lower().strip()

    # Режим случайной карты
    if q_lower == "random" or q_lower.startswith("random "):
        rest = q_lower[6:].strip()
        card_class = None
        rarity = None
        card_type = None
        type_map = {
            "minion": "minion", "существо": "minion",
            "spell": "spell", "заклинание": "spell",
            "weapon": "weapon", "оружие": "weapon",
            "hero": "hero", "локация": "location", "location": "location",
        }
        if rest:
            class_map = {
                "mage": "mage", "маг": "mage", "воин": "warrior", "warrior": "warrior",
                "охотник": "hunter", "hunter": "hunter", "друид": "druid", "druid": "druid",
                "жрец": "priest", "priest": "priest", "разбойник": "rogue", "rogue": "rogue",
                "шаман": "shaman", "shaman": "shaman", "warlock": "warlock", "демонолог": "warlock",
                "паладин": "paladin", "paladin": "paladin",
            }
            rarity_map = {
                "legendary": "legendary", "легендарная": "legendary", "epic": "epic", "эпик": "epic",
                "rare": "rare", "редкая": "rare", "common": "common", "обычная": "common",
            }
            tokens = rest.lower().split()
            for t in tokens:
                if t in type_map:
                    card_type = type_map[t]
                elif t in class_map:
                    card_class = class_map[t]
                elif t in rarity_map:
                    rarity = rarity_map[t]
                elif t in ("mage", "warrior", "hunter", "druid", "priest", "rogue", "shaman", "warlock", "paladin"):
                    card_class = t
                elif t in ("legendary", "epic", "rare", "common"):
                    rarity = t
        card = await loop.run_in_executor(
            None,
            lambda: get_random_card(card_class=card_class, rarity=rarity, card_type=card_type),
        )
        if card:
            status_msg = await message.answer("Загружаю карту...")
            await _send_one_card(message, card["id"], card=card, status_message=status_msg)
        else:
            await message.answer("Не найдено карт по заданным фильтрам.")
        _log_bot_event("card", getattr(message.chat, "type", None), {"query": query})
        return

    status_msg = await message.answer("🔎 Ищу карту в базе данных Манакоста…")
    try:
        matches = await loop.run_in_executor(
            None,
            lambda: manacost_search_cards_flexible(
                query,
                card_format="wild",
                limit=15,
            ),
        )
    except Exception:
        try:
            await status_msg.delete()
        except Exception:
            pass
        await message.answer(
            "Сервис поиска карт временно недоступен. "
            "Попробуйте ещё раз чуть позже."
        )
        return

    if not matches:
        local_hints = await loop.run_in_executor(
            None,
            lambda: (
                find_cards_by_query(query)
                or search_cards_fuzzy(query, limit=5)
                or suggest_cards_by_name(query, limit=5)
            ),
        )
        try:
            await status_msg.delete()
        except Exception:
            pass
        if local_hints:
            names = []
            for card in local_hints:
                name = str(card.get("name") or "").strip()
                if name and name not in names:
                    names.append(name)
            suffix = (
                "\n\nПохожие названия из других режимов: "
                + ", ".join(html.escape(name) for name in names[:4])
                + "."
                if names
                else ""
            )
            await message.answer(
                f"По запросу <b>{html.escape(query)}</b> нет доступной "
                "карты в базе данных Манакоста."
                f"{suffix}\n\n"
                "Можно искать по части названия, на русском или английском — "
                "бот также исправляет небольшие опечатки и неверную раскладку."
            )
        else:
            await message.answer(
                f"По запросу <b>{html.escape(query)}</b> ничего не найдено.\n\n"
                "Попробуйте часть названия, английское название, CardID "
                "или dbfId. Небольшие опечатки и раскладку бот исправляет сам."
            )
        return

    if len(matches) == 1:
        card = matches[0]
        dbf_id, _name, _details = _card_search_option(card)
        if dbf_id is None:
            try:
                await status_msg.delete()
            except Exception:
                pass
            await message.answer("У найденной карты отсутствует dbfId.")
            return
        try:
            await status_msg.edit_text("Загружаю карту…")
        except Exception:
            pass
        await _send_one_card(message, dbf_id, card=card, status_message=status_msg)
        _log_bot_event("card", getattr(message.chat, "type", None), {"query": query})
        return

    # Несколько вариантов — список с инлайн-кнопками
    try:
        await status_msg.delete()
    except Exception:
        pass
    lines = [
        f"По запросу <b>{html.escape(query)}</b> найдено несколько карт. "
        "Выберите нужную:"
    ]
    buttons = []
    for i, card in enumerate(matches[:15], 1):
        dbf_id, name, details = _card_search_option(card)
        if dbf_id is None:
            continue
        escaped_details = f" — {html.escape(details)}" if details else ""
        lines.append(f"{i}. <b>{html.escape(name)}</b>{escaped_details}")
        label = f"{name} · {details}" if details else name
        buttons.append(
            InlineKeyboardButton(
                text=label[:60],
                callback_data=f"card_pick:{dbf_id}",
            )
        )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[buttons[i : i + 2] for i in range(0, len(buttons), 2)])
    await message.answer("\n".join(lines), reply_markup=keyboard)
    _log_bot_event("card", getattr(message.chat, "type", None), {"query": query})


@router.callback_query(F.data.startswith("card_pick:"))
async def cb_card_pick(callback: types.CallbackQuery):
    """Показать выбранную по кнопке карту (заменить сообщение со списком на фото карты)."""
    raw = callback.data.split(":", 1)[1].strip()
    dbf_id = int(raw) if raw.isdigit() else None
    if dbf_id is None:
        await callback.answer("Ошибка.", show_alert=True)
        return
    await _send_one_card(callback, dbf_id)


@router.callback_query(F.data.startswith("card_view:"))
async def cb_card_view(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    if len(parts) != 3 or parts[1] not in {"card", "art"} or not parts[2].isdigit():
        await callback.answer("Ошибка.", show_alert=True)
        return
    await _send_one_card(callback, int(parts[2]), view=parts[1])


def _build_findwith_api_keyboard(
    decks: list[dict], search_token: str, page: int
) -> InlineKeyboardMarkup:
    """Deck buttons and pagination for an API-backed /findwith result."""
    offset = page * FINDWITH_PER_PAGE
    rows = []
    for i, deck in enumerate(decks[offset : offset + FINDWITH_PER_PAGE]):
        num = offset + i + 1
        label = deck.get("deck_name") or f"Колода {num}"
        wr = deck.get("winrate")
        games = int(deck.get("games") or 0)
        suffix = f" · {wr:.1f}% · {games:,} игр" if isinstance(wr, (int, float)) else f" · {games:,} игр"
        button_text = f"{num}. {label}{suffix}".replace(",", " ")
        rows.append([
            InlineKeyboardButton(
                text=button_text[:60],
                callback_data=f"findapi_deck:{deck['deck_id']}",
            )
        ])
    total_pages = max(1, (len(decks) + FINDWITH_PER_PAGE - 1) // FINDWITH_PER_PAGE)
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="◀", callback_data=f"findapi_page:{search_token}:{page - 1}"))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="findwith_noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton(text="▶", callback_data=f"findapi_page:{search_token}:{page + 1}"))
        rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _send_deck_photo(
    chat_id: int,
    deck: dict,
    reply_to_message_id: int | None = None,
    user_id: int | None = None,
):
    """Собрать колоду, отправить фото с кнопками «Скопировать», «Скачать», «Сохранить»."""
    deck_code = deck["deck_code"]
    deck_name = deck.get("deck_name")
    image_theme = _image_theme_for_context(user_id, chat_id)
    image_style = image_theme["style"]
    image_background = image_theme["background"]
    image_font = image_theme["font"]
    cache_style = image_theme["cache_style"]
    download_key = uuid.uuid4().hex[:12]
    tmp_path = os.path.join(_TMP_DIR, f"_tmp_dl_{download_key}.jpg")
    cache_entry = await asyncio.to_thread(
        lookup_render_cache,
        deck_code,
        deck_name,
        scope="telegram",
        image_style=cache_style,
    )
    if cache_entry and await asyncio.to_thread(
        materialize_render_cache, cache_entry, tmp_path
    ):
        cost = cache_entry["cost"]
        deck_class = cache_entry.get("deck_class")
        deck_mode = cache_entry.get("deck_mode")
    else:
        try:
            image, cost, deck_class, deck_mode, card_dbf_ids = await create_picture(
                deck_code,
                deck_name=deck_name,
                image_style=image_style,
                image_background=image_background,
                image_font=image_font,
                image_text_size=image_theme["text_size"],
                image_dust_display=image_theme["dust_display"],
                image_class_art=image_theme["class_art"],
                image_layout=image_theme["layout"],
                image_mana_curve=image_theme["mana_curve"],
            )
        except Exception:
            return False
        if not image:
            return False
        image.save(tmp_path, format="JPEG", quality=92, optimize=True)
        cache_entry = await asyncio.to_thread(
            store_render_cache,
            deck_code=deck_code,
            deck_name=deck_name,
            source_path=tmp_path,
            cost=cost,
            deck_class=normalize_deck_class_name(deck_class),
            deck_mode=deck_mode,
            card_dbf_ids=card_dbf_ids,
            image_style=cache_style,
        )
    archetype_name = str(deck.get("archetype_name") or "").strip()
    if archetype_name:
        caption = (
            f"<b>Архетип:</b> {html.escape(archetype_name)}\n"
            f"{build_deck_caption(deck_class, deck_mode or deck.get('deck_mode'), cost)}"
        )
    else:
        archetype_info = await _recognize_archetype_async(deck_code)
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
        deck.get("id"),
        image_theme.get("button_layout"),
    )
    await bot.send_photo(
        chat_id,
        FSInputFile(tmp_path),
        caption=caption,
        reply_markup=action_keyboard,
        reply_to_message_id=reply_to_message_id,
    )
    return True


@router.callback_query(F.data.startswith("save_deck:"))
async def cb_save_deck(callback: types.CallbackQuery):
    """Сохранить колоду в профиль пользователя."""
    raw = callback.data.removeprefix("save_deck:").strip()
    if not raw or not raw.isdigit():
        await callback.answer("Ошибка.", show_alert=True)
        return
    gen_id = int(raw)
    user_id = callback.from_user.id if callback.from_user else 0
    added = save_deck_for_user(user_id, gen_id)
    await callback.answer("Колода сохранена." if added else "Уже в сохранённых.")


@router.message(Command("findwith"))
async def cmd_findwith(message: types.Message):
    """Find current Manacost database decks containing every requested card."""
    text = (message.text or "").strip()
    parts = text.split(maxsplit=1)
    query = (parts[1].strip() if len(parts) > 1 else "").strip()
    if not query:
        await message.answer(
            "Укажите название карты или id.\n"
            "Пример: <code>/findwith Лейрой</code>, <code>/findwith 559</code>\n"
            "Несколько карт: <code>/findwith Карта1; Карта2</code> — найду колоды со всеми картами."
        )
        return

    tokens = [t.strip() for t in re.split(r"[;|\n\r]+", query) if t.strip()]
    if not tokens:
        await message.answer("Укажите хотя бы одну карту.")
        return

    def resolve_all():
        out: list[int] = []
        names: list[str] = []
        seen: set[int] = set()
        unresolved: list[str] = []
        for token in tokens:
            if token.isdigit():
                local_card = get_card_by_dbfid(int(token))
                card_id = str((local_card or {}).get("cardId") or "").strip()
                try:
                    best = (
                        manacost_get_card(card_id, card_format="wild")
                        if card_id
                        else manacost_get_card_by_dbf_id(int(token), card_format="wild")
                    )
                except Exception:
                    best = None
                matches = [best] if best else []
            else:
                matches = manacost_search_cards(token, card_format="wild", limit=5)
            best = next(
                (
                    card
                    for card in matches
                    if str(card.get("dbfId")) == token
                    or str(card.get("id") or "").lower() == token.lower()
                    or _localized(card.get("name")).lower() == token.lower()
                ),
                matches[0] if matches else None,
            )
            if not best or best.get("dbfId") is None:
                unresolved.append(token)
                continue
            dbf_id = int(best["dbfId"])
            if dbf_id not in seen:
                seen.add(dbf_id)
                out.append(dbf_id)
                names.append(_localized(best.get("name")) or token)
        return out, names, unresolved

    status = await message.answer("🔎 Ищу актуальные колоды в базе Манакоста…")
    try:
        dbf_ids, card_names, unresolved = await asyncio.to_thread(resolve_all)
    except Exception as e:
        await status.edit_text(f"Не удалось выполнить поиск по API: {html.escape(str(e)[:240])}")
        return
    if not dbf_ids:
        await status.edit_text(
            "Карты не найдены в базе данных Манакоста. "
            "Проверьте названия или dbfId."
        )
        _log_bot_event("findwith", getattr(message.chat, "type", None), {"query": query, "found": False})
        return

    try:
        decks = await asyncio.to_thread(manacost_find_decks_with_cards, dbf_ids)
    except Exception as e:
        await status.edit_text(f"Не удалось загрузить колоды из API: {html.escape(str(e)[:240])}")
        return
    if not decks:
        suffix = ""
        if unresolved:
            suffix = "\nНе распознаны: " + ", ".join(html.escape(x) for x in unresolved)
        await status.edit_text(
            f"В актуальной базе данных Манакоста нет колод со всеми картами: "
            f"<b>{html.escape(', '.join(card_names))}</b>.{suffix}"
        )
        _log_bot_event("findwith", getattr(message.chat, "type", None), {"query": query, "found": False, "multi": True})
        return
    search_token = manacost_remember_search(decks)
    unresolved_line = ""
    if unresolved:
        unresolved_line = "\n⚠️ Не распознаны: " + ", ".join(html.escape(x) for x in unresolved)
    await status.edit_text(
        f"Найдено актуальных колод: <b>{len(decks)}</b>\n"
        f"Карты: <b>{html.escape(', '.join(card_names))}</b>"
        f"{unresolved_line}\n\nВыберите колоду:",
        reply_markup=_build_findwith_api_keyboard(decks, search_token, 0),
    )
    _log_bot_event(
        "findwith",
        getattr(message.chat, "type", None),
        {"query": query, "found": True, "total": len(decks), "source": "manacost_api"},
    )


@router.callback_query(F.data.startswith("findapi_deck:"))
async def cb_findwith_api_deck(callback: types.CallbackQuery):
    deck_id = callback.data.removeprefix("findapi_deck:").strip()
    await callback.answer("Собираю колоду…")
    try:
        raw_deck = await asyncio.to_thread(manacost_get_deck, deck_id)
    except Exception:
        raw_deck = None
    if not raw_deck:
        await callback.message.answer("Данные колоды устарели. Повторите /findwith.")
        return
    deck = manacost_api_deck_to_bot(raw_deck)
    ok = await _send_deck_photo(
        callback.message.chat.id,
        deck,
        reply_to_message_id=callback.message.message_id,
        user_id=callback.from_user.id,
    )
    if not ok:
        await callback.message.answer("Не удалось собрать изображение колоды.")


@router.callback_query(F.data.startswith("findapi_page:"))
async def cb_findwith_api_page(callback: types.CallbackQuery):
    rest = callback.data.removeprefix("findapi_page:").strip()
    parts = rest.split(":", 1)
    if len(parts) != 2 or not parts[1].lstrip("-").isdigit():
        await callback.answer("Ошибка.", show_alert=True)
        return
    token, page = parts[0], int(parts[1])
    decks = manacost_remembered_search(token)
    if not decks:
        await callback.answer("Поиск устарел. Повторите /findwith.", show_alert=True)
        return
    total_pages = max(1, (len(decks) + FINDWITH_PER_PAGE - 1) // FINDWITH_PER_PAGE)
    if page < 0 or page >= total_pages:
        await callback.answer()
        return
    try:
        await callback.message.edit_reply_markup(
            reply_markup=_build_findwith_api_keyboard(decks, token, page)
        )
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data == "findwith_noop")
async def cb_findwith_noop(callback: types.CallbackQuery):
    """Кнопка «Стр. N/K» — только подтверждение нажатия."""
    await callback.answer()


# ─────────────────────────────────────────────────────────────────────────────
# /compare — сравнение двух колод
# ─────────────────────────────────────────────────────────────────────────────

# Premium-эмодзи редкости для сравнения (по rarityId)
_COMPARE_RARITY_EMOJI_ID = {
    5: "5440706176473917538",  # Легендарная
    4: "5440642671087479069",  # Эпическая
    3: "5438477376210103271",  # Редкая
    2: "5438166708340682874",  # Обычная
    1: "5438166708340682874",  # Бесплатная (как обычная)
}
_COMPARE_RARITY_FALLBACK = {5: "🟠", 4: "🟣", 3: "🔵", 2: "⬜", 1: "⬜"}


def _rarity_tgemoji(rarity_id) -> str:
    """Возвращает <tg-emoji> тег для иконки редкости карты."""
    eid = _COMPARE_RARITY_EMOJI_ID.get(rarity_id, _COMPARE_RARITY_EMOJI_ID[2])
    fb = _COMPARE_RARITY_FALLBACK.get(rarity_id, "⬜")
    return f'<tg-emoji emoji-id="{eid}">{fb}</tg-emoji>'


# Кэш кодов колод для кнопок сравнения: key -> (code1, code2), макс. 200 записей
_compare_cache: dict[str, tuple[str, str]] = {}
_COMPARE_CACHE_MAX = 200


def _build_card_map_for_compare(cards: list) -> dict:
    """Строит словарь {dbfId: (name, qty, rarityId)} из списка карт Blizzard API.

    Пропускает неколлекционируемые карты (Fabled-компаньоны),
    которые идут через sideboard автоматически.
    """
    result: dict = {}
    for c in cards:
        dbf_id = c.get("id")
        if dbf_id is None:
            continue
        # Пропускаем явно неколлекционируемые (Blizzard возвращает collectible=0)
        raw_coll = c.get("collectible")
        if raw_coll is not None and not raw_coll:
            continue
        qty = c.get("deckQuantity") or c.get("quantity") or 1
        try:
            qty = int(qty)
        except (TypeError, ValueError):
            qty = 1
        name = (c.get("name") or "").strip() or f"Карта {dbf_id}"
        rarity = c.get("rarityId") or 2
        if dbf_id in result:
            # Если карта встречается дважды в списке (иногда бывает с дублями API) — суммируем
            prev_name, prev_qty, prev_rarity = result[dbf_id]
            result[dbf_id] = (prev_name, prev_qty + qty, prev_rarity)
        else:
            result[dbf_id] = (name, qty, rarity)
    return result


@router.message(Command("compare"))
async def cmd_compare(message: types.Message):
    """Сравнить две колоды: показывает уникальные карты каждой и общий состав."""
    raw = (message.text or "").strip()

    # Извлекаем оба кода: AA… разделённые пробелом или переносом строки
    codes = [w for w in raw.split() if w.startswith("AA") and len(w) > 10]

    if len(codes) < 2:
        await message.answer(
            "Укажите два кода колоды через пробел или с новой строки:\n\n"
            "<code>/compare AA… AA…</code>\n\n"
            "Код колоды начинается с <code>AA</code> и копируется из Hearthstone "
            "через кнопку «Мой Уголок»."
        )
        return

    code1, code2 = codes[0], codes[1]

    if code1 == code2:
        await message.answer("⚠️ Оба кода одинаковые. Отправьте два <b>разных</b> кода.")
        return

    status_msg = await message.answer("⏳ <b>Декодирую обе колоды…</b>")

    try:
        # Декодируем обе колоды параллельно
        results = await asyncio.gather(
            retrieve_deck(code1),
            retrieve_deck(code2),
            return_exceptions=True,
        )

        errors = []
        for i, res in enumerate(results, 1):
            if isinstance(res, Exception):
                errors.append(f"Колода {i}: {html.escape(str(res)[:150])}")
            elif not isinstance(res, tuple) or res[0] == 0:
                errors.append(f"Колода {i}: не удалось декодировать (проверьте код)")
        if errors:
            await status_msg.edit_text("❌ " + "\n".join(errors))
            return

        (resp1, cls1_id, side1), (resp2, cls2_id, side2) = results

        # Считаем пыль параллельно (включая sideboard для корректного учёта Fabled)
        cost1, cost2 = await asyncio.gather(
            get_cost_of_deck(resp1["cards"] + side1),
            get_cost_of_deck(resp2["cards"] + side2),
        )

        # Метаданные
        class1_name = (resp1.get("class") or {}).get("name") or "Неизвестно"
        class2_name = (resp2.get("class") or {}).get("name") or "Неизвестно"
        _fmt_map = {
            "standard": "Стандарт", "wild": "Вольный",
            "classic": "Классический", "twist": "Потасовка",
        }
        mode1 = _fmt_map.get((resp1.get("format") or "").lower(), "Стандарт")
        mode2 = _fmt_map.get((resp2.get("format") or "").lower(), "Стандарт")

        # Карточные словари (только коллекционируемые карты из основной колоды)
        map1 = _build_card_map_for_compare(resp1["cards"])
        map2 = _build_card_map_for_compare(resp2["cards"])

        ids1, ids2 = set(map1), set(map2)
        only1 = ids1 - ids2
        only2 = ids2 - ids1
        shared = ids1 & ids2
        # Общие карты с разным количеством
        diff_qty = {d for d in shared if map1[d][1] != map2[d][1]}
        pure_shared = shared - diff_qty

        def _sort_key(dbf_id: int, card_map: dict) -> tuple:
            name, qty, rarity = card_map[dbf_id]
            return (-rarity, name.lower())  # Легендарные первыми, потом по алфавиту

        def _card_line(dbf_id: int, card_map: dict) -> str:
            name, qty, rarity = card_map[dbf_id]
            icon = _rarity_tgemoji(rarity)
            prefix = f"{qty}× " if qty > 1 else ""
            return f"{icon} {prefix}{html.escape(name)}"

        # Заголовок с классами
        def _deck_header(cls_name: str, mode: str, cost: int) -> str:
            norm = normalize_deck_class_name(cls_name)
            emoji_id = CLASS_EMOJI_ID_MAP.get(norm or "")
            emoji_str = f'<tg-emoji emoji-id="{emoji_id}">🛡️</tg-emoji> ' if emoji_id else ""
            return f"{emoji_str}<b>{html.escape(cls_name)}</b> · {mode} · {cost:,} пыли"

        lines = [
            "🆚 <b>Сравнение колод</b>\n",
            f"1️⃣  {_deck_header(class1_name, mode1, cost1)}",
            f"2️⃣  {_deck_header(class2_name, mode2, cost2)}",
        ]

        # Разница стоимости
        cost_diff = abs(cost1 - cost2)
        if cost_diff > 0:
            cheaper_num = "1️⃣" if cost1 < cost2 else "2️⃣"
            lines.append(f"💰 Колода {cheaper_num} дешевле на <b>{cost_diff:,}</b> пыли")
        else:
            lines.append("💰 Стоимость одинакова")

        # Полностью идентичные колоды
        if not only1 and not only2 and not diff_qty:
            lines.append("\n✅ Колоды <b>идентичны</b> по составу!")
            cmp_key = uuid.uuid4().hex[:12]
            if len(_compare_cache) >= _COMPARE_CACHE_MAX:
                for old_key in list(_compare_cache.keys())[:20]:
                    _compare_cache.pop(old_key, None)
            _compare_cache[cmp_key] = (code1, code2)
            _cmp_kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🖼 Колода 1", callback_data=f"cmp_img:{cmp_key}:1"),
                InlineKeyboardButton(text="🖼 Колода 2", callback_data=f"cmp_img:{cmp_key}:2"),
            ]])
            await status_msg.edit_text("\n".join(lines), reply_markup=_cmp_kb)
            return

        # ── Только в Колоде 1 ──
        if only1:
            lines.append(f"\n🔴 <b>Только в Колоде 1</b> ({len(only1)} уник.):")
            for dbf in sorted(only1, key=lambda d: _sort_key(d, map1)):
                lines.append("  • " + _card_line(dbf, map1))

        # ── Только в Колоде 2 ──
        if only2:
            lines.append(f"\n🔵 <b>Только в Колоде 2</b> ({len(only2)} уник.):")
            for dbf in sorted(only2, key=lambda d: _sort_key(d, map2)):
                lines.append("  • " + _card_line(dbf, map2))

        # ── Разное количество ──
        if diff_qty:
            lines.append(f"\n🔄 <b>Разное количество</b> ({len(diff_qty)}):")
            for dbf in sorted(diff_qty, key=lambda d: _sort_key(d, map1)):
                name, qty1, rarity = map1[dbf]
                _, qty2, _ = map2[dbf]
                icon = _rarity_tgemoji(rarity)
                lines.append(
                    f"  {icon} {html.escape(name)}: "
                    f"1️⃣ {qty1}× → 2️⃣ {qty2}×"
                )

        # ── Итог ──
        total_diff = len(only1) + len(only2) + len(diff_qty)
        lines.append(f"\n✅ <b>Общих карт:</b> {len(pure_shared)}")
        lines.append(f"📊 Различается <b>{total_diff}</b> позиц{'ия' if total_diff == 1 else 'ии' if 2 <= total_diff <= 4 else 'ий'} из ~30")

        cmp_key = uuid.uuid4().hex[:12]
        if len(_compare_cache) >= _COMPARE_CACHE_MAX:
            for old_key in list(_compare_cache.keys())[:20]:
                _compare_cache.pop(old_key, None)
        _compare_cache[cmp_key] = (code1, code2)
        cmp_keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🖼 Колода 1", callback_data=f"cmp_img:{cmp_key}:1"),
            InlineKeyboardButton(text="🖼 Колода 2", callback_data=f"cmp_img:{cmp_key}:2"),
        ]])
        await status_msg.edit_text("\n".join(lines), reply_markup=cmp_keyboard)

    except Exception as e:
        try:
            await status_msg.edit_text(
                f"❌ Ошибка при сравнении колод: {html.escape(str(e)[:200])}"
            )
        except Exception:
            pass


@router.callback_query(F.data.startswith("cmp_img:"))
async def cb_compare_img(callback: types.CallbackQuery):
    """Кнопка «🖼 Колода N» в сравнении — генерирует и отправляет картинку колоды со стандартными кнопками."""
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Неверный формат.", show_alert=True)
        return
    _, key, deck_num = parts
    codes = _compare_cache.get(key)
    if not codes:
        await callback.answer("Данные истекли. Запустите /compare заново.", show_alert=True)
        return

    code = codes[0] if deck_num == "1" else codes[1]
    label = "Колода 1" if deck_num == "1" else "Колода 2"

    await callback.answer()
    status = await callback.message.answer(f"⏳ Генерирую картинку для {label}…")
    try:
        image_theme = _image_theme_for_context(
            callback.from_user.id,
            callback.message.chat.id,
        )
        image, cost, deck_class, deck_mode, card_dbf_ids = await create_picture(
            code,
            image_style=image_theme["style"],
            image_background=image_theme["background"],
            image_font=image_theme["font"],
            image_text_size=image_theme["text_size"],
            image_dust_display=image_theme["dust_display"],
            image_class_art=image_theme["class_art"],
            image_layout=image_theme["layout"],
            image_mana_curve=image_theme["mana_curve"],
        )
        archetype_info = await _recognize_archetype_async(code)
        caption = f"🖼 <b>{label}</b>\n{_caption_with_archetype(build_deck_caption(deck_class, deck_mode, cost), archetype_info)}"

        # Сохраняем файл для кнопки «Скачать»
        download_key = uuid.uuid4().hex[:12]
        tmp_path = os.path.join(_TMP_DIR, f"_tmp_dl_{download_key}.jpg")
        image.save(tmp_path, format="JPEG", quality=92, optimize=True)
        normalized_class = normalize_deck_class_name(deck_class)
        cache_entry = await asyncio.to_thread(
            store_render_cache,
            deck_code=code,
            deck_name=label,
            source_path=tmp_path,
            cost=cost,
            deck_class=normalized_class,
            deck_mode=deck_mode,
            card_dbf_ids=card_dbf_ids,
            image_style=image_theme["cache_style"],
        )

        # Регистрируем в БД для кнопки «Сохранить»
        gen_id = None
        try:
            _sender = callback.from_user
            if _sender:
                ensure_bot_user(
                    _sender.id,
                    username=_sender.username,
                    first_name=_sender.first_name,
                )
            gen_id = add_generated_with_cards(
                deck_code=code,
                deck_name=label,
                cost=cost,
                filename=f"bot:{uuid.uuid4().hex}",
                dbf_ids=card_dbf_ids,
                source="telegram",
                deck_class=normalized_class,
                deck_mode=deck_mode,
                user_id=_sender.id if _sender else None,
            )
        except Exception as e:
            print(f"[Deckview] cb_compare_img: не удалось сохранить в БД: {e}")

        download_reference = build_download_reference(
            cache_entry,
            fallback_reference=download_key,
        )
        reply_markup = build_deck_action_keyboard(
            code,
            download_reference,
            gen_id,
            image_theme.get("button_layout"),
        )

        buf = BytesIO()
        image.save(buf, format="JPEG", quality=92, optimize=True)
        buf.seek(0)
        await status.delete()
        await callback.message.answer_photo(
            BufferedInputFile(buf.read(), filename=f"deck{deck_num}.jpg"),
            caption=caption,
            reply_markup=reply_markup,
        )
    except Exception as e:
        try:
            await status.edit_text(f"❌ Ошибка генерации: {html.escape(str(e)[:200])}")
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# /meta — текущая мета Hearthstone (Standard / Wild)
# ─────────────────────────────────────────────────────────────────────────────

def _meta_keyboard(active: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=("✅ " if active == 1 else "") + "⚔️ Стандарт",
                callback_data="meta_fmt:1",
            ),
            InlineKeyboardButton(
                text=("✅ " if active == 2 else "") + "🌙 Вольный",
                callback_data="meta_fmt:2",
            ),
        ],
        [
            InlineKeyboardButton(
                text="📊 Все мета-таблицы",
                url="https://arena.hs-manacost.ru/standard/meta",
            )
        ],
    ])


_META_CLASS_ID_TO_RU = {
    "warrior": "Воин",
    "demonhunter": "Охотник на демонов",
    "deathknight": "Рыцарь Смерти",
    "hunter": "Охотник",
    "druid": "Друид",
    "paladin": "Паладин",
    "mage": "Маг",
    "priest": "Жрец",
    "rogue": "Разбойник",
    "shaman": "Шаман",
    "warlock": "Чернокнижник",
}


def _meta_class_icon(class_id: str | None) -> str:
    ru_class = _META_CLASS_ID_TO_RU.get(str(class_id or "").strip().lower())
    emoji_id = CLASS_EMOJI_ID_MAP.get(ru_class or "")
    if not emoji_id:
        return "🃏"
    return f'<tg-emoji emoji-id="{emoji_id}">🛡️</tg-emoji>'


def _format_manacost_meta(
    payload: dict, best_decks: dict[str, dict], deck_meta: dict
) -> str:
    card_format = payload.get("format") or "standard"
    format_label = "Вольный" if card_format == "wild" else "Стандарт"
    period = (payload.get("meta") or {}).get("period") or {}
    patch = period.get("patch") or deck_meta.get("patch") or "—"
    lines = [
        f"📊 <b>Мета Hearthstone — {format_label}</b>",
        f"Патч <b>{html.escape(str(patch))}</b> · Легенда",
        "",
    ]
    ranked_archetypes = rank_meta_by_winrate(
        payload.get("items") or [],
        limit=META_VIEW_LIMIT,
    )
    for index, archetype in enumerate(ranked_archetypes, 1):
        name = archetype.get("localizedName") or archetype.get("name") or "Архетип"
        slug = str(archetype.get("slug") or "")
        metrics = archetype.get("metrics") or {}
        links = archetype.get("links") or {}
        web_url = str(links.get("web") or "https://arena.hs-manacost.ru/standard")
        wr = metrics.get("winratePercent")
        popularity = metrics.get("popularityPercent")
        games = int(metrics.get("games") or 0)
        class_icon = _meta_class_icon(archetype.get("classId"))
        lines.append(
            f'{index}. {class_icon} <b><a href="{html.escape(web_url, quote=True)}">'
            f"{html.escape(str(name))}</a></b>"
        )
        stat_parts = []
        if isinstance(wr, (int, float)):
            stat_parts.append(f"{wr:.1f}% WR")
        if isinstance(popularity, (int, float)):
            stat_parts.append(f"{popularity:.1f}% популярность")
        stat_parts.append(f"{games:,} игр".replace(",", " "))
        lines.append("   " + " · ".join(stat_parts))
        deck = best_decks.get(slug)
        code = str((deck or {}).get("deckCode") or "").strip()
        lines.append(f"   <code>{html.escape(code)}</code>" if code else "   <i>Код пока недоступен</i>")
        lines.append("")
    lines.append("<i>Источник: arena.hs-manacost.ru · данные текущего патча</i>")
    return "\n".join(lines)


async def _load_manacost_meta(format_id: int) -> tuple[str, InlineKeyboardMarkup]:
    now = time.monotonic()
    cached = _META_VIEW_CACHE.get(int(format_id))
    if cached and now - cached[0] < _META_VIEW_CACHE_TTL_SECONDS:
        return cached[1], _meta_keyboard(format_id)

    payload, deck_result = await asyncio.gather(
        asyncio.to_thread(
            manacost_get_meta,
            format_id,
            limit=META_SOURCE_LIMIT,
        ),
        asyncio.to_thread(
            manacost_best_decks_by_archetype,
            format_id,
            (),
        ),
    )
    base_decks, deck_meta = deck_result
    ranked_items = rank_meta_by_winrate(
        payload.get("items") or [],
        limit=META_VIEW_LIMIT,
    )
    payload = {**payload, "items": ranked_items}
    slugs = [
        item.get("slug")
        for item in payload.get("items") or []
        if item.get("slug")
    ]
    missing = [slug for slug in slugs if slug not in base_decks]
    if missing:
        deck_result = await asyncio.to_thread(
            manacost_best_decks_by_archetype,
            format_id,
            slugs,
        )
    best_decks, deck_meta = deck_result
    text = _format_manacost_meta(payload, best_decks, deck_meta)
    _META_VIEW_CACHE[int(format_id)] = (time.monotonic(), text)
    return text, _meta_keyboard(format_id)


_META_VIEW_CACHE_TTL_SECONDS = 5 * 60
_META_VIEW_CACHE: dict[int, tuple[float, str]] = {}
_META_SWITCH_TASKS: dict[tuple[int, int], asyncio.Task] = {}


async def _cancel_previous_meta_switch(
    callback: types.CallbackQuery,
) -> tuple[tuple[int, int], asyncio.Task | None]:
    key = (
        int(callback.message.chat.id),
        int(callback.message.message_id),
    )
    current = asyncio.current_task()
    previous = _META_SWITCH_TASKS.get(key)
    if previous is not None and previous is not current and not previous.done():
        previous.cancel()
        await asyncio.sleep(0)
    if current is not None:
        _META_SWITCH_TASKS[key] = current
    return key, current


def _finish_meta_switch(
    key: tuple[int, int],
    current: asyncio.Task | None,
) -> None:
    if current is not None and _META_SWITCH_TASKS.get(key) is current:
        _META_SWITCH_TASKS.pop(key, None)


@router.message(Command("meta"))
async def cmd_meta(message: types.Message):
    """Show current patch meta from the official Manacost Public API."""
    try:
        text, keyboard = await _load_manacost_meta(1)
        await message.answer(
            text,
            reply_markup=keyboard,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
        _log_bot_event("meta", getattr(message.chat, "type", None), {"format": "standard", "source": "manacost_api"})
    except Exception as e:
        await message.answer(
            f"❌ Не удалось загрузить мету: {html.escape(str(e)[:300])}"
        )


@router.callback_query(F.data.startswith("meta_fmt:"))
async def cb_meta_fmt(callback: types.CallbackQuery):
    """Переключение Стандарт ↔ Вольный в /meta."""
    parts = callback.data.split(":")
    if len(parts) != 2 or not parts[1].isdigit():
        await callback.answer("Неверный формат.", show_alert=True)
        return
    format_id = int(parts[1])
    current_buttons = (
        callback.message.reply_markup.inline_keyboard[0]
        if callback.message.reply_markup
        else []
    )
    if any(
        button.callback_data == callback.data and button.text.startswith("✅")
        for button in current_buttons
    ):
        await callback.answer("Этот формат уже открыт.")
        # A user can click back to the currently displayed format while the
        # opposite format is still loading. That click must cancel the pending
        # switch, otherwise the old request changes the message afterwards.
        switch_key, current_task = await _cancel_previous_meta_switch(callback)
        _finish_meta_switch(switch_key, current_task)
        return
    await callback.answer()
    switch_key, current_task = await _cancel_previous_meta_switch(callback)
    try:
        text, keyboard = await _load_manacost_meta(format_id)
        if _META_SWITCH_TASKS.get(switch_key) is not current_task:
            return
        await callback.message.edit_text(
            text,
            reply_markup=keyboard,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    except asyncio.CancelledError:
        return
    except Exception as e:
        if _META_SWITCH_TASKS.get(switch_key) is not current_task:
            return
        try:
            await callback.message.edit_text(
                f"❌ Ошибка загрузки меты: {html.escape(str(e)[:300])}"
            )
        except Exception:
            pass
    finally:
        _finish_meta_switch(switch_key, current_task)


@router.callback_query(F.data.startswith("archetip_"))
async def cb_archetip_removed(callback: types.CallbackQuery):
    """Закрыть старые inline-кнопки удалённой команды."""
    await callback.answer("Раздел архетипов удалён.", show_alert=True)


@router.message(F.text == "Мой профиль")
async def msg_my_profile(message: types.Message):
    """Кнопка «Мой профиль» в личке — показать сохранённые колоды."""
    await cmd_profile(message)


@router.message(F.text == "🏆 Посмотреть мету")
async def msg_meta_button(message: types.Message):
    """Кнопка «Посмотреть мету» в личке — показать мету Стандарт."""
    await cmd_meta(message)


@router.message(F.text == "О боте")
async def msg_about_bot(message: types.Message):
    """Кнопка «О боте» в личке — описание возможностей для обычного пользователя."""
    text = (
        "Я помогаю с колодами и картами Hearthstone.\n\n"
        "<b>Что умею:</b>\n"
        "• <b>Картинка колоды</b> — отправьте код колоды (начинается с <code>AA</code>).\n"
        "• <b>/meta</b> — текущая мета Стандарт/Вольный с винрейтами и кодами.\n"
        "• <b>/arena</b> — винрейты классов на арене.\n"
        "• <b>/compare</b> — сравнить две колоды: покажу уникальные и общие карты, разницу в пыли.\n"
        "• <b>/card</b> — картинка карты по названию или id (по-русски и по-английски).\n"
        "• <b>/findwith</b> — поиск колод по одной или нескольким картам (через ; или |).\n"
        "• Кнопка <b>«Сохранить»</b> под колодой — добавить её в «Мой профиль».\n"
        "• <b>«Мой профиль»</b> — ваши сохранённые колоды.\n\n"
        "Полный список команд: /help"
    )
    await message.answer(text, reply_markup=MAIN_REPLY_KEYBOARD if message.chat.type == "private" else None)


def _extract_deck_code_from_text(text: str):
    """Extract first token that looks like a deck code (starts with AA)."""
    if not text:
        return None
    for word in text.split():
        if word.startswith("AA") and len(word) > 10:
            return word
    return None


# Максимальная длина названия колоды при вставке из HSReplay и т.п.
MAX_PASTED_DECK_NAME_LENGTH = 50
# Максимум слов в названии — длинные фразы (комментарии) не используем как название
MAX_PASTED_DECK_NAME_WORDS = 5


def _normalize_pasted_deck_name(text_before_code: str) -> str | None:
    """
    Из текста перед кодом колоды (вставка из HSReplay, комментарий и т.п.) извлечь короткое название.
    Игнорирует список карт, метаданные, длинные фразы и комментарии (предложения с запятыми).
    """
    if not text_before_code or not text_before_code.strip():
        return None
    s = text_before_code.strip()
    lines = [ln.strip() for ln in s.splitlines() if ln.strip()]
    for line in lines:
        # Пропускаем метаданные и строки со списком карт
        if line.startswith("# Класс:") or line.startswith("# Формат:") or line == "#":
            continue
        if "x (" in line and ")" in line:  # "2x (1) Название карты"
            continue
        if "скопируйте" in line.lower() or "использовать" in line.lower() or "создайте новую колоду" in line.lower():
            continue
        # Убираем ведущие # и ###
        name = line.lstrip("#").strip()
        if not name or len(name) < 2:
            continue
        # Не используем строку, если это похоже на код (только буквы/цифры без пробелов)
        if len(name) > 20 and " " not in name and name.isalnum():
            continue
        # Длинные фразы с запятой — это комментарий, не название
        if "," in name and len(name) > 30:
            continue
        # Слишком много слов — скорее предложение/комментарий, не заголовок
        if len(name.split()) > MAX_PASTED_DECK_NAME_WORDS:
            continue
        if len(name) > MAX_PASTED_DECK_NAME_LENGTH:
            name = name[:MAX_PASTED_DECK_NAME_LENGTH].rstrip()
        return name
    # Запасной вариант: первая строка только если короткая и без запятой
    if lines:
        first = lines[0].lstrip("#").strip()
        if first and "x (" not in first:
            if "," in first or len(first.split()) > MAX_PASTED_DECK_NAME_WORDS:
                return None
            if len(first) <= MAX_PASTED_DECK_NAME_LENGTH:
                return first
    return None


@router.message(Command('stat'))
async def cmd_stat(message: types.Message):
    """Статистика бота: пользователи, события. Только для админов."""
    if not message.from_user or not is_admin(message.from_user.id):
        await message.answer("⛔ Команда доступна только администраторам.")
        return
    try:
        from deckview.repositories.web import _get_conn
        import psutil, os as _os

        # ── Сбор данных ──────────────────────────────────────────────
        users_total  = get_bot_users_count()
        events_total = get_bot_events_count()
        stats_7      = get_bot_stats_for_dashboard(days=7)
        by_type      = stats_7.get("by_type") or {}
        by_chat      = stats_7.get("by_chat_type") or {}
        by_day       = stats_7.get("by_day") or {}

        now_dt = datetime.now()
        today  = now_dt.strftime("%Y-%m-%d")
        week_ago = (now_dt - timedelta(days=7)).strftime("%Y-%m-%d")

        with _get_conn() as conn:
            new_today = conn.execute(
                "SELECT COUNT(*) FROM bot_users WHERE DATE(first_seen) = ?", (today,)
            ).fetchone()[0]
            new_7d = conn.execute(
                "SELECT COUNT(*) FROM bot_users WHERE DATE(first_seen) >= ?", (week_ago,)
            ).fetchone()[0]
            active_today = conn.execute(
                "SELECT COUNT(*) FROM bot_users WHERE DATE(last_seen) = ?", (today,)
            ).fetchone()[0]
            active_7d = conn.execute(
                "SELECT COUNT(*) FROM bot_users WHERE DATE(last_seen) >= ?", (week_ago,)
            ).fetchone()[0]
            events_today = conn.execute(
                "SELECT COUNT(*) FROM bot_events WHERE DATE(created_at) = ?", (today,)
            ).fetchone()[0]

        # ── Системные метрики ─────────────────────────────────────────
        proc   = psutil.Process(_os.getpid())
        mem_mb = proc.memory_info().rss / 1024 / 1024
        uptime_sec = time.monotonic() - _BOT_START_TIME
        uptime_h   = int(uptime_sec // 3600)
        uptime_m   = int((uptime_sec % 3600) // 60)

        # ── Иконки типов событий ──────────────────────────────────────
        _TYPE_ICON = {
            "deck_code": "🃏",
            "findwith":  "🔍",
            "card":      "🖼",
            "error":     "❗",
            "meta":      "📊",
            "compare":   "⚖️",
            "profile":   "👤",
        }

        # ── Мини-график по дням (последние 7) ─────────────────────────
        sorted_days = sorted(by_day.keys())[-7:]
        day_totals  = {d: sum(by_day[d].values()) for d in sorted_days}
        max_val     = max(day_totals.values(), default=1)
        BAR_CHARS   = "▁▂▃▄▅▆▇█"

        def _bar(val: int) -> str:
            idx = min(int(val / max_val * (len(BAR_CHARS) - 1)), len(BAR_CHARS) - 1)
            return BAR_CHARS[idx]

        chart_line = "  "
        for d in sorted_days:
            chart_line += _bar(day_totals.get(d, 0))
        # подписи крайних дат
        lbl_left  = sorted_days[0][5:]  if sorted_days else ""
        lbl_right = sorted_days[-1][5:] if sorted_days else ""

        # ── Сборка сообщения ──────────────────────────────────────────
        priv  = by_chat.get("private", 0)
        group = by_chat.get("supergroup", 0) + by_chat.get("group", 0)
        events_7d = stats_7.get("total_events", 0)

        lines = [
            "┌─────────────────────────────┐",
            "│  <b>📊 СТАТИСТИКА DECKVIEW</b>      │",
            "└─────────────────────────────┘",
            "",
            "👥 <b>АУДИТОРИЯ</b>",
            f"  Всего пользователей  <b>{users_total:,}</b>".replace(",", " "),
            f"  Новых сегодня        <b>+{new_today}</b>",
            f"  Новых за 7 дней      <b>+{new_7d}</b>",
            f"  Активных сегодня     <b>{active_today}</b>",
            f"  Активных за 7 дней   <b>{active_7d}</b>",
            "",
            "📡 <b>АКТИВНОСТЬ</b>",
            f"  Всего событий        <b>{events_total:,}</b>".replace(",", " "),
            f"  За сегодня           <b>{events_today}</b>",
            f"  За 7 дней            <b>{events_7d}</b>",
            f"  Личные чаты          <b>{priv}</b>",
            f"  Группы               <b>{group}</b>",
            "",
            "🎯 <b>ТОП КОМАНД (7 дней)</b>",
        ]

        for etype, cnt in sorted(by_type.items(), key=lambda x: -x[1]):
            icon  = _TYPE_ICON.get(etype, "•")
            label = etype.replace("_", " ").capitalize()
            bar   = "█" * min(cnt // max(max(by_type.values(), default=1) // 8, 1), 8)
            lines.append(f"  {icon} <b>{label:<18}</b> {cnt:>4}  <code>{bar}</code>")

        if not by_type:
            lines.append("  — нет данных")

        lines += [
            "",
            "📅 <b>ТРЕНД (7 дней)</b>",
            f"  <code>{chart_line}  {lbl_left}…{lbl_right}</code>",
        ]
        for d in sorted_days:
            cnt = day_totals.get(d, 0)
            bar = "█" * min(cnt // max(max_val // 10, 1), 12)
            suffix = " ◀ сегодня" if d == today else ""
            lines.append(f"  <code>{d[5:]}  {bar:<12} {cnt:>4}</code>{suffix}")

        lines += [
            "",
            "⚙️ <b>СЕРВЕР</b>",
            f"  Uptime    <b>{uptime_h}ч {uptime_m}м</b>",
            f"  RAM       <b>{mem_mb:.0f} МБ</b>",
            "",
            "<i>📣 Рассылка: /broadcast &lt;текст&gt;</i>",
        ]

        await message.answer("\n".join(lines))
    except Exception as e:
        await message.answer(f"Ошибка статистики: {html.escape(str(e)[:500])}")


@router.message(Command('broadcast'))
async def cmd_broadcast(message: types.Message):
    """Рассылка сообщения всем, кто хотя бы раз открывал бота (/start в личке). Только для админов."""
    if not message.from_user or not is_admin(message.from_user.id):
        await message.answer("⛔ Команда доступна только администраторам.")
        return
    text = (message.text or "").strip()
    parts = text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer(
            "Использование: /broadcast &lt;текст сообщения&gt;\n"
            "Сообщение будет отправлено всем пользователям, которые нажимали /start в личке."
        )
        return
    broadcast_text = parts[1].strip()
    user_ids = get_all_bot_user_ids()
    if not user_ids:
        await message.answer("Нет пользователей для рассылки (список пуст).")
        return
    status = await message.answer(f"Рассылка по {len(user_ids)} пользователям...")
    sent, failed = 0, 0
    for uid in user_ids:
        try:
            await bot.send_message(uid, broadcast_text)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    try:
        await status.edit_text(f"Готово. Отправлено: {sent}, не доставлено (блок/удаление): {failed}.")
    except Exception:
        pass


@router.message(Command('publish'))
async def cmd_publish(message: types.Message):
    """Publish deck: /publish — взять колоду с HSGuru и опубликовать; /publish AAE... — опубликовать указанный код. Только для админов."""
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Команда доступна только администраторам.")
        return

    text = (message.text or "").strip()
    parts = text.split(maxsplit=1)
    deck_code = None
    deck_name = None
    from_hsguru = False
    if len(parts) > 1:
        rest = parts[1].strip()
        for word in rest.split():
            if word.startswith("AA") and len(word) > 10:
                deck_code = word
                before = rest.find(word)
                if before > 0:
                    deck_name = _normalize_pasted_deck_name(rest[:before]) or None
                break
    if not deck_code and message.reply_to_message and message.reply_to_message.text:
        deck_code = _extract_deck_code_from_text(message.reply_to_message.text)
        if deck_code:
            reply_text = message.reply_to_message.text
            idx = reply_text.find(deck_code)
            if idx > 0:
                deck_name = _normalize_pasted_deck_name(reply_text[:idx]) or None

    if not deck_code:
        # Режим: парсим одну колоду с HSGuru и публикуем
        status_msg = await message.answer("Загружаю HSGuru, ищу новую колоду...")
        try:
            payload = await asyncio.get_running_loop().run_in_executor(None, get_one_new_deck)
        except Exception as e:
            await status_msg.edit_text(f"Ошибка парсинга HSGuru: {e}")
            return
        if not payload:
            await status_msg.edit_text(
                "Нет подходящих колод: все уже опубликованы, с малым числом игр (<20) или дубликаты."
            )
            return
        # Убираем служебные поля перед publish_deck
        deck_code = payload["deck_code"]
        deck_name = payload.get("deck_name", "Deck")
        _norm = payload.pop("_normalized_format", None)
        _deck = payload.pop("_deck", None)
        await status_msg.edit_text(
            f"Найдена колода: {deck_name}\nСтример: {payload.get('streamer')}, игр: {payload.get('wins', 0)}-{payload.get('losses', 0)}\nПубликую..."
        )
        from_hsguru = True
    else:
        status_msg = await message.answer("Публикую колоду...")
        payload = {
            "deck_code": deck_code,
            "deck_name": deck_name or "Deck",
            "streamer": "Неизвестный",
            "wins": 0,
            "losses": 0,
        }

    try:
        result = await publish_deck(
            payload,
            to_telegram=bool(CHANNEL_ID),
            to_wordpress=True,
        )
    except Exception as e:
        await status_msg.edit_text(f"Ошибка: {e}")
        return
    if not result["image_generated"]:
        await status_msg.edit_text(f"Не удалось сгенерировать изображение: {result.get('error', 'Unknown')}")
        return
    if from_hsguru and payload.get("deck_code") and result.get("wordpress_posted"):
        mark_deck_published({**payload, "_normalized_format": _norm or "Стандарт"})
    elif from_hsguru:
        print("[HSGuru] WordPress publish failed, deck remains unmarked for retry")
    lines = ["Готово."]
    if result.get("telegram"):
        lines.append("Опубликовано в Telegram канал.")
    elif CHANNEL_ID:
        lines.append("В Telegram канал не опубликовано (ошибка или CHANNEL_ID не настроен).")
    if result.get("wordpress"):
        lines.append("Опубликовано в WordPress.")
    else:
        lines.append("В WordPress не опубликовано (ошибка или WP не настроен).")
    await status_msg.edit_text("\n".join(lines))
    _log_bot_event("publish", getattr(message.chat, "type", None), {"source": "hsguru" if from_hsguru else "manual"})


@router.message(Command('wp_test'))
async def cmd_wp_test(message: types.Message):
    """Проверка публикации колоды в WordPress: генерирует картинку и создаёт пост. Только для админов. Использование: /wp_test AAE..."""
    if not message.from_user or not is_admin(message.from_user.id):
        await message.answer("⛔ Команда доступна только администраторам.")
        return
    text = (message.text or "").strip()
    deck_code = _extract_deck_code_from_text(text.split(maxsplit=1)[-1] if len(text.split()) > 1 else text)
    if not deck_code:
        await message.answer("Укажите код колоды: /wp_test AAE...")
        return
    status_msg = await message.answer("Проверяю публикацию в WordPress...")
    payload = {"deck_code": deck_code, "deck_name": "Тест WP", "streamer": "Test", "wins": 0, "losses": 0}
    try:
        result = await publish_deck(payload, to_telegram=False, to_wordpress=True)
    except Exception as e:
        await status_msg.edit_text(f"Ошибка: {e}")
        return
    if not result["image_generated"]:
        await status_msg.edit_text(f"Не удалось сгенерировать изображение: {result.get('error', 'Unknown')}")
        return
    if result.get("wordpress"):
        await status_msg.edit_text("✅ Тест WordPress успешен: пост создан.")
    else:
        await status_msg.edit_text("❌ Публикация в WordPress не удалась. Проверьте WP_BASE_URL, WP_USER, WP_APP_PASSWORD и что сайт доступен.")


async def _resolve_discussion_chat_id(channel_chat_id: int) -> int | None:
    """ID группы обсуждения канала для комментариев (Telegram: linked_chat_id). Сначала из конфига, иначе getChat."""
    if DISCUSSION_GROUP_ID is not None:
        return DISCUSSION_GROUP_ID
    try:
        chat = await bot.get_chat(channel_chat_id)
        return getattr(chat, "linked_chat_id", None)
    except Exception:
        return None


def _not_command(message: types.Message) -> bool:
    """Фильтр: сообщение не является командой (не начинается с /), чтобы команды вроде /stat не перехватывались."""
    t = (message.text or "").strip()
    return not t.startswith("/")


@router.channel_post(F.text)
@router.message(F.text, _not_command)
async def find_code_in_message(event: types.Message):
    text = event.text
    # Пост в канале: отвечаем только в комментариях (группа обсуждения), не в самом канале — по документации Telegram
    is_channel_post = getattr(event.chat, "type", None) == "channel"
    discussion_chat_id: int | None = None
    thread_id: int | None = None
    if is_channel_post:
        discussion_chat_id = await _resolve_discussion_chat_id(event.chat.id)
        if discussion_chat_id is None:
            return  # Не отвечаем в канале без группы обсуждения, чтобы не ломать комментарии
        thread_id = event.message_id  # Для комментариев к постам thread_id = message_id поста (документация Telegram)

    async def _send_status(text: str):
        if discussion_chat_id is not None and thread_id is not None:
            return await bot.send_message(
                discussion_chat_id,
                text,
                message_thread_id=thread_id,
                reply_to_message_id=thread_id,
                allow_sending_without_reply=True,
                parse_mode="HTML",
            )
        return await event.reply(text)

    async def _send_error(text: str):
        if discussion_chat_id is not None and thread_id is not None:
            await bot.send_message(
                discussion_chat_id,
                text,
                message_thread_id=thread_id,
                reply_to_message_id=thread_id,
                allow_sending_without_reply=True,
                parse_mode="HTML",
            )
        else:
            await event.reply(text)

    async def _send_photo(photo, caption: str, reply_markup):
        if discussion_chat_id is not None and thread_id is not None:
            await bot.send_photo(
                discussion_chat_id,
                photo=photo,
                caption=caption,
                reply_markup=reply_markup,
                message_thread_id=thread_id,
                reply_to_message_id=thread_id,
                allow_sending_without_reply=True,
                parse_mode="HTML",
            )
        else:
            await event.reply_photo(photo, caption=caption, reply_markup=reply_markup)

    # Ищем все слова, начинающиеся на AA (коды колод)
    words = text.split()
    for i, word in enumerate(words):
        if word.startswith('AA'):
            code_index = text.find(word)
            raw_before = text[:code_index].strip() if code_index > 0 else ""
            deck_name = _normalize_pasted_deck_name(raw_before)

            processing_msg = await _send_status(
                "✨ <b>Собираю колоду</b>\nПодготавливаю данные и картинку..."
            )
            try:
                sender = event.from_user
                target_chat_id = discussion_chat_id if discussion_chat_id is not None else event.chat.id
                image_theme = _image_theme_for_context(
                    sender.id if sender else None,
                    event.chat.id,
                )
                image_style = image_theme["style"]
                target_thread_id = thread_id if thread_id is not None else getattr(event, "message_thread_id", None)
                queued_job_id = enqueue_deck_render(
                    {
                        "deck_code": word,
                        "deck_name": deck_name,
                        "chat_id": target_chat_id,
                        "chat_type": getattr(event.chat, "type", None),
                        "message_thread_id": target_thread_id,
                        "reply_to_message_id": (
                            thread_id if discussion_chat_id is not None else event.message_id
                        ),
                        "status_chat_id": getattr(getattr(processing_msg, "chat", None), "id", target_chat_id),
                        "status_message_id": getattr(processing_msg, "message_id", None),
                        "source": "channel_comment" if is_channel_post else "chat",
                        "image_style": image_style,
                        "image_background": image_theme["background"],
                        "image_font": image_theme["font"],
                        "image_text_size": image_theme["text_size"],
                        "image_dust_display": image_theme["dust_display"],
                        "image_class_art": image_theme["class_art"],
                        "image_layout": image_theme["layout"],
                        "image_mana_curve": image_theme["mana_curve"],
                        "button_layout": image_theme.get(
                            "button_layout", "full"
                        ),
                        "theme_revision": image_theme[
                            "personalization_revision"
                        ],
                        "cache_style": image_theme["cache_style"],
                        "user": {
                            "id": sender.id,
                            "username": sender.username,
                            "first_name": sender.first_name,
                        }
                        if sender
                        else None,
                    }
                )
            except Exception as e:
                print(f"[Deckview queue] enqueue wrapper failed: {e}")
                queued_job_id = None
            if queued_job_id:
                _log_bot_event(
                    "deck_code_queued",
                    getattr(event.chat, "type", None),
                    {"source": "channel_comment" if is_channel_post else "chat", "job_id": queued_job_id},
                )
                return

            try:
                image, cost, deck_class, deck_mode, card_dbf_ids = await create_picture(
                    word,
                    deck_name=deck_name,
                    image_style=image_style,
                    image_background=image_theme["background"],
                    image_font=image_theme["font"],
                    image_text_size=image_theme["text_size"],
                    image_dust_display=image_theme["dust_display"],
                    image_class_art=image_theme["class_art"],
                    image_layout=image_theme["layout"],
                    image_mana_curve=image_theme["mana_curve"],
                )
            except Exception as e:
                try:
                    await processing_msg.edit_text("❌ Не удалось собрать колоду.")
                except Exception:
                    pass
                err_text = str(e)
                err_lower = err_text.lower()
                if "invalid rune costs" in err_lower:
                    await _send_error(
                        "⚠️ Этот код колоды содержит недействительные руны.\n"
                        "Скорее всего, колода создана в старом патче — обновите её в клиенте и скопируйте код заново."
                    )
                elif "invalid hero" in err_lower:
                    await _send_error(
                        "⚠️ Герой в этом коде колоды больше не существует в API.\n"
                        "Возможно, колода устарела — попробуйте обновить её в клиенте Hearthstone."
                    )
                elif "unsupported string format or version" in err_lower:
                    await _send_error(
                        "⚠️ Этот формат кода колоды не поддерживается.\n"
                        "Убедитесь, что код скопирован из актуальной версии Hearthstone."
                    )
                elif "BATTLE_NET_TOKEN" in err_text or "401" in err_text or "403" in err_text:
                    await _send_error(
                        "Ошибка доступа к Blizzard API: токен недействителен или истёк. Обратитесь к администратору."
                    )
                else:
                    await _send_error(f"Ошибка при создании картинки: {err_text[:400]}")
                _log_bot_event("error", getattr(event.chat, "type", None), {"context": "deck_code", "error": err_text[:300]})
                return

            if not image:
                try:
                    await processing_msg.delete()
                except Exception:
                    pass
                await _send_error(
                    "Не удалось расшифровать колоду по этому коду. "
                    "Проверьте код или настройки бота (BATTLE_NET_TOKEN)."
                )
                _log_bot_event("error", getattr(event.chat, "type", None), {"context": "deck_code", "error": "image None"})
                return

            normalized_class = normalize_deck_class_name(deck_class)
            archetype_info = await _recognize_archetype_async(word)
            caption = _caption_with_archetype(
                build_deck_caption(deck_class, deck_mode, cost),
                archetype_info,
            )

            download_key = uuid.uuid4().hex[:12]
            tmp_path = os.path.join(_TMP_DIR, f"_tmp_dl_{download_key}.jpg")
            image.save(tmp_path, format="JPEG", quality=92, optimize=True)
            cache_entry = await asyncio.to_thread(
                store_render_cache,
                deck_code=word,
                deck_name=deck_name,
                source_path=tmp_path,
                cost=cost,
                deck_class=normalized_class,
                deck_mode=deck_mode,
                card_dbf_ids=card_dbf_ids,
                image_style=image_theme["cache_style"],
            )
            gen_id = None
            try:
                _sender = event.from_user
                _sender_id = _sender.id if _sender else None
                if _sender:
                    ensure_bot_user(
                        _sender.id,
                        username=_sender.username,
                        first_name=_sender.first_name,
                    )
                bot_filename = f"bot:{uuid.uuid4().hex}"
                gen_id = add_generated_with_cards(
                    deck_code=word,
                    deck_name=deck_name,
                    cost=cost,
                    filename=bot_filename,
                    dbf_ids=card_dbf_ids,
                    source="telegram",
                    deck_class=normalized_class,
                    deck_mode=deck_mode,
                    user_id=_sender_id,
                )
            except Exception as e:
                print(f"[Deckview] Не удалось сохранить колоду в БД: {e}")
            download_reference = build_download_reference(
                cache_entry,
                fallback_reference=download_key,
            )
            reply_markup = build_deck_action_keyboard(
                word,
                download_reference,
                gen_id,
                image_theme.get("button_layout"),
            )

            try:
                await processing_msg.delete()
            except Exception:
                pass
            await _send_photo(FSInputFile(tmp_path), caption=caption, reply_markup=reply_markup)
            _log_bot_event(
                "deck_code",
                getattr(event.chat, "type", None),
                {"source": "channel_comment" if is_channel_post else "chat"},
            )


@router.callback_query(F.data.startswith("open_pack:"))
async def cb_download_as_file(callback: types.CallbackQuery):
    """По нажатию «Скачать как файл» отправляем изображение документом."""
    key = callback.data.removeprefix("open_pack:")
    # Both legacy random keys and persistent base64url cache references are
    # path-safe and fit inside Telegram's 64-byte callback_data limit.
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,43}", key or ""):
        await callback.answer("Неверный запрос.", show_alert=True)
        return
    await callback.answer()
    path = await asyncio.to_thread(resolve_cached_download, key)
    if path is None:
        legacy_path = os.path.join(_TMP_DIR, f"_tmp_dl_{key}.jpg")
        path = legacy_path if os.path.exists(legacy_path) else None
    try:
        if path is not None:
            await callback.message.answer_document(
                FSInputFile(path, filename="deck.jpg"),
                caption="Изображение колоды",
            )
            return

        # Compatibility for buttons already sent before persistent references:
        # Telegram retains the displayed photo even after our 2-hour temp file
        # is cleaned. Download that largest variant and return it as a document.
        photos = getattr(callback.message, "photo", None) or []
        largest_photo = photos[-1] if photos else None
        file_id = getattr(largest_photo, "file_id", None)
        if not file_id:
            await callback.message.answer(
                "Не удалось восстановить файл. Отправьте код колоды ещё раз."
            )
            return
        telegram_file = await bot.get_file(file_id)
        buffer = BytesIO()
        await bot.download(telegram_file, destination=buffer)
        payload = buffer.getvalue()
        if not payload:
            raise ValueError("Telegram вернул пустой файл")
        await callback.message.answer_document(
            BufferedInputFile(payload, filename="deck.jpg"),
            caption="Изображение колоды",
        )
    except Exception as e:
        await callback.message.answer(
            f"Не удалось скачать файл: {html.escape(str(e)[:180])}"
        )


async def _edit_profile_message(
    callback: types.CallbackQuery,
    *,
    notice: str | None = None,
) -> None:
    user_id = callback.from_user.id
    decks = get_saved_decks_for_user(user_id, limit=20)
    identity = get_manacost_identity(user_id)
    text, buttons = _build_profile_display(decks, identity)
    if notice:
        text = f"{html.escape(notice)}\n\n{text}"
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


def _manacost_auth_keyboard(flow: dict) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🌐 Открыть Manacost",
                    url=str(flow["verification_uri_complete"]),
                )
            ],
            [
                InlineKeyboardButton(
                    text="📋 Скопировать код",
                    copy_text=CopyTextButton(text=str(flow["user_code"])),
                ),
                InlineKeyboardButton(
                    text="✅ Проверить вход",
                    callback_data="profile_manacost_check",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="← Отмена",
                    callback_data="profile_manacost_cancel",
                )
            ],
        ]
    )


@router.callback_query(F.data == "profile_manacost_login")
async def cb_profile_manacost_login(callback: types.CallbackQuery):
    if callback.message.chat.type != "private":
        await callback.answer(
            "Авторизация доступна только в личном чате с ботом.",
            show_alert=True,
        )
        return
    try:
        flow = await asyncio.to_thread(manacost_start_device_authorization)
    except (ManacostIdentityError, OSError):
        await callback.answer(
            "Не удалось открыть вход Manacost ID. Попробуйте позже.",
            show_alert=True,
        )
        return
    except Exception as exc:
        print(
            "[Deckview] Manacost ID start failed: "
            f"{type(exc).__name__}"
        )
        await callback.answer(
            "Не удалось открыть вход Manacost ID. Попробуйте позже.",
            show_alert=True,
        )
        return
    now = time.monotonic()
    flow["expires_at"] = now + int(flow["expires_in"])
    flow["next_check_at"] = now
    _MANACOST_AUTH_PENDING[callback.from_user.id] = flow
    await callback.message.edit_text(
        "🔐 <b>Вход через Manacost ID</b>\n\n"
        "1. Откройте официальный сайт кнопкой ниже.\n"
        "2. Войдите в Manacost и подтвердите подключение.\n"
        "3. Вернитесь сюда и нажмите «Проверить вход».\n\n"
        f"Одноразовый код: <code>{html.escape(str(flow['user_code']))}</code>\n"
        "Код действует 10 минут. Пароль вводится только на сайте Manacost.",
        reply_markup=_manacost_auth_keyboard(flow),
    )
    await callback.answer()


@router.callback_query(F.data == "profile_manacost_check")
async def cb_profile_manacost_check(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    flow = _MANACOST_AUTH_PENDING.get(user_id)
    if not flow or time.monotonic() >= float(flow.get("expires_at") or 0):
        _MANACOST_AUTH_PENDING.pop(user_id, None)
        await callback.answer(
            "Код истёк. Начните вход заново.",
            show_alert=True,
        )
        await _edit_profile_message(callback)
        return
    now = time.monotonic()
    next_check_at = float(flow.get("next_check_at") or 0)
    if now < next_check_at:
        wait_seconds = max(1, int(next_check_at - now + 0.999))
        await callback.answer(
            f"Подождите {wait_seconds} сек. перед следующей проверкой.",
            show_alert=True,
        )
        return
    flow["next_check_at"] = now + int(flow.get("interval") or 5)

    tokens = None
    try:
        tokens = await asyncio.to_thread(
            manacost_exchange_device_code,
            str(flow["device_code"]),
        )
        profile = await asyncio.to_thread(
            manacost_get_authorized_profile,
            str(tokens["access_token"]),
        )
        save_manacost_identity(user_id, profile)
    except AuthorizationPending:
        await callback.answer(
            "Сайт ещё не подтвердил вход. Завершите авторизацию и попробуйте снова.",
            show_alert=True,
        )
        return
    except ValueError:
        _MANACOST_AUTH_PENDING.pop(user_id, None)
        await callback.answer(
            "Этот Manacost ID уже привязан к другому пользователю Telegram.",
            show_alert=True,
        )
        return
    except (ManacostIdentityError, OSError) as exc:
        if isinstance(exc, ManacostIdentityError) and exc.code in {
            "access_denied",
            "expired_token",
            "invalid_grant",
        }:
            _MANACOST_AUTH_PENDING.pop(user_id, None)
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception as exc:
        print(
            "[Deckview] Manacost ID completion failed: "
            f"{type(exc).__name__}"
        )
        await callback.answer(
            "Не удалось завершить вход. Попробуйте ещё раз.",
            show_alert=True,
        )
        return
    finally:
        if tokens and tokens.get("refresh_token"):
            await asyncio.to_thread(
                manacost_revoke_refresh_token,
                str(tokens["refresh_token"]),
            )

    _MANACOST_AUTH_PENDING.pop(user_id, None)
    await _edit_profile_message(
        callback,
        notice="✅ Manacost ID успешно подключён.",
    )
    await callback.answer("Manacost ID подключён.")


@router.callback_query(F.data == "profile_manacost_cancel")
async def cb_profile_manacost_cancel(callback: types.CallbackQuery):
    _MANACOST_AUTH_PENDING.pop(callback.from_user.id, None)
    await _edit_profile_message(callback)
    await callback.answer("Авторизация отменена.")


@router.callback_query(F.data == "profile_manacost_unlink")
async def cb_profile_manacost_unlink(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "🔓 <b>Отвязать Manacost ID?</b>\n\n"
        "Сохранённые колоды останутся в профиле. Подключить аккаунт "
        "снова можно будет в любой момент.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Да, отвязать",
                        callback_data="profile_manacost_unlink_confirm",
                    ),
                    InlineKeyboardButton(
                        text="Отмена",
                        callback_data="profile_manacost_cancel",
                    ),
                ]
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "profile_manacost_unlink_confirm")
async def cb_profile_manacost_unlink_confirm(callback: types.CallbackQuery):
    removed = remove_manacost_identity(callback.from_user.id)
    _MANACOST_AUTH_PENDING.pop(callback.from_user.id, None)
    await _edit_profile_message(
        callback,
        notice=(
            "Manacost ID отвязан."
            if removed
            else "Manacost ID уже был отвязан."
        ),
    )
    await callback.answer("Готово.")


@router.callback_query(F.data.startswith("profile_remove:"))
async def cb_profile_remove(callback: types.CallbackQuery):
    """Удалить колоду из профиля и обновить список."""
    raw = callback.data.removeprefix("profile_remove:").strip()
    if not raw or not raw.isdigit():
        await callback.answer("Ошибка.", show_alert=True)
        return
    gen_id = int(raw)
    user_id = callback.from_user.id if callback.from_user else 0
    removed = remove_saved_deck(user_id, gen_id)
    if not removed:
        await callback.answer("Колода не найдена в профиле.", show_alert=True)
        return
    decks = get_saved_decks_for_user(user_id, limit=20)
    if not decks:
        await _edit_profile_message(callback)
        await callback.answer("Колода удалена из профиля.")
        return
    identity = (
        get_manacost_identity(user_id)
        if callback.message.chat.type == "private"
        else None
    )
    text, buttons = _build_profile_display(
        decks,
        identity,
        show_manacost=callback.message.chat.type == "private",
    )
    try:
        await callback.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )
    except Exception:
        pass
    await callback.answer("Колода удалена из профиля.")


@router.callback_query(F.data.startswith("profile_deck:"))
async def cb_profile_deck(callback: types.CallbackQuery):
    """По нажатию на колоду из профиля — отправить её изображение."""
    raw = callback.data.removeprefix("profile_deck:").strip()
    if not raw or not raw.isdigit():
        await callback.answer("Ошибка.", show_alert=True)
        return
    gen_id = int(raw)
    deck = get_deck_by_id(gen_id)
    if not deck:
        await callback.answer("Колода не найдена.", show_alert=True)
        return
    await callback.answer("Собираю колоду...")
    ok = await _send_deck_photo(
        callback.message.chat.id,
        deck,
        reply_to_message_id=callback.message.message_id,
        user_id=callback.from_user.id,
    )
    if not ok:
        await callback.message.answer("Не удалось собрать изображение колоды.")


async def _publish_hsguru_payload(payload: dict, *, to_telegram: bool) -> bool:
    deck_code = payload["deck_code"]
    deck_name = payload.get("deck_name", "Deck")
    _norm = payload.pop("_normalized_format", None)
    payload.pop("_deck", None)
    print(f"[Periodic publish] Публикуем: {deck_name!r} ({deck_code[:20]}...)")
    result = await publish_deck(
        payload,
        to_telegram=to_telegram,
        to_wordpress=True,
    )
    tg_sent = result.get("telegram_sent", False)
    wp_posted = result.get("wordpress_posted", False)
    if result.get("image_generated"):
        if wp_posted:
            mark_deck_published({**payload, "deck_code": deck_code, "_normalized_format": _norm or "Стандарт"})
        else:
            print(f"[Periodic publish] WordPress не принял {deck_name!r}; колода не помечена seen и будет повторена")
        tg_ok = "✓" if tg_sent else "✗"
        wp_ok = "✓" if wp_posted else "✗"
        print(f"[Periodic publish] Готово: {deck_name!r} | TG:{tg_ok} WP:{wp_ok}")
        add_publish_log(
            deck_name=deck_name,
            deck_class=payload.get("deck_class"),
            deck_mode=_norm or payload.get("deck_mode"),
            deck_code=deck_code,
            telegram_sent=tg_sent,
            wordpress_posted=wp_posted,
        )
        return bool(wp_posted)

    err_msg = result.get("error") or "Изображение не создано"
    print(f"[Periodic publish] Изображение не создано для {deck_name!r}: {err_msg}")
    _BAD_DECK_ERRORS = (
        "invalid rune costs",
        "invalid hero",
        "unsupported string format or version",
    )
    if any(kw in err_msg.lower() for kw in _BAD_DECK_ERRORS):
        mark_deck_published({**payload, "deck_code": deck_code, "_normalized_format": _norm or "Стандарт"})
        print(f"[Periodic publish] Колода {deck_name!r} помечена как пропущенная (невалидный код)")
    add_publish_log(
        deck_name=deck_name,
        deck_class=payload.get("deck_class"),
        deck_mode=_norm or payload.get("deck_mode"),
        deck_code=deck_code,
        telegram_sent=False,
        wordpress_posted=False,
        error=err_msg,
    )
    return False


async def _periodic_publish_task():
    """Периодически публикует все новые HSGuru-колоды, подходящие под фильтр ленты."""
    if HSGURU_INTERVAL_SECONDS <= 0:
        return
    interval_sec = int(HSGURU_INTERVAL_SECONDS)
    loop = asyncio.get_running_loop()
    print(f"[Periodic publish] Запущен, интервал {interval_sec}с")
    while True:
        try:
            queued_cycle_id = enqueue_hsguru_cycle(to_telegram=bool(CHANNEL_ID))
            if queued_cycle_id:
                print(f"[Periodic publish] HSGuru cycle queued: {queued_cycle_id}")
            else:
                print("[Periodic publish] Получаем новые колоды с HSGuru...")
                payloads = await loop.run_in_executor(None, get_new_decks)
                if not payloads:
                    print("[Periodic publish] Нет новых колод, пропускаем")
                else:
                    print(f"[Periodic publish] Под фильтр подошло новых колод: {len(payloads)}")
                    for payload in payloads:
                        queued_job_id = enqueue_hsguru_publish(payload, to_telegram=bool(CHANNEL_ID))
                        if queued_job_id:
                            print(f"[Periodic publish] HSGuru publish queued: {queued_job_id}")
                        else:
                            await _publish_hsguru_payload(payload, to_telegram=bool(CHANNEL_ID))
        except Exception as e:
            print(f"[Periodic publish] Ошибка: {e}\n{traceback.format_exc()}")
            try:
                add_publish_log(
                    deck_name=None, deck_class=None, deck_mode=None, deck_code=None,
                    telegram_sent=False, wordpress_posted=False,
                    error=str(e)[:500],
                )
            except Exception:
                pass
        await asyncio.sleep(interval_sec)


_RUNTIME_STARTED = False
_ROUTER_INCLUDED = False


def _include_router_once() -> None:
    global _ROUTER_INCLUDED
    if _ROUTER_INCLUDED:
        return
    # Migrated feature routers go first so legacy catch-all message handlers
    # cannot consume their commands.  New features must be registered in the
    # package composition root instead of growing this module.
    dp.include_router(
        create_modular_router(
            is_admin=is_admin, started_at=_BOT_START_TIME,
            image_theme_provider=_image_theme_for_context,
        )
    )
    dp.include_router(router)
    _ROUTER_INCLUDED = True


async def _start_runtime_tasks() -> None:
    global _RUNTIME_STARTED
    if _RUNTIME_STARTED:
        return
    init_ratings_db()
    init_web_db()
    # Удаляем устаревшие tmp-файлы при старте (только старше TTL, свежие оставляем)
    n = _cleanup_tmp_files()
    if n:
        print(f"[Deckview] Startup: удалено {n} старых tmp-файлов")
    await _set_bot_commands()
    if HSGURU_INTERVAL_SECONDS > 0:
        asyncio.create_task(_periodic_publish_task())
    asyncio.create_task(_periodic_cleanup_task())
    asyncio.create_task(_prewarm_manacost_cache())
    _RUNTIME_STARTED = True


async def main():
    _include_router_once()
    await _start_runtime_tasks()
    try:
        await bot.delete_webhook(drop_pending_updates=False)
    except Exception as e:
        print(f"[Deckview] Не удалось удалить webhook перед polling: {e}")
    print("[Deckview] Starting in polling mode")
    await dp.start_polling(bot)


async def _set_webhook_after_startup() -> None:
    await asyncio.sleep(0.5)
    try:
        await bot.set_webhook(
            DECKVIEW_WEBHOOK_URL,
            secret_token=DECKVIEW_WEBHOOK_SECRET,
            drop_pending_updates=DECKVIEW_WEBHOOK_DROP_PENDING_UPDATES,
            # Telegram retains the previous subscription when this parameter
            # is omitted. Explicitly include membership updates so adding the
            # bot through "Мои чаты" always creates the managed-chat record.
            allowed_updates=dp.resolve_used_update_types(),
        )
        print(f"[Deckview] Webhook registered: {DECKVIEW_WEBHOOK_URL}")
    except Exception as e:
        print(f"[Deckview] Failed to register webhook {DECKVIEW_WEBHOOK_URL}: {e}")


async def _on_webhook_startup(app: web.Application) -> None:
    await _start_runtime_tasks()
    asyncio.create_task(_set_webhook_after_startup())
    print(f"[Deckview] Starting in webhook mode at {DECKVIEW_WEBHOOK_HOST}:{DECKVIEW_WEBHOOK_PORT}{DECKVIEW_WEBHOOK_PATH}")


async def _on_webhook_shutdown(app: web.Application) -> None:
    try:
        await bot.session.close()
    except Exception:
        pass


def run_webhook() -> None:
    _include_router_once()
    if not DECKVIEW_WEBHOOK_SECRET or len(DECKVIEW_WEBHOOK_SECRET) < 32:
        raise RuntimeError(
            "DECKVIEW_WEBHOOK_SECRET must be configured and contain at least 32 characters"
        )
    app = web.Application(client_max_size=DECKVIEW_WEBHOOK_MAX_BODY_BYTES)
    handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=DECKVIEW_WEBHOOK_SECRET,
    )
    handler.register(app, path=DECKVIEW_WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)
    app.on_startup.append(_on_webhook_startup)
    app.on_shutdown.append(_on_webhook_shutdown)
    web.run_app(
        app,
        host=DECKVIEW_WEBHOOK_HOST,
        port=DECKVIEW_WEBHOOK_PORT,
        access_log=None,
    )


def run() -> None:
    """Run the packaged bot while preserving the historical project cwd."""

    os.chdir(PROJECT_ROOT)
    if DECKVIEW_UPDATE_MODE == "webhook":
        run_webhook()
    else:
        asyncio.run(main())


if __name__ == "__main__":
    run()
