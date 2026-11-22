"""Bundled user guides, loaded as package data.

The Markdown files live next to this module so that they are included in the
wheel and available from the GUI Help menu after installation.
"""

from __future__ import annotations

from importlib.resources import files

GUIDES = {
    "measurement": "measurement.md",
    "calibration": "calibration.md",
}


def load_guide(name: str) -> str:
    """Return the Markdown text of a bundled guide."""
    try:
        filename = GUIDES[name]
    except KeyError as exc:
        raise ValueError(f"unknown guide: {name}") from exc
    return files(__name__).joinpath(filename).read_text(encoding="utf-8")
