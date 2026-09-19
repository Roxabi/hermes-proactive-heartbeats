"""Collector health across ticks: wake once a collector has stopped observing.

A collector that cannot observe reports it two ways: a hard failure (exception, wrong
return type, invalid signal) recorded in the tick diagnostics, or a soft ``error``
diagnostic on its own snapshot — the shape a probe uses when its host is unreachable.
Both are silence. Without this module that silence is indistinguishable from "nothing to
report", so a heartbeat can watch nothing for days and never say so.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import _bootstrap  # noqa: F401
from models import ActionSpec, JsonObject, Signal, TickContext
from tick_decide import DueSignal, is_due
from tick_facts import iso
from tick_state import default_cooldown, delivery_key

#: Pseudo-collector owning watchdog deliveries. A collector id must match
#: ``^[a-z][a-z0-9_-]{0,63}$``, so a leading underscore cannot collide with a real one.
WATCHDOG_ID = "_watchdog"
DEFAULT_AFTER_TICKS = 3
_MAX_ERROR_CHARS = 200

COLLECTOR_DOWN = ActionSpec(
    name="collector_down",
    wake_agent=True,
    priority=90,
    instruction=(
        "Say in one short sentence that a heartbeat collector has stopped observing: name it, "
        "how long it has been failing, and the reported error. Do not guess the cause."
    ),
    max_sentences=1,
)


@dataclass(frozen=True)
class WatchdogPass:
    """Watchdog fingerprints active this tick, and the subset that is due."""

    active: tuple[str, ...] = ()
    due: tuple[DueSignal, ...] = ()


def watchdog_settings(settings: Mapping[str, Any]) -> tuple[int, int | None]:
    """``(after_ticks, repeat_after_seconds)``; ``after_ticks <= 0`` disables the watchdog."""

    raw = settings.get("collector_watchdog")
    config = raw if isinstance(raw, Mapping) else {}
    try:
        after_ticks = int(config.get("after_ticks", DEFAULT_AFTER_TICKS))
    except (TypeError, ValueError):
        after_ticks = DEFAULT_AFTER_TICKS
    repeat = config.get("repeat_after_seconds")
    try:
        repeat_after = None if repeat is None else int(repeat)
    except (TypeError, ValueError):
        repeat_after = None
    return max(0, after_ticks), repeat_after


def error_text(value: Any) -> str | None:
    """The error a diagnostics mapping reports, or None when it only carries notes."""

    if not isinstance(value, Mapping):
        return None
    error = value.get("error")
    if error is None or error is False or error == "":
        return None
    text = str(error)
    message = value.get("message")
    if message and str(message) != text:
        text = f"{text}: {message}"
    return text[:_MAX_ERROR_CHARS]


def next_health(previous: Any, collected: Any, now: Any) -> JsonObject:
    """Consecutive-failure streak per collector. A collector that observed is dropped."""

    prior = previous if isinstance(previous, Mapping) else {}
    health: JsonObject = {}
    for use_case_id, entry in collected.next_use_cases.items():
        error = error_text(collected.diagnostics.get(use_case_id))
        if error is None and isinstance(entry, Mapping):
            error = error_text(entry.get("diagnostics"))
        if error is None:
            continue
        streak = 1
        since = iso(now)
        before = prior.get(use_case_id)
        if isinstance(before, Mapping):
            try:
                streak = int(before.get("streak", 0)) + 1
            except (TypeError, ValueError):
                streak = 1
            previous_since = before.get("since")
            if streak > 1 and isinstance(previous_since, str) and previous_since:
                since = previous_since
        health[use_case_id] = {"streak": streak, "since": since, "error": error}
    return health


def watchdog_pass(
    health: Mapping[str, Any],
    *,
    context: TickContext,
    delivered: Mapping[str, Any],
) -> WatchdogPass:
    """Signals for collectors blind for at least ``after_ticks`` consecutive ticks."""

    after_ticks, repeat_after = watchdog_settings(context.settings)
    if after_ticks <= 0:
        return WatchdogPass()

    active: list[str] = []
    due: list[DueSignal] = []
    cooldown = default_cooldown(context.settings)
    for use_case_id in sorted(health):
        record = health[use_case_id]
        if not isinstance(record, Mapping):
            continue
        try:
            streak = int(record.get("streak", 0))
        except (TypeError, ValueError):
            continue
        if streak < after_ticks:
            continue
        fingerprint = f"collector-down:{use_case_id}"
        active.append(fingerprint)
        signal = Signal(
            fingerprint=fingerprint,
            facts={
                "collector": use_case_id,
                "failing_ticks": streak,
                "since": record.get("since"),
                "error": record.get("error"),
            },
            decision=COLLECTOR_DOWN,
            initial_observation="eligible",
            repeat_after_seconds=repeat_after,
        )
        if is_due(
            use_case_id=WATCHDOG_ID,
            signal=signal,
            delivered=delivered,
            now=context.now,
            default_cooldown=cooldown,
        ):
            due.append(
                DueSignal(
                    use_case_id=WATCHDOG_ID,
                    signal=signal,
                    question_id=delivery_key(WATCHDOG_ID, fingerprint),
                )
            )
    return WatchdogPass(tuple(active), tuple(due))
