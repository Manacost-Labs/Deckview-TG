import time

from .card_counter import count_cards
from .cards_downloader import download_cards
from .cards_placer import place_cards
from .cost_getter import get_cost_of_deck
from .deck_retriever import retrieve_deck
from .deck_card_sources import hydrate_deck_cards
from .output_resolution import normalize_image_resolution


def _elapsed_ms(started_ns):
    return round((time.perf_counter_ns() - started_ns) / 1_000_000, 3)


async def create_picture(
    deck_code,
    deck_name=None,
    timings=None,
    image_style="classic",
    image_background=None,
    image_font="auto",
    image_text_size="normal",
    image_dust_display="normal",
    image_class_art=None,
    image_layout=None,
    image_mana_curve=None,
    image_resolution="standard",
):
    """Create a deck image and optionally populate per-request stage timings."""
    image_resolution = normalize_image_resolution(image_resolution)
    generator_started = time.perf_counter_ns()
    stage = "deck_resolve"
    try:
        started = time.perf_counter_ns()
        response, deck_class, sideboard = await retrieve_deck(deck_code)
        if timings is not None:
            timings["deck_resolve_ms"] = _elapsed_ms(started)
        if response == 0:
            if timings is not None:
                timings["generator_result"] = "empty"
            return None, 0, None, None, []

        all_cards = response["cards"] + sideboard
        stage = "card_sources"
        started = time.perf_counter_ns()
        await hydrate_deck_cards(all_cards)
        if timings is not None:
            timings["card_sources_ms"] = _elapsed_ms(started)

        # Blizzard deck API может отдавать id (dbfId) или dbfId; HSJSON — id (cardId строка) или dbfId (число)
        card_dbf_ids = []
        for c in all_cards:
            raw = c.get("dbfId") if c.get("dbfId") is not None else c.get("id")
            if raw is None:
                continue
            try:
                card_dbf_ids.append(int(raw))
            except (TypeError, ValueError):
                continue

        stage = "art_prepare"
        started = time.perf_counter_ns()
        await download_cards(all_cards)
        if timings is not None:
            timings["art_prepare_ms"] = _elapsed_ms(started)

        stage = "card_index"
        started = time.perf_counter_ns()
        counters, mana = await count_cards(all_cards)
        sideboard_slugs = {
            card["slug"]
            for card in all_cards
            if card.get("deckviewSideboard") is True
        }
        if timings is not None:
            timings["card_index_ms"] = _elapsed_ms(started)

        stage = "dust_cost"
        started = time.perf_counter_ns()
        cost = await get_cost_of_deck(all_cards)
        if timings is not None:
            timings["dust_cost_ms"] = _elapsed_ms(started)

        stage = "image_compose"
        started = time.perf_counter_ns()
        image = await place_cards(
            counters,
            mana,
            deck_class,
            cost,
            response,
            deck_name=deck_name,
            sideboard_slugs=sideboard_slugs,
            image_style=image_style,
            image_background=image_background,
            image_font=image_font,
            image_text_size=image_text_size,
            image_dust_display=image_dust_display,
            image_class_art=image_class_art,
            image_layout=image_layout,
            image_mana_curve=image_mana_curve,
            image_resolution=image_resolution,
        )
        if timings is not None:
            timings["image_compose_ms"] = _elapsed_ms(started)
            timings["renderer_backend"] = (
                getattr(image, "info", {}).get("deckview_renderer", "pillow")
                if image is not None
                else None
            )

        # Имя класса героя для WordPress (из Blizzard API, locale=ru_RU → «Воин», «Маг» и т.д.)
        deck_class_name = (response.get("class") or {}).get("name") or None
        if deck_class_name:
            deck_class_name = str(deck_class_name).strip()

        format_raw = str(response.get("format") or "").strip().lower()
        mode_map = {
            "standard": "Стандарт",
            "wild": "Вольный",
            "classic": "Классический",
            "twist": "Потасовка",
            "tavern-brawl": "Потасовка",
        }
        deck_mode_name = mode_map.get(format_raw, None)
        if timings is not None:
            timings["card_count"] = len(all_cards)
            timings["unique_card_count"] = len(counters)
            timings["generator_result"] = "ok" if image is not None else "empty"
        return image, cost, deck_class_name, deck_mode_name, card_dbf_ids
    except BaseException as exc:
        if timings is not None:
            timings["failed_stage"] = stage
            timings["error_type"] = type(exc).__name__
            timings["generator_result"] = "error"
        raise
    finally:
        if timings is not None:
            timings["generator_total_ms"] = _elapsed_ms(generator_started)
