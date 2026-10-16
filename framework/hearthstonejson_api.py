"""
Клиент HearthstoneJSON API для импорта карт по dbfId.
Документация: https://hearthstonejson.com/docs/cards.html
Билд 190920 ruRU: https://api.hearthstonejson.com/v1/190920/ruRU/
"""
import re
from typing import Any, Dict, List, Optional

from framework.http_session import get_http_session

# Базовые URL из конфига (задаются при первом использовании)
_HSJSON_CARDS_URL: Optional[str] = None
_HSJSON_ART_LOCALE = "ruRU"
_HSJSON_ART_BASE = "https://art.hearthstonejson.com/v1/render/latest"

# Кэш RU: dbfId -> сырая карта HSJSON (ruRU)
_cards_by_dbfid: Dict[int, Dict[str, Any]] = {}
_loaded = False

# Кэш enUS: загружается лениво при первом поиске по английскому имени.
# Нормализованное en-имя -> список dbfId (для точного и нечёткого поиска по en).
_en_name_to_dbfids: Dict[str, List[int]] = {}
_en_cards_by_dbfid: Dict[int, Dict[str, Any]] = {}  # dbfId -> raw card enUS (для имён)
_loaded_en = False


def _slugify(name: str) -> str:
    """Нормализация имени для slug: латиница/кириллица в нижний регистр, пробелы -> дефис."""
    s = (name or "").strip().lower()
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE)
    s = re.sub(r"[-\s]+", "-", s).strip("-")
    return s or "card"


def configure(cards_url: str, art_locale: str = "ruRU") -> None:
    """Задать URL списка карт и локаль для артов. Вызывается из config при импорте."""
    global _HSJSON_CARDS_URL, _HSJSON_ART_LOCALE
    _HSJSON_CARDS_URL = cards_url.rstrip("/")
    if not _HSJSON_CARDS_URL.endswith(".json"):
        _HSJSON_CARDS_URL = f"{_HSJSON_CARDS_URL}/cards.json"
    _HSJSON_ART_LOCALE = art_locale or "ruRU"


def _load_cards(timeout_seconds: float | None = None) -> None:
    """Загрузить cards.json из основного URL и дополнительно latest для новых сетов.

    Итоговый кэш _cards_by_dbfid содержит:
    - все карты из зафиксированного билда (HSJSON_CARDS_URL или 190920/ruRU);
    - а также карты из latest, которых нет в основном билде.

    Это нужно, чтобы поиск по имени (/card) работал и для новых дополнений,
    а не только для старого билда 190920.
    """
    global _cards_by_dbfid, _loaded
    if _loaded:
        return
    request_timeout = 45.0 if timeout_seconds is None else max(
        0.25,
        float(timeout_seconds),
    )
    url = _HSJSON_CARDS_URL or "https://api.hearthstonejson.com/v1/190920/ruRU/cards.json"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; Deckview/1.0)"}
    session = get_http_session()
    try:
        resp = session.get(url, headers=headers, timeout=request_timeout)
        resp.raise_for_status()
        data: List[Dict] = resp.json()
    except Exception as e:
        print(f"[HSJSON] Ошибка загрузки {url}: {e}")
        # НЕ выставляем _loaded=True — при следующем вызове попробуем снова
        return
    for c in data:
        dbf_id = c.get("dbfId")
        if dbf_id is not None:
            _cards_by_dbfid[int(dbf_id)] = c
    # Дополнительно пробуем latest, чтобы добавить карты из новых сетов.
    try:
        latest_url = "https://api.hearthstonejson.com/v1/latest/ruRU/cards.json"
        resp_latest = session.get(
            latest_url,
            headers=headers,
            timeout=request_timeout,
        )
        resp_latest.raise_for_status()
        latest_data: List[Dict] = resp_latest.json()
        added = 0
        for c in latest_data:
            dbf_id = c.get("dbfId")
            if dbf_id is not None and int(dbf_id) not in _cards_by_dbfid:
                _cards_by_dbfid[int(dbf_id)] = c
                added += 1
        print(f"[HSJSON] Загружено {len(_cards_by_dbfid)} карт: {url} + latest (добавлено {added})")
    except Exception as e:
        # Если latest не загрузился — не критично, работаем только на основном билде.
        print(f"[HSJSON] Ошибка загрузки latest: {e}")
        print(f"[HSJSON] Загружено {len(_cards_by_dbfid)} карт из {url}")
    # Выставляем флаг только если основной список успешно загружен
    _loaded = True


