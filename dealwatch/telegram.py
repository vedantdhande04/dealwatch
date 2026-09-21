"""Telegram bot for dealwatch — watch products and get pinged in chat.

Long polls the bot api for commands and runs the usual price check on a loop,
so a target price can be pushed straight to your phone.
"""

import json
import logging
import os
import time
from pathlib import Path

import requests

from dealwatch import cli, db

logger = logging.getLogger("dealwatch.telegram")

API_URL = "https://api.telegram.org/bot{token}/{method}"
POLL_SECONDS = 25
MAX_MESSAGE = 4000

HELP_TEXT = (
    "dealwatch bot\n"
    "\n"
    "/start — show this help\n"
    "/add <url> [target] — start watching a product\n"
)


class TelegramError(Exception):
    """The bot api answered with ok=false, or not with json at all."""


def chunk_text(text: str, size: int = MAX_MESSAGE) -> list[str]:
    """Split a reply on line breaks so telegram does not reject it."""
    chunks: list[str] = []
    current = ""
    for line in text.splitlines() or [""]:
        if current and len(current) + len(line) + 1 > size:
            chunks.append(current)
            current = ""
        current = f"{current}\n{line}" if current else line
    if current or not chunks:
        chunks.append(current)
    return chunks


class TelegramClient:
    """Thin wrapper over the handful of bot api methods we use."""

    def __init__(self, token: str, timeout: int = 20, session=None):
        if not token:
            raise TelegramError("TELEGRAM_BOT_TOKEN is empty")
        self.token = token
        self.timeout = timeout
        self.session = session or requests.Session()

    def call(self, method: str, **params):
        url = API_URL.format(token=self.token, method=method)
        # long polling needs the http timeout to outlast the poll timeout
        http_timeout = self.timeout + int(params.get("timeout") or 0)
        response = self.session.post(url, json=params, timeout=http_timeout)
        try:
            payload = response.json()
        except ValueError as exc:
            raise TelegramError(
                f"{method}: telegram sent back {response.status_code}, not json"
            ) from exc
        if not payload.get("ok"):
            raise TelegramError(
                f"{method}: {payload.get('description') or 'request failed'}"
            )
        return payload.get("result")

    def send_message(self, chat_id, text: str) -> None:
        """Send one message, splitting it if it is long."""
        for part in chunk_text(text):
            self.call(
                "sendMessage",
                chat_id=chat_id,
                text=part,
                disable_web_page_preview=True,
            )

    def get_updates(self, offset=None, poll_seconds: int = POLL_SECONDS) -> list:
        """Ask for new messages, waiting up to poll_seconds for one."""
        params: dict = {"timeout": poll_seconds}
        if offset is not None:
            params["offset"] = offset
        return self.call("getUpdates", **params) or []


class DealWatchBot:
    """Commands plus the check loop behind them."""

    def __init__(
        self,
        token: str,
        config_path: Path | str = cli.DEFAULT_CONFIG,
        db_path: Path | str | None = None,
        chat_id=None,
        client=None,
        every: int = 30,
    ):
        self.config_path = Path(config_path)
        self.db_path = db_path or db.DEFAULT_DB
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID") or None
        self.client = client or TelegramClient(token)
        self.every = max(1, every)
        self._offset = None
        self._last_check = 0.0

    # config helpers ---------------------------------------------------------

    def load_config(self) -> dict:
        """Read the watched products, same rules as the cli."""
        if self.config_path.exists():
            return cli.read_config(self.config_path)
        return {"products": [], "timeout": None}

    def save_config(self, config: dict) -> None:
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(
                {"timeout": config.get("timeout"), "products": config["products"]},
                f, indent=2,
            )
            f.write("\n")

    def load_products(self) -> list[dict]:
        return self.load_config()["products"]

    # commands ---------------------------------------------------------------

    def cmd_start(self) -> str:
        return HELP_TEXT

    def cmd_add(self, args: list[str]) -> str:
        """Watch a new url, optionally with a target price."""
        if not args:
            return "usage: /add <url> [target]"
        url = args[0].strip()
        if not url.startswith("http"):
            return f"that doesn't look like a url: {url}"
        target = None
        if len(args) > 1:
            try:
                target = float(args[1].replace("₹", "").replace(",", ""))
            except ValueError:
                return f"target should be a number, got {args[1]!r}"
        config = self.load_config()
        products = config["products"]
        if any(str(p.get("url", "")).strip() == url for p in products):
            return "already watching that one"
        entry: dict = {"name": url, "url": url}
        if target is not None:
            entry["target"] = target
        products.append(entry)
        self.save_config(config)
        note = f", alert below ₹{target:,.0f}" if target is not None else ""
        return f"added #{len(products)}{note}\n{url}"

    def handle(self, text: str, chat_id=None) -> str:
        """Turn one incoming message into a reply."""
        parts = (text or "").strip().split()
        if not parts:
            return HELP_TEXT
        command = parts[0].split("@")[0].lower()  # /add@dealwatch_bot
        args = parts[1:]
        if command in ("/start", "/help"):
            return self.cmd_start()
        if command == "/add":
            return self.cmd_add(args)
        return f"don't know {command}\n\n{HELP_TEXT}"

    # loop -------------------------------------------------------------------

    def poll_once(self) -> int:
        """Answer whatever commands are waiting. Returns how many we got."""
        updates = self.client.get_updates(offset=self._offset)
        for update in updates:
            self._offset = update.get("update_id", 0) + 1
            message = update.get("message") or update.get("edited_message") or {}
            text = message.get("text")
            chat_id = (message.get("chat") or {}).get("id")
            if not text or chat_id is None:
                continue
            if self.chat_id is None:
                self.chat_id = chat_id
            try:
                reply = self.handle(text, chat_id=chat_id)
            except Exception as exc:  # noqa: BLE001 — keep the bot alive
                logger.exception("command failed: %s", text)
                reply = f"that broke: {exc}"
            self.client.send_message(chat_id, reply)
        return len(updates)

    def check_round(self) -> None:
        """Run one round of the normal price check."""
        args = cli.build_parser().parse_args([])
        args.db = self.db_path
        try:
            cli.run_check(args)
        except FileNotFoundError as exc:
            logger.warning("nothing to check: %s", exc)

    def run(self, once: bool = False) -> int:
        logger.info("bot up, checking every %d min", self.every)
        while True:
            self.poll_once()
            now = time.time()
            if not self._last_check or now - self._last_check >= self.every * 60:
                self.check_round()
                self._last_check = time.time()
            if once:
                return 0


def run_bot(
    config_path: Path | str = cli.DEFAULT_CONFIG,
    db_path: Path | str | None = None,
    every: int = 30,
    once: bool = False,
    token: str | None = None,
    client=None,
) -> int:
    """Entry point used by `dealwatch bot`."""
    token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        print("set TELEGRAM_BOT_TOKEN first (see .env.example)")
        return 1
    bot = DealWatchBot(
        token, config_path=config_path, db_path=db_path,
        every=every, client=client,
    )
    return bot.run(once=once)
