"""Pack every waking observation into one heartbeat candidate."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

import _bootstrap  # noqa: F401
from models import ActionSpec, Candidate, JsonObject, TickContext
from tick_facts import bound_facts, iso


def pack_wake(candidates: list[Candidate], context: TickContext) -> Candidate | None:
    """Pack every waking candidate into one stdout candidate. Order is priority only."""
    if not candidates:
        return None
    ordered = sorted(
        candidates,
        key=lambda candidate: (
            -candidate.action.priority,
            candidate.collector,
            candidate.fingerprint,
        ),
    )
    observations = tuple(_observation(candidate) for candidate in ordered)
    action = _bundle_action(ordered)
    packed_context = _candidate_context(context)
    reuse_key = _reuse_key(ordered, action, packed_context)
    if len(ordered) == 1:
        lead = ordered[0]
        return Candidate(
            collector=lead.collector,
            fingerprint=lead.fingerprint,
            action=lead.action,
            facts=lead.facts,
            context=packed_context,
            decision=lead.decision,
            observations=observations,
            reuse_key=reuse_key,
        )
    sources = {
        str(candidate.decision.get("source") or "fallback")
        for candidate in ordered
        if isinstance(candidate.decision, Mapping)
    }
    source = sources.pop() if len(sources) == 1 else "bundle"
    return Candidate(
        collector="bundle",
        fingerprint="tick",
        action=action,
        facts={},
        context=packed_context,
        decision={"action": action.name, "source": source, "count": len(ordered)},
        observations=observations,
        reuse_key=reuse_key,
    )


def _observation(candidate: Candidate) -> JsonObject:
    decision = dict(candidate.decision) if isinstance(candidate.decision, Mapping) else {}
    return {
        "collector": candidate.collector,
        "fingerprint": candidate.fingerprint,
        "facts": candidate.facts,
        "decision": decision,
    }


def _reuse_key(
    ordered: list[Candidate], action: ActionSpec, packed_context: JsonObject
) -> str | None:
    """Digest of everything the packed message may say, or None when it cannot be reused.

    Each observation contributes its action and only the facts that action is worded from; the
    rest of its facts may move freely without changing what is said. ``now`` is left out of the
    context: it moves every tick, and an action that says the time lists the fact carrying it.
    """
    inputs: list[JsonObject] = []
    for candidate in ordered:
        keys = candidate.action.wording_facts
        if keys is None:
            return None
        inputs.append(
            {
                "collector": candidate.collector,
                "fingerprint": candidate.fingerprint,
                "action": candidate.action.name,
                "facts": {key: candidate.facts.get(key) for key in keys},
            }
        )
    material = {
        "context": {key: value for key, value in packed_context.items() if key != "now"},
        "instruction": action.instruction,
        "max_sentences": action.max_sentences,
        "inputs": inputs,
    }
    payload = json.dumps(
        material, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def _bundle_action(candidates: list[Candidate]) -> ActionSpec:
    if len(candidates) == 1:
        return candidates[0].action
    notes = [
        f"[{candidate.fingerprint}] {candidate.action.instruction.strip()}"
        for candidate in candidates
        if candidate.action.instruction.strip()
    ]
    per_item = (" Per-observation notes: " + " ".join(notes)) if notes else ""
    return ActionSpec(
        name="bundle",
        wake_agent=True,
        priority=max(candidate.action.priority for candidate in candidates),
        instruction=_BUNDLE_INSTRUCTION + per_item,
        max_sentences=sum(max(1, candidate.action.max_sentences) for candidate in candidates),
    )


# Several observations used to come out as one pasted sentence each ("Drink some water.
# Get back to your priorities."). The writer is told how to relate them, not a template.
_BUNDLE_INSTRUCTION = (
    "Several observations in inputs woke you at once. Write ONE coherent message, not "
    "one sentence per observation placed side by side. First work out how they relate: "
    "which fact explains another, what he should do now, what he should come back to "
    "afterwards. Then write a single line of thought that follows that logic, with "
    "natural transitions between the parts, each fact said once, and every "
    "observation's point kept. The per-observation notes below say WHAT each one must "
    "convey; their wording, examples and sentence counts describe that observation "
    "alone and are not sentences to paste. Do not add topics that are not in inputs. "
    "Do not re-open whether to speak."
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
