"""Stage ⑤ Stage B — CP-SAT dimensioning.

Stage A decides *which rooms neighbour which*; this decides *how wide*. That split is
decision 2, and the measurement that justifies it is stark: across 200 freshly
generated ground-floor topologies for a 30x40, **0% had a room below its minimum area
and 100% had one below its minimum width**. A slicing tree sets area through cut
ratios — proportions are whatever falls out of the tree shape, so `pooja` arrives with
exactly the right area as a 35 cm slot.

The tree structure is fixed here. The only variables are **where each cut lands**,
which keeps the model small — one integer per internal node rather than four per room
— and means the tiling stays gap-free for the same reason it did in Stage A: it is
still one rectangle divided recursively, just with the divisions chosen rather than
computed.

Infeasible is a real answer. A topology that cannot be dimensioned legally is one
Stage A should not have proposed, and the caller tries the next.
"""

from __future__ import annotations

import functools
import math

from ortools.sat.python import cp_model

from app.ir.layout import PlacedRoom
from app.ir.plan import RoomSpec
from app.rules import load_ruleset

from .slicing import Cut, Leaf, Node

# Centimetres. Millimetres would make the area products needlessly large for a
# precision nobody can build to, and metres cannot be integers.
_PER_M = 100

# cm² of target-area deviation one cm of shaft misalignment is worth — in effect "land on
# the shaft, then size the rooms", with legal minimums still constraints. Measured over
# ten plans at 0, 200, 1000, 2000, 3000 and 5000: 2000 is the smallest that puts every
# solvable plan's stair over the stair below (the 30x40 stilt still missed at 1000), with
# no new errors or warnings. The cost is a larger upper stair where the tree leaves the
# stair against a wall the shaft is not on: 8.6 to 17.7 m² on the 25x40.
SHAFT_PULL = 2000

# cm² of target-area deviation one cm of furnishing shortfall is worth. Target areas
# alone are indifferent to shape: a sum of area deviations is the same for a 2.2 x 4.1 m
# kitchen and a 1.9 x 4.7 m one, so Stage B handed back strips — a bedroom 7'6" x 19'10"
# on the 40x60. This prices each cm a room's short or long side falls below the nearest
# furnishable size, so width is taken from rooms that can spare it. A pull, never a
# constraint: furnishing is practice, and the law is already the constraints.
FIT_PULL = 3000


@functools.lru_cache(maxsize=1)
def _exterior_half() -> float:
    """Half the exterior wall thickness, from the ruleset stage ⑥ draws with.

    Loaded lazily: `load_ruleset` warns while a ruleset carries unchecked figures, and
    a module-level call fires that warning on import — before any caller has decided
    whether it cares.
    """
    return load_ruleset("refine_v1").data["walls"]["exterior_thickness_m"] / 2


def gross_minimum_cm2(spec) -> int:
    """The least gross area, in cm², the model will accept for this room.

    The clear area of a w x d room is (w - allow)(d - allow), which a bound on gross
    area cannot express, so the floor is the minimum grown by the allowance on the
    squarest room that could satisfy it. One function, so `cannot_fit` asks exactly the
    question `_build` enforces.
    """
    side = math.sqrt(spec.min_area_sq_m)
    allow = 2 * _exterior_half()
    gross = (side + allow) * (side + allow)
    if spec.min_length_m:
        # A statutory length forces a longer room than the squarest one of that area.
        gross = max(gross, (spec.min_width_m + allow) * (spec.min_length_m + allow))
    if spec.min_sizes_m:
        # So does a shape it must hold — the smallest of its alternatives.
        gross = max(gross, min((a + allow) * (b + allow) for a, b in spec.min_sizes_m))
    return round(gross * _PER_M * _PER_M)


