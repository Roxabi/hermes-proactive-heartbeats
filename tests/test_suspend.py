from __future__ import annotations

import io
import json
import sys
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest import mock

from suspend import SUSPEND_KEY, command
from tests.isolation import IsolatedHomeTestCase
from tests.test_plugin import FakeCtx, load_register
from tick_facts import iso

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)


def write_heartbeat(home: Any, name: str, collectors: dict[str, Any]) -> None:
    root = home / "proactive-heartbeats"
    (root / "heartbeats").mkdir(parents=True, exist_ok=True)
    (root / "proactive-heartbeats.json").write_text("{}", encoding="utf-8")
    (root / "heartbeats" / f"{name}.json").write_text(
        json.dumps({"delivery": {"target": "local"}, "collectors": collectors}),
        encoding="utf-8",
    )


class SuspendCommandTests(IsolatedHomeTestCase):
    def setUp(self) -> None:
        super().setUp()
        write_heartbeat(
            self.hermes_home,
            "ops",
            {
                "sense": {"enabled": True},
                "host": {"enabled": False},
                "stale_prs": {"enabled": True},
            },
        )
        write_heartbeat(self.hermes_home, "care", {})
        self.ctx = FakeCtx()

    def invoke(self, raw: str, *, now: datetime = NOW) -> tuple[int, str]:
        return command(
            self.ctx,
            raw,
            now=now,
            home=self.hermes_home,
            config_dir=None,
        )

    def test_the_list_shows_enablements_and_an_empty_heartbeat(self) -> None:
        code, text = self.invoke("")

        self.assertEqual(code, 0)
        self.assertIn("ops\n  sense\n  stale_prs", text)
        self.assertIn("care\n  (none)", text)
        self.assertNotIn("host", text)
        self.assertEqual(self.ctx.state.sets, [])

    def test_a_unique_collector_can_omit_the_heartbeat(self) -> None:
        code, text = self.invoke("sense 3600")

        self.assertEqual(code, 0)
        self.assertTrue(text.startswith("suspended ops/sense until "))
        until = self.ctx.state.get(SUSPEND_KEY)["spans"]["ops"]["sense"]["until"]
        self.assertEqual(until, iso(NOW + timedelta(seconds=3600)))

    def test_the_pair_form_suspends_the_named_enablement(self) -> None:
        code, _text = self.invoke("ops stale_prs 60")

        self.assertEqual(code, 0)
        self.assertIn("stale_prs", self.ctx.state.get(SUSPEND_KEY)["spans"]["ops"])

    def test_a_heartbeat_name_does_not_suspend_the_pipeline(self) -> None:
        code, text = self.invoke("ops 60")

        self.assertEqual(code, 2)
        self.assertIn("heartbeat, not a collector", text)
        self.assertIn("ops\n  sense", text)
        self.assertEqual(self.ctx.state.sets, [])

    def test_an_ambiguous_name_is_rejected_with_the_list(self) -> None:
        write_heartbeat(self.hermes_home, "sense", {"host": {"enabled": True}})

        code, text = self.invoke("sense 60")

        self.assertEqual(code, 2)
        self.assertIn("matches both a heartbeat and a collector", text)
        self.assertEqual(self.ctx.state.sets, [])

    def test_zero_clears_and_a_missing_span_is_a_success(self) -> None:
        self.invoke("sense 3600")
        code, text = self.invoke("sense 0")

        self.assertEqual(code, 0)
        self.assertEqual(text, "cleared ops/sense")
        self.assertNotIn("sense", self.ctx.state.get(SUSPEND_KEY)["spans"].get("ops", {}))

        self.ctx.state.sets.clear()
        code, text = self.invoke("sense 0")
        self.assertEqual(code, 0)
        self.assertEqual(text, "cleared ops/sense")
        self.assertEqual(self.ctx.state.sets, [])

    def test_a_later_suspend_replaces_the_deadline(self) -> None:
        self.invoke("sense 60")
        self.invoke("sense 120")

        until = self.ctx.state.get(SUSPEND_KEY)["spans"]["ops"]["sense"]["until"]
        self.assertEqual(until, iso(NOW + timedelta(seconds=120)))

    def test_an_elapsed_span_is_absent_from_the_list(self) -> None:
        self.invoke("sense 60")

        _code, text = self.invoke("", now=NOW + timedelta(seconds=61))

        self.assertNotIn("until", text)
        self.assertIn("  sense\n", text)

    def test_duration_rejects(self) -> None:
        for raw in ("sense -1", "sense 2h", "sense 1.5", "sense 604801", "sense", "sense 060"):
            code, text = self.invoke(raw)
            self.assertEqual(code, 2, raw)
            self.assertEqual(self.ctx.state.sets, [], text)


class SuspendSurfaceTests(IsolatedHomeTestCase):
    def test_status_and_doctor_mention_a_span_without_calling_it_a_failure(self) -> None:
        write_heartbeat(self.hermes_home, "ops", {"sense": {"enabled": True}})
        register = load_register()
        if register is None:
            self.skipTest("plugin register() is not importable without a Hermes install")
        ctx = FakeCtx()
        register(ctx)
        ctx.state.set(
            SUSPEND_KEY,
            {
                "version": 1,
                "spans": {
                    "ops": {
                        "sense": {"until": iso(datetime.now(timezone.utc) + timedelta(hours=2))}
                    }
                },
            },
        )
        handler = ctx.commands[0]["handler_fn"]
        stdout = io.StringIO()
        with mock.patch.object(sys, "stdout", stdout):
            status = handler(type("A", (), {"proactive_heartbeats_command": "status"})())
            status_out = stdout.getvalue()
            stdout.seek(0)
            stdout.truncate()
            handler(type("A", (), {"proactive_heartbeats_command": "doctor"})())
            doctor_out = stdout.getvalue()

        self.assertEqual(status, 0)
        self.assertIn("suspended: sense until", status_out)
        self.assertIn("info: ops/sense until", doctor_out)
        self.assertNotIn("error: ops/sense", doctor_out)
        reply = ctx.slash_commands[0]["handler"]("")
        self.assertIn("ops", reply)
        self.assertIn("sense", reply)
