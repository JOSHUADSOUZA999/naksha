"""Minimal SVG for looking at a Layout.

Not the DXF/PDF export of the v1 scope — this exists so a person can answer the one
question steps ①–⑦ were built to reach: *does this read as a house?* Walls, names,
areas, north. Nothing that would survive a plan-approval office.
"""

from __future__ import annotations

from xml.sax.saxutils import escape

from app.ir.layout import Layout
from app.ir.enums import Facing, OpeningKind
from app.ir.refined import RefinedFloor

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


def render(
    layout: Layout,
    kinds: dict[str, str],
    title: str = "",
    refined: RefinedFloor | None = None,
) -> str:
    """One floor as standalone SVG. `kinds` maps room id to `SpaceKind` value.

    Without `refined` this draws the tiling: rooms as outlined rectangles, which is
    what stage ⑤ produces and all there was to show before ⑥ existed. With it, the
    room outlines give way to real walls and the openings in them — the same
    geometry a DXF export would carry, drawn rather than invented here.

    y is flipped on the way out: the IR runs y north, SVG runs y down the page. Doing
    it here rather than in the solver keeps north-is-up a presentation concern.
    """
    # The sheet has to cover anything drawn outside the buildable rectangle — a car
    # porch in the setback sits beyond every bound `layout` knows about, and a canvas
    # sized to the house alone would simply clip it off the page.
    x_min_m, y_min_m = layout.x_min_m, layout.y_min_m
    x_max_m, y_max_m = layout.x_max_m, layout.y_max_m
    for outside in (refined.outside if refined else ()):
        x_min_m, y_min_m = min(x_min_m, outside.x_min_m), min(y_min_m, outside.y_min_m)
        x_max_m, y_max_m = max(x_max_m, outside.x_max_m), max(y_max_m, outside.y_max_m)

    width_m = x_max_m - x_min_m
    depth_m = y_max_m - y_min_m
    h = depth_m * _SCALE + _MARGIN * 2
    # The canvas has to clear the title as well as the plan. A narrow plot with a long
    # brief was clipping its own heading mid-word: the drawing was right and the sheet
    # was too small for it. 0.52em a character is an over-estimate for this font, and
    # over is the safe direction — the cost is white space, not a lost word.
    w = max(width_m * _SCALE, len(title) * _TITLE_SIZE * 0.52) + _MARGIN * 2

    def px(x_m: float, y_m: float) -> tuple[float, float]:
        return (
            _MARGIN + (x_m - x_min_m) * _SCALE,
            _MARGIN + (y_max_m - y_m) * _SCALE,          # flip: north is up
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
        # No outline when walls are coming: a 2 px stroke on the tile plus a 23 px
        # wall drawn over it reads as a double line, and the tile edge is the wall's
        # *centreline*, so the two do not even coincide.
        edge = '' if refined else ' stroke="#222" stroke-width="2"'
        out.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{room.width_m * _SCALE:.1f}" '
            f'height="{room.depth_m * _SCALE:.1f}" fill="{fill}"{edge}/>'
        )
        cx, cy = px(*room.centroid)
        # The area inside the plaster, once walls exist. Stage ⑤'s figure runs to the
        # centrelines and is the right number for the tiling and the wrong one to show
        # a person — it credits every room with half a wall on each side.
        area = room.area_sq_m
        if refined is not None:
            area = refined.clear_area_sq_m(room.room_id) or area
        label = escape(kind.replace("_", " ") or room.room_id)
        # Labels are dropped rather than overflowed on rooms too small to hold them —
        # a bathroom with its name spilling across the kitchen is worse than unlabelled.
        if room.width_m * _SCALE > 52 and room.depth_m * _SCALE > 26:
            out.append(
                f'<text x="{cx:.1f}" y="{cy - 3:.1f}" font-size="11" text-anchor="middle" '
                f'fill="#111">{label}</text>'
                f'<text x="{cx:.1f}" y="{cy + 11:.1f}" font-size="9.5" '
                f'text-anchor="middle" fill="#666">{area:.1f} m²</text>'
            )

    if refined is not None:
        for outside in refined.outside:
            # Dashed and unfilled: a porch is a slab and a roof, not a room. Drawing it
            # like one would claim built-up area the plan does not have.
            ox, oy = px(outside.x_min_m, outside.y_max_m)
            out.append(
                f'<rect x="{ox:.1f}" y="{oy:.1f}" '
                f'width="{outside.width_m * _SCALE:.1f}" '
                f'height="{outside.depth_m * _SCALE:.1f}" fill="#f4f4f2" '
                f'stroke="#8a8a8a" stroke-width="1.4" stroke-dasharray="6 4"/>'
            )
            lx, ly = px(*outside.centroid)
            out.append(
                f'<text x="{lx:.1f}" y="{ly:.1f}" font-size="10.5" '
                f'text-anchor="middle" fill="#6b7075">'
                f'{escape(kinds.get(outside.room_id, outside.room_id).replace("_", " "))}'
                f'</text>'
                f'<text x="{lx:.1f}" y="{ly + 13:.1f}" font-size="9" '
                f'text-anchor="middle" fill="#8a8a8a">in setback</text>'
            )

        # Under the walls: a fixture is inside a room and the wall is the room's edge,
        # so masonry drawn over a bed reads correctly and a bed drawn over masonry does
        # not. Under the labels too — the room's name is the thing to read first.
        out.extend(_fixtures(refined, px))
        out.extend(_walls_and_openings(refined, layout, px))

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


