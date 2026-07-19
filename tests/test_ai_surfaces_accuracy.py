"""Anti-drift guard for the AI-facing test-writing surfaces (#66).

The 12 `testerkit-*` skills (`src/testerkit/skills/<name>/SKILL.md`) tell a
generative AI how to use TesterKit. They drifted from the real API once (a
phantom `logger.measure` verb, `psu`/`dmm` assumed without a station,
limit-less `verify`, sidecar `ref:`/`vectors:`/dict-`mocks:`, deleted
`sequence` refs, a since-deleted `testerkit refs` CLI, dead doc citations) — and
nothing caught it because nothing *ran* or *resolved* the examples.

This test runs / validates the canonical snippets against the real plugin +
models, and structurally validates every skill file (frontmatter, dead
tokens, cited doc paths), so a verb rename, a schema change, or a stale
citation breaks CI instead of silently shipping broken advice to users.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import testerkit

_SKILLS = Path(testerkit.__file__).parent / "skills"
_MCP = Path(testerkit.__file__).parent / "mcp"

_SKILL_NAME_RE = re.compile(r"^testerkit-[a-z-]+$")

_EXPECTED_SKILL_NAMES = {
    "testerkit-tests",
    "testerkit-mocks",
    "testerkit-stations",
    "testerkit-parts",
    "testerkit-profiles",
    "testerkit-sites",
    "testerkit-capture",
    "testerkit-data",
    "testerkit-analysis",
    "testerkit-debug",
    "testerkit-interactive",
    "testerkit-datasheets",
}

_SKILL_DIRS = sorted(p for p in _SKILLS.iterdir() if p.is_dir() and (p / "SKILL.md").exists())


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Minimal frontmatter parser: only needs flat `key: value` lines."""
    assert text.startswith("---\n"), "SKILL.md must start with a frontmatter block"
    end = text.index("\n---", 4)
    block = text[4:end]
    out: dict[str, str] = {}
    for line in block.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            out[key.strip()] = value.strip()
    return out


# ── The verbs the surfaces name must actually exist ────────────────────────


def test_record_only_verbs_exist() -> None:
    """observe/measure/stream are the real record-only verbs; there is no
    `logger` object (the docs' old `logger.measure` was always a phantom)."""
    from testerkit import verbs

    assert set(verbs.__all__) == {"observe", "verify", "measure", "stream"}
    assert not hasattr(testerkit, "logger")


# ── The zero-config examples from the skills must RUN ──────────────────────


def test_generated_claude_md_zero_config_verify(verify) -> None:
    """`verify` with an inline limit — the documented no-config example."""
    verify("output_voltage", 3.3, limit={"low": 3.0, "high": 3.6, "unit": "V"})


def test_generated_claude_md_zero_config_observe(observe) -> None:
    """`observe` — record-only, no limit, no station, no part spec."""
    observe("rail_voltage", 3.28)


# ── The sidecar shapes the skills document must validate ───────────────────


def test_canonical_sidecar_shapes_validate() -> None:
    """The shapes shown in testerkit-tests / testerkit-mocks / testerkit-datasheets
    must pass SidecarConfig (extra=forbid) — catches a drift back to `ref:`,
    `vectors:`, or dict-`mocks:`."""
    from testerkit.models.test_config import SidecarConfig

    SidecarConfig.model_validate(
        {
            "sweeps": [{"vin": [4.5, 5.0, 5.5]}],
            "limits": {
                "output_voltage": {"characteristic": "output_voltage", "guardband_pct": 10},
                "efficiency": {"low": 55, "high": 100, "unit": "%"},
            },
            "mocks": [{"target": "dmm.measure_dc_voltage", "return_value": 5.0}],
        }
    )


def test_guardband_sidecar_shape_validates() -> None:
    """The guardband form (`{characteristic: X, guardband_pct: N}`, testerkit-tests
    §4 and testerkit-parts) must pass both the sidecar-level model and the
    per-measurement limit model directly — catches a drift in either the
    field names or their nesting under `limits:`."""
    from testerkit.models.test_config import MeasurementLimitConfig, SidecarConfig

    shape = {"characteristic": "rail_voltage", "guardband_pct": 5}

    MeasurementLimitConfig.model_validate(shape)
    SidecarConfig.model_validate({"limits": {"rail_voltage": shape}})


# ── Targeted guards for the specific stale tokens this branch removed ─────


def test_no_phantom_logger_measure_in_surfaces() -> None:
    for md in _SKILLS.rglob("*.md"):
        assert "logger.measure" not in md.read_text(), f"phantom verb resurfaced in {md.name}"


def test_no_deleted_sequence_schema_refs_in_mcp() -> None:
    for py in _MCP.glob("*.py"):
        text = py.read_text()
        assert 'yaml_type="sequence"' not in text, f"deleted 'sequence' schema ref in {py.name}"
        assert "yaml_type='sequence'" not in text, f"deleted 'sequence' schema ref in {py.name}"


# ── Every skill dir is spec-valid ───────────────────────────────────────────


