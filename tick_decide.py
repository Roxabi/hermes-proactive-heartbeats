"""Due gates, TypeSafe batching, and candidate resolution for one tick."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import _bootstrap  # noqa: F401
from models import ActionSpec, Candidate, JsonObject, JudgmentSpec, Signal, TickContext
from tick_facts import bound_facts, iso, parse_time
from tick_state import delivery_key


@dataclass(frozen=True)
class DueSignal:
    use_case_id: str
    signal: Signal
    question_id: str


def is_due(
    *,
    use_case_id: str,
    signal: Signal,
    delivered: Mapping[str, Any],
    now: datetime,
    default_cooldown: int,
) -> bool:
    fingerprint = signal.fingerprint
    key = delivery_key(use_case_id, fingerprint)
    record = delivered.get(key)
    if not isinstance(record, Mapping):
        # First appearance, or reappearance after the record was pruned.
        return True
    delivered_at = parse_time(record.get("at"))
    if delivered_at is None:
        return True

    cooldown = (
        signal.repeat_after_seconds if signal.repeat_after_seconds is not None else default_cooldown
    )
    try:
        cooldown_seconds = max(0, int(cooldown))
    except (TypeError, ValueError):
        cooldown_seconds = default_cooldown

    elapsed: float = (now - delivered_at).total_seconds()
    return elapsed >= cooldown_seconds


def evaluate_due(
    client: Any,
    context: TickContext,
    due: list[DueSignal],
) -> dict[str, Any] | None:
    """Answers for every due semantic decision, in one batched call, or None."""

    questions: dict[str, dict[str, Any]] = {}
    for item in due:
        decision = item.signal.decision
        if not isinstance(decision, JudgmentSpec):
            continue
        questions[item.question_id] = dict(decision.question)

    if client is None or not questions:
        return None

    state = {
        "now": iso(context.now),
        "signals": {
            item.question_id: {
                "use_case": item.use_case_id,
                "fingerprint": item.signal.fingerprint,
                "facts": bound_facts(item.signal.facts),
            }
            for item in due
            if item.question_id in questions
        },
    }
    try:
        answers: dict[str, Any] | None = client.evaluate(state, questions)
    except Exception:  # noqa: BLE001 - treat client failures as unavailable
        return None
    return answers


def resolve_candidate(
    item: DueSignal,
    answers: Mapping[str, Any] | None,
    *,
    context: TickContext,
    threshold: float,
) -> Candidate | None:
    decision = item.signal.decision
    facts = bound_facts(item.signal.facts)
    candidate_context = _candidate_context(context)

    if isinstance(decision, ActionSpec):
        if not decision.wake_agent:
            return None
        return Candidate(
            collector=item.use_case_id,
            fingerprint=item.signal.fingerprint,
            action=decision,
            facts=facts,
            context=candidate_context,
            decision={"action": decision.name, "source": "rule"},
        )

    raw_answer = answers.get(item.question_id) if isinstance(answers, Mapping) else None
    label = _label_for_answer(raw_answer, judgment=decision, threshold=threshold)
    source = "typesafe" if label is not None else "fallback"
    if label is None:
        label = decision.fallback_label

    action = decision.actions.get(label)
    if action is None:
        action = decision.actions.get(decision.fallback_label)
        source = "fallback"
    if action is None or not action.wake_agent:
        return None

    return Candidate(
        collector=item.use_case_id,
        fingerprint=item.signal.fingerprint,
        action=action,
        facts=facts,
        context=candidate_context,
        decision=_decision_meta(label=label, action=action, answer=raw_answer, source=source),
    )


def _candidate_context(context: TickContext) -> JsonObject:
    extra = context.settings.get("context")
    merged: JsonObject = {}
    if isinstance(extra, Mapping):
        merged.update({key: value for key, value in extra.items() if value is not None})
    name = context.settings.get("name")
    if isinstance(name, str) and name:
        merged["heartbeat"] = name
    merged["now"] = iso(context.now)
    return bound_facts(merged)


def _decision_meta(*, label: str, action: ActionSpec, answer: Any, source: str) -> JsonObject:
    meta: JsonObject = {
        "label": label,
        "action": action.name,
        "source": source,
    }
    if isinstance(answer, Mapping):
        for key in ("type", "choice", "noul"):
            if key in answer:
                meta[key] = answer[key]
        probs = answer.get("probabilities")
        if isinstance(probs, Mapping):
            meta["probabilities"] = bound_facts(probs)
    return meta


def _label_for_answer(answer: Any, *, judgment: JudgmentSpec, threshold: float) -> str | None:
    if answer is None:
        return None

    if isinstance(answer, Mapping):
        answer_type = answer.get("type")
        if answer_type == "choice" or "choice" in answer:
            choice = answer.get("choice")
            return choice if isinstance(choice, str) else None
        if answer_type == "noul" or "noul" in answer:
            noul = answer.get("noul")
            if isinstance(noul, bool) or not isinstance(noul, (int, float)):
                return None
            return _noul_label(float(noul), judgment=judgment, threshold=threshold)
        return None

    if isinstance(answer, str):
        return answer

    if isinstance(answer, bool):
        return None

    if isinstance(answer, (int, float)):
        return _noul_label(float(answer), judgment=judgment, threshold=threshold)

    return None


def _noul_label(probability: float, *, judgment: JudgmentSpec, threshold: float) -> str:
    if probability >= threshold:
        for key in ("include", "true", "notify", "yes"):
            if key in judgment.actions:
                return key
        for key, action in judgment.actions.items():
            if action.wake_agent and key != judgment.fallback_label:
                return key
    return judgment.fallback_label
