"""Profile resolution + session phase demotion.

Two responsibilities live here:

* **Profile resolution.** Load ``litmus.yaml``, auto-synthesize a
  ``--<facet>`` flag per declared facet key, resolve the active profile
  from name / facets / both, walk any ``extends:`` chain parent-first,
  install the merged :class:`ProfileConfig` on the ``_active_profile``
  ContextVar, and compose runner-level ``keyword`` / ``markexpr``
  filters (the runner adapter routes them to its host's primitives).
* **Test-phase demotion** (:func:`resolve_test_phase`). The data-stamp
  phase demotes to ``"development"`` on dirty git or active mocks so
  untrustworthy runs never stamp production. Profile selection reads
  the raw CLI value — only the data stamp is rewritten.

Errors raise :class:`ProfileError` — the runner adapter wraps that to
its host's user-error type (e.g. ``ProfileError`` in
:mod:`litmus.pytest_plugin`).
"""

from __future__ import annotations

import os
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from litmus.execution._state import (
    set_active_facets,
    set_active_profile,
    set_session_inputs,
)
from litmus.execution.sidecar import _merge_entry_into
from litmus.models.project import ProfileConfig, ProjectConfig
from litmus.models.test_config import TestEntry
from litmus.prompts import ask as ask_prompt


class ProfileError(Exception):
    """Raised when profile resolution / runner-block validation fails.

    Runner-neutral: the pytest adapter catches this and re-raises as
    ``pytest.UsageError`` at the hook boundary; other runners adapt
    to their own user-error type.
    """


class PytestRunner(BaseModel):
    """Typed schema for the pytest runner's ``runner:`` block.

    Tier 2 of the two-tier validation pattern: Litmus core stores the
    ``runner:`` block as ``dict[str, Any]`` (opaque, structurally
    validated only); the active runner plugin owns content validation
    via this Pydantic model. ``extra="forbid"`` catches typos in field
    names (``addopst:`` instead of ``addopts:``) at session start with
    a clear error pointing at the offending field.

    Per-test scope (a future per-test ``runner:`` overlay thread) will
    add policy that rejects session-only fields (``addopts``,
    ``markexpr``, ``keyword``, ``plugins``, ``parallelism``) at
    sub-profile scopes; for v1 the model just defines all valid
    fields. ``markers`` is a list of single-key dicts (each entry is
    one ecosystem marker like ``parametrize`` / ``flaky`` / ``skip``)
    — pytest's stacking semantic stays for these.
    """

    model_config = ConfigDict(extra="forbid")

    addopts: str | None = None
    markexpr: str | None = None
    keyword: str | None = None
    plugins: list[str] = Field(default_factory=list)
    parallelism: int | None = None
    timeout: int | None = None
    markers: list[dict[str, Any]] = Field(default_factory=list)


def validate_pytest_runner(block: dict[str, Any]) -> PytestRunner:
    """Validate a ``runner:`` block from project / profile scope.

    Raises ``ProfileError`` with a runner-namespaced message on
    unknown fields (``runner.<field>``), so users see exactly which
    block to fix. Empty input returns an empty (all-defaults)
    PytestRunner.
    """
    try:
        return PytestRunner.model_validate(block)
    except Exception as exc:  # pydantic.ValidationError — surface to user as UsageError
        raise ProfileError(f"Invalid runner: block — {exc}") from exc


def load_project_defaults() -> ProjectConfig:
    """Load :class:`ProjectConfig` from ``litmus.yaml``, falling back to defaults."""
    try:
        from litmus.store import load_project_config

        return load_project_config()
    except Exception:  # noqa: BLE001 — any load failure falls back to defaults
        # Bad or missing litmus.yaml — don't crash pytest over config
        return ProjectConfig(name="litmus")


def collect_profile_facet_keys(project: ProjectConfig) -> list[str]:
    """Return the union of facet keys declared across all profiles.

    Used to auto-synthesize one ``--<facet>`` CLI flag per declared key,
    so operators can select profiles by facet query instead of by name.
    """
    keys: set[str] = set()
    for profile in project.profiles.values():
        keys.update(profile.facets)
    return sorted(keys)


