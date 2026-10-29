"""User-visible replies: greetings, hints, error texts, aborted renders, photo uploads."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from PIL import Image
from rq.utils import import_attribute

from deckview.bot import application as main
from deckview.repositories import web as web_db
from deckview.services.deck_errors import (
    CARD_SOURCE_UNAVAILABLE,
    RENDER_FAILED,
    RENDER_TIMED_OUT,
    friendly_deck_error,
)
from deckview.workers import jobs
from deckview.workers import queue as deckview_queue
from image_creator.jpeg_output import write_telegram_photo_jpeg


def _text_message(text, *, chat_type="private", chat_id=42, first_name="Игрок"):
    return SimpleNamespace(
        text=text,
        message_id=7,
        message_thread_id=None,
        chat=SimpleNamespace(id=chat_id, type=chat_type, title=None, first_name=None),
        from_user=SimpleNamespace(id=42, username="player", first_name=first_name),
        answer=AsyncMock(),
        reply=AsyncMock(),
    )


class StartGreetingTests(unittest.IsolatedAsyncioTestCase):
    async def test_group_start_greets_the_sender_not_none(self):
        message = _text_message("/start", chat_type="supergroup", chat_id=-100, first_name="Игрок")

        with patch.object(main, "ensure_bot_user"):
            await main.process_start_command(message)

        text = message.answer.await_args.args[0]
        self.assertTrue(text.startswith("Привет, Игрок!"))
        self.assertNotIn("None", text)

    async def test_sender_name_is_html_escaped(self):
        message = _text_message("/start", first_name="<b>Rex</b>")

        with patch.object(main, "ensure_bot_user"):
            await main.process_start_command(message)

        self.assertTrue(
            message.answer.await_args.args[0].startswith("Привет, &lt;b&gt;Rex&lt;/b&gt;!")
        )


class NonDeckTextTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        path_patch = patch.object(web_db, "WEB_DATABASE_PATH", str(Path(tempdir.name) / "deckview.db"))
        path_patch.start()
        self.addCleanup(path_patch.stop)
        web_db._db_initialized = False
        self.addCleanup(setattr, web_db, "_db_initialized", False)
        web_db.init_db()

    async def test_private_text_without_code_gets_a_hint(self):
        message = _text_message("привет")

        with patch.object(main, "enqueue_deck_render") as enqueue:
            await main.find_code_in_message(message)

        enqueue.assert_not_called()
        message.reply.assert_not_awaited()
        hint = message.answer.await_args.args[0]
        self.assertIn("Не нашёл в сообщении код колоды", hint)
        self.assertIn("/card Название", hint)

    async def test_group_chatter_starting_with_aa_is_ignored(self):
        message = _text_message("AARGH, опять проиграл", chat_type="supergroup", chat_id=-100)

        with patch.object(main, "enqueue_deck_render") as enqueue:
            await main.find_code_in_message(message)

        enqueue.assert_not_called()
        message.reply.assert_not_awaited()
        message.answer.assert_not_awaited()

    async def test_quoted_deck_code_is_recognised(self):
        message = _text_message("Моя колода: «AAECAa0GBuWwBKi2BJ3UBA==»")
        message.reply.return_value = SimpleNamespace(chat=SimpleNamespace(id=42), message_id=8)

        with (
            patch.object(main, "enqueue_deck_render", return_value="job-1") as enqueue,
            patch.object(main, "_log_bot_event"),
        ):
            await main.find_code_in_message(message)

        self.assertEqual(enqueue.call_args.args[0]["deck_code"], "AAECAa0GBuWwBKi2BJ3UBA==")
        message.answer.assert_not_awaited()


class HealthCommandVisibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_help_hides_health_from_regular_users(self):
        message = _text_message("/help")

        with patch.object(main, "ADMIN_IDS", [1]):
            await main.cmd_help(message)

        text = message.answer.await_args.args[0]
        self.assertNotIn("/healt", text)

    async def test_help_shows_health_to_admins(self):
        message = _text_message("/help")

        with patch.object(main, "ADMIN_IDS", [42]):
            await main.cmd_help(message)

        self.assertIn("/health", message.answer.await_args.args[0])

    async def test_health_command_is_listed_only_in_admin_chats(self):
        set_commands = AsyncMock()

        with (
            patch.object(main, "ADMIN_IDS", [1001]),
            patch.object(main.bot, "set_my_commands", set_commands),
        ):
            await main._set_bot_commands()

        public_call, admin_call = set_commands.await_args_list
        public_names = [command.command for command in public_call.args[0]]
        admin_names = [command.command for command in admin_call.args[0]]
        self.assertNotIn("healt", public_names)
        self.assertNotIn("health", public_names)
        self.assertIn("health", admin_names)
        self.assertEqual(admin_call.kwargs["scope"].chat_id, 1001)


class DeckErrorTextTests(unittest.TestCase):
    def test_unexpected_error_hides_exception_text(self):
        message = friendly_deck_error(RuntimeError("boom <tag> & /srv/deckview/shared/.env"))

        self.assertEqual(message, RENDER_FAILED)

    def test_token_problem_does_not_mention_configuration(self):
        message = friendly_deck_error(RuntimeError("BATTLE_NET_TOKEN expired: 401"))

        self.assertEqual(message, CARD_SOURCE_UNAVAILABLE)
        self.assertNotIn("BATTLE_NET_TOKEN", message)

    def test_old_patch_runes_keep_their_specific_advice(self):
        message = friendly_deck_error(ValueError("Invalid rune costs"))

        self.assertIn("недействительные руны", message)


class AbortedRenderTests(unittest.TestCase):
    def test_aborted_job_replaces_the_status_message(self):
        bot = SimpleNamespace(
            edit_message_text=AsyncMock(),
            send_message=AsyncMock(),
            session=SimpleNamespace(close=AsyncMock()),
        )
        job = SimpleNamespace(
            id="job-1",
            args=[{"chat_id": 42, "status_chat_id": 42, "status_message_id": 8}],
        )

        with patch.object(jobs, "_build_bot", return_value=bot):
            jobs.render_deck_message_failed(job, None, TimeoutError, TimeoutError(), None)

        bot.edit_message_text.assert_awaited_once_with(
            RENDER_TIMED_OUT,
            chat_id=42,
            message_id=8,
        )
        bot.session.close.assert_awaited_once()

    def test_deck_render_jobs_register_the_abort_hook(self):
        queue = SimpleNamespace(enqueue=Mock(return_value=SimpleNamespace(id="job-1")))

        with patch.object(deckview_queue, "_queue", return_value=queue):
            deckview_queue.enqueue_deck_render({"deck_code": "AA-test"})

        kwargs = queue.enqueue.call_args.kwargs
        self.assertIs(import_attribute(kwargs["on_failure"].name), jobs.render_deck_message_failed)
        self.assertIs(import_attribute(kwargs["on_stopped"].name), jobs.render_deck_message_failed)


class TelegramPhotoUploadTests(unittest.TestCase):
    def test_upload_copy_is_smaller_and_keeps_the_source(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "deck.jpg"
            upload = Path(directory) / "deck_tg.jpg"
            Image.effect_noise((640, 480), 90).convert("RGB").save(
                source, "JPEG", quality=92, subsampling=0
            )
            source_bytes = source.read_bytes()

            written = write_telegram_photo_jpeg(source, upload)

            self.assertTrue(written)
            self.assertLess(upload.stat().st_size, len(source_bytes))
            self.assertEqual(source.read_bytes(), source_bytes)
            with Image.open(upload) as copy:
                self.assertEqual(copy.size, (640, 480))

    def test_no_copy_when_it_would_not_be_smaller(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "deck.jpg"
            upload = Path(directory) / "deck_tg.jpg"
            Image.new("RGB", (8, 8), "navy").save(source, "JPEG", quality=10)

            written = write_telegram_photo_jpeg(source, upload)

            self.assertFalse(written)
            self.assertFalse(upload.exists())


if __name__ == "__main__":
    unittest.main()
