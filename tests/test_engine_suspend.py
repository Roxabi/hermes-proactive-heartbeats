from __future__ import annotations

from datetime import timedelta

from engine import HeartbeatEngine
from models import Snapshot
from tests.engine_fakes import NOW, FakeTypeSafe, FakeUseCase, context, rule_signal
from tests.isolation import IsolatedHomeTestCase
from tick_facts import iso

BLIND = Snapshot(state={"ok": False}, diagnostics={"error": "unavailable"})


class SuspendedCollectorTests(IsolatedHomeTestCase):
    def test_a_suspended_collector_is_not_invoked_and_does_not_hide_a_sibling(self) -> None:
        probe = FakeUseCase("probe", [BLIND])
        host = FakeUseCase(
            "host",
            [
                Snapshot(
                    signals=(rule_signal("disk:root", priority=70, initial_observation="eligible"),)
                )
            ],
        )
        previous = {
            "version": 3,
            "use_cases": {
                "probe": {"state": {"cursor": 7}, "active": ["old"]},
                "host": {"state": {}, "active": []},
            },
            "delivered": {
                "probe:old": {"at": iso(NOW), "action": "notify", "woke": True},
            },
            "health": {"probe": {"streak": 2, "since": iso(NOW), "error": "unavailable"}},
        }

        result = HeartbeatEngine([probe, host], typesafe=FakeTypeSafe(None)).tick(
            context(),
            previous_state=previous,
            suspended=frozenset({"probe"}),
        )

        self.assertEqual(probe.previous_states, [])
        self.assertEqual(result.candidate.collector, "host")
        self.assertEqual(result.state["use_cases"]["probe"]["state"], {"cursor": 7})
        self.assertEqual(result.state["use_cases"]["probe"]["active"], ["old"])
        self.assertEqual(
            result.state["delivered"]["probe:old"]["action"],
            "notify",
        )
        self.assertNotIn("probe", result.state.get("health", {}))
        self.assertNotIn("probe", result.diagnostics)

    def test_the_streak_starts_over_after_the_span_and_the_watchdog_stays_quiet(self) -> None:
        probe = FakeUseCase("probe", [BLIND, BLIND])
        engine = HeartbeatEngine([probe], typesafe=FakeTypeSafe(None))
        blind = engine.tick(context(), previous_state=None)
        held = engine.tick(
            context(now=NOW + timedelta(minutes=15)),
            previous_state=blind.state,
            suspended=frozenset({"probe"}),
        )
        resumed = engine.tick(
            context(now=NOW + timedelta(minutes=30)),
            previous_state=held.state,
        )

        self.assertEqual(len(probe.previous_states), 2)
        self.assertIsNone(held.candidate)
        self.assertNotIn("health", held.state)
        self.assertEqual(resumed.state["health"]["probe"]["streak"], 1)
        self.assertIsNone(resumed.candidate)

    def test_a_kept_stamp_does_not_burst_when_the_span_ends(self) -> None:
        signal = rule_signal("disk:root", priority=70, initial_observation="eligible")
        probe = FakeUseCase("probe", [Snapshot(signals=(signal,)), Snapshot(signals=(signal,))])
        engine = HeartbeatEngine([probe], typesafe=FakeTypeSafe(None))
        previous = {
            "version": 3,
            "use_cases": {"probe": {"state": {}, "active": ["disk:root"]}},
            "delivered": {
                "probe:disk:root": {"at": iso(NOW), "action": "notify", "woke": True},
            },
        }

        held = engine.tick(context(), previous_state=previous, suspended=frozenset({"probe"}))
        resumed = engine.tick(
            context(now=NOW + timedelta(minutes=15)),
            previous_state=held.state,
        )

        self.assertIsNone(held.candidate)
        self.assertIsNone(resumed.candidate)
        self.assertEqual(resumed.state["delivered"]["probe:disk:root"]["action"], "notify")