def facet_key_to_cli_flag(key: str) -> str:
    """Map a facet key (``product``, ``instrument_set``) to its CLI flag form."""
    return f"--{key.replace('_', '-')}"


def _clone_test_entry(entry: TestEntry) -> TestEntry:
    """Deep-clone a TestEntry so merge mutations don't leak into source models.

    Pydantic's ``model_copy(deep=True)`` covers the marker fields plus
    the ``runner:`` dict; the recursive ``tests:`` map is rebuilt with
    fresh clones so children can be mutated independently.
    """
    cloned = entry.model_copy(deep=True)
    cloned.tests = {k: _clone_test_entry(v) for k, v in entry.tests.items()}
    return cloned


def _merge_test_entries(
    parent: dict[str, TestEntry],
    child: dict[str, TestEntry],
) -> dict[str, TestEntry]:
    """Recursively merge two ``tests:`` trees parent-first, child overrides.

    Same rule applied at every level: same key → child marker fields
    merge into parent's (last-wins), nested ``tests:`` recurse.
    Parent-only keys pass through.
    """
    merged: dict[str, TestEntry] = {
        name: _clone_test_entry(entry) for name, entry in parent.items()
    }
    for name, child_entry in child.items():
        existing = merged.get(name)
        if existing is None:
            merged[name] = _clone_test_entry(child_entry)
        else:
            _merge_entry_into(existing, child_entry)
            existing.tests = _merge_test_entries(existing.tests, child_entry.tests)
    return merged


def flatten_profile_chain(leaf_name: str, project: ProjectConfig) -> ProfileConfig:
    """Walk ``extends`` chain parent-first and merge into a single profile.

    The leaf profile is the starting point; each ``extends`` link walks one
    step further up the chain. The merge is parent-first so child values
    win on conflicts — same rule as stacked pytest decorators.

    Raises ``ProfileError`` on an unknown parent or a cycle.
    """
    chain: list[ProfileConfig] = []
    visited: list[str] = []
    current: str | None = leaf_name
    while current is not None:
        if current in visited:
            cycle = " -> ".join(visited + [current])
            raise ProfileError(f"Cyclic profile extends chain: {cycle}")
        profile = project.profiles.get(current)
        if profile is None:
            if not visited:
                known = ", ".join(sorted(project.profiles)) or "(none defined)"
                raise ProfileError(f"Unknown profile {current!r}; known profiles: {known}")
            raise ProfileError(f"Profile {visited[-1]!r} extends unknown profile {current!r}")
        visited.append(current)
        chain.append(profile)
        current = profile.extends
    chain.reverse()

    merged = ProfileConfig()
    addopts_parts: list[str] = []
    markexpr: str | None = None
    keyword: str | None = None
    for profile in chain:
        if profile.description is not None:
            merged.description = profile.description
        merged.facets.update(profile.facets)
        runner_block = dict(profile.runner)
        # Validate to catch typos early; addopts is concatenated specially.
        runner = validate_pytest_runner(runner_block)
        if runner.addopts:
            addopts_parts.append(runner.addopts)
        if runner.markexpr is not None:
            markexpr = runner.markexpr
        if runner.keyword is not None:
            keyword = runner.keyword
        # Strip flat session fields we handle specially; remaining keys
        # (markers, timeout, plugins, parallelism) go through the
        # generic last-wins runner merge.
        for k in ("addopts", "markexpr", "keyword"):
            runner_block.pop(k, None)
        # Merge marker fields directly onto the ProfileConfig (it shares
        # TestEntry's flat shape); runner/tests merge separately.
        scratch = TestEntry(
            limits=profile.limits,
            sweeps=profile.sweeps,
            mocks=profile.mocks,
            specs=profile.specs,
            connections=profile.connections,
            retry=profile.retry,
            prompts=profile.prompts,
            runner=runner_block,
        )
        _merge_entry_into(merged, scratch)
        merged.tests = _merge_test_entries(merged.tests, profile.tests)

    if addopts_parts:
        merged.runner["addopts"] = " ".join(addopts_parts)
    if markexpr is not None:
        merged.runner["markexpr"] = markexpr
    if keyword is not None:
        merged.runner["keyword"] = keyword

    merged.extends = None
    return merged


