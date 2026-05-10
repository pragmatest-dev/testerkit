"""Generic documentation page renderer."""

import importlib.resources
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
KNOWN_SECTIONS = {"tutorial", "integration", "concepts", "guides", "reference", "examples"}


def _get_section_title(section: str) -> str:
    """Get display title for a section."""
    titles = {
        "tutorial": "Tutorial",
        "integration": "Integration",
        "concepts": "Concepts",
        "guides": "How-To Guides",
        "reference": "Reference",
        "examples": "Examples",
    }
    return titles.get(section, section.replace("-", " ").title())


def _get_section_icon(section: str) -> str:
    """Get icon for a section."""
    icons = {
        "tutorial": "school",
        "integration": "sync_alt",
        "concepts": "lightbulb",
        "guides": "integration_instructions",
        "reference": "api",
        "examples": "code",
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
    # Add Mermaid.js for diagram rendering
    ui.add_head_html("""
        <script type="module">
            import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';
            mermaid.initialize({ startOnLoad: false, theme: 'neutral' });

            // Render mermaid diagrams after page load
            document.addEventListener('DOMContentLoaded', () => {
                setTimeout(() => {
                    // Find code blocks with language-mermaid class
                    document.querySelectorAll('pre code.language-mermaid')
                        .forEach((block, index) => {
                        const pre = block.parentElement;
                        const container = document.createElement('div');
                        container.className = 'mermaid';
                        container.textContent = block.textContent;
                        pre.replaceWith(container);
                    });
                    mermaid.run();
                }, 100);
            });
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


def _render_sidebar_nav(section: str, current_page: str | None = None):
    """Render sidebar navigation for the section."""
    section_dir = DOCS_DIR / section
    if not section_dir.exists():
        return

    # Get all markdown files in the section
    pages = sorted(section_dir.glob("*.md"))
    if not pages:
        return

    with ui.column().classes("w-56 p-4 border-r border-slate-200 docs-sidebar bg-white"):
        ui.label(_get_section_title(section).upper()).classes(
            "text-xs text-slate-500 font-medium mb-2"
        )
        for page_path in pages:
            page_name = page_path.stem
            if page_name == "index":
                continue  # Skip index in nav

            # Read first line as title
            try:
                content = page_path.read_text()
                title = _extract_title_from_markdown(content)
            except OSError:
                title = page_name.replace("-", " ").title()

            is_current = page_name == current_page
            link_classes = "text-sm py-1 block "
            if is_current:
                link_classes += "text-blue-600 font-medium"
            else:
                link_classes += "text-slate-600 hover:text-blue-600"

            ui.link(title, f"/docs/{section}/{page_name}").classes(link_classes)


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
                ui.markdown(content, extras=["fenced-code-blocks"]).classes(
                    "prose prose-slate max-w-none"
                )

                # Next/prev navigation
                section_dir = DOCS_DIR / section
                pages = sorted([p.stem for p in section_dir.glob("*.md") if p.stem != "index"])
                if page in pages:
                    idx = pages.index(page)
                    with ui.row().classes("mt-8 pt-6 border-t border-slate-200 gap-4"):
                        if idx > 0:
                            prev_page = pages[idx - 1]
                            ui.link(
                                f"← {prev_page.replace('-', ' ').title()}",
                                f"/docs/{section}/{prev_page}",
                            ).classes("text-blue-600 hover:underline")
                        ui.element("div").classes("flex-1")
                        if idx < len(pages) - 1:
                            next_page = pages[idx + 1]
                            ui.link(
                                f"{next_page.replace('-', ' ').title()} →",
                                f"/docs/{section}/{next_page}",
                            ).classes("text-blue-600 hover:underline")
            else:
                with ui.column().classes("gap-4"):
                    ui.icon("warning").classes("text-amber-500 text-4xl")
                    ui.label(f"Page not found: {section}/{page}").classes("text-lg text-slate-700")
                    ui.link(f"Back to {_get_section_title(section)}", f"/docs/{section}").classes(
                        "text-blue-600 hover:underline"
                    )


def _render_section_index_content(section: str):
    """Render the content of a section index page (without layout)."""
    section_dir = DOCS_DIR / section

    with ui.column().classes("w-full max-w-4xl mx-auto p-6"):
        # Check for index.md first
        index_path = section_dir / "index.md"
        if index_path.exists():
            content = index_path.read_text()
            ui.markdown(content, extras=["fenced-code-blocks"]).classes(
                "prose prose-slate max-w-none"
            )
        else:
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
