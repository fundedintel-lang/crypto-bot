from __future__ import annotations

from typing import Any


class BotError(Exception):
    """Base class for all bot errors."""


class ConfigError(BotError):
    """Configuration is missing or invalid."""


class MarketDataError(BotError):
    """Market data is missing, stale, or inconsistent."""


class OptionSelectionError(BotError):
    """No safe option contract could be selected."""


class RiskCheckError(BotError):
    """A pre-trade safety check rejected an order."""


class ExecutionError(BotError):
    """An order could not be safely created or reconciled."""


class StateError(BotError):
    """Persistent state could not be read or written."""


class DeltaAPIError(BotError):
    """An error returned by the Delta Exchange REST API."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        payload: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code or ""
        self.payload = payload

    @property
    def is_retryable(self) -> bool:
        if self.status_code in {408, 425, 429, 500, 502, 503, 504}:
            return True
        return self.code.lower() in {
            "internal_server_error",
            "service_unavailable",
            "too_many_requests",
            "rate_limit_exceeded",
            "temporarily_unavailable",
            "network_error",
            "timeout",
        }

    @property
    def is_not_found(self) -> bool:
        return self.status_code == 404 or self.code.lower() in {
            "not_found",
            "invalid_contract",
            "order_not_found",
        }
