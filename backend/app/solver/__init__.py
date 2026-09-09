"""Stage ⑤ LAYOUT — `Program` + `Envelope` to ranked floor plans.

**Stage A only.** Decision 2 splits layout in two: a slicing tree produces gap-free
tilings by construction, then CP-SAT tunes dimensions with adjacency already fixed.
This is the first half. It generates many candidates cheaply, scores them, and returns
the best — good enough to see whether the approach produces houses at all, which is
the question the second half is not worth building until someone has answered.

Deterministic given a seed, and the seed is returned with the plan. A layout you
cannot reproduce is one you cannot debug.
"""

from __future__ import annotations

import random

from app.ir.envelope import Envelope
from app.ir.layout import Layout
from app.ir.plan import Program

from . import score as _score
from . import slicing, tuning

# 900 beat 300 by roughly a quarter on mean score and costs ~130 ms a floor, which is
# still inside the interactive budget decision 2 was chosen to protect. Generation is
# the cheap half; only the shortlist gets hill-climbed.
DEFAULT_CANDIDATES = 900

# Candidates to hill-climb, as a multiple of how many are kept. Improving is far more
# expensive than generating, so only the shortlist earns it — and the ranking after
# improvement differs from the ranking before, which is why the shortlist is wider
# than what is returned.
# 24, measured. Ranking happens *before* hill-climbing, but hill-climbing is what
# fixes sector violations — so a candidate that looks mediocre pre-climb can win
# post-climb, and too narrow a shortlist never gives it the chance. Over 12 seeds:
# 8 → mean 212 / worst 305; 24 → 206 / 280; 64 and 150 → identical to 24 at two and
# six times the cost. The knee is sharp.
IMPROVE_SHORTLIST = 24

# Stage B's shortlist, and the limit per topology. 24 is a deliberate ceiling rather
# than a search: on a marginal floor the feasible rate runs about 1 in 60, and hunting
# it costs eight seconds to *maybe* return a plan the user cannot regenerate tomorrow.
# Stage ④ says "this floor is over-programmed, here is what to change" instead, which
# is the more useful answer and arrives immediately. The limit is tight because
# quality is flat in it — 0.1 s and 1.0 s returned identical scores on every floor tested,
# because a topology CP-SAT can dimension it dimensions quickly, and one it cannot is
# usually proven infeasible in milliseconds. The time went entirely into models that
# are merely *hard to disprove*, which buy nothing: the next topology is free.
TUNE_SHORTLIST = 24
TUNE_SECONDS = 0.15

# Tuned candidates to collect before choosing. Was 3, and 3 is too few now that the
# shortlist interleaves two groups: the first three successes came from whichever
# group led, so the other never got compared. Six is enough for both to land and still
# well inside the interactive budget — the four reference briefs run in 0.3-0.9 s.
TUNE_KEEP = 6


# How much more than the programme asks for the house may spread into, where the
# envelope allows it.
#
# This is not a comfort margin, and treating it as one is a mistake worth naming: the
# tiling is *exact*, so whatever fraction is granted here is surplus that must land in
# some room. The oversize defect is therefore a direct function of this number, not a
# side effect of it. Measured on the two briefs with room to shrink, at 1.00 / 1.05 /
# 1.15 / 1.30: the 40x60 carries 0 / 0 / 2 / 3 grossly oversized rooms and the 50x80
# 1 / 1 / 3 / 3. Above about 1.05 the slack stops buying anything and starts showing
# up as a 14 m² corridor.
#
# Not 1.00, though: the 50x80 scores 290 at 1.00 against 250 at 1.05, because a little
# room lets Stage B meet a minimum width without taking it out of a neighbour.
GENEROSITY = 1.05