def _load_cards_en() -> None:
    """Лениво загрузить latest/enUS/cards.json для поиска по английским именам. Не блокирует старт бота."""
    global _en_name_to_dbfids, _en_cards_by_dbfid, _loaded_en
    if _loaded_en:
        return
    url = "https://api.hearthstonejson.com/v1/latest/enUS/cards.json"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; Deckview/1.0)"}
    session = get_http_session()
    try:
        resp = session.get(url, headers=headers, timeout=45)
        resp.raise_for_status()
        data: List[Dict] = resp.json()
    except Exception as e:
        print(f"[HSJSON] Ошибка загрузки enUS: {e}")
        # НЕ выставляем _loaded_en=True — при следующем вызове попробуем снова
        return
    for c in data:
        dbf_id = c.get("dbfId")
        if dbf_id is None:
            continue
        dbf_id = int(dbf_id)
        _en_cards_by_dbfid[dbf_id] = c
        name = (c.get("name") or "").strip()
        if name:
            name_norm = " ".join(name.lower().split())
            if name_norm not in _en_name_to_dbfids:
                _en_name_to_dbfids[name_norm] = []
            if dbf_id not in _en_name_to_dbfids[name_norm]:
                _en_name_to_dbfids[name_norm].append(dbf_id)
    _loaded_en = True
    print(f"[HSJSON] Загружено enUS: {len(_en_cards_by_dbfid)} карт")


def get_card_en_name(dbf_id: int) -> Optional[str]:
    """Вернуть английское название карты по dbfId или None."""
    _load_cards_en()
    return _en_cards_by_dbfid.get(dbf_id, {}).get("name") or None


_RARITY_TO_ID = {"FREE": 1, "COMMON": 2, "RARE": 3, "EPIC": 4, "LEGENDARY": 5}


def _card_to_deckview(c: Dict[str, Any], dbf_id: int) -> Dict[str, Any]:
    """Превратить объект карты HSJSON в формат Deckview (slug, name, manaCost, image, rarityId)."""
    card_id = c.get("id") or str(dbf_id)
    name = (c.get("name") or "").strip() or "Card"
    cost = c.get("cost")
    if cost is None:
        cost = 0
    rarity_id = _RARITY_TO_ID.get((c.get("rarity") or "").upper(), 2)
    name_slug = _slugify(name)
    slug = f"{dbf_id}-{name_slug}"
    image = f"{_HSJSON_ART_BASE}/{_HSJSON_ART_LOCALE}/512x/{card_id}.png"
    text = (c.get("text") or "").strip() or None
    set_id = (c.get("set") or "").strip() or None
    card_type = (c.get("type") or "").strip() or None
    rarity = (c.get("rarity") or "").strip() or None
    # collectible: True = игрок может крафтить/собирать; False/None = неколлекционируемая
    # (токены, компаньоны Fabled-карт и т.п.).  В HSJSON поле отсутствует у некол. карт.
    collectible = c.get("collectible") is True
    return {
        "id": dbf_id,
        "slug": slug,
        "name": name,
        "manaCost": cost,
        "rarityId": rarity_id,
        "image": image,
        "text": text,
        "set": set_id,
        "cardId": card_id,
        "type": card_type,
        "rarity": rarity,
        "collectible": collectible,
        "deckviewPlayerClass": c.get("cardClass"),
        "deckviewClasses": list(c.get("classes") or []),
    }


def get_card_by_dbfid(dbf_id: int) -> Optional[Dict[str, Any]]:
    """
    Вернуть объект карты в формате Deckview по dbfId (для pipeline: slug, name, manaCost, image).
    Сначала ищет в загруженном билде (190920), при отсутствии — пробует latest.
    """
    global _cards_by_dbfid, _loaded
    if _HSJSON_CARDS_URL is None:
        configure("https://api.hearthstonejson.com/v1/190920/ruRU/cards.json")
    _load_cards()
    c = _cards_by_dbfid.get(dbf_id)
    if c is not None:
        return _card_to_deckview(c, dbf_id)
    # Fallback: загрузить latest и поискать там (карты из новых патчей)
    try:
        latest_url = "https://api.hearthstonejson.com/v1/latest/ruRU/cards.json"
        resp = get_http_session().get(
            latest_url, headers={"User-Agent": "Mozilla/5.0 (compatible; Deckview/1.0)"}, timeout=30
        )
        if resp.ok:
            for card in resp.json():
                if card.get("dbfId") == dbf_id:
                    _cards_by_dbfid[dbf_id] = card
                    return _card_to_deckview(card, dbf_id)
    except Exception as e:
        print(f"[HSJSON] Fallback latest для dbfId {dbf_id}: {e}")
    return None


