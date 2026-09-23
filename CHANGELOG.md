# Changelog

All notable changes to this plugin are documented here. Versions follow
[semantic versioning](https://semver.org/spec/v2.0.0.html); releases are tagged
`proactive-heartbeats/vX.Y.Z`.

## 0.10.0 — 2026-09-23

### Added

- `/suspend` in a Hermes session, and `hermes proactive-heartbeats suspend`, suspend one enablement for a whole number of seconds (0 to 7 days) or list them. The span is wall-clock, stored outside heartbeat state, and a suspended collector is not invoked. Its stamps stay, so the return does not burst, and the watchdog streak is cleared rather than treated as blindness. `status` and `doctor` mention an active span without failing for it.

## 0.9.0 — 2026-09-23

### Changed

- A packed wake asks the writer for **one coherent message**: work out how the
  observations relate (what explains what, what to do now, what to come back to) and
  write a single line of thought, instead of one sentence per observation side by side.
  Each observation's instruction now reaches the writer as a note tied to its
  fingerprint, and is read as what that observation must convey — not a sentence to
  paste. Measured: "Drink a glass of water and stand up. You should get back to your
  priorities: Metalyde, Ether." became one thought from the 94-minute stretch, through
  the pause, to the priorities to resume. The default cron prompt says the same.

## 0.8.0 — 2026-09-20

### Changed

- Delivery records carry `woke`, and collectors ask `was_announced(record)` instead of
  interpreting the action name. A collector cannot know every non-waking stamp the
  engine might learn to write: `quiet` (0.5.0) landed after the bundled collectors were
  written, and one testing `action not in {"baseline", "silent"}` read a signal the
  quiet window had swallowed as already said — a CVE that stops being reported at all.
  Unrecognised records now read as *not* announced, so an observation is repeated
  rather than dropped.

## 0.7.0 — 2026-09-20

### Added

- `doctor` and `status` read `state.health`. A collector blind for at least
  `collector_watchdog.after_ticks` ticks fails `doctor`; a shorter streak warns;
  with the watchdog disabled one blind tick is already fatal, because nothing
  else would report it. Measured on the live install: `sense` had not observed
  for 37 consecutive ticks while `doctor` printed `OK`, since every check it ran
  was about configuration rather than about whether anything was still watching.

## 0.6.0 — 2026-09-20

### Added

- `Signal(awake_evidence=True)` for observations emitted on measured presence.
  Quiet hours let them through whatever `min_priority` says. The window is a
  proxy for "nobody is there to be woken", and a collector that read presence
  knows the answer the clock was guessing: holding those signals back silenced
  the care nudges that exist *for* the odd hours — "02:00 and still typing" is
  the moment that advice is worth anything, not noise to suppress.

## 0.5.0 — 2026-09-19

### Added

- `Signal(perishable=True)` for observations whose value expires with the
  moment. Quiet hours **drop** a perishable signal — stamped `quiet` so its
  cooldown runs, and listed in `state.quiet_dropped` — instead of deferring it.
  Without it, a "it is late" nudge held at 02:30 was read out at 07:30, which is
  both wrong and the opposite of what quiet hours are for.

## 0.4.0 — 2026-09-19

### Changed

- **A failed collector no longer silences the whole heartbeat.** A collector that
  raises, returns the wrong type, or emits an invalid signal is isolated: it keeps
  its previous state and delivery records while every healthy collector still
  resolves, judges, and wakes. Before, one unreachable SSH probe could hide a disk
  alert and an open CVE for as long as it stayed down.
- `tick` now exits `0` when a collector failed but the tick still produced its
  stdout gate, and reports the failing collectors on stderr. Hermes Cron reads
  stdout; a partial tick must be allowed to speak instead of being treated as a
  failed job.

### Added

- **Collector watchdog.** `state.health[{collector}]` counts consecutive ticks on
  which a collector could not observe — a raised exception *or* an `error`
  diagnostic, which is how a probe reports an unreachable host. After
  `collector_watchdog.after_ticks` (default 3) the `_watchdog` pseudo-collector
  emits `collector-down:{collector}` with the `collector_down` action, so a
  heartbeat that has stopped watching says so instead of looking calm.
  `collector_watchdog.repeat_after_seconds` gives the alarm its own cooldown;
  `after_ticks: 0` disables it.
- **Quiet hours.** `quiet_hours` = `{start, end, min_priority}` in local time,
  settable at the root, under `defaults`, or per heartbeat. Inside the window,
  waking observations below the floor are **deferred**, not suppressed: they are
  never stamped in `delivered`, never judged (so they cost no model call), listed
  in `state.quiet_deferred`, and due again as soon as the window ends.

### Removed

- The fail-closed `pending` queue and its state key, along with
  `coerce_pending`, `sorted_pending`, `pending_reusable`, and `action_from_json`.
  Per-collector isolation removed its only writer: there is nothing left to retry,
  because a failed collector emits no signal at all.

### Migration

- `STATE_VERSION` is now `3`. A version-2 record is discarded, never migrated: the
  next tick baselines again, so already-known conditions stay quiet for one
  cooldown instead of arriving as a burst.

## 0.3.1 — 2026-09-19

### Fixed

- Cooldown now survives a fingerprint leaving `active`. `is_due` reads
  `delivered` even when the fingerprint is new to this tick's active set.
  `retained_delivered` keeps inactive records until their cooldown elapses,
  then drops them so content-addressed keys still cannot grow forever.
  0.2.1 treated a reappearance as a brand-new event; a care signal that
  flickered off for one tick then came back would wake immediately.

### Tests

- A fingerprint that is delivered, vanishes, and returns inside the default
  cooldown stays silent. The same return after the cooldown is due again.
  Inactive records still inside cooldown are kept on failed, unloaded, and
  colon-keyed collectors; expired vanished records are still pruned.


## 0.3.0 — 2026-09-18

### Changed

- A tick no longer picks a single winner. Every due signal whose resolved action
  wakes the agent is packed into one `heartbeat_candidate`. `inputs` is always a
  list of observations `{collector, fingerprint, facts, decision}`. Priority only
  orders that list. Hermes Cron still sees one stdout object and one agent wake.
- Delivered observations are stamped immediately, so cooldown applies to each of
  them. Losers are no longer queued in `pending`. `pending` remains the
  fail-closed retry queue when a collector errors.
- The managed cron prompt tells the woken agent to cover every input and not
  re-open whether to speak.

### Migration

- Collectors do not change. Operators who parsed `inputs` as a single facts
  object must read a list. `setup` rewrites the cron prompt.


## 0.2.1 — 2026-09-18

### Fixed

- `delivered` in the heartbeat state only ever grew. Measured on a live install, a tick still carried records for fingerprints the collector had stopped emitting hours earlier, so every collector minting content-addressed or date-anchored fingerprints — a daily digest keyed `…:digest:{date}`, a delta keyed by a hash of the new items — added at least one permanent record per day. The engine now persists a delivery record only while its fingerprint is in that collector's active set for the tick being persisted.
- This is a behavioral no-op by construction: `_is_due` returns `True` for a fingerprint absent from its collector's previous active set **before** it reads `delivered`, so a record for an inactive fingerprint can suppress nothing, at any age and for any `repeat_after_seconds`. Dropping it changes no decision.
- Retention is fail-closed. A collector that failed this tick has no fresh active set, so every one of its records is kept untouched, on the fail-closed return as well as the normal one. A key that names no collector loaded by this heartbeat — a collector temporarily disabled in the heartbeat JSON, for instance — is kept too: pruning never shreds state it cannot judge.
- Collectors that need memory outliving a fingerprint must keep it in their own `Snapshot.state`; `context.delivered` now covers the fingerprints active on the previous tick, not the whole history.

### Tests

- Five cases pin the observable rule: a vanished fingerprint's record is gone from the next persisted state, a still-active record survives and keeps suppressing inside its cooldown, a failed collector's records all survive, an unloaded collector's records survive, and a fingerprint containing `:` is matched to its own collector rather than split at the wrong separator.

## 0.2.0 — 2026-09-18

### Added

- `TickContext.delivered`: the delivery records the engine persisted for **the collector being invoked**, keyed by its own fingerprints with no `{collector}:` prefix, each holding `{"at": "<iso8601>", "action": "<action name>"}`. A collector could see what it observed, never what the engine actually announced: a signal stamped `silent` because a higher-priority signal won the tick was indistinguishable from one that really woke the agent. Digest and delta collectors need that difference — "list everything once a day, otherwise only what is new" is not implementable without it. Action `baseline` means the fingerprint was only baselined and `silent` that it was due but did not wake the agent, so both mean "not announced to the user"; any other name is the action that woke it. A collector never sees a sibling collector's fingerprints, and the first tick sees `{}`.

### Migration

- No collector change is required. Collectors that construct a `TickContext` themselves — tests, harnesses — get the new field by default (an empty mapping), and `collect()` keeps its signature. The persisted state schema, `STATE_VERSION`, the gates, and the stdout contract are untouched.

## 0.1.2 — 2026-09-18

### Documentation

- First-tick semantics were described as "recorded without resolution", which reads as "due on the next tick". The actual behavior — verified on a live install — is that a `baseline` signal is stamped `delivered: baseline`, which **starts the cooldown clock**: it can only wake one cooldown later (4 h by default). A fingerprint that first appears on any later tick has no delivery record and is due immediately.
- `Cooldown` now states that it also applies to a baseline stamp, and that `repeat_after_seconds: 0` means "due on every tick while active".
- Collector guidance is explicit: anything that must not sit unreported for a cooldown window belongs in `initial_observation="eligible"`.

### Tests

- Added coverage for a signal already active at the baseline tick: silent at the stamp, still silent one second before the cooldown elapses, due exactly when it does. The existing cooldown tests all introduced their signal after the baseline tick, so this timing path was unpinned.

No runtime change: the engine behaves exactly as in 0.1.1.

## 0.1.1 — 2026-09-18

### Fixed

- Install docs: `hermes plugins install --ref` rejects anything that is not a full 40-character commit SHA, so the tag-based command shipped in 0.1.0 could not run. The install section now resolves a release tag to its commit — and warns that an unpeeled `git ls-remote` returns the annotated tag object, not a commit.
- Concrete SHAs now live in the release notes only. Hardcoding one in the tree pins a release the tree has already moved past.

No runtime change: the 0.1.0 collector SDK, engine behavior, and stdout contract are untouched.

## 0.1.0 — 2026-09-18

First tagged release. Nothing before this tag was published, so the contract below is the baseline rather than a delta.

### Install

`hermes plugins install --ref` takes only a 40-character commit SHA, so pin this release by its tagged commit:

```bash
hermes plugins install Roxabi/hermes-proactive-heartbeats --ref a9a5d883135df15ee609c98aaffe4ab37feb0372
```

### Decide before the model call

- A tick collects facts in plain Python, gates them deterministically, and prints exactly `{"wakeAgent": false}` when nothing is due — no agent wake, no tokens.
- When something is due, stdout is one `heartbeat_candidate` carrying the compact facts and the already-selected action, so the woken agent writes the message instead of re-deciding whether to speak.

### Collector SDK

- `Signal.decision` takes exactly one policy: a deterministic `ActionSpec` rule resolved in-process, or a semantic `JudgmentSpec` resolved by an optional TypeSafe batch. There is no dual form.
- `ActionSpec` requires an explicit `wake_agent` boolean; `SILENT` is the exported canonical non-waking action.
- `Signal.initial_observation` chooses the first-tick policy per signal: `"baseline"` (default) records a newly seen fingerprint without resolving it, `"eligible"` resolves it immediately so a critical condition can wake on tick 1.
- Candidate selection is deterministic: highest `priority`, then collector id, then fingerprint. A semantic decision never outranks a rule by virtue of being semantic.
- Decision provenance is stamped on the payload as `rule`, `typesafe`, or `fallback`.

### Named heartbeats

- One plugin serves many heartbeats. Each `heartbeats/{name}.json` owns one Hermes cron job, one generated shim, and one state key `heartbeat:{name}`.
- Collector implementations and operator policy live under `$HERMES_HOME/proactive-heartbeats/`, never in this repository.
- `setup` is idempotent: it reconciles one cron job per heartbeat file and removes the job when the file is deleted.
- Heartbeat state is versioned (`STATE_VERSION = 2`); a record written by another version is discarded rather than migrated.

### Operations

- `setup`, `tick --name`, `status`, and `doctor` commands; collector load or execution failures exit non-zero instead of reporting a quiet success.
- Pending decisions are reused while a signal stays active with unchanged facts, so an unresolved judgment costs at most one model call.
- TypeSafe is optional: without `TYPESAFE_API_KEY`, rules stay deterministic and semantic decisions use their configured fallback.
