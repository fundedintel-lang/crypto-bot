from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from decimal import Decimal
from pathlib import Path
from typing import Any

from .errors import ConfigError
from .utils import IST, as_bool, decimal_str, parse_hhmm, to_decimal

BASE_URLS = {
    ("india", "live"): "https://api.india.delta.exchange",
    ("india", "paper"): "https://api.india.delta.exchange",
    ("india", "testnet"): "https://cdn-ind.testnet.deltaex.org",
    ("global", "live"): "https://api.delta.exchange",
    ("global", "paper"): "https://api.delta.exchange",
    ("global", "testnet"): "https://testnet-api.delta.exchange",
}
VENUE_DEFAULTS = {
    "india": {
        "price_symbol": "BTCUSD",
        "candle_symbol": ".DEXBTUSD",
        "capital_asset": "USD",
    },
    "global": {
        "price_symbol": "BTCUSDT",
        "candle_symbol": ".DEXBTUSDT",
        "capital_asset": "USDT",
    },
}
LIVE_CONFIRMATION = "I_UNDERSTAND_REAL_MONEY"


@dataclass(frozen=True)
class BotConfig:
    mode: str
    venue: str
    base_url: str
    api_key: str = field(default="", repr=False)
    api_secret: str = field(default="", repr=False)
    live_confirmation: str = field(default="", repr=False)

    # Strategy: fixed to the requested rules, but validated from configuration.
    ema_period: int = 21
    candle_resolution: str = "1h"
    target_points: Decimal = Decimal("1500")
    initial_capital_pct: Decimal = Decimal("0.02")
    additional_capital_pct: Decimal = Decimal("0.02")
    entry_time: Any = None  # datetime.time in IST
    additional_time: Any = None
    exit_time: Any = None
    no_stop_loss: bool = True

    # Capital.
    capital_mode: str = "net_equity"
    fixed_capital: Decimal | None = None
    capital_asset: str = "USD"
    paper_capital: Decimal = Decimal("1000")
    min_wallet_equity: Decimal = Decimal("0")

    # Market symbols.
    price_symbol: str = "BTCUSD"
    candle_symbol: str = ".DEXBTUSD"
    underlying_asset_symbol: str = "BTC"

    # Option selection.
    option_strike_mode: str = "atm"
    option_strike_offset: int = 0
    option_expiry_mode: str = "next_after_exit"
    manual_option_symbol: str = ""
    min_expiry_buffer_minutes: int = 5
    max_expiry_days: int = 8

    # Execution and safety.
    poll_seconds: Decimal = Decimal("1")
    catchup_minutes: int = 10
    max_market_data_age_seconds: int = 60
    max_spread_pct: Decimal = Decimal("0.15")
    max_book_slippage_pct: Decimal = Decimal("0.10")
    min_option_turnover_usd: Decimal = Decimal("0")
    order_price_buffer_pct: Decimal = Decimal("0.01")
    exit_price_buffer_pct: Decimal = Decimal("0.02")
    fee_buffer_pct: Decimal = Decimal("0.01")
    order_fill_timeout_seconds: int = 180
    exit_reprice_seconds: int = 5
    http_timeout_seconds: Decimal = Decimal("15")
    http_retries: int = 4
    allow_existing_position: bool = False
    dedicated_account: bool = False

    # Local state and controls.
    data_dir: Path = Path("data")
    log_dir: Path = Path("logs")
    pause_file: Path = Path("data/PAUSE")
    kill_file: Path = Path("data/KILL")
    state_db: Path = Path("data/state.sqlite3")
    log_file: Path = Path("logs/bot.log")
    lock_file: Path = Path("data/bot.lock")
    paper_state_file: Path = Path("data/paper_state.json")

    # Optional web dashboard.
    web_enabled: bool = True
    web_host: str = "127.0.0.1"
    web_port: int = 8080
    web_token: str = field(default="", repr=False)

    # Optional Telegram alerts.
    telegram_bot_token: str = field(default="", repr=False)
    telegram_chat_id: str = ""

    def with_overrides(self, **overrides: Any) -> BotConfig:
        clean = {key: value for key, value in overrides.items() if value is not None}
        if "mode" in clean:
            mode = str(clean["mode"]).lower()
            clean["mode"] = mode
            clean["base_url"] = BASE_URLS[(self.venue, mode)]
        updated = replace(self, **clean)
        updated.validate()
        return updated

    @property
    def is_paper(self) -> bool:
        return self.mode == "paper"

    @property
    def is_live(self) -> bool:
        return self.mode == "live"

    @property
    def private_api_required(self) -> bool:
        return self.mode in {"testnet", "live"}

    @property
    def timezone(self):
        return IST

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def safe_dict(self) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for key, value in self.__dict__.items():
            if key in {
                "api_key",
                "api_secret",
                "web_token",
                "telegram_bot_token",
                "live_confirmation",
            }:
                values[key] = "***" if value else ""
            elif isinstance(value, Decimal):
                values[key] = decimal_str(value)
            elif isinstance(value, Path):
                values[key] = str(value)
            elif hasattr(value, "isoformat"):
                values[key] = value.isoformat()
            else:
                values[key] = value
        values["timezone"] = "Asia/Kolkata (UTC+05:30)"
        return values

    @classmethod
    def from_env(
        cls,
        *,
        env_file: str | Path = ".env",
        environ: Mapping[str, str] | None = None,
        require_credentials: bool = True,
    ) -> BotConfig:
        raw = _load_env_file(env_file)
        env = dict(os.environ if environ is None else environ)
        env.update({key: value for key, value in raw.items() if key not in env})

        def get(name: str, default: str = "") -> str:
            return str(env.get(name, default)).strip()

        def dec(name: str, default: str) -> Decimal:
            return to_decimal(get(name, default))

        def integer(name: str, default: str) -> int:
            return int(get(name, default))

        mode = get("BOT_MODE", "paper").lower()
        venue = get("VENUE", "india").lower()
        if mode not in {"paper", "testnet", "live"}:
            raise ConfigError("BOT_MODE must be paper, testnet, or live")
        if venue not in VENUE_DEFAULTS:
            raise ConfigError("VENUE must be india or global")

        confirmation = get("LIVE_TRADING_CONFIRMATION")
        api_key = _read_secret(env, "DELTA_API_KEY")
        api_secret = _read_secret(env, "DELTA_API_SECRET")
        if require_credentials and mode in {"testnet", "live"} and (not api_key or not api_secret):
            raise ConfigError(
                "DELTA_API_KEY and DELTA_API_SECRET are required for testnet/live mode. "
                "Use paper mode if you only want to test without credentials."
            )

        defaults = VENUE_DEFAULTS[venue]
        data_dir = Path(get("DATA_DIR", "data"))
        log_dir = Path(get("LOG_DIR", "logs"))
        pause_file = Path(get("PAUSE_FILE", str(data_dir / "PAUSE")))
        kill_file = Path(get("KILL_FILE", str(data_dir / "KILL")))

        config = cls(
            mode=mode,
            venue=venue,
            base_url=BASE_URLS[(venue, mode)],
            api_key=api_key,
            api_secret=api_secret,
            live_confirmation=confirmation,
            ema_period=integer("EMA_PERIOD", "21"),
            candle_resolution=get("CANDLE_RESOLUTION", "1h"),
            target_points=dec("TARGET_POINTS", "1500"),
            initial_capital_pct=dec("INITIAL_CAPITAL_PCT", "0.02"),
            additional_capital_pct=dec("ADDITIONAL_CAPITAL_PCT", "0.02"),
            entry_time=parse_hhmm(get("ENTRY_TIME_IST", "18:30")),
            additional_time=parse_hhmm(get("ADDITIONAL_TIME_IST", "01:00")),
            exit_time=parse_hhmm(get("EXIT_TIME_IST", "17:20")),
            no_stop_loss=as_bool(get("NO_STOP_LOSS", "true"), default=True),
            capital_mode=get("CAPITAL_MODE", "net_equity").lower(),
            fixed_capital=(to_decimal(get("FIXED_CAPITAL")) if get("FIXED_CAPITAL") else None),
            capital_asset=get("CAPITAL_ASSET", defaults["capital_asset"]).upper(),
            paper_capital=dec("PAPER_CAPITAL", "1000"),
            min_wallet_equity=dec("MIN_WALLET_EQUITY", "0"),
            price_symbol=get("PRICE_SYMBOL", defaults["price_symbol"]),
            candle_symbol=get("CANDLE_SYMBOL", defaults["candle_symbol"]),
            underlying_asset_symbol=get("UNDERLYING_ASSET_SYMBOL", "BTC").upper(),
            option_strike_mode=get("OPTION_STRIKE_MODE", "atm").lower(),
            option_strike_offset=integer("OPTION_STRIKE_OFFSET", "0"),
            option_expiry_mode=get("OPTION_EXPIRY_MODE", "next_after_exit").lower(),
            manual_option_symbol=get("MANUAL_OPTION_SYMBOL"),
            min_expiry_buffer_minutes=integer("MIN_EXPIRY_BUFFER_MINUTES", "5"),
            max_expiry_days=integer("MAX_EXPIRY_DAYS", "8"),
            poll_seconds=dec("POLL_SECONDS", "1"),
            catchup_minutes=integer("CATCHUP_MINUTES", "10"),
            max_market_data_age_seconds=integer("MAX_MARKET_DATA_AGE_SECONDS", "60"),
            max_spread_pct=dec("MAX_SPREAD_PCT", "0.15"),
            max_book_slippage_pct=dec("MAX_BOOK_SLIPPAGE_PCT", "0.10"),
            min_option_turnover_usd=dec("MIN_OPTION_TURNOVER_USD", "0"),
            order_price_buffer_pct=dec("ORDER_PRICE_BUFFER_PCT", "0.01"),
            exit_price_buffer_pct=dec("EXIT_PRICE_BUFFER_PCT", "0.02"),
            fee_buffer_pct=dec("FEE_BUFFER_PCT", "0.01"),
            order_fill_timeout_seconds=integer("ORDER_FILL_TIMEOUT_SECONDS", "180"),
            exit_reprice_seconds=integer("EXIT_REPRICE_SECONDS", "5"),
            http_timeout_seconds=dec("HTTP_TIMEOUT_SECONDS", "15"),
            http_retries=integer("HTTP_RETRIES", "4"),
            allow_existing_position=as_bool(get("ALLOW_EXISTING_POSITION", "false")),
            dedicated_account=as_bool(get("DEDICATED_ACCOUNT", "false")),
            data_dir=data_dir,
            log_dir=log_dir,
            pause_file=pause_file,
            kill_file=kill_file,
            state_db=Path(get("STATE_DB", str(data_dir / "state.sqlite3"))),
            log_file=Path(get("LOG_FILE", str(log_dir / "bot.log"))),
            lock_file=Path(get("LOCK_FILE", str(data_dir / "bot.lock"))),
            paper_state_file=Path(get("PAPER_STATE_FILE", str(data_dir / "paper_state.json"))),
            web_enabled=as_bool(get("WEB_ENABLED", "true"), default=True),
            web_host=get("WEB_HOST", "127.0.0.1"),
            web_port=integer("WEB_PORT", "8080"),
            web_token=get("WEB_TOKEN"),
            telegram_bot_token=get("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=get("TELEGRAM_CHAT_ID"),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if self.mode == "live" and self.live_confirmation != LIVE_CONFIRMATION:
            raise ConfigError(
                "Live mode is locked. Set LIVE_TRADING_CONFIRMATION="
                f"{LIVE_CONFIRMATION} only when you are ready to use real money."
            )
        if self.ema_period != 21:
            raise ConfigError("EMA_PERIOD must be 21 for the requested strategy")
        if self.candle_resolution != "1h":
            raise ConfigError("CANDLE_RESOLUTION must be 1h for the requested strategy")
        if self.target_points != Decimal("1500"):
            raise ConfigError("TARGET_POINTS must be 1500 for the requested strategy")
        if self.initial_capital_pct != Decimal("0.02"):
            raise ConfigError("INITIAL_CAPITAL_PCT must be 0.02 (2%) for the requested strategy")
        if self.additional_capital_pct != Decimal("0.02"):
            raise ConfigError("ADDITIONAL_CAPITAL_PCT must be 0.02 (2%) for the requested strategy")
        if self.entry_time.strftime("%H:%M") != "18:30":
            raise ConfigError("ENTRY_TIME_IST must be 18:30 for the requested strategy")
        if self.additional_time.strftime("%H:%M") != "01:00":
            raise ConfigError("ADDITIONAL_TIME_IST must be 01:00 for the requested strategy")
        if self.exit_time.strftime("%H:%M") != "17:20":
            raise ConfigError("EXIT_TIME_IST must be 17:20 for the requested strategy")
        if not self.no_stop_loss:
            raise ConfigError("This strategy has no stop-loss. NO_STOP_LOSS must remain true.")
        if self.capital_mode not in {"net_equity", "fixed", "available_balance"}:
            raise ConfigError("CAPITAL_MODE must be net_equity, fixed, or available_balance")
        if self.capital_mode == "fixed" and (self.fixed_capital is None or self.fixed_capital <= 0):
            raise ConfigError("FIXED_CAPITAL must be positive when CAPITAL_MODE=fixed")
        if self.option_strike_mode not in {"atm", "offset"}:
            raise ConfigError("OPTION_STRIKE_MODE must be atm or offset")
        if self.option_expiry_mode not in {"next_after_exit", "manual"}:
            raise ConfigError("OPTION_EXPIRY_MODE must be next_after_exit or manual")
        if self.option_expiry_mode == "manual" and not self.manual_option_symbol:
            raise ConfigError("MANUAL_OPTION_SYMBOL is required when OPTION_EXPIRY_MODE=manual")
        if self.manual_option_symbol and self.option_expiry_mode != "manual":
            raise ConfigError(
                "When MANUAL_OPTION_SYMBOL is set, OPTION_EXPIRY_MODE must be manual"
            )
        if self.poll_seconds <= 0 or self.poll_seconds > 60:
            raise ConfigError("POLL_SECONDS must be in (0, 60]")
        if self.catchup_minutes < 0 or self.catchup_minutes > 120:
            raise ConfigError("CATCHUP_MINUTES must be between 0 and 120")
        if self.max_spread_pct < 0 or self.max_spread_pct > 1:
            raise ConfigError("MAX_SPREAD_PCT must be between 0 and 1")
        if self.max_book_slippage_pct < 0 or self.max_book_slippage_pct > 1:
            raise ConfigError("MAX_BOOK_SLIPPAGE_PCT must be between 0 and 1")
        if self.http_retries < 0 or self.http_retries > 10:
            raise ConfigError("HTTP_RETRIES must be between 0 and 10")
        if not 1 <= self.web_port <= 65535:
            raise ConfigError("WEB_PORT must be between 1 and 65535")
        if self.paper_capital <= 0 and self.is_paper:
            raise ConfigError("PAPER_CAPITAL must be positive in paper mode")


def _load_env_file(path: str | Path) -> dict[str, str]:
    file_path = Path(path)
    if not file_path.exists():
        return {}
    result: dict[str, str] = {}
    for raw_line in file_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.lstrip("\ufeff").strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if value and value[0] in {'"', "'"} and value[-1:] == value[0]:
            value = value[1:-1]
        else:
            value = re.sub(r"\s+#.*$", "", value).strip()
        result[key] = value
    return result


def _read_secret(env: Mapping[str, str], name: str) -> str:
    file_key = f"{name}_FILE"
    file_value = env.get(file_key, "").strip()
    if file_value:
        return Path(file_value).read_text(encoding="utf-8").strip()
    return env.get(name, "").strip()