def get_loaded_card_by_dbfid(dbf_id: int) -> Optional[Dict[str, Any]]:
    """Read only the preloaded shared catalog and never perform an HTTP call."""
    if not _loaded:
        return None
    try:
        normalized_dbf_id = int(dbf_id)
    except (TypeError, ValueError):
        return None
    card = _cards_by_dbfid.get(normalized_dbf_id)
    if card is None:
        return None
    return _card_to_deckview(card, normalized_dbf_id)


def ensure_loaded(timeout_seconds: float | None = None) -> bool:
    """Принудительно загрузить кэш карт. Возвращает True при успехе."""
    _load_cards(timeout_seconds=timeout_seconds)
    return len(_cards_by_dbfid) > 0


def find_cards_by_query(query: str) -> List[Dict[str, Any]]:
    """
    Строгий поиск карт. Приоритет: dbfId (число) -> cardId (например CORE_CS3_027) ->
    точное имя ru -> точное имя en. Возвращает список карт в формате Deckview (для выбора кнопками при нескольких).
    """
    global _cards_by_dbfid, _loaded
    if _HSJSON_CARDS_URL is None:
        configure("https://api.hearthstonejson.com/v1/190920/ruRU/cards.json")
    _load_cards()
    query = (query or "").strip()
    if not query:
        return []

    # 1) Запрос — число: ищем по dbfId
    if query.isdigit():
        card = get_card_by_dbfid(int(query))
        return [card] if card else []

    # 2) Запрос — card id (например CORE_CS3_027)
    q_lower = query.lower()
    by_id = []
    for dbf_id, c in _cards_by_dbfid.items():
        cid = (c.get("id") or "").strip().lower()
        if cid == q_lower:
            by_id.append(_card_to_deckview(c, dbf_id))
    if by_id:
        return by_id

    # 3) Точное совпадение имени (ru)
    query_norm = " ".join(query.lower().split())
    exact_matches = []
    for dbf_id, c in _cards_by_dbfid.items():
        name = (c.get("name") or "").strip()
        name_norm = " ".join(name.lower().split())
        if name_norm == query_norm:
            exact_matches.append(_card_to_deckview(c, dbf_id))
    if exact_matches:
        exact_matches.sort(key=lambda x: x.get("id", 0))
        return exact_matches

    # 4) Точное совпадение имени (en) — ленивая загрузка enUS
    _load_cards_en()
    if query_norm in _en_name_to_dbfids:
        dbf_ids = _en_name_to_dbfids[query_norm]
        out = []
        for dbf_id in sorted(dbf_ids):
            c = _cards_by_dbfid.get(dbf_id)
            if c is not None:
                out.append(_card_to_deckview(c, dbf_id))
        if out:
            return out

    return []


def _normalize_search_query(s: str) -> str:
    """Нормализация для нечёткого поиска: lower, один пробел, без апострофов/кавычек."""
    if not s:
        return ""
    s = (s or "").strip().lower()
    s = re.sub(r"['\"`]", "", s)
    return " ".join(s.split())


