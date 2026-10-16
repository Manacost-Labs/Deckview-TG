import json
import unittest
from pathlib import Path
from unittest.mock import patch

from framework import hearthstonejson_api as hsjson
from image_creator import deck_retriever


BRAWL_CODE = (
    "AAECAfCdCAKK9geQ9gcO1+kH/O4HxfAH6PEHgfYHjPYH6fkH4foH4/oH5PoH"
    "3PsH0f0HgIMIp5IIAAA="
)
FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "multiclass_brawl.json").read_text()
)
CARDS = {card["dbfId"]: card for card in FIXTURE["cards"]}


def loaded_card(dbf_id):
    raw = CARDS.get(dbf_id)
    return hsjson._card_to_deckview(raw, dbf_id) if raw else None


def hydrate(cards):
    for card in cards:
        metadata = loaded_card(card["dbfId"])
        if metadata:
            card.update(metadata)
    return cards


class MulticlassDeckTests(unittest.IsolatedAsyncioTestCase):
    def test_hsjson_preserves_hero_classes(self):
        hero = loaded_card(134896)
        self.assertEqual(hero["deckviewClasses"], ["PRIEST", "WARLOCK"])

    def test_hsjson_preserves_single_card_class(self):
        card = loaded_card(129802)
        self.assertEqual(card["deckviewPlayerClass"], "WARLOCK")

    def test_all_brawl_heroes_keep_their_own_class(self):
        expected = {
            134895: "PRIEST",
            134893: "WARRIOR",
            134896: "WARLOCK",
            134901: "DEMONHUNTER",
            134885: "MAGE",
            134902: "DEATHKNIGHT",
            134899: "DRUID",
            134900: "HUNTER",
            134898: "SHAMAN",
            134894: "PALADIN",
            134897: "ROGUE",
        }
        with patch.object(
            deck_retriever, "get_loaded_card_by_dbfid", side_effect=loaded_card
        ):
            for hero_id, class_key in expected.items():
                with self.subTest(hero=hero_id):
                    # Class art must work even with an entirely neutral deck.
                    self.assertEqual(
                        deck_retriever._infer_class([hero_id], []), class_key
                    )

    async def test_reported_deck_resolves_without_blizzard_deck_api(self):
        with (
            patch.object(deck_retriever, "hydrate_deck_cards_sync", side_effect=hydrate),
            patch.object(
                deck_retriever, "get_loaded_card_by_dbfid", side_effect=loaded_card
            ),
            patch.object(deck_retriever, "_DECK_CACHE_TTL_SECONDS", 0),
            patch.object(
                deck_retriever,
                "get_blizzard_api",
                side_effect=AssertionError("Multiclass decks must resolve locally"),
            ),
        ):
            response, class_id, sideboard = await deck_retriever.retrieve_deck(BRAWL_CODE)

        self.assertEqual(class_id, 9)
        self.assertEqual(response["class"]["slug"], "warlock")
        self.assertEqual(response["format"], "tavern-brawl")
        self.assertEqual([item["slug"] for item in response["classes"]], ["warlock", "priest"])
        self.assertEqual(len(response["cards"]), 30)
        self.assertEqual(len({card["id"] for card in response["cards"]}), 16)
        self.assertEqual(sideboard, [])
        self.assertTrue(all(card.get("cardId") for card in response["cards"]))

    def test_cold_catalog_recovers_cards_and_hero_in_one_load(self):
        ready = False

        def load_catalog(**kwargs):
            nonlocal ready
            ready = True
            return True

        def lookup(dbf_id):
            return loaded_card(dbf_id) if ready else None

        with (
            patch.object(deck_retriever, "hydrate_deck_cards_sync"),
            patch.object(deck_retriever, "get_loaded_card_by_dbfid", side_effect=lookup),
            patch.object(deck_retriever, "hsjson_ensure_loaded", side_effect=load_catalog) as load,
        ):
            response, class_id, _ = deck_retriever._build_local_deck(BRAWL_CODE)

        load.assert_called_once_with(timeout_seconds=8)
        self.assertEqual(class_id, 9)
        self.assertEqual(len(response["cards"]), 30)
        self.assertTrue(all(card.get("cardId") for card in response["cards"]))

    def test_three_class_brawl_keeps_all_classes_even_with_neutral_cards(self):
        with (
            patch.object(deck_retriever, "hydrate_deck_cards_sync", side_effect=hydrate),
            patch.object(deck_retriever, "get_loaded_card_by_dbfid", side_effect=loaded_card),
            patch.object(
                deck_retriever,
                "_decode_deckstring",
                return_value=([(128892, 2)], [134901], 2, []),
            ),
        ):
            response, class_id, _ = deck_retriever._build_local_deck("three-class-code")
        self.assertEqual(class_id, 14)
        self.assertEqual(response["format"], "tavern-brawl")
        self.assertEqual(
            [item["slug"] for item in response["classes"]],
            ["demonhunter", "deathknight", "warrior"],
        )

    def test_catalog_failure_preserves_blizzard_fallback(self):
        with (
            patch.object(deck_retriever, "hydrate_deck_cards_sync"),
            patch.object(deck_retriever, "get_loaded_card_by_dbfid", return_value=None),
            patch.object(deck_retriever, "hsjson_ensure_loaded", return_value=False),
        ):
            self.assertIsNone(deck_retriever._build_local_deck(BRAWL_CODE))

    def test_unknown_single_class_hero_uses_catalog_before_card_majority(self):
        with patch.object(
            deck_retriever,
            "get_loaded_card_by_dbfid",
            return_value={"cardId": "HERO_08_SKIN", "deckviewPlayerClass": "MAGE"},
        ):
            self.assertEqual(
                deck_retriever._infer_class(
                    [987654], [{"deckviewPlayerClass": "PRIEST"}] * 10
                ),
                "MAGE",
            )

    def test_complete_card_still_gets_missing_class_metadata(self):
        card = {
            "id": 129802,
            "dbfId": 129802,
            "slug": "129802-blasphemy",
            "cardId": "BE_086",
            "name": "Кощунство",
            "manaCost": 99,
        }
        with patch.object(
            deck_retriever, "get_loaded_card_by_dbfid", side_effect=loaded_card
        ):
            deck_retriever._fill_missing_from_preloaded_hsjson([card])
        self.assertEqual(card["deckviewPlayerClass"], "WARLOCK")
        self.assertEqual(card["manaCost"], 99)


if __name__ == "__main__":
    unittest.main()