def cannot_fit(rooms, bounds: tuple[float, float, float, float]) -> bool:
    """True when no slicing tree over these rooms can be dimensioned inside `bounds`.

    Exact tiling makes the rooms' areas sum to the footprint, and every leaf must reach
    `gross_minimum_cm2` — so when those minimums already exceed the footprint, every
    tree is infeasible and asking CP-SAT about each one only proves it again. Necessary,
    not sufficient: passing says nothing about whether a tree exists.
    """
    x_min_m, y_min_m, x_max_m, y_max_m = bounds
    width = math.floor(x_max_m * _PER_M) - math.ceil(x_min_m * _PER_M)
    depth = math.floor(y_max_m * _PER_M) - math.ceil(y_min_m * _PER_M)
    return sum(gross_minimum_cm2(room) for room in rooms) > width * depth


def tune(
    tree: Node,
    bounds: tuple[float, float, float, float],
    targets: dict[str, float],
    *,
    work_limit: float = 2.0,
    anchors: dict[str, tuple[float, float, float, float]] | None = None,
) -> list[PlacedRoom] | None:
    """Choose cut positions so every room is legal. None if this topology cannot be.

    `targets` is what each room should get if the geometry allows — the objective
    pulls towards it, the constraints refuse to break the law for it.

    `work_limit` is CP-SAT *deterministic* time, not seconds: it counts work, so the
    same tree gives the same rectangles on any machine at any load.

    `anchors` pulls a room's rectangle towards a fixed one — for a staircase upstairs, the
    stair the storey below settled on. A preference traded against target areas, never a
    constraint: pinning shafts as constraints was tried twice and broke other plans
    (DECISIONS question 9), while leaving Stage B blind to the shaft meant no candidate
    upstairs even had a rectangle over it.
    """
    model = cp_model.CpModel()
    # Inwards, not nearest. The grid must sit *inside* the bounds so that snapping a
    # boundary edge back out to the true bound only ever grows a room — rounding to
    # nearest can shrink one below the minimum the model just proved it met.
    x_min_m, y_min_m, x_max_m, y_max_m = bounds
    x0 = math.ceil(x_min_m * _PER_M)
    x1 = math.floor(x_max_m * _PER_M)
    y0 = math.ceil(y_min_m * _PER_M)
    y1 = math.floor(y_max_m * _PER_M)

    leaves: list[tuple[RoomSpec, cp_model.IntVar, cp_model.IntVar, cp_model.IntVar]] = []
    _build(model, tree, x0, x1, y0, y1, leaves)
    if not leaves:
        return None

    # Pull towards the target areas. Absolute deviation rather than squared: CP-SAT is
    # an integer solver, and squaring turns a linear objective into products it would
    # have to reify for no gain in the answer anyone would notice.
    #
    # The objective cannot fix an oversized room, and it was worth establishing that
    # rather than assuming it. Exact tiling fixes the total, so surplus the programme
    # never asked for lands *somewhere*; a sum of absolute deviations is flat over
    # every way of distributing it. Weighting each deviation by 1/target was tried —
    # it prices a square metre by who receives it — and measured 2-2 on penalty across
    # four briefs while losing adjacency on two of them. Relative L1 is still linear,
    # so its optimum still puts the whole surplus in *one* room, just a larger one.
    #
    # The surplus itself is the defect. `score` now penalises a grossly oversized room
    # so the ranking can at least see it; not forcing a plan to fill the maximum
    # permitted footprint is the actual fix, and it is an open question in DECISIONS.md.
    deviations = []
    for spec, _, _, area, _, _ in leaves:
        want = round(targets.get(spec.id, spec.target_area_sq_m) * _PER_M * _PER_M)
        deviation = model.NewIntVar(0, (x1 - x0) * (y1 - y0), f"dev_{spec.id}")
        model.AddAbsEquality(deviation, area - want)
        deviations.append(deviation)
    # **The shaft, as a preference.** Each anchored edge's distance from where it should
    # land, in cm, priced at SHAFT_PULL cm² of target-area deviation. Legal minimums are
    # untouched — they are constraints — so a pull can reshape a floor but never make a
    # room illegal.
    pulls = []
    for spec, left, bottom, _, width, depth in leaves:
        anchor = (anchors or {}).get(spec.id)
        if anchor is None:
            continue
        ax0, ay0, ax1, ay1 = (round(v * _PER_M) for v in anchor)
        for n, (edge, aim) in enumerate(
            ((left, ax0), (left + width, ax1), (bottom, ay0), (bottom + depth, ay1))
        ):
            if isinstance(edge, int):
                continue  # an outer bound: nothing the solver can move
            gap = model.NewIntVar(0, 100_000, f"pull_{spec.id}_{n}")
            model.AddAbsEquality(gap, edge - aim)
            pulls.append(gap)
    # **Furniture, as a preference.** Against the smallest furnishable size on each side —
    # a lower bound over the arrangements, which keeps the model linear; `score` and ⑦
    # judge the exact fit afterwards. Gross, like the minimums, so the pull asks for the
    # clear size plus the walls around it.
    fits = []
    allow = 2 * _exterior_half()
    for spec, _, _, _, width, depth in leaves:
        if not spec.usable_sizes_m:
            continue
        want_short = round((min(s for s, _ in spec.usable_sizes_m) + allow) * _PER_M)
        want_long = round((min(l for _, l in spec.usable_sizes_m) + allow) * _PER_M)
        shorter = model.NewIntVar(0, 10_000, f"short_{spec.id}")
        longer = model.NewIntVar(0, 10_000, f"long_{spec.id}")
        model.AddMinEquality(shorter, [width, depth])
        model.AddMaxEquality(longer, [width, depth])
        for side, want in ((shorter, want_short), (longer, want_long)):
            gap = model.NewIntVar(0, 10_000, f"fit_{spec.id}_{want}")
            model.Add(gap >= want - side)
            fits.append(gap)
    model.Minimize(sum(deviations) + SHAFT_PULL * sum(pulls) + FIT_PULL * sum(fits))

    solver = cp_model.CpSolver()
    # Determinism matters more here than the last few percent of quality: a plan you
    # cannot reproduce is one two people cannot discuss.
    #
    # **A budget of work, not of seconds.** One worker and a fixed seed were not enough.
    # Most solves on a busy floor stop at their limit, not at optimality — 22 of 25 on a
    # 30x50 — and a wall-clock limit stops them wherever the machine happened to have got
    # to. The same brief, seed and process gave a plan whose stairs met on one run and
    # missed on the next, and a test flipped with it. Deterministic time counts work, so
    # the solver stops at the same point every time. There is no wall-clock cap beside
    # it: one that ever bound would bring the variance straight back.
    solver.parameters.max_deterministic_time = work_limit
    solver.parameters.num_workers = 1
    solver.parameters.random_seed = 0

    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None

    def at(expr) -> int:
        """`x0` is a plain int at the root and a cut variable below it."""
        return expr if isinstance(expr, int) else solver.Value(expr)

    def snap(value: int, lo: int, hi: int, lo_m: float, hi_m: float) -> float:
        """Put the outermost edges back on the given bounds, exactly.

        The model runs on a 1 cm integer grid; bounds derived from feet do not
        land on it — 30 ft is 9.144 m. So every tuned layout stopped a fraction of a
        centimetre short of the wall, `Layout`'s exact-tiling validator rejected it as
        a gap, and `solve` swallowed the rejection and fell through to its Stage A
        path. Stage B was dead for every plot quoted in feet, which is every Indian
        plot the intent prompt describes. It ran, correctly, and its output was thrown
        away without a word.

        Only the two outer edges move. Interior cuts stay on the grid and are shared
        between neighbours, so they still cancel and the tiling is exact.
        """
        if value == lo:
            return lo_m
        if value == hi:
            return hi_m
        return value / _PER_M

    return [
        PlacedRoom(
            room_id=spec.id,
            x_min_m=snap(at(left), x0, x1, x_min_m, x_max_m),
            y_min_m=snap(at(bottom), y0, y1, y_min_m, y_max_m),
            x_max_m=snap(
                at(left) + solver.Value(width), x0, x1, x_min_m, x_max_m
            ),
            y_max_m=snap(
                at(bottom) + solver.Value(depth), y0, y1, y_min_m, y_max_m
            ),
        )
        for spec, left, bottom, _, width, depth in leaves
    ]


