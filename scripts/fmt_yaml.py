#!/usr/bin/env python3
"""Format Litmus YAML files to enforce consistent style.

Works on any Litmus YAML: catalog, products, sequences, stations, fixtures.

Usage:
    python scripts/fmt_yaml.py                      # format all under catalog/
    python scripts/fmt_yaml.py catalog/keysight/    # format directory
    python scripts/fmt_yaml.py products/            # format products
    python scripts/fmt_yaml.py some_file.yaml       # single file
    python scripts/fmt_yaml.py --check              # check only, exit 1 if changes needed
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from litmus.config.fmt import format_file_inplace, format_file


def main():
    args = sys.argv[1:]
    check_only = "--check" in args
    args = [a for a in args if a != "--check"]

    target = Path(args[0]) if args else Path("catalog")

    if target.is_file():
        files = [target]
    else:
        files = sorted(target.rglob("*.yaml"))
        files = [f for f in files if not f.name.startswith("_") and ".variants." not in f.name]

    changed = 0
    for path in files:
        if check_only:
            original = path.read_text()
            formatted = format_file(path)
            if formatted != original:
                print(f"  needs formatting: {path}")
                changed += 1
        else:
            if format_file_inplace(path):
                print(f"  formatted: {path}")
                changed += 1

    total = len(files)
    if check_only:
        print(f"\n{changed}/{total} files need formatting")
        sys.exit(1 if changed else 0)
    else:
        print(f"\n{changed}/{total} files changed")


if __name__ == "__main__":
    main()
