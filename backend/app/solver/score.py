"""How good is a tiling? Every penalty here is a thing a person would object to.

A slicing tree hands back plans that are all equally *valid*. Ranking them is the
entire difference between a floor plan and a partitioned rectangle, so the weights are
data-shaped judgments and stated plainly rather than tuned until the output looked
nice.

Order of magnitude is the point: a room below its legal minimum is not a worse version
of a room with an awkward aspect ratio, it is a different kind of failure.
"""

from __future__ import annotations

import functools

from app.ir.enums import Relation, SpaceKind
from app.ir.layout import TOLERANCE_M, Layout
from app.ir.plan import Program
from app.ir.units import area_text, length_text
from app.rules import load_ruleset

# Illegal beats unpleasant. A room under its NBC minimum cannot be built at all; a
# kitchen in the wrong sector is a preference a user may not share.
# Float slack on every legal comparison. `effective_areas` allocates *exactly* the
# minimum when the budget is tight — which is when it matters most — and 17.999999 is
# not a violation of an 18.0 m² floor. Without this the scorer invents unbuildable
# rooms on precisely the plans it should be judging most carefully.
EPSILON = 1e-6


@functools.lru_cache(maxsize=1)
def wall_allowance() -> tuple[float, float]:
    """`(exterior, interior)` half-thicknesses, from the same ruleset stage ⑥ draws with.

    Stage ⑤ has to know how thick a wall is, and that is not a coupling to ⑥ — it is
    shared rule data. The alternative is what this codebase did until now: measure
    legality against lines of zero width and be wrong by half a wall on every side.
    """
    walls = load_ruleset("refine_v1").data["walls"]
    return walls["exterior_thickness_m"] / 2, walls["interior_thickness_m"] / 2


def clear_sides(placed, layout: Layout) -> tuple[float, float]:
    """`(width, depth)` of the floor inside the plaster.

    Each side is inset by half of whatever wall is on it, and which wall that is
    follows from position: a side on the plan's boundary is exterior, anything else is
    a partition. The same rule `refine._clear_rect` uses, and it has to stay the same
    rule — a solver that clears a room its own refiner then reports as illegal is worse
    than either alone.
    """
    outer, inner = wall_allowance()

    def inset(coordinate: float, edge: float) -> float:
        return outer if abs(coordinate - edge) <= TOLERANCE_M else inner

    width = (placed.x_max_m - placed.x_min_m) - (
        inset(placed.x_min_m, layout.x_min_m) + inset(placed.x_max_m, layout.x_max_m)
    )
    depth = (placed.y_max_m - placed.y_min_m) - (
        inset(placed.y_min_m, layout.y_min_m) + inset(placed.y_max_m, layout.y_max_m)
    )
    return max(0.0, width), max(0.0, depth)


def clear_dims(placed, layout: Layout) -> tuple[float, float]:
    """`(area, shortest side)` of the floor inside the plaster. See `clear_sides`."""
    width, depth = clear_sides(placed, layout)
    return width * depth, min(width, depth)

ILLEGAL = 100.0
# Just under ILLEGAL, and deliberately not a round fraction of it. A car bay the
# driveway cannot reach does not satisfy the parking requirement that put it in the
# programme, so it is much worse than an awkward aspect ratio — but it does not make
# the *room* unlawful, and `unbuildable` means "below a statutory minimum". Keeping it
# below 100 keeps that counter honest while still outranking everything soft.
#
# Measured across the four reference briefs at 40 / 90 / 200. At 40 the road defect
# survives on two briefs, outvoted by sector and adjacency terms. At 90 the 40x60 goes
# to zero road violations *and* its other penalties fall 240 → 120, so it is not a
# trade. At 200 the 30x50 also clears, but pays 195 → 270 for it — buying the last
# case with plans that are worse everywhere else.
# Just under ILLEGAL, and deliberately not a round fraction of it. A car bay the
# driveway cannot reach does not satisfy the parking requirement that put it in the
# programme, so it is much worse than an awkward aspect ratio — but it does not make
# the *room* unlawful, and `unbuildable` means "below a statutory minimum". Keeping it
# below 100 keeps that counter honest while still outranking everything soft.
#
# Raising it to 220 was tried, to make a broken circulation decisive, and it is worse.
# It reorders the Stage A ranking enough to change which topologies reach CP-SAT, and a
# 30x50 came back with two rooms below their minimums where it had none — buying a
# walkable plan with an illegal one, which is the trade this file exists to refuse.
# Stage ⑥ connecting the circulation before it attaches anything private is the fix
# that costs nothing.
INACCESSIBLE = 90.0

