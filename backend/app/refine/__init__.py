"""Stage ⑥ REFINE — a `Layout` and its `Program` to walls, doors and windows.

Deterministic; no model, no network. Stage ⑤ decided where the rooms are, and nothing
here moves one: refining is about giving the lines between them a thickness and putting
holes in the right ones.

**The adjacency graph is the door schedule, and it always was.** `Relation` has carried
`ADJACENT` and `CONNECTED` as separate values since the IR was written, for a reason
recorded there: "a shared wall versus a shared wall with a door in it — both constrain
the tiling identically, so the distinction has to survive ⑤ rather than being flattened
into it." This is the stage that spends it. A door is not guessed from geometry; it is
the edge stage ③ asked for, drawn where stage ⑤ put the wall.

What this does not do: fixtures, furniture, dimension lines, stairs. Those are the rest
of ⑥ and they need a plan that reads as a house first.
"""

from __future__ import annotations

from app.ir.enums import OpeningKind, Relation, SpaceKind, WallKind
from app.ir.layout import TOLERANCE_M, Layout, PlacedRoom
from app.ir.plan import Program
from app.ir.refined import Opening, RefinedFloor, Wall
from app.rules import load_ruleset

REFINE_RULES = "refine_v1"


def refine(layout: Layout, program: Program) -> RefinedFloor:
    """Walls and openings for one storey."""
    rules = load_ruleset(REFINE_RULES).data
    walls = _walls(layout, rules["walls"])
    openings = _doors(walls, layout, program, rules["doors"])
    openings += _windows(walls, program, rules["windows"], taken=openings)
    return RefinedFloor(floor=layout.floor, walls=walls, openings=openings)


def _walls(layout: Layout, rules: dict) -> list[Wall]:
    """Every shared edge between two rooms, plus every edge on the outer boundary.

    Interior walls come from *pairs* rather than from merging collinear edges, because
    a wall's identity here is the pair of rooms it separates — that is what a door
    needs to know. Merging first and re-deriving the pair afterwards would throw away
    the one fact the next step depends on.

    Exterior walls stay per-room for the same reason: a window belongs to the room it
    lights. Two collinear exterior segments abut and render as one continuous wall, so
    nothing is lost by not merging them.
    """
    interior = rules["interior_thickness_m"]
    exterior = rules["exterior_thickness_m"]
    walls: list[Wall] = []

    rooms = layout.rooms
    for i, a in enumerate(rooms):
        for b in rooms[i + 1:]:
            shared = _shared_edge(a, b)
            if shared is None:
                continue
            (x1, y1), (x2, y2) = shared
            walls.append(
                Wall(
                    id=f"w{len(walls)}",
                    x1_m=x1, y1_m=y1, x2_m=x2, y2_m=y2,
                    thickness_m=interior,
                    kind=WallKind.INTERIOR,
                    rooms=[a.room_id, b.room_id],
                )
            )

    for room in rooms:
        for (x1, y1), (x2, y2) in _boundary_edges(room, layout):
            walls.append(
                Wall(
                    id=f"w{len(walls)}",
                    x1_m=x1, y1_m=y1, x2_m=x2, y2_m=y2,
                    thickness_m=exterior,
                    kind=WallKind.EXTERIOR,
                    rooms=[room.room_id],
                )
            )
    return walls


