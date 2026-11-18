import os

from dotenv import load_dotenv

load_dotenv(override=True)

# Bot token (Deckview originally used TOKEN; tg-manacost-bot uses BOT_TOKEN)
# strip() removes accidental whitespace/CRLF from environment values.
TOKEN = (os.getenv("TOKEN") or os.getenv("BOT_TOKEN") or "").strip()
TELEGRAM_API_BASE_URL = os.getenv("TELEGRAM_API_BASE_URL", "").strip().rstrip("/")
DECKVIEW_SOURCE_CODE_URL = (
    os.getenv(
        "DECKVIEW_SOURCE_CODE_URL",
        "https://github.com/Manacost-Labs/Deckview-TG",
    ).strip()
    or "https://github.com/Manacost-Labs/Deckview-TG"
)

# Telegram updates delivery. `webhook` is intended for the local telegram-bot-api
# instance on 127.0.0.1; `polling` remains a fast rollback mode.
DECKVIEW_UPDATE_MODE = os.getenv("DECKVIEW_UPDATE_MODE", "polling").strip().lower()
DECKVIEW_WEBHOOK_HOST = os.getenv("DECKVIEW_WEBHOOK_HOST", "127.0.0.1").strip()
DECKVIEW_WEBHOOK_PORT = int(os.getenv("DECKVIEW_WEBHOOK_PORT", "8792") or "8792")
DECKVIEW_WEBHOOK_PATH = os.getenv("DECKVIEW_WEBHOOK_PATH", "/deckview/webhook").strip() or "/deckview/webhook"
if not DECKVIEW_WEBHOOK_PATH.startswith("/"):
    DECKVIEW_WEBHOOK_PATH = f"/{DECKVIEW_WEBHOOK_PATH}"
DECKVIEW_WEBHOOK_URL = (
    os.getenv("DECKVIEW_WEBHOOK_URL", "").strip()
    or f"http://{DECKVIEW_WEBHOOK_HOST}:{DECKVIEW_WEBHOOK_PORT}{DECKVIEW_WEBHOOK_PATH}"
)
DECKVIEW_WEBHOOK_SECRET = os.getenv("DECKVIEW_WEBHOOK_SECRET", "").strip() or None
DECKVIEW_WEBHOOK_DROP_PENDING_UPDATES = os.getenv(
    "DECKVIEW_WEBHOOK_DROP_PENDING_UPDATES",
    "0",
).strip().lower() in ("1", "true", "yes", "on")
DECKVIEW_WEBHOOK_MAX_BODY_BYTES = max(
    64 * 1024,
    min(
        1024 * 1024,
        int(os.getenv("DECKVIEW_WEBHOOK_MAX_BODY_BYTES", str(256 * 1024)) or 256 * 1024),
    ),
)

# Optional Redis/RQ queue for heavy bot jobs. If disabled or unavailable, handlers
# keep the previous synchronous behavior.
DECKVIEW_QUEUE_ENABLED = os.getenv("DECKVIEW_QUEUE_ENABLED", "0").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
DECKVIEW_REDIS_URL = os.getenv("DECKVIEW_REDIS_URL", "redis://127.0.0.1:6379/2").strip()
DECKVIEW_QUEUE_NAME = os.getenv("DECKVIEW_QUEUE_NAME", "deckview").strip() or "deckview"
DECKVIEW_API_QUEUE_NAME = (
    os.getenv("DECKVIEW_API_QUEUE_NAME", DECKVIEW_QUEUE_NAME).strip()
    or DECKVIEW_QUEUE_NAME
)
DECKVIEW_QUEUE_JOB_TIMEOUT = int(os.getenv("DECKVIEW_QUEUE_JOB_TIMEOUT", "300") or "300")

BATTLE_NET_TOKEN = os.getenv("BATTLE_NET_TOKEN")

FOLDER = "cards/"

