"""Generic documentation page renderer."""

import importlib.resources
import re
from pathlib import Path

from nicegui import ui


def _resolve_docs_dir() -> Path:
    """Locate the docs directory across wheel and editable installs.

    Wheel installs bundle the curated user-facing tiers at
    ``litmus/_docs/`` via the ``[tool.hatch.build.targets.wheel.force-include]``
    rules in ``pyproject.toml``. Editable / source installs don't get
    the bundle, so we fall back to the repo's ``docs/`` directory above
    ``src/litmus/``. If neither exists the downstream ``.exists()``
    checks in the route handlers 404 naturally.
    """
    pkg_root = Path(str(importlib.resources.files("litmus")))
    bundled = pkg_root / "_docs"
    if bundled.exists():
        return bundled
    return pkg_root.parent.parent / "docs"


DOCS_DIR = _resolve_docs_dir()

# Known documentation sections
KNOWN_SECTIONS = {"tutorial", "integration", "concepts", "how-to", "reference"}


def _get_section_title(section: str) -> str:
    """Get display title for a section."""
    titles = {
        "tutorial": "Tutorial",
        "integration": "Integration",
        "concepts": "Concepts",
        "how-to": "How-To Guides",
        "reference": "Reference",
    }
    return titles.get(section, section.replace("-", " ").title())


def _get_section_icon(section: str) -> str:
    """Get icon for a section."""
    icons = {
        "tutorial": "school",
        "integration": "sync_alt",
        "concepts": "lightbulb",
        "how-to": "integration_instructions",
        "reference": "api",
    }
    return icons.get(section, "article")


def _extract_title_from_markdown(content: str) -> str:
    """Extract the first heading from markdown content."""
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return "Untitled"


def _find_page_section(page_name: str) -> str | None:
    """Find which section contains a given page."""
    for section in KNOWN_SECTIONS:
        section_dir = DOCS_DIR / section
        if section_dir.exists():
            page_path = section_dir / f"{page_name}.md"
            if page_path.exists():
                return section
    return None