# How much of the smaller footprint two stacked rooms must share to count as the same
# shaft. Not 1.0: the floors tile independently, so the rectangles are never identical
# to the centimetre, and demanding that would reject every candidate. Not much lower
# either — at half, a "staircase" whose flights miss each other by a metre passes.
ALIGNMENT = 0.75

# The marker that makes broken circulation findable in a reason list. Ranking has to
# put it *ahead* of the penalty rather than inside it — see `circulation_is_broken`.
BROKEN_CIRCULATION = "the circulation is broken"


@functools.lru_cache(maxsize=1)
def _door_span() -> float:
    """The shortest wall that can hold a door, from the ruleset stage ⑥ places them by.

    Walkability has to ask the question ⑥ will later have to answer. Testing that two
    rooms merely *touch* is not it: a 0.4 m shared edge is a corner, not a doorway, and
    a 40x60 passed this check on one and came out with a room ⑥ could not reach.
    """
    doors = load_ruleset("refine_v1").data["doors"]
    return doors["service_width_m"] + 2 * doors["clearance_m"]
STRUCTURAL = 40.0
PREFERENCE = 5.0

# Points per metre a room falls short of the furniture it exists for, capped at
# STRUCTURAL. Per metre rather than a flat charge so the hill-climb can see a swap that
# makes a strip kitchen less of a strip; capped so no quantity of it reaches ILLEGAL,
# because a legal room that is hard to furnish is not an unbuildable one.
FURNISH_PER_M = 60.0
FURNISH_CAP = STRUCTURAL


@functools.lru_cache(maxsize=1)
def _furnish_tolerance() -> float:
    return load_ruleset("furnish_v1").data["grading"]["tolerance_m"]