def _shared_edge(
    a: PlacedRoom, b: PlacedRoom
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """The segment two rooms have in common, or None if they only touch at a corner.

    A corner contact is not a wall. Two rooms meeting at a point share an edge of zero
    length, and a door in it would be a door 0 m wide — so the overlap has to clear the
    tiling tolerance, not merely be non-negative.
    """
    # Vertical: one room's right face is the other's left face.
    for left, right in ((a, b), (b, a)):
        if abs(left.x_max_m - right.x_min_m) <= TOLERANCE_M:
            lo = max(left.y_min_m, right.y_min_m)
            hi = min(left.y_max_m, right.y_max_m)
            if hi - lo > TOLERANCE_M:
                return ((left.x_max_m, lo), (left.x_max_m, hi))

    # Horizontal: one room's top face is the other's bottom face.
    for lower, upper in ((a, b), (b, a)):
        if abs(lower.y_max_m - upper.y_min_m) <= TOLERANCE_M:
            lo = max(lower.x_min_m, upper.x_min_m)
            hi = min(lower.x_max_m, upper.x_max_m)
            if hi - lo > TOLERANCE_M:
                return ((lo, lower.y_max_m), (hi, lower.y_max_m))
    return None


def _boundary_edges(
    room: PlacedRoom, layout: Layout
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """The room's own edges that lie on the outside of the plan."""
    edges = []
    if abs(room.x_min_m - layout.x_min_m) <= TOLERANCE_M:
        edges.append(((room.x_min_m, room.y_min_m), (room.x_min_m, room.y_max_m)))
    if abs(room.x_max_m - layout.x_max_m) <= TOLERANCE_M:
        edges.append(((room.x_max_m, room.y_min_m), (room.x_max_m, room.y_max_m)))
    if abs(room.y_min_m - layout.y_min_m) <= TOLERANCE_M:
        edges.append(((room.x_min_m, room.y_min_m), (room.x_max_m, room.y_min_m)))
    if abs(room.y_max_m - layout.y_max_m) <= TOLERANCE_M:
        edges.append(((room.x_min_m, room.y_max_m), (room.x_max_m, room.y_max_m)))
    return edges


def _doors(
    walls: list[Wall], layout: Layout, program: Program, rules: dict
) -> list[Opening]:
    """One door per `CONNECTED` edge, plus the entrance.

    An edge whose rooms stage ⑤ failed to place against each other gets no door rather
    than a door through a third room. That is a real outcome — `score` already counts
    it as a violation and stage ④ reports it — and inventing a connection here would
    hide a defect the ranking is measuring.
    """
    kinds = {room.id: room.kind for room in program.rooms}
    narrow = {SpaceKind(k) for k in rules["narrow_kinds"]}
    clearance = rules["clearance_m"]
    openings: list[Opening] = []

    between = {frozenset(w.rooms): w for w in walls if w.kind is WallKind.INTERIOR}
    for edge in program.adjacencies:
        if edge.relation is not Relation.CONNECTED:
            continue
        wall = between.get(frozenset({edge.a, edge.b}))
        if wall is None:
            continue
        width = (
            rules["service_width_m"]
            if kinds.get(edge.a) in narrow or kinds.get(edge.b) in narrow
            else rules["internal_width_m"]
        )
        placed = _fit(
            wall, width, clearance, openings, floor_width=rules["service_width_m"]
        )
        if placed is not None:
            offset, fitted = placed
            openings.append(
                Opening(
                    wall_id=wall.id,
                    kind=OpeningKind.DOOR,
                    offset_m=offset,
                    width_m=fitted,
                    connects=[edge.a, edge.b],
                )
            )

    entrance = _entrance(walls, layout, program, rules, openings)
    if entrance is not None:
        openings.append(entrance)
    return openings


def _entrance(
    walls: list[Wall], layout: Layout, program: Program, rules: dict, taken: list[Opening]
) -> Opening | None:
    """The front door: the foyer's exterior wall, on the road.

    Not any exterior wall of the foyer. The road-edge constraint in `solver/score.py`
    exists to put the foyer on the street, and a front door on the wall facing the
    neighbour's plot would quietly undo it — the plan would look right and open the
    wrong way.
    """
    foyer = next(
        (room.id for room in program.rooms if room.kind is SpaceKind.FOYER), None
    )
    if foyer is None or not layout.road_edges:
        return None

    candidates = [
        wall for wall in walls
        if wall.kind is WallKind.EXTERIOR
        and wall.rooms == [foyer]
        and _faces_a_road(wall, layout)
    ]
    if not candidates:
        return None

    wall = max(candidates, key=lambda w: w.length_m)
    placed = _fit(
        wall,
        rules["entrance_width_m"],
        rules["clearance_m"],
        taken,
        # A front door may narrow to an ordinary internal width, but no further: below
        # that it is not a doorway anyone would build as the way into a house.
        floor_width=rules["internal_width_m"],
    )
    if placed is None:
        return None
    offset, width = placed
    return Opening(
        wall_id=wall.id, kind=OpeningKind.ENTRANCE, offset_m=offset,
        width_m=width, connects=[foyer],
    )


def _faces_a_road(wall: Wall, layout: Layout) -> bool:
    """Is this exterior wall on one of the boundaries that fronts a road?"""
    from app.ir.enums import Facing

    on = {
        Facing.WEST: wall.is_vertical and abs(wall.x1_m - layout.x_min_m) <= TOLERANCE_M,
        Facing.EAST: wall.is_vertical and abs(wall.x1_m - layout.x_max_m) <= TOLERANCE_M,
        Facing.SOUTH: not wall.is_vertical and abs(wall.y1_m - layout.y_min_m) <= TOLERANCE_M,
        Facing.NORTH: not wall.is_vertical and abs(wall.y1_m - layout.y_max_m) <= TOLERANCE_M,
    }
    return any(on.get(edge, False) for edge in layout.road_edges)


def _windows(
    walls: list[Wall], program: Program, rules: dict, taken: list[Opening]
) -> list[Opening]:
    """One window on the longest exterior wall of every room that needs light.

    `needs_exterior_wall` is the same flag `score` uses to penalise a room with no
    window, so the drawing and the score agree about which rooms have one. A room the
    solver put on no boundary at all gets none here — again, visibly, rather than by
    drawing a window into an internal partition.
    """
    needs = {room.id for room in program.rooms if room.needs_exterior_wall}
    width = rules["width_m"]
    minimum = rules["min_wall_m"]
    openings: list[Opening] = []

    for room_id in sorted(needs):
        outer = [
            wall for wall in walls
            if wall.kind is WallKind.EXTERIOR
            and wall.rooms == [room_id]
            and wall.length_m >= minimum
        ]
        if not outer:
            continue
        wall = max(outer, key=lambda w: w.length_m)
        placed = _fit(wall, width, 0.0, [*taken, *openings])
        if placed is not None:
            offset, fitted = placed
            openings.append(
                Opening(
                    wall_id=wall.id, kind=OpeningKind.WINDOW, offset_m=offset,
                    width_m=fitted, connects=[room_id],
                )
            )
    return openings


def _fit(
    wall: Wall,
    width: float,
    clearance: float,
    taken: list[Opening],
    *,
    floor_width: float | None = None,
) -> tuple[float, float] | None:
    """Centre the opening and return `(offset, width)`, or None if it cannot go here.

    Centring rather than optimising. A door's exact position along a wall is a thing
    the *user* adjusts — CLAUDE.md's v1 editor is "adjustment, not authoring" — and
    placing it cleverly would mean guessing at furniture that does not exist yet.

    **It narrows before it gives up.** The first version did not, and a 40x60 came out
    with no front door at all: the foyer's road-facing wall measured 1.27 m against the
    1.30 m a 1.0 m entrance and its clearances need, so the house had no way in and
    nothing said so. Missing by three centimetres is not a reason to draw a house
    nobody can enter — it is a reason to draw a slightly narrower door. `floor_width`
    is where narrowing stops, and below it there genuinely is no door to draw.
    """
    span = wall.length_m
    usable = span - 2 * clearance
    if usable < (floor_width if floor_width is not None else width):
        return None
    width = min(width, usable)

    centre = span / 2
    for other in taken:
        if other.wall_id != wall.id:
            continue
        if abs(other.offset_m - centre) < (other.width_m + width) / 2:
            return None                 # already occupied; one opening per wall is enough
    return centre, width