def _create_docs_layout(section: str | None = None, page: str | None = None):
    """Create docs layout with breadcrumb in header."""
    from litmus.ui.shared.layout import create_sidebar

    ui.add_head_html('<link rel="stylesheet" href="/static/global.css">')
    # Mermaid diagram rendering. NiceGUI strips the ``class="language-X"``
    # attribute on ``<code>`` elements, so we can't select by language hint.
    # Instead we sniff fenced code blocks by their content: the first non-
    # whitespace line of a mermaid block starts with one of the diagram-
    # type keywords (``flowchart``, ``erDiagram``, ``sequenceDiagram``,
    # ``stateDiagram``, ``classDiagram``, ``gantt``, ``pie``, ``journey``,
    # ``gitGraph``, ``mindmap``, ``timeline``).
    #
    # NiceGUI also injects content over WebSocket after ``DOMContentLoaded``,
    # so use a ``MutationObserver`` to catch blocks whenever they appear.
    # A ``WeakSet`` of processed blocks makes the operation idempotent.
    ui.add_head_html("""
        <style>
            div.mermaid {
                cursor: zoom-in; position: relative;
                padding: 12px; margin: 16px 0;
                background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px;
                text-align: center;
            }
            div.mermaid > svg {
                max-width: 100% !important;
                height: auto !important;
                display: inline-block;
            }
            div.mermaid:hover::after {
                content: "click to expand";
                position: absolute; top: 6px; right: 10px;
                font-size: 11px; color: #475569;
                background: rgba(255, 255, 255, 0.9);
                border: 1px solid #cbd5e1;
                padding: 2px 8px; border-radius: 4px;
                pointer-events: none;
            }
            .mermaid-overlay {
                position: fixed; inset: 0;
                background: rgba(15, 23, 42, 0.88);
                z-index: 9999; cursor: zoom-out;
                display: flex; align-items: center; justify-content: center;
                padding: 24px;
                overflow: auto;
            }
            .mermaid-overlay > svg {
                background: white; border-radius: 8px; padding: 24px;
                box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);
            }
            .mermaid-overlay-hint {
                position: fixed; bottom: 16px; left: 50%;
                transform: translateX(-50%);
                color: #cbd5e1; font-size: 12px;
                background: rgba(0,0,0,0.4); padding: 4px 10px; border-radius: 4px;
            }
        </style>
        <script type="module">
            import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';
            mermaid.initialize({ startOnLoad: false, theme: 'neutral' });
            window.mermaid = mermaid;

            const MERMAID_KEYWORDS = new RegExp(
                '^\\\\s*(?:%%[\\\\s\\\\S]*?%%\\\\s*)*' +
                '(flowchart|graph|erDiagram|sequenceDiagram|stateDiagram(-v2)?|' +
                'classDiagram|gantt|pie|journey|gitGraph|mindmap|timeline|' +
                'requirementDiagram|C4Context|C4Container|quadrantChart|' +
                'xychart-beta|sankey-beta|block-beta)\\\\b'
            );
            const seen = new WeakSet();

            function attachZoom(container) {
                container.addEventListener('click', () => {
                    const svg = container.querySelector('svg');
                    if (!svg) return;
                    const overlay = document.createElement('div');
                    overlay.className = 'mermaid-overlay';
                    const clone = svg.cloneNode(true);
                    // Strip mermaid's intrinsic width/height + its inline
                    // max-width cap so CSS can fully upscale via viewBox.
                    // preserveAspectRatio defaults to "xMidYMid meet" so
                    // contents fit without distortion.
                    clone.removeAttribute('width');
                    clone.removeAttribute('height');
                    clone.style.maxWidth = 'none';
                    clone.style.maxHeight = 'none';
                    clone.style.width = '92vw';
                    clone.style.height = '88vh';
                    overlay.appendChild(clone);
                    const hint = document.createElement('div');
                    hint.className = 'mermaid-overlay-hint';
                    hint.textContent = 'click anywhere to close • Esc';
                    overlay.appendChild(hint);
                    const close = () => {
                        overlay.remove();
                        document.removeEventListener('keydown', onKey);
                    };
                    const onKey = (e) => { if (e.key === 'Escape') close(); };
                    overlay.addEventListener('click', close);
                    document.addEventListener('keydown', onKey);
                    document.body.appendChild(overlay);
                });
            }

            function processMermaidBlocks(root) {
                const blocks = (root || document).querySelectorAll('pre > code');
                const newContainers = [];
                blocks.forEach((block) => {
                    if (seen.has(block)) return;
                    const text = block.textContent || '';
                    if (!MERMAID_KEYWORDS.test(text)) return;
                    seen.add(block);
                    const pre = block.parentElement;
                    const container = document.createElement('div');
                    container.className = 'mermaid';
                    container.textContent = text;
                    pre.replaceWith(container);
                    newContainers.push(container);
                });
                if (newContainers.length > 0) {
                    mermaid.run({ querySelector: 'div.mermaid:not([data-processed="true"])' })
                        .then(() => newContainers.forEach(attachZoom))
                        .catch((err) => console.warn('mermaid render failed', err));
                }
            }

            const observer = new MutationObserver((mutations) => {
                for (const m of mutations) {
                    for (const node of m.addedNodes) {
                        if (node.nodeType === 1) processMermaidBlocks(node);
                    }
                }
            });

            const start = () => {
                processMermaidBlocks(document);
                observer.observe(document.body, { childList: true, subtree: true });
            };

            if (document.readyState === 'loading') {
                document.addEventListener('DOMContentLoaded', start);
            } else {
                start();
            }
        </script>
    """)
    ui.query("body").classes("bg-slate-50")

    create_sidebar()

    # Header with breadcrumb
    with ui.header().classes("bg-white border-b border-slate-200 shadow-sm"):
        with ui.row().classes("gap-2 text-sm items-center"):
            ui.link("Docs", "/docs").classes("text-slate-500 hover:text-blue-600")
            if section:
                ui.icon("chevron_right").classes("text-xs text-slate-400")
                if page:
                    ui.link(_get_section_title(section), f"/docs/{section}").classes(
                        "text-slate-500 hover:text-blue-600"
                    )
                    ui.icon("chevron_right").classes("text-xs text-slate-400")
                    ui.label(page.replace("-", " ").title()).classes("text-slate-800 font-medium")
                else:
                    ui.label(_get_section_title(section)).classes("text-slate-800 font-medium")


def _parse_section_outline(section: str) -> list[tuple[str | None, list[tuple[str, str]]]]:
    """Parse ``<section>/index.md`` into an ordered tree of groups.

    Each group is ``(group_label, [(page_title, page_slug), ...])``. Group
    label is the H2 heading text under which the page links appear; ``None``
    means "links appear before any H2 in index.md."

    Pages that exist in the section but aren't referenced by index.md are
    appended at the end under an ``"Other"`` group so nothing is hidden.

    With no ``index.md`` present, returns a single unnamed group containing
    every page in alphabetical order (legacy behavior).
    """
    section_dir = DOCS_DIR / section
    all_pages = sorted(p.stem for p in section_dir.glob("*.md") if p.stem != "index")
    if not all_pages:
        return []

    index_path = section_dir / "index.md"
    if not index_path.exists():
        items = [(_page_title(section_dir / f"{slug}.md", slug), slug) for slug in all_pages]
        return [(None, items)]

    text = index_path.read_text()
    groups: list[tuple[str | None, list[tuple[str, str]]]] = []
    current_label: str | None = None
    current_items: list[tuple[str, str]] = []
    referenced: set[str] = set()

    link_re = re.compile(r"\[([^\]]+)\]\(([^)#?\s]+?)\.md\)")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            if current_items or current_label is not None:
                groups.append((current_label, current_items))
            current_label = stripped[3:].strip()
            current_items = []
            continue
        for match in link_re.finditer(line):
            title, target = match.group(1), match.group(2)
            slug = target.split("/")[-1]
            if slug in all_pages and slug not in referenced:
                current_items.append((title, slug))
                referenced.add(slug)

    if current_items or current_label is not None:
        groups.append((current_label, current_items))

    leftovers = [slug for slug in all_pages if slug not in referenced]
    if leftovers:
        items = [(_page_title(section_dir / f"{slug}.md", slug), slug) for slug in leftovers]
        groups.append(("Other", items))

    return [g for g in groups if g[1]]


