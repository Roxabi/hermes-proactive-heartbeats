"""Persisted heartbeat state: use-case entries, delivered, pending."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

try:
    from . import _bootstrap  # noqa: F401
except ImportError:  # flat plugin-dir / unittest load
    import _bootstrap  # noqa: F401
from models import JsonObject
from tick_facts import parse_time

#: 3 dropped the fail-closed `pending` queue and added `health`. A record of another
#: version is discarded, never migrated: one quiet tick, then the gates re-baseline.
STATE_VERSION = 3


def coerce_previous(previous: Mapping[str, Any] | None) -> JsonObject | None:
    if previous is None:
        return None
    if not isinstance(previous, Mapping):
        return None
    version = previous.get("version")
    if version is None:
        return None
    try:
        if int(version) != STATE_VERSION:
            return None
    except (TypeError, ValueError):
        return None
    return dict(previous)


def empty_state() -> JsonObject:
    return {"version": STATE_VERSION, "use_cases": {}, "delivered": {}}


def use_case_entry(state: Mapping[str, Any], use_case_id: str) -> JsonObject:
    use_cases = state.get("use_cases") or {}
    if not isinstance(use_cases, Mapping):
        return {"state": {}, "active": []}
    entry = use_cases.get(use_case_id) or {}
    if not isinstance(entry, Mapping):
        return {"state": {}, "active": []}
    result: JsonObject = {
        "state": dict(entry.get("state") or {}) if isinstance(entry.get("state"), Mapping) else {},
        "active": list(entry.get("active") or []) if isinstance(entry.get("active"), list) else [],
    }
    if isinstance(entry.get("diagnostics"), Mapping):
        result["diagnostics"] = dict(entry["diagnostics"])
    return result


def default_cooldown(settings: Mapping[str, Any]) -> int:
    value = settings.get("default_cooldown_seconds", 14_400)
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 14_400


def threshold(settings: Mapping[str, Any]) -> float:
    value = settings.get("typesafe_threshold", 0.65)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.65


def delivery_key(use_case_id: str, fingerprint: str) -> str:
    return f"{use_case_id}:{fingerprint}"


def delivered_view(delivered: Any, use_case_id: str) -> JsonObject:
    if not isinstance(delivered, Mapping):
        return {}
    prefix = delivery_key(use_case_id, "")
    return {
        key[len(prefix) :]: dict(record)
        for key, record in delivered.items()
        if isinstance(key, str) and key.startswith(prefix) and isinstance(record, Mapping)
    }


def _record_cooling(
    record: Mapping[str, Any],
    *,
    now: datetime,
    default_cooldown: int,
) -> bool:
    delivered_at = parse_time(record.get("at"))
    if delivered_at is None:
        return True
    raw = record.get("repeat_after_seconds", default_cooldown)
    try:
        cooldown_seconds = max(0, int(raw))
    except (TypeError, ValueError):
        cooldown_seconds = default_cooldown
    return (now - delivered_at).total_seconds() < cooldown_seconds


def retained_delivered(
    delivered: Mapping[str, Any],
    *,
    use_cases: Mapping[str, Any],
    diagnostics: Mapping[str, Any],
    now: datetime,
    default_cooldown: int,
) -> JsonObject:
    """Keep records that can still suppress a later due check.

    Active fingerprints stay. Inactive ones stay until cooldown elapses,
    then drop so content-addressed keys cannot grow forever. Failed or
    unloaded collectors stay untouched.
    """

    retained: JsonObject = {}
    for key, record in delivered.items():
        if not isinstance(key, str):
            retained[key] = record
            continue
        use_case_id, separator, fingerprint = key.partition(":")
        entry = use_cases.get(use_case_id)
        if not separator or not isinstance(entry, Mapping) or use_case_id in diagnostics:
            retained[key] = record
            continue
        active = entry.get("active")
        if isinstance(active, list) and fingerprint in active:
            retained[key] = record
            continue
        if isinstance(record, Mapping) and _record_cooling(
            record, now=now, default_cooldown=default_cooldown
        ):
            retained[key] = record
    return retained
