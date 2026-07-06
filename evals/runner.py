"""Model-in-loop eval runner — dev tooling, NOT part of the litmus package.

For each task, invokes an AI to generate the test into a throwaway project dir,
then grades it deterministically (grader.py). Reports per-task pass-rate over N
runs. The default backend is **Claude Code headless** (`claude -p`), which runs
under your existing Max/Pro subscription — no API key, no per-call billing (it
draws against subscription rate limits, so keep N small).

The platform never calls an LLM; this harness does, and lives outside src/litmus.

Usage:
    uv run python evals/runner.py                      # all tasks, skill-augmented, N=3
    uv run python evals/runner.py --vanilla            # baseline: no skill in context
    uv run python evals/runner.py --task r0_verify --n 5
    uv run python evals/runner.py --model claude-sonnet-5

Vanilla vs skill-augmented is the lift measurement Anthropic's skill guidance
recommends: run both and compare pass-rates to prove the skills actually help.

To eval a different model/provider, swap `_generate` — everything else (tasks,
grader) is backend-agnostic.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from grader import grade  # noqa: E402
from tasks import TASKS  # noqa: E402

_REPO = Path(__file__).resolve().parent.parent
_SKILL_DIRS = (
    _REPO / "src" / "litmus" / "skills" / "workflow",
    _REPO / "src" / "litmus" / "skills" / "refs",
)


def _skill_context(task) -> str:
    parts = []
    for ref in task.context_refs:
        for base in _SKILL_DIRS:
            f = base / f"{ref}.md"
            if f.exists():
                parts.append(f"# {ref}\n\n{f.read_text()}")
                break
    return "\n\n---\n\n".join(parts)


def _prompt(task, with_skill: bool) -> str:
    ctx = (
        ("\n\nUse this Litmus test-writing guide:\n\n" + _skill_context(task)) if with_skill else ""
    )
    return (
        "You are writing a hardware test with Litmus (a pytest-native framework).\n"
        f"Task: {task.prompt}\n"
        "Write the necessary file(s) into the current directory. Do the SMALLEST "
        "thing that satisfies the request — do not add configuration the task "
        f"doesn't need.{ctx}"
    )


def _generate(prompt: str, project: Path, model) -> None:
    """Drive Claude Code headless to write the candidate into `project`."""
    cmd = ["claude", "-p", prompt, "--dangerously-skip-permissions"]
    if model:
        cmd += ["--model", model]
    subprocess.run(cmd, cwd=str(project), capture_output=True, text=True, timeout=300)


def run(task, n: int, with_skill: bool, model):
    results = []
    for i in range(n):
        proj = Path(tempfile.mkdtemp(prefix=f"eval_{task.id}_{i}_"))
        try:
            _generate(_prompt(task, with_skill), proj, model)
            results.append(grade(proj, task))
        finally:
            shutil.rmtree(proj, ignore_errors=True)
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--task", help="single task id (default: all)")
    ap.add_argument("--n", type=int, default=3, help="runs per task (error bars)")
    ap.add_argument("--vanilla", action="store_true", help="no skill in context (baseline)")
    ap.add_argument("--model", default=None, help="model id passed to `claude --model`")
    a = ap.parse_args()

    tasks = [t for t in TASKS if not a.task or t.id == a.task]
    if not tasks:
        print(f"no task {a.task!r}; known: {[t.id for t in TASKS]}")
        return 2

    cond = "vanilla" if a.vanilla else "skill-augmented"
    print(f"condition={cond}  n={a.n}  model={a.model or 'default'}\n")
    total_ok = total = 0
    for t in tasks:
        rs = run(t, a.n, not a.vanilla, a.model)
        ok = sum(r.ok for r in rs)
        total_ok += ok
        total += len(rs)
        print(f"{t.id:20s} {ok}/{len(rs)} pass")
        for r in rs:
            if not r.ok:
                print("   " + r.summary().replace("\n", "\n   "))
    print(f"\nTOTAL {total_ok}/{total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
