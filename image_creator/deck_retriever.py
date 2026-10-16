import asyncio
import base64
import copy
import json
import os
import sqlite3
import threading
import time
from collections import OrderedDict
from pathlib import Path

from deckview.config import HSJSON_CARDS_URL, HSJSON_LOCALE
from framework.blizzard_api import get_blizzard_api
from framework.hearthstonejson_api import (
    configure as hsjson_configure,
    ensure_loaded as hsjson_ensure_loaded,
    get_card_by_dbfid,
    get_loaded_card_by_dbfid,
)
from image_creator.card_catalog_snapshot import get_standard_dbf_ids
from image_creator.deck_card_sources import hydrate_deck_cards_sync

# Карты, которые Blizzard/HSJSON не отдают по dbfId. (slug_suffix, name_ru, mana, rarityId, art CardID или None).
# Для отображения используем русские названия. Art: https://hearthstonejson.com/docs/images.html
# CORE_CS3_027 — это «Средоточие воли» (Priest), не Consumption; у Consumption (Поглощение) в HSJSON нет арта по dbfId 127410.
ART_API_BASE = "https://art.hearthstonejson.com/v1"
INVALID_CARD_FALLBACK = {
    127410: ("consumption", "Поглощение", 4, 3, None),   # Consumption — плейсхолдер (арта по CardID в HSJSON нет)
    127536: ("alexandros-mograine", "Александрос Могрейн", 7, 5, "RLK_706"),  # Alexandros Mograine, арт RLK_706
}

_DECK_CACHE_TTL_SECONDS = max(
    0.0,
    float(os.getenv("DECKVIEW_DECK_CACHE_TTL_SECONDS", "1800").replace(",", ".")),
)
_DECK_CACHE_MAX_ENTRIES = max(
    1,
    int(os.getenv("DECKVIEW_DECK_CACHE_MAX_ENTRIES", "256")),
)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DECK_DISK_CACHE_PATH = Path(
    os.getenv(
        "DECKVIEW_DECK_CACHE_PATH",
        str(_PROJECT_ROOT / "cache" / "deck_payloads.sqlite3"),
    )
)
_deck_cache_lock = threading.RLock()
_deck_cache = OrderedDict()
_DECK_CACHE_PAYLOAD_VERSION = 4

_FORMAT_NAMES = {
    1: "wild",
    2: "standard",
    3: "classic",
    4: "twist",
}
_CLASS_INFO = {
    "DEATHKNIGHT": (1, "deathknight", "Рыцарь Смерти"),
    "DRUID": (2, "druid", "Друид"),
    "HUNTER": (3, "hunter", "Охотник"),
    "MAGE": (4, "mage", "Маг"),
    "PALADIN": (5, "paladin", "Паладин"),
    "PRIEST": (6, "priest", "Жрец"),
    "ROGUE": (7, "rogue", "Разбойник"),
    "SHAMAN": (8, "shaman", "Шаман"),
    "WARLOCK": (9, "warlock", "Чернокнижник"),
    "WARRIOR": (10, "warrior", "Воин"),
    "DEMONHUNTER": (14, "demonhunter", "Охотник на демонов"),
}
_BASE_HERO_CLASSES = {
    7: "WARRIOR",
    31: "HUNTER",
    274: "DRUID",
    637: "MAGE",
    671: "PALADIN",
    813: "PRIEST",
    893: "WARLOCK",
    930: "ROGUE",
    1066: "SHAMAN",
    56550: "DEMONHUNTER",
    78065: "DEATHKNIGHT",
}
# These heroes have `classes` but no single `cardClass` in HearthstoneJSON.
# Keep their own class for the existing class art, regardless of deck contents.
_MULTICLASS_HERO_CLASSES = {
    "Anduin": "PRIEST",
    "Garrosh": "WARRIOR",
    "Guldan": "WARLOCK",
    "Illidan": "DEMONHUNTER",
    "Jaina": "MAGE",
    "Lich_King": "DEATHKNIGHT",
    "Malfurion": "DRUID",
    "Rexxar": "HUNTER",
    "Thrall": "SHAMAN",
    "Uther": "PALADIN",
    "Valeera": "ROGUE",
}


