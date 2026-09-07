"""Stage ⑤ Stage A — slicing trees.

A binary tree whose leaves are rooms and whose internal nodes are cuts. Placing it is
a recursive division of one rectangle, so **the tiling is gap-free and overlap-free by
construction** — no constraint solver is asked to discover a property the
representation already guarantees. That is the whole reason for this stage: CP-SAT
could not find a feasible ten-room plan in twenty seconds, and it was being asked to
rediscover "the rooms must not overlap" on every solve.

What a slicing tree cannot guarantee is that the plan is any *good*. Areas land
proportional to their targets, but a room can still come out three metres by one, or
the kitchen can land north-west. Judging that is `score.py`; this file only generates.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from app.ir.layout import PlacedRoom
from app.ir.enums import Relation
from app.ir.plan import AdjacencySpec, RoomSpec


@dataclass(frozen=True, slots=True)
class Leaf:
    room: RoomSpec
    weight: float


def effective_areas(rooms: list[RoomSpec], budget: float) -> dict[str, float]:
    """How much space each room should actually get, given what there is.

    This is what `RoomSpec` carries two areas *for*, and the solver ignored it for
    long enough to ship a bug. Allocating in proportion to `target_area_sq_m` scales
    every room by the same factor when the targets overflow — so a room whose target
    is barely above its legal minimum gets pushed *below* it, while a room with plenty
    of slack keeps room it does not need. On a 30x40 that put car parking at 11.4 m²
    against a 13.5 m² minimum, in a programme feasibility had already passed.

    Each room instead keeps its minimum and shares the leftover in proportion to how
    much slack it asked for:

        effective = min + (target - min) x f,   f chosen so the total is the budget

    With a generous budget `f` reaches 1 and everyone gets their target. With a tight
    one `f` approaches 0 and everyone lands on their legal minimum — which is the
    smallest the house can legally be, and the same quantity feasibility tests. The
    two now agree by construction rather than by coincidence.
    """
    floor = sum(room.min_area_sq_m for room in rooms)
    slack = sum(room.target_area_sq_m - room.min_area_sq_m for room in rooms)
    if slack <= 0:
        return {room.id: room.target_area_sq_m for room in rooms}
    # Clamped: above 1 would exceed the targets anyone asked for, and below 0 would
    # mean the minimums alone overflow — infeasible, which is stage ④'s to report,
    # not something to paper over with negative rooms.
    share = max(0.0, min(1.0, (budget - floor) / slack))
    return {
        room.id: room.min_area_sq_m + (room.target_area_sq_m - room.min_area_sq_m) * share
        for room in rooms
    }


@dataclass(frozen=True, slots=True)
class Cut:
    """`vertical=True` splits left/right (a cut running north-south)."""

    vertical: bool
    left: "Node"
    right: "Node"

    @property
    def weight(self) -> float:
        return self.left.weight + self.right.weight


Node = Leaf | Cut


def random_tree(
    rooms: list[RoomSpec], rng: random.Random, weights: dict[str, float] | None = None
) -> Node:
    """A random slicing tree over these rooms.

    Shuffling before splitting is what makes two candidates differ: the *order* of the
    leaves is the topology. A balanced split keeps trees shallow, which keeps rooms
    closer to square — a spine of single-room cuts produces a row of corridors.

    **A sector-aware ordering was tried here and removed.** Biasing the shuffle so
    rooms land near their preferred Vastu sector measured as a clear win — until the
    scorer was fixed to rank unbuildable rooms ahead of unmet preferences, at which
    point the advantage vanished (2056 biased against 2024 unbiased, inside the noise
    of eight seeds). The bias had been tuned against an objective that rewarded
    satisfying preferences at the cost of rooms below their legal minimum. Placing
    rectangles in the right sectors is a real idea, but it belongs in Stage B, where
    dimensions are tuned against a correct objective.
    """
    if weights is None:
        weights = {room.id: room.target_area_sq_m for room in rooms}
    if len(rooms) == 1:
        return Leaf(rooms[0], weights[rooms[0].id])
    ordered = rooms[:]
    rng.shuffle(ordered)
    # Bias towards the middle rather than a uniform cut point, for the reason above.
    midpoint = len(ordered) // 2
    drift = rng.randint(-(len(ordered) // 4), len(ordered) // 4) if len(ordered) > 3 else 0
    split = max(1, min(len(ordered) - 1, midpoint + drift))
    return Cut(
        vertical=rng.random() < 0.5,
        left=random_tree(ordered[:split], rng, weights),
        right=random_tree(ordered[split:], rng, weights),
    )


def place(node: Node, x_min: float, y_min: float, x_max: float, y_max: float) -> list[PlacedRoom]:
    """Divide the rectangle down the tree, proportional to target areas.

    The cut position is the left subtree's share of the combined target area, so a
    room asking for twice the space gets twice the width. Exact by construction: the
    two halves always sum back to the parent.
    """
    if isinstance(node, Leaf):
        return [
            PlacedRoom(
                room_id=node.room.id,
                x_min_m=x_min,
                y_min_m=y_min,
                x_max_m=x_max,
                y_max_m=y_max,
            )
        ]

    share = node.left.weight / node.weight
    if node.vertical:
        cut = x_min + (x_max - x_min) * share
        return place(node.left, x_min, y_min, cut, y_max) + place(
            node.right, cut, y_min, x_max, y_max
        )
    cut = y_min + (y_max - y_min) * share
    return place(node.left, x_min, y_min, x_max, cut) + place(
        node.right, x_min, cut, x_max, y_max
    )


def graph_tree(
    rooms: list[RoomSpec],
    adjacencies: list[AdjacencySpec],
    rng: random.Random,
    weights: dict[str, float],
) -> Node:
    """A slicing tree built *from* the room graph rather than from a shuffle.

    Two sibling leaves always share a wall — that is the one adjacency a slicing tree
    guarantees. Everything deeper is geometry-dependent. So the way to satisfy a
    constraint graph is to keep connected rooms together as the tree descends, until
    the pair that must touch is the last two in a subtree and becomes siblings.

    A random shuffle does the opposite: it scatters connected rooms across the first
    cut and then no amount of dimensioning can bring them back together. Stage B tunes
    *sizes* against a fixed topology — it cannot move a room to another branch. That
    is why a third of the graph went unrealised, and why the failures were circulation:
    `corridor does not reach bed_parents` is a plan with rooms you cannot walk between.

    Recursive bisection, grown from a random seed so candidates still differ. Only
    `SEPARATED` is treated as repulsion; everything else pulls together.

    **Measured, and not on the default path.** Per tree it is clearly better — 5.77 of
    12 edges satisfied against 4.63, and CP-SAT dimensions 9/60 of them against 6/60.
    End to end it is not: across 8 seeds and 3 briefs, random generation met 74% of
    edges, graph-driven 71%, an even mix 73%. All inside the noise.

    The reason is that `solve` generates 900 candidates and keeps the *best*. Biasing
    generation raises the mean and lowers the ceiling, and only the ceiling is taken.
    This is the second bias to fail that way here — a sector-aware shuffle did the same
    — which is worth remembering before adding a third.

    Kept because it becomes the right tool the moment generation stops being free:
    fewer candidates, or a graph large enough that random search stops finding good
    topologies at all.
    """
    if len(rooms) == 1:
        return Leaf(rooms[0], weights[rooms[0].id])

    left, right = _bisect(rooms, adjacencies, rng, weights)
    return Cut(
        vertical=rng.random() < 0.5,
        left=graph_tree(left, adjacencies, rng, weights),
        right=graph_tree(right, adjacencies, rng, weights),
    )


def _bisect(
    rooms: list[RoomSpec],
    adjacencies: list[AdjacencySpec],
    rng: random.Random,
    weights: dict[str, float],
) -> tuple[list[RoomSpec], list[RoomSpec]]:
    """Split this set in two, keeping connected rooms on the same side.

    Grows a cluster outward from a random seed along the graph until it holds about
    half the floor area — area rather than room count, because two halves of wildly
    different size force a lopsided cut and thin slices downstream.
    """
    here = {room.id for room in rooms}
    wanted: dict[str, set[str]] = {room.id: set() for room in rooms}
    for edge in adjacencies:
        if edge.relation is Relation.SEPARATED:
            continue  # a repulsion, not a reason to group
        if edge.a in here and edge.b in here:
            wanted[edge.a].add(edge.b)
            wanted[edge.b].add(edge.a)

    half = sum(weights[r.id] for r in rooms) / 2
    seed = rng.choice(rooms)
    cluster = {seed.id}
    held = weights[seed.id]
    frontier = set(wanted[seed.id])

    while frontier and held < half:
        # Sorted before the draw so the same seed always makes the same tree — a
        # layout you cannot reproduce is one two people cannot discuss.
        pick = rng.choice(sorted(frontier))
        cluster.add(pick)
        held += weights[pick]
        frontier |= wanted[pick]
        frontier -= cluster

    left = [r for r in rooms if r.id in cluster]
    right = [r for r in rooms if r.id not in cluster]

    if not left or not right:
        # The graph is disconnected here, or one component swallowed everything.
        # Fall back to the shuffle: an arbitrary split beats no split.
        shuffled = rooms[:]
        rng.shuffle(shuffled)
        cut = max(1, len(shuffled) // 2)
        return shuffled[:cut], shuffled[cut:]
    return left, right