# Telegram channel for deck publishing (optional)
CHANNEL_ID = os.getenv("CHANNEL_ID", "").strip()
# Optional: use a different bot token for posting to channel
CHANNEL_BOT_TOKEN = os.getenv("CHANNEL_BOT_TOKEN", "").strip() or TOKEN
# Стиль картинок автопубликации (лента hs-manacost.ru и канал): parchment или classic.
PUBLISH_IMAGE_STYLE = os.getenv("DECKVIEW_PUBLISH_IMAGE_STYLE", "parchment").strip().lower()
if PUBLISH_IMAGE_STYLE not in {"classic", "parchment"}:
    PUBLISH_IMAGE_STYLE = "parchment"
# ID группы обсуждения канала (для комментариев к постам). Если не задан — берётся из getChat(channel).linked_chat_id
_discussion = os.getenv("DISCUSSION_GROUP_ID", "").strip()
try:
    DISCUSSION_GROUP_ID = int(_discussion) if _discussion else None
except ValueError:
    DISCUSSION_GROUP_ID = None

# WordPress integration (optional)
WP_BASE_URL = (os.getenv("WP_BASE_URL", "") or "").rstrip("/")
WP_USER = os.getenv("WP_USER", "")
WP_APP_PASSWORD = os.getenv("WP_APP_PASSWORD", "")
WP_UPLOAD_ENABLED = os.getenv("WP_UPLOAD_ENABLED", "1") == "1"
# When true, WordPress is expected to create/sideload deck images itself.
# Keep this opt-in only: the hs-manacost feed renders featured_media, so
# skipping the upload leaves new deck cards without images.
WP_USE_KOLODAHS_IMAGE = os.getenv("WP_USE_KOLODAHS_IMAGE", "0").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)

# Admin user IDs (comma-separated in .env). Only they can use /publish and /wp_test.
_admin_ids_str = os.getenv("ADMIN_IDS", "")
DEFAULT_ADMIN_IDS = {883935723}
ADMIN_IDS = sorted(
    DEFAULT_ADMIN_IDS
    | {int(x.strip()) for x in _admin_ids_str.split(",") if x.strip().isdigit()}
)

