"""Global data directory resolution.

Single source of truth for where TesterKit stores its data (events, runs,
channels, uploads). The default is the platform data directory
(``~/.local/share/testerkit`` on Linux, ``AppData/Local/testerkit`` on
Windows). A project ``testerkit.yaml`` can override this, but the
global default ensures all processes on a machine share the same
event bus without coordination.

The dir holds three subsystems — `events/` (durable WAL), `runs/`
(per-run parquet test results), `channels/` (time-series instrument
signals) — plus per-subsystem index DBs and lock/state files. Same
shape as PostgreSQL's ``data_directory`` (PGDATA): one dir, mixed
content (tables + WAL + indexes + state), all "data."

Resolution chain:

1. Explicit ``path`` argument (rare — tests, migration scripts)
2. ``testerkit.yaml`` in CWD ancestors → ``data_dir`` field (if set)
3. ``TESTERKIT_HOME`` environment variable
4. ``platformdirs.user_data_dir("testerkit")``

This module is also the chokepoint for the OTHER per-machine files that
live alongside ``data/`` under the same global home — ``machine_id``
(this machine's identity GUID), ``credentials`` (the secret auth token
``testerkit connect`` obtains), and ``config.yaml`` (the server URL that
same command persists) — see :func:`get_or_create_machine_id`,
:func:`save_credentials` / :func:`resolve_server_token`, and
:func:`save_server_url` / :func:`resolve_server_url`.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import platformdirs
from filelock import FileLock

# Sibling of the ``data`` subdir the global branch resolves below — the
# machine identity file lives at ``<global-home>/machine_id``, not inside
# ``data/``. See docs/_internal/explorations/paths-storage-and-dotfolder.md
# §15 for the design ("framework placement" of ``machine_id``).
_MACHINE_ID_FILENAME = "machine_id"

# Also siblings of ``machine_id`` under the same global home: the auth
# token ``testerkit connect`` obtains (a SEPARATE file — a secret, unlike
# the non-secret machine_id GUID) and the global config the same command
# persists the server URL to, so a bare ``testerkit forward`` can find both
# afterward with no flags or env vars.
_CREDENTIALS_FILENAME = "credentials"
_GLOBAL_CONFIG_FILENAME = "config.yaml"
_URL_ENV = "TESTERKIT_SERVER_URL"
_TOKEN_ENV = "TESTERKIT_TOKEN"


def _global_home() -> Path:
    """Resolve the global TesterKit home — same resolution ``resolve_data_dir()``
    uses for its own global-default branch (``TESTERKIT_HOME`` env var, else
    ``platformdirs.user_data_dir("testerkit")``), without appending ``data/``.

    Deliberately does NOT depend on the not-yet-built ``~/.testerkit``
    dotfolder migration (§15 of the exploration doc) — this is the current
    global home, the same one every other global-tier accessor uses today.
    """
    return Path(os.environ.get("TESTERKIT_HOME", platformdirs.user_data_dir("testerkit")))


def get_or_create_machine_id() -> str:
    """Return this machine's stable identity GUID, creating it on first use.

    A random **uuid4**, generated once per physical controller and shared by
    every project checkout and every installed TesterKit version on the box
    — an application-level GUID this accessor owns, deliberately NOT the
    systemd/OS ``/etc/machine-id``, and NOT derived from hostname or any
    other hardware signal. Distinct from ``station_id`` (the config-assigned
    test-station identity).

    Persisted at ``<global-home>/machine_id`` (a sibling of ``credentials``
    and of the ``data/`` dir under the same global home ``resolve_data_dir()``
    resolves to for its global-default branch). This is the one chokepoint —
    every call site that needs "which machine is this" (run-stamping,
    ``testerkit connect``, ``testerkit forward``) reads through here rather
    than re-implementing the read-or-generate logic.

    Creation is lazy, on first need, and NEVER baked into images: a
    golden/base image must blank (delete) ``machine_id`` before capture, the
    same way cloud AMI/golden-image practice blanks ``/etc/machine-id`` — the
    first process on a newly-cloned bench that calls this regenerates a
    fresh, correct GUID.

    Concurrent first-run safety: the read-check-create sequence is guarded by
    an OS-level ``FileLock`` (the same locking primitive
    ``data/_daemon_lifecycle.py`` uses), and the actual write is a temp-file +
    ``os.replace`` so a reader never observes a partially-written file. See
    ``docs/_internal/explorations/paths-storage-and-dotfolder.md`` §15.
    """
    home = _global_home()
    path = home / _MACHINE_ID_FILENAME

    existing = _read_machine_id(path)
    if existing is not None:
        return existing

    home.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(home / f"{_MACHINE_ID_FILENAME}.lock"), timeout=10)
    with lock:
        # Re-check inside the lock — another process may have created it
        # while we were waiting.
        existing = _read_machine_id(path)
        if existing is not None:
            return existing

        new_id = str(uuid.uuid4())
        tmp_path = path.with_name(f"{_MACHINE_ID_FILENAME}.tmp-{os.getpid()}")
        tmp_path.write_text(new_id, encoding="utf-8")
        os.replace(tmp_path, path)
        return new_id


def _read_machine_id(path: Path) -> str | None:
    """Read the persisted machine id, or ``None`` if the file doesn't exist yet."""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    return text or None


