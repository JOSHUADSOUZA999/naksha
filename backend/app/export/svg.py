"""Minimal SVG for looking at a Layout.

Not the DXF/PDF export of the v1 scope — this exists so a person can answer the one
question steps ①–⑦ were built to reach: *does this read as a house?* Walls, names,
areas, north. Nothing that would survive a plan-approval office.
"""

from __future__ import annotations

from xml.sax.saxutils import escape

from app.ir.layout import Layout

_SCALE = 44          # px per metre
_MARGIN = 56         # px around the plan, for the title and the compass
_TITLE_SIZE = 15     # px; the sheet is widened to fit the title, see `render`
_FILLS = {
    "hall": "#eef2f7", "dining": "#eef2f7", "kitchen": "#fdf1e3",
    "master_bedroom": "#f0f4ec", "bedroom": "#f0f4ec",
    "bathroom": "#e8f1f5", "wc": "#e8f1f5",
    "pooja": "#f7efe0", "car_parking": "#f1f1f1",
    "corridor": "#fafafa", "foyer": "#fafafa", "staircase": "#ededf3",
}


def render(layout: Layout, kinds: dict[str, str], title: str = "") -> str:
    """One floor as standalone SVG. `kinds` maps room id to `SpaceKind` value.

    y is flipped on the way out: the IR runs y north, SVG runs y down the page. Doing
    it here rather than in the solver keeps north-is-up a presentation concern.
    """
    width_m = layout.x_max_m - layout.x_min_m
    depth_m = layout.y_max_m - layout.y_min_m
    h = depth_m * _SCALE + _MARGIN * 2
    # The canvas has to clear the title as well as the plan. A narrow plot with a long
    # brief was clipping its own heading mid-word: the drawing was right and the sheet
    # was too small for it. 0.52em a character is an over-estimate for this font, and
    # over is the safe direction — the cost is white space, not a lost word.
    w = max(width_m * _SCALE, len(title) * _TITLE_SIZE * 0.52) + _MARGIN * 2

    def px(x_m: float, y_m: float) -> tuple[float, float]:
        return (
            _MARGIN + (x_m - layout.x_min_m) * _SCALE,
            _MARGIN + (layout.y_max_m - y_m) * _SCALE,   # flip: north is up
        )

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.0f}" height="{h:.0f}" '
        f'viewBox="0 0 {w:.0f} {h:.0f}" font-family="ui-sans-serif,system-ui,sans-serif">',
        f'<rect width="{w:.0f}" height="{h:.0f}" fill="#ffffff"/>',
    ]
    if title:
        out.append(
            f'<text x="{_MARGIN}" y="30" font-size="{_TITLE_SIZE}" '
            f'fill="#111">{escape(title)}</text>'
        )

    for room in layout.rooms:
        x, y = px(room.x_min_m, room.y_max_m)     # SVG rect anchors at top-left
        kind = kinds.get(room.room_id, "")
        fill = _FILLS.get(kind, "#f6f6f6")
        out.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{room.width_m * _SCALE:.1f}" '
            f'height="{room.depth_m * _SCALE:.1f}" fill="{fill}" '
            f'stroke="#222" stroke-width="2"/>'
        )
        cx, cy = px(*room.centroid)
        label = escape(kind.replace("_", " ") or room.room_id)
        # Labels are dropped rather than overflowed on rooms too small to hold them —
        # a bathroom with its name spilling across the kitchen is worse than unlabelled.
        if room.width_m * _SCALE > 52 and room.depth_m * _SCALE > 26:
            out.append(
                f'<text x="{cx:.1f}" y="{cy - 3:.1f}" font-size="11" text-anchor="middle" '
                f'fill="#111">{label}</text>'
                f'<text x="{cx:.1f}" y="{cy + 11:.1f}" font-size="9.5" '
                f'text-anchor="middle" fill="#666">{room.area_sq_m:.1f} m²</text>'
            )

    out.append(
        f'<g stroke="#111" stroke-width="1.6" fill="none">'
        f'<path d="M{w - 34:.0f} {h - 52:.0f} v-26 m-7 8 l7 -8 l7 8"/></g>'
        f'<text x="{w - 34:.0f}" y="{h - 36:.0f}" font-size="10" text-anchor="middle" '
        f'fill="#111">N</text>'
    )
    out.append(
        f'<text x="{_MARGIN}" y="{h - 18:.0f}" font-size="10" fill="#888">'
        f'{width_m:.2f} × {depth_m:.2f} m · schematic, not a sanction drawing</text>'
    )
    out.append("</svg>")
    return "\n".join(out)
