from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from engine import HeartbeatEngine
from models import ActionSpec, JudgmentSpec, Signal, Snapshot
from tests.engine_fakes import NOW, FakeTypeSafe, FakeUseCase, context, rule_signal
from tests.isolation import IsolatedHomeTestCase

DRIFT = ActionSpec(
    name="drift",
    wake_agent=True,
    priority=45,
    instruction="Name every priority.",
    max_sentences=1,
    wording_facts=("priorities",),
)
COMMS = ActionSpec(
    name="comms",
    wake_agent=True,
    priority=42,
    instruction="Name the messaging apps, then every priority.",
    max_sentences=1,
    wording_facts=("priorities",),
)
SILENT = ActionSpec(name="silent", wake_agent=False, priority=0)


def drift_facts(*, priorities: list[str], app_minutes: float) -> dict[str, Any]:
    return {"priorities": priorities, "apps": [{"app": "ghostty", "minutes": app_minutes}]}


def judged(facts: dict[str, Any]) -> Signal:
    return Signal(
        fingerprint="care:drift",
        facts=facts,
        decision=JudgmentSpec(
            question={"type": "choice", "instructions": "Where did attention go?"},
            actions={"drift": DRIFT, "comms": COMMS, "aligned": SILENT},
            fallback_label="aligned",
        ),
        initial_observation="eligible",
    )


def tick(*signals: Signal, answers: Any = None, minutes: int = 0) -> dict[str, Any]:
    engine = HeartbeatEngine(
        [FakeUseCase("sense", [Snapshot(signals=signals)])],
        typesafe=FakeTypeSafe(answers or {}),
    )
    result = engine.tick(
        context(now=NOW + timedelta(minutes=minutes), name="ops", context={"who": "M"}),
        previous_state=None,
    )
    rendered: dict[str, Any] = json.loads(result.render())
    return rendered


def choice(label: str, confidence: float) -> dict[str, Any]:
    return {
        "sense:care:drift": {
            "type": "choice",
            "choice": label,
            "probabilities": {label: confidence},
            "confidence": confidence,
        }
    }


class ReuseKeyTests(IsolatedHomeTestCase):
    def test_facts_outside_the_wording_move_without_changing_the_key(self) -> None:
        early = tick(
            judged(drift_facts(priorities=["Metalyde", "Ether"], app_minutes=5.9)),
            answers=choice("drift", 0.91),
        )
        later = tick(
            judged(drift_facts(priorities=["Metalyde", "Ether"], app_minutes=10.7)),
            answers=choice("drift", 0.84),
            minutes=12,
        )

        self.assertTrue(str(early.get("reuseKey")).startswith("sha256:"))
        self.assertEqual(early["reuseKey"], later["reuseKey"])
        # The agent still receives every fact; only the key ignores them.
        self.assertNotEqual(
            early["heartbeat_candidate"]["inputs"][0]["facts"],
            later["heartbeat_candidate"]["inputs"][0]["facts"],
        )

    def test_a_wording_fact_changes_the_key(self) -> None:
        before = tick(
            judged(drift_facts(priorities=["Metalyde"], app_minutes=5.9)),
            answers=choice("drift", 0.9),
        )
        after = tick(
            judged(drift_facts(priorities=["Metalyde", "Ether"], app_minutes=5.9)),
            answers=choice("drift", 0.9),
        )

        self.assertNotEqual(before["reuseKey"], after["reuseKey"])

    def test_the_judged_action_is_part_of_the_key(self) -> None:
        facts = drift_facts(priorities=["Metalyde"], app_minutes=5.9)

        drift = tick(judged(facts), answers=choice("drift", 0.9))
        comms = tick(judged(facts), answers=choice("comms", 0.9))

        self.assertNotEqual(drift["reuseKey"], comms["reuseKey"])

    def test_one_observation_without_wording_facts_makes_the_wake_unreusable(self) -> None:
        rendered = tick(
            judged(drift_facts(priorities=["Metalyde"], app_minutes=5.9)),
            rule_signal("disk:root", priority=80, initial_observation="eligible"),
            answers=choice("drift", 0.9),
        )

        self.assertEqual(len(rendered["heartbeat_candidate"]["inputs"]), 2)
        self.assertNotIn("reuseKey", rendered)

    def test_a_bare_string_of_wording_facts_fails_the_collector(self) -> None:
        signal = Signal(
            fingerprint="care:drift",
            facts={"priorities": ["Metalyde"]},
            decision=ActionSpec(
                name="drift",
                wake_agent=True,
                priority=45,
                wording_facts="priorities",  # type: ignore[arg-type]
            ),
            initial_observation="eligible",
        )
        engine = HeartbeatEngine(
            [FakeUseCase("sense", [Snapshot(signals=(signal,))])], typesafe=FakeTypeSafe({})
        )

        result = engine.tick(context(), previous_state=None)

        self.assertIsNone(result.candidate)
        self.assertIn("wording_facts", result.diagnostics["sense"]["message"])
