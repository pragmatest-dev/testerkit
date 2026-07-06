"""Model-in-loop eval runner — dev tooling, NOT part of the litmus package.

For each task, invokes an AI to generate the candidate into a throwaway project
dir, then grades it deterministically (grader.py). Reports per-task pass-rate
over N runs. The default backend is **Claude Code headless** (`claude -p`),
which runs under your existing Max/Pro subscription — no API key, no per-call
billing (it draws against subscription rate limits, so keep N small).

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
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from grader import grade  # noqa: E402
from tasks import TASKS  # noqa: E402

_REPO = Path(__file__).resolve().parent.parent
_SKILLS_ROOT = _REPO / "src" / "litmus" / "skills"


def _skill_context(task) -> str:
    """Load a task's skill as model-augmentation context.

    Skills live at ``src/litmus/skills/<skill>/SKILL.md``. If the skill dir (or
    its SKILL.md) doesn't exist — e.g. ``litmus-interactive`` before it ships —
    this returns "" and the task still runs, just vanilla-only.
    """
    skill_md = _SKILLS_ROOT / task.skill / "SKILL.md"
    if not skill_md.exists():
        return ""
    parts = [f"# {task.skill}\n\n{skill_md.read_text()}"]
    for ref in task.context_refs:
        f = _SKILLS_ROOT / task.skill / "references" / f"{ref}.md"
        if f.exists():
            parts.append(f"# {task.skill}/references/{ref}\n\n{f.read_text()}")
    return "\n\n---\n\n".join(parts)


def _prompt(task, with_skill: bool) -> str:
    skill_text = _skill_context(task) if with_skill else ""
    # Missing skill dir (e.g. a skill not shipped yet) -> run vanilla, no
    # dangling "use this guide" header pointing at nothing.
    ctx = ("\n\nUse this Litmus skill guide:\n\n" + skill_text) if skill_text else ""
    if task.expect_cli:
        body = (
            "You are answering a question about existing Litmus test data using "
            "the real `litmus` CLI (or an equivalent MCP tool) — do not write a "
            "test.\n"
            f"Task: {task.prompt}\n"
            "Write the exact command you would run into a file named "
            "`answer.txt` in the current directory (one command, nothing else)."
        )
    elif task.validate_yaml:
        body = (
            f"You are authoring Litmus {task.validate_yaml} configuration YAML.\n"
            f"Task: {task.prompt}\n"
            f"Write the YAML file(s) into the current directory's "
            f"{task.validate_yaml}s/ folder. Do the SMALLEST thing that "
            "satisfies the request — do not add configuration the task "
            "doesn't need."
        )
    else:
        body = (
            "You are writing a hardware test with Litmus (a pytest-native framework).\n"
            f"Task: {task.prompt}\n"
            "Write the necessary file(s) into the current directory. Do the SMALLEST "
            "thing that satisfies the request — do not add configuration the task "
            "doesn't need."
        )
    return body + ctx


def _generate(prompt: str, project: Path, model) -> str:
    """Drive Claude Code headless to write the candidate into `project`.

    Returns the model's captured stdout+stderr so the runner can persist it as
    `.eval_response.txt` for CLI-answer tasks (grader.py's structural check).
    """
    cmd = ["claude", "-p", prompt, "--dangerously-skip-permissions"]
    if model:
        cmd += ["--model", model]
    proc = subprocess.run(cmd, cwd=str(project), capture_output=True, text=True, timeout=300)
    return proc.stdout + "\n" + proc.stderr


def run(task, n: int, with_skill: bool, model):
    results = []
    for i in range(n):
        proj = Path(tempfile.mkdtemp(prefix=f"eval_{task.id}_{i}_"))
        try:
            reply = _generate(_prompt(task, with_skill), proj, model)
            (proj / ".eval_response.txt").write_text(reply)
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

    per_skill_ok: dict = defaultdict(int)
    per_skill_total: dict = defaultdict(int)
    total_ok = total = 0
    for t in tasks:
        if t.manual:
            print(f"{t.id:26s} SKIPPED ({t.skip_reason or 'manual task'})")
            continue
        rs = run(t, a.n, not a.vanilla, a.model)
        ok = sum(r.ok for r in rs)
        total_ok += ok
        total += len(rs)
        per_skill_ok[t.skill] += ok
        per_skill_total[t.skill] += len(rs)
        print(f"{t.id:26s} [{t.skill}] {ok}/{len(rs)} pass")
        for r in rs:
            if not r.ok:
                print("   " + r.summary().replace("\n", "\n   "))

    print("\nPer-skill:")
    for skill in sorted(per_skill_total):
        print(f"  {skill:20s} {per_skill_ok[skill]}/{per_skill_total[skill]} pass")

    print(f"\nTOTAL {total_ok}/{total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
