from __future__ import annotations

import logging
import queue
import threading
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

from .config import BotConfig
from .utils import json_dumps

logger = logging.getLogger("btc_options_bot.notifier")


class Notifier:
    """Non-blocking console/state/optional-Telegram notification dispatcher."""

    def __init__(
        self,
        config: BotConfig,
        *,
        event_sink: Callable[[str, str, str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.config = config
        self.event_sink = event_sink
        self._queue: queue.Queue[tuple[str, str, str, dict[str, Any]] | None] = queue.Queue()
        self._thread = threading.Thread(target=self._worker, name="notifier", daemon=True)
        self._thread.start()

    def notify(
        self,
        level: str,
        title: str,
        message: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        payload = dict(context or {})
        log = getattr(logger, level.lower(), logger.info)
        log("%s — %s | %s", title, message, json_dumps(payload)[:1000])
        if self.event_sink is not None:
            try:
                self.event_sink(level.upper(), title, message, payload)
            except Exception:
                logger.exception("state event sink failed")
        self._queue.put_nowait((level.upper(), title, message, payload))

    def _worker(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            level, title, message, context = item
            try:
                self._send_telegram(level, title, message, context)
            except Exception:
                logger.exception("telegram notification failed")

    def _send_telegram(
        self,
        level: str,
        title: str,
        message: str,
        context: dict[str, Any],
    ) -> None:
        if not self.config.telegram_bot_token or not self.config.telegram_chat_id:
            return
        if level not in {"WARNING", "ERROR", "CRITICAL", "TRADE"}:
            return
        icon = {
            "WARNING": "⚠️",
            "ERROR": "❌",
            "CRITICAL": "🚨",
            "TRADE": "📈",
        }.get(level, "•")
        text = f"{icon} <b>{title}</b>\n{message}"
        if context:
            detail = json_dumps(context)
            text += f"\n<pre>{detail[:2500]}</pre>"
        url = (
            f"https://api.telegram.org/bot{self.config.telegram_bot_token}/sendMessage"
        )
        payload = urllib.parse.urlencode(
            {
                "chat_id": self.config.telegram_chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=7) as response:
            response.read()

    def close(self) -> None:
        self._queue.put_nowait(None)
