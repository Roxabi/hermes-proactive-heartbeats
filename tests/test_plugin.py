from __future__ import annotations

import importlib.util
import io
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest import mock

from tests.isolation import IsolatedHomeTestCase

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "hermes_proactive_heartbeats"


class FakeState:
    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self.gets: list[tuple[str, Any]] = []
        self.sets: list[tuple[str, Any]] = []

    def get(self, key: str, default: Any = None) -> Any:
        self.gets.append((key, default))
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.sets.append((key, value))
        self._data[key] = value


class FakeCtx:
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}
        self.state = FakeState()
        self.commands: list[dict[str, Any]] = []
        self.slash_commands: list[dict[str, Any]] = []

    def get_config(self, key: str, default: Any = None) -> Any:
        return self.config.get(key, default)

    def register_cli_command(self, **kwargs: Any) -> None:
        self.commands.append(kwargs)

    def register_command(self, name: str, handler: Any, **kwargs: Any) -> None:
        self.slash_commands.append({"name": name, "handler": handler, **kwargs})


def _load_module(module_name: str, path: Path, package: ModuleType | None = None) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        module_name,
        path,
        submodule_search_locations=[str(path.parent)] if path.name == "__init__.py" else None,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    if package is not None:
        setattr(package, module_name.rsplit(".", 1)[-1], module)
    spec.loader.exec_module(module)
    return module


def load_plugin_package() -> ModuleType | None:
    existing = sys.modules.get(PACKAGE_NAME)
    if existing is not None and hasattr(existing, "register"):
        return existing

    init_path = ROOT / "__init__.py"
    if not init_path.exists():
        return None

    package = ModuleType(PACKAGE_NAME)
    package.__file__ = str(init_path)
    package.__path__ = [str(ROOT)]  # type: ignore[attr-defined]
    package.__package__ = PACKAGE_NAME
    sys.modules[PACKAGE_NAME] = package

    for path in sorted(ROOT.glob("*.py")):
        if path.name == "__init__.py":
            continue
        module_name = f"{PACKAGE_NAME}.{path.stem}"
        module = _load_module(module_name, path, package)
        sys.modules.setdefault(path.stem, module)

    use_cases_dir = ROOT / "use_cases"
    if use_cases_dir.is_dir():
        use_cases_pkg = ModuleType(f"{PACKAGE_NAME}.use_cases")
        use_cases_pkg.__path__ = [str(use_cases_dir)]  # type: ignore[attr-defined]
        use_cases_pkg.__package__ = f"{PACKAGE_NAME}.use_cases"
        sys.modules[f"{PACKAGE_NAME}.use_cases"] = use_cases_pkg
        sys.modules.setdefault("use_cases", use_cases_pkg)
        package.use_cases = use_cases_pkg
        for path in sorted(use_cases_dir.glob("*.py")):
            if path.name == "__init__.py":
                module = _load_module(f"{PACKAGE_NAME}.use_cases", path)
                sys.modules[f"{PACKAGE_NAME}.use_cases"] = module
                sys.modules.setdefault("use_cases", module)
                package.use_cases = module
                continue
            module_name = f"{PACKAGE_NAME}.use_cases.{path.stem}"
            module = _load_module(module_name, path, use_cases_pkg)
            sys.modules.setdefault(f"use_cases.{path.stem}", module)

    return _load_module(PACKAGE_NAME, init_path)


def load_register() -> Callable[[Any], Any] | None:
    try:
        package = load_plugin_package()
    except Exception:
        return None
    if package is None:
        return None
    register = getattr(package, "register", None)
    return register if callable(register) else None


class PluginRegistrationTests(IsolatedHomeTestCase):
    def test_register_only_registers_surfaces_without_setup_side_effects(self) -> None:
        register = load_register()
        if register is None:
            self.skipTest("plugin register() is not importable without a Hermes install")

        ctx = FakeCtx()
        with (
            mock.patch("subprocess.run") as subprocess_run,
            mock.patch("subprocess.Popen") as subprocess_popen,
            mock.patch("pathlib.Path.mkdir") as path_mkdir,
            mock.patch("builtins.open", side_effect=AssertionError("open during register")),
        ):
            register(ctx)

        self.assertEqual([command["name"] for command in ctx.commands], ["proactive-heartbeats"])
        self.assertEqual([command["name"] for command in ctx.slash_commands], ["suspend"])
        self.assertEqual(ctx.slash_commands[0]["args_hint"], "[heartbeat] <collector> <seconds>")
        self.assertTrue(callable(ctx.slash_commands[0]["handler"]))
        self.assertEqual(ctx.state.sets, [])
        self.assertEqual(ctx.state.gets, [])
        subprocess_run.assert_not_called()
        subprocess_popen.assert_not_called()
        path_mkdir.assert_not_called()


