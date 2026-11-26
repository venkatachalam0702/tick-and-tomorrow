"""Channel look: colours and fonts shared by diagrams, cards and thumbnails."""
from __future__ import annotations

from pathlib import Path

W, H = 1920, 1080

COLORS = {
    "bg1": "#13234A",   # navy (top)
    "bg2": "#060B1A",   # deep navy (bottom)
    "ink": "#FFFFFF",
    "sub": "#AAB6D3",
    "warm": "#FFB547",  # amber
    "cool": "#2EE6C5",  # teal
    "red": "#FF6B6B",   # only for "hot" / warning highlights
    "blue": "#6FA8FF",  # only for "cold" highlights
}


def color(name: str | None, default: str = "ink") -> str:
    if name and name.startswith("#") and len(name) in (4, 7):
        return name
    return COLORS.get((name or default).lower(), COLORS[default])


def font_path(bold: bool = True) -> str:
    """DejaVu Sans ships with matplotlib, so it exists on every runner."""
    import matplotlib

    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    return str(Path(matplotlib.get_data_path()) / "fonts" / "ttf" / name)