def resolve_active_profile(
    profile_name: str | None,
    facet_flags: dict[str, str],
    project: ProjectConfig,
) -> tuple[str | None, ProfileConfig | None, dict[str, str]]:
    """Select a profile by name, by facet query, or by cross-checked both.

    Resolution rules (see ``docs/guides/profiles.md``):

    * **Name + facets** — name wins, but every flag must match the
      profile's declared facet value. Mismatches raise ``UsageError``.
    * **Name only** — direct lookup in ``profiles:``.
    * **Facets only** — filter profiles matching **all** provided flags.
      A profile that does not declare a facet key the query uses does
      **not** match (strict "unspecified" semantics). Zero matches and
      >1 matches both raise ``UsageError``.
    * **Neither** — returns ``(None, None, {})``.

    Returns ``(profile_name, profile, facets_dict)``. ``facets_dict`` is
    the profile's declared facets (so a name-only selection still gets
    provenance facets populated) plus any explicitly provided flags.
    """
    if not profile_name and not facet_flags:
        return None, None, {}

    if profile_name:
        profile = project.profiles.get(profile_name)
        if profile is None:
            known = ", ".join(sorted(project.profiles)) or "(none defined)"
            raise ProfileError(
                f"Unknown --litmus-profile={profile_name!r}; known profiles: {known}"
            )
        if facet_flags:
            mismatches = [
                f"--{k.replace('_', '-')}={v!r} (profile declares {k}={profile.facets.get(k)!r})"
                for k, v in facet_flags.items()
                if profile.facets.get(k) != v
            ]
            if mismatches:
                raise ProfileError(
                    f"Profile {profile_name!r} does not match facet flags: " + ", ".join(mismatches)
                )
        merged = flatten_profile_chain(profile_name, project)
        facets = {**merged.facets, **facet_flags}
        return profile_name, merged, facets

    # Facet-only query.
    matches = [
        (name, profile)
        for name, profile in project.profiles.items()
        if all(profile.facets.get(k) == v for k, v in facet_flags.items())
    ]
    if len(matches) == 0:
        known = sorted(
            " ".join(f"{k}={v}" for k, v in p.facets.items()) or "(no facets)"
            for p in project.profiles.values()
        )
        raise ProfileError(
            "No profile matches the facet query "
            f"({', '.join(f'{k}={v}' for k, v in facet_flags.items())}); "
            f"available facet combinations: {'; '.join(known) or '(none defined)'}"
        )
    if len(matches) > 1:
        overlap = ", ".join(name for name, _ in matches)
        raise ProfileError(
            "Facet query is ambiguous — matches multiple profiles: "
            f"{overlap}. Disambiguate with --litmus-profile=<name>."
        )
    name, _ = matches[0]
    merged = flatten_profile_chain(name, project)
    return name, merged, {**merged.facets, **facet_flags}


def collect_facet_flags_from_config(config, project: ProjectConfig) -> dict[str, str]:
    """Read user-provided facet flag values off ``config.option``."""
    values: dict[str, str] = {}
    for key in collect_profile_facet_keys(project):
        raw = config.getoption(facet_key_to_cli_flag(key), default=None)
        if raw:
            values[key] = str(raw)
    return values


def compose_filter_expr(profile_expr: str, cli_expr: str) -> str:
    """AND-compose a profile filter with any CLI-provided filter."""
    profile_expr = (profile_expr or "").strip()
    cli_expr = (cli_expr or "").strip()
    if not profile_expr:
        return cli_expr
    if not cli_expr:
        return profile_expr
    return f"({profile_expr}) and ({cli_expr})"


