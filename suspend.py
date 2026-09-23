"""Suspend spans for one enablement.

A span lives in its own plugin-state key, not in ``heartbeat:{name}``. The tick
rewrites that key every run, and a ``STATE_VERSION`` bump discards it. The tick
only reads this key: writing it back would clobber a command that landed while
collectors were still running.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import _bootstrap  # noqa: F401
from config import NAME_RE, heartbeat_names, load_heartbeat
from tick_facts import iso, parse_time

SUSPEND_KEY = "suspends"
MAX_SECONDS = 7 * 24 * 60 * 60
_VERSION = 1


@dataclass(frozen=True)
class Enablement:
    heartbeat: str
    collector: str


@dataclass(frozen=True)
class Catalog:
    heartbeats: tuple[str, ...]
    enablements: tuple[Enablement, ...]


def load_catalog(home: Any, *, config_dir: str | None) -> Catalog:
    names = heartbeat_names(home, config_dir=config_dir)
    found: list[Enablement] = []
    for name in names:
        try:
            settings = load_heartbeat(home, name, config_dir=config_dir)
        except Exception:  # noqa: BLE001 — an unreadable file is not an enablement
            continue
        collectors = settings.get("collectors")
        if not isinstance(collectors, Mapping):
            continue
        for collector_id, section in collectors.items():
            if not isinstance(collector_id, str) or not NAME_RE.fullmatch(collector_id):
                continue
            if not isinstance(section, Mapping) or section.get("enabled") is not True:
                continue
            found.append(Enablement(name, collector_id))
    found.sort(key=lambda item: (item.heartbeat, item.collector))
    return Catalog(heartbeats=names, enablements=tuple(found))


def suspended_collectors(raw: Any, heartbeat: str, now: datetime) -> frozenset[str]:
    """Collector ids whose span has not elapsed. A corrupt store suspends nothing."""
    spans, _warning = _spans(raw)
    active: set[str] = set()
    heartbeat_spans = spans.get(heartbeat)
    if not isinstance(heartbeat_spans, Mapping):
        return frozenset()
    for collector_id, record in heartbeat_spans.items():
        until = _until(record)
        if until is not None and until > now:
            active.add(str(collector_id))
    return frozenset(active)


def store_warning(raw: Any) -> str | None:
    _spans_value, warning = _spans(raw)
    del _spans_value
    return warning


def command(
    ctx: Any,
    raw: str,
    *,
    now: datetime,
    home: Any,
    config_dir: str | None,
) -> tuple[int, str]:
    """Apply one Discord or CLI invocation. ``0`` is success, ``2`` is a rejection."""
    try:
        catalog = load_catalog(home, config_dir=config_dir)
    except Exception as exc:  # noqa: BLE001 — the command must answer, not traceback
        return 2, f"suspend failed: {exc}"

    stored = ctx.state.get(SUSPEND_KEY, default=None)
    spans, warning = _spans(stored)
    outcome = _dispatch(raw, catalog, spans, now)
    if outcome.store is not None and outcome.store != _document(spans):
        ctx.state.set(SUSPEND_KEY, outcome.store)
    text = outcome.text
    if warning and outcome.code == 0:
        text = f"{warning}\n{text}"
    return outcome.code, text


@dataclass(frozen=True)
class _Outcome:
    code: int
    text: str
    store: dict[str, Any] | None = None


def _dispatch(raw: str, catalog: Catalog, spans: dict[str, Any], now: datetime) -> _Outcome:
    tokens = raw.split()
    listing = _render(catalog, spans, now)
    if not tokens:
        return _Outcome(0, listing)
    if len(tokens) == 1 or len(tokens) > 3:
        return _Outcome(2, f"usage: suspend [heartbeat] <collector> <seconds>\n\n{listing}")

    seconds, seconds_error = _parse_seconds(tokens[-1])
    if seconds_error is not None or seconds is None:
        return _Outcome(2, f"{seconds_error}\n\n{listing}")
    names = tokens[:-1]
    if len(names) == 1:
        resolved = _resolve_one(names[0], catalog)
    elif len(names) == 2:
        resolved = _resolve_pair(names[0], names[1], catalog)
    else:
        return _Outcome(2, f"usage: suspend [heartbeat] <collector> <seconds>\n\n{listing}")
    if isinstance(resolved, str):
        return _Outcome(2, f"{resolved}\n\n{listing}")

    updated = _copy_spans(spans)
    heartbeat_spans = dict(updated.get(resolved.heartbeat) or {})
    if seconds == 0:
        heartbeat_spans.pop(resolved.collector, None)
        if heartbeat_spans:
            updated[resolved.heartbeat] = heartbeat_spans
        else:
            updated.pop(resolved.heartbeat, None)
        if _document(updated) == _document(spans):
            return _Outcome(0, f"cleared {resolved.heartbeat}/{resolved.collector}")
        return _Outcome(
            0,
            f"cleared {resolved.heartbeat}/{resolved.collector}",
            _document(updated),
        )

    until = now + timedelta(seconds=seconds)
    heartbeat_spans[resolved.collector] = {"until": iso(until)}
    updated[resolved.heartbeat] = heartbeat_spans
    return _Outcome(
        0,
        f"suspended {resolved.heartbeat}/{resolved.collector} until {_format_deadline(until)}",
        _document(updated),
    )


def active_lines(raw: Any, heartbeat: str, now: datetime) -> tuple[str, ...]:
    spans, _warning = _spans(raw)
    heartbeat_spans = spans.get(heartbeat)
    if not isinstance(heartbeat_spans, Mapping):
        return ()
    lines: list[str] = []
    for collector_id in sorted(heartbeat_spans):
        until = _until(heartbeat_spans[collector_id])
        if until is not None and until > now:
            lines.append(f"{collector_id} until {_format_deadline(until)}")
    return tuple(lines)


def _resolve_one(name: str, catalog: Catalog) -> Enablement | str:
    matches = [item for item in catalog.enablements if item.collector == name]
    is_heartbeat = name in catalog.heartbeats
    if is_heartbeat and matches:
        return f"{name} matches both a heartbeat and a collector — name the enablement"
    if is_heartbeat:
        return (
            f"{name} is a heartbeat, not a collector — a Suspend does not cover a whole heartbeat"
        )
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        return f"{name} is enabled in more than one heartbeat — name the enablement"
    return f"{name} is not an enablement"


def _resolve_pair(heartbeat: str, collector: str, catalog: Catalog) -> Enablement | str:
    for item in catalog.enablements:
        if item.heartbeat == heartbeat and item.collector == collector:
            return item
    return f"{heartbeat}/{collector} is not an enablement"


def _parse_seconds(token: str) -> tuple[int | None, str | None]:
    if not token.isascii() or not token.isdigit() or (len(token) > 1 and token.startswith("0")):
        return None, f"{token} is not a whole number of seconds"
    value = int(token)
    if value > MAX_SECONDS:
        return None, f"{value} is above 7 days ({MAX_SECONDS} seconds)"
    return value, None


def _render(catalog: Catalog, spans: Mapping[str, Any], now: datetime) -> str:
    if not catalog.heartbeats:
        return "(no heartbeats)"
    lines: list[str] = []
    for heartbeat in catalog.heartbeats:
        lines.append(heartbeat)
        rows = [item for item in catalog.enablements if item.heartbeat == heartbeat]
        if not rows:
            lines.append("  (none)")
            continue
        heartbeat_spans = spans.get(heartbeat)
        if not isinstance(heartbeat_spans, Mapping):
            heartbeat_spans = {}
        for item in rows:
            until = _until(heartbeat_spans.get(item.collector))
            if until is not None and until > now:
                lines.append(f"  {item.collector}  until {_format_deadline(until)}")
            else:
                lines.append(f"  {item.collector}")
    return "\n".join(lines)


def _spans(raw: Any) -> tuple[dict[str, Any], str | None]:
    if raw is None:
        return {}, None
    if not isinstance(raw, Mapping) or raw.get("version") != _VERSION:
        return {}, "suspend store ignored (unreadable)"
    spans = raw.get("spans")
    if not isinstance(spans, dict):
        return {}, "suspend store ignored (unreadable)"
    return spans, None


def _until(record: Any) -> datetime | None:
    if not isinstance(record, Mapping):
        return None
    parsed = parse_time(record.get("until"))
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _copy_spans(spans: Mapping[str, Any]) -> dict[str, Any]:
    copied: dict[str, Any] = {}
    for heartbeat, collectors in spans.items():
        if isinstance(collectors, Mapping):
            copied[str(heartbeat)] = dict(collectors)
    return copied


def _document(spans: Mapping[str, Any]) -> dict[str, Any]:
    return {"version": _VERSION, "spans": dict(spans)}


def _format_deadline(until: datetime) -> str:
    return until.astimezone().strftime("%Y-%m-%d %H:%M")