def search_cards_fuzzy(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """
    Нечёткий поиск: по части имени (contains) по ru и en. При необходимости — difflib для опечаток.
    Возвращает до limit карт в формате Deckview, отсортированных по релевантности (startswith выше).
    """
    global _cards_by_dbfid, _loaded
    if _HSJSON_CARDS_URL is None:
        configure("https://api.hearthstonejson.com/v1/190920/ruRU/cards.json")
    _load_cards()
    _load_cards_en()
    q = _normalize_search_query(query)
    if not q or len(q) < 2:
        return []

    # Собираем кандидатов: ru name contains q или startswith q
    start_matches_ru: List[tuple] = []  # (dbf_id, name)
    contains_matches_ru: List[tuple] = []
    for dbf_id, c in _cards_by_dbfid.items():
        name = (c.get("name") or "").strip()
        name_norm = " ".join(name.lower().split())
        if not name_norm:
            continue
        if name_norm.startswith(q):
            start_matches_ru.append((dbf_id, name_norm))
        elif q in name_norm:
            contains_matches_ru.append((dbf_id, name_norm))

    # en: по _en_cards_by_dbfid имена, но результат выдаём из _cards_by_dbfid (ru для отображения)
    start_matches_en: List[int] = []
    contains_matches_en: List[int] = []
    for dbf_id, c in _en_cards_by_dbfid.items():
        name = (c.get("name") or "").strip()
        name_norm = " ".join(name.lower().split())
        if not name_norm:
            continue
        if name_norm.startswith(q):
            start_matches_en.append(dbf_id)
        elif q in name_norm:
            contains_matches_en.append(dbf_id)

    # Уникальные dbf_id с приоритетом: startswith ru, startswith en, contains ru, contains en
    seen: set = set()
    ordered_dbf_ids: List[int] = []
    for dbf_id, _ in sorted(start_matches_ru, key=lambda x: x[1]):
        if dbf_id not in seen:
            seen.add(dbf_id)
            ordered_dbf_ids.append(dbf_id)
    for dbf_id in sorted(start_matches_en):
        if dbf_id not in seen:
            seen.add(dbf_id)
            ordered_dbf_ids.append(dbf_id)
    for dbf_id, _ in sorted(contains_matches_ru, key=lambda x: x[1]):
        if dbf_id not in seen:
            seen.add(dbf_id)
            ordered_dbf_ids.append(dbf_id)
    for dbf_id in sorted(contains_matches_en):
        if dbf_id not in seen:
            seen.add(dbf_id)
            ordered_dbf_ids.append(dbf_id)

    # Если ничего не нашли — пробуем difflib по всем ru именам (дорого, только при малом числе кандидатов)
    if not ordered_dbf_ids and len(q) >= 2:
        import difflib
        all_ru_names = []
        for dbf_id, c in _cards_by_dbfid.items():
            name = (c.get("name") or "").strip()
            if name:
                all_ru_names.append((dbf_id, " ".join(name.lower().split())))
        if all_ru_names:
            names_only = [n for _, n in all_ru_names]
            close = difflib.get_close_matches(q, names_only, n=limit, cutoff=0.6)
            for match_name in close:
                for dbf_id, n in all_ru_names:
                    if n == match_name and dbf_id not in seen:
                        seen.add(dbf_id)
                        ordered_dbf_ids.append(dbf_id)
                        break

    result = []
    for dbf_id in ordered_dbf_ids[:limit]:
        c = _cards_by_dbfid.get(dbf_id)
        if c is not None:
            result.append(_card_to_deckview(c, dbf_id))
    return result


def suggest_cards_by_name(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """
    Подсказки «Возможно, вы имели в виду» для опечаток (1-2 символа).
    Использует difflib с пониженным порогом (0.4), ищет по русским и английским именам.
    Вызывается только когда find_cards_by_query И search_cards_fuzzy оба вернули пустой результат.
    Возвращает до limit карт в формате Deckview.
    """
    import difflib
    _load_cards()
    _load_cards_en()
    q = _normalize_search_query(query)
    if not q or len(q) < 2:
        return []

    # Строим словарь: нормализованное имя -> dbf_id (предпочитаем ru)
    name_to_dbfid: Dict[str, int] = {}
    # Сначала английские (ниже приоритет)
    for dbf_id, c in _en_cards_by_dbfid.items():
        name = " ".join((c.get("name") or "").strip().lower().split())
        if name and name not in name_to_dbfid:
            name_to_dbfid[name] = dbf_id
    # Потом русские (перезаписывают en при совпадении нормализации)
    for dbf_id, c in _cards_by_dbfid.items():
        name = " ".join((c.get("name") or "").strip().lower().split())
        if name:
            name_to_dbfid[name] = dbf_id

    all_names = list(name_to_dbfid.keys())
    close = difflib.get_close_matches(q, all_names, n=limit, cutoff=0.4)

    result = []
    seen: set = set()
    for match_name in close:
        dbf_id = name_to_dbfid[match_name]
        if dbf_id in seen:
            continue
        seen.add(dbf_id)
        c = _cards_by_dbfid.get(dbf_id)
        if c is not None:
            result.append(_card_to_deckview(c, dbf_id))
    return result


def get_random_card(
    *,
    card_class: Optional[str] = None,
    rarity: Optional[str] = None,
    card_type: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Вернуть одну случайную карту из кэша. Фильтры: card_class (например "mage", "warrior"),
    rarity (например "legendary", "epic"), card_type (например "minion", "spell", "weapon").
    Без фильтров — любая карта из _cards_by_dbfid. Возвращает карту в формате Deckview или None.
    """
    import random
    global _cards_by_dbfid, _loaded
    if _HSJSON_CARDS_URL is None:
        configure("https://api.hearthstonejson.com/v1/190920/ruRU/cards.json")
    _load_cards()
    candidates = []
    class_upper = (card_class or "").strip().upper()
    rarity_upper = (rarity or "").strip().upper()
    type_upper = (card_type or "").strip().upper()
    for dbf_id, c in _cards_by_dbfid.items():
        if class_upper and (c.get("cardClass") or "").upper() != class_upper:
            continue
        if rarity_upper and (c.get("rarity") or "").upper() != rarity_upper:
            continue
        if type_upper and (c.get("type") or "").strip().upper() != type_upper:
            continue
        # Только играбельные карты (исключаем token, hero power и т.п. по желанию можно сузить)
        candidates.append((dbf_id, c))
    if not candidates:
        return None
    dbf_id, c = random.choice(candidates)
    return _card_to_deckview(c, dbf_id)