def _page_title(path: Path, fallback_slug: str) -> str:
    try:
        return _extract_title_from_markdown(path.read_text())
    except OSError:
        return fallback_slug.replace("-", " ").title()


def _flatten_outline(outline: list[tuple[str | None, list[tuple[str, str]]]]) -> list[str]:
    """Return the slug order from a parsed outline — drives prev/next nav."""
    return [slug for _, items in outline for _, slug in items]


def _numeric_prefix(slug: str) -> str | None:
    """Return the leading numeric prefix of a doc slug, e.g. `00-quickstart` → `00`."""
    m = re.match(r"^(\d+)-", slug)
    return m.group(1) if m else None


def _render_sidebar_nav(section: str, current_page: str | None = None):
    """Render the section sidebar as a tree driven by ``index.md``."""
    outline = _parse_section_outline(section)
    if not outline:
        return

    with ui.column().classes("w-56 p-4 border-r border-slate-200 docs-sidebar bg-white"):
        ui.label(_get_section_title(section).upper()).classes(
            "text-xs text-slate-500 font-medium mb-2"
        )
        for group_label, items in outline:
            if group_label is not None:
                ui.label(group_label).classes(
                    "text-xs uppercase tracking-wide text-slate-400 font-semibold mt-3 mb-1"
                )
            for title, slug in items:
                is_current = slug == current_page
                prefix = _numeric_prefix(slug)
                # Active state: 2px blue left border + bluish bg + heavier text.
                # Matches the pragmatest sidebar (Phase I.4) so the two
                # renderers feel the same.
                base_pl = "pl-3" if group_label is not None else "pl-2"
                if is_current:
                    link_classes = (
                        f"text-sm py-1 {base_pl} pr-2 block border-l-2 border-blue-600 "
                        "bg-blue-50 text-blue-900 font-medium rounded-r no-underline"
                    )
                    badge_color = "text-blue-700"
                else:
                    link_classes = (
                        f"text-sm py-1 {base_pl} pr-2 block border-l-2 border-transparent "
                        "text-slate-700 hover:border-slate-300 hover:bg-slate-50 "
                        "hover:text-blue-600 rounded-r no-underline"
                    )
                    badge_color = "text-slate-400"
                with ui.link(target=f"/docs/{section}/{slug}").classes(link_classes):
                    with ui.row().classes("items-baseline gap-2 flex-nowrap"):
                        if prefix is not None:
                            ui.label(prefix).classes(f"text-xs font-mono {badge_color} shrink-0")
                        ui.label(title).classes("text-sm truncate")


