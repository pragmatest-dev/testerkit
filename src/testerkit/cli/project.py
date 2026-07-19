"""Project initialization and scaffolding commands."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import click

from testerkit.cli._common import _discover_instruments
from testerkit.cli.root import main
from testerkit.cli.setup_cmd import (
    setup_claude_code,
    setup_claude_desktop,
    setup_copilot,
)


def _noninteractive() -> bool:
    """Detect automation envs (CI/Codespaces) where isatty() is unreliable.

    Codespaces allocates a pseudo-TTY for postCreate scripts, so
    ``sys.stdin.isatty()`` returns True even though nothing is there to
    answer a prompt. CI runners are similarly non-interactive.
    """
    return bool(os.environ.get("CI") or os.environ.get("CODESPACES"))


@main.command()
@click.argument("name", required=False)
@click.option("--no-git", is_flag=True, help="Skip git initialization")
@click.option("--discover", is_flag=True, help="Auto-discover instruments and create station file")
@click.option(
    "--starter/--no-starter",
    default=None,
    help="Generate starter example files (prompts if not specified)",
)
@click.option(
    "--tier",
    type=click.Choice(["bringup", "bench", "factory"], case_sensitive=False),
    default=None,
    help=(
        "Scaffold tier. 'bringup' = Tier 0/1 (MagicMock fixtures, one test, "
        "one sidecar, no station/part YAML). 'bench' = Tier 2 starter "
        "(equivalent to --starter). 'factory' = Tier 3/4 (bench + profiles)."
    ),
)
@click.option(
    "--ai",
    type=click.Choice(["claude-code", "claude-desktop", "copilot"], case_sensitive=False),
    default=None,
    help="Set up AI tool integration (MCP server + project instructions)",
)
@click.option(
    "--no-input",
    is_flag=True,
    help=(
        "Run non-interactively: scaffold with defaults and never prompt "
        "(skips AI setup unless --ai is given)."
    ),
)
@click.option("--no-ai", is_flag=True, help="Skip AI tool integration.")
@click.option("--name", "project_name", default=None, help="Project name (overrides auto-detect)")
def init(
    name: str | None,
    no_git: bool,
    discover: bool,
    starter: bool | None,
    tier: str | None,
    ai: str | None,
    no_input: bool,
    no_ai: bool,
    project_name: str | None,
):
    """Initialize a new TesterKit project.

    With NAME: creates a new directory and scaffolds inside it.
    Without NAME: scaffolds the current directory (like ``uv init``).

    All files are skip-if-exists, so it's safe to run on an existing project.

    Examples:

        testerkit init my_project

        testerkit init my_project --starter

        testerkit init --discover

        testerkit init my_project --discover
    """
    from testerkit.init import check_command, init_project

    if name:
        # New-directory mode
        project_path = Path.cwd() / name
        if project_path.exists():
            click.echo(f"Error: '{name}' already exists", err=True)
            raise SystemExit(1)
        project_path.mkdir()
        cwd_mode = False
    else:
        # Scaffold CWD mode
        project_path = Path.cwd()
        cwd_mode = True

    # Check dependencies and warn if missing
    if not check_command("git") and not no_git:
        click.echo("Warning: git not found, skipping git init")
        click.echo("  Install git: https://git-scm.com/downloads")
        no_git = True

    if not check_command("uv"):
        click.echo("Warning: uv not found")
        click.echo("  Install: curl -LsSf https://astral.sh/uv/install.sh | sh")

    # Instrument discovery vs starter files
    # - If --tier or --starter: skip discovery (they have their own mock station)
    # - If --discover: skip starter (user wants real instruments)
    # - If neither: prompt for starter first; if declined, prompt for discovery
    station = None
    use_starter = False
    tier_lower = tier.lower() if tier else None

    if tier_lower:
        # Explicit --tier flag wins over --starter / --discover / prompts
        pass
    elif starter is True:
        # Explicit --starter flag
        use_starter = True
    elif discover:
        # Explicit --discover flag
        station = _discover_instruments(interactive=False)
    elif starter is False:
        # Explicit --no-starter flag — skip prompts (use --discover for instruments)
        pass
    elif sys.stdin.isatty() and not no_input and not _noninteractive():
        # No flags provided and truly interactive - prompt
        if click.confirm("Create starter example files?", default=True):
            use_starter = True
        elif click.confirm("Discover instruments?", default=False):
            station = _discover_instruments(interactive=True)
    # else: non-interactive with no flags given - bare scaffold (use_starter stays False)

    result = init_project(
        project_path,
        git=not no_git,
        station=station,
        starter=use_starter,
        name=project_name,
        tier=tier_lower,
    )

    # Print summary
    if cwd_mode:
        click.echo(f"\nInitialized testerkit project in {project_path.name}/")
    else:
        click.echo(f"\nCreated {name}/")
    for d in result["created_dirs"]:
        click.echo(f"  {d}/")
    for f in result["created_files"]:
        click.echo(f"  {f}")

    if result["git_initialized"]:
        click.echo("  .git/")

    for warning in result["warnings"]:
        click.echo(f"Warning: {warning}")

    # AI tool setup. An explicit --ai always wins, even under --no-input.
    if ai is None and not no_ai and not no_input and not _noninteractive() and sys.stdin.isatty():
        # Detect installed tools and prompt (only when truly interactive)
        ai_tools: list[tuple[str, str]] = []
        if check_command("claude"):
            ai_tools.append(("claude-code", "Claude Code"))
        # Check for VS Code / Copilot
        if (project_path / ".vscode").exists() or check_command("code"):
            ai_tools.append(("copilot", "GitHub Copilot"))

        if ai_tools:
            choices = [name for name, _ in ai_tools]
            labels = [label for _, label in ai_tools]
            click.echo(f"\nDetected AI tools: {', '.join(labels)}")
            if click.confirm("Set up AI assistance?", default=True):
                if len(ai_tools) == 1:
                    ai = choices[0]
                else:
                    ai = click.prompt(
                        "Which tool?",
                        type=click.Choice(choices, case_sensitive=False),
                        default=choices[0],
                    )
    elif ai is None:
        # Non-interactive (or explicitly skipped): never prompt. If AI
        # tools were detected, hint how to wire them up later.
        ai_tools = []
        if check_command("claude"):
            ai_tools.append(("claude-code", "Claude Code"))
        if (project_path / ".vscode").exists() or check_command("code"):
            ai_tools.append(("copilot", "GitHub Copilot"))
        if ai_tools and not no_ai:
            first_tool = ai_tools[0][0]
            click.echo(f"\nTip: wire up your AI tool with  testerkit setup {first_tool}")

    if ai:
        original_cwd = os.getcwd()
        try:
            os.chdir(project_path)
            ctx = click.get_current_context()
            if ai == "claude-code":
                ctx.invoke(setup_claude_code, print_only=False)
            elif ai == "claude-desktop":
                ctx.invoke(setup_claude_desktop, legacy=False, print_only=False)
            elif ai == "copilot":
                ctx.invoke(setup_copilot, print_only=False)
        finally:
            os.chdir(original_cwd)

    click.echo("\nNext steps:")
    if not cwd_mode:
        click.echo(f"  cd {name}")
        click.echo("  uv sync")
    if tier_lower == "bringup":
        click.echo("  pytest -v             # run smoke tests with MagicMock instruments")
    elif use_starter or tier_lower == "bench":
        click.echo("  pytest                # run tests with mock instruments")
    else:
        click.echo("  pytest tests/ --mock-instruments --uut-serial=TEST001")
    click.echo("  testerkit serve          # open operator UI at localhost:8000")


@main.command("new-test")
@click.argument("name")
def new_test(name: str):
    """Scaffold a new test file.

    Creates tests/test_<name>.py with instrument fixtures from your station.

    Examples:

        testerkit new-test output_voltage

        testerkit new-test smoke_check
    """
    # Normalize name: strip test_ prefix if present, we'll add it back
    test_name = name.removeprefix("test_")
    filename = f"test_{test_name}.py"

    tests_dir = Path.cwd() / "tests"
    target = tests_dir / filename

    if target.exists():
        click.echo(f"Error: {target} already exists", err=True)
        raise SystemExit(1)

    # Try to discover available roles from station config
    available_roles: list[str] = []
    try:
        from testerkit.store import list_stations

        stations = list_stations()
        if stations:
            available_roles = sorted(stations[0].instruments.keys())
    except (ImportError, OSError, ValueError):
        pass

    # Prompt for instruments
    hint = ""
    if available_roles:
        hint = f" (available from station: {', '.join(available_roles)})"
    roles_input = click.prompt(
        f"Instruments to use in test{hint}, or Enter to skip",
        default="",
        show_default=False,
    )

    roles = [r.strip() for r in roles_input.split(",") if r.strip()] if roles_input else []

    # Build function signature: context, <roles>, verify
    param_parts = ["context", *roles, "verify"]
    sig = ", ".join(param_parts)

    lines = [
        f'"""Tests for {test_name}."""',
        "",
        "",
        f"def test_{test_name}({sig}) -> None:",
        f'    """Measure {test_name}."""',
    ]
    # Add a helpful skeleton showing the 3-step pattern
    if roles:
        lines.append("    # 1. GET conditions from context")
        lines.append('    # vin = context.get_param("vin", 5.0)')
        lines.append("    #")
        lines.append("    # 2. SET UP stimulus")
        first_role = roles[0]
        lines.append(f"    # {first_role}.set_voltage(vin)")
        lines.append("    #")
        lines.append("    # 3. MEASURE and VERIFY (framework checks limits)")
        measure_role = roles[1] if len(roles) > 1 else roles[0]
        lines.append(f'    verify("{test_name}", float({measure_role}.measure_voltage()))')
    else:
        lines.append("    # TODO: Add test logic")
        lines.append("    pass")
    lines.append("")
    content = "\n".join(lines)

    tests_dir.mkdir(exist_ok=True)
    target.write_text(content)
    click.echo(f"Created {target}")
    click.echo("\nNext: pytest --mock-instruments")
