"""Setup commands for AI tool integrations."""

from __future__ import annotations

import json
from pathlib import Path

import click

from testerkit import __version__
from testerkit.cli.root import main

_PKG_ROOT = Path(__file__).resolve().parent.parent


@main.group()
def setup():
    """Configure AI tool integrations."""
    pass


@setup.result_callback()
def _after_setup(result: object) -> None:
    """After any ``testerkit setup`` command, nudge (never force) reclaiming older
    index epochs — post-upgrade setup is when they've accumulated. Best-effort;
    the helper swallows its own errors, so this can't break setup."""
    from testerkit.cli.data_cmd import old_epoch_hint

    hint = old_epoch_hint()
    if hint:
        click.echo(f"\nNote: {hint}")


def _get_testerkit_path() -> Path:
    """Find the testerkit executable path."""
    import sys

    testerkit_path = Path(sys.executable).parent / "testerkit"
    if not testerkit_path.exists():
        testerkit_path = Path("testerkit")
    return testerkit_path


def _mcp_server_entry() -> dict[str, str | list[str]]:
    """Build the MCP server config entry for testerkit."""
    return {
        "command": str(_get_testerkit_path()),
        "args": ["mcp", "serve"],
    }


def _write_mcp_config(mcp_file: Path) -> None:
    """Merge testerkit MCP server entry into an MCP config file and write it."""
    config = {"mcpServers": {"testerkit": _mcp_server_entry()}}

    if mcp_file.exists():
        existing = json.loads(mcp_file.read_text())
        if "mcpServers" not in existing:
            existing["mcpServers"] = {}
        existing["mcpServers"]["testerkit"] = config["mcpServers"]["testerkit"]
        config = existing

    mcp_file.parent.mkdir(parents=True, exist_ok=True)
    mcp_file.write_text(json.dumps(config, indent=2) + "\n")
    click.echo(f"Wrote {mcp_file}")


def _skill_names() -> list[str]:
    """List packaged Agent Skill dir names (sorted), without copying anything."""
    skills_src = _PKG_ROOT / "skills"
    if not skills_src.exists():
        return []
    return sorted(d.name for d in skills_src.iterdir() if d.is_dir() and (d / "SKILL.md").exists())


def _copy_skill_dirs(target_root: Path) -> list[str]:
    """Project every packaged Agent Skill dir into a per-tool skills root.

    A skill dir is any immediate child of ``skills/`` containing a
    ``SKILL.md``. Each is copied whole (``SKILL.md`` + ``references/`` +
    any other assets, e.g. ``testerkit-datasheets/agents/``). Always
    overwrites — the project copy is a projection of package data, not
    something users hand-edit.

    This single ``SKILL.md`` (``name``/``description`` frontmatter) format
    is now natively read by all four target tools — no per-tool adapter
    generation (e.g. Cursor ``.mdc`` rules, Copilot ``.prompt.md``) is
    needed. Verified 2026-07:
    - Claude Code / Codex: ``.claude/skills/`` / ``.agents/skills/`` (long-standing).
    - Cursor 2.4+ (shipped 2026-01-22): reads ``.cursor/skills/`` and
      ``.agents/skills/`` natively — https://cursor.com/docs/skills ,
      https://cursor.com/changelog/2-4
    - GitHub Copilot (shipped 2025-12-18, stable VS Code early Jan 2026):
      reads ``.github/skills/``, ``.claude/skills/``, ``.agents/skills/``
      natively across VS Code/JetBrains agent mode, Copilot CLI, and the
      coding agent —
      https://github.blog/changelog/2025-12-18-github-copilot-now-supports-agent-skills/ ,
      https://docs.github.com/en/copilot/concepts/agents/about-agent-skills

    Do NOT reintroduce Cursor-rules/Copilot-prompt adapters on the
    (now stale) assumption that these tools can't read ``SKILL.md`` —
    re-verify against the URLs above first.

    Returns the sorted list of skill dir names copied.
    """
    import shutil

    skills_src = _PKG_ROOT / "skills"
    if not skills_src.exists():
        return []

    copied = []
    for skill_dir in sorted(skills_src.iterdir()):
        if not skill_dir.is_dir() or not (skill_dir / "SKILL.md").exists():
            continue
        shutil.copytree(skill_dir, target_root / skill_dir.name, dirs_exist_ok=True)
        copied.append(skill_dir.name)
    return copied


_MARKER_START = "<!-- testerkit:start -->"
_MARKER_END = "<!-- testerkit:end -->"


