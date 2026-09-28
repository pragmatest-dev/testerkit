#!/usr/bin/env bash
# testerkit-server compatibility gate (docs/44 §1, testerkit-server's
# docs/44-single-source-model-and-projection.md: "[RULE] testerkit-server
# depends on testerkit and stays compatible with every testerkit change").
#
# Runs as a local pre-commit hook (see .pre-commit-config.yaml, id
# `server-compat`). If the staged commit touches a testerkit module that
# testerkit-server imports (per scripts/server_compat_modules.txt, kept
# current by scripts/generate_server_compat_list.py and checked for drift
# by tests/test_server_contract.py), this runs testerkit-server's targeted
# read-model / serving / parity suite against the CURRENT (staged) testerkit
# tree, not whatever testerkit-server's own pyproject.toml path dependency
# happens to point at.
#
# If the sibling testerkit-server checkout can't be found AND a gated
# module was touched, this FAILS the commit with a clear message. It never
# silently skips a check that should have run. (A commit that doesn't touch
# any gated module always no-ops, sibling or no sibling.)
#
# Override the sibling location with TESTERKIT_SERVER_DIR (e.g. when the
# checkout isn't named "testerkit-server", as with a worktree).
#
# Set SERVER_COMPAT_DRY_RUN=1 to stop right after the touched/not-touched
# decision (printed on stdout as "server-compat: decision=touched" or
# "decision=not-touched") without checking for the sibling checkout or
# running anything — this is what tests/test_server_compat_hook.py uses to
# exercise the module-selection logic in isolation, deterministically and
# without a network call or a real testerkit-server checkout.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
MODULE_LIST="${REPO_ROOT}/scripts/server_compat_modules.txt"
SERVER_DIR="${TESTERKIT_SERVER_DIR:-${REPO_ROOT}/../testerkit-server}"

if [[ ! -f "${MODULE_LIST}" ]]; then
  echo "server-compat: ${MODULE_LIST} is missing — regenerate with" >&2
  echo "  uv run python scripts/generate_server_compat_list.py" >&2
  exit 1
fi

mapfile -t STAGED < <(git diff --cached --name-only --diff-filter=ACMR)

touched=0
while IFS= read -r module; do
  [[ -z "${module}" || "${module}" == \#* ]] && continue
  path="src/${module//./\/}"
  for f in "${STAGED[@]}"; do
    if [[ "${f}" == "${path}.py" || "${f}" == "${path}/"* ]]; then
      touched=1
      break 2
    fi
  done
done < "${MODULE_LIST}"

if [[ "${touched}" -eq 0 ]]; then
  echo "server-compat: no staged file touches a module testerkit-server imports; skipping."
  echo "server-compat: decision=not-touched"
  exit 0
fi

echo "server-compat: staged change touches a testerkit-server-imported module."
echo "server-compat: decision=touched"

if [[ "${SERVER_COMPAT_DRY_RUN:-0}" == "1" ]]; then
  exit 0
fi

if [[ ! -d "${SERVER_DIR}" ]]; then
  cat >&2 <<EOF
server-compat: testerkit-server compatibility gate CANNOT RUN.

Expected a testerkit-server checkout at:
    ${TESTERKIT_SERVER_DIR:-${REPO_ROOT}/../testerkit-server}

This commit touches a module testerkit-server imports (docs/44 §1), so
this gate refuses to pass silently. Check out testerkit-server as a
sibling of this repo, or set TESTERKIT_SERVER_DIR to point at your
checkout, then retry the commit.
EOF
  exit 1
fi

echo "server-compat: running testerkit-server's targeted suite against this tree (${REPO_ROOT})..."

cd "${SERVER_DIR}"
# Pre-commit exports GIT_DIR/GIT_INDEX_FILE for the testerkit repo being
# committed. Anything below that shells out to git (tests included) must not
# inherit them, or it acts on testerkit's repo instead of its own directory.
unset GIT_DIR GIT_INDEX_FILE GIT_WORK_TREE GIT_COMMON_DIR GIT_PREFIX GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES
# --extra dev: pytest itself, plus sqlglot/pytest-rerunfailures that the
# targeted suite below imports at collection time, live in testerkit-server's
# `dev` optional-dependencies group, not its base dependencies.
uv sync --quiet --extra dev

# The server's pyproject.toml resolves testerkit via `path = "../testerkit"`,
# which is only guaranteed to BE this checkout when the two repos are true
# siblings. Force the install to point at this exact tree regardless of
# naming/layout, so the gate always tests what's actually staged. `uv pip`
# (not `uv run pip`) — a `uv`-managed venv has no `pip` binary installed in
# it by default, so `uv run pip` falls through to whatever `pip` is first
# on PATH (a system/externally-managed one on Debian-family hosts), which
# refuses to touch the venv at all.
uv pip install --quiet --no-deps -e "${REPO_ROOT}"

TARGETED_TESTS=(
  tests/test_run_object.py
  tests/test_run_rows_serving.py
  tests/test_run_rows_runs_and_lanes_serving.py
  tests/test_metrics_parity.py
  tests/test_dimensions_parity.py
  tests/test_facts_derive.py
  tests/test_facts_derive_routes.py
  tests/test_bq_schema.py
  tests/test_query_service.py
)

timeout 300 uv run pytest -q "${TARGETED_TESTS[@]}"
