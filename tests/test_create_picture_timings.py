"""Contract tests for optional create_picture stage timings."""

import unittest
from unittest.mock import AsyncMock, patch

import image_creator


class CreatePictureTimingTests(unittest.IsolatedAsyncioTestCase):
    async def test_timings_do_not_change_result_contract(self):
        response = {
            "cards": [
                {
                    "id": 123,
                    "slug": "123-test-card",
                    "manaCost": 2,
                }
            ],
            "class": {"id": 8, "name": "Маг", "slug": "mage"},
            "format": "standard",
        }
        expected_image = object()
        timings = {}

        with (
            patch.object(image_creator, "retrieve_deck", AsyncMock(return_value=(response, 8, []))),
            patch.object(image_creator, "download_cards", AsyncMock()),
            patch.object(image_creator, "count_cards", AsyncMock(return_value=({"123-test-card": 1}, {"123-test-card": 2}))),
            patch.object(image_creator, "get_cost_of_deck", AsyncMock(return_value=40)),
            patch.object(image_creator, "place_cards", AsyncMock(return_value=expected_image)),
        ):
            result = await image_creator.create_picture("test-code", timings=timings)

        self.assertEqual(result, (expected_image, 40, "Маг", "Стандарт", [123]))
        for key in (
            "deck_resolve_ms",
            "art_prepare_ms",
            "card_index_ms",
            "dust_cost_ms",
            "image_compose_ms",
            "generator_total_ms",
        ):
            self.assertGreaterEqual(timings[key], 0)
        self.assertEqual(timings["generator_result"], "ok")
        self.assertEqual(timings["renderer_backend"], "pillow")

    async def test_empty_result_contract_is_preserved(self):
        timings = {}
        with patch.object(image_creator, "retrieve_deck", AsyncMock(return_value=(0, 0, []))):
            result = await image_creator.create_picture("bad-code", timings=timings)

        self.assertEqual(result, (None, 0, None, None, []))
        self.assertEqual(timings["generator_result"], "empty")
        self.assertIn("generator_total_ms", timings)

    async def test_tavern_brawl_mode_is_named_for_bot_and_api(self):
        response = {
            "cards": [],
            "class": {"id": 9, "name": "Чернокнижник", "slug": "warlock"},
            "format": "tavern-brawl",
        }
        with (
            patch.object(image_creator, "retrieve_deck", AsyncMock(return_value=(response, 9, []))),
            patch.object(image_creator, "hydrate_deck_cards", AsyncMock()),
            patch.object(image_creator, "download_cards", AsyncMock()),
            patch.object(image_creator, "count_cards", AsyncMock(return_value=({}, {}))),
            patch.object(image_creator, "get_cost_of_deck", AsyncMock(return_value=0)),
            patch.object(image_creator, "place_cards", AsyncMock(return_value=object())),
        ):
            result = await image_creator.create_picture("brawl-code")
        self.assertEqual(result[2:4], ("Чернокнижник", "Потасовка"))


if __name__ == "__main__":
    unittest.main()