def save_credentials(*, token: str, org_id: str | None = None, org_name: str | None = None) -> None:
    """Persist this machine's cloud auth token to ``<global-home>/credentials``.

    Written by ``testerkit connect`` on a successful device-authorization
    exchange; read by ``testerkit forward`` (via :func:`resolve_server_token`)
    as its last-resort token source. A SEPARATE file from ``machine_id`` —
    the id is a stable, non-secret identity GUID generated locally, while
    this file holds a secret bearer token issued by the server, so it is
    written mode ``0600`` (owner read/write only) and must never be
    committed or logged. Atomic temp-file + ``os.replace`` write, same
    pattern as :func:`get_or_create_machine_id`.
    """
    home = _global_home()
    home.mkdir(parents=True, exist_ok=True)
    path = home / _CREDENTIALS_FILENAME
    payload = {"token": token, "org_id": org_id, "org_name": org_name}
    tmp_path = path.with_name(f"{_CREDENTIALS_FILENAME}.tmp-{os.getpid()}")
    fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    os.chmod(tmp_path, 0o600)  # belt-and-suspenders in case umask widened the open() mode
    os.replace(tmp_path, path)


def load_credentials() -> dict[str, str | None] | None:
    """Read the persisted credentials, or ``None`` if this machine has never
    run a successful ``testerkit connect``."""
    path = _global_home() / _CREDENTIALS_FILENAME
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def resolve_server_token(explicit: str | None = None) -> str | None:
    """Resolve the machine's server auth token.

    Precedence: explicit (``--token``) → ``$TESTERKIT_TOKEN`` → the
    credential store ``testerkit connect`` writes. Returns ``None`` if none
    of those resolve anything.
    """
    if explicit:
        return explicit
    env = os.environ.get(_TOKEN_ENV)
    if env:
        return env
    creds = load_credentials()
    return creds.get("token") if creds else None


def resolve_server_url(explicit: str | None = None) -> str | None:
    """Resolve the TesterKit cloud server URL.

    Precedence: explicit (``--url``) → ``$TESTERKIT_SERVER_URL`` → the current
    project's ``testerkit.yaml`` ``server.url`` → the global config
    ``testerkit connect`` persists on success. Returns ``None`` if none of
    those resolve anything.
    """
    if explicit:
        return explicit
    env = os.environ.get(_URL_ENV)
    if env:
        return env

    # Project testerkit.yaml `server.url` — same ancestor-walk lookup
    # resolve_data_dir() uses for `data_dir`, deliberately lazy-imported to
    # avoid a module-load cycle with testerkit.connect (see resolve_data_dir).
    try:
        from testerkit.connect import _find_project_config

        found = _find_project_config()
        if found and found[1].server.url:
            return found[1].server.url
    except (ImportError, AttributeError, FileNotFoundError):
        pass

    from testerkit.store import load_global_config

    return load_global_config(_global_home() / _GLOBAL_CONFIG_FILENAME).url


def save_server_url(url: str) -> None:
    """Persist ``url`` to the global config so a bare ``testerkit forward``
    finds it after a successful ``testerkit connect``."""
    from testerkit.store import load_global_config, save_global_config

    path = _global_home() / _GLOBAL_CONFIG_FILENAME
    config = load_global_config(path).model_copy(update={"url": url})
    save_global_config(config, path)


def resolve_data_dir(path: Path | str | None = None) -> Path:
    """Resolve the data directory.

    Most callers should pass no arguments — the global default is the
    right choice for nearly all cases.
    """
    if path is not None:
        d = Path(path)
        d.mkdir(parents=True, exist_ok=True)
        return d

    # Check project config (testerkit.yaml in CWD ancestors)
    try:
        from testerkit.connect import _find_project_config

        found = _find_project_config()
        if found:
            root, project = found
            if project.data_dir:
                d = root / project.data_dir
                d.mkdir(parents=True, exist_ok=True)
                return d
    except (ImportError, AttributeError, FileNotFoundError):
        pass

    # Global default
    home = Path(os.environ.get("TESTERKIT_HOME", platformdirs.user_data_dir("testerkit")))
    d = home / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d
