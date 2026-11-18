import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from deckview.bot import publishing


class PublishDeckImageStyleTest(unittest.TestCase):
    def test_site_and_channel_images_use_parchment_theme(self):
        create_picture = AsyncMock(return_value=(None, 0, None, None, []))

        with patch.object(publishing, "create_picture", create_picture):
            result = asyncio.run(
                publishing.publish_deck(
                    {"deck_code": "AAECAQcAAAAA", "deck_name": "Test Warrior"},
                    to_telegram=False,
                    to_wordpress=True,
                )
            )

        self.assertEqual(result["error"], "Failed to generate image")
        create_picture.assert_awaited_once()
        self.assertEqual(create_picture.await_args.kwargs.get("image_style"), "parchment")


if __name__ == "__main__":
    unittest.main()
