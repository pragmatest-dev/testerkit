"""Pre-commit reminder: re-run screenshot regeneration when a UI file
with a manifest-tracked ``data-testid`` is changed.

This is a *non-blocking* warning. It exits 0 either way; the goal is
to nudge the author to run ``scripts/regenerate-ui-screenshots.py``
when their commit might have desynchronised one of the cropped PNGs
under ``docs/_assets/operator-ui/``.

The hook is wired in ``.pre-commit-config.yaml`` to fire on
``src/testerkit/ui/pages/**/*.py``. It reads the screenshot script's
``MANIFEST`` to learn which testids the docs depend on, then greps
each changed file for ``data-testid='<name>'`` patterns. If any
changed file contains a tracked testid, print the list of affected
shots and the regenerate command.

The author can ignore the message — drift is also caught by manual
re-runs and (eventually) a CI workflow. The hook just makes
forgetting noisy instead of silent.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MANIFEST_PATH = _REPO_ROOT / "scripts" / "regenerate-ui-screenshots.py"
_TESTID_RE = re.compile(r"data-testid=['\"]([^'\"]+)['\"]")


def _tracked_testids() -> set[str]:
    """Return the set of testids referenced by the screenshot manifest.

    Reads ``regenerate-ui-screenshots.py`` as text and pulls every
    ``selector="[data-testid='X']"`` value out — no import overhead,
    no Playwright dep required for the hook to run.
    """
    text = _MANIFEST_PATH.read_text()
    return set(_TESTID_RE.findall(text))


def _testids_in(path: Path) -> set[str]:
    try:
        return set(_TESTID_RE.findall(path.read_text()))
    except (OSError, UnicodeDecodeError):
        return set()


def main(argv: list[str]) -> int:
    files = [Path(arg) for arg in argv[1:]]
    if not files:
        return 0

    tracked = _tracked_testids()
    if not tracked:
        return 0

    affected: dict[str, set[str]] = {}
    for f in files:
        ids = _testids_in(f) & tracked
        if ids:
            affected[str(f)] = ids

    if not affected:
        return 0

    print(
        "\nUI files with manifest-tracked testids were modified:\n",
        file=sys.stderr,
    )
    for path, ids in sorted(affected.items()):
        for tid in sorted(ids):
            print(f"  {path}  →  data-testid='{tid}'", file=sys.stderr)
    print(
        "\nThe corresponding screenshots under docs/_assets/operator-ui/ "
        "may be stale.\nRe-run:\n\n"
        "    uv run python scripts/regenerate-ui-screenshots.py\n\n"
        "then commit any changed PNGs.\n",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
