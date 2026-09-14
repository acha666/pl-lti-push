import runpy
from pathlib import Path

import pytest

release_notes = runpy.run_path(Path(__file__).parents[1] / "scripts/changelog.py")["release_notes"]

PREPARED = """# Changelog

## [Unreleased]

## [0.1.0] - 2026-09-13

### Added

- Current release.

## [0.0.1] - 2026-09-01

### Fixed

- Older release.
"""


def test_extracts_only_selected_release():
    assert release_notes(PREPARED, "0.1.0", "v0.1.0") == "### Added\n\n- Current release.\n"


def test_allows_pending_changes_and_unrelated_history():
    text = PREPARED.replace("[Unreleased]", "[Unreleased]\n- Pending change.")
    text = text.replace("[0.0.1] - 2026-09-01", "Older notes")
    assert release_notes(text, "0.1.0", "v0.1.0") == "### Added\n\n- Current release.\n"


@pytest.mark.parametrize(
    ("text", "tag", "message"),
    [
        (PREPARED, "v0.1.1", "does not match"),
        (PREPARED, "v0.1.0-rc.1", "must be vX.Y.Z"),
        ("## [Unreleased]\n", "v0.1.0", "one nonempty"),
        (PREPARED.replace("[0.0.1]", "[0.1.0]"), "v0.1.0", "one nonempty"),
        (PREPARED.replace("### Added\n\n- Current release.", ""), "v0.1.0", "one nonempty"),
    ],
)
def test_rejects_invalid_release(text, tag, message):
    with pytest.raises(ValueError, match=message):
        release_notes(text, "0.1.0", tag)
