#!/usr/bin/env python3
"""Build skill zip for Claude Desktop."""

import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SKILLS_DIR = REPO_ROOT / "litmus" / "skills"
OUTPUT_DIR = REPO_ROOT / "dist"


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    output_path = OUTPUT_DIR / "litmus-skills.zip"

    skill_name = "litmus-skills"
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in SKILLS_DIR.rglob("*"):
            if file.is_file() and "__pycache__" not in str(file):
                # Wrap in skill_name/ directory as Claude Desktop expects
                arcname = Path(skill_name) / file.relative_to(SKILLS_DIR)
                zf.write(file, arcname)
                print(f"  Added: {arcname}")

    print(f"\nCreated: {output_path}")
    print(f"Size: {output_path.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
