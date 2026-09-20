"""Quiet hours: defer a wake instead of spending it while nobody wants to be woken.

Deferral, not suppression: a deferred observation is never stamped in `delivered`, so it
stays due and speaks as soon as the window ends. A suppressed one would start a cooldown
and could stay unsaid for hours after the person is back.

The window is a proxy, not the goal. What it protects is sleep, and the clock is only a
guess about sleep: a person typing at 02:00 is exactly who a "it is late" nudge is for.
A signal that carries `awake_evidence` was emitted on measured presence, so it crosses the
window untouched — otherwise the window would silence the nudges that only exist for the
odd hours it covers.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any

import _bootstrap  # noqa: F401
from models import ActionSpec, JudgmentSpec, Signal
from tick_decide import DueSignal
from tick_state import delivery_key


@dataclass(frozen=True)
class QuietWindow:
    """A local-time window, and the priority floor that still gets through it."""

    start: time
    end: time
    min_priority: int | None = None

    def covers(self, moment: datetime) -> bool:
        current = moment.astimezone().time()
        if self.start <= self.end:
            return self.start <= current < self.end
        # Crosses midnight: 23:00 → 07:30 is "late evening or early morning".
        return current >= self.start or current < self.end

    def passes(self, signal: Signal) -> bool:
        # The window is a proxy for "nobody is there to be woken". A collector that measured
        # presence knows better than the clock does, so its evidence outranks the floor.
        if signal.awake_evidence:
            return True
        if self.min_priority is None:
            return False
        return best_priority(signal) >= self.min_priority


@dataclass(frozen=True)
class QuietPass:
    """Due signals that survive the window, and what the window held or discarded."""

    due: list[DueSignal]
    deferred: tuple[str, ...] = ()
    dropped: tuple[str, ...] = ()


def parse_clock(value: Any) -> time | None:
    """`"23:00"` / `"7:30"` → time. Anything else is a misconfiguration, not a window."""

    if not isinstance(value, str):
        return None
    parts = value.strip().split(":")
    if len(parts) != 2:
        return None
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return time(hour=hour, minute=minute)


def quiet_window(settings: Mapping[str, Any]) -> QuietWindow | None:
    """The configured window, or None when quiet hours are off or unusable."""

    raw = settings.get("quiet_hours")
    if not isinstance(raw, Mapping):
        return None
    start = parse_clock(raw.get("start"))
    end = parse_clock(raw.get("end"))
    if start is None or end is None or start == end:
        return None
    floor = raw.get("min_priority")
    try:
        min_priority = None if floor is None else int(floor)
    except (TypeError, ValueError):
        min_priority = None
    return QuietWindow(start=start, end=end, min_priority=min_priority)


def best_priority(signal: Signal) -> int:
    """Highest priority this signal could still reach once its decision resolves."""

    decision = signal.decision
    if isinstance(decision, ActionSpec):
        return decision.priority if decision.wake_agent else 0
    if isinstance(decision, JudgmentSpec):
        waking = [
            action.priority
            for action in decision.actions.values()
            if isinstance(action, ActionSpec) and action.wake_agent
        ]
        return max(waking) if waking else 0
    return 0


def filter_quiet(due: list[DueSignal], *, settings: Mapping[str, Any], now: datetime) -> QuietPass:
    """Hold back every due signal the window covers, before it costs a model call."""

    window = quiet_window(settings)
    if window is None or not window.covers(now):
        return QuietPass(due=due)
    kept: list[DueSignal] = []
    deferred: list[str] = []
    dropped: list[str] = []
    for item in due:
        if window.passes(item.signal):
            kept.append(item)
            continue
        key = delivery_key(item.use_case_id, item.signal.fingerprint)
        # Perishable: saying it late is worse than not saying it. Stamping it keeps the
        # cooldown honest, so the window does not turn into a queue of stale remarks.
        (dropped if item.signal.perishable else deferred).append(key)
    return QuietPass(due=kept, deferred=tuple(deferred), dropped=tuple(dropped))
