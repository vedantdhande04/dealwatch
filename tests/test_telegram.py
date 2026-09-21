"""Tests for the telegram bot commands — no network involved."""

import json
import tempfile
import unittest
from pathlib import Path

from dealwatch import db
from dealwatch.telegram import (
    DealWatchBot,
    TelegramClient,
    TelegramError,
    chunk_text,
)


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    """Records the calls the client would have made to telegram."""

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []

    def post(self, url, json=None, timeout=None):
        self.calls.append((url, json, timeout))
        if self.responses:
            return self.responses.pop(0)
        return FakeResponse({"ok": True, "result": []})


class FakeClient:
    def __init__(self, updates=None):
        self.sent = []
        self.offsets = []
        self.updates = list(updates or [])

    def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))

    def get_updates(self, offset=None, poll_seconds=None):
        self.offsets.append(offset)
        return self.updates.pop(0) if self.updates else []


class BotTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.config = self.tmp / "config.json"
        self.db_path = self.tmp / "test.db"
        self.client = FakeClient()
        self.bot = DealWatchBot(
            "token", config_path=self.config, db_path=self.db_path,
            chat_id=123, client=self.client,
        )

    def tearDown(self):
        self._tmp.cleanup()

    def write_config(self, products):
        self.config.write_text(
            json.dumps({"timeout": None, "products": products}), encoding="utf-8"
        )


class ChunkTextTests(unittest.TestCase):
    def test_short_text_is_one_chunk(self):
        self.assertEqual(chunk_text("hello"), ["hello"])

    def test_long_text_is_split(self):
        parts = chunk_text("a" * 10 + "\n" + "b" * 10, size=12)
        self.assertEqual(parts, ["a" * 10, "b" * 10])


class TelegramClientTests(unittest.TestCase):
    def test_send_message_posts_the_text(self):
        session = FakeSession()
        TelegramClient("tok", session=session).send_message(7, "hi")
        url, payload, _ = session.calls[0]
        self.assertEqual(url, "https://api.telegram.org/bottok/sendMessage")
        self.assertEqual(payload["chat_id"], 7)
        self.assertEqual(payload["text"], "hi")

    def test_error_payload_raises(self):
        session = FakeSession([FakeResponse({"ok": False, "description": "nope"})])
        with self.assertRaises(TelegramError):
            TelegramClient("tok", session=session).send_message(7, "hi")

    def test_timeout_waits_longer_than_the_poll(self):
        session = FakeSession([FakeResponse({"ok": True, "result": []})])
        TelegramClient("tok", timeout=5, session=session).get_updates(poll_seconds=30)
        self.assertEqual(session.calls[0][2], 35)

    def test_empty_token_is_rejected(self):
        with self.assertRaises(TelegramError):
            TelegramClient("")


class StartAndAddTests(BotTestCase):
    def test_start_shows_the_commands(self):
        reply = self.bot.handle("/start")
        self.assertIn("/add <url> [target]", reply)
        self.assertIn("/status", reply)

    def test_add_stores_the_product(self):
        reply = self.bot.handle("/add https://www.amazon.in/dp/B0X 19999")
        self.assertIn("added #1", reply)
        saved = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(saved["products"][0]["url"], "https://www.amazon.in/dp/B0X")
        self.assertEqual(saved["products"][0]["target"], 19999.0)

    def test_add_same_url_twice_is_refused(self):
        self.bot.handle("/add https://www.amazon.in/dp/B0X")
        self.assertEqual(self.bot.handle("/add https://www.amazon.in/dp/B0X"),
                         "already watching that one")

    def test_add_needs_a_url(self):
        self.assertEqual(self.bot.handle("/add"), "usage: /add <url> [target]")
        self.assertIn("doesn't look like a url", self.bot.handle("/add nope"))

    def test_add_rejects_a_silly_target(self):
        self.assertIn("should be a number", self.bot.handle("/add https://a.example/x cheap"))

    def test_unknown_command_falls_back_to_help(self):
        self.assertIn("don't know /nope", self.bot.handle("/nope"))


class StatusTests(BotTestCase):
    def test_status_after_add(self):
        self.write_config([
            {"name": "phone", "url": "https://a.example/p", "target": 12000},
        ])
        db.record_check("phone", "https://a.example/p", "Phone", 13499.0,
                        db_path=self.db_path)
        reply = self.bot.handle("/status")
        self.assertIn("#1 phone", reply)
        self.assertIn("₹13,499", reply)
        self.assertIn("target ₹12,000", reply)

    def test_status_without_a_price(self):
        self.write_config([{"name": "phone", "url": "https://a.example/p"}])
        self.assertIn("no price yet", self.bot.handle("/status"))

    def test_status_when_nothing_is_watched(self):
        self.assertIn("nothing watched yet", self.bot.handle("/status"))


