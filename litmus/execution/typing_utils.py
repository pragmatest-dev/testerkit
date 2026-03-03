"""Utilities for resolving and applying type annotations to test functions.

Used by ``litmus new-test`` (scaffold) and ``litmus update-types`` (backfill).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path


def resolve_role_types(
    station_instruments: dict,
) -> dict[str, tuple[str, str]]:
    """Map instrument roles to (module_path, class_name) from driver strings.

    Args:
        station_instruments: Mapping of role name to instrument config objects
            (must have a ``.driver`` attribute).

    Returns:
        Dict of role -> (module_path, class_name) for roles that have a driver.
        Roles with ``driver=None`` are omitted.
    """
    result: dict[str, tuple[str, str]] = {}
    for role, config in station_instruments.items():
        driver = config.driver
        if not driver:
            continue
        parts = driver.rsplit(".", 1)
        if len(parts) == 2:
            result[role] = (parts[0], parts[1])
    return result


def _find_litmus_test_functions(source: str) -> list[dict]:
    """Parse source and find @litmus_test decorated functions.

    Returns list of dicts with keys:
        - name: function name
        - lineno: 1-based line number of the ``def`` line
        - params: list of dicts with keys ``name``, ``annotation`` (str or None)
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    results = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        # Check for @litmus_test decorator
        is_litmus = False
        for dec in node.decorator_list:
            if isinstance(dec, ast.Name) and dec.id == "litmus_test":
                is_litmus = True
            elif isinstance(dec, ast.Call):
                func = dec.func
                if isinstance(func, ast.Name) and func.id == "litmus_test":
                    is_litmus = True
                elif isinstance(func, ast.Attribute) and func.attr == "litmus_test":
                    is_litmus = True
        if not is_litmus:
            continue

        params = []
        for arg in node.args.args:
            annotation = None
            if arg.annotation:
                annotation = ast.unparse(arg.annotation)
            params.append({"name": arg.arg, "annotation": annotation})

        results.append({
            "name": node.name,
            "lineno": node.lineno,
            "params": params,
        })

    return results


def _find_existing_imports(source: str) -> set[str]:
    """Find all imported names in source (e.g. 'Keithley2000')."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.names:
            for alias in node.names:
                names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def compute_type_edits(
    source: str,
    role_types: dict[str, tuple[str, str]],
) -> tuple[str, list[str]]:
    """Compute edits to add type annotations and imports to a source file.

    Args:
        source: Python source code.
        role_types: Mapping of role name -> (module_path, class_name).

    Returns:
        Tuple of (new_source, list_of_change_descriptions).
    """
    functions = _find_litmus_test_functions(source)
    if not functions:
        return source, []

    existing_imports = _find_existing_imports(source)

    # Collect needed annotations: param_name -> (module, class)
    needed_annotations: dict[str, tuple[str, str]] = {}
    changes: list[str] = []

    for func in functions:
        for param in func["params"]:
            pname = param["name"]
            if pname == "context":
                continue
            if param["annotation"] is not None:
                continue
            if pname in role_types:
                needed_annotations[pname] = role_types[pname]
                changes.append(f"  {func['name']}({pname}: {role_types[pname][1]})")

    if not changes:
        return source, []

    lines = source.split("\n")

    # 1. Add type annotations to function signatures
    for func in functions:
        for param in func["params"]:
            pname = param["name"]
            if pname == "context" or param["annotation"] is not None:
                continue
            if pname not in role_types:
                continue
            _, class_name = role_types[pname]

            # Find the line containing this parameter in the function def
            # Handle single-line and multi-line signatures
            def_lineno = func["lineno"] - 1  # 0-based
            # Search from def line onward for the parameter
            for i in range(def_lineno, min(def_lineno + 20, len(lines))):
                line = lines[i]
                # Match the parameter name as a word boundary, not already annotated
                pattern = rf'\b({re.escape(pname)})\s*([,\):])'
                match = re.search(pattern, line)
                if match:
                    # Check it's not already annotated (colon after name)
                    if match.group(2) == ':':
                        break
                    # Replace with annotated version
                    replacement = rf'\1: {class_name}\2'
                    lines[i] = re.sub(pattern, replacement, line, count=1)
                    break

    # 2. Add import lines for new types
    imports_to_add: dict[str, set[str]] = {}  # module -> {class_names}
    for _, (module, cls) in needed_annotations.items():
        if cls not in existing_imports:
            imports_to_add.setdefault(module, set()).add(cls)

    if imports_to_add:
        import_lines = []
        for module in sorted(imports_to_add):
            classes = sorted(imports_to_add[module])
            import_lines.append(f"from {module} import {', '.join(classes)}")

        # Find insertion point: after last import, before first non-import/non-blank
        insert_idx = 0
        in_imports = False
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")):
                in_imports = True
                insert_idx = i + 1
            elif stripped.startswith("__") or stripped.startswith("@") or (
                stripped.startswith("def ") or stripped.startswith("class ")
            ):
                if in_imports:
                    break
            elif stripped == "" and in_imports:
                # Allow blank lines between imports
                continue
            elif stripped.startswith(("#", '"""', "'''")):
                continue
            elif in_imports and stripped:
                break

        # Insert imports with a blank line separator
        for j, imp_line in enumerate(import_lines):
            lines.insert(insert_idx + j, imp_line)
        # Add blank line after if next line isn't blank
        after_idx = insert_idx + len(import_lines)
        if after_idx < len(lines) and lines[after_idx].strip():
            lines.insert(after_idx, "")

    return "\n".join(lines), changes


def scan_test_files(
    test_dir: Path,
    role_types: dict[str, tuple[str, str]],
) -> list[tuple[Path, str, list[str]]]:
    """Scan test files and compute type annotation edits.

    Args:
        test_dir: Directory to scan for test files.
        role_types: Mapping of role name -> (module_path, class_name).

    Returns:
        List of (file_path, new_source, change_descriptions) for files needing changes.
    """
    results = []
    for py_file in sorted(test_dir.rglob("*.py")):
        source = py_file.read_text()
        if "litmus_test" not in source:
            continue
        new_source, changes = compute_type_edits(source, role_types)
        if changes:
            results.append((py_file, new_source, changes))
    return results
