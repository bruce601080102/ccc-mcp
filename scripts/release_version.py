"""Apply a manually chosen release version to a temporary checkout.

The release workflow calls this in test/build jobs and again before tagging the
versioned source commit. The tracked main branch is never rewritten by this script.
"""

import argparse
import re
from pathlib import Path

from packaging.version import InvalidVersion, Version

VERSION_FILE = Path(__file__).resolve().parents[1] / "src" / "ccc_mcp" / "__init__.py"
VERSION_LINE = re.compile(r'^__version__ = "[^"]+"$', re.MULTILINE)


def validate_version(value: str) -> str:
    try:
        parsed = Version(value)
    except InvalidVersion as exc:
        raise ValueError(f"Invalid release version: {value!r}") from exc
    if value != str(parsed) or parsed.local is not None or parsed.epoch:
        raise ValueError(
            f"Use a canonical public Python version without a v prefix (for example 0.3.0): {value!r}"
        )
    return value


def apply_version(path: Path, value: str) -> None:
    version = validate_version(value)
    source = path.read_text()
    updated, count = VERSION_LINE.subn(f'__version__ = "{version}"', source)
    if count != 1:
        raise ValueError(f"Expected exactly one __version__ assignment in {path}")
    if updated != source:
        path.write_text(updated)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="Version entered in the Release workflow form")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--path", type=Path, default=VERSION_FILE)
    args = parser.parse_args()
    if args.validate_only:
        validate_version(args.version)
    else:
        apply_version(args.path, args.version)