def install_active_profile(config) -> None:
    """Resolve profile (name and/or facets) and install it; compose filter options.

    ``keyword`` and ``markexpr`` are set on ``config.option`` **here**
    (not in ``pytest_collection_modifyitems``) so pytest's own ``-k`` /
    ``-m`` filter — which runs via its own modifyitems hook — sees them
    during deselection. Marker injection per node-id remains in
    ``pytest_collection_modifyitems`` because it depends on the item
    list that only exists at collection time.
    """
    project = load_project_defaults()
    profile_name = config.getoption("--litmus-profile", default=None)
    no_profile = config.getoption("--no-profile", default=False)
    facet_flags = collect_facet_flags_from_config(config, project)
    profile_name = resolve_default_profile(profile_name, facet_flags, no_profile, project)
    _, profile, facets = resolve_active_profile(profile_name, facet_flags, project)
    set_active_profile(profile)
    set_active_facets(facets)
    if profile is None:
        return
    runner = validate_pytest_runner(profile.runner)
    if runner.keyword:
        existing = getattr(config.option, "keyword", None) or ""
        config.option.keyword = compose_filter_expr(runner.keyword, existing)
    if runner.markexpr:
        existing = getattr(config.option, "markexpr", None) or ""
        config.option.markexpr = compose_filter_expr(runner.markexpr, existing)


def parse_flag_from_args(args, flag: str) -> str | None:
    """Scan ``args`` for ``--flag value`` or ``--flag=value`` and return the value."""
    for i, tok in enumerate(args):
        if tok == flag and i + 1 < len(args):
            return args[i + 1]
        if tok.startswith(f"{flag}="):
            return tok.split("=", 1)[1]
    return None


def apply_profile_addopts_env(args) -> None:
    """Apply ``profile.runner.addopts`` via ``PYTEST_ADDOPTS`` before collection.

    Setting ``PYTEST_ADDOPTS`` at this stage is the pytest-blessed path
    for injecting CLI tokens — equivalent to exporting the variable in
    the shell. Downstream plugins (pytest-rerunfailures, pytest-xdist,
    pytest-timeout) see the tokens during their own configure phase.
    Mutating ``config.option.*`` later is too fragile when plugins
    register their own option handlers.
    """
    # Scan args directly — our options aren't registered on early_config yet.
    profile_name = parse_flag_from_args(args, "--litmus-profile") or os.environ.get(
        "LITMUS_PROFILE"
    )
    no_profile = "--no-profile" in args

    project = load_project_defaults()
    facet_flags: dict[str, str] = {}
    for key in collect_profile_facet_keys(project):
        value = parse_flag_from_args(args, facet_key_to_cli_flag(key))
        if value:
            facet_flags[key] = value

    # Apply default_profile when nothing else selected one. We don't raise
    # here on missing default — pytest_configure surfaces that with a
    # cleaner traceback.
    if not profile_name and not facet_flags and not no_profile and project.default_profile:
        profile_name = project.default_profile

    if not profile_name and not facet_flags:
        return
    try:
        _, profile, _ = resolve_active_profile(profile_name, facet_flags, project)
    except ProfileError:
        # Let pytest_configure surface the error with a clean stacktrace.
        return
    if profile is None:
        return
    runner = validate_pytest_runner(profile.runner)
    if not runner.addopts:
        return
    existing = os.environ.get("PYTEST_ADDOPTS", "").strip()
    merged = f"{existing} {runner.addopts}".strip()
    os.environ["PYTEST_ADDOPTS"] = merged


def required_input_key_to_cli_flag(key: str) -> str:
    """Map a required-input key to its CLI flag form (``serial_number`` → ``--serial-number``)."""
    return f"--{key.replace('_', '-')}"


def required_input_key_to_env_var(key: str) -> str:
    """Map a required-input key to its env-var form.

    Example: ``serial_number`` → ``LITMUS_SERIAL_NUMBER``.
    """
    return f"LITMUS_{key.upper()}"


