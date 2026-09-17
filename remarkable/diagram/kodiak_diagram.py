#!/usr/bin/env python3
"""Shared primitives for Kodiak vault diagrams (`*-deps.svg`, `*-ports.svg`).

`build_deps_svg.py`, `build_ports_svg.py` and `validate_diagram_svg.py` all
import from here. That is the point: the advance ratio and the palette are the
two things a hand-edit can silently desync, so no side keeps its own copy.

The failure this exists to catch: an SVG's `textLength` pins the rendered
advance width so a font fallback cannot reflow the label. Edit the label text
by hand without recomputing `textLength` and the glyphs are *stretched or
squeezed to the stale width* rather than overflowing -- it still looks like a
diagram, just subtly wrong, which is quieter than the reflow bug the attribute
was added to prevent.
"""

from __future__ import annotations

# Kodiak / Bernese palette. A hex outside this map is ad-hoc and a finding --
# see Homelab/Brand.md. Pure #FFFFFF/#000000 are absent on purpose: rule 1 of
# that doc forbids them outright.
PALETTE: dict[str, str] = {
    "#ECE2D0": "--paper",
    "#F7F1E6": "--paper-warm",
    "#DFD4C0": "--paper-dark",
    "#1B1A18": "--ink",
    "#6B6358": "--bark",
    "#9A9183": "--stone",
    "#9D4A25": "--rust",
    "#161311": "--terminal-bg",
    "#6F8F5C": "--terminal-green",
    "#9BBD7E": "--terminal-green-bright",
    "#C9743F": "--ember",
    "#8D8377": "--ash",
}

BG, CARD, INK = "#ECE2D0", "#F7F1E6", "#1B1A18"
MUTED, FAINT, RUST = "#6B6358", "#9A9183", "#9D4A25"
DARK, GREEN = "#161311", "#6F8F5C"

# Monospace advance width, in em. These diagrams set a monospace font stack, so
# every glyph is exactly this fraction of the font size wide.
ADV = 0.6

FONT_STACK = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"

MARK_D = (
    "M 324.730469 203.519531 C 315.105469 193.894531 304.105469 191.144531 "
    "294.480469 192.519531 C 280.734375 193.894531 268.359375 207.644531 "
    "268.359375 225.519531 C 268.359375 246.140625 280.734375 265.390625 "
    "299.980469 273.640625 C 310.980469 279.140625 319.230469 276.390625 "
    "324.730469 272.265625 C 330.230469 276.390625 338.480469 279.140625 "
    "349.476562 273.640625 C 368.726562 265.390625 381.101562 246.140625 "
    "381.101562 225.519531 C 381.101562 207.644531 368.726562 193.894531 "
    "354.976562 192.519531 C 345.355469 191.144531 334.355469 193.894531 "
    "324.730469 203.519531 Z M 306.855469 225.519531 C 297.230469 222.769531 "
    "287.605469 228.269531 286.230469 239.265625 C 284.855469 248.890625 "
    "291.730469 254.390625 299.980469 251.640625 C 294.480469 247.515625 "
    "295.855469 237.890625 305.480469 233.769531 C 310.980469 231.019531 "
    "310.980469 226.894531 306.855469 225.519531 Z M 342.605469 225.519531 "
    "C 352.226562 222.769531 361.851562 228.269531 363.226562 239.265625 "
    "C 364.601562 248.890625 357.726562 254.390625 349.476562 251.640625 "
    "C 354.976562 247.515625 353.601562 237.890625 343.980469 233.769531 "
    "C 338.480469 231.019531 338.480469 226.894531 342.605469 225.519531 Z "
    "M 322.667969 272.265625 C 322.667969 261.265625 322.667969 244.765625 "
    "322.667969 231.019531 C 322.667969 226.894531 326.792969 226.894531 "
    "326.792969 231.019531 C 326.792969 244.765625 326.792969 261.265625 "
    "326.792969 272.265625 C 326.792969 275.015625 322.667969 275.015625 "
    "322.667969 272.265625 Z M 322.667969 272.265625 "
)
MARK_BBOX = (268.4229, 191.1147, 112.74, 88.0)  # x, y, w, h in the source artboard


def expected_text_length(text: str, font_size: float, adv: float = ADV) -> int:
    """The `textLength` a label of this string at this size must carry.

    Mirrors the generator's own `f"{len(s) * ADV * size:.0f}"` exactly, including
    its round-half-to-even behaviour -- `format` is used rather than `round()` so
    the two can never disagree on a .5 boundary.
    """
    return int(f"{len(text) * adv * font_size:.0f}")


# --- WCAG 2.1 contrast ------------------------------------------------------
# Brand.md: "Recompute if any hex changes; don't trust the table over the
# arithmetic." These two functions are that arithmetic, so a test can assert a
# ratio instead of a reviewer recalling one.

#: Minimum ratio for normal-size text (WCAG 2.1 AA, 1.4.3).
MIN_TEXT_RATIO = 4.5
#: Minimum for large text (>=24px, or >=19px bold) and non-text (1.4.11).
MIN_LARGE_RATIO = 3.0


