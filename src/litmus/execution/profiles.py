"""Profile resolution + session phase demotion.

Two responsibilities live here:

* **Profile resolution.** Load ``litmus.yaml``, auto-synthesize a
  ``--<facet>`` flag per declared facet key, resolve the active profile
  from name / facets / both, walk any ``extends:`` chain parent-first,
  install the merged :class:`ProfileConfig` on the ``_active_profile``
  ContextVar, and compose ``pytest.keyword`` / ``pytest.markexpr``
  filters.
* **Test-phase demotion** (:func:`resolve_test_phase`). The data-stamp
  phase demotes to ``"development"`` on dirty git or active mocks so
  untrustworthy runs never stamp production. Profile selection reads
  the raw CLI value — only the data stamp is rewritten.

Lived in ``plugin.py`` originally; extracted to keep the pytest hook
file focused on hook registration.
"""

from __future__ import annotations

import os

import pytest

from litmus.config.test_config import ClassMarkers, MarkerSpec, TestMarkers
from litmus.execution._state import (
    set_active_facets,
    set_active_profile,
    set_session_inputs,
)
from litmus.models.project import ProfileConfig, ProfilePytest, ProjectConfig
from litmus.prompts import ask as ask_prompt


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


def flatten_profile_chain(leaf_name: str, project: ProjectConfig) -> ProfileConfig:
    """Walk ``extends`` chain parent-first and merge into a single profile.

    The leaf profile is the starting point; each ``extends`` link walks one
    step further up the chain. The merge is parent-first so child values
    win on conflicts — same rule as stacked pytest decorators.

    Raises ``pytest.UsageError`` on an unknown parent or a cycle.
    """
    chain: list[ProfileConfig] = []
    visited: list[str] = []
    current: str | None = leaf_name
    while current is not None:
        if current in visited:
            cycle = " -> ".join(visited + [current])
            raise pytest.UsageError(f"Cyclic profile extends chain: {cycle}")
        profile = project.profiles.get(current)
        if profile is None:
            if not visited:
                known = ", ".join(sorted(project.profiles)) or "(none defined)"
                raise pytest.UsageError(f"Unknown profile {current!r}; known profiles: {known}")
            raise pytest.UsageError(f"Profile {visited[-1]!r} extends unknown profile {current!r}")
        visited.append(current)
        chain.append(profile)
        current = profile.extends
    chain.reverse()

    description: str | None = None
    merged_facets: dict[str, str] = {}
    addopts_parts: list[str] = []
    markexpr: str | None = None
    keyword: str | None = None
    merged_markers: list[MarkerSpec] = []
    merged_classes: dict[str, ClassMarkers] = {}
    merged_tests: dict[str, TestMarkers] = {}
    for profile in chain:
        if profile.description is not None:
            description = profile.description
        merged_facets.update(profile.facets)
        if profile.pytest.addopts:
            addopts_parts.append(profile.pytest.addopts)
        if profile.pytest.markexpr is not None:
            markexpr = profile.pytest.markexpr
        if profile.pytest.keyword is not None:
            keyword = profile.pytest.keyword
        merged_markers.extend(profile.markers)
        for cls_name, cls_block in profile.classes.items():
            existing = merged_classes.get(cls_name)
            if existing is None:
                merged_classes[cls_name] = ClassMarkers(markers=list(cls_block.markers))
            else:
                existing.markers.extend(cls_block.markers)
        for test_name, test_block in profile.tests.items():
            existing_t = merged_tests.get(test_name)
            if existing_t is None:
                merged_tests[test_name] = TestMarkers(markers=list(test_block.markers))
            else:
                existing_t.markers.extend(test_block.markers)

    return ProfileConfig(
        description=description,
        facets=merged_facets,
        extends=None,
        pytest=ProfilePytest(
            addopts=" ".join(addopts_parts) or None,
            markexpr=markexpr,
            keyword=keyword,
        ),
        markers=merged_markers,
        classes=merged_classes,
        tests=merged_tests,
    )


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
            raise pytest.UsageError(
                f"Unknown --litmus-profile={profile_name!r}; known profiles: {known}"
            )
        if facet_flags:
            mismatches = [
                f"--{k.replace('_', '-')}={v!r} (profile declares {k}={profile.facets.get(k)!r})"
                for k, v in facet_flags.items()
                if profile.facets.get(k) != v
            ]
            if mismatches:
                raise pytest.UsageError(
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
        raise pytest.UsageError(
            "No profile matches the facet query "
            f"({', '.join(f'{k}={v}' for k, v in facet_flags.items())}); "
            f"available facet combinations: {'; '.join(known) or '(none defined)'}"
        )
    if len(matches) > 1:
        overlap = ", ".join(name for name, _ in matches)
        raise pytest.UsageError(
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
    if profile.pytest.keyword:
        existing = getattr(config.option, "keyword", None) or ""
        config.option.keyword = compose_filter_expr(profile.pytest.keyword, existing)
    if profile.pytest.markexpr:
        existing = getattr(config.option, "markexpr", None) or ""
        config.option.markexpr = compose_filter_expr(profile.pytest.markexpr, existing)


def parse_flag_from_args(args, flag: str) -> str | None:
    """Scan ``args`` for ``--flag value`` or ``--flag=value`` and return the value."""
    for i, tok in enumerate(args):
        if tok == flag and i + 1 < len(args):
            return args[i + 1]
        if tok.startswith(f"{flag}="):
            return tok.split("=", 1)[1]
    return None


def apply_profile_addopts_env(args) -> None:
    """Apply ``profile.pytest.addopts`` via ``PYTEST_ADDOPTS`` before collection.

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
    except pytest.UsageError:
        # Let pytest_configure surface the error with a clean stacktrace.
        return
    if profile is None or not profile.pytest.addopts:
        return
    existing = os.environ.get("PYTEST_ADDOPTS", "").strip()
    merged = f"{existing} {profile.pytest.addopts}".strip()
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

    Raises :class:`pytest.UsageError` if a value cannot be resolved —
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
            raise pytest.UsageError(
                f"Required input {key!r} not supplied: pass {flag}=<value>, "
                f"set {env_var}, or run interactively. ({exc})"
            ) from exc
        if value is None or value == "":
            raise pytest.UsageError(
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
    Raises :class:`pytest.UsageError` when profiles are declared but the
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
    raise pytest.UsageError(
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
    from litmus.execution._git import is_git_clean

    if mocks_active:
        return "development"
    if not is_git_clean():
        return "development"
    return requested_phase or "development"