def score(layout: Layout, program: Program) -> tuple[int, float, list[str]]:
    """`(unbuildable, penalty, reasons)` — ranked in that order, lexicographically.

    The count is separate from the penalty on purpose, and it is not a stylistic
    choice. Weighting alone lets many cheap violations outvote a few catastrophic
    ones: hill-climbing happily traded two rooms below their legal minimum for enough
    satisfied Vastu preferences to come out ahead on total. It produced a plan with a
    2.2 m² hall and called it an improvement.

    A room that cannot legally be built is not a worse version of a misplaced one. No
    quantity of preferences compensates, so the comparison has to be lexicographic
    rather than a large weight — a weight big enough today stops being big enough when
    the room count grows.

    Reasons travel with it because "score 47" tells nobody what to fix, and explaining
    that is stage ④'s whole job.
    """
    specs = {room.id: room for room in program.rooms}
    total = 0.0
    unbuildable = 0
    reasons: list[str] = []

    def fail(weight: float, reason: str) -> None:
        nonlocal total, unbuildable
        total += weight
        if weight >= ILLEGAL:
            unbuildable += 1
        reasons.append(reason)

    # Rooms open to each other are furnished as one space, below; not one strip at a time.
    open_pairs = _open_pairs(layout, program)
    open_rooms = {room for pair in open_pairs for room in pair[:2]}
    for a_id, b_id, short_m in open_pairs:
        if short_m > _furnish_tolerance():
            fail(
                min(FURNISH_CAP, FURNISH_PER_M * short_m),
                f"{a_id} and {b_id}, open to each other, are {length_text(short_m)} short "
                f"of the furniture they are for",
            )

    for placed in layout.rooms:
        spec = specs.get(placed.room_id)
        if spec is None:
            continue
        # Measured inside the walls, because that is what the bye-laws mean. A 2.1 m
        # minimum bedroom width is 2.1 m of floor, not 2.1 m between wall centres —
        # and this compared centreline rectangles until stage ⑥ made the gap visible.
        # Three of the four reference briefs reported zero unbuildable rooms and had
        # six or seven below a minimum once the walls were real. See DECISIONS q8.
        clear_area, clear_side = clear_dims(placed, layout)
        if clear_area < spec.min_area_sq_m - EPSILON:
            fail(
                ILLEGAL,
                f"{spec.id} has {area_text(clear_area)} of floor inside its walls, below "
                f"the {area_text(spec.min_area_sq_m)} minimum",
            )
        if clear_side < spec.min_width_m - EPSILON:
            fail(
                ILLEGAL,
                f"{spec.id} is {length_text(clear_side)} clear across, below the "
                f"{length_text(spec.min_width_m)} minimum width",
            )
        shape_short_m = spec.minimum_shape_shortfall_m(*clear_sides(placed, layout))
        if shape_short_m > EPSILON:
            fail(
                ILLEGAL,
                f"{spec.id} is below the smallest shape a "
                f"{spec.kind.value.replace('_', ' ')} must hold, by {length_text(shape_short_m)}",
            )
        clear_length = clear_area / clear_side if clear_side > 0 else 0.0
        if spec.min_length_m and clear_length < spec.min_length_m - EPSILON:
            fail(
                ILLEGAL,
                f"{spec.id} is {length_text(clear_length)} long inside its walls, below the "
                f"{length_text(spec.min_length_m)} minimum length",
            )
        # Grossly oversized is a defect too, and until this existed nothing measured
        # it. Exact tiling fixes the total area, so whatever the programme does not
        # ask for is forced into *some* room — on a 50x80, where 48% of the permitted
        # footprint is surplus, that produced an 18 m² bathroom and a 34.8 m² corridor.
        # Legal, gap-free, correctly scored 0 unbuildable, and not a house.
        #
        # Both tests have to fire. Relative alone flags a 5 m² pooja room against a
        # 2.5 m² target, which nobody would object to; absolute alone flags a large
        # hall for being large. Together they catch the room that is the wrong *kind*
        # of size.
        # Measured against the room's *ceiling* where it has one. Stage ③ grows targets
        # on a generous site, and this rule compared against the grown target — so a
        # bedroom whose ceiling is 16 m² could reach 29.9 m² and pass, which is the
        # defect the ceiling exists to prevent. A room with no ceiling does not grow,
        # and its target is its ceiling.
        ceiling = spec.max_target_sq_m or spec.target_area_sq_m
        excess = placed.area_sq_m - ceiling
        if placed.area_sq_m > 1.5 * ceiling and excess > 3.0:
            fail(
                STRUCTURAL,
                f"{spec.id} is {area_text(placed.area_sq_m)}, "
                f"{placed.area_sq_m / ceiling:.1f}x the {area_text(ceiling)} "
                f"it should ever be",
            )
        if placed.aspect > spec.max_aspect + EPSILON:
            fail(
                STRUCTURAL,
                f"{spec.id} is {placed.aspect:.1f}:1 — a corridor, not a "
                f"{spec.kind.value.replace('_', ' ')}",
            )
        # **Legal is not usable.** A 1.9 x 4.7 m kitchen clears every minimum and holds
        # no working aisle. Stage ⑦ reports it; pricing it here is what lets the search
        # avoid it, since ⑦ only ever chooses among plans the search already made.
        short_m = (
            0.0 if placed.room_id in open_rooms
            else spec.furnishing_shortfall_m(*clear_sides(placed, layout))
        )
        if short_m > _furnish_tolerance():
            fail(
                min(FURNISH_CAP, FURNISH_PER_M * short_m),
                f"{spec.id} is {length_text(short_m)} short of the furniture it is for",
            )
        if spec.needs_exterior_wall and not _on_the_boundary(placed, layout) and not any(
            # Open to a room that has one: one space, lit through either part.
            placed.room_id in pair[:2] and _on_the_boundary(
                layout.by_id(pair[1] if pair[0] == placed.room_id else pair[0]), layout
            )
            for pair in open_pairs
        ):
            fail(
                STRUCTURAL,
                f"{spec.id} has no external wall, so no window",
            )
        # The plan has to meet the street. Nothing measured this until now: stage ②
        # reads `road_edges` carefully enough to set a different setback per edge, and
        # then stage ⑤ placed the car bay wherever the tree happened to leave a gap —
        # on a 30x50 that put an 18 m² garage in the interior south-west, with no
        # route to it. Gap-free, legal, correctly scored, and a car cannot get in.
        #
        # INACCESSIBLE rather than ILLEGAL: the bye-laws size a garage, they do not
        # say which wall it touches, and `unbuildable` is reserved for a room below a
        # statutory floor. Nowhere near a PREFERENCE either — a driveway through the
        # neighbour's plot is not a matter of taste.
        if spec.needs_road_access and layout.road_edges:
            if not _on_a_road_edge(placed, layout):
                fail(
                    INACCESSIBLE,
                    f"{spec.id} does not reach the "
                    f"{'/'.join(e.value for e in layout.road_edges)} road",
                )
        # A staircase that does not sit above the one below is not a staircase, it is
        # two holes in two floors. `plan` fixes the shaft on the ground floor and every
        # storey above is scored against it — the same weight as an unreachable car
        # bay, because it is the same kind of defect: a thing that cannot be got to.
        #
        # Keyed by kind. Stage ③ names the staircase `stair1` downstairs and `stair2`
        # up, so the first version keyed on room id, matched nothing, and reported a
        # perfectly aligned building that was not one.
        #
        # Hill-climbing is what actually fixes it. `improve` swaps which room occupies
        # which rectangle without touching the geometry, so a scored misalignment lets
        # it move the staircase onto the shaft whenever the tiling has a rectangle
        # there — which is why this is a penalty rather than a constraint on the tree.
        # And it has to rise somewhere the building actually is. A 30x40 G+1 put the
        # ground-floor stair at y 7.30-8.70 while the upper storey's footprint stopped
        # at 6.99 — under the shaft was open sky, so no arrangement upstairs could ever
        # have aligned with it. Scoring alignment alone chased a placement that did not
        # exist; the zone is what makes one possible.
        if spec.kind is SpaceKind.STAIRCASE and layout.shaft_zone is not None:
            if not _inside(placed, layout.shaft_zone):
                fail(
                    INACCESSIBLE,
                    f"{spec.id} rises through part of the plan that has no floor above",
                )
        target = layout.shafts.get(spec.kind)
        if target is not None and _overlap(placed, target) < ALIGNMENT:
            fail(
                INACCESSIBLE,
                f"{spec.id} does not land on the {spec.kind.value.replace('_', ' ')} "
                f"on the floor below",
            )
        if spec.sector is not None:
            actual = layout.sector_of(placed)
            if actual is not spec.sector:
                weight = ILLEGAL if spec.sector_is_hard else PREFERENCE
                fail(
                    weight,
                    f"{spec.id} landed {actual.value.replace('_', ' ')}, "
                    f"wanted {spec.sector.value.replace('_', ' ')}",
                )

    # **Can this tiling be walked at all, without going through a bedroom?**
    #
    # No doors exist yet, so this asks the question doors will later answer: is every
    # space joined to the entrance by a chain of *touching* rooms whose intermediate
    # links are ones you may pass through. If such a chain exists, stage ⑥ can put
    # doors along it; if it does not, no arrangement of doors will save the plan.
    #
    # Scoring the adjacency edges alone was not enough, and the failure was
    # instructive. A 40x60 left `hall ↔ corridor` unsatisfied, took the 40-point
    # structural hit, and won anyway — so ⑥ reached the corridor the only way left to
    # it, through a bedroom, and the route to the master bedroom's bathroom ran
    # hall → dining → bed2 → corridor → bed1 → bath1. Every room reachable, every check
    # passed, a plan nobody would live in. A stranded corridor is not forty points
    # worse than a tidy one; it is a different kind of thing.
    # Once, not once per room. Counting each stranded room separately let the tally
    # swamp everything else it was supposed to be weighed against: three stranded rooms
    # outscored the foyer losing its road access, and the solver duly returned a plan
    # with no front door — walkable in principle and impossible to enter. It is one
    # defect, "this plan cannot be walked", which is how stage ⑦ reports it too.
    stranded = _unwalkable(layout, program)
    if stranded:
        fail(
            INACCESSIBLE,
            f"{BROKEN_CIRCULATION} — {', '.join(stranded)} can only be reached "
            f"by walking through a private room",
        )

    for edge in program.adjacencies:
        a, b = layout.by_id(edge.a), layout.by_id(edge.b)
        if a is None or b is None:
            continue
        touching = a.touches(b)
        weight = STRUCTURAL if edge.hard else PREFERENCE
        if edge.relation is Relation.SEPARATED and touching:
            fail(weight, f"{edge.a} shares a wall with {edge.b}, which it must not")
        elif edge.relation is Relation.NEAR:
            # Doors do not exist yet, so the walk is read along the programme's own door
            # edges, centre to centre — the route ⑦ will walk through the doors ⑥ puts
            # on those edges. The straight grid distance was too kind: it passed a
            # bedroom whose only way from the foyer ran the length of the corridor.
            if _programme_walk(layout, program, edge.b, edge.a) > _near_limit():
                fail(weight, f"{edge.a} is not near {edge.b}")
        elif edge.relation is not Relation.SEPARATED and not touching:
            fail(weight, f"{edge.a} does not reach {edge.b}")

    return unbuildable, total, reasons