# HSGuru: URL and path to store published deck codes (for /publish from HSGuru)
HSGURU_URL = os.getenv("HSGURU_URL", "https://www.hsguru.com/streamer-decks")
HSGURU_FALLBACK_URLS = tuple(
    url.strip()
    for url in os.getenv("HSGURU_FALLBACK_URLS", "https://api.hsguru.com/").split(",")
    if url.strip()
)
HSGURU_USER_AGENT = os.getenv(
    "HSGURU_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
).strip()
HSGURU_COOKIES = os.getenv("HSGURU_COOKIES", "").strip()
HSGURU_CF_CLEARANCE = os.getenv("HSGURU_CF_CLEARANCE", "").strip()
HSGURU_PROXY_URLS = tuple(
    proxy.strip()
    for proxy in os.getenv("HSGURU_PROXY_URLS", "").split(",")
    if proxy.strip()
)
HSGURU_META_URL = os.getenv("HSGURU_META_URL", "https://www.hsguru.com/meta").strip()
HSGURU_SEEN_PATH = os.getenv("HSGURU_SEEN_PATH", "cache/hsguru_seen.json")
HSGURU_PUBLISH_BATCH_LIMIT = max(0, int(os.getenv("HSGURU_PUBLISH_BATCH_LIMIT", "0") or "0"))
HSGURU_MIN_GAMES = max(0, int(os.getenv("HSGURU_MIN_GAMES", "15") or "15"))
HSGURU_STREAMER_PAGE_LIMIT = max(20, int(os.getenv("HSGURU_STREAMER_PAGE_LIMIT", "100") or "100"))
HSGURU_STREAMER_OFFSETS = tuple(
    int(value.strip())
    for value in os.getenv("HSGURU_STREAMER_OFFSETS", "0,50,100,150,200,250").split(",")
    if value.strip().isdigit()
)
HSGURU_FETCH_RETRIES = max(1, int(os.getenv("HSGURU_FETCH_RETRIES", "2") or "2"))
HSGURU_FETCH_BACKOFF_SECONDS = max(
    0.0,
    float(os.getenv("HSGURU_FETCH_BACKOFF_SECONDS", "1.5").replace(",", ".")),
)
HSGURU_MIN_PARSED_DECKS = max(0, int(os.getenv("HSGURU_MIN_PARSED_DECKS", "20") or "20"))
HSGURU_STATUS_PATH = os.getenv("HSGURU_STATUS_PATH", "cache/hsguru_status.json")
HSGURU_LOCK_PATH = os.getenv("HSGURU_LOCK_PATH", "cache/hsguru_fetch.lock")
HSGURU_FETCH_TIMEOUT = max(
    5,
    float(os.getenv("HSGURU_FETCH_TIMEOUT", "30").replace(",", ".")),
)
HSGURU_BROWSER_FALLBACK = os.getenv("HSGURU_BROWSER_FALLBACK", "1").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
HSGURU_BROWSER_PATH = os.getenv("HSGURU_BROWSER_PATH", os.getenv("HSGURU_ARCHETYPE_BROWSER_PATH", "/usr/bin/chromium")).strip()
HSGURU_BROWSER_TIMEOUT = max(
    10,
    float(os.getenv("HSGURU_BROWSER_TIMEOUT", "35").replace(",", ".")),
)
HSGURU_ARCHETYPE_API_URL = os.getenv(
    "HSGURU_ARCHETYPE_API_URL",
    "https://api.hsguru.com/api/deck-info",
).rstrip("/")
HSGURU_ARCHETYPE_CACHE_PATH = os.getenv(
    "HSGURU_ARCHETYPE_CACHE_PATH",
    "cache/hsguru_archetype_cache.json",
)
HSGURU_ARCHETYPE_CACHE_HOURS = max(
    0,
    float(os.getenv("HSGURU_ARCHETYPE_CACHE_HOURS", "168").replace(",", ".")),
)
HSGURU_ARCHETYPE_TIMEOUT = max(
    1,
    float(os.getenv("HSGURU_ARCHETYPE_TIMEOUT", "20").replace(",", ".")),
)
HSGURU_ARCHETYPE_BROWSER_FALLBACK = os.getenv(
    "HSGURU_ARCHETYPE_BROWSER_FALLBACK",
    "1",
).strip().lower() in ("1", "true", "yes", "on")
HSGURU_ARCHETYPE_BROWSER_PATH = os.getenv("HSGURU_ARCHETYPE_BROWSER_PATH", "/usr/bin/chromium").strip()
HSGURU_ARCHETYPE_BROWSER_TIMEOUT = max(
    1,
    float(os.getenv("HSGURU_ARCHETYPE_BROWSER_TIMEOUT", "25").replace(",", ".")),
)

# Public cached data API from github.com/Zulut30/hearthstone-parses.
# Used as the primary source for HSGuru/MetaStats data when direct HSGuru
# requests are blocked by Cloudflare; old direct parsers remain as fallback.
HS_DATA_API_ENABLED = os.getenv("HS_DATA_API_ENABLED", "1").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
HS_DATA_API_BASE_URL = os.getenv("HS_DATA_API_BASE_URL", "https://api.hs-manacost.ru").rstrip("/")
HS_DATA_API_TIMEOUT = max(
    5,
    float(os.getenv("HS_DATA_API_TIMEOUT", "20").replace(",", ".")),
)
HS_DATA_API_USER_AGENT = os.getenv(
    "HS_DATA_API_USER_AGENT",
    "Mozilla/5.0 Deckview/1.0 (+https://hs-manacost.ru)",
).strip()
HS_DATA_API_STREAMER_SOURCES = tuple(
    source_id.strip()
    for source_id in os.getenv(
        "HS_DATA_API_STREAMER_SOURCES",
        "hsguru_streamer_decks_legend_1000,hearthstone_decks",
    ).split(",")
    if source_id.strip()
)
HS_DATA_API_META_STANDARD_SOURCE = os.getenv(
    "HS_DATA_API_META_STANDARD_SOURCE",
    "hsguru_meta_standard_top_legend",
).strip()
HS_DATA_API_META_WILD_SOURCE = os.getenv(
    "HS_DATA_API_META_WILD_SOURCE",
    "hsguru_meta_wild_top_legend",
).strip()
HS_DATA_API_DECK_INDEX_SOURCES = tuple(
    source_id.strip()
    for source_id in os.getenv(
        "HS_DATA_API_DECK_INDEX_SOURCES",
        "metastats_decks,hearthstone_decks,hsguru_streamer_decks_legend_1000",
    ).split(",")
    if source_id.strip()
)

