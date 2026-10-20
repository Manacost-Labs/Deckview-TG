#!/usr/bin/env python3
"""Render canonical 30-card and 40-card Reno decks for release checks."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from image_creator import create_picture  # noqa: E402
from image_creator.jpeg_output import write_rendered_jpeg  # noqa: E402
from image_creator.deck_retriever import _decode_deckstring  # noqa: E402


@dataclass(frozen=True)
class RegressionDeck:
    slug: str
    title: str
    code: str
    expected_cards: int


RENO_DECKS = (
    RegressionDeck(
        slug="reno-30-highlander-mage",
        title="Рено Маг — 30 карт",
        code=(
            "AAEBAf0EHr/BAsiHA/yjA/OvA/qwA/jdA63uA6GSBKfkBKqYBaijBfLEBbH+"
            "Bd/+BdSVBvGbBpidBsyiBuKmBrSnBq+oBqjOBtvjBpnqBt/qBseHB5uWB5+W"
            "B86bB4uxBwAAAA=="
        ),
        expected_cards=30,
    ),
    RegressionDeck(
        slug="reno-40-igneous-warrior",
        title="XL Рено Воин — 40 карт",
        code=(
            "AAEBAQcoS/bPAs/nAri5A4/OA5PQA/mMBOWwBIy3BO/OBLjZBJfvBOKkBf3E"
            "BaH7BYuUBveXBp+eBtGeBsekBpGoBq+oBuypBtCwBtW6Bo6/BvrJBvPKBo/P"
            "BqfTBqrqBqn1BsODB+6PB9uXB/ObB+qnB/yvB7LYB5TZBwAAAQbD6gL9xAXv"
            "zgT9xAX1swbHpAb3swbHpAbu3gbHpAaTmwf9xAUAAA=="
        ),
        expected_cards=40,
    ),
)


def validate_deck(deck: RegressionDeck) -> dict[str, int]:
    cards, _heroes, _deck_format, sideboards = _decode_deckstring(deck.code)
    main_count = sum(copies for _dbf_id, copies in cards)
    unique_count = len(cards)
    sideboard_count = sum(copies for _dbf_id, copies, _owner in sideboards)
    if main_count != deck.expected_cards:
        raise AssertionError(
            f"{deck.slug}: expected {deck.expected_cards} main-deck cards, got {main_count}"
        )
    if unique_count != deck.expected_cards or any(copies != 1 for _dbf_id, copies in cards):
        raise AssertionError(
            f"{deck.slug}: Reno fixture must contain {deck.expected_cards} unique singletons"
        )
    return {
        "main_cards": main_count,
        "unique_cards": unique_count,
        "sideboard_cards": sideboard_count,
    }


async def render_deck(
    deck: RegressionDeck,
    output_dir: Path,
    style: str,
    resolution: str = "standard",
) -> dict[str, object]:
    counts = validate_deck(deck)
    timings: dict[str, object] = {}
    image, dust, deck_class, mode, _card_ids = await create_picture(
        deck.code,
        deck_name=deck.title,
        timings=timings,
        image_style=style,
        image_resolution=resolution,
    )
    if image is None:
        raise RuntimeError(f"{deck.slug}: renderer returned no image")

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{deck.slug}.jpg"
    if image.mode != "RGB":
        image = image.convert("RGB")
    write_rendered_jpeg(image, output_path, quality=90)
    return {
        "deck": deck.slug,
        "title": deck.title,
        "path": str(output_path.resolve()),
        "size": list(image.size),
        "image_resolution": resolution,
        "dust": dust,
        "class": deck_class,
        "mode": mode,
        **counts,
        "timings_ms": timings,
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "artifacts" / "reno-regression",
    )
    parser.add_argument(
        "--style",
        choices=("classic", "parchment"),
        default="parchment",
    )
    parser.add_argument("--resolution", choices=("standard", "high"), default="standard")
    args = parser.parse_args()

    results = []
    for deck in RENO_DECKS:
        result = await render_deck(deck, args.output_dir, args.style, args.resolution)
        results.append(result)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    manifest_path = args.output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Reno regression passed: {manifest_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