def circulation_is_broken(reasons: list[str]) -> bool:
    """Does this plan make a private room do the corridor's job?

    **Ranked ahead of the penalty, not inside it.** As a weight it was 90 points, and
    on a 40x60 that produced an exact tie: the best plan with a sound spine scored 115
    on preferences, the best with a bedroom serving as a corridor scored 25 plus the
    90, and the tie broke on generation index. The pipeline shipped a house where the
    route to the kitchen ran through the master bedroom.

    CLAUDE.md already contains the argument against fixing that with a bigger number —
    "a weight big enough today stops being big enough when the room count grows", which
    is exactly what happens here as more rooms bring more preferences to outvote it. So
    it is lexicographic, below `unbuildable` and above everything else: no quantity of
    satisfied sectors buys a bedroom you have to walk through, and no broken corridor
    justifies a room below its legal minimum.
    """
    return any(BROKEN_CIRCULATION in reason for reason in reasons)


def _shared_wall_m(a, b) -> float:
    """How much wall two rooms actually share. 0.0 when they only meet at a corner."""
    if abs(a.x_max_m - b.x_min_m) <= TOLERANCE_M or abs(b.x_max_m - a.x_min_m) <= TOLERANCE_M:
        return max(0.0, min(a.y_max_m, b.y_max_m) - max(a.y_min_m, b.y_min_m))
    if abs(a.y_max_m - b.y_min_m) <= TOLERANCE_M or abs(b.y_max_m - a.y_min_m) <= TOLERANCE_M:
        return max(0.0, min(a.x_max_m, b.x_max_m) - max(a.x_min_m, b.x_min_m))
    return 0.0