def _channels(hexcolour: str) -> tuple[float, float, float]:
    h = hexcolour.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        raise ValueError(f"not a hex colour: {hexcolour!r}")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def relative_luminance(hexcolour: str) -> float:
    """WCAG 2.1 relative luminance of an sRGB hex colour."""
    r, g, b = (c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
               for c in _channels(hexcolour))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg: str, bg: str) -> float:
    """WCAG 2.1 contrast ratio between two hex colours. Order-independent."""
    a, b = relative_luminance(fg), relative_luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def escape(s: str) -> str:
    """XML-escape text content. Mirrored by `unescape` in the validator."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class Canvas:
    """Accumulates SVG elements on a fixed grid with locked text advances."""

    def __init__(self, width: int, height: int, title: str) -> None:
        self.width, self.height, self.title = width, height, title
        self.out: list[str] = []

    # -- primitives ---------------------------------------------------------
    def text(self, x, y, s, size, fill, anchor="middle", weight=None, lock=True):
        """lock=True pins the advance width so a font fallback can't reflow it."""
        w = f' font-weight="{weight}"' if weight else ""
        tl = ""
        if lock:
            tl = (f' textLength="{expected_text_length(s, size)}"'
                  f' lengthAdjust="spacingAndGlyphs"')
        self.out.append(
            f'  <text x="{x}" y="{y}" font-size="{size}" text-anchor="{anchor}"'
            f' fill="{fill}"{w}{tl}>{escape(s)}</text>'
        )

    def box(self, x, y, w, h, stroke, fill=CARD, sw=1, rx=6):
        self.out.append(
            f'  <rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}" />'
        )

    def card(self, x, y, width, lines, stroke, fill=CARD):
        """lines = [(text, size, fill)]; height derives from the lines, never guessed."""
        h = 16 + sum(size + 8 for _, size, _ in lines)
        self.box(x, y, width, h, stroke, fill)
        cy = y + 16
        for s, size, col in lines:
            cy += size
            self.text(x + width / 2, cy, s, size, col)
            cy += 8
        return h

    def elbow(self, x1, y1, x2, y2, color=RUST, label=None, label_fill=MUTED):
        """Down from the source, across a shared bus, then down into the target."""
        bus = y1 + (y2 - y1) / 2
        d = f"M {x1} {y1} V {bus} H {x2} V {y2}" if x1 != x2 else f"M {x1} {y1} V {y2}"
        self.out.append(
            f'  <path d="{d}" fill="none" stroke="{color}" stroke-width="1.5" '
            f'marker-end="url(#a)" />'
        )
        if label:
            self.text((x1 + x2) / 2, bus - 4, label, 9, label_fill)

    def harrow(self, x1, y1, x2, y2, color=RUST, label=None, label_fill=MUTED,
               jog="target"):
        """A left-to-right edge: run horizontally, jog vertically near the target,
        then enter it head-on.

        Architecture diagrams flow across the page, so the jog belongs at the
        END of the run rather than mid-span: it keeps the long horizontal
        segment free for the port/protocol label, which is the content. The
        label is centred on that run and sits above the line, never on a box
        edge -- the template calls overlap a layout bug, not a drawing mistake.
        """
        if jog == "target":
            # Long run at the SOURCE's y, short jog just before the target.
            jx = x2 - 10 if x2 > x1 else x2 + 10
            run_a, run_b, run_y = x1, jx, y1
        else:
            # Long run at the TARGET's y, jog immediately off the source. Edges
            # that share a start point (everything leaving one node) would
            # otherwise put every label on the same pixel and stack them.
            jx = x1 + 10 if x2 > x1 else x1 - 10
            run_a, run_b, run_y = jx, x2, y2
        d = (f"M {x1} {y1} H {jx} V {y2} H {x2}"
             if y1 != y2 else f"M {x1} {y1} H {x2}")
        self.out.append(
            f'  <path d="{d}" fill="none" stroke="{color}" stroke-width="1.5" '
            f'marker-end="url(#a)" />'
        )
        if label:
            self.text((run_a + run_b) / 2, run_y - 6, label, 9, label_fill)

    def mark(self, x, y, h, fill, opacity=1.0):
        """Kodiak mark, height-normalised, top-left anchored at (x, y)."""
        mx, my, _, mh = MARK_BBOX
        s = h / mh
        self.out.append(
            f'  <g transform="translate({x:.2f} {y:.2f}) scale({s:.5f}) '
            f'translate({-mx:.4f} {-my:.4f})" fill="{fill}" fill-rule="evenodd" '
            f'opacity="{opacity}"><path d="{MARK_D}"/></g>'
        )

    # -- document -----------------------------------------------------------
    def render(self) -> str:
        head = [
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="0 0 {self.width} {self.height}" '
            f'font-family="{FONT_STACK}" role="img">',
            f'  <title>{escape(self.title)}</title>',
            '  <defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" '
            'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
            f'<path d="M0,0 L10,5 L0,10 z" fill="{RUST}"/></marker></defs>',
            f'  <rect width="{self.width}" height="{self.height}" fill="{BG}" />',
        ]
        return "\n".join(head + self.out + ["</svg>"]) + "\n"
