# Contributing

Thanks for helping improve `hermes-proactive-heartbeats`.

## Before you start

- Use Python 3.10 or newer.
- Use Hermes 0.21.3 or newer for integration checks.
- Open an issue before making a large behavioral or configuration change.
- Never commit credentials, personal paths, hostnames, repository names, channel IDs, or operator data.

## Repository boundary

This repository contains the generic plugin runtime only:

- collection orchestration;
- deterministic decisions, semantic TypeSafe batching, and deduplication;
- named heartbeat configuration loading;
- Hermes Cron reconciliation;
- the collector SDK.

Collector implementations and operator configuration belong under the active `$HERMES_HOME/proactive-heartbeats/` directory. They must not be added to this repository, including as examples copied from a live environment.

Hermes Cron owns scheduling, the `wakeAgent` script gate, and delivery. `register(ctx)` must remain registration-only and must not create files, start loops, or reconcile jobs.

## Local setup

Create a virtual environment and install the pinned development tool:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install ruff==0.15.10
```

Run the same quality gates as CI:

```bash
ruff format --check .
ruff check .
python3 -m compileall -q .
python3 -m unittest discover -s tests -v
```

If Hermes is installed, also validate plugin discovery and registration:

```bash
hermes plugins doctor . --ci
```

## Tests

Tests use the standard-library `unittest` runner. Add or update tests only for observable behavior: configuration boundaries, state transitions, wake-gate output, Cron reconciliation, or real error handling. Keep tests deterministic and isolated from the user's actual `HERMES_HOME`.

A quiet tick must remain the exact standalone line:

```json
{"wakeAgent": false}
```

## Pull requests

1. Fork the repository and create a focused branch.
2. Keep the change generic and remove obsolete paths rather than adding compatibility shims.
3. Update `README.md` or `after-install.md` when installation or operator behavior changes, and add a `CHANGELOG.md` entry when behavior visible to an operator changes.
4. Run all quality gates above.
5. Explain the user-visible behavior and verification in the pull request.

## Releases

Trunk-manual, as in the fleet's release convention: merging to `main` cuts nothing; a release exists because someone pushed an annotated tag. There is no publishing step — Hermes installs a commit SHA.

1. Land every change through a PR, merged with a merge commit.
2. A PR that changes operator-visible behavior bumps `version:` in `plugin.yaml` **and** adds the matching `## X.Y.Z — YYYY-MM-DD` section at the top of `CHANGELOG.md`. CI (`scripts/release.py check`) fails unless the plugin version is the newest changelog heading: a section without its bump, or a bump without its section, cannot land.
3. After the merge, tag the merge commit and push the tag:

   ```bash
   git tag -a proactive-heartbeats/vX.Y.Z -m "proactive-heartbeats X.Y.Z" <merge-sha>
   git push origin proactive-heartbeats/vX.Y.Z
   ```

   CI runs the suite on the tagged commit, then the `release` job checks that the tag is annotated, on `main`, and names the `plugin.yaml` version, and publishes the GitHub release: the install SHA, the `hermes plugins install --ref` command, and the changelog section. It is marked Latest only when it is the highest version.
4. The SHA lives in the release notes, never in the tree — `hermes plugins install --ref` accepts only a 40-character SHA, and a SHA committed to `README.md` or `CHANGELOG.md` pins a release the tree has already moved past.

`pyproject.toml` is Ruff config only — it must not carry a product version.

Tags 0.6.0 to 0.9.0 were cut after the fact, on the merges that wrote their changelog sections: `plugin.yaml` still read `0.5.0` at those commits, which is what the consistency check now refuses.

By contributing, you agree that your contribution is licensed under the repository's MIT license.