def _write_instructions(target_path: Path, header: str = "") -> str | None:
    """Write or update project instructions from the shared template.

    Returns:
        "created"  — file didn't exist, wrote full template
        "updated"  — file existed, appended/replaced managed section
        None       — no change needed (content already up to date)
    """
    template = _PKG_ROOT / "skills" / "templates" / "project-instructions.md"
    if not template.exists():
        return None

    content = template.read_text()

    if header:
        content = header + "\n\n" + content

    managed = f"{_MARKER_START}\n{content}\n{_MARKER_END}\n"

    if not target_path.exists():
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(managed)
        return "created"

    existing = target_path.read_text()

    if _MARKER_START in existing:
        # Replace content between markers
        start = existing.index(_MARKER_START)
        end = existing.index(_MARKER_END) + len(_MARKER_END)
        # Include trailing newline if present
        if end < len(existing) and existing[end] == "\n":
            end += 1
        old_section = existing[start:end]
        if old_section == managed:
            return None
        target_path.write_text(existing[:start] + managed + existing[end:])
        return "updated"

    # No markers yet — append managed section
    separator = "\n" if existing.endswith("\n") else "\n\n"
    target_path.write_text(existing + separator + managed)
    return "updated"


@setup.command("claude-code")
@click.option("--print-only", is_flag=True, help="Print config instead of installing")
def setup_claude_code(print_only: bool):
    """Configure TesterKit MCP server for Claude Code.

    Registers the MCP server, projects the packaged Agent Skills into
    .claude/skills/, and generates a CLAUDE.md project instructions file
    if one doesn't exist.

    Example:
        testerkit setup claude-code
    """
    import subprocess

    testerkit_path = _get_testerkit_path()
    config = {"name": "testerkit", **_mcp_server_entry()}

    if print_only:
        # Preview ALL three side effects of the real run so users can
        # decide whether to commit before doing so. Previously only the
        # MCP JSON was shown, hiding the .claude/skills/ + CLAUDE.md
        # writes entirely.
        cmd = f"claude mcp add testerkit -- {testerkit_path} mcp serve"
        skills_dst = Path.cwd() / ".claude" / "skills"
        skill_names = _skill_names()
        claude_md = Path.cwd() / "CLAUDE.md"

        def _rel(p: Path) -> Path:
            try:
                return p.relative_to(Path.cwd())
            except ValueError:
                return p

        click.echo("`testerkit setup claude-code` would do three things:\n")
        click.echo("1. Register the MCP server via the Claude CLI:")
        click.echo(f"   $ {cmd}\n")
        click.echo("   Equivalent MCP JSON if you'd rather configure manually:")
        for line in json.dumps(config, indent=2).splitlines():
            click.echo(f"   {line}")
        click.echo("")
        click.echo(f"2. Copy {len(skill_names)} Agent Skill dir(s) to {_rel(skills_dst)}/:")
        for name in skill_names:
            click.echo(f"     {name}/")
        click.echo("")
        action = "Create" if not claude_md.exists() else "Update (TesterKit section)"
        click.echo(f"3. {action} {_rel(claude_md)}")
        click.echo("   (TesterKit context the agent reads on every conversation in this project.)")
        click.echo("")
        click.echo("Re-run without --print-only to apply all three.")
        return

    # 1. Register MCP server via claude CLI
    try:
        result = subprocess.run(
            ["claude", "mcp", "add", "testerkit", "--", str(testerkit_path), "mcp", "serve"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            click.echo("✓ Registered TesterKit MCP server")
        else:
            click.echo("Could not add via claude CLI. Add manually:")
            click.echo(f"\n  claude mcp add testerkit -- {testerkit_path} mcp serve\n")
    except (FileNotFoundError, subprocess.CalledProcessError):
        click.echo("Claude CLI not found or failed. Add manually:")
        click.echo(f"\n  claude mcp add testerkit -- {testerkit_path} mcp serve\n")

    # 2. Project Agent Skills into .claude/skills/
    skills_dst = Path.cwd() / ".claude" / "skills"
    copied = _copy_skill_dirs(skills_dst)
    if copied:
        click.echo(f"✓ Copied Agent Skills to .claude/skills/ ({len(copied)} dirs)")

    # 3. Generate/update CLAUDE.md
    result = _write_instructions(Path.cwd() / "CLAUDE.md")
    if result == "created":
        click.echo("✓ Created CLAUDE.md")
    elif result == "updated":
        click.echo("✓ Updated CLAUDE.md (TesterKit section)")
    else:
        click.echo("· CLAUDE.md already up to date")


@setup.command("claude-desktop")
@click.option("--legacy", is_flag=True, help="Use legacy JSON config instead of .mcpb bundle")
@click.option("--print-only", is_flag=True, help="Print config instead of installing")
def setup_claude_desktop(legacy: bool, print_only: bool):
    """Configure TesterKit for Claude Desktop.

    Builds a .mcpb Desktop Extension bundle that can be double-clicked
    to install. Use --legacy for older Claude Desktop versions.

    Example:
        testerkit setup claude-desktop
    """
    import os
    import sys
    import zipfile

    testerkit_path = _get_testerkit_path()

    is_wsl = os.environ.get("WSL_DISTRO_NAME") is not None or (
        Path("/proc/version").exists() and "microsoft" in Path("/proc/version").read_text().lower()
    )
    username = os.environ.get("USERNAME") or os.environ.get("USER", "").split("@")[-1]

    if legacy:
        # Legacy path: direct JSON config editing
        if sys.platform == "win32":
            config_dir = Path(os.environ.get("APPDATA", "")) / "Claude"
        elif is_wsl:
            config_dir = Path(f"/mnt/c/Users/{username}/AppData/Roaming/Claude")
        elif sys.platform == "darwin":
            config_dir = Path.home() / "Library" / "Application Support" / "Claude"
        else:
            config_dir = Path.home() / ".config" / "Claude"

        if is_wsl:
            server_config = {
                "command": "wsl.exe",
                "args": [str(testerkit_path), "mcp", "serve"],
            }
        else:
            server_config = {
                "command": str(testerkit_path),
                "args": ["mcp", "serve"],
            }

        if print_only:
            click.echo("claude_desktop_config.json:\n")
            click.echo(json.dumps({"mcpServers": {"testerkit": server_config}}, indent=2))
            click.echo(f"\nConfig location: {config_dir / 'claude_desktop_config.json'}")
            return

        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "claude_desktop_config.json"

        if config_file.exists():
            config = json.loads(config_file.read_text())
        else:
            config = {}

        if "mcpServers" not in config:
            config["mcpServers"] = {}

        config["mcpServers"]["testerkit"] = server_config
        config_file.write_text(json.dumps(config, indent=2) + "\n")
        click.echo(f"✓ Wrote MCP config: {config_file}")
        click.echo("  Restart Claude Desktop to use TesterKit tools.")
        return

    # Build .mcpb Desktop Extension bundle
    manifest = {
        "schema_version": "1.0",
        "name": "testerkit",
        "display_name": "TesterKit Hardware Test Platform",
        "description": (
            "MCP server for hardware test configuration, instrument discovery, and test execution."
        ),
        "version": __version__,
        "author": "TesterKit",
        "server": {
            "transport": "stdio",
            "command": "wsl.exe" if is_wsl else str(testerkit_path),
            "args": [str(testerkit_path), "mcp", "serve"] if is_wsl else ["mcp", "serve"],
        },
    }

    if print_only:
        click.echo("manifest.json:\n")
        click.echo(json.dumps(manifest, indent=2))
        return

    # Determine output location
    if is_wsl:
        desktop = Path(f"/mnt/c/Users/{username}/Desktop")
        if desktop.exists():
            mcpb_path = desktop / "testerkit.mcpb"
        else:
            mcpb_path = Path.cwd() / "testerkit.mcpb"
    elif sys.platform == "darwin":
        mcpb_path = Path.home() / "Desktop" / "testerkit.mcpb"
    else:
        mcpb_path = Path.cwd() / "testerkit.mcpb"

    with zipfile.ZipFile(mcpb_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2) + "\n")

        # Bundle skills as reference
        skills_dir = _PKG_ROOT / "skills"
        if skills_dir.exists():
            for file in sorted(skills_dir.rglob("*")):
                if file.is_file() and "__pycache__" not in str(file):
                    arcname = "skills" / file.relative_to(skills_dir)
                    zf.write(file, str(arcname))

    click.echo("✓ Built testerkit.mcpb (Desktop Extension)")
    click.echo(f"  → {mcpb_path}")
    click.echo("  Double-click to install in Claude Desktop.")


@setup.command("copilot")
@click.option("--print-only", is_flag=True, help="Print config instead of installing")
def setup_copilot(print_only: bool):
    """Configure TesterKit for GitHub Copilot (VS Code + CLI).

    Sets up the MCP server, projects the packaged Agent Skills into
    .github/skills/ (Copilot has read SKILL.md natively from this path
    since Dec 2025 — VS Code/JetBrains agent mode, Copilot CLI, and the
    coding agent all discover it automatically; no adapter/prompt-file
    generation needed), and writes instruction files for both Copilot in
    VS Code and Copilot CLI (which also reads AGENTS.md).

    Example:
        testerkit setup copilot
    """
    mcp_config = {
        "servers": {
            "testerkit": {
                "type": "stdio",
                "command": "testerkit",
                "args": ["mcp", "serve"],
            }
        }
    }

    if print_only:
        click.echo(".vscode/mcp.json:\n")
        click.echo(json.dumps(mcp_config, indent=2))
        click.echo(
            f"\nWould copy {len(_skill_names())} Agent Skill dir(s) to .github/skills/ "
            "(native Agent Skills format — Copilot reads SKILL.md directly):"
        )
        for name in _skill_names():
            click.echo(f"     {name}/")
        click.echo("\nAnd create/update copilot-instructions.md + AGENTS.md (TesterKit section).")
        return

    # 1. Create/merge .vscode/mcp.json
    vscode_dir = Path.cwd() / ".vscode"
    vscode_dir.mkdir(exist_ok=True)
    mcp_file = vscode_dir / "mcp.json"

    if mcp_file.exists():
        existing = json.loads(mcp_file.read_text())
        if "servers" not in existing:
            existing["servers"] = {}
        existing["servers"]["testerkit"] = mcp_config["servers"]["testerkit"]
        final_config = existing
    else:
        final_config = mcp_config

    mcp_file.write_text(json.dumps(final_config, indent=2) + "\n")
    click.echo("✓ Wrote .vscode/mcp.json (testerkit MCP server)")

    # 2. Project Agent Skills into .github/skills/
    skills_dst = Path.cwd() / ".github" / "skills"
    copied = _copy_skill_dirs(skills_dst)
    if copied:
        click.echo(f"✓ Copied Agent Skills to .github/skills/ ({len(copied)} dirs)")

    # 3. Generate/update .github/copilot-instructions.md
    copilot_instructions = Path.cwd() / ".github" / "copilot-instructions.md"
    result = _write_instructions(copilot_instructions)
    if result == "created":
        click.echo("✓ Created .github/copilot-instructions.md")
    elif result == "updated":
        click.echo("✓ Updated .github/copilot-instructions.md (TesterKit section)")
    else:
        click.echo("· .github/copilot-instructions.md already up to date")

    # 4. Generate/update AGENTS.md (for Copilot CLI + other tools)
    result = _write_instructions(Path.cwd() / "AGENTS.md")
    if result == "created":
        click.echo("✓ Created AGENTS.md")
    elif result == "updated":
        click.echo("✓ Updated AGENTS.md (TesterKit section)")
    else:
        click.echo("· AGENTS.md already up to date")


@setup.command("cursor")
@click.option("--print-only", is_flag=True, help="Print config instead of installing")
def setup_cursor(print_only: bool):
    """Configure TesterKit for Cursor.

    Creates or updates .cursor/mcp.json, projects the packaged Agent
    Skills into .cursor/skills/ (Cursor 2.4+ reads SKILL.md natively
    from this path — no adapter/rules-file generation needed), and
    generates AGENTS.md project instructions (Cursor reads AGENTS.md
    natively).

    Example:
        testerkit setup cursor
    """
    if print_only:
        config = {"mcpServers": {"testerkit": _mcp_server_entry()}}
        click.echo("Add this to .cursor/mcp.json:\n")
        click.echo(json.dumps(config, indent=2))
        click.echo(
            f"\nWould copy {len(_skill_names())} Agent Skill dir(s) to .cursor/skills/ "
            "(native Agent Skills format — Cursor 2.4+ reads SKILL.md directly):"
        )
        for name in _skill_names():
            click.echo(f"     {name}/")
        click.echo("\nAnd create/update AGENTS.md (TesterKit section).")
        return

    # 1. MCP server
    mcp_file = Path.cwd() / ".cursor" / "mcp.json"
    _write_mcp_config(mcp_file)

    # 2. Project Agent Skills into .cursor/skills/
    skills_dst = Path.cwd() / ".cursor" / "skills"
    copied = _copy_skill_dirs(skills_dst)
    if copied:
        click.echo(f"✓ Copied Agent Skills to .cursor/skills/ ({len(copied)} dirs)")

    # 3. Generate/update AGENTS.md (Cursor reads it natively)
    result = _write_instructions(Path.cwd() / "AGENTS.md")
    if result == "created":
        click.echo("✓ Created AGENTS.md")
    elif result == "updated":
        click.echo("✓ Updated AGENTS.md (TesterKit section)")
    else:
        click.echo("· AGENTS.md already up to date")
    click.echo("Restart Cursor to use TesterKit tools.")


@setup.command("codex")
@click.option("--print-only", is_flag=True, help="Print config instead of installing")
def setup_codex(print_only: bool):
    """Configure TesterKit for OpenAI Codex.

    Projects the packaged Agent Skills into .agents/skills/, generates
    AGENTS.md project instructions (Codex's native context file), and
    prints the MCP server entry for ~/.codex/config.toml.

    Example:
        testerkit setup codex
    """
    testerkit_path = _get_testerkit_path()
    toml_snippet = (
        f'[mcp_servers.testerkit]\ncommand = "{testerkit_path}"\nargs = ["mcp", "serve"]\n'
    )

    if print_only:
        click.echo(f"Would copy {len(_skill_names())} Agent Skill dir(s) to .agents/skills/:")
        for name in _skill_names():
            click.echo(f"     {name}/")
        click.echo("\nAnd create/update AGENTS.md (TesterKit section).\n")
        click.echo("Add this to ~/.codex/config.toml for MCP tools:\n")
        click.echo(toml_snippet)
        return

    # 1. Project Agent Skills into .agents/skills/
    skills_dst = Path.cwd() / ".agents" / "skills"
    copied = _copy_skill_dirs(skills_dst)
    if copied:
        click.echo(f"✓ Copied Agent Skills to .agents/skills/ ({len(copied)} dirs)")

    # 2. Generate/update AGENTS.md (Codex reads it natively)
    result = _write_instructions(Path.cwd() / "AGENTS.md")
    if result == "created":
        click.echo("✓ Created AGENTS.md")
    elif result == "updated":
        click.echo("✓ Updated AGENTS.md (TesterKit section)")
    else:
        click.echo("· AGENTS.md already up to date")

    # 3. MCP is user-global config in Codex — print, don't write, another tool's home config
    click.echo("\nTo add TesterKit MCP tools, add this to ~/.codex/config.toml:\n")
    click.echo(toml_snippet)


@setup.command("cline")
@click.option("--print-only", is_flag=True, help="Print config instead of installing")
def setup_cline(print_only: bool):
    """Configure TesterKit MCP server for Cline (VS Code extension).

    Creates or updates cline_mcp_settings.json in VS Code settings.

    Example:
        testerkit setup cline
    """
    config = {"mcpServers": {"testerkit": _mcp_server_entry()}}

    if print_only:
        click.echo("Add this to your Cline MCP settings:\n")
        click.echo(json.dumps(config, indent=2))
        return

    # Try to find VS Code settings directory
    home = Path.home()
    vscode_dirs = [
        home / ".config" / "Code" / "User",  # Linux
        home / "Library" / "Application Support" / "Code" / "User",  # macOS
        home / "AppData" / "Roaming" / "Code" / "User",  # Windows
    ]

    settings_dir = next((d for d in vscode_dirs if d.exists()), None)

    if not settings_dir:
        click.echo("VS Code settings directory not found. Add manually:")
        click.echo(json.dumps(config, indent=2))
        return

    mcp_file = settings_dir / "cline_mcp_settings.json"
    _write_mcp_config(mcp_file)
    click.echo("Restart VS Code to use TesterKit tools with Cline.")


@setup.command("show")
def setup_show():
    """Show current MCP server configuration.

    Displays the command to start the TesterKit MCP server.
    """
    testerkit_path = _get_testerkit_path()

    click.echo("TesterKit MCP Server")
    click.echo("-" * 40)
    click.echo(f"Command: {testerkit_path} mcp serve")
    click.echo("Transport: stdio")
    click.echo()
    click.echo("Available tools:")
    click.echo("  - list_parts: List all part specifications")
    click.echo("  - get_part_spec: Get a part specification by ID")
    click.echo("  - list_stations: List all test stations")
    click.echo("  - get_station_config: Get a station configuration by ID")
    click.echo("  - find_compatible_stations: Find stations for a part")
    click.echo("  - check_station_compatibility: Check if station can test part")
    click.echo("  - derive_required_capabilities: Get capability requirements")
    click.echo("  - get_instrument_library: Get instrument definitions")
    click.echo("  - save_part_spec: Save a new part specification")