class PluginTickHandlerTests(IsolatedHomeTestCase):
    def test_tick_handler_loads_and_persists_engine_state_then_prints_render(self) -> None:
        register = load_register()
        if register is None:
            self.skipTest("plugin register() is not importable without a Hermes install")

        previous_state = {
            "version": 3,
            "use_cases": {},
            "delivered": {},
        }
        next_state = {
            "version": 3,
            "use_cases": {"host": {"state": {}, "active": []}},
            "delivered": {},
        }
        rendered = '{"wakeAgent": false}'
        tick_result = SimpleNamespace(state=next_state, diagnostics={}, render=lambda: rendered)
        engine = SimpleNamespace(tick=mock.Mock(return_value=tick_result))

        root = self.hermes_home / "proactive-heartbeats"
        (root / "heartbeats").mkdir(parents=True)
        (root / "proactive-heartbeats.json").write_text(
            '{"heartbeats": ["care"]}', encoding="utf-8"
        )
        (root / "heartbeats" / "care.json").write_text(
            '{"delivery": {"target": "origin"}, "collectors": {}}', encoding="utf-8"
        )

        ctx = FakeCtx()
        ctx.state.set("heartbeat:care", previous_state)
        ctx.state.sets.clear()
        ctx.state.gets.clear()

        register(ctx)
        handler = ctx.commands[0]["handler_fn"]
        args = SimpleNamespace(proactive_heartbeats_command="tick", name="care")
        stdout = io.StringIO()
        cli_module = sys.modules[f"{PACKAGE_NAME}.cli"]
        build_registry = mock.Mock(return_value=[])
        engine_cls = mock.Mock(return_value=engine)
        typesafe_cls = mock.Mock(return_value=object())

        with (
            mock.patch.object(
                cli_module,
                "_import_tick_deps",
                return_value=(engine_cls, build_registry, typesafe_cls),
            ),
            mock.patch.object(sys, "stdout", stdout),
        ):
            exit_code = handler(args)

        self.assertEqual(exit_code, 0)
        engine_cls.assert_called_once()
        engine.tick.assert_called_once()
        self.assertEqual(engine.tick.call_args.kwargs["previous_state"], previous_state)
        self.assertIn(("heartbeat:care", None), ctx.state.gets)
        self.assertEqual(ctx.state.sets, [("heartbeat:care", next_state)])
        self.assertEqual(stdout.getvalue(), rendered + "\n")

    def test_a_collector_failure_reports_on_stderr_but_still_delivers_the_wake(self) -> None:
        register = load_register()
        if register is None:
            self.skipTest("plugin register() is not importable without a Hermes install")

        rendered = '{"heartbeat_candidate":{"collector":"host"}}'
        tick_result = SimpleNamespace(
            state={"version": 3, "use_cases": {}, "delivered": {}},
            diagnostics={"probe": {"error": "RuntimeError", "message": "ssh failed"}},
            render=lambda: rendered,
        )
        engine = SimpleNamespace(tick=mock.Mock(return_value=tick_result))

        root = self.hermes_home / "proactive-heartbeats"
        (root / "heartbeats").mkdir(parents=True)
        (root / "proactive-heartbeats.json").write_text("{}", encoding="utf-8")
        (root / "heartbeats" / "care.json").write_text(
            '{"delivery": {"target": "local"}, "collectors": {}}', encoding="utf-8"
        )

        ctx = FakeCtx()
        register(ctx)
        handler = ctx.commands[0]["handler_fn"]
        args = SimpleNamespace(proactive_heartbeats_command="tick", name="care")
        stdout, stderr = io.StringIO(), io.StringIO()
        cli_module = sys.modules[f"{PACKAGE_NAME}.cli"]
        tick_deps = (mock.Mock(return_value=engine), mock.Mock(return_value=[]), mock.Mock())

        with (
            mock.patch.object(cli_module, "_import_tick_deps", return_value=tick_deps),
            mock.patch.object(sys, "stdout", stdout),
            mock.patch.object(sys, "stderr", stderr),
        ):
            exit_code = handler(args)

        # Cron reads stdout for the gate: a partial tick must still be allowed to speak.
        self.assertEqual(exit_code, 0)
        self.assertEqual(stdout.getvalue(), rendered + "\n")
        self.assertIn("probe", stderr.getvalue())


