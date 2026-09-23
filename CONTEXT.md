# Proactive heartbeats

Named pipelines that observe through collectors and wake an agent only when something is worth an interruption.

## Language

**Heartbeat**:
A named proactive pipeline. It owns one schedule and the set of collectors it has enabled.
_Avoid_: cron job, loop

**Collector**:
An observer of one source, identified by its module stem. Several heartbeats may enable the same collector with different configuration.
_Avoid_: probe, sensor, check

**Enablement**:
One heartbeat's enabled use of one collector. A Suspend applies to an enablement, not to the collector module.
_Avoid_: pair, couple, binding

**Suspend**:
A wall-clock span of a whole number of seconds, from zero up to seven days, during which one enablement is not invoked. A span of zero leaves no active Suspend. A later Suspend of the same enablement replaces it.
_Avoid_: Hold, pause, timeout, disable, mute, quiet hours, cooldown, collector health, watchdog