def _build(model, node: Node, x0, x1, y0, y1, leaves: list) -> None:
    """Walk the tree, creating a cut variable per internal node and constraints per leaf.

    `x0`/`x1`/`y0`/`y1` arrive as integers at the root and as cut variables below it,
    so a leaf's extent is a difference of expressions — which is why width and depth
    become their own variables: `AddMultiplicationEquality` needs variables, not
    expressions, and area is the one genuinely nonlinear constraint in the model.
    """
    if isinstance(node, Leaf):
        spec = node.room
        # Gross, not clear. The model dimensions rectangles that run to wall
        # centrelines while the minimums are internal, so every leaf carries an
        # allowance for the walls around it. Conservative — the exterior thickness on
        # every side — because which sides land on the boundary is not known until the
        # tree is placed, and over-allowing costs a few centimetres while under-
        # allowing produces a room that is illegal and says it is not.
        allow = 2 * _exterior_half()
        floor_cm = max(1, round((spec.min_width_m + allow) * _PER_M))
        width = model.NewIntVar(floor_cm, 10_000, f"w_{spec.id}")
        depth = model.NewIntVar(floor_cm, 10_000, f"d_{spec.id}")
        model.Add(width == x1 - x0)
        model.Add(depth == y1 - y0)
        # A length the law sets as well as a width: a private garage is 3.0 x 6.0 m.
        # Either side may be the long one — which way the car faces is not the bye-law's
        # question, and the opening goes on whichever side meets the road.
        if spec.min_length_m:
            longer = model.NewIntVar(floor_cm, 10_000, f"l_{spec.id}")
            model.AddMaxEquality(longer, [width, depth])
            model.Add(longer >= round((spec.min_length_m + allow) * _PER_M))

        # A shape the room must hold, as one of several: a staircase's flights fit as a
        # dog-leg or a straight run. A constraint, not a pull — a stair that holds neither
        # cannot be climbed — so a boolean per alternative and at least one true.
        if spec.min_sizes_m:
            shorter = model.NewIntVar(floor_cm, 10_000, f"min_short_{spec.id}")
            longer_side = model.NewIntVar(floor_cm, 10_000, f"min_long_{spec.id}")
            model.AddMinEquality(shorter, [width, depth])
            model.AddMaxEquality(longer_side, [width, depth])
            options = []
            for n, (a, b) in enumerate(spec.min_sizes_m):
                chosen = model.NewBoolVar(f"shape_{spec.id}_{n}")
                model.Add(shorter >= round((a + allow) * _PER_M)).OnlyEnforceIf(chosen)
                model.Add(longer_side >= round((b + allow) * _PER_M)).OnlyEnforceIf(chosen)
                options.append(chosen)
            model.AddBoolOr(options)

        area = model.NewIntVar(0, 100_000_000, f"a_{spec.id}")
        model.AddMultiplicationEquality(area, [width, depth])
        # The clear area of a w x d room is (w - allow)(d - allow). Bounding the gross
        # area alone cannot express that, so the floor is the minimum grown by the
        # allowance on the squarest room that could satisfy it — enough to keep CP-SAT
        # honest, with `score` doing the exact per-side check afterwards.
        model.Add(area >= gross_minimum_cm2(spec))

        # Aspect both ways, scaled to stay integral. Linear, unlike area.
        ratio = max(1, round(spec.max_aspect * 100))
        model.Add(100 * width <= ratio * depth)
        model.Add(100 * depth <= ratio * width)

        leaves.append((spec, x0, y0, area, width, depth))
        return

    assert isinstance(node, Cut)
    if node.vertical:
        cut = model.NewIntVar(0, 100_000, "cut_x")
        model.Add(cut >= x0 + 1)
        model.Add(cut <= x1 - 1)
        _build(model, node.left, x0, cut, y0, y1, leaves)
        _build(model, node.right, cut, x1, y0, y1, leaves)
    else:
        cut = model.NewIntVar(0, 100_000, "cut_y")
        model.Add(cut >= y0 + 1)
        model.Add(cut <= y1 - 1)
        _build(model, node.left, x0, x1, y0, cut, leaves)
        _build(model, node.right, x0, x1, cut, y1, leaves)
