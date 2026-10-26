from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from logging.handlers import RotatingFileHandler

from .config import BotConfig

_CONFIGURED = False
_SECRET_PATTERN = re.compile(
    r"(api[-_ ]?(?:key|secret)|token|signature|password)\s*[:=]\s*([^\s,}\"']+)",
    re.IGNORECASE,
)


class RedactingFilter(logging.Filter):
    def __init__(self, secrets: Iterable[str]) -> None:
        super().__init__()
        self.secrets = [value for value in secrets if value and len(value) >= 4]

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for secret in self.secrets:
            message = message.replace(secret, "***")
        message = _SECRET_PATTERN.sub(r"\1=***", message)
        record.msg = message
        record.args = ()
        return True


def configure_logging(config: BotConfig, *, level: str = "INFO") -> logging.Logger:
    global _CONFIGURED
    config.ensure_directories()
    logger = logging.getLogger("btc_options_bot")
    if _CONFIGURED:
        return logger
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False
    formatter = logging.Formatter(
        "%(asctime)s.%(msecs)03dZ %(levelname)-8s %(name)s:%(lineno)d %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    formatter.converter = __import__("time").gmtime

    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    file_handler = RotatingFileHandler(
        config.log_file,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    redactor = RedactingFilter(
        [config.api_key, config.api_secret, config.web_token, config.telegram_bot_token]
    )
    stream.addFilter(redactor)
    file_handler.addFilter(redactor)
    logger.addHandler(stream)
    logger.addHandler(file_handler)
    _CONFIGURED = True
    return logger