def _resolve_deck_format(deck_format: int, dbf_ids) -> str:
    """Correct stale Standard headers using the current local legality set."""
    format_name = _FORMAT_NAMES[int(deck_format)]
    if format_name != "standard":
        return format_name
    standard_ids = get_standard_dbf_ids()
    if not standard_ids:
        return format_name
    main_ids = {
        int(dbf_id)
        for dbf_id in dbf_ids
        if str(dbf_id).strip().isdigit() and int(dbf_id) > 0
    }
    if main_ids and not main_ids.issubset(standard_ids):
        return "wild"
    return format_name


def _response_deck_format(deck_code: str, response: dict) -> str:
    """Resolve legality from the deckstring itself, even after API fallback."""
    try:
        entries, heroes, deck_format, _sideboards = _decode_deckstring(deck_code)
        dbf_ids = [dbf_id for dbf_id, _copies in entries]
    except (TypeError, ValueError):
        raw_format = str(response.get("format") or "").strip().lower()
        deck_format = next(
            (
                format_id
                for format_id, format_name in _FORMAT_NAMES.items()
                if format_name == raw_format
            ),
            2,
        )
        dbf_ids = []
        for card in response.get("cards", []):
            if not isinstance(card, dict):
                continue
            raw_dbf_id = card.get("dbfId", card.get("id"))
            try:
                dbf_id = int(raw_dbf_id)
            except (TypeError, ValueError):
                continue
            if dbf_id > 0:
                dbf_ids.append(dbf_id)
    else:
        if _is_brawl_deck(heroes):
            return "tavern-brawl"
    return _resolve_deck_format(deck_format, dbf_ids)


