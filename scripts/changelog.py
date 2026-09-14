"""Check a release tag and extract its changelog entry."""

import argparse
import re
import tomllib
from pathlib import Path


def release_notes(changelog: str, version: str, tag: str) -> str:
    if not re.fullmatch(r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", tag):
        raise ValueError("Release tag must be vX.Y.Z")
    if tag != f"v{version}":
        raise ValueError(f"Tag {tag} does not match project version {version}")
    entries = []
    for section in re.split(r"^## ", changelog, flags=re.MULTILINE)[1:]:
        heading, _, body = section.partition("\n")
        if re.fullmatch(rf"\[{re.escape(version)}\](?: - .+)?", heading.strip()):
            entries.append(body.strip())
    if len(entries) != 1 or not entries[0]:
        raise ValueError(f"Expected one nonempty changelog entry for [{version}]")
    return entries[0] + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True, help="Release tag (vX.Y.Z)")
    parser.add_argument("--output", type=Path, help="Write notes to a file instead of stdout")
    args = parser.parse_args()
    version = tomllib.loads(Path("pyproject.toml").read_text())["project"]["version"]
    try:
        notes = release_notes(Path("CHANGELOG.md").read_text(), version, args.tag)
    except ValueError as error:
        parser.exit(1, f"Changelog: {error}\n")
    if args.output:
        args.output.write_text(notes)
    else:
        print(notes, end="")


if __name__ == "__main__":
    main()
