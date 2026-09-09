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

from app.ir.enums import Facing, FixtureKind, OpeningKind, Relation, SpaceKind, WallKind
from app.ir.layout import TOLERANCE_M, Layout, PlacedRoom
from app.ir.plan import Program
from app.ir.refined import Fixture, Opening, RefinedFloor, Wall
from app.rules import load_ruleset

REFINE_RULES = "refine_v1"


def refine(layout: Layout, program: Program) -> RefinedFloor:
    """Walls and openings for one storey."""
    rules = load_ruleset(REFINE_RULES).data
    walls = _walls(layout, rules["walls"])
    openings = _doors(walls, layout, program, rules["doors"])
    openings += _connect(walls, layout, program, openings, rules["doors"])
    openings += _windows(walls, program, rules["windows"], taken=openings)
    fixtures = _fixtures(layout, program, walls, openings, rules)
    clear = {
        placed.room_id: _clear_rect(placed, layout, rules["walls"])
        for placed in layout.rooms
    }
    return RefinedFloor(
        floor=layout.floor, walls=walls, openings=openings,
        fixtures=fixtures, clear=clear,
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

        counter: tuple[tuple[float, float, float, float], "Facing"] | None = None
        for name in schedule:
            size = sizes[name]
            if name in _ON_THE_COUNTER and counter is not None:
                # A sink on one wall and the counter on another is a kitchen nobody
                # cooks in. These two sit *in* the run, so they are positioned along it
                # rather than sent looking for a wall of their own.
                spot = _on_counter(counter, size["width_m"], name)
            else:
                spot = _against_a_wall(
                    clear, size["width_m"], size["depth_m"], blocked + taken
                )
            if spot is None:
                continue
            rect, faces = spot
            if name == "counter":
                counter = (rect, faces)
            # The counter is not an obstacle to what stands on it.
            if name not in _ON_THE_COUNTER:
                taken.append(rect)
            out.append(
                Fixture(
                    kind=FixtureKind(name), room_id=placed.room_id,
                    x_min_m=rect[0], y_min_m=rect[1], x_max_m=rect[2], y_max_m=rect[3],
                    faces=faces,
                )
            )
    return out


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
        if opening.kind is OpeningKind.WINDOW:
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


def _against_a_wall(
    clear: tuple[float, float, float, float],
    width: float,
    depth: float,
    blocked: list[tuple[float, float, float, float]],
) -> tuple[tuple[float, float, float, float], "Facing"] | None:
    """Back the fixture onto whichever wall it fits against, trying corners first.

    Corners first because that is where furniture goes: a bed in the middle of a wall
    and a bed in the corner both fit, and only one of them leaves a usable room. The
    order of the sides is fixed rather than clever, so the same plan furnishes the same
    way twice — a drawing that reshuffles itself between runs is one nobody can discuss.
    """
    from app.ir.enums import Facing

    x_min, y_min, x_max, y_max = clear
    if x_max - x_min <= 0 or y_max - y_min <= 0:
        return None

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
    for faces, rect in plans:
        if rect[0] < x_min - 1e-9 or rect[1] < y_min - 1e-9:
            continue
        if rect[2] > x_max + 1e-9 or rect[3] > y_max + 1e-9:
            continue
        if any(_hits(rect, other) for other in blocked):
            continue
        return rect, faces
    return None


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
                f"{room_id} has {area:.1f} m² of floor inside its walls, below the "
                f"{spec.min_area_sq_m:.1f} m² minimum"
            )
        if width < spec.min_width_m - TOLERANCE_M:
            found.append(
                f"{room_id} is {width:.2f} m clear across, below the "
                f"{spec.min_width_m:.2f} m minimum width"
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

    entrance = next((o for o in openings if o.kind is OpeningKind.ENTRANCE), None)
    if entrance is None or not entrance.connects:
        return []

    reached = {entrance.connects[0]}
    graph: dict[str, set[str]] = {}
    for opening in openings:
        if opening.kind is OpeningKind.DOOR and len(opening.connects) == 2:
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

    flood(entrance.connects[0])

    # From the ruleset, via the programme — not a set of kinds written out here. The
    # identical list lived in this module and in `validator`, and two copies of the
    # same judgment is a drift waiting to happen: ⑥ connecting one set of rooms while
    # ⑦ checks another would read as a clean plan.
    walk_in = {room.id for room in program.rooms if room.needs_door}
    wanted = {placed.room_id for placed in layout.rooms if placed.room_id in walk_in}
    added: list[Opening] = []

    # Grow outwards from what is already reachable, one room at a time. Circulation
    # spaces are preferred as the host so new doors land on the corridor and the hall
    # rather than turning a bedroom into a through-route — which is exactly the privacy
    # the corridor exists to protect.
    def host_rank(room_id: str) -> int:
        kind = kinds.get(room_id)
        if kind in (SpaceKind.CORRIDOR, SpaceKind.FOYER):
            return 0
        if kind in (SpaceKind.HALL, SpaceKind.DINING, SpaceKind.STAIRCASE):
            return 1
        return 2

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
            if frozenset({a, b}) in forbidden:
                continue
            candidates.append((host_rank(next(iter(inside))), -wall.length_m, wall))

        for _, _, wall in sorted(candidates, key=lambda row: (row[0], row[1])):
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