def _unwalkable(layout: Layout, program: Program) -> list[str]:
    """Circulation spaces that can only be reached by walking through a private room.

    **The rule is narrower than "no private room is ever a passage", and the narrowing
    matters.** An en-suite is reached through its bedroom and that is the point of an
    en-suite; a first version flagged every one of them. What is wrong is the other
    direction — reaching the *corridor* through a bedroom, which turns that bedroom
    into a passage and is what CLAUDE.md means by "bedrooms open off the corridor".

    So: the circulation spine must hang together on its own. Every through-route space
    has to be reachable from the entrance across doorways between through-route spaces
    only. What hangs off the spine afterwards — bedrooms, their bathrooms — is the
    programme's business and not this check's.
    """
    specs = {room.id: room for room in program.rooms}
    placed = {r.room_id: r for r in layout.rooms}
    spine = {
        r.id for r in program.rooms
        if r.is_through_route and r.needs_door and r.id in placed
    }
    start = next(
        (r.id for r in program.rooms if r.kind is SpaceKind.FOYER and r.id in spine),
        None,
    )
    if start is None:
        return []

    seen = {start}
    queue = [start]
    while queue:
        here = placed[queue.pop()]
        for other_id in spine - seen:
            if _shared_wall_m(here, placed[other_id]) >= _door_span() - EPSILON:
                seen.add(other_id)
                queue.append(other_id)

    return sorted(spine - seen)


def _inside(placed, zone: tuple[float, float, float, float]) -> bool:
    """Is the room wholly within the zone? Whole, not mostly — half a stair is none."""
    x_min, y_min, x_max, y_max = zone
    return (
        placed.x_min_m >= x_min - EPSILON
        and placed.y_min_m >= y_min - EPSILON
        and placed.x_max_m <= x_max + EPSILON
        and placed.y_max_m <= y_max + EPSILON
    )


def _overlap(a, b) -> float:
    """Shared area as a fraction of the smaller room. 0.0 when they miss entirely."""
    wide = min(a.x_max_m, b.x_max_m) - max(a.x_min_m, b.x_min_m)
    tall = min(a.y_max_m, b.y_max_m) - max(a.y_min_m, b.y_min_m)
    if wide <= 0 or tall <= 0:
        return 0.0
    return (wide * tall) / min(a.area_sq_m, b.area_sq_m)


