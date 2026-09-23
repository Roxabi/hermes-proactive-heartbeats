# A Suspend is wall-clock, not a skipped tick

An operator can stop one enablement from being invoked for a number of seconds without editing heartbeat JSON or pausing the cron job. The span is an absolute deadline. A span shorter than the wait to the next tick can therefore change nothing, and a future reader will want to "fix" that into "skip at least one tick" or `enabled: false`. Both were rejected: the first lies about the seconds, the second dirties the live git checkout and does not expire.