def _walls_and_openings(refined: RefinedFloor, layout: Layout, px) -> list[str]:
    """Walls as thick strokes, then openings punched back out of them.

    Drawing the hole rather than splitting the wall into two segments either side of
    it. Both give the same picture; punching is one shape per opening instead of two
    per wall and, more to the point, it keeps the wall a single object that matches the
    `Wall` in the IR one-for-one. A renderer that silently re-partitions the geometry
    is a renderer whose output cannot be traced back to what produced it.
    """
    out = ['<g stroke-linecap="butt">']

    for wall in refined.walls:
        x1, y1 = px(wall.x1_m, wall.y1_m)
        x2, y2 = px(wall.x2_m, wall.y2_m)
        out.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="#1c1c1c" stroke-width="{wall.thickness_m * _SCALE:.1f}"/>'
        )
    out.append('</g>')

    # White over the wall, one pixel wider, so no dark seam survives at the reveal.
    out.append('<g stroke="#ffffff" stroke-linecap="butt">')
    arcs: list[str] = []
    for opening in refined.openings:
        wall = refined.by_id(opening.wall_id)
        if wall is None:                       # the IR validator refuses this
            continue
        half = opening.width_m / 2
        ax, ay = px(*wall.point_at(opening.offset_m - half))
        bx, by = px(*wall.point_at(opening.offset_m + half))
        out.append(
            f'<line x1="{ax:.1f}" y1="{ay:.1f}" x2="{bx:.1f}" y2="{by:.1f}" '
            f'stroke-width="{wall.thickness_m * _SCALE + 1:.1f}"/>'
        )
        if opening.kind is not OpeningKind.WINDOW:
            arcs.append(_swing(ax, ay, bx, by, _inward(wall, opening, layout, px)))
        else:
            arcs.append(
                f'<line x1="{ax:.1f}" y1="{ay:.1f}" x2="{bx:.1f}" y2="{by:.1f}" '
                f'stroke="#3f6f8f" stroke-width="1.4"/>'
            )
    out.append('</g>')
    out.extend(arcs)
    return out


def _inward(wall, opening, layout: Layout, px) -> tuple[float, float]:
    """A unit vector, in screen space, pointing from the wall into the room it serves.

    The first version picked a side by axis alone and the front door swung *out of the
    building* — north, across the title block. Which side a leaf falls on is not a
    detail: a door that opens into the neighbour's setback is wrong in a way a reader
    sees immediately.

    An exterior wall has one room and the answer is forced. An interior wall has two
    and this takes the *second* of the pair, which is the room being entered — stage
    ③ writes its edges as (corridor, bedroom), (hall, kitchen), (foyer, hall), so the
    leaf lands in the room you are going to rather than the one you are leaving. That
    is the convention, not a rule; it needs furniture to do properly, and there is none
    yet.
    """
    served = opening.connects[-1] if opening.connects else wall.rooms[-1]
    room = layout.by_id(served) or layout.by_id(wall.rooms[-1])
    mid_x, mid_y = px(*wall.point_at(opening.offset_m))
    if room is None:
        return (0.0, -1.0)

    cx, cy = px(*room.centroid)
    if wall.is_vertical:
        return (1.0, 0.0) if cx > mid_x else (-1.0, 0.0)
    return (0.0, 1.0) if cy > mid_y else (0.0, -1.0)


