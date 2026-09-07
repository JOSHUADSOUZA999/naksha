"""How good is a tiling? Every penalty here is a thing a person would object to.

A slicing tree hands back plans that are all equally *valid*. Ranking them is the
entire difference between a floor plan and a partitioned rectangle, so the weights are
data-shaped judgments and stated plainly rather than tuned until the output looked
nice.

Order of magnitude is the point: a room below its legal minimum is not a worse version
of a room with an awkward aspect ratio, it is a different kind of failure.
"""

from __future__ import annotations

from app.ir.enums import Relation
from app.ir.layout import Layout
from app.ir.plan import Program

# Illegal beats unpleasant. A room under its NBC minimum cannot be built at all; a
# kitchen in the wrong sector is a preference a user may not share.
# Float slack on every legal comparison. `effective_areas` allocates *exactly* the
# minimum when the budget is tight — which is when it matters most — and 17.999999 is
# not a violation of an 18.0 m² floor. Without this the scorer invents unbuildable
# rooms on precisely the plans it should be judging most carefully.
EPSILON = 1e-6

ILLEGAL = 100.0
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
        if placed.area_sq_m < spec.min_area_sq_m - EPSILON:
            fail(
                ILLEGAL,
                f"{spec.id} is {placed.area_sq_m:.1f} m², below the "
                f"{spec.min_area_sq_m:.1f} m² minimum",
            )
        if placed.shortest_side_m < spec.min_width_m - EPSILON:
            fail(
                ILLEGAL,
                f"{spec.id} is {placed.shortest_side_m:.2f} m across, below the "
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
        excess = placed.area_sq_m - spec.target_area_sq_m
        if placed.area_sq_m > 2 * spec.target_area_sq_m and excess > 3.0:
            fail(
                STRUCTURAL,
                f"{spec.id} is {placed.area_sq_m:.1f} m², "
                f"{placed.area_sq_m / spec.target_area_sq_m:.1f}x the "
                f"{spec.target_area_sq_m:.1f} m² it needs",
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
        if spec.sector is not None:
            actual = layout.sector_of(placed)
            if actual is not spec.sector:
                weight = ILLEGAL if spec.sector_is_hard else PREFERENCE
                fail(
                    weight,
                    f"{spec.id} landed {actual.value.replace('_', ' ')}, "
                    f"wanted {spec.sector.value.replace('_', ' ')}",
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