# Official Manacost Public API.
# Contract: https://arena.hs-manacost.ru/api/v1/openapi.json
MANACOST_PUBLIC_API_BASE_URL = os.getenv(
    "MANACOST_PUBLIC_API_BASE_URL",
    "https://arena.hs-manacost.ru",
).strip().rstrip("/")
MANACOST_PUBLIC_API_KEY = os.getenv("MANACOST_PUBLIC_API_KEY", "").strip()
MANACOST_PUBLIC_API_TIMEOUT = max(
    5,
    float(os.getenv("MANACOST_PUBLIC_API_TIMEOUT", "20").replace(",", ".")),
)

# Таблица переводов архетипов (опубликованная Google Таблица: pubhtml или pub?output=csv)
ARCHETYPES_SHEET_URL = os.getenv(
    "ARCHETYPES_SHEET_URL",
    "https://docs.google.com/spreadsheets/d/e/2PACX-1vRGMOTwzxCfcpQtX9jW9wVhrkqQIyU42ooWwhPaaOWy76XUes4ymwrshWs0ak_FlqGAm8g76Gluty4m/pubhtml",
).strip()

# Автопубликация колоды с HSGuru каждые N часов (0 = отключено)
PUBLISH_INTERVAL_HOURS = max(0, float(os.getenv("PUBLISH_INTERVAL_HOURS", "2").replace(",", ".")))
_hsguru_interval_seconds_raw = os.getenv("HSGURU_INTERVAL_SECONDS", "").strip()
if _hsguru_interval_seconds_raw:
    HSGURU_INTERVAL_SECONDS = max(
        0,
        int(float(_hsguru_interval_seconds_raw.replace(",", "."))),
    )
else:
    HSGURU_INTERVAL_SECONDS = int(PUBLISH_INTERVAL_HOURS * 3600)

# HearthstoneJSON API для импорта карт (билд и локаль)
# Документация: https://hearthstonejson.com/docs/cards.html
HSJSON_BUILD = os.getenv("HSJSON_BUILD", "190920").strip()
HSJSON_LOCALE = os.getenv("HSJSON_LOCALE", "ruRU").strip()
HSJSON_CARDS_URL = (
    os.getenv("HSJSON_CARDS_URL", "").strip()
    or f"https://api.hearthstonejson.com/v1/{HSJSON_BUILD}/{HSJSON_LOCALE}/cards.json"
)

# Веб-приложение: БД и кэш генераций
WEB_DATABASE_PATH = os.getenv("WEB_DATABASE_PATH", "cache/deckview_web.db")
WEB_CACHE_MAX_AGE_HOURS = max(0, float(os.getenv("WEB_CACHE_MAX_AGE_HOURS", "24").replace(",", ".")))
WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.getenv("WEB_PORT", "5000"))

# Дашборд бота: опциональный ключ доступа (?key=... или заголовок X-Dashboard-Key)
DASHBOARD_SECRET = os.getenv("DASHBOARD_SECRET", "").strip() or None

# Внешний API для генерации, публикации и перевода колод.
# Используется в заголовке Authorization: Bearer ... или X-API-Key.
API_TOKEN = os.getenv("API_TOKEN", "").strip() or None

