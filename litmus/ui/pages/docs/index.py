"""Documentation landing page."""

from nicegui import ui

from litmus.ui.shared.layout import create_layout


@ui.page("/docs")
def docs_index():
    """Render the documentation landing page."""
    create_layout("Documentation")

    with ui.column().classes("w-full max-w-4xl mx-auto p-6 gap-8"):
        ui.markdown("# Litmus Documentation").classes("text-3xl font-bold text-slate-800")
        ui.markdown(
            "Python-native hardware test platform for the AI-assisted era."
        ).classes("text-lg text-slate-600")

        # Section cards following Diátaxis framework
        sections = [
            (
                "tutorial",
                "school",
                "Tutorial",
                "Engineer's First Project - progressive learning path"
                " from first test to production",
            ),
            (
                "integration",
                "sync_alt",
                "Integration",
                "Adopt Litmus with existing tests and infrastructure",
            ),
            (
                "concepts",
                "lightbulb",
                "Concepts",
                "Products, stations, capabilities, fixtures, and matching",
            ),
            (
                "guides",
                "integration_instructions",
                "How-To Guides",
                "Step-by-step guides for common tasks",
            ),
            (
                "reference",
                "api",
                "Reference",
                "MCP tools, HTTP endpoints, CLI, models, configuration",
            ),
            (
                "examples",
                "code",
                "Examples",
                "Complete working examples with full source",
            ),
        ]

        with ui.row().classes("gap-4 flex-wrap"):
            for section, icon, title, description in sections:
                with ui.card().classes(
                    "w-72 cursor-pointer hover:shadow-lg transition-shadow"
                ).on("click", lambda s=section: ui.navigate.to(f"/docs/{s}")):
                    with ui.card_section():
                        with ui.row().classes("items-center gap-3"):
                            ui.icon(icon).classes("text-blue-500 text-2xl")
                            ui.label(title).classes("text-lg font-semibold text-slate-800")
                        ui.label(description).classes("text-sm text-slate-500 mt-2")

        # Quick links
        ui.separator().classes("my-4")
        ui.markdown("## Quick Links").classes("text-xl font-semibold text-slate-800")

        with ui.row().classes("gap-6 flex-wrap"):
            with ui.column().classes("gap-2"):
                ui.markdown("**Get Started**").classes("font-medium")
                ui.link("Installation", "/docs/tutorial/01-first-test").classes(
                    "text-blue-600 hover:underline"
                )
                ui.link("Quick Start", "/docs/tutorial").classes(
                    "text-blue-600 hover:underline"
                )

            with ui.column().classes("gap-2"):
                ui.markdown("**Learn**").classes("font-medium")
                ui.link("Core Concepts", "/docs/concepts/overview").classes(
                    "text-blue-600 hover:underline"
                )
                ui.link("Writing Tests", "/docs/guides/writing-tests").classes(
                    "text-blue-600 hover:underline"
                )

            with ui.column().classes("gap-2"):
                ui.markdown("**Reference**").classes("font-medium")
                ui.link("API Reference", "/docs/reference/api").classes(
                    "text-blue-600 hover:underline"
                )
                ui.link("Configuration", "/docs/reference/configuration").classes(
                    "text-blue-600 hover:underline"
                )
