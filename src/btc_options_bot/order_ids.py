from __future__ import annotations

import hashlib

ACTION_CODES = {"entry": "E", "additional": "A", "exit": "X"}


def client_order_id(cycle_id: str, action: str, attempt: int = 0) -> str:
    """Build a deterministic, <=32-character idempotency key."""
    if action not in ACTION_CODES:
        raise ValueError(f"unknown action: {action}")
    key = f"btc-options-bot|{cycle_id}|{action}|{attempt}"
    digest = hashlib.blake2s(key.encode("utf-8"), digest_size=8).hexdigest()
    clean_cycle = "".join(char for char in cycle_id if char.isalnum())[-8:]
    return f"X1{ACTION_CODES[action]}{clean_cycle}{digest}"[:32]