def test_exactly_eleven_skills_named_correctly() -> None:
    names = {p.name for p in _SKILL_DIRS}
    assert names == _EXPECTED_SKILL_NAMES, (
        f"skill dirs drifted from the expected 12: missing={_EXPECTED_SKILL_NAMES - names}, "
        f"extra={names - _EXPECTED_SKILL_NAMES}"
    )
    for name in names:
        assert _SKILL_NAME_RE.match(name), f"skill dir {name!r} doesn't match ^testerkit-[a-z-]+$"


@pytest.mark.parametrize("skill_dir", _SKILL_DIRS, ids=lambda p: p.name)
def test_skill_md_is_spec_valid(skill_dir: Path) -> None:
    """Every `SKILL.md` has frontmatter `name` == its dir name, a non-empty
    `description`, and a body under 500 lines (skills are meant to be read
    in full by an agent, not skimmed)."""
    md = skill_dir / "SKILL.md"
    text = md.read_text()
    frontmatter = _parse_frontmatter(text)

    assert frontmatter.get("name") == skill_dir.name, (
        f"{md}: frontmatter name {frontmatter.get('name')!r} != dir name {skill_dir.name!r}"
    )
    assert frontmatter.get("description"), f"{md}: frontmatter description is empty or missing"

    line_count = len(text.splitlines())
    assert line_count < 500, f"{md}: {line_count} lines, must be < 500"


# ── No dead tokens anywhere under skills/ ───────────────────────────────────

_DEAD_TOKEN_PATTERNS: dict[str, re.Pattern[str]] = {
    "deleted 'testerkit refs' CLI": re.compile(r"testerkit refs\b"),
    "phantom 'logger' fixture": re.compile(r"\blogger\b"),
    "removed in_* prefixed column": re.compile(r"\bin_[a-z][a-z0-9_]*\b"),
    "removed out_* prefixed column": re.compile(r"\bout_[a-z][a-z0-9_]*\b"),
    "pre-rename 'slot' terminology": re.compile(r"\bslot\b"),
    "deleted monolith skill name": re.compile(r"name:\s*testerkit-skills\b"),
}


def _all_skill_md_files() -> list[Path]:
    files: list[Path] = []
    for skill_dir in _SKILL_DIRS:
        files.append(skill_dir / "SKILL.md")
        refs_dir = skill_dir / "references"
        if refs_dir.exists():
            files.extend(sorted(refs_dir.rglob("*.md")))
    return files


_ALL_SKILL_MD = _all_skill_md_files()


@pytest.mark.parametrize("md", _ALL_SKILL_MD, ids=lambda p: str(p.relative_to(_SKILLS)))
def test_no_dead_tokens_in_skill_files(md: Path) -> None:
    text = md.read_text()
    for label, pattern in _DEAD_TOKEN_PATTERNS.items():
        m = pattern.search(text)
        assert m is None, f"{md.relative_to(_SKILLS)}: {label} (found {m and m.group(0)!r})"


# ── Every cited `testerkit docs show <path>` must resolve ──────────────────────

_DOCS_SHOW_RE = re.compile(r"testerkit docs show\s+([a-zA-Z0-9_/-]+)")


def _cited_doc_paths() -> list[tuple[Path, str]]:
    cites: list[tuple[Path, str]] = []
    for md in _ALL_SKILL_MD:
        for m in _DOCS_SHOW_RE.finditer(md.read_text()):
            cites.append((md, m.group(1)))
    return cites


_CITED_DOC_PATHS = _cited_doc_paths()


@pytest.mark.parametrize(
    "md_and_path",
    _CITED_DOC_PATHS,
    ids=lambda t: f"{t[0].relative_to(_SKILLS)}::{t[1]}",
)
def test_cited_doc_paths_resolve(md_and_path: tuple[Path, str]) -> None:
    """Every `testerkit docs show <path>` cited from a skill must resolve to a
    real shipped doc page, via the same resolver `testerkit docs show` itself
    uses (not a hardcoded `testerkit/_docs` guess) — so a skill citing a
    nonexistent page fails CI instead of dead-ending an agent at runtime."""
    from testerkit.cli.docs_cmd import KNOWN_SECTIONS, _docs_dir

    md, path = md_and_path
    root = _docs_dir()
    rel = path if path.endswith(".md") else f"{path}.md"
    top_section = rel.split("/", 1)[0]
    doc_path = root / rel

    assert top_section in KNOWN_SECTIONS, (
        f"{md.relative_to(_SKILLS)} cites {path!r} — top-level section "
        f"{top_section!r} isn't a known docs section {KNOWN_SECTIONS}"
    )
    assert doc_path.exists(), (
        f"{md.relative_to(_SKILLS)} cites nonexistent doc page: {path!r} (resolved {doc_path})"
    )


def test_at_least_one_doc_citation_found() -> None:
    """Sanity check on the scanner itself: skills reference shipped docs
    heavily — if this drops to zero, the citation regex broke, not the docs."""
    assert len(_CITED_DOC_PATHS) > 5