def off_the_road(layout: Layout, program: Program) -> int:
    """How many rooms that must meet the street do not.

    A count, not a weight, because `improve` ranks by it. Stage ⑦ refuses both cases it
    covers — a car bay no driveway reaches, and a foyer with no road-facing wall, which
    leaves the house with no front door — so for a plan ⑦ will judge, road access is not
    a preference a swap may trade away.
    """
    if not layout.road_edges:
        return 0
    street = {room.id for room in program.rooms if room.needs_road_access}
    return sum(
        1
        for placed in layout.rooms
        if placed.room_id in street and not _on_a_road_edge(placed, layout)
    )


@functools.lru_cache(maxsize=1)
def _near_limit() -> float:
    return load_ruleset("circulation_v1").data["near"]["max_walk_m"]


def _centre_distance(a, b) -> float:
    """Grid distance between two rooms' centres."""
    ax, ay = (a.x_min_m + a.x_max_m) / 2, (a.y_min_m + a.y_max_m) / 2
    bx, by = (b.x_min_m + b.x_max_m) / 2, (b.y_min_m + b.y_max_m) / 2
    return abs(ax - bx) + abs(ay - by)


def _open_pairs(layout: Layout, program: Program) -> list[tuple[str, str, float]]:
    """`(a, b, shortfall)` for every `OPEN` edge whose rooms share a wall."""
    from app.ir.plan import open_pair_shortfall_m

    placed = {room.room_id: room for room in layout.rooms}
    specs = {room.id: room for room in program.rooms}
    _, inner = wall_allowance()
    out = []
    for edge in program.adjacencies:
        if edge.relation is not Relation.OPEN:
            continue
        a, b = placed.get(edge.a), placed.get(edge.b)
        if a is None or b is None or not a.touches(b):
            continue
        wa, da = clear_sides(a, layout)
        wb, db = clear_sides(b, layout)
        side_by_side_in_x = (
            abs(a.x_max_m - b.x_min_m) <= TOLERANCE_M or abs(b.x_max_m - a.x_min_m) <= TOLERANCE_M
        )
        if side_by_side_in_x:
            across = wa + wb + 2 * inner
            along = min(a.y_max_m, b.y_max_m) - max(a.y_min_m, b.y_min_m) - 2 * inner
        else:
            across = da + db + 2 * inner
            along = min(a.x_max_m, b.x_max_m) - max(a.x_min_m, b.x_min_m) - 2 * inner
        short = open_pair_shortfall_m(
            specs[edge.a].usable_sizes_m, specs[edge.b].usable_sizes_m, across, along
        )
        out.append((edge.a, edge.b, short))
    return out


def _programme_walk(layout: Layout, program: Program, start: str, end: str) -> float:
    """The shortest proper walk from `start` to `end` the tiling allows, before doors.

    Doors go where stage ⑥ will put them: on a `CONNECTED` edge, or between two rooms a
    person may walk through. Each step runs to the middle of the wall the two rooms share
    — ⑥ centres its doors — so a corridor is crossed door to door, not through its middle,
    which made every bedroom off a long corridor read as far. Only walk-through rooms are
    passed on the way. Infinite when the tiling leaves no such walk.
    """
    import heapq

    placed = {room.room_id: room for room in layout.rooms}
    specs = {room.id: room for room in program.rooms}
    connected = {
        frozenset((edge.a, edge.b)) for edge in program.adjacencies
        if edge.relation in (Relation.CONNECTED, Relation.OPEN)
    }
    if start not in placed or end not in placed:
        return float("inf")

    def through(room_id: str) -> bool:
        spec = specs.get(room_id)
        return spec is not None and spec.is_through_route

    def door(a, b) -> tuple[float, float] | None:
        """The middle of the wall two rooms share, or None if they do not share one."""
        if not a.touches(b):
            return None
        if abs(a.x_max_m - b.x_min_m) <= TOLERANCE_M or abs(b.x_max_m - a.x_min_m) <= TOLERANCE_M:
            x = a.x_max_m if abs(a.x_max_m - b.x_min_m) <= TOLERANCE_M else a.x_min_m
            lo, hi = max(a.y_min_m, b.y_min_m), min(a.y_max_m, b.y_max_m)
            return x, (lo + hi) / 2
        y = a.y_max_m if abs(a.y_max_m - b.y_min_m) <= TOLERANCE_M else a.y_min_m
        lo, hi = max(a.x_min_m, b.x_min_m), min(a.x_max_m, b.x_max_m)
        return (lo + hi) / 2, y

    def centre(room) -> tuple[float, float]:
        return (room.x_min_m + room.x_max_m) / 2, (room.y_min_m + room.y_max_m) / 2

    origin = centre(placed[start])
    best: dict[str, float] = {}
    queue = [(0.0, start, origin)]
    while queue:
        walked, here, at = heapq.heappop(queue)
        if here == end:
            ex, ey = centre(placed[end])
            return walked + abs(at[0] - ex) + abs(at[1] - ey)
        if walked > best.get(here, float("inf")):
            continue
        best[here] = walked
        if here != start and not through(here):
            continue  # arrived somewhere private; nobody walks on from here
        for there, room in placed.items():
            if there == here:
                continue
            # Into the destination only by a door the programme asked for, when it is a
            # room nobody walks through: a bedroom entered off the dining room is a walk
            # the circulation engine grades major, not a short one.
            if frozenset((here, there)) not in connected and not (through(here) and through(there)):
                continue
            spot = door(placed[here], room)
            if spot is None:
                continue
            step = walked + abs(at[0] - spot[0]) + abs(at[1] - spot[1])
            if step < best.get(there, float("inf")):
                heapq.heappush(queue, (step, there, spot))
    return float("inf")


