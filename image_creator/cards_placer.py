import math
import os
import threading
import time
import traceback
from collections import OrderedDict
from io import BytesIO
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from db.config import FOLDER

from db.font import FONT, FONT_PATH
from deckview.infrastructure.async_tools import to_thread

# Всегда абсолютный путь к шрифту заголовка (HEARTHSTONE_CYRILLIC), чтобы не подхватить другой шрифт
_TITLE_FONT_PATH = os.path.abspath(os.path.normpath(FONT_PATH))

from .card_showcase import (
    BELWE_FONT_PATH,
    PARCHMENT_PATH,
    WOOD_FRAME_PATH,
    parchment_background,
    wood_frame_overlay,
)
from .custom_background import decorative_background, normalize_background_blur
from .font_catalog import FONT_OPTIONS, load_title_font, normalize_font_key
from .place_runes import place_runes
from .personalization import (
    classify_deck_layout,
    normalize_cards_per_row,
    normalize_class_art_mode,
    normalize_dust_display,
    normalize_mana_curve_mode,
    resolve_cards_per_row,
)
from .prepared_card_cache import load_prepared_card, store_prepared_card
from .text_size import normalize_title_size, title_size_scale
from .output_resolution import OUTPUT_SIDES, normalize_image_resolution


IMAGE_STYLE_CLASSIC = "classic"
IMAGE_STYLE_PARCHMENT = "parchment"
IMAGE_STYLE_CUSTOM = "custom"
VALID_IMAGE_STYLES = {
    IMAGE_STYLE_CLASSIC,
    IMAGE_STYLE_PARCHMENT,
    IMAGE_STYLE_CUSTOM,
}
_CARD_CELL_CACHE_MAX = 96
_card_cell_cache_lock = threading.RLock()
_card_cell_cache: OrderedDict[tuple, tuple[Image.Image, tuple[int, int, int, int]]] = (
    OrderedDict()
)
_VISIBLE_CARD_ALPHA_THRESHOLD = 128
_VISIBLE_CARD_MIN_COVERAGE = 0.01
# A 10% wider grid cell accommodates even the widest current card frames at a
# shared height without horizontally squeezing them (545 px max at 677 px).
_CARD_GRID_WIDTH_RATIO = 1.10
_STANDARD_CARD_VISIBLE_ASPECT = 477 / 677


def _grid_cell_size(base_size: int) -> tuple[int, int]:
    return (
        max(1, int(round(base_size * _CARD_GRID_WIDTH_RATIO))),
        max(1, int(base_size * 1.354)),
    )


def _is_sideboard_card_id(card_id, sideboard_slugs=None) -> bool:
    """Return True only for the explicit ``-side`` slug suffix.

    A substring check misclassified ordinary cards such as
    ``beaming-sidekick`` and moved/tinted them as sideboard cards.
    """
    if sideboard_slugs is not None:
        return card_id in sideboard_slugs
    return str(card_id or "").endswith("-side")


def normalize_image_style(value) -> str:
    style = str(value or IMAGE_STYLE_CLASSIC).strip().lower()
    return style if style in VALID_IMAGE_STYLES else IMAGE_STYLE_CLASSIC