def _read_varint(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while offset < len(data):
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte & 0x80 == 0:
            return value, offset
        shift += 7
        if shift > 63:
            break
    raise ValueError("Truncated deckstring varint")


def _decode_deckstring(deck_code: str):
    """Decode cards, hero and sideboards locally without an HTTP request."""
    clean = str(deck_code or "").strip()
    clean += "=" * (-len(clean) % 4)
    raw = base64.b64decode(clean, validate=True)
    if not raw or raw[0] != 0:
        raise ValueError("Invalid deckstring")
    offset = 1
    version, offset = _read_varint(raw, offset)
    if version != 1:
        raise ValueError(f"Unsupported deckstring version {version}")
    deck_format, offset = _read_varint(raw, offset)
    if deck_format not in _FORMAT_NAMES:
        raise ValueError(f"Unsupported deckstring format {deck_format}")

    hero_count, offset = _read_varint(raw, offset)
    heroes = []
    for _ in range(hero_count):
        hero, offset = _read_varint(raw, offset)
        heroes.append(hero)
    if not heroes:
        raise ValueError("Invalid hero")

    cards = []
    for copies in (1, 2):
        count, offset = _read_varint(raw, offset)
        for _ in range(count):
            dbf_id, offset = _read_varint(raw, offset)
            cards.append((dbf_id, copies))
    n_count, offset = _read_varint(raw, offset)
    for _ in range(n_count):
        dbf_id, offset = _read_varint(raw, offset)
        copies, offset = _read_varint(raw, offset)
        cards.append((dbf_id, copies))

    sideboards = []
    has_sideboards = offset < len(raw) and raw[offset] == 1
    offset += 1 if offset < len(raw) else 0
    if has_sideboards:
        for copies in (1, 2):
            count, offset = _read_varint(raw, offset)
            for _ in range(count):
                dbf_id, offset = _read_varint(raw, offset)
                owner, offset = _read_varint(raw, offset)
                sideboards.append((dbf_id, copies, owner))
        n_count, offset = _read_varint(raw, offset)
        for _ in range(n_count):
            dbf_id, offset = _read_varint(raw, offset)
            copies, offset = _read_varint(raw, offset)
            owner, offset = _read_varint(raw, offset)
            sideboards.append((dbf_id, copies, owner))
    return cards, heroes, deck_format, sideboards


def _expanded_cards(entries, *, sideboard=False):
    cards = []
    for entry in entries:
        dbf_id, copies = entry[:2]
        for _ in range(max(0, int(copies))):
            cards.append(
                {
                    "id": int(dbf_id),
                    "dbfId": int(dbf_id),
                    "quantity": 1,
                    "deckQuantity": 1,
                    "deckviewSideboard": bool(sideboard),
                }
            )
    return cards


_LOCAL_CARD_REQUIRED_FIELDS = ("slug", "name", "manaCost", "cardId")


def _fill_missing_from_preloaded_hsjson(cards):
    """Complete generated/sideboard cards without entering a network fallback."""
    for card in cards:
        if (
            all(card.get(field) not in (None, "") for field in _LOCAL_CARD_REQUIRED_FIELDS)
            and (card.get("deckviewPlayerClass") or card.get("deckviewClasses"))
        ):
            continue
        fallback = get_loaded_card_by_dbfid(card.get("dbfId", card.get("id")))
        if not fallback:
            continue
        for field, value in fallback.items():
            if value not in (None, "") and card.get(field) in (None, ""):
                card[field] = value
        card.setdefault("deckviewMetadataFallback", "hsjson-memory")
    return cards


def _is_brawl_deck(heroes):
    for hero in heroes:
        metadata = get_loaded_card_by_dbfid(hero) or {}
        if metadata.get("set") == "TB" or str(metadata.get("cardId") or "").startswith("TB_"):
            return True
    return False


def _infer_class(heroes, cards):
    for hero in heroes:
        class_key = _BASE_HERO_CLASSES.get(int(hero))
        if class_key:
            return class_key
        metadata = get_loaded_card_by_dbfid(hero) or {}
        class_key = str(metadata.get("deckviewPlayerClass") or "").upper()
        if class_key in _CLASS_INFO:
            return class_key
        card_id = str(metadata.get("cardId") or "")
        prefix = "TB_MultiClass_Hero_"
        if card_id.startswith(prefix):
            class_key = _MULTICLASS_HERO_CLASSES.get(card_id[len(prefix):])
            if class_key:
                return class_key
        for class_key in metadata.get("deckviewClasses") or []:
            if class_key in _CLASS_INFO:
                return class_key
    counts = {}
    for card in cards:
        class_keys = [card.get("deckviewPlayerClass"), *(card.get("deckviewClasses") or [])]
        for class_key in dict.fromkeys(class_keys):
            class_key = str(class_key or "").strip().upper()
            if class_key in _CLASS_INFO:
                counts[class_key] = counts.get(class_key, 0) + 1
    return max(counts, key=counts.get) if counts else None


def _build_local_deck(deck_code):
    entries, heroes, deck_format, sideboard_entries = _decode_deckstring(deck_code)
    cards = _expanded_cards(entries)
    sideboard = _expanded_cards(sideboard_entries, sideboard=True)
    hydrate_deck_cards_sync(cards + sideboard)
    _fill_missing_from_preloaded_hsjson(cards + sideboard)

    def missing_metadata():
        return any(
            card.get(field) in (None, "")
            for card in cards + sideboard
            for field in _LOCAL_CARD_REQUIRED_FIELDS
        )

    unknown_hero = any(
        hero not in _BASE_HERO_CLASSES and get_loaded_card_by_dbfid(hero) is None
        for hero in heroes
    )
    if missing_metadata() or unknown_hero:
        # Load the shared catalog once, rather than request every missing card.
        # This also bypasses Blizzard's single-class deck endpoint for brawls.
        hsjson_configure(HSJSON_CARDS_URL, HSJSON_LOCALE)
        if hsjson_ensure_loaded(timeout_seconds=8):
            _fill_missing_from_preloaded_hsjson(cards + sideboard)

    # Missing metadata would make ordering, mana curve or images incorrect.
    # In that uncommon case use the bounded Blizzard fallback below.
    if missing_metadata():
        return None
    for card in sideboard:
        if not str(card["slug"]).endswith("-side"):
            card["slug"] += "-side"

    class_key = _infer_class(heroes, cards)
    if not class_key:
        return None
    class_id, class_slug, class_name = _CLASS_INFO[class_key]
    response = {
        "cards": cards,
        "cardCount": len(cards),
        "class": {"id": class_id, "slug": class_slug, "name": class_name},
        "format": "tavern-brawl" if _is_brawl_deck(heroes) else _resolve_deck_format(
            deck_format, [dbf_id for dbf_id, _copies in entries]
        ),
        "deckviewDataSource": "deckstring+kolodahs",
    }
    class_keys = [class_key]
    for hero in heroes:
        metadata = get_loaded_card_by_dbfid(hero) or {}
        for key in metadata.get("deckviewClasses") or []:
            if key in _CLASS_INFO and key not in class_keys:
                class_keys.append(key)
    if len(class_keys) > 1:
        response["classes"] = [
            {"id": class_id, "slug": slug, "name": name}
            for class_id, slug, name in (_CLASS_INFO[key] for key in class_keys)
        ]
    return response, class_id, sideboard


def _deck_disk_cache_enabled():
    return os.getenv("DECKVIEW_DECK_DISK_CACHE", "1").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _deck_disk_connection():
    _DECK_DISK_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(_DECK_DISK_CACHE_PATH, timeout=5)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS deck_payloads (
            deck_code TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL,
            fetched_at REAL NOT NULL
        )
        """
    )
    return connection


def _get_disk_cached_deck(deck_code):
    if _DECK_CACHE_TTL_SECONDS <= 0 or not _deck_disk_cache_enabled():
        return None
    try:
        with _deck_disk_connection() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM deck_payloads
                WHERE deck_code = ? AND fetched_at >= ?
                """,
                (deck_code, time.time() - _DECK_CACHE_TTL_SECONDS),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row[0])
        if (
            not isinstance(payload, dict)
            or payload.get("version") != _DECK_CACHE_PAYLOAD_VERSION
        ):
            return None
        value = payload.get("value")
        if not isinstance(value, list) or len(value) != 3:
            return None
        return tuple(value)
    except Exception as exc:
        print(
            "[Deckview deck cache] shared read fallback: "
            f"{type(exc).__name__}"
        )
        return None


