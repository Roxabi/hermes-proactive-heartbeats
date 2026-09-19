"""Heartbeat tick engine: collect, gate, batch TypeSafe, pack, persist."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

try:
    from . import _bootstrap  # noqa: F401
except ImportError:  # flat plugin-dir / unittest load
    import _bootstrap  # noqa: F401
from models import Candidate, HeartbeatUseCase, JsonObject, TickContext
from tick_collect import CollectPass, collect_use_cases, stamp_baseline
from tick_decide import evaluate_due, resolve_candidate
from tick_facts import iso
from tick_health import WATCHDOG_ID, WatchdogPass, next_health, watchdog_pass
from tick_pack import pack_wake
from tick_quiet import QuietPass, filter_quiet
from tick_state import (
    STATE_VERSION,
    coerce_previous,
    default_cooldown,
    delivery_key,
    empty_state,
    retained_delivered,
    threshold,
)


class SupportsEvaluate(Protocol):
    def evaluate(
        self,
        state: dict[str, Any],
        questions: Any,
    ) -> dict[str, Any] | None: ...


@dataclass(frozen=True)
class TickResult:
    """Immutable outcome of one heartbeat tick."""

    candidate: Candidate | None
    state: JsonObject
    diagnostics: JsonObject

    def render(self) -> str:
        """Return the exact one-line stdout contract for Hermes Cron."""

        if self.candidate is None:
            return '{"wakeAgent": false}'
        payload = {"heartbeat_candidate": self.candidate.as_json()}
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True)
class HeartbeatEngine:
    """Run one deterministic heartbeat tick across registered use cases."""

    use_cases: tuple[HeartbeatUseCase, ...]
    typesafe_client: SupportsEvaluate | None = None

    def __init__(
        self,
        use_cases: Iterable[HeartbeatUseCase],
        typesafe_client: SupportsEvaluate | None = None,
        *,
        typesafe: SupportsEvaluate | None = None,
    ) -> None:
        object.__setattr__(
            self,
            "use_cases",
            tuple(sorted(use_cases, key=lambda use_case: use_case.id)),
        )
        client = typesafe_client if typesafe_client is not None else typesafe
        object.__setattr__(self, "typesafe_client", client)

    def tick(
        self,
        context: TickContext,
        previous: Mapping[str, Any] | None = None,
        *,
        previous_state: Mapping[str, Any] | None = None,
    ) -> TickResult:
        """Collect snapshots, gate due signals, optionally judge, and persist next state.

        A collector that fails is isolated: it contributes no signal and keeps its previous
        state, while every healthy collector still resolves and wakes. Sustained failure is
        itself reported, by the watchdog, instead of silently shrinking what is watched.
        """

        prior = coerce_previous(previous_state if previous_state is not None else previous)
        is_baseline = prior is None
        previous_root = prior or empty_state()
        previous_delivered = previous_root.get("delivered") or {}
        collected = collect_use_cases(self.use_cases, context, previous_root, is_baseline)
        delivered = dict(previous_delivered)
        if is_baseline:
            stamp_baseline(delivered, collected, context.now)
        health = next_health(previous_root.get("health"), collected, context.now)
        watchdog = watchdog_pass(health, context=context, delivered=previous_delivered)
        quiet = filter_quiet(
            [*collected.due, *watchdog.due],
            settings=context.settings,
            now=context.now,
        )
        answers = evaluate_due(self.typesafe_client, context, quiet.due)
        return _settle(collected, watchdog, health, quiet, delivered, answers, context)


def _settle(
    collected: CollectPass,
    watchdog: WatchdogPass,
    health: JsonObject,
    quiet: QuietPass,
    delivered: dict[str, Any],
    answers: Mapping[str, Any] | None,
    context: TickContext,
) -> TickResult:
    due = quiet.due
    candidates = [
        candidate
        for item in due
        for candidate in [
            resolve_candidate(
                item,
                answers,
                context=context,
                threshold=threshold(context.settings),
            )
        ]
        if candidate is not None
    ]
    packed = pack_wake(candidates, context)
    delivered_keys = {
        delivery_key(candidate.collector, candidate.fingerprint) for candidate in candidates
    }
    for candidate in candidates:
        delivered[delivery_key(candidate.collector, candidate.fingerprint)] = {
            "at": iso(context.now),
            "action": candidate.action.name,
        }

    for item in due:
        key = delivery_key(item.use_case_id, item.signal.fingerprint)
        if key in delivered_keys:
            continue
        delivered[key] = {"at": iso(context.now), "action": "silent"}

    use_cases = dict(collected.next_use_cases)
    if watchdog.active:
        use_cases[WATCHDOG_ID] = {"state": {}, "active": list(watchdog.active)}

    state: JsonObject = {
        "version": STATE_VERSION,
        "use_cases": use_cases,
        "delivered": retained_delivered(
            delivered,
            use_cases=use_cases,
            diagnostics=collected.diagnostics,
            now=context.now,
            default_cooldown=default_cooldown(context.settings),
        ),
    }
    if health:
        state["health"] = health
    if quiet.deferred:
        state["quiet_deferred"] = list(quiet.deferred)
    return TickResult(candidate=packed, state=state, diagnostics=collected.diagnostics)