class DoctorCollectorHealthTests(IsolatedHomeTestCase):
    """A heartbeat can be wired, scheduled, and seeing nothing at all.

    Measured on the live install: `sense` was blind for 37 consecutive ticks while
    `doctor` printed OK, because every check it ran was about configuration.
    """

    def _heartbeat(self, watchdog: str = "") -> None:
        root = self.hermes_home / "proactive-heartbeats"
        (root / "heartbeats").mkdir(parents=True)
        (root / "proactive-heartbeats.json").write_text(
            f'{{"collector_watchdog": {{"after_ticks": {watchdog or 3}}}}}', encoding="utf-8"
        )
        (root / "heartbeats" / "care.json").write_text(
            '{"delivery": {"target": "local"}, "collectors": {}}', encoding="utf-8"
        )

    def _run(self, command: str, health: dict[str, Any]) -> tuple[int, str]:
        register = load_register()
        if register is None:
            self.skipTest("plugin register() is not importable without a Hermes install")
        ctx = FakeCtx()
        ctx.state.set(
            "heartbeat:care",
            {"version": 3, "use_cases": {}, "delivered": {}, "health": health},
        )
        register(ctx)
        handler = ctx.commands[0]["handler_fn"]
        args = SimpleNamespace(proactive_heartbeats_command=command)
        stdout = io.StringIO()
        with mock.patch.object(sys, "stdout", stdout):
            exit_code = handler(args)
        return exit_code, stdout.getvalue()

    def _sense_line(self, out: str) -> str:
        """The one report line about `sense`, with its `error:`/`warn:` classification.

        Asserting the classification, not the exit code: an isolated home has no `hermes`
        on PATH and no cron shim, so `doctor` is already failing for reasons this test
        does not own.
        """
        lines = [line.strip() for line in out.splitlines() if "has not observed" in line]
        self.assertEqual(len(lines), 1, out)
        return lines[0]

    def test_doctor_reports_a_collector_that_has_stopped_observing_as_an_error(self) -> None:
        self._heartbeat()

        _, out = self._run(
            "doctor",
            {"sense": {"streak": 37, "since": "2026-09-19T21:58:07Z", "error": "unavailable"}},
        )

        line = self._sense_line(out)
        self.assertTrue(line.startswith("error:"), line)
        self.assertIn("'sense'", line)
        self.assertIn("37 tick(s)", line)
        self.assertIn("unavailable", line)

    def test_a_streak_below_the_watchdog_threshold_is_only_a_warning(self) -> None:
        """One missed tick is an ssh blip; the threshold is what the watchdog itself uses,
        so `doctor` and the Discord alert never disagree about what counts as down."""

        self._heartbeat()

        _, out = self._run("doctor", {"sense": {"streak": 1, "since": "x", "error": "timeout"}})

        line = self._sense_line(out)
        self.assertTrue(line.startswith("warn:"), line)
        self.assertIn("1 tick(s)", line)

    def test_one_blind_tick_is_fatal_when_the_watchdog_is_disabled(self) -> None:
        """Nothing else would ever report it."""

        self._heartbeat(watchdog="0")

        _, out = self._run("doctor", {"sense": {"streak": 1, "since": "x", "error": "timeout"}})

        self.assertTrue(self._sense_line(out).startswith("error:"))

    def test_a_healthy_heartbeat_reports_no_blindness(self) -> None:
        self._heartbeat()

        _, out = self._run("doctor", {})

        self.assertNotIn("has not observed", out)

    def test_status_names_the_blind_collectors(self) -> None:
        self._heartbeat()

        _, out = self._run(
            "status",
            {
                "sense": {"streak": 4, "since": "t1", "error": "unavailable"},
                "host": {"streak": 12, "since": "t0", "error": "ssh"},
            },
        )

        # Worst first: the operator reads the top of the list.
        self.assertLess(out.index("blind: host"), out.index("blind: sense"))
        self.assertIn("12 tick(s) since t0", out)