def resolve_required_inputs(
    project: ProjectConfig,
    config,
) -> dict[str, str]:
    """Resolve every declared ``required_inputs`` value at session start.

    For each declared key, the first source that yields a value wins:

    1. CLI flag ``--<key>`` (already registered by ``pytest_addoption``).
    2. Env var ``LITMUS_<KEY>``.
    3. Operator prompt via :func:`litmus.prompts.ask`, using the
       declared :class:`PromptConfig` (handler / auto-confirm / tty
       fall-through, then ``PromptUnavailableError``).

    Raises :class:`ProfileError` if a value cannot be resolved —
    fail-fast so a long sequence never runs with a missing serial.
    """
    if not project.required_inputs:
        return {}

    resolved: dict[str, str] = {}
    for key, prompt_config in project.required_inputs.items():
        flag = required_input_key_to_cli_flag(key)
        cli_value = config.getoption(flag, default=None)
        if cli_value:
            resolved[key] = str(cli_value)
            continue

        env_var = required_input_key_to_env_var(key)
        env_value = os.environ.get(env_var)
        if env_value:
            resolved[key] = env_value
            continue

        try:
            value = ask_prompt(prompt_config)
        except Exception as exc:
            raise ProfileError(
                f"Required input {key!r} not supplied: pass {flag}=<value>, "
                f"set {env_var}, or run interactively. ({exc})"
            ) from exc
        if value is None or value == "":
            raise ProfileError(
                f"Required input {key!r}: prompt returned no value. "
                f"Pass {flag}=<value> or set {env_var}."
            )
        resolved[key] = str(value)

    return resolved


def install_session_inputs(project: ProjectConfig, config) -> dict[str, str]:
    """Resolve ``required_inputs`` and stash them in the session ContextVar."""
    inputs = resolve_required_inputs(project, config)
    set_session_inputs(inputs)
    return inputs


def resolve_default_profile(
    profile_name: str | None,
    facet_flags: dict[str, str],
    no_profile: bool,
    project: ProjectConfig,
) -> str | None:
    """Apply ``default_profile`` semantics for bare-``pytest`` invocations.

    Returns the profile name to use, or ``None`` when no profile applies.
    Raises :class:`ProfileError` when profiles are declared but the
    operator didn't make a selection and no ``default_profile`` is set.
    """
    if profile_name or facet_flags or no_profile:
        return profile_name
    if not project.profiles:
        return None
    if project.default_profile:
        return project.default_profile
    combos = sorted(
        " ".join(f"{k}={v}" for k, v in p.facets.items()) or "(no facets)"
        for p in project.profiles.values()
    )
    raise ProfileError(
        "Profiles are declared in litmus.yaml but no profile was "
        "selected. Pass facet flags (e.g. --test-phase=production), "
        "--litmus-profile=<name>, --no-profile to skip, or set "
        "`default_profile:` in litmus.yaml.\n"
        f"  Available facet combinations: {'; '.join(combos)}"
    )


def resolve_test_phase(requested_phase: str | None, mocks_active: bool = False) -> str:
    """Resolve the ``test_phase`` data stamp.

    Demotes to ``"development"`` when the run cannot produce trustworthy
    data, regardless of what the operator requested:

    * Dirty git (or git unavailable) — code under test isn't recorded.
    * ``--mock-instruments`` active — measurements aren't real.

    Both demotions apply to the **data stamp only**. Profile selection
    reads the raw CLI facet value via :func:`collect_facet_flags_from_config`
    (unmodified), so ``--test-phase=production --mock-instruments`` on
    a dev checkout still applies the production profile for test
    execution (limits, markers, fixtures) — it just stamps the run
    ``test_phase='development'`` so dashboards ignore it.
    """
    # Lazy import: tests in test_phase_and_mocks.py patch
    # ``litmus.execution._git.is_git_clean`` — top-level binding here
    # would freeze the reference at import time and break the patch.
    from litmus.execution._git import is_git_clean

    if mocks_active:
        return "development"
    if not is_git_clean():
        return "development"
    return requested_phase or "development"