def off_the_shaft(layout: Layout, program: Program) -> int:
    """How many shafts on this storey miss the one below.

    A count for the same reason `off_the_road` is one. Stage ⑦ refuses a storey whose
    stair does not sit over the stair below — there is no way up to it — and as a
    weighted term it lost: the 30x40 stilt plan and a 30x50 from the model both shipped
    a first floor whose stair missed the one beneath by metres.
    """
    kinds = {room.id: room.kind for room in program.rooms}
    missed = 0
    # And from underneath: a stair with no floor above it is no way up either. It was a
    # weighted term only, and once `score` priced furniture, several strip bedrooms
    # outvoted it and a ground floor put its stair under open sky.
    if layout.shaft_zone is not None:
        missed += sum(
            1 for placed in layout.rooms
            if kinds.get(placed.room_id) is SpaceKind.STAIRCASE
            and not _inside(placed, layout.shaft_zone)
        )
    if not layout.shafts:
        return missed
    for kind, below in layout.shafts.items():
        here = [placed for placed in layout.rooms if kinds.get(placed.room_id) is kind]
        if here and not any(_overlap(placed, below) >= ALIGNMENT for placed in here):
            missed += 1
    return missed


def _on_a_road_edge(placed, layout: Layout) -> bool:
    """Does the room touch one of the boundaries that fronts a road?

    Stricter than `_on_the_boundary`, and the difference is the whole point: a room on
    the rear wall has an external wall and no street. A corner plot has two road edges
    and touching either one is enough.
    """
    from app.ir.enums import Facing
    from app.ir.layout import TOLERANCE_M

    reaches = {
        Facing.NORTH: abs(placed.y_max_m - layout.y_max_m) <= TOLERANCE_M,
        Facing.SOUTH: abs(placed.y_min_m - layout.y_min_m) <= TOLERANCE_M,
        Facing.EAST: abs(placed.x_max_m - layout.x_max_m) <= TOLERANCE_M,
        Facing.WEST: abs(placed.x_min_m - layout.x_min_m) <= TOLERANCE_M,
    }
    return any(reaches.get(edge, False) for edge in layout.road_edges)


def _on_the_boundary(placed, layout: Layout) -> bool:
    """Does the room touch the outside of the plan? No boundary, no window.

    A tolerance rather than equality: the room's edge arrives from float division down
    the tree, and a room a micron short of the wall still has a window.
    """
    from app.ir.layout import TOLERANCE_M

    return (
        abs(placed.x_min_m - layout.x_min_m) <= TOLERANCE_M
        or abs(placed.x_max_m - layout.x_max_m) <= TOLERANCE_M
        or abs(placed.y_min_m - layout.y_min_m) <= TOLERANCE_M
        or abs(placed.y_max_m - layout.y_max_m) <= TOLERANCE_M
    )