def footprint(
    envelope: Envelope, rooms: list, *, generosity: float = GENEROSITY
) -> tuple[float, float, float, float]:
    """The rectangle the house occupies — which is **not** the envelope.

    Max coverage is a ceiling, not a requirement: a house does not have to fill its
    setback lines. `Layout` tiles its bounds exactly, so tiling the envelope forced
    every square metre the programme never asked for into some room. On a 50x80,
    where 48% of the permitted footprint was surplus, that produced a 33 m² bathroom
    and a 36 m² foyer — legal, gap-free, zero unbuildable, and not a house.

    Two ceilings bind, and the second was being ignored entirely. `max_footprint_sq_m`
    is the smaller of the setback rectangle and the coverage cap, and the solver only
    ever saw the rectangle — so a 50x80 tiled 249.7 m² against a 241.5 m² cap and
    produced an over-covered plan that nothing in the pipeline reported.

    Shape and position are one choice, not two. The house keeps its full extent along
    the road and gives up depth, because side setbacks are what pin an Indian plot's
    width; and it sits at the **rear**, so the leftover falls in front, where the
    approach, the porch and the garden belong. A house centred in its envelope leaves
    two useless strips instead of one usable one.
    """
    east_west = envelope.x_max_m - envelope.x_min_m
    north_south = envelope.y_max_m - envelope.y_min_m
    unchanged = (envelope.x_min_m, envelope.y_min_m, envelope.x_max_m, envelope.y_max_m)

    wanted = sum(room.target_area_sq_m for room in rooms) * generosity
    # Never below what feasibility already cleared the programme at, never above what
    # the bye-laws permit. If the floor exceeds the ceiling the programme does not fit
    # at all — stage ④'s finding, not something to paper over here.
    area = min(max(wanted, sum(room.min_area_sq_m for room in rooms)),
               envelope.max_footprint_sq_m)

    # The depth axis runs perpendicular to the primary frontage.
    from app.ir.enums import Facing

    road = envelope.road_edges[0]
    along = east_west if road in (Facing.NORTH, Facing.SOUTH) else north_south
    if along <= 0:
        return unchanged
    depth = area / along
    span = north_south if road in (Facing.NORTH, Facing.SOUTH) else east_west
    if depth >= span:
        return unchanged            # the programme fills it; nothing to give back

    if road is Facing.NORTH:        # rear is south
        return (envelope.x_min_m, envelope.y_min_m, envelope.x_max_m, envelope.y_min_m + depth)
    if road is Facing.SOUTH:
        return (envelope.x_min_m, envelope.y_max_m - depth, envelope.x_max_m, envelope.y_max_m)
    if road is Facing.EAST:         # rear is west
        return (envelope.x_min_m, envelope.y_min_m, envelope.x_min_m + depth, envelope.y_max_m)
    return (envelope.x_max_m - depth, envelope.y_min_m, envelope.x_max_m, envelope.y_max_m)


def _interleave(primary: list, secondary: list) -> list:
    """Merge two ranked shortlists, best-first, alternating, without duplicates.

    Alternating rather than concatenating because the tuning loop stops as soon as it
    has enough dimensioned candidates — appending the second group would mean it was
    only ever reached when the first group failed outright.
    """
    merged: list = []
    seen: set[int] = set()
    for pair in zip(primary, secondary):
        for row in pair:
            if row[1] not in seen:          # row[1] is the generation index
                seen.add(row[1])
                merged.append(row)
    for row in [*primary, *secondary]:
        if row[1] not in seen:
            seen.add(row[1])
            merged.append(row)
    return merged


def improve(layout: Layout, program: Program, rounds: int = 4) -> Layout:
    """Hill-climb by swapping which room occupies which rectangle.

    The tiling is never touched — the rectangles stay exactly where the slicing tree
    put them, and only the room-to-rectangle assignment changes. So validity is
    preserved for free, the same way generation preserves it, and no swap can produce
    a gap.

    This exists because sector violations dominated every score: a random tree assigns
    rooms to leaves by shuffling, so landing nine Vastu preferences correctly was pure
    luck. Swapping is the cheapest thing that can fix it — and it fixes undersized
    rooms in the same pass, since a room that does not fit its rectangle often fits
    another one on the same floor.

    Greedy and first-improvement rather than best-improvement: with a dozen rooms the
    difference in quality is small and the difference in cost is not.
    """
    best = layout
    best_hard, best_soft, best_reasons = _score.score(best, program)

    for _ in range(rounds):
        moved = False
        for i in range(len(best.rooms)):
            for j in range(i + 1, len(best.rooms)):
                swapped = _swap(best, i, j)
                hard, soft, reasons = _score.score(swapped, program)
                # Lexicographic: never accept a swap that makes a room unbuildable,
                # however many preferences it satisfies in exchange.
                if (hard, soft) < (best_hard, best_soft - 1e-9):
                    best, best_hard, best_soft, best_reasons = swapped, hard, soft, reasons
                    moved = True
        if not moved:
            break

    return best.model_copy(
        update={"score": best_soft, "unbuildable": best_hard, "violations": best_reasons}
    )