def _put_disk_cached_deck(deck_code, value):
    if _DECK_CACHE_TTL_SECONDS <= 0 or not _deck_disk_cache_enabled():
        return
    try:
        payload_json = json.dumps(
            {
                "version": _DECK_CACHE_PAYLOAD_VERSION,
                "value": value,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with _deck_disk_connection() as connection:
            connection.execute(
                """
                INSERT INTO deck_payloads (deck_code, payload_json, fetched_at)
                VALUES (?, ?, ?)
                ON CONFLICT(deck_code) DO UPDATE SET
                    payload_json=excluded.payload_json,
                    fetched_at=excluded.fetched_at
                """,
                (deck_code, payload_json, time.time()),
            )
    except Exception as exc:
        print(
            "[Deckview deck cache] shared write fallback: "
            f"{type(exc).__name__}"
        )


def _get_cached_deck(deck_code):
    if _DECK_CACHE_TTL_SECONDS <= 0:
        return None
    now = time.monotonic()
    with _deck_cache_lock:
        cached = _deck_cache.get(deck_code)
        if cached is None:
            disk_value = _get_disk_cached_deck(deck_code)
            if disk_value is None:
                return None
            _deck_cache[deck_code] = (
                time.monotonic(),
                copy.deepcopy(disk_value),
            )
            _deck_cache.move_to_end(deck_code)
            while len(_deck_cache) > _DECK_CACHE_MAX_ENTRIES:
                _deck_cache.popitem(last=False)
            return copy.deepcopy(disk_value)
        cached_at, value = cached
        if now - cached_at >= _DECK_CACHE_TTL_SECONDS:
            _deck_cache.pop(deck_code, None)
            return None
        _deck_cache.move_to_end(deck_code)
        # retrieve_deck callers add suffixes and metadata to card dictionaries.
        # A deep copy prevents one render from changing another cached render.
        return copy.deepcopy(value)


def _put_cached_deck(deck_code, value):
    if _DECK_CACHE_TTL_SECONDS <= 0:
        return
    with _deck_cache_lock:
        _deck_cache[deck_code] = (time.monotonic(), copy.deepcopy(value))
        _deck_cache.move_to_end(deck_code)
        while len(_deck_cache) > _DECK_CACHE_MAX_ENTRIES:
            _deck_cache.popitem(last=False)
    _put_disk_cached_deck(deck_code, value)


def _make_fallback_card(card_id, slug_suffix, wiki_name, mana_cost, rarity_id=2, art_card_id=None):
    """Карта-заглушка. Если задан art_card_id — image = HearthstoneJSON Art API (render PNG)."""
    slug = f"{card_id}-{slug_suffix}"
    if art_card_id:
        image = f"{ART_API_BASE}/render/latest/ruRU/512x/{art_card_id}.png"
    else:
        image = "https://httpbin.org/status/404"
    return {
        "id": card_id,
        "slug": slug,
        "name": wiki_name,
        "manaCost": mana_cost,
        "rarityId": rarity_id,
        "image": image,
    }


async def retrieve_deck(deck_code):
    cached = _get_cached_deck(deck_code)
    if cached is not None:
        return cached

    try:
        local_result = await asyncio.to_thread(_build_local_deck, deck_code)
    except Exception as exc:
        print(f"[Deckview deck] local decode fallback: {type(exc).__name__}")
        local_result = None
    if local_result is not None:
        _put_cached_deck(deck_code, local_result)
        return copy.deepcopy(local_result)

    try:
        api = get_blizzard_api(locale="ru_RU")
    except ValueError as e:
        print(f"retrieve_deck: {e}")
        return [0, 0, 0]
    response = await api.get_from_code(deck_code)
    if "error" in response:
        raise ValueError(response["error"])

    duels_class = None
    sideboard = []

    if "sideboardCards" in response:
        sideboard = response["sideboardCards"][0]["cardsInSideboard"]
        for i in sideboard:
            i["deckviewSideboard"] = True
            i["slug"] += "-side"
    for card in response.get("cards", []):
        card["deckviewSideboard"] = False

    # Добавляем карты из invalidCardIds (API отдаёт их отдельно, cardCount может быть только по "cards").
    # Для конструктора 30 карт: в cards бывает 27, в invalidCardIds — ещё 3; без этого условия их не добавляем.
    if response.get("invalidCardIds"):
        for card_id in response["invalidCardIds"]:
            resp_card = await api.get_card_from_id(card_id)
            if "error" not in resp_card:
                response["cards"].append(resp_card)
                if response["cardCount"] == 15:
                    duels_class = resp_card.get("classId")
            else:
                # Blizzard не отдаёт карту — пробуем HearthstoneJSON API (билд 190920/ruRU + fallback latest)
                hsjson_configure(HSJSON_CARDS_URL, HSJSON_LOCALE)
                deckview_card = get_card_by_dbfid(card_id)
                if deckview_card:
                    response["cards"].append(deckview_card)
                    if response["cardCount"] == 15:
                        duels_class = response["class"]["id"]
                else:
                    # Ни в HSJSON нет — используем захардкоженную заглушку (вики)
                    fallback = INVALID_CARD_FALLBACK.get(card_id)
                    if fallback:
                        slug_suffix, name_ru, mana_cost, rarity_id, art_card_id = fallback
                        response["cards"].append(
                            _make_fallback_card(card_id, slug_suffix, name_ru, mana_cost, rarity_id, art_card_id)
                        )
                        if response["cardCount"] == 15:
                            duels_class = response["class"]["id"]
                    else:
                        print(f"deck_retriever: не удалось загрузить карту id={card_id}: {resp_card.get('error')}")

    if duels_class:
        deck_class = int(str(response["class"]["id"]) + str(duels_class))
    else:
        deck_class = response["class"]["id"]

    response["format"] = _response_deck_format(deck_code, response)

    result = (response, deck_class, sideboard)
    _put_cached_deck(deck_code, result)
    return copy.deepcopy(result)
