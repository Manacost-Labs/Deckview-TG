"""High resolution must survive rendering, caching, queuing and delivery."""

import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image, JpegImagePlugin

from deckview.handlers.high_resolution import create_high_resolution_router
from deckview.infrastructure.render_cache import (
    build_render_cache_key,
    lookup_render_cache,
    store_render_cache,
)
from deckview.services.high_resolution_service import HighResolutionService
from deckview.web import application as web_app
from deckview.workers import jobs
from image_creator.cards_placer import _build_rust_payload
from image_creator.jpeg_output import write_rendered_jpeg
from scripts.render_regression_decks import RENO_DECKS


class HighResolutionTests(unittest.TestCase):
    def test_high_output_uses_native_canvas_with_bounded_size(self):
        payload = _build_rust_payload(
            {"card": 1},
            {"card": 2},
            100,
            {"cards": [], "class": {"slug": "mage"}},
            image_resolution="high",
        )
        self.assertEqual(payload["output"]["max_output_side"], 3840)
        self.assertEqual(payload["output"]["jpeg_quality"], 95)

    def test_resolution_has_its_own_cache_key_and_default_stays_stable(self):
        standard = build_render_cache_key("code", None, "parchment")
        explicit = build_render_cache_key(
            "code",
            None,
            "parchment",
            image_resolution="standard",
        )
        high = build_render_cache_key(
            "code",
            None,
            "parchment",
            image_resolution="high",
        )
        self.assertEqual(standard, explicit)
        self.assertNotEqual(standard, high)

    def test_api_rejects_unknown_resolution_before_work(self):
        with (
            patch.object(web_app, "_require_deckview_api_auth", return_value=None),
            patch.object(web_app, "lookup_render_cache") as lookup,
        ):
            response = web_app.app.test_client().post(
                "/deckview-api/v1/render",
                json={"deck_code": "code", "image_resolution": "unbounded"},
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["error_code"], "INVALID_IMAGE_RESOLUTION")
        lookup.assert_not_called()

    def test_async_api_queues_resolution_separately(self):
        with (
            patch.object(web_app, "_require_deckview_api_auth", return_value=None),
            patch.object(web_app, "lookup_render_cache", return_value=None),
            patch.object(web_app, "find_cached") as legacy,
            patch.object(
                web_app, "enqueue_api_render", return_value="hq-job"
            ) as enqueue,
        ):
            response = web_app.app.test_client().post(
                "/deckview-api/v1/render",
                json={"deck_code": "code", "image_resolution": "high", "async": True},
            )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json["image_resolution"], "high")
        self.assertEqual(enqueue.call_args.args[0]["image_resolution"], "high")
        legacy.assert_not_called()

    def test_high_jpeg_preserves_color_detail_in_pillow(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "high.jpg"
            image = Image.new("RGB", (40, 40), "red")
            image.info["deckview_image_resolution"] = "high"
            write_rendered_jpeg(image, path, quality=90)
            with Image.open(path) as saved:
                self.assertEqual(JpegImagePlugin.get_sampling(saved), 0)

    def test_cache_round_trip_keeps_standard_and_high_artifacts_separate(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.dict(
                os.environ,
                {
                    "WEB_DATABASE_PATH": str(root / "cache.db"),
                    "DECKVIEW_RENDER_CACHE_ROOT": str(root / "cache"),
                    "DECKVIEW_RENDER_CACHE_WRITE": "1",
                    "DECKVIEW_RENDER_CACHE_READ": "1",
                },
            ):
                entries = []
                for resolution, size in (("standard", 20), ("high", 40)):
                    path = root / f"{resolution}.jpg"
                    Image.new("RGB", (size, size), "red").save(path)
                    entries.append(
                        store_render_cache(
                            deck_code="code",
                            deck_name=None,
                            source_path=path,
                            cost=100,
                            deck_class="Маг",
                            deck_mode="Стандарт",
                            card_dbf_ids=[1],
                            image_resolution=resolution,
                        )
                    )
                self.assertNotEqual(entries[0]["cache_key"], entries[1]["cache_key"])
                for resolution, size in (("standard", 20), ("high", 40)):
                    hit = lookup_render_cache("code", None, image_resolution=resolution)
                    self.assertEqual(hit["image_resolution"], resolution)
                    with Image.open(hit["artifact_path"]) as image:
                        self.assertEqual(image.size, (size, size))


class HighResolutionAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_api_worker_passes_high_resolution_to_renderer_and_cache(self):
        with (
            patch.object(jobs, "init_web_db"),
            patch.object(jobs, "lookup_render_cache", return_value=None) as lookup,
            patch.object(jobs, "acquire_render_lock", return_value=None),
            patch.object(
                jobs,
                "create_picture",
                new=AsyncMock(return_value=(None, 0, None, None, [])),
            ) as render,
            patch.object(jobs, "emit_render_timing"),
        ):
            await jobs._render_api_deck_job(
                {"deck_code": "code", "image_resolution": "high"}
            )
        self.assertEqual(lookup.call_args.kwargs["image_resolution"], "high")
        self.assertEqual(render.await_args.kwargs["image_resolution"], "high")

    async def test_hq_command_queues_current_design_without_a_default_title(self):
        service = SimpleNamespace(submit=AsyncMock(return_value="job"))
        theme = {"style": "parchment", "font": "belwe", "personalization_revision": 7}
        router = create_high_resolution_router(
            service=service, image_theme_provider=lambda *_: theme
        )
        message = SimpleNamespace(
            chat=SimpleNamespace(id=1, type="private"),
            from_user=SimpleNamespace(id=42),
            message_thread_id=None,
            message_id=3,
            reply=AsyncMock(return_value=SimpleNamespace(message_id=4)),
        )
        await router.message.handlers[0].callback(message, SimpleNamespace(args="code"))
        payload = service.submit.await_args.args[0]
        self.assertIsNone(payload["deck_name"])
        self.assertEqual(payload["image_style"], "parchment")
        self.assertEqual(payload["image_font"], "belwe")
        self.assertEqual(payload["theme_revision"], 7)

    async def test_service_rejects_bad_codes_and_queues_valid_hq(self):
        service = HighResolutionService()
        with patch(
            "deckview.services.high_resolution_service.enqueue_deck_render",
            return_value="job",
        ) as enqueue:
            with self.assertRaises(ValueError):
                await service.submit({"deck_code": "broken"})
            enqueue.assert_not_called()
            await service.submit({"deck_code": RENO_DECKS[0].code})
        self.assertEqual(enqueue.call_args.args[0]["image_resolution"], "high")

    async def test_high_worker_delivers_document_and_never_uses_photo_cache(self):
        bot = SimpleNamespace(
            send_photo=AsyncMock(),
            send_document=AsyncMock(),
            send_message=AsyncMock(),
            session=SimpleNamespace(close=AsyncMock()),
        )
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            fake_image = Image.new("RGB", (40, 40))
            mocks = {}
            for name, kwargs in {
                "_build_bot": {"return_value": bot},
                "get_current_job": {"return_value": None},
                "init_ratings_db": {},
                "init_web_db": {},
                "lookup_render_cache": {"return_value": None},
                "acquire_render_lock": {"return_value": None},
                "create_picture": {
                    "new": AsyncMock(
                        return_value=(fake_image, 100, "Маг", "Стандарт", [1])
                    )
                },
                "_recognize_archetype": {"new": AsyncMock(return_value={})},
                "add_generated_with_cards": {"return_value": 1},
                "add_bot_event": {},
                "emit_render_timing": {},
                "store_render_cache": {"return_value": None},
                "get_telegram_photo_file_id": {},
                "store_telegram_photo_file_id": {},
            }.items():
                mocks[name] = stack.enter_context(patch.object(jobs, name, **kwargs))
            stack.enter_context(patch.object(jobs, "_TMP_DIR", folder))
            result = await jobs._render_deck_message_job(
                {
                    "deck_code": "code",
                    "chat_id": 1,
                    "image_resolution": "high",
                    "reply_to_message_id": 9,
                }
            )
        self.assertTrue(result["ok"])
        bot.send_document.assert_awaited_once()
        bot.send_photo.assert_not_awaited()
        mocks["get_telegram_photo_file_id"].assert_not_called()
        mocks["store_telegram_photo_file_id"].assert_not_called()
        self.assertEqual(
            mocks["create_picture"].await_args.kwargs["image_resolution"], "high"
        )
        self.assertEqual(
            mocks["store_render_cache"].call_args.kwargs["image_resolution"], "high"
        )
