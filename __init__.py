"""Proactive heartbeats Hermes plugin — CLI and slash registration only."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent
_ROOT_S = str(_ROOT)
if _ROOT_S not in sys.path:
    sys.path.insert(0, _ROOT_S)


def register(ctx: Any) -> None:
    """Register surfaces. No hooks, tools, or background work."""
    from . import cli

    def _handler(args: Any) -> int:
        return cli.handle(ctx, args)

    def _suspend(raw: str) -> str:
        return cli.suspend_reply(ctx, raw)

    ctx.register_cli_command(
        name="proactive-heartbeats",
        help="Run and manage named proactive heartbeat tick pipelines",
        setup_fn=cli.configure_parser,
        handler_fn=_handler,
        description=(
            "Operator CLI for the proactive heartbeats plugin: tick one named "
            "heartbeat, inspect status, run doctor checks, suspend an enablement, "
            "and reconcile one Hermes cron job per heartbeat."
        ),
    )
    ctx.register_command(
        "suspend",
        handler=_suspend,
        description="Suspend one collector for N seconds, or list enablements",
        args_hint="[heartbeat] <collector> <seconds>",
    )
