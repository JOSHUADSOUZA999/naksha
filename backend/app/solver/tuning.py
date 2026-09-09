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

import math

from ortools.sat.python import cp_model

from app.ir.layout import PlacedRoom
from app.ir.plan import RoomSpec

from .slicing import Cut, Leaf, Node

# Centimetres. Millimetres would make the area products needlessly large for a
# precision nobody can build to, and metres cannot be integers.
_PER_M = 100


def tune(
    tree: Node,
    bounds: tuple[float, float, float, float],
    targets: dict[str, float],
    *,
    time_limit_s: float = 2.0,
) -> list[PlacedRoom] | None:
    """Choose cut positions so every room is legal. None if this topology cannot be.

    `targets` is what each room should get if the geometry allows — the objective
    pulls towards it, the constraints refuse to break the law for it.
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
    model.Minimize(sum(deviations))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    # Determinism matters more here than the last few percent of quality: a plan you
    # cannot reproduce is one two people cannot discuss.
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
        floor_cm = max(1, round(spec.min_width_m * _PER_M))
        width = model.NewIntVar(floor_cm, 10_000, f"w_{spec.id}")
        depth = model.NewIntVar(floor_cm, 10_000, f"d_{spec.id}")
        model.Add(width == x1 - x0)
        model.Add(depth == y1 - y0)

        area = model.NewIntVar(0, 100_000_000, f"a_{spec.id}")
        model.AddMultiplicationEquality(area, [width, depth])
        model.Add(area >= round(spec.min_area_sq_m * _PER_M * _PER_M))

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
