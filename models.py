"""Stable contracts between heartbeat use cases and the engine."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol

JsonObject = dict[str, Any]


@dataclass(frozen=True)
class ActionSpec:
    """Trusted delivery behavior selected by code or a semantic judgment."""

    name: str
    wake_agent: bool
    priority: int
    instruction: str = ""
    max_sentences: int = 0


SILENT = ActionSpec(name="silent", wake_agent=False, priority=0)


@dataclass(frozen=True)
class JudgmentSpec:
    """One TypeSafe question and its answer-label-to-action mapping."""

    question: JsonObject
    actions: Mapping[str, ActionSpec]
    fallback_label: str = "silent"


@dataclass(frozen=True)
class Signal:
    """One currently active condition emitted by a use case."""

    fingerprint: str
    facts: JsonObject
    decision: ActionSpec | JudgmentSpec
    initial_observation: Literal["baseline", "eligible"] = "baseline"
    repeat_after_seconds: int | None = None
    #: True when the observation is only worth saying now: "it is late" read out at
    #: 07:30 is wrong, while "you have not paused" is still true when the window ends.
    #: Quiet hours drop a perishable signal instead of deferring it.
    perishable: bool = False
    #: True when the collector has direct evidence the person is awake and at the keyboard.
    #: Quiet hours exist to protect sleep; a signal carrying that evidence cannot disturb it,
    #: and holding it back would silence the nudges that only matter at an odd hour
    #: ("it is 02:00 and you are still typing"). Evidence, never a wish: a collector that
    #: merely reads the clock must leave this false.
    awake_evidence: bool = False


@dataclass(frozen=True)
class Snapshot:
    """A use case's full current state for edge detection and persistence."""

    signals: tuple[Signal, ...] = ()
    state: JsonObject = field(default_factory=dict)
    diagnostics: JsonObject = field(default_factory=dict)


@dataclass(frozen=True)
class Candidate:
    """Resolved wake payload: one or more observations packed for a single agent wake."""

    collector: str
    fingerprint: str
    action: ActionSpec
    facts: JsonObject
    context: JsonObject = field(default_factory=dict)
    decision: JsonObject = field(default_factory=dict)
    observations: tuple[JsonObject, ...] | None = None

    def as_json(self) -> JsonObject:
        metadata = self.decision if isinstance(self.decision, Mapping) else {}
        source = metadata.get("source")
        decision = {
            "action": self.action.name,
            "source": source or "fallback",
            **{key: value for key, value in metadata.items() if key not in {"action", "source"}},
        }
        if self.observations is not None:
            inputs: Any = list(self.observations)
        else:
            inputs = [
                {
                    "collector": self.collector,
                    "fingerprint": self.fingerprint,
                    "facts": self.facts,
                    "decision": decision,
                }
            ]
        return {
            "collector": self.collector,
            "fingerprint": self.fingerprint,
            "action": self.action.name,
            "priority": self.action.priority,
            "inputs": inputs,
            "decision": decision,
            "context": self.context,
            "delivery": {
                "instruction": self.action.instruction,
                "max_sentences": self.action.max_sentences,
            },
        }


def was_announced(record: Any) -> bool:
    """True only when a delivery record proves the agent was woken for that fingerprint.

    Collectors that must not repeat themselves — a CVE already reported, a digest already
    sent — ask this instead of interpreting the action name. The engine records ``woke``
    from the resolved action, so a non-waking outcome it learns to stamp later cannot
    silently read as "said": that is how `quiet` (0.5.0) started counting as announced in
    a collector written against `{"baseline", "silent"}`.

    Anything unrecognised — a malformed record, one written before this field existed —
    reads as not announced. Repeating an observation costs a duplicate line; swallowing
    one costs the whole point of the collector.
    """
    return isinstance(record, Mapping) and record.get("woke") is True


@dataclass(frozen=True)
class TickContext:
    """Read-only context for one tick, narrowed to the use case being invoked.

    `delivered` maps that use case's own fingerprints — never another use case's — to the
    delivery record persisted before this tick:
    `{"at": "<iso8601>", "action": "<action name>", "woke": <bool>}`. `woke` is the only
    field that answers "was this said to the user"; read it through `was_announced`
    rather than by interpreting the action name. A first tick sees an empty mapping.
    """

    now: datetime
    settings: JsonObject
    delivered: JsonObject = field(default_factory=dict)


class HeartbeatUseCase(Protocol):
    """Deep use-case seam: collect a complete snapshot, nothing else."""

    id: str

    def collect(self, context: TickContext, previous_state: JsonObject) -> Snapshot: ...
