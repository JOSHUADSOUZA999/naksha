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


def clear_dims(placed, layout: Layout) -> tuple[float, float]:
    """`(area, shortest side)` of the floor inside the plaster.

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
    return max(0.0, width) * max(0.0, depth), max(0.0, min(width, depth))

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
                f"{spec.id} has {clear_area:.1f} m² of floor inside its walls, below "
                f"the {spec.min_area_sq_m:.1f} m² minimum",
            )
        if clear_side < spec.min_width_m - EPSILON:
            fail(
                ILLEGAL,
                f"{spec.id} is {clear_side:.2f} m clear across, below the "
                f"{spec.min_width_m:.2f} m minimum width",
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
                f"{spec.id} is {placed.area_sq_m:.1f} m², "
                f"{placed.area_sq_m / ceiling:.1f}x the {ceiling:.1f} m² "
                f"it should ever be",
            )
        if placed.aspect > spec.max_aspect + EPSILON:
            fail(
                STRUCTURAL,
                f"{spec.id} is {placed.aspect:.1f}:1 — a corridor, not a "
                f"{spec.kind.value.replace('_', ' ')}",
            )
        if spec.needs_exterior_wall and not _on_the_boundary(placed, layout):
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