def _rust_render_enabled() -> bool:
    return os.getenv("DECKVIEW_RUST_RENDER", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _rust_render_strict() -> bool:
    """Fail instead of silently falling back during native benchmarks/tests."""
    return any(
        os.getenv(key, "0").strip().lower() in ("1", "true", "yes", "on")
        for key in ("DECKVIEW_RUST_RENDER_STRICT", "DECKVIEW_RUST_REQUIRED")
    )


def _fast_pil_enabled() -> bool:
    """Opt-in switch for output-compatible Pillow/NumPy optimizations."""
    return os.getenv("DECKVIEW_FAST_PIL", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _tint_sideboard_legacy(im: Image.Image) -> Image.Image:
    """Reference implementation kept as a rollback path."""
    im = im.convert("RGBA")
    width, height = im.size
    pixels = im.load()
    for x in range(width):
        for y in range(height):
            red, green, blue, alpha = pixels[x, y]
            im.putpixel(
                (x, y),
                (
                    min(255, red + 100),
                    min(255, green + 40),
                    min(255, blue + 45),
                    alpha,
                ),
            )
    return im


def _tint_sideboard(im: Image.Image) -> Image.Image:
    """Tint a sideboard card, with a bit-exact vectorized fast path."""
    if not _fast_pil_enabled():
        return _tint_sideboard_legacy(im)

    try:
        pixels = np.asarray(im.convert("RGBA"), dtype=np.uint8).copy()
        rgb = pixels[:, :, :3].astype(np.uint16)
        rgb += np.array([100, 40, 45], dtype=np.uint16)
        pixels[:, :, :3] = np.minimum(rgb, 255).astype(np.uint8)
        return Image.fromarray(pixels, mode="RGBA")
    except Exception as exc:
        print(f"[Deckview Fast PIL] sideboard fallback: {type(exc).__name__}: {exc}")
        return _tint_sideboard_legacy(im)


def _build_rust_payload(
    counters,
    mana,
    deck_cost,
    response,
    deck_name=None,
    sideboard_slugs=None,
    image_style=IMAGE_STYLE_CLASSIC,
    image_background=None,
    image_font="auto",
    image_text_size="normal",
    image_dust_display="normal",
    image_class_art=None,
    image_layout=None,
    image_mana_curve=None,
    image_resolution="standard",
):
    image_resolution = normalize_image_resolution(image_resolution)
    image_style = normalize_image_style(image_style)
    normalized_font = normalize_font_key(image_font)
    normalized_text_size = normalize_title_size(image_text_size)
    title_scale = title_size_scale(normalized_text_size)
    dust_display = normalize_dust_display(image_dust_display)
    class_art = (
        image_class_art
        if isinstance(image_class_art, dict)
        else {"mode": "class", "path": None}
    )
    class_art_mode = normalize_class_art_mode(class_art.get("mode"))
    mana_curve = (
        image_mana_curve
        if isinstance(image_mana_curve, dict)
        else {"mode": "chart", "path": None}
    )
    mana_curve_mode = normalize_mana_curve_mode(mana_curve.get("mode"))
    title_font_key = (
        "belwe"
        if normalized_font == "auto" and image_style == IMAGE_STYLE_PARCHMENT
        else "hearthstone"
        if normalized_font == "auto"
        else normalized_font
    )
    font_option = FONT_OPTIONS.get(title_font_key, FONT_OPTIONS["hearthstone"])
    font_path = os.path.abspath(str(font_option["path"]))
    n_cards = len(counters)
    category = classify_deck_layout(counters, sideboard_slugs)
    n_cols = resolve_cards_per_row(image_layout, category, n_cards)
    size = min(500, max(300, 3000 // max(1, n_cols)))
    cell_w, cell_h = _grid_cell_size(size)
    top_margin = (
        max(250, round(128 * title_scale) + round(105 * max(1.0, title_scale)))
        if deck_name
        else 0
    )

    def card_sort_key(card_id):
        is_module = 1 if (_is_sideboard_card_id(card_id, sideboard_slugs) or getattr(card_id, "is_zilliax_module", False)) else 0
        return (is_module, mana.get(card_id, 0))

    sorted_card_ids = sorted(counters.keys(), key=card_sort_key)
    card_names = {}
    card_types = {}
    for c in response.get("cards", []):
        slug = c.get("slug")
        if slug:
            card_names[slug] = (c.get("name") or "?").strip() or "?"
            card_types[slug] = str(c.get("deckviewCardType") or "").upper()

    cards = []
    for card_id in sorted_card_ids:
        cards.append(
            {
                "path": os.path.abspath(f"{FOLDER}{card_id}.png"),
                "name": card_names.get(card_id, "?"),
                "count": int(counters.get(card_id, 1)),
                "mana": int(mana.get(card_id, 0)),
                "is_side": _is_sideboard_card_id(card_id, sideboard_slugs),
                "card_type": card_types.get(card_id, ""),
            }
        )

    deck_class_slug = response.get("class", {}).get("slug", "neutral")
    custom_logo_path = str(class_art.get("path") or "").strip()
    class_asset_path = (
        os.path.abspath(custom_logo_path)
        if class_art_mode == "logo" and custom_logo_path
        else os.path.abspath(f"class/class_{deck_class_slug}.png")
    )
    background = image_background if isinstance(image_background, dict) else {}
    background_kind = str(background.get("kind") or "").strip().lower()
    background_value = str(background.get("value") or "").strip()
    background_path = (
        os.path.abspath(background_value)
        if image_style == IMAGE_STYLE_CUSTOM
        and background_kind == "image"
        and background_value
        else ""
    )
    mana_curve_path_value = str(mana_curve.get("path") or "").strip()
    mana_curve_path = (
        os.path.abspath(mana_curve_path_value)
        if mana_curve_mode == "image" and mana_curve_path_value
        else ""
    )
    rune_assets = []
    for rune_name, count in (response.get("runeSlots") or {}).items():
        rune_assets.append(
            {
                "kind": str(rune_name),
                "count": max(0, min(3, int(count or 0))),
                "path": os.path.abspath(f"death_knight/{rune_name}.png"),
            }
        )
    card_paths = [card["path"] for card in cards]
    supporting_paths = [
        os.path.abspath("assets/x2-white.png"),
        os.path.abspath("assets/dust.png"),
        class_asset_path,
        font_path,
        os.path.abspath(str(BELWE_FONT_PATH)),
        os.path.abspath(str(PARCHMENT_PATH)),
        os.path.abspath(str(WOOD_FRAME_PATH)),
        background_path,
        mana_curve_path,
        *(rune["path"] for rune in rune_assets),
    ]
    allowed_roots = {
        os.path.realpath(FOLDER),
        os.path.realpath(os.path.dirname(_TITLE_FONT_PATH)),
        os.path.realpath(os.getcwd()),
    }
    allowed_roots.update(
        os.path.dirname(os.path.realpath(path))
        for path in card_paths + supporting_paths
        if path
    )

    return {
        "schema_version": 2,
        "renderer_version": "deckview-native/0.3.0",
        "cards": cards,
        "layout": {
            "cell_w": cell_w,
            "cell_h": cell_h,
            "row_gap": 72,
            "top_margin": top_margin,
            "bottom_margin": 800,
            "n_cols": n_cols,
        },
        "assets": {
            "water_path": os.path.abspath("assets/x2-white.png"),
            "dust_asset_path": os.path.abspath("assets/dust.png"),
            "class_asset_path": class_asset_path,
            "font_path": font_path,
            "ornament_font_path": os.path.abspath(str(BELWE_FONT_PATH)),
            "parchment_path": os.path.abspath(str(PARCHMENT_PATH)),
            "wood_frame_path": os.path.abspath(str(WOOD_FRAME_PATH)),
            "allowed_roots": sorted(allowed_roots),
        },
        "background": {
            "style": image_style,
            "kind": background_kind,
            "value": background_value if background_kind == "gradient" else "",
            "path": background_path,
            "blur": normalize_background_blur(background.get("blur")),
        },
        "typography": {
            "font_key": title_font_key,
            "title_scale": title_scale,
        },
        "dust": {"mode": dust_display},
        "class_art": {
            "mode": class_art_mode,
        },
        "mana_curve": {
            "mode": mana_curve_mode,
            "path": mana_curve_path,
        },
        "runes": rune_assets,
        "output": {
            "max_output_side": OUTPUT_SIDES[image_resolution],
            "jpeg_quality": 95 if image_resolution == "high" else 92,
        },
        "deck": {
            "cost": int(deck_cost or 0),
            "name": deck_name or "",
        },
    }


def _place_cards_rust(
    counters,
    mana,
    class_id,
    deck_cost,
    response,
    deck_name=None,
    sideboard_slugs=None,
    image_style=IMAGE_STYLE_CLASSIC,
    image_background=None,
    image_font="auto",
    image_text_size="normal",
    image_dust_display="normal",
    image_class_art=None,
    image_layout=None,
    image_mana_curve=None,
    image_resolution="standard",
):
    if not _rust_render_enabled():
        return None
    try:
        from deckview_core import render_deck_image

        started = time.perf_counter()
        payload = _build_rust_payload(
            counters,
            mana,
            deck_cost,
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
        image_bytes = render_deck_image(payload)
        image = Image.open(BytesIO(image_bytes)).convert("RGB")
        image.info["deckview_renderer"] = "rust"
        image.info["deckview_image_resolution"] = image_resolution
        # Downstream API/Telegram delivery can persist the native JPEG without
        # paying for a second Pillow encode. Keep the PIL image for the public
        # Python contract and visual fallback compatibility.
        image.info["deckview_encoded_jpeg"] = bytes(image_bytes)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        print(f"[Deckview Rust] place_cards rendered in {elapsed_ms} ms")
        return image
    except Exception as e:
        if _rust_render_strict():
            raise
        print(f"[Deckview Rust] fallback to Python renderer: {type(e).__name__}: {e}")
        return None


def _trim_rgba_alpha_bbox(im: Image.Image) -> Image.Image:
    """Обрезать полностью прозрачные поля (у x2.png часто большой отступ снизу — из‑за него бейдж «висит»)."""
    im = im.convert("RGBA")
    bb = im.getchannel("A").getbbox()
    return im.crop(bb) if bb else im


# Сдвиг x2 вниз от нижнего края карты (px), затем бейдж режется по высоте зазора до следующего ряда.
X2_DROP_BELOW_CARD_MIN = 12
X2_DROP_BELOW_CARD_MAX = 28


def _make_parchment_x2_badge(card_width: int) -> Image.Image:
    """Compact embossed multiplier with subtle Hearthstone-style ornaments."""
    from PIL import ImageFont

    badge_width = max(92, int(card_width * 0.34))
    badge_height = max(42, int(card_width * 0.15))
    badge = Image.new("RGBA", (badge_width, badge_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(badge)
    font_size = max(32, int(card_width * 0.11))
    font = ImageFont.truetype(str(BELWE_FONT_PATH), font_size)
    text = "×2"
    center_x = badge_width // 2
    center_y = badge_height // 2 - 1
    box = draw.textbbox((0, 0), text, font=font, stroke_width=2)
    text_width = box[2] - box[0]
    line_y = center_y + 3
    line_color = (137, 89, 41, 205)
    left_end = center_x - text_width // 2 - 10
    right_start = center_x + text_width // 2 + 10
    if left_end > 10:
        draw.line((8, line_y, left_end, line_y), fill=line_color, width=2)
        draw.ellipse((6, line_y - 2, 10, line_y + 2), fill=(181, 127, 55, 220))
    if right_start < badge_width - 10:
        draw.line(
            (right_start, line_y, badge_width - 8, line_y),
            fill=line_color,
            width=2,
        )
        draw.ellipse(
            (badge_width - 10, line_y - 2, badge_width - 6, line_y + 2),
            fill=(181, 127, 55, 220),
        )
    draw.text(
        (center_x + 2, center_y + 3),
        text,
        font=font,
        fill=(72, 42, 24, 105),
        anchor="mm",
    )
    draw.text(
        (center_x, center_y),
        text,
        font=font,
        fill=(63, 39, 25, 255),
        anchor="mm",
        stroke_width=2,
        stroke_fill=(224, 178, 87, 230),
    )
    return badge


def _make_white_x2_badge(card_width: int) -> Image.Image:
    """High-contrast multiplier for classic and user-supplied backgrounds."""
    from PIL import ImageFont

    badge_width = max(92, int(card_width * 0.34))
    badge_height = max(42, int(card_width * 0.15))
    badge = Image.new("RGBA", (badge_width, badge_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(badge)
    font_size = max(32, int(card_width * 0.11))
    font = ImageFont.truetype(str(BELWE_FONT_PATH), font_size)
    text = "×2"
    center_x = badge_width // 2
    center_y = badge_height // 2 - 1
    box = draw.textbbox((0, 0), text, font=font, stroke_width=2)
    text_width = box[2] - box[0]
    line_y = center_y + 3
    line_color = (255, 255, 255, 218)
    left_end = center_x - text_width // 2 - 10
    right_start = center_x + text_width // 2 + 10
    if left_end > 10:
        draw.line((8, line_y, left_end, line_y), fill=line_color, width=2)
        draw.ellipse((6, line_y - 2, 10, line_y + 2), fill=(255, 255, 255, 235))
    if right_start < badge_width - 10:
        draw.line(
            (right_start, line_y, badge_width - 8, line_y),
            fill=line_color,
            width=2,
        )
        draw.ellipse(
            (badge_width - 10, line_y - 2, badge_width - 6, line_y + 2),
            fill=(255, 255, 255, 235),
        )
    draw.text(
        (center_x + 2, center_y + 3),
        text,
        font=font,
        fill=(0, 0, 0, 150),
        anchor="mm",
        stroke_width=3,
        stroke_fill=(0, 0, 0, 85),
    )
    draw.text(
        (center_x, center_y),
        text,
        font=font,
        fill=(255, 255, 255, 255),
        anchor="mm",
        stroke_width=2,
        stroke_fill=(20, 25, 32, 235),
    )
    return badge


def _make_x2_badge(card_width: int, image_style: str) -> Image.Image:
    """Choose a transparent multiplier with contrast appropriate to the theme."""
    style = normalize_image_style(image_style)
    if style == IMAGE_STYLE_PARCHMENT:
        return _make_parchment_x2_badge(card_width)
    return _make_white_x2_badge(card_width)


def _make_custom_mana_curve_overlay(
    chart_width: int,
    chart_height: int,
    curve: dict[int, int],
    label_font,
    count_font,
) -> tuple[Image.Image, tuple[int, int]]:
    """Render a transparent, high-contrast mana curve for arbitrary photos."""
    bins = tuple(range(8))
    maximum = max((int(curve.get(bucket, 0)) for bucket in bins), default=1)
    maximum = max(1, maximum)
    pad_x, pad_top, pad_bottom = 24, 44, 66
    size = (chart_width + pad_x * 2, chart_height + pad_top + pad_bottom)
    shadow = Image.new("RGBA", size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    foreground = Image.new("RGBA", size, (0, 0, 0, 0))

    gap = max(12, int(chart_width * 0.014))
    bar_width = max(10, int((chart_width - gap * 7) / 8))
    base_y = pad_top + chart_height
    chart_x = pad_x
    bar_colors = (
        (72, 167, 213),
        (67, 157, 211),
        (65, 146, 209),
        (66, 135, 205),
        (71, 124, 199),
        (78, 114, 193),
        (88, 105, 187),
        (99, 96, 180),
    )

    # Shadows belong to the individual chart elements, not to an opaque panel.
    shadow_draw.line(
        (chart_x - 2, base_y + 4, chart_x + chart_width + 2, base_y + 4),
        fill=(0, 0, 0, 185),
        width=10,
    )
    geometry: list[tuple[int, int, int, int, int, int]] = []
    for index, bucket in enumerate(bins):
        count = max(0, int(curve.get(bucket, 0)))
        bar_height = (
            max(12, round((count / maximum) * (chart_height - 28)))
            if count
            else 0
        )
        x0 = chart_x + index * (bar_width + gap)
        x1 = x0 + bar_width
        y0 = base_y - bar_height
        geometry.append((bucket, count, x0, y0, x1, base_y))
        if count:
            shadow_draw.rounded_rectangle(
                (x0 - 4, y0, x1 + 5, base_y + 7),
                radius=11,
                fill=(0, 0, 0, 185),
            )
    shadow = shadow.filter(ImageFilter.GaussianBlur(7))
    foreground = Image.alpha_composite(shadow, foreground)
    draw = ImageDraw.Draw(foreground)

    draw.line(
        (chart_x, base_y, chart_x + chart_width, base_y),
        fill=(244, 249, 255, 220),
        width=3,
    )
    for index, (bucket, count, x0, y0, x1, y1) in enumerate(geometry):
        if count:
            color = bar_colors[index]
            draw.rounded_rectangle(
                (x0, y0, x1, y1),
                radius=9,
                fill=(*color, 238),
                outline=(241, 248, 255, 230),
                width=3,
            )
            draw.line(
                (x0 + 8, y0 + 7, x1 - 8, y0 + 7),
                fill=(255, 255, 255, 145),
                width=3,
            )
            draw.line(
                (x0 + 7, y0 + 10, x0 + 7, y1 - 7),
                fill=(221, 244, 255, 120),
                width=max(2, bar_width // 18),
            )
            draw.text(
                ((x0 + x1) // 2, y0 - 19),
                str(count),
                font=count_font,
                fill=(255, 255, 255, 255),
                anchor="mm",
                stroke_width=3,
                stroke_fill=(13, 18, 25, 230),
            )

        label = "7+" if bucket == 7 else str(bucket)
        draw.text(
            ((x0 + x1) // 2, base_y + 30),
            label,
            font=label_font,
            fill=(255, 255, 255, 255),
            anchor="mm",
            stroke_width=3,
            stroke_fill=(13, 18, 25, 235),
        )
    return foreground, (-pad_x, -pad_top)


def _paste_card_in_cell(
    im: Image.Image,
    cell_w: int,
    cell_h: int,
    water: Image.Image | None,
    draw_x2: bool,
    card_type: str = "",
) -> tuple[Image.Image, tuple[int, int, int, int] | None]:
    """
    Вписать видимую рамку карты в расширенную ячейку без искажения пропорций.

    Возвращает (ячейка, x2_layout) или (ячейка, None).
    x2_layout = (ox, oy, nw, nh) — геометрия вписанной карты внутри ячейки; ×2 рисуется снаружи на полотне колоды.
    """
    im = im.convert("RGBA")
    alpha = im.getchannel("A")
    try:
        alpha_pixels = np.asarray(alpha, dtype=np.uint8)
        visible_pixels = alpha_pixels >= _VISIBLE_CARD_ALPHA_THRESHOLD
        min_row_pixels = max(
            2,
            round(im.width * _VISIBLE_CARD_MIN_COVERAGE),
        )
        min_column_pixels = max(
            2,
            round(im.height * _VISIBLE_CARD_MIN_COVERAGE),
        )
        visible_rows = np.flatnonzero(
            visible_pixels.sum(axis=1) >= min_row_pixels
        )
        visible_columns = np.flatnonzero(
            visible_pixels.sum(axis=0) >= min_column_pixels
        )
        visible_box = (
            (
                int(visible_columns[0]),
                int(visible_rows[0]),
                int(visible_columns[-1]) + 1,
                int(visible_rows[-1]) + 1,
            )
            if visible_rows.size and visible_columns.size
            else None
        )
    except Exception:
        visible_box = None
    if visible_box is None:
        visible_box = alpha.getbbox()
    if visible_box is not None:
        # Some Arena/Blizzard exports contain a large, barely visible shadow
        # below the actual frame. Cropping by any non-zero alpha makes those
        # cards look noticeably shorter than their neighbours.
        im = im.crop(visible_box)
    iw, ih = im.size
    if iw < 1 or ih < 1:
        empty = Image.new("RGBA", (cell_w, cell_h), (0, 0, 0, 0))
        return empty, None
    # Every card keeps its source proportions. The grid cell itself is wide
    # enough for current spell, minion, location, hero and quest frames at the
    # common row height; the min() remains a safe fallback for future outliers.
    scale = min(cell_h / ih, cell_w / iw)
    nw = max(1, int(round(iw * scale)))
    nh = max(1, int(round(ih * scale)))
    # Location frames are a few percent narrower than the standard frame and
    # therefore look isolated inside the same grid cell. A small horizontal
    # optical correction brings only LOCATION cards to the standard footprint;
    # broad special frames (for example Shaladrassil) remain untouched.
    if str(card_type or "").strip().upper() == "LOCATION" and nh == cell_h:
        standard_width = min(
            cell_w,
            max(1, int(round(cell_h * _STANDARD_CARD_VISIBLE_ASPECT))),
        )
        nw = max(nw, standard_width)
    scaled = im.resize((nw, nh), Image.LANCZOS).convert("RGBA")

    cell = Image.new("RGBA", (cell_w, cell_h), (0, 0, 0, 0))
    ox = (cell_w - nw) // 2
    oy = 0
    cell.paste(scaled, (ox, oy), scaled)

    if draw_x2 and water is not None:
        return cell, (ox, oy, nw, nh)
    return cell, None


def _remember_prepared_card_cell(key, prepared) -> None:
    with _card_cell_cache_lock:
        _card_cell_cache[key] = prepared
        _card_cell_cache.move_to_end(key)
        while len(_card_cell_cache) > _CARD_CELL_CACHE_MAX:
            _card_cell_cache.popitem(last=False)


def _prepared_card_cell(
    card_path: str,
    cell_size: tuple[int, int],
    *,
    is_sideboard: bool,
    water: Image.Image,
    card_type: str = "",
) -> tuple[Image.Image, tuple[int, int, int, int]] | None:
    """Load, trim and resize a card once per worker and target cell size."""
    stat = os.stat(card_path)
    key = (
        os.path.realpath(card_path),
        stat.st_mtime_ns,
        stat.st_size,
        tuple(cell_size),
        bool(is_sideboard),
        str(card_type or "").strip().upper(),
    )
    with _card_cell_cache_lock:
        cached = _card_cell_cache.get(key)
        if cached is not None:
            _card_cell_cache.move_to_end(key)
            return cached

    prepared = load_prepared_card(key, tuple(cell_size))
    if prepared is not None:
        _remember_prepared_card_cell(key, prepared)
        return prepared

    with Image.open(card_path) as source:
        card = source.convert("RGBA")
    if not card.getchannel("A").getbbox():
        return None
    if is_sideboard:
        card = _tint_sideboard(card)
    cell, layout = _paste_card_in_cell(
        card,
        cell_size[0],
        cell_size[1],
        water,
        True,
        card_type=card_type,
    )
    if layout is None:
        return None
    prepared = (cell, layout)
    store_prepared_card(key, prepared)
    _remember_prepared_card_cell(key, prepared)
    return prepared


def _clear_card_cell_cache() -> None:
    with _card_cell_cache_lock:
        _card_cell_cache.clear()


def _fit_deck_title(text: str, font_key: str, target_size: int, max_width: int):
    """Keep large presets visibly large by wrapping before shrinking."""
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    words = str(text or "").split()
    font_size = max(34, int(target_size))
    while font_size >= 34:
        font = load_title_font(font_key, font_size)
        lines: list[str] = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            width = probe.textbbox((0, 0), candidate, font=font)[2]
            if current and width > max_width:
                lines.append(current)
                current = word
            else:
                current = candidate
        if current:
            lines.append(current)
        rendered = "\n".join(lines) if lines else str(text or "")
        bbox = probe.multiline_textbbox(
            (0, 0), rendered, font=font, align="center", spacing=8
        )
        if len(lines) <= 3 and bbox[2] - bbox[0] <= max_width:
            return rendered, font, bbox
        font_size -= 4
    font = load_title_font(font_key, 34)
    rendered = str(text or "")
    return rendered, font, probe.textbbox((0, 0), rendered, font=font)


def _fit_asset_to_slot(
    asset: Image.Image,
    slot: tuple[int, int, int, int],
    *,
    max_fill: float = 1.0,
):
    """Contain an uploaded asset and center it in a footer slot."""
    image = asset.convert("RGBA")
    alpha_box = image.getchannel("A").getbbox()
    if alpha_box:
        image = image.crop(alpha_box)
    x, y, width, height = slot
    if image.width < 1 or image.height < 1 or width < 1 or height < 1:
        return image, (x, y)
    fill = max(0.1, min(1.0, float(max_fill)))
    scale = min((width * fill) / image.width, (height * fill) / image.height)
    target = (
        max(1, round(image.width * scale)),
        max(1, round(image.height * scale)),
    )
    image = image.resize(target, Image.LANCZOS)
    return image, (
        x + (width - image.width) // 2,
        y + (height - image.height) // 2,
    )


class _SkipManaCurve(Exception):
    pass


def _make_gradient_90(size):
    w, h = size
    colors = [
        (0, 0, 0),       # #000000
        (13, 21, 33),    # #0d1521
        (7, 14, 24),     # #070e18
        (13, 21, 33),    # #0d1521
        (0, 0, 0)        # #000000
    ]

    # Создаем горизонтальный градиент (ось x)
    x = np.linspace(0, 1, w)

    def interpolate(x, colors):
        n = len(colors) - 1
        idx = (x * n).astype(int)
        idx = np.clip(idx, 0, n - 1)
        t = (x * n) - idx

        c_start = np.array([colors[i] for i in idx])
        c_end = np.array([colors[i+1] for i in idx])

        return (c_start + (c_end - c_start) * t[:, np.newaxis]).astype(np.uint8)

    line = interpolate(x, colors)
    # Повторяем строку по вертикали
    img_array = np.tile(line[np.newaxis, :, :], (h, 1, 1))

    return Image.fromarray(img_array).convert("RGBA")


@to_thread
def place_cards(
    counters,
    mana,
    class_id,
    deck_cost,
    response,
    deck_name=None,
    sideboard_slugs=None,
    image_style=IMAGE_STYLE_CLASSIC,
    image_background=None,
    image_font="auto",
    image_text_size="normal",
    image_dust_display="normal",
    image_class_art=None,
    image_layout=None,
    image_mana_curve=None,
    image_resolution="standard",
):
    image_resolution = normalize_image_resolution(image_resolution)
    max_output_side = OUTPUT_SIDES[image_resolution]
    image_style = normalize_image_style(image_style)
    normalized_font = normalize_font_key(image_font)
    normalized_text_size = normalize_title_size(image_text_size)
    title_scale = title_size_scale(normalized_text_size)
    dust_display = normalize_dust_display(image_dust_display)
    class_art = (
        image_class_art
        if isinstance(image_class_art, dict)
        else {"mode": "class", "path": None}
    )
    class_art_mode = normalize_class_art_mode(class_art.get("mode"))
    mana_curve = (
        image_mana_curve
        if isinstance(image_mana_curve, dict)
        else {"mode": "chart", "path": None}
    )
    mana_curve_mode = normalize_mana_curve_mode(mana_curve.get("mode"))
    mana_curve_path = str(mana_curve.get("path") or "").strip()
    title_font_key = (
        "belwe"
        if normalized_font == "auto" and image_style == IMAGE_STYLE_PARCHMENT
        else "hearthstone"
        if normalized_font == "auto"
        else normalized_font
    )
    # A custom background is the classic layout with only its backdrop
    # replaced. Parchment remains the sole wood-framed decorative variant.
    is_decorative = image_style == IMAGE_STYLE_PARCHMENT
    is_custom = image_style == IMAGE_STYLE_CUSTOM
    rust_image = _place_cards_rust(
        counters,
        mana,
        class_id,
        deck_cost,
        response,
        deck_name=deck_name,
        sideboard_slugs=sideboard_slugs,
        image_style=image_style,
        image_background=image_background,
        image_font=normalized_font,
        image_text_size=normalized_text_size,
        image_dust_display=dust_display,
        image_class_art=class_art,
        image_layout=image_layout,
        image_mana_curve=mana_curve,
        image_resolution=image_resolution,
    )
    if rust_image is not None:
        return rust_image

    # The transparent Belwe multiplier needs the same breathing room in every
    # theme. Keeping the gap consistent also prevents ×2 from touching the
    # next card row in the classic layout.
    row_gap = 72
    layout_category = classify_deck_layout(counters, sideboard_slugs)
    if isinstance(image_layout, dict) and image_layout.get("_preview_category") in (
        "normal",
        "extended",
        "highlander",
    ):
        layout_category = image_layout["_preview_category"]
    n_cards = len(counters)
    n_cols = resolve_cards_per_row(image_layout, layout_category, n_cards)
    try:
        size = min(500, max(300, 3000 // max(1, n_cols)))
        water = _make_x2_badge(size, image_style)
    except Exception as e:
        print(f"Error loading watermark: {e}")
        # Create a dummy watermark if x2.png is missing
        water = Image.new("RGBA", (141, 80), (0, 0, 0, 0))

    water = _trim_rgba_alpha_bbox(water)

    sizes = _grid_cell_size(size)

    top_margin = 0
    row, col = 0, 0

    def card_sort_key(card_id):
        # Модули Зиллиакса (-side) в конец, затем по мане
        is_module = 1 if (_is_sideboard_card_id(card_id, sideboard_slugs) or getattr(card_id, "is_zilliax_module", False)) else 0
        return (is_module, mana.get(card_id, 0))

    sorted_card_ids = sorted(counters.keys(), key=card_sort_key)
    # slug -> name для подписей на плейсхолдерах отсутствующих артов
    card_names = {}
    card_types = {}
    for c in response.get("cards", []):
        slug = c.get("slug")
        if slug:
            card_names[slug] = (c.get("name") or "?").strip() or "?"
            card_types[slug] = str(c.get("deckviewCardType") or "").upper()

    n_cards = len(sorted_card_ids)
    n_cols = max(1, min(n_cards, n_cols))
    n_rows = math.ceil(n_cards / n_cols)
    width = n_cols * sizes[0]
    title_render_text = str(deck_name or "")
    title_font = None
    if deck_name:
        target_size = round((128 if is_decorative else 112) * title_scale)
        maximum_width = width - max(120, int(width * 0.08))
        title_render_text, title_font, title_bbox = _fit_deck_title(
            deck_name,
            title_font_key,
            target_size,
            maximum_width,
        )
        title_height = max(1, title_bbox[3] - title_bbox[1])
        top_margin = max(250, title_height + round(105 * max(1.0, title_scale)))
    row = top_margin
    # Адаптивная высота: ряды + отступ под руны + отступ под логотип/пыль/класс (персонаж 700px) + заголовок
    # Добавляем 800px снизу, чтобы персонаж не перекрывал нижний ряд карт
    height = n_rows * (sizes[1] + row_gap) + 800 + top_margin
    if image_style == IMAGE_STYLE_PARCHMENT:
        image = parchment_background((width, height))
    elif image_style == IMAGE_STYLE_CUSTOM:
        image = decorative_background((width, height), image_background)
    else:
        image = _make_gradient_90((width, height))

    if deck_name:
        if is_decorative:
            draw = ImageDraw.Draw(image)
            from PIL import ImageFont

            draw.multiline_text(
                (width // 2 + 3, top_margin // 2 + 5),
                title_render_text,
                font=title_font,
                fill=(76, 43, 23, 115),
                align="center",
                anchor="mm",
                spacing=8,
            )
            draw.multiline_text(
                (width // 2, top_margin // 2),
                title_render_text,
                font=title_font,
                fill=(48, 37, 28, 255),
                align="center",
                anchor="mm",
                stroke_width=2,
                stroke_fill=(239, 207, 140, 220),
                spacing=8,
            )
        else:
            draw = ImageDraw.Draw(image)
            from PIL import ImageFont

            draw.multiline_text(
                (width // 2 + 3, top_margin // 2 + 4),
                title_render_text,
                font=title_font,
                fill=(0, 0, 0, 165),
                align="center",
                anchor="mm",
                stroke_width=4,
                stroke_fill=(0, 0, 0, 140),
                spacing=8,
            )
            draw.multiline_text(
                (width // 2, top_margin // 2),
                title_render_text,
                font=title_font,
                fill=(255, 255, 255),
                align="center",
                anchor="mm",
                stroke_width=2,
                stroke_fill=(20, 24, 30),
                spacing=8,
            )

    def make_placeholder(sz, name):
        """Плейсхолдер для карты без арта: тёмный фон + название шрифтом HEARTHSTONE_CYRILLIC."""
        from PIL import ImageFont
        pl = Image.new("RGBA", sz, (45, 55, 70, 255))
        draw_pl = ImageDraw.Draw(pl)
        font_size = min(28, max(12, sz[0] // 18))
        try:
            pl_font = ImageFont.truetype(_TITLE_FONT_PATH, font_size)
        except Exception:
            pl_font = ImageFont.load_default()
        short = (name[:20] + "…") if len(name) > 20 else name
        bbox = draw_pl.textbbox((0, 0), short, font=pl_font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        tx, ty = (sz[0] - tw) // 2, (sz[1] - th) // 2
        draw_pl.text((tx + 1, ty + 1), short, font=pl_font, fill=(0, 0, 0, 180))
        draw_pl.text((tx, ty), short, font=pl_font, fill=(220, 230, 240))
        return pl

    placed_card_count = 0
    try:
        for card in sorted_card_ids:
            try:
                card_path = f"{FOLDER}{card}.png"
                prepared = _prepared_card_cell(
                    card_path,
                    sizes,
                    is_sideboard=_is_sideboard_card_id(card, sideboard_slugs),
                    water=water,
                    card_type=card_types.get(card, ""),
                )
            except FileNotFoundError:
                name = card_names.get(card, "?")
                print(f"Warning: Card image not found for {card}, using placeholder: {name}")
                im = make_placeholder(sizes, name)
                cell, placeholder_layout = _paste_card_in_cell(
                    im,
                    sizes[0],
                    sizes[1],
                    water,
                    True,
                )
                prepared = (
                    (cell, placeholder_layout)
                    if placeholder_layout is not None
                    else None
                )
            except Exception as e:
                # A single unreadable/corrupt cached art must not create a
                # deck with an empty hole. Keep the layout intact and make the
                # failure visible while the downloader refreshes the asset.
                name = card_names.get(card, "?")
                print(
                    f"Error loading image for {card}: {e}. "
                    f"Using placeholder. Traceback: {traceback.format_exc()}"
                )
                im = make_placeholder(sizes, name)
                cell, placeholder_layout = _paste_card_in_cell(
                    im,
                    sizes[0],
                    sizes[1],
                    water,
                    True,
                )
                prepared = (
                    (cell, placeholder_layout)
                    if placeholder_layout is not None
                    else None
                )
            if prepared is None:
                print(f"Warning: Image for {card} is fully transparent. Skipping.")
                continue
            cell, prepared_layout = prepared
            x2_layout = prepared_layout if counters[card] == 2 else None
            image.paste(cell, (col, row), mask=cell)
            placed_card_count += 1
            if x2_layout is not None:
                bx, by, bnw, bnh = x2_layout
                wmark = water if water.mode == "RGBA" else water.convert("RGBA")
                ww, wh = wmark.size
                drop = max(
                    X2_DROP_BELOW_CARD_MIN,
                    min(X2_DROP_BELOW_CARD_MAX, bnh // 20),
                )
                wx = col + bx + max(0, (bnw - ww) // 2)
                wy = row + by + bnh + drop
                # Не наезжать на следующий ряд (зазор 40 px между ячейками)
                next_row_y = row + sizes[1] + row_gap
                max_wy = next_row_y - wh - 2
                if wy > max_wy:
                    wy = max_wy
                wy = max(wy, row + by + bnh)
                wx = max(0, min(wx, image.width - ww))
                wy = max(0, min(wy, image.height - wh))
                image.paste(wmark, (wx, wy), mask=wmark)

            col += sizes[0]
            if col >= width:
                col = 0
                row += sizes[1] + row_gap
    except Exception as e:
        print(f"CRITICAL ERROR in card placement loop: {e}\n{traceback.format_exc()}")
        raise e

    if sorted_card_ids and placed_card_count == 0:
        # Never allow a transient asset/cache problem to become a valid blank
        # render and then be distributed from the long-lived render cache.
        raise RuntimeError("Deck render produced no visible card cells")

    if class_id == 1:
        # Центрируем руны внизу
        try:
            image = place_runes(image, response)
        except Exception as e:
            print(f"Error placing runes: {e}")

    # Базовая линия для размещения элементов под картами
    cards_bottom_y = n_rows * (sizes[1] + row_gap) + top_margin
    footer_top = cards_bottom_y
    footer_h = max(1, height - footer_top)
    dust_center_y = int(footer_top + footer_h * 0.6)
    footer_margin = max(36, int(width * 0.025))
    footer_slot_w = max(1, int(width * 0.35))
    footer_slot_h = max(1, int(footer_h * 0.72))
    footer_slot_y = footer_top + max(1, (footer_h - footer_slot_h) // 2)
    curve_slot = (footer_margin, footer_slot_y, footer_slot_w, footer_slot_h)
    art_extra_height = int(footer_h * 0.125)
    # The class illustration is intentionally a little closer to the right
    # frame than the mana curve is to the left one.  The source PNGs have
    # transparent margins, so a symmetric slot made the visible character
    # look shifted towards the centre of the composition.
    art_shift_x = max(16, int(width * 0.0375))
    art_slot = (
        width - footer_margin - footer_slot_w + art_shift_x,
        footer_top - art_extra_height,
        footer_slot_w,
        footer_h + art_extra_height,
    )
    dust_canvas = (
        image
        if dust_display != "hidden"
        else Image.new("RGBA", image.size, (0, 0, 0, 0))
    )

    # Добавляем пыль строго по центру (Сначала текст, потом иконка прямо после него)
    try:
        from PIL import ImageFont
        dust_scale = 1.28 if dust_display == "large" else 1.0
        try:
            if is_decorative:
                dust_font_size = round(92 * dust_scale)
                dust_font = ImageFont.truetype(str(BELWE_FONT_PATH), dust_font_size)
            else:
                dust_font_size = round(148 * dust_scale)
                dust_font = ImageFont.truetype(_TITLE_FONT_PATH, dust_font_size)
        except:
            dust_font = FONT

        draw = ImageDraw.Draw(dust_canvas)
        dust_text = (
            f"{int(deck_cost):,}".replace(",", " ")
            if is_decorative
            else f"{deck_cost}"
        )
        text_bbox = draw.textbbox((0, 0), dust_text, font=dust_font)
        t_w = text_bbox[2] - text_bbox[0]
        t_h = text_bbox[3] - text_bbox[1]

        dust_bg = Image.open("assets/dust.png").convert("RGBA")
        if is_decorative:
            dust_bg = _trim_rgba_alpha_bbox(dust_bg)
        # Делаем иконку примерно того же размера (высоты), что и текст
        dust_w = int(t_h * (0.82 if is_decorative else 1.1))
        dust_h = int(dust_w * dust_bg.height / dust_bg.width)
        dust_bg = dust_bg.resize((dust_w, dust_h))

        # Общая ширина: текст + отступ + иконка
        spacing = 12 if is_decorative else 15
        total_w = t_w + spacing + dust_w
        start_x = (width - total_w) // 2

        t_x = start_x
        t_y = dust_center_y - (t_h // 2) - text_bbox[1]

        icon_x = start_x + t_w + spacing
        icon_y = dust_center_y - (dust_h // 2)

        dust_canvas.paste(dust_bg, (int(icon_x), int(icon_y)), mask=dust_bg)

        if is_decorative:
            draw.text(
                (t_x + 2, t_y + 4),
                dust_text,
                font=dust_font,
                fill=(76, 43, 23, 105),
            )
            draw.text(
                (t_x, t_y),
                dust_text,
                font=dust_font,
                fill=(58, 39, 27),
                stroke_width=2,
                stroke_fill=(239, 207, 140),
            )
        else:
            for offset_x in [-3, -2, -1, 1, 2, 3]:
                for offset_y in [-3, -2, -1, 1, 2, 3]:
                    draw.text((t_x + offset_x, t_y + offset_y), dust_text, font=dust_font, fill=(0, 0, 0))
            draw.text((t_x, t_y), dust_text, font=dust_font, fill=(255, 255, 255))
    except Exception as e:
        print(f"Ошибка при добавлении стоимости пыли: {e}")

    # Добавляем манакривую слева (столбчатая диаграмма)
    try:
        if mana_curve_mode != "chart":
            raise _SkipManaCurve()
        from PIL import ImageFont
        # Считаем ману по кривой (0–6 и 7+)
        bins = [0, 1, 2, 3, 4, 5, 6, 7]
        curve = {b: 0 for b in bins}
        for card_id, count in counters.items():
            cost = mana.get(card_id, 0)
            # kolodahs.ru uses negative service values for configurable cards
            # such as Zilliax. They belong in the zero-cost visual bucket.
            bucket = max(0, 7 if cost >= 7 else int(cost))
            curve[bucket] += count

        max_count = max(curve.values()) if curve else 1
        if is_decorative:
            chart_w = min(curve_slot[2], 900)
            chart_h = min(380, int(curve_slot[3] * 0.78))
            chart_x = curve_slot[0] + (curve_slot[2] - chart_w) // 2
        else:
            chart_w = min(curve_slot[2], 900)
            chart_h = min(380, int(curve_slot[3] * 0.75))
            chart_x = curve_slot[0] + (curve_slot[2] - chart_w) // 2
        chart_y = curve_slot[1] + (curve_slot[3] - chart_h) // 2

        draw = ImageDraw.Draw(image)
        if not is_decorative and not is_custom:
            bg_pad = 16
            bg_pad_bottom = 28
            bg_rect = [
                chart_x - bg_pad,
                chart_y - bg_pad,
                chart_x + chart_w + bg_pad,
                chart_y + chart_h + bg_pad_bottom,
            ]
            draw.rounded_rectangle(
                bg_rect,
                radius=20,
                fill=(6, 10, 16, 220),
                outline=(35, 50, 65, 220),
            )

        n = len(bins)
        gap = 14 if is_decorative else 10
        bar_w = max(10, int((chart_w - gap * (n - 1)) / n))
        base_y = chart_y + chart_h
        if is_decorative:
            draw.line(
                (chart_x, base_y, chart_x + chart_w, base_y),
                fill=(89, 55, 31, 210),
                width=4,
            )

        # Цвета баров (мягкий градиент по мане)
        bar_colors = (
            [
                (64, 154, 211),
                (55, 145, 207),
                (48, 135, 201),
                (44, 124, 194),
                (48, 112, 184),
                (55, 101, 171),
                (64, 91, 157),
                (75, 81, 143),
            ]
            if is_decorative
            else [
                (120, 210, 255),  # 0
                (120, 200, 255),  # 1
                (120, 185, 255),  # 2
                (120, 170, 255),  # 3
                (120, 155, 255),  # 4
                (120, 140, 255),  # 5
                (120, 125, 255),  # 6
                (120, 110, 255),  # 7+
            ]
        )

        try:
            label_font = ImageFont.truetype(
                str(BELWE_FONT_PATH) if is_decorative else _TITLE_FONT_PATH,
                36,
            )
            count_font = ImageFont.truetype(str(BELWE_FONT_PATH), 30)
        except Exception:
            label_font = FONT
            count_font = FONT

        if is_custom:
            curve_overlay, offset = _make_custom_mana_curve_overlay(
                chart_w,
                chart_h,
                curve,
                label_font,
                count_font,
            )
            image.alpha_composite(
                curve_overlay,
                (chart_x + offset[0], chart_y + offset[1]),
            )
        else:
            for i, b in enumerate(bins):
                count = curve[b]
                bar_h = (
                    int((count / max_count) * (chart_h - 20))
                    if max_count > 0
                    else 0
                )
                x0 = chart_x + i * (bar_w + gap)
                y0 = base_y - bar_h
                x1 = x0 + bar_w
                y1 = base_y
                color = (
                    bar_colors[i]
                    if i < len(bar_colors)
                    else (120, 110, 255)
                )
                if is_decorative:
                    if bar_h > 0:
                        draw.rounded_rectangle(
                            [x0, y0, x1, y1],
                            radius=9,
                            fill=color,
                            outline=(73, 49, 32),
                            width=3,
                        )
                        highlight_x = min(x1 - 4, x0 + max(5, bar_w // 7))
                        draw.line(
                            (highlight_x, y0 + 8, highlight_x, y1 - 6),
                            fill=(151, 210, 237, 145),
                            width=max(2, bar_w // 18),
                        )
                        draw.text(
                            ((x0 + x1) // 2, y0 - 20),
                            str(count),
                            font=count_font,
                            fill=(58, 39, 27),
                            anchor="mm",
                            stroke_width=1,
                            stroke_fill=(239, 207, 140),
                        )
                else:
                    muted = (
                        int(color[0] * 0.85),
                        int(color[1] * 0.85),
                        int(color[2] * 0.85),
                    )
                    draw.rounded_rectangle(
                        [x0, y0, x1, y1],
                        radius=8,
                        fill=muted,
                    )

                # Подпись маны под каждым столбцом (0, 1, 2, … 7+)
                label = "7+" if b == 7 else str(b)
                label_y = base_y + int(chart_h * 0.1)
                if is_decorative:
                    draw.text(
                        (x0 + bar_w // 2, label_y),
                        label,
                        font=label_font,
                        fill=(58, 39, 27),
                        anchor="mm",
                        stroke_width=1,
                        stroke_fill=(239, 207, 140),
                    )
                else:
                    draw.text(
                        (x0 + bar_w // 2, label_y),
                        label,
                        font=label_font,
                        fill=(245, 249, 255, 245),
                        anchor="mm",
                        stroke_width=1,
                        stroke_fill=(12, 17, 24, 220),
                    )
    except _SkipManaCurve:
        pass
    except Exception as e:
        print(f"Ошибка при добавлении манакривой: {e}")

    if mana_curve_mode == "image" and os.path.isfile(mana_curve_path):
        try:
            with Image.open(mana_curve_path) as source:
                replacement, position = _fit_asset_to_slot(
                    source,
                    curve_slot,
                    max_fill=0.7,
                )
            image.paste(replacement, position, mask=replacement)
        except Exception as e:
            print(f"Ошибка картинки вместо манакривой: {e}")

    # Добавляем арт класса или пользовательский логотип справа.
    deck_class_slug = response.get("class", {}).get("slug", "neutral")
    custom_logo_path = str(class_art.get("path") or "").strip()
    use_custom_logo = (
        class_art_mode == "logo"
        and custom_logo_path
        and os.path.isfile(custom_logo_path)
    )
    bg_path = (
        custom_logo_path
        if use_custom_logo
        else f"class/class_{deck_class_slug}.png"
    )
    try:
        with Image.open(bg_path) as source:
            class_img = source.convert("RGBA")
        if not use_custom_logo:
            # Preserve the established subdued class illustration treatment.
            class_img = class_img.point(lambda p: int(p * 0.88))
            alpha = class_img.getchannel("A").point(lambda p: int(p * 0.85))
            class_img.putalpha(alpha)
        class_img, (c_x, c_y) = _fit_asset_to_slot(
            class_img,
            art_slot,
            max_fill=0.72 if use_custom_logo else 1.0,
        )
        if use_custom_logo:
            shadow_alpha = class_img.getchannel("A").filter(
                ImageFilter.GaussianBlur(12)
            )
            shadow_layer = Image.new("RGBA", class_img.size, (0, 0, 0, 0))
            shadow_layer.putalpha(shadow_alpha.point(lambda value: value * 120 // 255))
            image.paste(shadow_layer, (c_x + 8, c_y + 10), mask=shadow_layer)
        image.paste(class_img, (c_x, c_y), mask=class_img)
    except Exception as e:
        print(f"Warning: Could not load class art {bg_path}: {e}")

    # Telegram иногда показывает прозрачность (шахматка).
    # Делаем финальный слой полностью непрозрачным.
    image = image.convert("RGB")

    decorative_padding = 0
    if is_decorative:
        # Give the wooden frame its own margin so it never covers card edges or
        # the title. The padding is resized together with the composition.
        padding = max(36, int(min(image.size) * 0.012))
        decorative_padding = padding
        frame_size = (image.width + padding * 2, image.height + padding * 2)
        framed_canvas = parchment_background(frame_size)
        framed_canvas.alpha_composite(image.convert("RGBA"), (padding, padding))
        image = framed_canvas.convert("RGB")

    # Keep the original composition for HQ; never upscale a smaller canvas.
    w, h = image.size
    output_scale = 1.0
    if w > max_output_side or h > max_output_side:
        scale = max_output_side / max(w, h)
        output_scale = scale
        new_w = max(1, int(w * scale))
        new_h = max(1, int(h * scale))
        image = image.resize((new_w, new_h), Image.LANCZOS)
    if is_decorative:
        # The content padding is resized with tall layouts. Match the wooden
        # rail to that final padding so it cannot cover the outer cards.
        frame_width = max(
            12,
            min(46, int(decorative_padding * output_scale)),
        )
        image = Image.alpha_composite(
            image.convert("RGBA"),
            wood_frame_overlay(image.size, destination_slice=frame_width),
        ).convert("RGB")
    image.info["deckview_renderer"] = "pillow"
    image.info["deckview_image_resolution"] = image_resolution
    return image