def _render_doc_page_content(section: str, page: str):
    """Render the content of a documentation page (without layout)."""
    md_path = DOCS_DIR / section / f"{page}.md"

    with ui.element("div").classes("docs-layout"):
        # Sidebar navigation (sticky)
        _render_sidebar_nav(section, page)

        # Main content (scrolls with window)
        with ui.column().classes("docs-content p-6 max-w-4xl"):
            if md_path.exists():
                content = md_path.read_text()
                ui.markdown(
                    content,
                    extras=["fenced-code-blocks", "tables", "strike", "task_list"],
                ).classes("prose prose-slate max-w-none")

                # Next/prev navigation — prominent button cards so the next step is
                # the obvious continuation, not a footnote.
                outline = _parse_section_outline(section)
                ordered = _flatten_outline(outline)
                if page in ordered:
                    idx = ordered.index(page)
                    title_for = {slug: title for _, items in outline for title, slug in items}
                    prev_slug = ordered[idx - 1] if idx > 0 else None
                    next_slug = ordered[idx + 1] if idx < len(ordered) - 1 else None

                    btn_base = (
                        "block flex-1 p-4 border rounded-lg "
                        "bg-white border-slate-200 hover:border-blue-500 hover:shadow-md "
                        "transition no-underline"
                    )
                    with ui.row().classes("mt-12 pt-8 border-t border-slate-200 gap-4 w-full"):
                        if prev_slug:
                            with ui.link(target=f"/docs/{section}/{prev_slug}").classes(
                                btn_base + " text-left"
                            ):
                                ui.label("← Previous").classes(
                                    "text-xs uppercase tracking-wide text-slate-500"
                                )
                                ui.label(title_for[prev_slug]).classes(
                                    "text-lg font-semibold text-blue-700"
                                )
                        else:
                            ui.element("div").classes("flex-1")
                        if next_slug:
                            with ui.link(target=f"/docs/{section}/{next_slug}").classes(
                                btn_base + " text-right"
                            ):
                                ui.label("Next →").classes(
                                    "text-xs uppercase tracking-wide text-slate-500"
                                )
                                ui.label(title_for[next_slug]).classes(
                                    "text-lg font-semibold text-blue-700"
                                )
                        else:
                            ui.element("div").classes("flex-1")
            else:
                with ui.column().classes("gap-4"):
                    ui.icon("warning").classes("text-amber-500 text-4xl")
                    ui.label(f"Page not found: {section}/{page}").classes("text-lg text-slate-700")
                    ui.link(f"Back to {_get_section_title(section)}", f"/docs/{section}").classes(
                        "text-blue-600 hover:underline"
                    )


def _render_section_index_content(section: str):
    """Render the content of a section index page with the same sidebar as inner pages."""
    section_dir = DOCS_DIR / section

    with ui.element("div").classes("docs-layout"):
        # Same tree sidebar that inner pages get — keeps the section's nav chrome
        # consistent regardless of whether the reader is on the landing or a child page.
        _render_sidebar_nav(section, current_page=None)

        with ui.column().classes("docs-content p-6 max-w-4xl"):
            index_path = section_dir / "index.md"
            if index_path.exists():
                content = index_path.read_text()
                ui.markdown(
                    content,
                    extras=["fenced-code-blocks", "tables", "strike", "task_list"],
                ).classes("prose prose-slate max-w-none")
                return
            # Generate a section listing
            with ui.row().classes("items-center gap-3 mb-6"):
                ui.icon(_get_section_icon(section)).classes("text-blue-500 text-3xl")
                ui.label(_get_section_title(section)).classes("text-2xl font-bold text-slate-800")

            # List pages in section
            if section_dir.exists():
                pages = sorted(section_dir.glob("*.md"))
                for page_path in pages:
                    page_name = page_path.stem
                    if page_name == "index":
                        continue

                    try:
                        content = page_path.read_text()
                        title = _extract_title_from_markdown(content)
                        # Get first paragraph as description
                        lines = content.split("\n")
                        desc_lines = []
                        in_content = False
                        for line in lines:
                            if line.startswith("# "):
                                in_content = True
                                continue
                            if in_content and line.strip():
                                if line.startswith("#"):
                                    break
                                desc_lines.append(line.strip())
                                if len(desc_lines) >= 2:
                                    break
                        description = " ".join(desc_lines)[:200]
                    except OSError:
                        title = page_name.replace("-", " ").title()
                        description = ""

                    with (
                        ui.card()
                        .classes("w-full mb-3 cursor-pointer hover:shadow transition-shadow")
                        .on(
                            "click", lambda p=page_name, s=section: ui.navigate.to(f"/docs/{s}/{p}")
                        )
                    ):
                        with ui.card_section():
                            ui.label(title).classes("font-medium text-slate-800")
                            if description:
                                ui.label(description).classes(
                                    "text-sm text-slate-500 mt-1 line-clamp-2"
                                )
            else:
                ui.label(f"Section '{section}' not found.").classes("text-amber-600")


@ui.page("/docs/{section}")
def section_index(section: str):
    """Render a section index page."""
    # Strip .md extension if present (from markdown links)
    if section.endswith(".md"):
        section = section[:-3]

    # If this isn't a known section, it might be a page name from a relative link
    # (e.g., clicked "01-first-test.md" from /docs/tutorial → /docs/01-first-test.md)
    # Try to find which section contains this page and render it
    if section not in KNOWN_SECTIONS:
        found_section = _find_page_section(section)
        if found_section:
            # Render the page content with proper layout
            _create_docs_layout(found_section, section)
            _render_doc_page_content(found_section, section)
            return

    _create_docs_layout(section)
    _render_section_index_content(section)


@ui.page("/docs/{section}/{page}")
def doc_page(section: str, page: str):
    """Render a documentation page."""
    # Strip .md extension if present (from markdown links)
    if page.endswith(".md"):
        page = page[:-3]

    _create_docs_layout(section, page)
    _render_doc_page_content(section, page)
