from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from engine import HeartbeatEngine
from models import Snapshot, was_announced
from tests.engine_fakes import FakeTypeSafe, FakeUseCase, choice_signal, context, rule_signal
from tests.isolation import IsolatedHomeTestCase

NIGHT = "23:00"
MORNING = "07:30"
QUIET = {"start": NIGHT, "end": MORNING, "min_priority": 80}


def local(hour: int, minute: int = 0) -> datetime:
    """A local wall-clock moment, expressed in UTC the way a tick receives it."""

    naive = datetime(2026, 9, 17, hour, minute)
    return naive.astimezone().astimezone(timezone.utc)


def tick(signals, *, now: datetime, quiet=QUIET, previous=None):
    engine = HeartbeatEngine(
        [FakeUseCase("host", [Snapshot(signals=tuple(signals))])],
        typesafe=FakeTypeSafe(None),
    )
    return engine.tick(context(now=now, quiet_hours=quiet), previous_state=previous)


def eligible(fingerprint: str, priority: int):
    return rule_signal(fingerprint, priority=priority, initial_observation="eligible")


class QuietHoursTests(IsolatedHomeTestCase):
    def test_a_low_priority_wake_is_held_back_at_night(self) -> None:
        result = tick([eligible("care:pause", 50)], now=local(2, 30))

        self.assertIsNone(result.candidate)
        self.assertEqual(result.render(), '{"wakeAgent": false}')
        self.assertEqual(result.state["quiet_deferred"], ["host:care:pause"])
        # Deferred, not delivered: nothing was stamped, so nothing starts a cooldown.
        self.assertEqual(result.state["delivered"], {})

    def test_the_deferred_wake_speaks_once_the_window_ends(self) -> None:
        night = tick([eligible("care:pause", 50)], now=local(2, 30))
        morning = tick([eligible("care:pause", 50)], now=local(8, 0), previous=night.state)

        self.assertIsNotNone(morning.candidate)
        self.assertEqual(morning.candidate.fingerprint, "care:pause")
        self.assertNotIn("quiet_deferred", morning.state)

    def test_the_priority_floor_still_lets_an_urgent_signal_through(self) -> None:
        result = tick([eligible("m1:disk", 90)], now=local(3, 0))

        self.assertIsNotNone(result.candidate)
        self.assertEqual(result.candidate.fingerprint, "m1:disk")
        self.assertNotIn("quiet_deferred", result.state)

    def test_without_a_floor_nothing_wakes_inside_the_window(self) -> None:
        result = tick(
            [eligible("m1:disk", 90)],
            now=local(3, 0),
            quiet={"start": NIGHT, "end": MORNING},
        )

        self.assertIsNone(result.candidate)
        self.assertEqual(result.state["quiet_deferred"], ["host:m1:disk"])

    def test_a_semantic_decision_is_judged_on_its_best_waking_action(self) -> None:
        typesafe = FakeTypeSafe(None)
        engine = HeartbeatEngine(
            [
                FakeUseCase(
                    "host",
                    [
                        Snapshot(
                            signals=(
                                choice_signal(
                                    "care:late",
                                    priority=10,
                                    fallback_label="notify",
                                    initial_observation="eligible",
                                ),
                            )
                        )
                    ],
                )
            ],
            typesafe=typesafe,
        )

        result = engine.tick(context(now=local(1, 0), quiet_hours=QUIET), previous_state=None)

        self.assertIsNone(result.candidate)
        self.assertEqual(result.state["quiet_deferred"], ["host:care:late"])
        # Held before the batch: a deferred judgment must not cost a model call.
        self.assertEqual(typesafe.calls, [])

    def test_daytime_and_malformed_windows_change_nothing(self) -> None:
        day = tick([eligible("care:pause", 50)], now=local(14, 0))
        self.assertIsNotNone(day.candidate)

        broken = tick(
            [eligible("care:pause", 50)],
            now=local(2, 0),
            quiet={"start": "not-a-time", "end": MORNING},
        )
        self.assertIsNotNone(broken.candidate)

    def test_a_window_inside_one_day_is_honoured(self) -> None:
        inside = tick(
            [eligible("care:pause", 50)],
            now=local(13, 30),
            quiet={"start": "13:00", "end": "14:00"},
        )
        self.assertIsNone(inside.candidate)

        outside = tick(
            [eligible("care:pause", 50)],
            now=local(13, 30) + timedelta(hours=1),
            quiet={"start": "13:00", "end": "14:00"},
        )
        self.assertIsNotNone(outside.candidate)

    def test_a_perishable_signal_is_dropped_rather_than_said_in_the_morning(self) -> None:
        late = rule_signal("care:late", priority=60, initial_observation="eligible")
        late = replace(late, perishable=True)

        night = tick([late], now=local(2, 30))
        self.assertIsNone(night.candidate)
        self.assertEqual(night.state["quiet_dropped"], ["host:care:late"])
        self.assertNotIn("quiet_deferred", night.state)
        # Stamped: the decision was "not worth saying", so the cooldown runs.
        self.assertEqual(night.state["delivered"]["host:care:late"]["action"], "quiet")

        # The stamp is a real decision: the same condition stays quiet for its cooldown.
        still_night = tick([late], now=local(4, 0), previous=night.state)
        self.assertIsNone(still_night.candidate)
        self.assertNotIn("quiet_dropped", still_night.state)

    def test_a_signal_the_window_swallowed_never_reads_as_announced(self) -> None:
        """`quiet` is a stamp the engine learned to write in 0.5.0, after the collectors
        were written. One reading action names against `{"baseline", "silent"}` concluded
        the user had been told — which is how a CVE stops being reported at all. The
        record states the outcome instead of implying it."""
        late = replace(
            rule_signal("care:late", priority=60, initial_observation="eligible"),
            perishable=True,
        )
        held = eligible("care:pause", 50)

        night = tick([late, held], now=local(2, 30))

        self.assertIsNone(night.candidate)
        self.assertFalse(was_announced(night.state["delivered"]["host:care:late"]))
        self.assertFalse(
            any(was_announced(record) for record in night.state["delivered"].values()),
            "nobody was woken tonight, so no record may claim otherwise",
        )

    def test_a_perishable_signal_still_wakes_outside_the_window(self) -> None:
        late = replace(
            rule_signal("care:late", priority=60, initial_observation="eligible"),
            perishable=True,
        )

        result = tick([late], now=local(22, 15))

        self.assertIsNotNone(result.candidate)
        self.assertEqual(result.candidate.fingerprint, "care:late")

    def test_measured_presence_crosses_the_window_the_clock_would_close(self) -> None:
        """02:00 is when "it is late" earns its keep — provided somebody is there to read it."""

        late = replace(
            rule_signal("care:late", priority=60, initial_observation="eligible"),
            perishable=True,
            awake_evidence=True,
        )

        result = tick([late], now=local(2, 30))

        self.assertIsNotNone(result.candidate)
        self.assertEqual(result.candidate.fingerprint, "care:late")
        self.assertNotIn("quiet_dropped", result.state)
        self.assertNotIn("quiet_deferred", result.state)

    def test_presence_outranks_a_floor_no_priority_could_clear(self) -> None:
        """The evidence is about who is awake, not about how loud the observation is."""

        water = replace(eligible("care:pause", 50), awake_evidence=True)

        result = tick([water], now=local(3, 0), quiet={"start": NIGHT, "end": MORNING})

        self.assertIsNotNone(result.candidate)
        self.assertEqual(result.candidate.fingerprint, "care:pause")

    def test_a_collector_that_cannot_see_presence_is_still_held_back(self) -> None:
        result = tick([eligible("care:pause", 50)], now=local(3, 0))

        self.assertIsNone(result.candidate)
        self.assertEqual(result.state["quiet_deferred"], ["host:care:pause"])
