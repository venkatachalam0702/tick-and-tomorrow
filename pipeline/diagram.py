"""Draw a diagram from a small JSON layout in the channel's style (matplotlib, CPU).

Gemini writes the layout; code draws it, so labels are always crisp and spelled right.
Coordinates: x from 0 (left) to 16 (right), y from 0 (bottom) to 9 (top).

Compact form (what the planner writes): every element is {type, at: [numbers], label, color, fill}.
  box     at = [x, y, w, h]       circle at = [x, y, r]
  arrow   at = [x1, y1, x2, y2]   wave   at = [x1, y1, x2, y2]
  line    at = [x1, y1, x2, y2, x3, y3, ...]
  text    at = [x, y]  (label = the text)
  label   at = [x, y, target_x, target_y]  (a callout with a leader line)
Missing sizes get sensible defaults, so a half-filled element still draws.

Long form (also accepted):
  box     x, y, w, h, label, color, fill(bool)        (x, y = bottom-left corner)
  circle  x, y, r, label, color, fill(bool)            (x, y = centre)
  arrow   from [x, y], to [x, y], label, color, curve(float, optional)
  line    points [[x, y], ...], color, dashed(bool)
  wave    from [x, y], to [x, y], amplitude, cycles, color
  text    x, y, text, size (s|m|l|xl), color, align (left|center|right)
  label   x, y, text, target [x, y], color   (callout text with a thin leader line)
Colours: ink, sub, warm (amber), cool (teal), red (hot), blue (cold), or #RRGGBB.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import numpy as np

from .brand import COLORS, H, W, color

SIZES = {"s": 20, "m": 26, "l": 34, "xl": 48}


class DiagramError(ValueError):
    pass


def _pt(v, name) -> tuple[float, float]:
    try:
        x, y = float(v[0]), float(v[1])
    except Exception as e:  # noqa: BLE001
        raise DiagramError(f"{name} must be [x, y], got {v!r}") from e
    return min(max(x, -0.5), 16.5), min(max(y, -0.5), 9.5)


def _num(el, key, default=None):
    v = el.get(key, default)
    if v is None:
        raise DiagramError(f"{el.get('type')} needs '{key}'")
    try:
        return float(v)
    except (TypeError, ValueError) as e:
        raise DiagramError(f"{el.get('type')}.{key} must be a number, got {v!r}") from e


DEFAULTS = {"w": 3.0, "h": 1.4, "r": 1.0}


def normalize(el: dict) -> dict:
    """Turn the compact {type, at: [...]} form into the long form; long-form keys win if both are given."""
    el = dict(el or {})
    t = str(el.get("type") or "").lower().strip()
    el["type"] = t
    at = el.pop("at", None)
    if isinstance(at, (list, tuple)):
        try:
            nums = [float(v) for v in at]
        except (TypeError, ValueError) as e:
            raise DiagramError(f"{t}.at must be numbers, got {at!r}") from e
        if t == "box":
            keys = ("x", "y", "w", "h")
        elif t == "circle":
            keys = ("x", "y", "r")
        elif t in ("text", "label"):
            keys = ("x", "y")
        else:
            keys = ()
        for k, v in zip(keys, nums):
            el.setdefault(k, v)
        if t in ("arrow", "wave") and len(nums) >= 4:
            el.setdefault("from", nums[0:2])
            el.setdefault("to", nums[2:4])
        if t == "line" and len(nums) >= 4:
            el.setdefault("points", [nums[i:i + 2] for i in range(0, len(nums) - 1, 2)])
        if t == "label" and len(nums) >= 4:
            el.setdefault("target", nums[2:4])
    if t in ("text", "label") and not el.get("text") and el.get("label"):
        el["text"] = el["label"]
    if t == "label" and not el.get("target") and "x" in el and "y" in el:
        el["target"] = [el["x"], el["y"]]  # no leader line target: point at itself
    return el


def _wrap(text: str, width: int) -> str:
    """Wrap at spaces only: never split a word ("Hydrog-en") across lines."""
    if not text:
        return ""
    return "\n".join(textwrap.wrap(str(text), width=max(6, width), break_long_words=False, break_on_hyphens=False))


PX_PER_UNIT = W / 16
PX_PER_PT = 100 / 72


def _fit(text: str, w: float, h: float, max_size: int = SIZES["m"], min_size: int = 15):
    """Largest font size (<= max_size) at which text, wrapped at spaces, fits a w x h area (grid units).

    Returns (wrapped_text, size), or None if it does not fit even at min_size.
    """
    text = str(text or "").strip()
    if not text:
        return "", max_size
    for size in range(max_size, min_size - 1, -1):
        em = size * PX_PER_PT
        chars = int(w * PX_PER_UNIT / (0.62 * em))
        if chars < 1 or max(len(x) for x in text.split()) > chars:
            continue
        lines = textwrap.wrap(text, width=chars, break_long_words=False, break_on_hyphens=False)
        # A single line may fill a thin bar (e.g. a turntable); several lines need line spacing.
        if (0.8 if len(lines) == 1 else 1.25 * len(lines)) * em <= h * PX_PER_UNIT:
            return "\n".join(lines), size
    return None


def _anchors(elements: list[dict]) -> list[tuple[int, float, float]]:
    """(index, x, y) of each shape's centre, to spot shapes drawn inside a box."""
    pts = []
    for i, el in enumerate(elements):
        try:
            if el["type"] == "box":
                pts.append((i, float(el["x"]) + float(el.get("w", DEFAULTS["w"])) / 2,
                            float(el["y"]) + float(el.get("h", DEFAULTS["h"])) / 2))
            elif el["type"] in ("circle", "text"):
                pts.append((i, float(el["x"]), float(el["y"])))
            elif el["type"] in ("arrow", "wave"):
                (x1, y1), (x2, y2) = el["from"], el["to"]
                pts.append((i, (float(x1) + float(x2)) / 2, (float(y1) + float(y2)) / 2))
        except (KeyError, TypeError, ValueError):
            continue
    return pts