def _swap(layout: Layout, i: int, j: int) -> Layout:
    """Exchange the occupants of two rectangles, leaving the geometry alone."""
    rooms = list(layout.rooms)
    rooms[i], rooms[j] = (
        rooms[i].model_copy(update={"room_id": rooms[j].room_id}),
        rooms[j].model_copy(update={"room_id": rooms[i].room_id}),
    )
    return layout.model_copy(update={"rooms": rooms})


def solve(
    program: Program,
    envelope: Envelope,
    *,
    floor: int = 1,
    candidates: int = DEFAULT_CANDIDATES,
    seed: int = 0,
    keep: int = 1,
    tune_seconds: float = TUNE_SECONDS,
) -> list[Layout]:
    """The best `keep` layouts out of `candidates` random topologies.

    Generate-and-rank rather than search: a slicing tree is cheap enough that trying
    two hundred and keeping the best beats reasoning about which one to try. That is
    only true because validity is free — every candidate tiles the envelope, so none
    of the budget is spent on plans that are not plans.
    """
    rooms = program.on_floor(floor)
    if not rooms:
        return []

    # The house, not the envelope. Everything below tiles *this* rectangle — the
    # allocation budget included, or the rooms would be sized for a footprint the
    # plan no longer occupies.
    x_min_m, y_min_m, x_max_m, y_max_m = footprint(envelope, rooms)
    area = (x_max_m - x_min_m) * (y_max_m - y_min_m)

    # Shrink towards minimums rather than scaling targets, so a tight budget cannot
    # push a room below the floor feasibility already cleared it at.
    weights = slicing.effective_areas(rooms, area)

    rng = random.Random(seed)
    scored: list[tuple[tuple[int, float], int, Layout, slicing.Node]] = []

    for index in range(candidates):
        tree = slicing.random_tree(rooms, rng, weights)
        placed = slicing.place(tree, x_min_m, y_min_m, x_max_m, y_max_m)
        try:
            layout = Layout(
                rooms=placed,
                x_min_m=x_min_m,
                y_min_m=y_min_m,
                x_max_m=x_max_m,
                y_max_m=y_max_m,
                floor=floor,
                road_edges=envelope.road_edges,
            )
        except ValueError:
            # A room collapsed to nothing — a tree deep enough that a leaf got a
            # sliver. Cheaper to discard the candidate than to constrain the
            # generator, since generating another costs microseconds.
            continue

        hard, penalty, reasons = _score.score(layout, program)
        scored.append(
            (
                (hard, penalty),
                index,
                layout.model_copy(
                    update={"score": penalty, "unbuildable": hard, "violations": reasons}
                ),
                tree,
            )
        )

    scored.sort(key=lambda row: (row[0], row[1]))

    # Stage A's rank is a poor predictor of post-tuning quality in one specific way,
    # and the road constraint is where it bites. A topology that puts the car bay on
    # the street tends to split the boundary early, which leaves narrow leaves — and
    # narrow leaves are exactly what Stage B fixes. So road-legal topologies rank badly
    # on a score that counts a defect the next stage removes, never reach the tuning
    # shortlist, and the pipeline returns a plan with an unreachable garage while a
    # better one sits at rank 200. Measured: 143 of 900 topologies on a 30x50 put both
    # road rooms on the street, and the best of them tuned to a *lower* penalty than
    # the plan actually returned.
    #
    # They are *added* to the shortlist, never promoted above it. Ordering the whole
    # shortlist by road-legality first was tried and is wrong: it puts a preference
    # ahead of the lexicographic key, and 30x40 went from 3 unbuildable rooms to 5 —
    # the precise trade CLAUDE.md forbids. Both groups get tuned and the post-tuning
    # re-rank, which is still lexicographic, decides. A road-legal candidate wins only
    # by being better.
    def unreachable(row) -> bool:
        return any("road" in reason for reason in row[2].violations)

    reachable = [row for row in scored if not unreachable(row)]
    shortlist = _interleave(scored[:TUNE_SHORTLIST], reachable[:TUNE_SHORTLIST])

    # Stage B. Stage A chose *which rooms neighbour which*; CP-SAT now chooses *how
    # wide*, which is the one thing a slicing tree structurally cannot: across 200
    # generated topologies on a tight ground floor, 0% broke a minimum area and 100%
    # broke a minimum width.
    tuned: list[tuple[tuple[int, float], int, Layout]] = []
    for _, index, _, tree in shortlist:
        dimensioned = tuning.tune(
            tree, (x_min_m, y_min_m, x_max_m, y_max_m), weights,
            time_limit_s=tune_seconds,
        )
        if dimensioned is None:
            continue  # this topology cannot be dimensioned legally; try the next
        try:
            layout = Layout(
                rooms=dimensioned,
                x_min_m=x_min_m,
                y_min_m=y_min_m,
                x_max_m=x_max_m,
                y_max_m=y_max_m,
                floor=floor,
                road_edges=envelope.road_edges,
            )
        except ValueError:
            continue
        # Hill-climb the tuned layout too. This was missing rather than decided: the
        # tuned path never ran (see `tuning.snap`), so everything fell through to the
        # Stage A path below, which does climb — and the omission here was invisible
        # because it never executed. Fixing the grid bug alone made 50x80 *worse* than
        # before, penalty 285 to 415, for exactly this reason.
        #
        # Safe on dimensioned rectangles for the same reason it is safe on generated
        # ones: swapping occupants leaves the geometry alone, and the acceptance test
        # is lexicographic, so a swap that makes a room unbuildable is refused however
        # many preferences it wins.
        layout = improve(layout, program)
        tuned.append(((layout.unbuildable, layout.score), index, layout))
        # Enough to choose between. Every further attempt costs a full solve, and the
        # marginal candidate rarely wins.
        if len(tuned) >= max(keep, TUNE_KEEP):
            break

    if tuned:
        tuned.sort(key=lambda row: (row[0], row[1]))
        return [layout for _, _, layout in tuned[:keep]]

    # No topology could be dimensioned legally — the programme does not fit this
    # envelope, which is stage ④'s to explain. Return the closest Stage A attempt with
    # its violations intact rather than nothing: a plan the user can see is wrong
    # beats a blank screen that does not say why.
    polished = [
        (improve(layout, program), index) for _, index, layout, _ in scored[: max(keep, IMPROVE_SHORTLIST)]
    ]
    polished.sort(key=lambda row: (row[0].unbuildable, row[0].score, row[1]))
    return [layout for layout, _ in polished[:keep]]


def plan(
    brief,
    envelope: Envelope,
    program: Program,
    *,
    candidates: int = DEFAULT_CANDIDATES,
    seed: int = 0,
):
    """Solve every floor and bundle the result for a viewer.

    One `solve` per floor because each storey is its own rectangle — stacking is
    stage ③'s decision, and by the time geometry is being fixed the floors no longer
    interact. Floors that produced nothing are dropped rather than represented by an
    empty layout, which would draw as a blank page rather than as an absence.
    """
    from app.ir.layout import PlanBundle

    floors = sorted({room.floor for room in program.rooms})
    layouts = []
    for floor in floors:
        best = solve(program, envelope, floor=floor, candidates=candidates, seed=seed)
        if best:
            layouts.append(best[0])

    return PlanBundle(
        brief_text=brief.raw_text,
        envelope=envelope,
        program=program,
        layouts=layouts,
        seed=seed,
        candidates=candidates,
    )
