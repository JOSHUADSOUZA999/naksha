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

What this does not do: dimension lines, stair treads, furniture beyond the fixtures a
room needs to read as that kind of room. Those are the rest of ⑥.
"""

from __future__ import annotations

import math

from app.ir.enums import Facing, FixtureKind, OpeningKind, Relation, SpaceKind, WallKind
from app.ir.layout import TOLERANCE_M, Layout, PlacedRoom
from app.ir.plan import Program
from app.ir.refined import Fixture, Opening, RefinedFloor, Wall
from app.ir.units import area_text, feet_and_inches, length_text
from app.rules import load_ruleset

REFINE_RULES = "refine_v1"


def refine(layout: Layout, program: Program, envelope=None) -> RefinedFloor:
    """Walls and openings for one storey.

    `envelope` is needed only to place spaces that sit outside the buildable rectangle
    — a car porch in the setback. Optional so every existing caller keeps working, and
    a programme carrying such a space without one draws the house without it rather
    than guessing where the plot boundary is.
    """
    rules = load_ruleset(REFINE_RULES).data
    walls = _walls(layout, rules["walls"])
    openings = _doors(walls, layout, program, rules["doors"])
    openings += _connect(walls, layout, program, openings, rules["doors"])
    openings += _vehicle_openings(walls, layout, program, rules["vehicles"], openings)

    clear = {
        placed.room_id: _clear_rect(placed, layout, rules["walls"])
        for placed in layout.rooms
    }
    # After the clear rects, because a window is sized from the floor area it lights.
    openings += _windows(
        walls, program, rules["windows"], openings, clear, rules["ventilation"]
    )
    fixtures = _fixtures(layout, program, walls, openings, rules)
    fixtures += _stair_flights(layout, program, walls, openings, rules)
    return RefinedFloor(
        floor=layout.floor, walls=walls, openings=openings,
        fixtures=fixtures, clear=clear,
        outside=_in_the_setback(layout, program, envelope),
    )


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
    # Open first: a wall that is not built takes no door, and nothing else may claim it.
    for edge in program.adjacencies:
        if edge.relation is not Relation.OPEN:
            continue
        wall = between.get(frozenset({edge.a, edge.b}))
        if wall is None:
            continue
        openings.append(
            Opening(
                wall_id=wall.id, kind=OpeningKind.OPEN, offset_m=wall.length_m / 2,
                width_m=wall.length_m, connects=[edge.a, edge.b],
            )
        )
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
            wall, width, clearance, openings, floor_width=rules["service_width_m"],
            at_an_end=SpaceKind.STAIRCASE in (kinds.get(edge.a), kinds.get(edge.b)),
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


def _vehicle_openings(
    walls: list[Wall], layout: Layout, program: Program, rules: dict, taken: list[Opening]
) -> list[Opening]:
    """The car bay's opening to the road: a gap a car can drive through.

    Stage ⑥ gave a car bay what it gives every room with an exterior wall — a window —
    so every bay in the project was drawn as a sealed room no car could get into. The
    opening goes on the bay's road-facing wall, which `score` and ⑦ already require it to
    have, and narrows to the least a car fits through before it gives up. A bay with no
    such wall, or too little of one, gets nothing, and ⑦ refuses it. A porch built in the
    setback is drawn by `_in_the_setback` and is open anyway.

    **Which gate depends on which way the bay lies.** One that runs back from the road is
    driven into nose first, through an ordinary 2.7 m gate in its short side. One lying
    along the road — its long side on the street — is swung into, like a car porch, and
    gets a gate across that whole side: a 2.7 m gap in the middle of a 6 m wall, 3 m
    deep, is a garage no car can turn into.
    """
    bays = [
        room.id for room in program.rooms
        if room.kind is SpaceKind.CAR_PARKING and not room.outside_envelope
    ]
    if not bays or not layout.road_edges:
        return []
    openings: list[Opening] = []
    for bay in bays:
        road_walls = sorted(
            (
                wall for wall in walls
                if wall.kind is WallKind.EXTERIOR
                and wall.rooms == [bay]
                and _faces_a_road(wall, layout)
            ),
            key=lambda w: w.length_m,
            reverse=True,
        )
        room = layout.by_id(bay)
        for wall in road_walls:
            along = room is not None and (
                (room.x_max_m - room.x_min_m) > (room.y_max_m - room.y_min_m)
                if not wall.is_vertical
                else (room.y_max_m - room.y_min_m) > (room.x_max_m - room.x_min_m)
            )
            placed = _fit(
                wall,
                wall.length_m if along else rules["opening_width_m"],
                rules["clearance_m"],
                [*taken, *openings],
                floor_width=rules["side_gate_min_m"] if along else rules["min_opening_width_m"],
            )
            if placed is None:
                continue
            offset, width = placed
            openings.append(
                Opening(
                    wall_id=wall.id, kind=OpeningKind.VEHICLE, offset_m=offset,
                    width_m=width, connects=[bay],
                )
            )
            break
    return openings


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
    walls: list[Wall],
    program: Program,
    rules: dict,
    taken: list[Opening],
    clear: dict[str, tuple[float, float, float, float]],
    air: dict | None = None,
) -> list[Opening]:
    """Give every room that meets the outside the opening its use calls for.

    **Glaze every room that needs light, to the area the bye-laws ask for.** Sized to the
    room, not to a convention: every window used to be 1.2 m wide, so a 19 m² hall and a
    3 m² bathroom got the same opening — which satisfies neither the code nor anyone
    living there. The rule is an aggregate area of one tenth of the floor, so the width
    follows from the floor area and the window height.

    Spills onto a second wall when one cannot hold it. Past the maximum width an opening
    wants a mullion and is really two windows, so drawing it as two is not a workaround —
    it is what gets built. Rooms with only one short exterior wall end up under-glazed,
    and that is a finding for stage ⑦ rather than something to fake here by drawing a
    window wider than its wall.

    **Then air, which is a different question from light.** Only rooms that had to touch
    the outside used to get an opening, so across fourteen plans every bathroom was drawn
    sealed — all thirty-one had an outside wall — and not one of seventy-eight halls,
    kitchens and bedrooms had windows on two sides. Now a bathroom gets a ventilator; a
    stair, corridor, pooja room or foyer a window where it meets the outside; and a room
    people live in a second window on another side when it has one, so air can come in
    one side and leave by the other. Which kind gets what is `refine_v1`'s `ventilation`
    block, not a list in this function.
    """
    if air is None:
        air = load_ruleset(REFINE_RULES).data["ventilation"]
    height = rules["height_m"]
    fraction = rules["area_fraction"]
    smallest = rules["min_width_m"]
    widest = rules["max_width_m"]
    shortest_wall = rules["min_wall_m"]
    glazed_too = {SpaceKind(kind) for kind in air["glazed_if_outside"]}
    lit = {SpaceKind(kind) for kind in air["lit_kinds"]}
    vented = {SpaceKind(kind) for kind in air["ventilator_kinds"]}
    crossed = {SpaceKind(kind) for kind in air["cross_kinds"]}
    by_id = {wall.id: wall for wall in walls}
    openings: list[Opening] = []

    def outside(room_id: str, shortest: float) -> list[Wall]:
        """The room's own outside walls long enough for an opening, longest first."""
        return sorted(
            (
                wall for wall in walls
                if wall.kind is WallKind.EXTERIOR
                and wall.rooms == [room_id]
                and wall.length_m >= shortest
            ),
            key=lambda w: w.length_m,
            reverse=True,
        )

    def add(
        wall: Wall, kind: OpeningKind, width: float, tall: float, room_id: str,
        floor_width: float | None,
    ) -> Opening | None:
        placed = _fit(wall, width, 0.0, [*taken, *openings], floor_width=floor_width)
        if placed is None:
            return None
        offset, fitted = placed
        opening = Opening(
            wall_id=wall.id, kind=kind, offset_m=offset, width_m=fitted,
            height_m=tall, connects=[room_id],
        )
        openings.append(opening)
        return opening

    for room in sorted(program.rooms, key=lambda r: r.id):
        rect = clear.get(room.id)
        # Not the car bay: its opening to the road lights and airs it, and a window there
        # was the only opening it ever had, which is how every bay came out sealed.
        if rect is None or room.kind is SpaceKind.CAR_PARKING:
            continue

        if room.kind in vented:
            # One, high and small: the bye-laws give a bathroom an opening of its own
            # rather than a share of its floor, and nobody wants a view into it.
            for wall in outside(room.id, air["ventilator_min_wall_m"]):
                if add(
                    wall, OpeningKind.VENTILATOR, air["ventilator_width_m"],
                    air["ventilator_height_m"], room.id, None,
                ) is not None:
                    break
        elif room.needs_exterior_wall or room.kind in glazed_too:
            floor_area = max(0.0, rect[2] - rect[0]) * max(0.0, rect[3] - rect[1])
            wanted = floor_area * fraction / height if height > 0 else 0.0
            # The minimum width is a floor on a window, not a reason to skip one. A 6.6 m²
            # kitchen wants 0.55 m of glazing at one tenth, which is under the smallest
            # window anybody builds — and the first version read that as "no window", so
            # every kitchen in the set came out blind. Small rooms get the minimum; only
            # the *remainder* after a window is placed has to clear it to earn another.
            first = True
            for wall in outside(room.id, shortest_wall):
                if not first and wanted < smallest:
                    break
                window = add(
                    wall, OpeningKind.WINDOW, min(max(wanted, smallest), widest),
                    height, room.id, smallest,
                )
                if window is None:
                    continue
                wanted -= window.width_m
                first = False
        elif room.kind in lit:
            # One ordinary window: daylight on a stair, and a corridor with a window at
            # its end is what pulls a breeze through the house.
            for wall in outside(room.id, shortest_wall):
                if add(
                    wall, OpeningKind.WINDOW, air["lit_width_m"], height, room.id, smallest,
                ) is not None:
                    break

        if room.kind in crossed:
            # A second side, where the room has one. Glazing to a tenth nearly always fits
            # on the longest wall, which left every corner room open on one side only.
            sides = {
                _side(by_id[opening.wall_id], rect)
                for opening in openings
                if room.id in opening.connects
            }
            if len(sides) == 1:
                for wall in outside(room.id, shortest_wall):
                    if _side(wall, rect) in sides:
                        continue
                    if add(
                        wall, OpeningKind.WINDOW, air["cross_width_m"], height, room.id,
                        smallest,
                    ) is not None:
                        break
    return openings