def render(spec: dict, out: Path) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import patches

    elements = spec.get("elements") or []
    if not elements:
        raise DiagramError("diagram has no elements")
    if len(elements) > 60:
        raise DiagramError("too many elements (max 60); simplify the diagram")

    fig = plt.figure(figsize=(W / 100, H / 100), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 16)
    ax.set_ylim(0, 9)
    ax.axis("off")
    # Vertical navy gradient background.
    grad = np.linspace(0, 1, 256).reshape(-1, 1)
    from matplotlib.colors import LinearSegmentedColormap

    cmap = LinearSegmentedColormap.from_list("bg", [COLORS["bg2"], COLORS["bg1"]])
    ax.imshow(grad, extent=[0, 16, 0, 9], aspect="auto", cmap=cmap, origin="lower", zorder=0)

    if spec.get("title"):
        ax.text(0.6, 8.4, str(spec["title"]), color=COLORS["ink"], fontsize=40, fontweight="bold",
                va="center", ha="left", zorder=10)
        ax.plot([0.6, 2.6], [7.85, 7.85], color=COLORS["warm"], lw=5, solid_capstyle="round", zorder=10)

    norm = []
    for i, raw in enumerate(elements):
        try:
            norm.append(normalize(raw))
        except DiagramError as e:
            plt.close(fig)
            raise DiagramError(f"element {i + 1}: {e}") from e
    anchors = _anchors(norm)

    for i, el in enumerate(norm):
        t = el["type"]
        c = color(el.get("color"), "cool" if t in ("arrow", "wave") else "ink")
        lw = float(el.get("width", 4))
        try:
            if t == "box":
                x, y = _num(el, "x"), _num(el, "y")
                w, h = _num(el, "w", DEFAULTS["w"]), _num(el, "h", DEFAULTS["h"])
                fill = el.get("fill", False)
                ax.add_patch(patches.FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.18",
                                                    lw=lw, edgecolor=c, facecolor=c if fill else "none",
                                                    alpha=0.9 if fill else 1, zorder=3))
                if el.get("label"):
                    inside = any(j != i and x < ax_ < x + w and y < ay < y + h for j, ax_, ay in anchors)
                    txt_c = COLORS["bg2"] if fill else c
                    if inside:  # a container: label along its top edge so it does not hit what is inside
                        fit = _fit(el["label"], w - 0.4, 0.9) or (_wrap(el["label"], 30), 15)
                        ax.text(x + w / 2, y + h - 0.2, fit[0], ha="center", va="top", color=txt_c,
                                fontsize=fit[1], fontweight="bold", zorder=4)
                    else:
                        fit = _fit(el["label"], w - 0.3, max(h - 0.2, h * 0.9))
                        if fit:
                            ax.text(x + w / 2, y + h / 2, fit[0], ha="center", va="center", color=txt_c,
                                    fontsize=fit[1], fontweight="bold", zorder=4)
                        else:  # too small to hold its label: write it underneath
                            ax.text(x + w / 2, y - 0.15, _wrap(el["label"], 22), ha="center", va="top", color=c,
                                    fontsize=SIZES["s"], fontweight="bold", zorder=4)
            elif t == "circle":
                x, y, r = _num(el, "x"), _num(el, "y"), _num(el, "r", DEFAULTS["r"])
                fill = el.get("fill", False)
                ax.add_patch(patches.Circle((x, y), r, lw=lw, edgecolor=c, facecolor=c if fill else "none",
                                            alpha=0.9 if fill else 1, zorder=3))
                if el.get("label"):
                    fit = _fit(el["label"], r * 1.45, r * 1.45)  # the square inside the circle
                    if fit:
                        ax.text(x, y, fit[0], ha="center", va="center", color=COLORS["bg2"] if fill else c,
                                fontsize=fit[1], fontweight="bold", zorder=4)
                    else:  # small circle: label underneath
                        ax.text(x, y - r - 0.15, _wrap(el["label"], 18), ha="center", va="top", color=c,
                                fontsize=SIZES["s"], fontweight="bold", zorder=4)
            elif t == "arrow":
                a, b = _pt(el.get("from"), "arrow.from"), _pt(el.get("to"), "arrow.to")
                curve = float(el.get("curve", 0))
                ax.add_patch(patches.FancyArrowPatch(a, b, arrowstyle="-|>", mutation_scale=34, lw=lw, color=c,
                                                     connectionstyle=f"arc3,rad={curve}", zorder=5))
                if el.get("label"):
                    mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
                    if abs(b[0] - a[0]) < abs(b[1] - a[1]):  # mostly vertical: label beside it
                        ax.text(mx + 0.3, my, _wrap(el["label"], 16), ha="left", va="center", color=c,
                                fontsize=SIZES["s"], fontweight="bold", zorder=6)
                    else:
                        ax.text(mx, my + 0.35, _wrap(el["label"], 22), ha="center", va="bottom", color=c,
                                fontsize=SIZES["s"], fontweight="bold", zorder=6)
            elif t == "line":
                pts = [_pt(p, "line.points") for p in el.get("points") or []]
                if len(pts) < 2:
                    raise DiagramError("line needs at least 2 points")
                xs, ys = zip(*pts)
                ax.plot(xs, ys, color=c, lw=lw, ls="--" if el.get("dashed") else "-", zorder=2)
            elif t == "wave":
                a, b = _pt(el.get("from"), "wave.from"), _pt(el.get("to"), "wave.to")
                amp, cycles = float(el.get("amplitude", 0.4)), float(el.get("cycles", 4))
                s = np.linspace(0, 1, 400)
                dx, dy = b[0] - a[0], b[1] - a[1]
                length = (dx * dx + dy * dy) ** 0.5 or 1
                nx, ny = -dy / length, dx / length
                off = amp * np.sin(2 * np.pi * cycles * s)
                ax.plot(a[0] + dx * s + nx * off, a[1] + dy * s + ny * off, color=c, lw=lw, zorder=4)
            elif t == "text":
                x, y = _num(el, "x"), _num(el, "y")
                size = SIZES.get(str(el.get("size", "m")).lower(), SIZES["m"])
                ax.text(x, y, _wrap(el.get("text", ""), 48), ha=el.get("align", "center"), va="center",
                        color=c, fontsize=size, fontweight="bold" if size >= SIZES["l"] else "normal", zorder=7)
            elif t == "label":
                x, y = _num(el, "x"), _num(el, "y")
                tx, ty = _pt(el.get("target"), "label.target")
                ax.annotate(_wrap(el.get("text", ""), 24), xy=(tx, ty), xytext=(x, y), color=c,
                            fontsize=SIZES["s"], fontweight="bold", ha="center", va="center", zorder=8,
                            arrowprops=dict(arrowstyle="-", color=COLORS["sub"], lw=2))
            else:
                raise DiagramError(f"unknown element type '{t}'")
        except DiagramError as e:
            plt.close(fig)
            raise DiagramError(f"element {i + 1}: {e}") from e

    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=100, facecolor=COLORS["bg2"])
    plt.close(fig)
    return out


# Compact, all-required shape: small models fill this reliably (the long form with
# a dozen optional keys let them drop sizes and positions).
DIAGRAM_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "title": {"type": "STRING"},
        "elements": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "type": {"type": "STRING", "enum": ["box", "circle", "arrow", "line", "wave", "text", "label"]},
                    "at": {"type": "ARRAY", "items": {"type": "NUMBER"}},
                    "label": {"type": "STRING"},
                    "color": {"type": "STRING", "enum": ["ink", "sub", "warm", "cool", "red", "blue"]},
                    "fill": {"type": "BOOLEAN"},
                },
                "required": ["type", "at", "label", "color"],
                "propertyOrdering": ["type", "at", "label", "color", "fill"],
            },
        },
    },
    "required": ["title", "elements"],
    "propertyOrdering": ["title", "elements"],
}
