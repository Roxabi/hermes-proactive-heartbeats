"""Release consistency for proactive-heartbeats (trunk-manual, see CONTRIBUTING.md § Releases).

``check``           plugin.yaml ``version`` is the newest CHANGELOG heading. Runs in CI on every
                    push and PR, so a changelog section without its bump — or a bump without its
                    section — cannot land. Measured: 0.6.0 to 0.9.0 were written to the changelog
                    while plugin.yaml still read 0.5.0, and none of them was ever tagged.
``notes TAG SHA``   the tag names that version; print the GitHub release notes (install SHA,
                    install command, the version's changelog section).

Reads ``plugin.yaml`` and ``CHANGELOG.md`` from the current directory.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

COMPONENT = "proactive-heartbeats"
_HEADING = re.compile(r"^## (\d+\.\d+\.\d+) — \d{4}-\d{2}-\d{2}$", re.MULTILINE)


def manifest_version() -> str:
    match = re.search(
        r"^version:\s*(\S+)\s*$", Path("plugin.yaml").read_text(encoding="utf-8"), re.M
    )
    if match is None:
        raise SystemExit("plugin.yaml has no version")
    return match.group(1)


def changelog_section(changelog: str, version: str) -> str:
    """Body of ``## <version> — <date>``, up to the next version heading."""
    headings = list(_HEADING.finditer(changelog))
    for index, heading in enumerate(headings):
        if heading.group(1) == version:
            end = headings[index + 1].start() if index + 1 < len(headings) else len(changelog)
            return changelog[heading.end() : end].strip()
    raise SystemExit(f"CHANGELOG.md has no '## {version} — <date>' section")


def check() -> str:
    version = manifest_version()
    headings = _HEADING.findall(Path("CHANGELOG.md").read_text(encoding="utf-8"))
    newest = headings[0] if headings else None
    if newest != version:
        raise SystemExit(
            f"plugin.yaml version {version} must be the newest CHANGELOG heading (found {newest})"
        )
    return version


def notes(tag: str, sha: str) -> str:
    version = check()
    if tag != f"{COMPONENT}/v{version}":
        raise SystemExit(f"tag {tag} does not name plugin.yaml version {version}")
    section = changelog_section(Path("CHANGELOG.md").read_text(encoding="utf-8"), version)
    return (
        f"Install SHA: `{sha}`\n\n"
        f"```bash\nhermes plugins install Roxabi/hermes-{COMPONENT} --ref {sha}\n```\n\n"
        f"{section}\n"
    )


def main(argv: list[str]) -> int:
    if argv == ["check"]:
        print(check())
        return 0
    if len(argv) == 3 and argv[0] == "notes":
        sys.stdout.write(notes(argv[1], argv[2]))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