def _side(wall: Wall, rect: tuple[float, float, float, float]) -> Facing:
    """Which side of a room a wall bounds, from where it lies against the room's middle."""
    if wall.is_vertical:
        return Facing.WEST if wall.x1_m < (rect[0] + rect[2]) / 2 else Facing.EAST
    return Facing.SOUTH if wall.y1_m < (rect[1] + rect[3]) / 2 else Facing.NORTH



def _fit(
    wall: Wall,
    width: float,
    clearance: float,
    taken: list[Opening],
    *,
    floor_width: float | None = None,
    at_an_end: bool = False,
) -> tuple[float, float] | None:
    """Centre the opening and return `(offset, width)`, or None if it cannot go here.

    **A door into a staircase goes at an end of the wall, not its middle.** Centred on a
    straight stair's long side it opens onto the eleventh step, and no flight can be drawn
    clear of it: 17 of 22 benchmark stairs had none. At an end it opens where the flight
    starts or the landing is. `at_an_end` tries both ends, then the centre.

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
    spots = (
        [clearance + width / 2, span - clearance - width / 2, centre] if at_an_end else [centre]
    )
    for spot in spots:
        if not any(
            other.wall_id == wall.id and abs(other.offset_m - spot) < (other.width_m + width) / 2
            for other in taken
        ):
            return spot, width
    return None                         # already occupied; one opening per wall is enough


def _fixtures(
    layout: Layout, program: Program, walls: list[Wall], openings: list[Opening], rules: dict
) -> list[Fixture]:
    """Furnish the rooms whose kind has a schedule.

    Deterministic and modest on purpose. This is what makes a 3.5 m² blue rectangle
    read as a bathroom, and nothing more — CLAUDE.md's v1 editor is "adjustment, not
    authoring", so the job here is to put a defensible arrangement on the page for the
    user to push around, not to solve furniture layout.

    **Nothing is placed where a door swings.** That is the one rule that cannot be
    left to the user: a WC drawn under the door is not a starting point, it is a
    mistake they have to notice before they can fix it. A fixture that does not fit
    clear of the doors is dropped instead — a bathroom holding only a WC and a basin
    is a real bathroom.
    """
    sizes = rules["fixtures"]
    schedules = rules["schedules"]
    kinds = {room.id: room.kind for room in program.rooms}

    out: list[Fixture] = []
    for placed in layout.rooms:
        schedule = schedules.get(kinds.get(placed.room_id, SpaceKind.HALL).value)
        if not schedule:
            continue

        clear = _clear_rect(placed, layout, rules["walls"])
        blocked = _door_swings(placed, walls, openings)
        taken: list[tuple[float, float, float, float]] = []

        for name, (rect, faces) in _arrange(schedule, sizes, clear, blocked):
            out.append(
                Fixture(
                    kind=FixtureKind(name), room_id=placed.room_id,
                    x_min_m=rect[0], y_min_m=rect[1], x_max_m=rect[2], y_max_m=rect[3],
                    faces=faces,
                )
            )
    return out


def _stair_flights(
    layout: Layout, program: Program, walls: list[Wall], openings: list[Opening], rules: dict
) -> list[Fixture]:
    """Draw each staircase as its flights and landing, clear of every door's swing.

    A stair was a labelled rectangle, and a labelled rectangle cannot say whether a flight
    fits in it — the JP Nagar plan's 12'3" x 4'8" "staircase" held none. The flights are
    drawn from the same arithmetic stage ⑤ enforces (`program.stair_sizes`), at the
    comfortable figures where the room allows and the legal ones where it does not.

    A door may not open onto the steps, so the flights go where no leaf swings: every
    template that fits, with its landing at either end and its first flight on either
    side, and the first arrangement clear of the doors wins. A stair with no such
    arrangement is drawn without flights rather than with a door across them, and ⑦'s
    circulation still sees the room.
    """
    from app.ir.enums import Facing
    from app.program import STAIR_RULES

    stair_rules = load_ruleset(STAIR_RULES).data
    kinds = {room.id: room.kind for room in program.rooms}
    out: list[Fixture] = []
    for placed in layout.rooms:
        if kinds.get(placed.room_id) is not SpaceKind.STAIRCASE:
            continue
        x0, y0, x1, y1 = _clear_rect(placed, layout, rules["walls"])
        along_x = (x1 - x0) >= (y1 - y0)
        long_side, short_side = (x1 - x0, y1 - y0) if along_x else (y1 - y0, x1 - x0)

        def to_rect(a0: float, a1: float, b0: float, b1: float) -> tuple[float, float, float, float]:
            # a runs along the stair's length, b across it.
            return (x0 + a0, y0 + b0, x0 + a1, y0 + b1) if along_x else (x0 + b0, y0 + a0, x0 + b1, y0 + a1)

        def climb(forward: bool) -> Facing:
            if along_x:
                return Facing.EAST if forward else Facing.WEST
            return Facing.NORTH if forward else Facing.SOUTH

        drawn = None
        for figures in ("practice", "legal"):
            f = stair_rules[figures]
            width, tread = f["flight_width_m"], f["min_tread_m"]
            blocked = _onto_a_landing(
                placed, walls, openings, width, kinds, stair_rules["circulation_threshold_m"]
            )
            risers = math.ceil(round(f["floor_to_floor_m"] / f["max_riser_m"], 6))
            for template in stair_rules["templates"]:
                if template == "dog_leg":
                    run = (math.ceil(risers / 2) - 1) * tread
                    need_long, need_short = run + width, 2 * width + f["well_m"]
                else:
                    flights = math.ceil(risers / f["max_risers_per_flight"])
                    run = (risers - flights) * tread
                    need_long, need_short = run + (flights - 1) * width, width
                if need_long > long_side + TOLERANCE_M or need_short > short_side + TOLERANCE_M:
                    continue
                for landing_far in (True, False):
                    for flip in (False, True):
                        # The landing against one end wall; the flights run back from it.
                        land_a = (long_side - width, long_side) if landing_far else (0.0, width)
                        b_lo = short_side - need_short if flip else 0.0
                        pieces = []
                        if template == "dog_leg":
                            flight_a = (long_side - need_long, long_side - width) if landing_far else (width, need_long)
                            first = (b_lo, b_lo + width)
                            second = (b_lo + width + f["well_m"], b_lo + need_short)
                            pieces = [
                                (FixtureKind.FLIGHT, to_rect(*flight_a, *first), climb(landing_far)),
                                (FixtureKind.LANDING, to_rect(*land_a, b_lo, b_lo + need_short), climb(landing_far)),
                                (FixtureKind.FLIGHT, to_rect(*flight_a, *second), climb(not landing_far)),
                            ]
                            if any(_hits(rect, zone) for _, rect, _ in pieces for zone in blocked):
                                continue
                            drawn = pieces
                            break
                        else:
                            # A straight run need not touch an end wall: the floor left at
                            # its foot is where you step on, so it slides along the room,
                            # 50 mm at a time, until it clears the doors.
                            flights = math.ceil(risers / f["max_risers_per_flight"])
                            per = run / flights
                            slack = max(0.0, long_side - need_long)
                            steps = int(slack / 0.05) + 1
                            for k in range(steps):
                                lo = slack - k * 0.05 if landing_far else k * 0.05
                                pieces, cursor = [], lo
                                for n in range(flights):
                                    pieces.append((FixtureKind.FLIGHT, to_rect(cursor, cursor + per, b_lo, b_lo + width), climb(landing_far)))
                                    cursor += per
                                    if n < flights - 1:
                                        pieces.append((FixtureKind.LANDING, to_rect(cursor, cursor + width, b_lo, b_lo + width), climb(landing_far)))
                                        cursor += width
                                if not any(_hits(rect, zone) for _, rect, _ in pieces for zone in blocked):
                                    drawn = pieces
                                    break
                            if drawn:
                                break
                    if drawn:
                        break
                if drawn:
                    break
            if drawn:
                break
        for kind, rect, faces in drawn or []:
            out.append(Fixture(
                kind=kind, room_id=placed.room_id,
                x_min_m=rect[0], y_min_m=rect[1], x_max_m=rect[2], y_max_m=rect[3], faces=faces,
            ))
    return out


def _onto_a_landing(
    placed: PlacedRoom,
    walls: list[Wall],
    openings: list[Opening],
    depth: float,
    kinds: dict,
    threshold: float,
) -> list[tuple[float, float, float, float]]:
    """The floor a door into a staircase must open onto: the doorway's width, a flight
    deep. Not the swing square other rooms keep clear — a door opens onto a landing, not
    onto the steps, and that is the whole rule; the square also blocked the foot of the
    flight the door exists to reach.

    **A corridor, hall or foyer is the landing.** From one of those only a tread's depth
    of floor is kept inside the stair, since the law lets circulation carry the landing
    and a stair room sized to hold its own cost more than it bought (stairs_v1)."""
    circulation = {SpaceKind.CORRIDOR, SpaceKind.HALL, SpaceKind.FOYER}
    by_id = {wall.id: wall for wall in walls}
    zones = []
    for opening in openings:
        if opening.kind not in (OpeningKind.DOOR, OpeningKind.ENTRANCE):
            continue
        if placed.room_id not in opening.connects:
            continue
        wall = by_id.get(opening.wall_id)
        if wall is None:
            continue
        hx, hy = wall.point_at(opening.offset_m)
        half = opening.width_m / 2
        other = [room for room in opening.connects if room != placed.room_id]
        reach = threshold if other and kinds.get(other[0]) in circulation else depth
        if wall.is_vertical:
            zones.append((hx - reach, hy - half, hx + reach, hy + half))
        else:
            zones.append((hx - half, hy - reach, hx + half, hy + reach))
    return zones


def _clear_rect(
    placed: PlacedRoom, layout: Layout, rules: dict
) -> tuple[float, float, float, float]:
    """The room inside its walls, side by side.

    Stage ⑤'s rectangle runs to the wall *centrelines*, so it overstates the room by
    half a wall on every side — furnishing against it pushes everything into the
    masonry, and labelling with it tells a person their bathroom is bigger than it is.

    Each side is inset by half of whatever wall is on it, and which wall that is
    follows from position alone: a side on the plan's boundary is exterior, everything
    else is a partition. That holds because there are exactly two thicknesses.
    """
    outer = rules["exterior_thickness_m"] / 2
    inner = rules["interior_thickness_m"] / 2

    def inset(coordinate: float, edge: float) -> float:
        return outer if abs(coordinate - edge) <= TOLERANCE_M else inner

    return (
        placed.x_min_m + inset(placed.x_min_m, layout.x_min_m),
        placed.y_min_m + inset(placed.y_min_m, layout.y_min_m),
        placed.x_max_m - inset(placed.x_max_m, layout.x_max_m),
        placed.y_max_m - inset(placed.y_max_m, layout.y_max_m),
    )


def _door_swings(
    placed: PlacedRoom, walls: list[Wall], openings: list[Opening]
) -> list[tuple[float, float, float, float]]:
    """A square of keep-out for every door opening into this room.

    Square rather than the quarter-disc the drawing shows: the leaf sweeps a quarter
    circle, and a rectangle bounding it is both easier to test against and the more
    conservative answer. Erring towards fewer fixtures is right — a dropped shower is
    a smaller wrong than one drawn through a door.
    """
    by_id = {wall.id: wall for wall in walls}
    zones = []
    for opening in openings:
        # Only a leaf swings. A window or a ventilator has none, and counting the
        # ventilator as a door kept the WC off the one wall it belongs against — the
        # outside wall under the ventilator — and dropped it from the 50x80's bathrooms.
        if opening.kind not in (OpeningKind.DOOR, OpeningKind.ENTRANCE):
            continue
        if placed.room_id not in opening.connects:
            continue
        wall = by_id.get(opening.wall_id)
        if wall is None:
            continue
        hx, hy = wall.point_at(opening.offset_m)
        reach = opening.width_m
        zones.append((hx - reach, hy - reach, hx + reach, hy + reach))
    return zones


def _arrange(
    schedule: list[str],
    sizes: dict,
    clear: tuple[float, float, float, float],
    blocked: list[tuple[float, float, float, float]],
) -> list[tuple[str, tuple[tuple[float, float, float, float], "Facing"]]]:
    """The arrangement of a room's schedule that places the most fixtures.

    Greedy placement took the first free corner for each fixture in turn, and a bed in
    the wrong corner left no wall for the wardrobe: a 2.59 x 3.63 m bedroom that holds
    both, bed against a side wall, was drawn without one. This tries every spot for each
    fixture — eight at most, and a schedule is three long — and keeps the arrangement that
    places the most. Ties go to the earliest spots in the fixed order, so a room greedy
    already furnished fully is furnished exactly as before, and the same plan furnishes
    the same way twice.

    Order is still priority: a fixture is dropped only when no arrangement of everything
    before it leaves it room, and an earlier fixture is never dropped to fit a later one.
    """
    best: list = []
    best_key: tuple = ()

    def walk(index: int, taken: list, counter, chosen: list, key: tuple) -> None:
        nonlocal best, best_key
        if index == len(schedule):
            # Earlier fixtures placed outrank later ones: compare the placed/dropped
            # pattern in schedule order, then the spot indices for determinism.
            pattern = tuple(0 if c is None else 1 for c in chosen)
            score = (pattern, tuple(-k for k in key))
            if not best or score > best_key:
                best, best_key = list(chosen), score
            return
        name = schedule[index]
        size = sizes[name]
        if name in _ON_THE_COUNTER:
            spots = [] if counter is None else [s for s in [_on_counter(counter, size["width_m"], name)] if s]
        else:
            spots = _wall_spots(clear, size["width_m"], size["depth_m"], blocked + taken)
        for n, spot in enumerate(spots):
            rect, _ = spot
            walk(
                index + 1,
                taken if name in _ON_THE_COUNTER else taken + [rect],
                spot if name == "counter" else counter,
                chosen + [(name, spot)],
                key + (n,),
            )
        walk(index + 1, taken, counter, chosen + [None], key + (len(spots),))

    walk(0, [], None, [], ())
    return [c for c in best if c is not None]


def _wall_spots(
    clear: tuple[float, float, float, float],
    width: float,
    depth: float,
    blocked: list[tuple[float, float, float, float]],
) -> list[tuple[tuple[float, float, float, float], "Facing"]]:
    """Every place the fixture backs onto a wall clear of what is blocked, corners first.

    Corners first because that is where furniture goes: a bed in the middle of a wall
    and a bed in the corner both fit, and only one of them leaves a usable room. The
    order of the sides is fixed rather than clever, so the same plan furnishes the same
    way twice — a drawing that reshuffles itself between runs is one nobody can discuss.
    """
    from app.ir.enums import Facing

    x_min, y_min, x_max, y_max = clear
    if x_max - x_min <= 0 or y_max - y_min <= 0:
        return []

    # (side the fixture backs onto, the way it then faces, its footprint there)
    plans = [
        (Facing.NORTH, (x_min, y_max - depth, x_min + width, y_max)),
        (Facing.NORTH, (x_max - width, y_max - depth, x_max, y_max)),
        (Facing.SOUTH, (x_min, y_min, x_min + width, y_min + depth)),
        (Facing.SOUTH, (x_max - width, y_min, x_max, y_min + depth)),
        (Facing.EAST, (x_min, y_min, x_min + depth, y_min + width)),
        (Facing.EAST, (x_min, y_max - width, x_min + depth, y_max)),
        (Facing.WEST, (x_max - depth, y_min, x_max, y_min + width)),
        (Facing.WEST, (x_max - depth, y_max - width, x_max, y_max)),
    ]
    spots = []
    for faces, rect in plans:
        if rect[0] < x_min - 1e-9 or rect[1] < y_min - 1e-9:
            continue
        if rect[2] > x_max + 1e-9 or rect[3] > y_max + 1e-9:
            continue
        if any(_hits(rect, other) for other in blocked):
            continue
        spots.append((rect, faces))
    return spots


def _hits(a: tuple[float, ...], b: tuple[float, ...]) -> bool:
    """Do two rectangles share any area? Touching edges do not count."""
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


# Fixtures that are set into a worktop rather than standing on the floor.
_ON_THE_COUNTER = {"sink", "stove"}

# Where along the run each one sits, as a fraction of its length. A sink under the
# window end and the hob away from it is the ordinary arrangement; the point is that
# they are apart and both on the counter, not that these exact fractions are right.
_COUNTER_POSITION = {"sink": 0.3, "stove": 0.75}


def _on_counter(
    counter: tuple[tuple[float, float, float, float], "Facing"], width: float, name: str
) -> tuple[tuple[float, float, float, float], "Facing"] | None:
    """Place a sink or hob within the counter run, inset from its front edge."""
    (x_min, y_min, x_max, y_max), faces = counter
    from app.ir.enums import Facing

    along = _COUNTER_POSITION.get(name, 0.5)
    inset = 0.06                       # a lip of worktop in front and behind

    if faces in (Facing.NORTH, Facing.SOUTH):
        span = x_max - x_min
        if span < width:
            return None
        cx = x_min + span * along
        left = min(max(cx - width / 2, x_min), x_max - width)
        return (left, y_min + inset, left + width, y_max - inset), faces

    span = y_max - y_min
    if span < width:
        return None
    cy = y_min + span * along
    low = min(max(cy - width / 2, y_min), y_max - width)
    return (x_min + inset, low, x_max - inset, low + width), faces


def breaches(floor: RefinedFloor, program: Program) -> list[str]:
    """Rooms that are legal by the tiling and illegal once the walls are real.

    **This should now always come back empty, and that is the point of keeping it.**
    Stage ⑤ once checked minimums against centreline rectangles while the bye-laws mean
    clear internal size, and three of the four reference briefs reported a clean plan
    while carrying six or seven rooms below a minimum. `score.clear_dims` and
    `_clear_rect` now apply the same rule, so the two agree by construction — and this
    is what fails loudly if they ever stop.

    An independent recomputation rather than a call into the scorer, deliberately: a
    guard that shares its implementation with the thing it guards cannot catch the
    thing going wrong.

    See DECISIONS.md question 8.
    """
    specs = {room.id: room for room in program.rooms}
    found: list[str] = []
    for room_id, (x_min, y_min, x_max, y_max) in sorted(floor.clear.items()):
        spec = specs.get(room_id)
        if spec is None:
            continue
        area = max(0.0, x_max - x_min) * max(0.0, y_max - y_min)
        width = min(max(0.0, x_max - x_min), max(0.0, y_max - y_min))
        if area < spec.min_area_sq_m - TOLERANCE_M:
            found.append(
                f"{room_id} has {area_text(area)} of floor inside its walls, below the "
                f"{area_text(spec.min_area_sq_m)} minimum"
            )
        if width < spec.min_width_m - TOLERANCE_M:
            found.append(
                f"{room_id} is {length_text(width)} clear across, below the "
                f"{length_text(spec.min_width_m)} minimum width"
            )
        length = max(max(0.0, x_max - x_min), max(0.0, y_max - y_min))
        if spec.min_sizes_m:
            fits = [
                (short, long) for short, long in spec.min_sizes_m
                if width >= short - TOLERANCE_M and length >= long - TOLERANCE_M
            ]
            if not fits:
                shapes = " or ".join(
                    f"{feet_and_inches(short)} x {feet_and_inches(long)}"
                    for short, long in spec.min_sizes_m
                )
                found.append(
                    f"{room_id} is {feet_and_inches(width)} x {feet_and_inches(length)} "
                    f"inside its walls, and a {spec.kind.value.replace('_', ' ')} needs at "
                    f"least {shapes}"
                )
        if spec.min_length_m and length < spec.min_length_m - TOLERANCE_M:
            found.append(
                f"{room_id} is {length_text(length)} long inside its walls, below the "
                f"{length_text(spec.min_length_m)} minimum length"
            )
    return found


def _connect(
    walls: list[Wall], layout: Layout, program: Program, openings: list[Opening], rules: dict
) -> list[Opening]:
    """Add whatever further doors the plan needs to be walkable.

    **The adjacency graph is a door schedule, not the whole door schedule.** Stage ③
    says where doors *should* be and stage ⑤ satisfies about half of what it asks for,
    so honouring only those edges produced houses you could not walk through — one room
    of eleven reachable from the front door on a 30x50. Stage ⑦ measures it; this is
    what fixes it.

    Connectivity is a much weaker requirement than adjacency, and that is why it can be
    met here when the ceiling in DECISIONS question 6 says the adjacency graph cannot.
    A room does not need a door to the room stage ③ named — it needs a door to
    *something* already reachable, and a tiling gives almost every room several
    neighbours to choose from.

    Two things are never crossed. A `SEPARATED` edge is hard and means it: a door from
    a bathroom into a kitchen would satisfy circulation by making the plan worse. And a
    wall too short to hold a door is not one — the opening has to fit.
    """
    forbidden = {
        frozenset({edge.a, edge.b})
        for edge in program.adjacencies
        if edge.relation is Relation.SEPARATED
    }
    kinds = {room.id: room.kind for room in program.rooms}
    clearance = rules["clearance_m"]
    narrow = {SpaceKind(k) for k in rules["narrow_kinds"]}

    # Where the walk starts depends on the storey: a ground floor is entered from the
    # street, a first floor off the stair. Requiring an entrance meant this returned
    # immediately on every upper floor, so no connecting doors were added at all and
    # stage ⑦ duly reported four rooms in five as unreachable.
    entrance = next((o for o in openings if o.kind is OpeningKind.ENTRANCE), None)
    if entrance is not None and entrance.connects:
        origin = entrance.connects[0]
    else:
        origin = next(
            (r.id for r in program.all_on_floor(layout.floor)
             if r.kind is SpaceKind.STAIRCASE),
            None,
        )
    if origin is None:
        return []

    reached = {origin}
    graph: dict[str, set[str]] = {}
    for opening in openings:
        if opening.kind in (OpeningKind.DOOR, OpeningKind.OPEN) and len(opening.connects) == 2:
            a, b = opening.connects
            graph.setdefault(a, set()).add(b)
            graph.setdefault(b, set()).add(a)

    def flood(origin: str) -> None:
        """Pull in everything now reachable, following doors that already exist.

        Marking only the room a new door opens into was a real bug and a quiet one:
        opening the dining room also reaches the hall and the kitchen through doors
        stage ⑤ had already earned, and a loop that does not notice believes the
        kitchen is still stranded. It then stops finding candidates while three rooms
        sit one door away from the plan.
        """
        stack = [origin]
        while stack:
            for neighbour in graph.get(stack.pop(), ()):
                if neighbour not in reached:
                    reached.add(neighbour)
                    stack.append(neighbour)

    flood(origin)

    # From the ruleset, via the programme — not a set of kinds written out here. The
    # identical list lived in this module and in `validator`, and two copies of the
    # same judgment is a drift waiting to happen: ⑥ connecting one set of rooms while
    # ⑦ checks another would read as a clean plan.
    walk_in = {room.id for room in program.rooms if room.needs_door}
    wanted = {placed.room_id for placed in layout.rooms if placed.room_id in walk_in}
    added: list[Opening] = []

    # Grow outwards from what is already reachable, one room at a time — and only ever
    # *through* a space you may pass through.
    #
    # Preferring circulation was not enough, and the difference is the whole point. A
    # 40x60 came out with the corridor reachable only through a bedroom, so the route
    # to the master bedroom's bathroom ran hall → dining → bed2 → corridor → bed1 →
    # bath1: every room reachable, stage ⑦ satisfied, and a plan nobody would live in.
    # A preference bends when nothing better is available, which is exactly when it
    # matters. This is a refusal instead: if the only way to reach a room is through a
    # bedroom, it stays unreached and ⑦ reports it, because that is a defect in the
    # tiling that ⑤ should have avoided and not one ⑥ can paper over.
    through = {room.id for room in program.rooms if room.is_through_route}

    def host_rank(room_id: str) -> int:
        kind = kinds.get(room_id)
        if kind in (SpaceKind.CORRIDOR, SpaceKind.FOYER):
            return 0
        if kind in (SpaceKind.HALL, SpaceKind.DINING, SpaceKind.STAIRCASE):
            return 1
        return 2

    # **Two passes, and the order is the whole fix.** Join the circulation to itself
    # first; attach everything else after.
    #
    # A single greedy pass looks right and is not. On a 40x60 it added a perfectly
    # reasonable kitchen → bedroom door, and that door incidentally pulled the corridor
    # into reach *through* the bedroom, along the corridor ↔ bedroom door stage ③ had
    # asked for. Nothing about the choice was wrong in isolation; the route it created
    # was. Ranking the host by kind cannot catch it either, because the host was the
    # kitchen and a kitchen is a perfectly good thing to walk through.
    #
    # Completing the spine first removes the opportunity: by the time bedrooms are
    # attached, every hall and corridor is already reachable without them.
    for spine_only in (True, False):
        progress = True
        while progress:
            progress = False
            candidates = []
            for wall in walls:
                if wall.kind is not WallKind.INTERIOR:
                    continue
                a, b = wall.rooms
                inside = {a, b} & reached
                outside = ({a, b} - reached) & wanted
                if len(inside) != 1 or len(outside) != 1:
                    continue
                if spine_only and not ({*inside, *outside} <= through):
                    continue
                # Hard, and weighed before anything else. An edit once left this behind
                # an unconditional `continue` and a bathroom got a door into a kitchen —
                # the one placement CLAUDE.md says every Indian client objects to,
                # arrived at while making the plan more walkable.
                if frozenset({a, b}) in forbidden:
                    continue
                candidates.append(
                    (0 if inside & through else 1, host_rank(next(iter(inside))),
                     -wall.length_m, wall)
                )

            for _, _, _, wall in sorted(candidates, key=lambda r: (r[0], r[1], r[2])):
                a, b = wall.rooms
                target = next(iter({a, b} - reached))
                width = (
                    rules["service_width_m"]
                    if kinds.get(a) in narrow or kinds.get(b) in narrow
                    else rules["internal_width_m"]
                )
                placed = _fit(
                    wall, width, clearance, openings + added,
                    floor_width=rules["service_width_m"],
                    at_an_end=SpaceKind.STAIRCASE in (kinds.get(a), kinds.get(b)),
                )
                if placed is None:
                    continue
                offset, fitted = placed
                added.append(
                    Opening(
                        wall_id=wall.id, kind=OpeningKind.DOOR, offset_m=offset,
                        width_m=fitted, connects=[a, b],
                    )
                )
                graph.setdefault(a, set()).add(b)
                graph.setdefault(b, set()).add(a)
                reached.add(target)
                flood(target)
                progress = True
                break
    return added


def _in_the_setback(layout: Layout, program: Program, envelope) -> list[PlacedRoom]:
    """Place whatever stage ③ marked as sitting outside the buildable rectangle.

    Centred on the road edge, in the strip between the envelope and the plot boundary.
    Centred rather than cornered because a car porch is the approach: it wants to be in
    front of the door, and with the entrance already constrained to the road-facing
    wall the middle of the strip is the closest this can get without knowing where in
    that wall the door landed.

    Whether the strip can hold a bay at all is stage ③'s decision — it refuses the
    whole programme when it cannot, rather than letting a drawing show a house with a
    car nowhere. By the time this runs the answer is yes.
    """
    wanted = [r for r in program.all_on_floor(layout.floor) if r.outside_envelope]
    if not wanted or envelope is None:
        return []

    from app.ir.enums import Facing

    road = envelope.road_edges[0]
    depth = envelope.setbacks[road]
    vertical_road = road in (Facing.NORTH, Facing.SOUTH)
    placed: list[PlacedRoom] = []

    for index, room in enumerate(wanted):
        short = room.min_width_m
        long = room.min_area_sq_m / short if short > 0 else 0.0
        # Whichever way round the strip can take: out from the house if it is deep
        # enough, along the road otherwise.
        out, across = (long, short) if depth >= long else (short, long)
        if depth < out:
            continue

        step = (index - (len(wanted) - 1) / 2) * (across + 0.3)
        if vertical_road:
            centre = (envelope.x_min_m + envelope.x_max_m) / 2 + step
            x_min, x_max = centre - across / 2, centre + across / 2
            y_min, y_max = (
                (envelope.y_max_m, envelope.y_max_m + out)
                if road is Facing.NORTH
                else (envelope.y_min_m - out, envelope.y_min_m)
            )
        else:
            centre = (envelope.y_min_m + envelope.y_max_m) / 2 + step
            y_min, y_max = centre - across / 2, centre + across / 2
            x_min, x_max = (
                (envelope.x_max_m, envelope.x_max_m + out)
                if road is Facing.EAST
                else (envelope.x_min_m - out, envelope.x_min_m)
            )

        placed.append(
            PlacedRoom(
                room_id=room.id, x_min_m=x_min, y_min_m=y_min,
                x_max_m=x_max, y_max_m=y_max,
            )
        )
    return placed


def draw(bundle, envelope=None):
    """Stage ⑥ over a whole `PlanBundle` — every storey refined, in one call.

    Takes the bundle and returns it with `floors` filled. The direction matters: ⑥
    depends on ⑤'s output and ⑤ knows nothing about walls, so `solver.plan` cannot do
    this itself without inverting the pipeline. A caller that wants rectangles stops
    after `plan`; one that wants a drawing calls this.
    """
    floors = [refine(layout, bundle.program, envelope or bundle.envelope)
              for layout in bundle.layouts]
    return bundle.model_copy(update={"floors": floors})
