from __future__ import annotations

import contextlib
import logging
import threading
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from . import __version__
from .broker import Broker
from .clock import Clock, SystemClock, sleep_seconds
from .config import BotConfig
from .errors import MarketDataError, OptionSelectionError, RiskCheckError
from .models import ExchangeOrder, OrderPlan, OrderRequest, TradePlan
from .notifier import Notifier
from .order_ids import client_order_id
from .schedule import candidate_sessions, next_event, within_catchup
from .state import CycleState, OrderRecord, StateStore
from .strategy import (
    build_additional_plan,
    build_exit_plan,
    build_trade_plan,
    current_spot_price,
    position_is_losing,
    target_reached,
)
from .utils import IST, UTC, decimal_str, iso_utc, parse_iso_datetime, to_decimal

logger = logging.getLogger("btc_options_bot.engine")


class BotEngine:
    """Restart-safe daily strategy state machine."""

    def __init__(
        self,
        config: BotConfig,
        broker: Broker,
        state: StateStore,
        notifier: Notifier,
        *,
        clock: Clock | None = None,
    ) -> None:
        self.config = config
        self.broker = broker
        self.state = state
        self.notifier = notifier
        self.clock = clock or SystemClock()
        self.stopped = False
        self.last_tick = ""
        self.last_error = ""
        self.consecutive_errors = 0
        self._last_plan_attempt: dict[str, float] = {}

    # -------------------------------------------------------------- public API
    def run(self, stop_event: threading.Event | None = None) -> None:
        stop_event = stop_event or threading.Event()
        self.startup_reconcile()
        self.notifier.notify(
            "TRADE",
            "Bot started",
            f"{self.config.mode.upper()} mode; exact BTC options strategy armed",
            context={
                "venue": self.config.venue,
                "base_url": self.config.base_url,
                "version": __version__,
            },
        )
        try:
            while not stop_event.is_set() and not self.stopped:
                try:
                    self.tick()
                    if self.consecutive_errors:
                        self.notifier.notify(
                            "INFO",
                            "Bot recovered",
                            f"API/strategy loop recovered after {self.consecutive_errors} error(s)",
                        )
                    self.consecutive_errors = 0
                    self.last_error = ""
                except Exception as exc:  # keep the service alive; every action is idempotent
                    self.consecutive_errors += 1
                    self.last_error = f"{type(exc).__name__}: {exc}"
                    logger.exception("engine tick failed")
                    self.state.add_event(
                        "ERROR",
                        "Engine tick failed",
                        self.last_error,
                        {"consecutive_errors": self.consecutive_errors},
                    )
                    if self.consecutive_errors in {1, 5, 20, 100}:
                        self.notifier.notify(
                            "ERROR",
                            "Bot loop error",
                            self.last_error,
                            context={"consecutive_errors": self.consecutive_errors},
                        )
                sleep_seconds(float(self.config.poll_seconds), stop_event)
        except KeyboardInterrupt:
            logger.info("keyboard interrupt received")
        finally:
            self.state.set_runtime("stopped_at", iso_utc(self.clock.now()))
            self.state.set_runtime("status", "STOPPED")
            self.notifier.notify("WARNING", "Bot stopped", "Trading loop has stopped")

    def startup_reconcile(self) -> None:
        now = self.clock.now()
        self.state.health_check()
        self.reconcile_working_orders(now)
        active = self.state.list_active_cycles()
        for cycle in active:
            self.recompute_cycle_from_orders(cycle)
            try:
                self.manage_cycle(cycle, now, paused=False, kill=False)
            except Exception as exc:
                logger.exception("startup reconciliation failed for cycle %s", cycle.cycle_id)
                self.state.add_event(
                    "WARNING",
                    "Startup reconciliation issue",
                    str(exc),
                    {"cycle_id": cycle.cycle_id},
                )
        self.detect_orphan_positions()
        self.state.set_runtime("startup_reconciled_at", iso_utc(now))
        self.state.set_runtime("bot_version", __version__)
        self.state.set_runtime("mode", self.config.mode)

    def tick(self) -> None:
        now = self.clock.now()
        self.last_tick = iso_utc(now)
        paused = self.config.pause_file.exists()
        kill = self.config.kill_file.exists()
        self.state.set_runtime("last_tick", self.last_tick)
        self.state.set_runtime("paused", paused)
        self.state.set_runtime("kill_requested", kill)
        self.state.set_runtime("now_ist", now.astimezone(IST).isoformat())

        self.reconcile_working_orders(now)
        active = self.state.list_active_cycles()

        if kill:
            for cycle in active:
                if cycle.status != "EXITING":
                    cycle.status = "EXITING"
                    cycle.exit_reason = cycle.exit_reason or "kill_switch"
                    self.state.save_cycle(cycle)
            active = self.state.list_active_cycles()

        for cycle in active:
            try:
                self.recompute_cycle_from_orders(cycle)
                self.manage_cycle(cycle, now, paused=paused, kill=kill)
            except Exception as exc:
                logger.exception("cycle %s management failed", cycle.cycle_id)
                self.state.add_event(
                    "ERROR",
                    "Cycle management failed",
                    str(exc),
                    {"cycle_id": cycle.cycle_id, "status": cycle.status},
                )
                self.notifier.notify(
                    "ERROR",
                    "Cycle management failed",
                    str(exc),
                    context={"cycle_id": cycle.cycle_id},
                )

        active = self.state.list_active_cycles()
        if not active and not paused and not kill:
            self.maybe_open_cycle(now)

        if kill and not self.state.list_active_cycles():
            self.stopped = True
            self.state.set_runtime("status", "KILLED")
        else:
            self.state.set_runtime("status", "RUNNING")

    def status(self) -> dict[str, Any]:
        now = self.clock.now()
        event_name, event_at = next_event(now, self.config)
        return {
            "version": __version__,
            "mode": self.config.mode,
            "venue": self.config.venue,
            "base_url": self.config.base_url,
            "now_utc": iso_utc(now),
            "now_ist": now.astimezone(IST).isoformat(),
            "last_tick": self.last_tick,
            "last_error": self.last_error,
            "consecutive_errors": self.consecutive_errors,
            "stopped": self.stopped,
            "paused": self.config.pause_file.exists(),
            "kill_requested": self.config.kill_file.exists(),
            "next_event": {"name": event_name, "at": iso_utc(event_at)},
            "active_cycles": [cycle.to_dict() for cycle in self.state.list_active_cycles()],
            "recent_cycles": [cycle.to_dict() for cycle in self.state.list_cycles(5)],
            "recent_events": self.state.list_events(20),
            "runtime": self.state.runtime_dict(),
            "broker": self.broker.name,
        }

    # ------------------------------------------------------------- scheduling
    def maybe_open_cycle(self, now: datetime) -> None:
        safety_halt = self.state.get_runtime("safety_halt")
        if safety_halt:
            logger.warning("new entries blocked by safety_halt=%s", safety_halt)
            return
        for session in candidate_sessions(now, self.config):
            if not within_catchup(now, session.entry_at, self.config.catchup_minutes):
                continue
            session_key = session.session_date.isoformat()
            if self.state.get_decision(session_key):
                continue
            if self.state.get_cycle(session.cycle_id):
                continue
            last_attempt = self._last_plan_attempt.get(session.cycle_id, 0.0)
            if now.timestamp() - last_attempt < 30:
                continue
            self._last_plan_attempt[session.cycle_id] = now.timestamp()

            try:
                result = build_trade_plan(
                    self.broker,
                    self.config,
                    session,
                    now=now,
                )
            except (MarketDataError, OptionSelectionError, RiskCheckError) as exc:
                logger.warning("entry planning rejected for %s: %s", session.cycle_id, exc)
                self.state.add_event(
                    "WARNING",
                    "Entry planning rejected",
                    str(exc),
                    {"cycle_id": session.cycle_id, "session_date": session_key},
                )
                self.notifier.notify(
                    "WARNING",
                    "Entry skipped",
                    str(exc),
                    context={"cycle_id": session.cycle_id},
                )
                return
            except Exception as exc:
                logger.exception("unexpected planning failure")
                self.state.add_event(
                    "ERROR",
                    "Entry planning error",
                    str(exc),
                    {"cycle_id": session.cycle_id},
                )
                return

            if result.plan is None:
                self.state.set_decision(session_key, "skip", result.reason)
                self.state.add_event(
                    "INFO",
                    "No trade",
                    result.reason,
                    {
                        "cycle_id": session.cycle_id,
                        "spot": decimal_str(result.signal.spot_price),
                        "ema21": decimal_str(result.signal.ema21),
                    },
                )
                self.notifier.notify(
                    "INFO",
                    "No trade at 18:30",
                    result.reason,
                    context={"cycle_id": session.cycle_id},
                )
                return

            self.open_cycle(result.plan, now)
            return

    def open_cycle(self, plan: TradePlan, now: datetime) -> None:
        option = plan.selected.instrument
        entry_cid = client_order_id(plan.cycle_id, "entry")
        cycle = CycleState(
            cycle_id=plan.cycle_id,
            session_date=plan.entry_at.astimezone(IST).date().isoformat(),
            status="PENDING_ENTRY",
            direction=plan.signal.direction,
            product_id=option.product_id,
            product_symbol=option.symbol,
            entry_at=iso_utc(plan.entry_at),
            add_at=iso_utc(plan.add_at),
            exit_at=iso_utc(plan.exit_at),
            entry_spot=decimal_str(plan.entry_btc_price),
            ema21=decimal_str(plan.signal.ema21),
            target_price=decimal_str(plan.target_btc_price),
            initial_capital=decimal_str(plan.capital),
            capital_asset=self.config.capital_asset,
            initial_budget=decimal_str(plan.budget),
            initial_quantity=plan.order_plan.quantity,
            initial_limit_price=decimal_str(plan.order_plan.limit_price),
            entry_client_order_id=entry_cid,
            created_at=iso_utc(now),
            updated_at=iso_utc(now),
        )
        cycle.set_instrument(option)
        # Persist the intent BEFORE sending anything to the exchange.
        self.state.save_cycle(cycle)
        self.state.set_decision(cycle.session_date, "entered", plan.signal.direction)

        request = self._buy_request(cycle, entry_cid, plan.order_plan, attempt=0)
        self.submit_order(cycle, "entry", request, now)
        self.state.add_event(
            "TRADE",
            "Initial entry submitted",
            f"{cycle.direction} {option.symbol} x{plan.order_plan.quantity}",
            context=plan.to_dict(),
        )
        self.notifier.notify(
            "TRADE",
            "Initial entry submitted",
            f"{cycle.direction} {option.symbol} x{plan.order_plan.quantity}",
            context=plan.to_dict(),
        )

    # ----------------------------------------------------------- cycle states
    def manage_cycle(
        self,
        cycle: CycleState,
        now: datetime,
        *,
        paused: bool,
        kill: bool,
    ) -> None:
        if cycle.status == "PENDING_ENTRY":
            self.manage_entry(cycle, now)
            return
        if cycle.status == "ACTIVE":
            self.manage_active(cycle, now, paused=paused, kill=kill)
            return
        if cycle.status == "EXITING":
            self.manage_exit(cycle, now)
            return

    def manage_entry(self, cycle: CycleState, now: datetime) -> None:
        record = self.state.get_order(cycle.entry_client_order_id)
        if record is None:
            cycle.status = "ABORTED"
            cycle.close_reason = "missing_entry_order_record"
            self.state.save_cycle(cycle)
            return
        order = self.sync_order(record, now)
        self.recompute_cycle_from_orders(cycle)

        timed_out = order is not None and order.is_working and self._record_age(record, now) > self.config.order_fill_timeout_seconds
        if timed_out:
            logger.warning("entry order %s timed out; cancelling", record.client_order_id)
            self.cancel_order_record(cycle, record, now)
            order = self.sync_order(record, now)
            self.recompute_cycle_from_orders(cycle)

        if order is not None and order.is_working:
            return
        if cycle.filled_quantity > 0:
            cycle.status = "ACTIVE"
            cycle.initial_quantity = self._action_filled_size(cycle, "entry")
            cycle.filled_quantity = cycle.initial_quantity + self._action_filled_size(
                cycle, "additional"
            )
            self.state.save_cycle(cycle)
            self.state.add_event(
                "TRADE",
                "Initial position active",
                f"{cycle.product_symbol} x{cycle.filled_quantity}",
                {"cycle_id": cycle.cycle_id, "avg_entry": cycle.avg_entry_premium},
            )
        else:
            cycle.status = "ABORTED"
            cycle.close_reason = "entry_order_not_filled"
            self.state.save_cycle(cycle)
            self.state.add_event(
                "WARNING",
                "Entry aborted",
                "No quantity was filled",
                {"cycle_id": cycle.cycle_id},
            )

    def manage_active(
        self,
        cycle: CycleState,
        now: datetime,
        *,
        paused: bool,
        kill: bool,
    ) -> None:
        instrument = cycle.instrument()
        exit_at = parse_iso_datetime(cycle.exit_at)
        add_at = parse_iso_datetime(cycle.add_at)

        if kill:
            self.start_exit(cycle, "kill_switch", now)
            return
        if exit_at is not None and now >= exit_at:
            self.start_exit(cycle, "time_exit", now)
            return
        if instrument.settlement_time - now <= timedelta(
            minutes=self.config.min_expiry_buffer_minutes
        ):
            self.start_exit(cycle, "expiry_safety", now)
            return

        # Target is based on BTC's underlying price, not the option premium.
        try:
            spot = current_spot_price(self.broker, self.config, now=now)
            target = to_decimal(cycle.target_price)
            if target_reached(cycle.direction, spot, target):
                self.state.add_event(
                    "TRADE",
                    "Target reached",
                    f"BTC {decimal_str(spot)} reached target {decimal_str(target)}",
                    {"cycle_id": cycle.cycle_id},
                )
                self.start_exit(cycle, "target_hit", now)
                return
        except MarketDataError as exc:
            logger.warning("target check skipped for %s: %s", cycle.cycle_id, exc)

        if cycle.additional_started and not cycle.additional_done:
            self.manage_additional_order(cycle, now)

        if (
            not cycle.additional_started
            and not cycle.additional_done
            and not paused
            and add_at is not None
        ):
            if within_catchup(now, add_at, self.config.catchup_minutes):
                self.maybe_start_additional(cycle, now)
            elif now > add_at + timedelta(minutes=self.config.catchup_minutes):
                cycle.additional_done = True
                cycle.additional_skip_reason = "missed_1am_window"
                self.state.save_cycle(cycle)

    def maybe_start_additional(self, cycle: CycleState, now: datetime) -> None:
        try:
            losing = position_is_losing(self.broker, cycle)
        except Exception as exc:
            logger.warning("loss check failed for %s: %s", cycle.cycle_id, exc)
            self.state.add_event(
                "WARNING",
                "Loss check failed",
                str(exc),
                {"cycle_id": cycle.cycle_id},
            )
            return

        if not losing:
            cycle.additional_done = True
            cycle.additional_skip_reason = "position_not_in_loss"
            self.state.save_cycle(cycle)
            self.state.add_event(
                "INFO",
                "Additional entry skipped",
                "Position was not in loss at 01:00 IST",
                {"cycle_id": cycle.cycle_id},
            )
            return

        try:
            plan = build_additional_plan(self.broker, self.config, cycle, now=now)
        except RiskCheckError as exc:
            cycle.additional_done = True
            cycle.additional_skip_reason = str(exc)
            self.state.save_cycle(cycle)
            self.state.add_event(
                "WARNING",
                "Additional entry skipped",
                str(exc),
                {"cycle_id": cycle.cycle_id},
            )
            return

        cid = client_order_id(cycle.cycle_id, "additional")
        cycle.additional_client_order_id = cid
        cycle.additional_limit_price = decimal_str(plan.limit_price)
        cycle.additional_started = True
        self.state.save_cycle(cycle)
        request = self._buy_request(cycle, cid, plan, attempt=0)
        self.submit_order(cycle, "additional", request, now)
        self.state.add_event(
            "TRADE",
            "Additional entry submitted",
            f"Position was in loss; adding {plan.quantity} contracts",
            {"cycle_id": cycle.cycle_id, **plan.to_dict()},
        )
        self.notifier.notify(
            "TRADE",
            "Additional entry submitted",
            f"{cycle.product_symbol} x{plan.quantity}",
            context={"cycle_id": cycle.cycle_id, **plan.to_dict()},
        )

    def manage_additional_order(self, cycle: CycleState, now: datetime) -> None:
        record = self.state.get_order(cycle.additional_client_order_id)
        if record is None:
            cycle.additional_done = True
            cycle.additional_skip_reason = "missing_additional_order_record"
            self.state.save_cycle(cycle)
            return
        order = self.sync_order(record, now)
        if order is not None and order.is_working and self._record_age(record, now) > self.config.order_fill_timeout_seconds:
            logger.warning("additional order %s timed out; cancelling", record.client_order_id)
            self.cancel_order_record(cycle, record, now)
            order = self.sync_order(record, now)
        if order is None or not order.is_working:
            cycle.additional_done = True
            self.state.save_cycle(cycle)
        self.recompute_cycle_from_orders(cycle)

    def start_exit(self, cycle: CycleState, reason: str, now: datetime) -> None:
        if cycle.status == "EXITING" and cycle.exit_reason:
            return
        cycle.status = "EXITING"
        cycle.exit_reason = reason
        self.state.save_cycle(cycle)
        self.state.add_event(
            "TRADE",
            "Exit started",
            reason,
            {"cycle_id": cycle.cycle_id, "product": cycle.product_symbol},
        )
        self.notifier.notify(
            "TRADE",
            "Exit started",
            reason,
            context={"cycle_id": cycle.cycle_id, "product": cycle.product_symbol},
        )
        self.manage_exit(cycle, now)

    def manage_exit(self, cycle: CycleState, now: datetime) -> None:
        self.cancel_working_entry_orders(cycle, now)
        self.recompute_cycle_from_orders(cycle)
        remaining = cycle.filled_quantity - cycle.closed_quantity
        if remaining <= 0:
            self.close_cycle(cycle, now)
            return

        working_exit: OrderRecord | None = None
        for cid in cycle.exit_order_ids:
            record = self.state.get_order(cid)
            if record is None:
                continue
            order = self.sync_order(record, now)
            if record.is_working and order is not None and order.is_working:
                working_exit = record
        self.recompute_cycle_from_orders(cycle)
        remaining = cycle.filled_quantity - cycle.closed_quantity
        if remaining <= 0:
            self.close_cycle(cycle, now)
            return

        if working_exit is not None:
            if self._record_age(working_exit, now) >= self.config.exit_reprice_seconds:
                logger.info("repricing exit order %s", working_exit.client_order_id)
                self.cancel_order_record(cycle, working_exit, now)
            return

        try:
            plan, _ = build_exit_plan(
                self.broker,
                self.config,
                cycle,
                quantity=remaining,
                allow_market_fallback=True,
            )
        except Exception as exc:
            logger.exception("could not build exit order for %s", cycle.cycle_id)
            self.state.add_event(
                "ERROR",
                "Exit order planning failed",
                str(exc),
                {"cycle_id": cycle.cycle_id},
            )
            self.notifier.notify(
                "ERROR",
                "Exit order planning failed",
                str(exc),
                context={"cycle_id": cycle.cycle_id},
            )
            return

        unused_cid = next(
            (cid for cid in cycle.exit_order_ids if self.state.get_order(cid) is None),
            None,
        )
        if unused_cid:
            cid = unused_cid
            attempt = cycle.exit_order_ids.index(cid)
        else:
            attempt = len(cycle.exit_order_ids)
            cid = client_order_id(cycle.cycle_id, "exit", attempt=attempt)
            cycle.add_exit_order_id(cid)
        self.state.save_cycle(cycle)
        request = self._sell_request(cycle, cid, plan, attempt=attempt)
        self.submit_order(cycle, "exit", request, now)
        self.state.add_event(
            "TRADE",
            "Exit order submitted",
            f"{request.order_type} sell x{plan.quantity}",
            {"cycle_id": cycle.cycle_id, **plan.to_dict()},
        )

    def close_cycle(self, cycle: CycleState, now: datetime) -> None:
        pnl = self.estimate_realized_pnl(cycle)
        cycle.status = "CLOSED"
        cycle.close_reason = cycle.exit_reason or "completed"
        cycle.realized_pnl = decimal_str(pnl)
        self.state.save_cycle(cycle)
        self.state.set_runtime("last_cycle", cycle.to_dict())
        self.state.add_event(
            "TRADE",
            "Cycle closed",
            f"{cycle.close_reason}; estimated PnL {decimal_str(pnl)}",
            {"cycle_id": cycle.cycle_id, "product": cycle.product_symbol},
        )
        self.notifier.notify(
            "TRADE",
            "Cycle closed",
            f"{cycle.close_reason}; estimated PnL {decimal_str(pnl)}",
            context={"cycle_id": cycle.cycle_id},
        )

    # ------------------------------------------------------------- order logic
    def _buy_request(
        self,
        cycle: CycleState,
        cid: str,
        plan: OrderPlan,
        *,
        attempt: int,
    ) -> OrderRequest:
        instrument = cycle.instrument()
        return OrderRequest(
            client_order_id=cid,
            product_id=int(cycle.product_id or 0),
            product_symbol=cycle.product_symbol,
            side="buy",
            size=plan.quantity,
            order_type=plan.order_type,
            limit_price=(plan.limit_price if plan.order_type == "limit_order" else None),
            reduce_only=False,
            time_in_force=("ioc" if plan.order_type == "market_order" else "gtc"),
            contract_value=instrument.contract_value,
            metadata={"fee_rate": decimal_str(plan.fee_rate), "attempt": attempt},
        )

    def _sell_request(
        self,
        cycle: CycleState,
        cid: str,
        plan: OrderPlan,
        *,
        attempt: int,
    ) -> OrderRequest:
        instrument = cycle.instrument()
        return OrderRequest(
            client_order_id=cid,
            product_id=int(cycle.product_id or 0),
            product_symbol=cycle.product_symbol,
            side="sell",
            size=plan.quantity,
            order_type=plan.order_type,
            limit_price=(plan.limit_price if plan.order_type == "limit_order" else None),
            reduce_only=True,
            time_in_force=("ioc" if plan.order_type == "market_order" else "gtc"),
            contract_value=instrument.contract_value,
            metadata={"fee_rate": decimal_str(plan.fee_rate), "attempt": attempt},
        )

    def submit_order(
        self,
        cycle: CycleState,
        action: str,
        request: OrderRequest,
        now: datetime,
    ) -> OrderRecord:
        existing = self.state.get_order(request.client_order_id)
        if existing is not None and existing.exchange_order_id:
            self.sync_order(existing, now)
            return self.state.get_order(request.client_order_id) or existing

        if existing is None:
            existing = OrderRecord(
                client_order_id=request.client_order_id,
                cycle_id=cycle.cycle_id,
                action=action,
                status="INTENT",
                product_id=request.product_id,
                product_symbol=request.product_symbol,
                side=request.side,
                size=request.size,
                limit_price=decimal_str(request.limit_price) if request.limit_price else "",
                order_type=request.order_type,
                reduce_only=request.reduce_only,
                attempt=int(request.metadata.get("attempt", 0)),
                created_at=iso_utc(now),
                updated_at=iso_utc(now),
            )
            self.state.save_order(existing)

        # An INTENT may represent an order that reached Delta before a crash.
        remote = self.broker.get_order_by_client_order_id(request.client_order_id)
        if remote is None:
            remote = self.broker.place_order(request)
        self.apply_exchange_order(existing, remote, now)
        return existing

    def sync_order(self, record: OrderRecord, now: datetime) -> ExchangeOrder | None:
        remote: ExchangeOrder | None = None
        try:
            if record.exchange_order_id:
                remote = self.broker.get_order(record.exchange_order_id)
            if remote is None:
                remote = self.broker.get_order_by_client_order_id(record.client_order_id)
        except Exception as exc:
            logger.warning("order lookup failed for %s: %s", record.client_order_id, exc)
            record.error = str(exc)
            self.state.save_order(record)
            return None

        if remote is None:
            # Some exchange endpoints are eventually consistent. Active orders
            # provide a second lookup path before an intent is declared failed.
            try:
                for candidate in self.broker.get_active_orders([record.product_id]):
                    if candidate.client_order_id == record.client_order_id:
                        remote = candidate
                        break
            except Exception as exc:
                logger.debug("active-order fallback failed for %s: %s", record.client_order_id, exc)
        if remote is None:
            if self._record_age(record, now) > self.config.order_fill_timeout_seconds + 60:
                record.status = "FAILED"
                record.error = "order was never found at the exchange"
                self.state.save_order(record)
                # Best effort: prevent a hidden working order from surviving.
                with contextlib.suppress(Exception):
                    self.broker.cancel_order(
                        product_id=record.product_id,
                        client_order_id=record.client_order_id,
                    )
            return None
        self.apply_exchange_order(record, remote, now)
        return remote

    def apply_exchange_order(
        self,
        record: OrderRecord,
        order: ExchangeOrder,
        now: datetime,
    ) -> None:
        record.exchange_order_id = order.id or record.exchange_order_id
        record.filled_size = order.filled_size
        record.average_fill_price = (
            decimal_str(order.average_fill_price)
            if order.average_fill_price is not None
            else record.average_fill_price
        )
        record.error = ""
        if order.is_filled:
            record.status = "FILLED"
        elif order.is_cancelled:
            record.status = "CANCELLED"
        elif order.is_working and order.filled_size > 0:
            record.status = "PARTIALLY_FILLED"
        elif order.is_working:
            record.status = "OPEN"
        else:
            record.status = "FAILED"
        record.updated_at = iso_utc(now)
        self.state.save_order(record)

    def cancel_order_record(
        self,
        cycle: CycleState,
        record: OrderRecord,
        now: datetime,
    ) -> None:
        try:
            self.broker.cancel_order(
                product_id=int(cycle.product_id or record.product_id),
                order_id=record.exchange_order_id or None,
                client_order_id=(None if record.exchange_order_id else record.client_order_id),
            )
        except Exception as exc:
            logger.warning("cancel failed for %s: %s", record.client_order_id, exc)
        self.sync_order(record, now)
        if record.is_working:
            record.status = "CANCELLED"
            record.updated_at = iso_utc(now)
            self.state.save_order(record)

    def cancel_working_entry_orders(self, cycle: CycleState, now: datetime) -> None:
        for record in self.state.list_orders_for_cycle(cycle.cycle_id):
            if record.action not in {"entry", "additional"} or not record.is_working:
                continue
            self.cancel_order_record(cycle, record, now)

    def reconcile_working_orders(self, now: datetime) -> None:
        for record in self.state.list_working_orders():
            try:
                self.sync_order(record, now)
            except Exception as exc:
                logger.warning("startup order reconcile failed for %s: %s", record.client_order_id, exc)

    def recompute_cycle_from_orders(self, cycle: CycleState) -> None:
        records = self.state.list_orders_for_cycle(cycle.cycle_id)
        entry_filled = 0
        add_filled = 0
        exit_filled = 0
        weighted_notional = Decimal("0")
        weighted_size = 0
        for record in records:
            fill_price = to_decimal(
                record.average_fill_price or record.limit_price,
                default=Decimal("0"),
            )
            if record.action == "entry":
                entry_filled += record.filled_size
                weighted_notional += fill_price * Decimal(record.filled_size)
                weighted_size += record.filled_size
            elif record.action == "additional":
                add_filled += record.filled_size
                weighted_notional += fill_price * Decimal(record.filled_size)
                weighted_size += record.filled_size
            elif record.action == "exit":
                exit_filled += record.filled_size
        cycle.initial_quantity = entry_filled
        cycle.additional_quantity = add_filled
        cycle.filled_quantity = entry_filled + add_filled
        cycle.closed_quantity = exit_filled
        if weighted_size:
            cycle.avg_entry_premium = decimal_str(weighted_notional / Decimal(weighted_size))
        self.state.save_cycle(cycle)

    def _action_filled_size(self, cycle: CycleState, action: str) -> int:
        return sum(
            record.filled_size
            for record in self.state.list_orders_for_cycle(cycle.cycle_id)
            if record.action == action
        )

    def estimate_realized_pnl(self, cycle: CycleState) -> Decimal:
        instrument = cycle.instrument()
        buy_value = Decimal("0")
        sell_value = Decimal("0")
        for record in self.state.list_orders_for_cycle(cycle.cycle_id):
            price = to_decimal(
                record.average_fill_price or record.limit_price,
                default=Decimal("0"),
            )
            value = price * Decimal(record.filled_size) * instrument.contract_value
            if record.action in {"entry", "additional"}:
                buy_value += value
            elif record.action == "exit":
                sell_value += value
        return sell_value - buy_value

    # --------------------------------------------------------------- utilities
    def _record_age(self, record: OrderRecord, now: datetime) -> float:
        created = parse_iso_datetime(record.created_at) or now
        return max(0.0, (now.astimezone(UTC) - created).total_seconds())

    def detect_orphan_positions(self) -> None:
        try:
            positions = self.broker.get_positions_margined()
        except Exception as exc:
            logger.warning("could not inspect positions for orphan detection: %s", exc)
            return
        managed = {
            int(cycle.product_id)
            for cycle in self.state.list_active_cycles()
            if cycle.product_id is not None
        }
        orphans = [
            position
            for position in positions
            if position.is_open and position.product_id not in managed
        ]
        if not orphans:
            return
        summary = [
            {"product_id": item.product_id, "symbol": item.product_symbol, "size": item.size}
            for item in orphans
        ]
        self.state.add_event(
            "WARNING",
            "Unmanaged positions detected",
            "The bot will not touch positions outside its persisted cycles",
            {"positions": summary},
        )
        if self.config.dedicated_account:
            self.state.set_runtime("safety_halt", "unmanaged_positions_in_dedicated_account")
            self.notifier.notify(
                "CRITICAL",
                "Unmanaged positions",
                "Dedicated account has positions outside bot state; new entries are blocked",
                context={"positions": summary},
            )
