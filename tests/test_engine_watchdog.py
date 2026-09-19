from __future__ import annotations

from datetime import timedelta

from engine import HeartbeatEngine
from models import Snapshot
from tests.engine_fakes import NOW, FakeTypeSafe, FakeUseCase, context, rule_signal, wake_inputs
from tests.isolation import IsolatedHomeTestCase

BLIND = Snapshot(state={"ok": False}, diagnostics={"error": "unavailable"})
SEEING = Snapshot(signals=(), state={"ok": True})


def run(snapshots: list, *, ticks: int | None = None, **settings) -> list:
    """Drive one collector through consecutive ticks, carrying state forward."""

    engine = HeartbeatEngine([FakeUseCase("probe", snapshots)], typesafe=FakeTypeSafe(None))
    results = []
    state = None
    for index in range(ticks if ticks is not None else len(snapshots)):
        now = NOW + timedelta(minutes=15 * index)
        result = engine.tick(context(now=now, **settings), previous_state=state)
        state = result.state
        results.append(result)
    return results


class CollectorWatchdogTests(IsolatedHomeTestCase):
    def test_a_soft_error_counts_as_blindness_and_wakes_after_three_ticks(self) -> None:
        results = run([BLIND, BLIND, BLIND])

        self.assertIsNone(results[0].candidate)
        self.assertIsNone(results[1].candidate)
        third = results[2]
        self.assertIsNotNone(third.candidate)
        self.assertEqual(third.candidate.action.name, "collector_down")
        facts = wake_inputs(third)[0]["facts"]
        self.assertEqual(facts["collector"], "probe")
        self.assertEqual(facts["failing_ticks"], 3)
        self.assertEqual(facts["error"], "unavailable")
        self.assertEqual(facts["since"], "2026-09-17T12:00:00Z")

    def test_a_raising_collector_is_counted_the_same_way(self) -> None:
        results = run([RuntimeError("ssh: connect failed"), RuntimeError("ssh: connect failed")])

        self.assertEqual(results[1].state["health"]["probe"]["streak"], 2)
        self.assertEqual(
            results[1].state["health"]["probe"]["error"],
            "RuntimeError: ssh: connect failed",
        )
        self.assertIsNone(results[1].candidate)

    def test_one_clean_tick_clears_the_streak(self) -> None:
        results = run([BLIND, BLIND, SEEING, BLIND])

        self.assertNotIn("health", results[2].state)
        self.assertEqual(results[3].state["health"]["probe"]["streak"], 1)
        self.assertIsNone(results[3].candidate)

    def test_the_alarm_does_not_repeat_inside_its_cooldown(self) -> None:
        results = run([BLIND, BLIND, BLIND, BLIND, BLIND])

        woke = [index for index, result in enumerate(results) if result.candidate is not None]
        self.assertEqual(woke, [2])
        self.assertEqual(results[4].state["health"]["probe"]["streak"], 5)

    def test_after_ticks_can_be_tuned_and_zero_disables_the_watchdog(self) -> None:
        early = run([BLIND], collector_watchdog={"after_ticks": 1})
        self.assertIsNotNone(early[0].candidate)

        off = run([BLIND, BLIND, BLIND, BLIND], collector_watchdog={"after_ticks": 0})
        self.assertEqual([result.candidate for result in off], [None, None, None, None])
        self.assertEqual(off[3].state["health"]["probe"]["streak"], 4)

    def test_a_blind_collector_never_hides_a_sibling_wake(self) -> None:
        host_signal = rule_signal("disk:root", priority=70, initial_observation="eligible")
        engine = HeartbeatEngine(
            [
                FakeUseCase("probe", [BLIND, BLIND, BLIND]),
                FakeUseCase("host", [Snapshot(signals=(host_signal,))] * 3),
            ],
            typesafe=FakeTypeSafe(None),
        )
        state = None
        results = []
        for index in range(3):
            result = engine.tick(
                context(now=NOW + timedelta(minutes=15 * index)), previous_state=state
            )
            state = result.state
            results.append(result)

        # Tick 1: the blind probe does not suppress the host observation.
        self.assertEqual([item["collector"] for item in wake_inputs(results[0])], ["host"])
        # Tick 3: host sits in its cooldown, the watchdog is what has something to say.
        self.assertEqual([item["collector"] for item in wake_inputs(results[2])], ["_watchdog"])
        self.assertEqual(results[2].state["use_cases"]["host"]["active"], ["disk:root"])