def _swing(
    ax: float, ay: float, bx: float, by: float, inward: tuple[float, float]
) -> str:
    """A door leaf and its quarter-circle arc — the convention that says "door".

    A gap alone is ambiguous at this scale: it reads as a missing wall as easily as an
    opening. The swing is what makes a plan legible to someone who reads plans, and it
    is also what stage ⑦ will need when it checks a door does not foul a fixture.
    """
    radius = ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5
    nx, ny = inward
    lx, ly = ax + nx * radius, ay + ny * radius

    # Sweep direction follows the hand the leaf falls on, or the arc bulges away from
    # the leaf and closes the wrong quadrant.
    cross = (lx - ax) * (by - ay) - (ly - ay) * (bx - ax)
    sweep = "0" if cross > 0 else "1"
    return (
        f'<g fill="none" stroke="#555" stroke-width="1.1">'
        f'<path d="M{ax:.1f} {ay:.1f} L{lx:.1f} {ly:.1f}"/>'
        f'<path d="M{lx:.1f} {ly:.1f} A{radius:.1f} {radius:.1f} 0 0 {sweep} '
        f'{bx:.1f} {by:.1f}" stroke-dasharray="3 3"/></g>'
    )


def _fixtures(refined: RefinedFloor, px) -> list[str]:
    """Draw each fixture as the glyph that names it.

    A plain rectangle is not enough: a 0.4 x 0.7 box and a 0.55 x 0.45 box are a WC and
    a basin to the person who placed them and two boxes to everyone else. These are the
    conventional marks — a bowl, a hob's four burners, a bed's pillow band — drawn thin
    and grey so they sit under the plan rather than competing with it.
    """
    out = ['<g fill="none" stroke="#8a8a8a" stroke-width="1">']
    for fixture in refined.fixtures:
        x1, y1 = px(fixture.x_min_m, fixture.y_max_m)     # SVG anchors top-left
        x2, y2 = px(fixture.x_max_m, fixture.y_min_m)
        w, h = x2 - x1, y2 - y1
        cx, cy = x1 + w / 2, y1 + h / 2
        kind = fixture.kind.value

        out.append(f'<rect x="{x1:.1f}" y="{y1:.1f}" width="{w:.1f}" height="{h:.1f}" '
                   f'fill="#ffffff" fill-opacity="0.55"/>')

        if kind in ("wc", "basin", "sink"):
            out.append(
                f'<ellipse cx="{cx:.1f}" cy="{cy:.1f}" rx="{w * 0.34:.1f}" '
                f'ry="{h * 0.34:.1f}"/>'
            )
        elif kind == "shower":
            out.append(
                f'<path d="M{x1:.1f} {y1:.1f} L{x2:.1f} {y2:.1f} '
                f'M{x2:.1f} {y1:.1f} L{x1:.1f} {y2:.1f}"/>'
            )
        elif kind == "stove":
            r = min(w, h) * 0.16
            for fx in (0.3, 0.7):
                for fy in (0.3, 0.7):
                    out.append(
                        f'<circle cx="{x1 + w * fx:.1f}" cy="{y1 + h * fy:.1f}" '
                        f'r="{r:.1f}"/>'
                    )
        elif kind in ("bed", "single_bed"):
            # A band across the head end. Which end is the head is `faces` reversed —
            # you get out of a bed the way it faces, so the pillows are at the back.
            band = 0.22
            if fixture.faces in (Facing.NORTH, Facing.SOUTH):
                yy = y2 - h * band if fixture.faces is Facing.NORTH else y1 + h * band
                out.append(f'<path d="M{x1:.1f} {yy:.1f} H{x2:.1f}"/>')
            else:
                xx = x1 + w * band if fixture.faces is Facing.WEST else x2 - w * band
                out.append(f'<path d="M{xx:.1f} {y1:.1f} V{y2:.1f}"/>')
        elif kind == "wardrobe":
            out.append(f'<path d="M{x1:.1f} {y1:.1f} L{x2:.1f} {y2:.1f}"/>')

    out.append("</g>")
    return out