# Публичный режим для безопасных endpoints (/render, /translate, /archetypes).
# /publish всегда требует API_TOKEN, чтобы не открыть публикацию в WordPress/Telegram всем.
PUBLIC_API_AUTH_REQUIRED = os.getenv("PUBLIC_API_AUTH_REQUIRED", "0").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)

# ─── Telegram Premium Emoji IDs ───────────────────────────────────────────────
# Используются для иконок класса/режима/пыли в подписях к колодам.
PREMIUM_EMOJI_ID = "5440749199161322936"  # иконка пыли 💎

CLASS_EMOJI_ID_MAP: dict[str, str] = {
    "Воин": "5438455068149966917",
    "Охотник на демонов": "5438367558191313225",
    "Рыцарь Смерти": "5440741171867446879",
    "Охотник": "5438553208152681635",
    "Друид": "5440388387548719145",
    "Паладин": "5440845402133783633",
    "Маг": "5438158414758831946",
    "Жрец": "5440877395345173758",
    "Разбойник": "5440596933980742987",
    "Шаман": "5438410142792052203",
    "Чернокнижник": "5440711755636436510",
}

MODE_EMOJI_ID_MAP: dict[str, str] = {
    "Стандарт": "5195044355064220820",
    "Вольный": "5197162946467219199",
}


def normalize_deck_class_name(deck_class: str | None) -> str | None:
    """Нормализует название класса: приводит к каноничному написанию (напр. «рыцарь смерти» → «Рыцарь Смерти»)."""
    if not deck_class:
        return None
    value = str(deck_class).strip()
    if value.lower() in ("рыцарь смерти", "рыцарь смерти "):
        return "Рыцарь Смерти"
    return value


def build_deck_caption(deck_class: str | None, deck_mode: str | None, cost: int) -> str:
    """Формирует HTML-подпись для фото колоды: класс, режим, стоимость пыли."""
    normalized_class = normalize_deck_class_name(deck_class)
    class_emoji = CLASS_EMOJI_ID_MAP.get(normalized_class or "")
    mode_text = deck_mode or "Стандарт"
    mode_emoji = MODE_EMOJI_ID_MAP.get(mode_text)

    class_line = (
        f'<tg-emoji emoji-id="{class_emoji}">🛡️</tg-emoji> <b>Класс:</b> {normalized_class}'
        if normalized_class and class_emoji
        else f"<b>Класс:</b> {normalized_class or 'Неизвестно'}"
    )
    mode_line = (
        f'<tg-emoji emoji-id="{mode_emoji}">🎮</tg-emoji> <b>Режим:</b> {mode_text}'
        if mode_emoji
        else f"<b>Режим:</b> {mode_text}"
    )
    return (
        f"{class_line}\n"
        f"{mode_line}\n"
        f'Пыль: {cost} <tg-emoji emoji-id="{PREMIUM_EMOJI_ID}">💎</tg-emoji>'
    )


# ── Startup validation warnings ──────────────────────────────────────────────
import sys as _sys
if not TOKEN:
    print("[Config] ⚠ КРИТИЧНО: BOT_TOKEN не задан — бот не запустится!", file=_sys.stderr)
if not ADMIN_IDS:
    print("[Config] ⚠ ПРЕДУПРЕЖДЕНИЕ: ADMIN_IDS не задан — админ-команды недоступны.", file=_sys.stderr)
if DASHBOARD_SECRET and len(DASHBOARD_SECRET) < 8:
    print("[Config] ⚠ ПРЕДУПРЕЖДЕНИЕ: DASHBOARD_SECRET слишком короткий (< 8 символов).", file=_sys.stderr)
if not API_TOKEN:
    print("[Config] ⚠ ПРЕДУПРЕЖДЕНИЕ: API_TOKEN не задан — /deckview-api/v1/publish будет недоступен.", file=_sys.stderr)
elif len(API_TOKEN) < 24:
    print("[Config] ⚠ ПРЕДУПРЕЖДЕНИЕ: API_TOKEN слишком короткий (< 24 символов).", file=_sys.stderr)