class RemoveTests(BotTestCase):
    def test_remove_drops_the_right_product(self):
        self.write_config([
            {"name": "one", "url": "https://a.example/1"},
            {"name": "two", "url": "https://a.example/2"},
        ])
        reply = self.bot.handle("/remove 1")
        self.assertIn("removed #1 one", reply)
        saved = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual([p["name"] for p in saved["products"]], ["two"])

    def test_remove_accepts_a_hash(self):
        self.write_config([{"name": "one", "url": "https://a.example/1"}])
        self.assertIn("removed #1", self.bot.handle("/remove #1"))

    def test_remove_rejects_bad_ids(self):
        self.write_config([{"name": "one", "url": "https://a.example/1"}])
        self.assertEqual(self.bot.handle("/remove"), "usage: /remove <id>")
        self.assertIn("isn't an id", self.bot.handle("/remove abc"))
        self.assertIn("no product #9", self.bot.handle("/remove 9"))


class HistoryTests(BotTestCase):
    def setUp(self):
        super().setUp()
        self.write_config([{"name": "phone", "url": "https://a.example/p"}])
        for price in (25000.0, 23000.0):
            db.record_check("phone", "https://a.example/p", "Phone", price,
                            db_path=self.db_path)

    def test_history_by_id(self):
        reply = self.bot.handle("/history 1")
        self.assertIn("last 2 check(s)", reply)
        self.assertIn("₹25,000", reply)
        self.assertIn("₹23,000", reply)

    def test_history_by_name(self):
        self.assertIn("₹23,000", self.bot.handle("/history phone"))

    def test_history_usage_and_misses(self):
        self.assertEqual(self.bot.handle("/history"), "usage: /history <id or name>")
        self.assertIn("no product #8", self.bot.handle("/history 8"))
        self.assertIn("no price history", self.bot.handle("/history kindle"))


class AlertTests(BotTestCase):
    def test_send_alert_goes_to_the_chat(self):
        self.bot.send_alert("phone", 11999.0, 12000.0)
        chat_id, text = self.client.sent[0]
        self.assertEqual(chat_id, 123)
        self.assertIn("₹11,999", text)
        self.assertIn("₹12,000", text)

    def test_send_alert_without_a_chat_does_nothing(self):
        silent = DealWatchBot("token", config_path=self.config,
                              db_path=self.db_path, client=self.client)
        silent.chat_id = None
        silent.send_alert("phone", 1.0, 2.0)
        self.assertEqual(self.client.sent, [])

    def test_notify_failure_does_not_break_the_check(self):
        def boom(*_args):
            raise RuntimeError("telegram is down")

        import argparse
        from dealwatch import cli

        cli.fetch_price = lambda url, timeout=15: ("Phone", 10.0)
        args = argparse.Namespace(url="https://a.example/p", name="phone",
                                  db=self.db_path, target=99999, timeout=5,
                                  dry_run=False)
        self.assertEqual(cli.run_check(args, notify=boom), 0)


class PollTests(BotTestCase):
    def test_poll_answers_and_moves_the_offset(self):
        self.client.updates = [
            [{"update_id": 4, "message": {"text": "/status", "chat": {"id": 55}}}],
        ]
        self.assertEqual(self.bot.poll_once(), 1)
        self.assertEqual(self.client.offsets, [None])
        self.assertEqual(self.client.sent[0][0], 55)
        self.assertEqual(self.bot._offset, 5)

    def test_poll_skips_messages_without_text(self):
        self.client.updates = [[{"update_id": 9, "message": {"chat": {"id": 1}}}]]
        self.bot.poll_once()
        self.assertEqual(self.client.sent, [])

    def test_poll_keeps_going_when_a_command_blows_up(self):
        self.bot.handle = lambda text, chat_id=None: (_ for _ in ()).throw(ValueError("boom"))
        self.client.updates = [
            [{"update_id": 1, "message": {"text": "/status", "chat": {"id": 55}}}],
        ]
        self.bot.poll_once()
        self.assertIn("that broke", self.client.sent[0][1])


if __name__ == "__main__":
    unittest.main()
