from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from .config import BotConfig
from .utils import IST, UTC, iso_utc


@dataclass(frozen=True)
class SessionSchedule:
    cycle_id: str
    session_date: date
    entry_at: datetime
    add_at: datetime
    exit_at: datetime

    def to_dict(self) -> dict[str, str]:
        return {
            "cycle_id": self.cycle_id,
            "session_date": self.session_date.isoformat(),
            "entry_at": iso_utc(self.entry_at),
            "add_at": iso_utc(self.add_at),
            "exit_at": iso_utc(self.exit_at),
        }


def build_session(session_date: date, config: BotConfig) -> SessionSchedule:
    def at(day: date, value: time) -> datetime:
        return datetime.combine(day, value, tzinfo=IST).astimezone(UTC)

    return SessionSchedule(
        cycle_id=session_date.strftime("%Y%m%d"),
        session_date=session_date,
        entry_at=at(session_date, config.entry_time),
        add_at=at(session_date + timedelta(days=1), config.additional_time),
        exit_at=at(session_date + timedelta(days=1), config.exit_time),
    )


def candidate_sessions(now: datetime, config: BotConfig) -> list[SessionSchedule]:
    """Return today's and yesterday's cycles, newest first."""
    now_ist = now.astimezone(IST)
    today = now_ist.date()
    return [
        build_session(today, config),
        build_session(today - timedelta(days=1), config),
    ]


def within_catchup(now: datetime, target: datetime, minutes: int) -> bool:
    delta = (now - target).total_seconds()
    return 0 <= delta <= minutes * 60


def next_entry_session(now: datetime, config: BotConfig) -> SessionSchedule:
    """Return the next 18:30 IST session, used by preview/check."""
    now_ist = now.astimezone(IST)
    today = build_session(now_ist.date(), config)
    if now <= today.entry_at:
        return today
    return build_session(now_ist.date() + timedelta(days=1), config)


def next_event(now: datetime, config: BotConfig) -> tuple[str, datetime]:
    candidates: list[tuple[str, datetime]] = []
    now_ist_date = now.astimezone(IST).date()
    for offset in (-1, 0, 1):
        session = build_session(now_ist_date + timedelta(days=offset), config)
        for name, when in (
            ("entry", session.entry_at),
            ("additional", session.add_at),
            ("exit", session.exit_at),
        ):
            if when > now:
                candidates.append((f"{name}:{session.cycle_id}", when))
    if not candidates:
        raise RuntimeError("could not determine next schedule event")
    return min(candidates, key=lambda item: item[1])
