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
from app.ir.enums import Facing, Relation, Sector, SpaceKind
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
    allocated = {
        room.id: room.min_area_sq_m + (room.target_area_sq_m - room.min_area_sq_m) * share
        for room in rooms
    }

    # **Surplus follows headroom, not proportion.** Above everyone's target the shares
    # stop moving, and `place` then normalises them to fill the rectangle — which
    # scales every room by the same factor, including the ones that should not grow at
    # all. On a stilt level that put the car bay at 46.9 m² against an 18 m² ceiling
    # and the staircase at 40.1 against 9.5, while the open ground it was all supposed
    # to land in sat at 5.2.
    #
    # A room with no ceiling does not grow; one with a large ceiling takes what the
    # others cannot. Anything still left over after every ceiling is full has nowhere
    # to go and spreads as before — which is the surplus problem in DECISIONS question
    # 7, and this does not pretend to solve it.
    surplus = budget - sum(allocated.values())
    if surplus <= 0:
        return allocated

    headroom = {
        room.id: max(0.0, (room.max_target_sq_m or room.target_area_sq_m) - allocated[room.id])
        for room in rooms
    }
    available = sum(headroom.values())
    if available <= 0:
        return allocated

    taken = min(surplus, available)
    for room in rooms:
        allocated[room.id] += headroom[room.id] / available * taken
    return allocated


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


def road_first_tree(
    rooms: list[RoomSpec],
    rng: random.Random,
    weights: dict[str, float],
    road: Facing,
    house_tree=None,
) -> Node:
    """A tree whose first cut peels off a street-side strip holding the car and the door.

    **Whether a room touches the boundary is decided by the shape of the tree, not by
    its dimensions** — so no weight in `score` and no tuning in Stage B can move a car
    bay onto the road if the topology put it in the middle. Measured on a 30x30, a 30x40
    2BHK and a 30x50: of 50–66 shortlisted random trees only one or two could be
    dimensioned at all, and none of those had the bay on the road. All three plots were
    refused for it.

    This is how an Indian house is laid out anyway: parking and the entrance at the
    front, the house behind. The strip is cut *along* the road, so every room in it keeps
    the street edge; the body behind it is an ordinary random tree.

    **An addition to the random pool, never a replacement.** A sector-aware shuffle and
    `graph_tree` both failed the same way — biasing generation raises the mean candidate
    and lowers the best, and `solve` only keeps the best. Road-first trees compete as
    their own group, so the random pool's ceiling is untouched.
    """
    front = [room for room in rooms if room.kind in (SpaceKind.CAR_PARKING, SpaceKind.FOYER)]
    body = [room for room in rooms if room not in front]
    if not front or not body:
        return random_tree(rooms, rng, weights)

    front = front[:]
    rng.shuffle(front)
    # A band across a north or south road is divided by vertical cuts, and a band
    # along an east or west road by horizontal ones — so no room in it loses the street.
    strip: Node = Leaf(front[0], weights[front[0].id])
    for room in front[1:]:
        strip = Cut(
            vertical=road in (Facing.NORTH, Facing.SOUTH),
            left=strip,
            right=Leaf(room, weights[room.id]),
        )
    house = (house_tree or random_tree)(body, rng, weights)

    # `place` gives a horizontal cut's right child the north half and a vertical cut's
    # right child the east half.
    if road is Facing.NORTH:
        return Cut(vertical=False, left=house, right=strip)
    if road is Facing.SOUTH:
        return Cut(vertical=False, left=strip, right=house)
    if road is Facing.EAST:
        return Cut(vertical=True, left=house, right=strip)
    return Cut(vertical=True, left=strip, right=house)


def spine_first_tree(
    rooms: list[RoomSpec], rng: random.Random, weights: dict[str, float]
) -> Node:
    """A corridor across the floor, with every other room keeping a wall on it.

    **Whether a bedroom can have a door to the corridor is decided by the shape of the
    tree** — the car bay's lesson again. No swap and no tuning can make two rooms touch
    that the tree put apart, and random trees rarely put every private room against the
    corridor: the model's 30x40 3BHK came out with its master bedroom, a bathroom and the
    corridor itself reachable only through another bedroom.

    The corridor is a band across the floor. The other rooms form two rows, one on each
    side, cut *across* the band, so every leaf spans its row's full depth and keeps a
    length of wall on the corridor. An addition to the random pool, never a replacement:
    biasing generation has failed here twice.
    """
    spine = [room for room in rooms if room.kind is SpaceKind.CORRIDOR]
    rest = [room for room in rooms if room.kind is not SpaceKind.CORRIDOR]
    if not spine or len(rest) < 2:
        return random_tree(rooms, rng, weights)
    rest = rest[:]
    rng.shuffle(rest)
    split = max(1, min(len(rest) - 1, len(rest) // 2 + rng.randint(-1, 1)))
    # True: the band runs east-west, so the rows sit north and south of it.
    east_west = rng.random() < 0.5

    def row(group: list[RoomSpec]) -> Node:
        node: Node = Leaf(group[0], weights[group[0].id])
        for room in group[1:]:
            node = Cut(vertical=east_west, left=node, right=Leaf(room, weights[room.id]))
        return node

    return Cut(
        vertical=not east_west,
        left=row(rest[:split]),
        right=Cut(vertical=not east_west, left=row(spine), right=row(rest[split:])),
    )


# A Vastu zone as a cell of the 3x3 grid `Layout.sector_of` reads: (row, column), row 0
# south and column 0 west.
_ZONE_CELL = {
    Sector.SOUTH_WEST: (0, 0), Sector.SOUTH: (0, 1), Sector.SOUTH_EAST: (0, 2),
    Sector.WEST: (1, 0), Sector.BRAHMASTHAN: (1, 1), Sector.EAST: (1, 2),
    Sector.NORTH_WEST: (2, 0), Sector.NORTH: (2, 1), Sector.NORTH_EAST: (2, 2),
}


def zone_spine_tree(
    rooms: list[RoomSpec], rng: random.Random, weights: dict[str, float]
) -> Node:
    """A corridor-first tree whose two rows follow the compass.

    Vastu zones were met 18 times in 110 across the reference plans — barely better than
    chance on a 3x3 grid — because nothing generated a tree with rooms where they asked
    to be, and a 5-point penalty cannot move a room the tree put elsewhere. Rooms wanting
    the south of a band that runs east-west go in its south row, and each row runs west
    to east in the order its rooms want; a band running north-south sorts the other way.
    Rooms with no zone fill the shorter row.

    **Its own group, beside plain corridor-first, never instead of it.** Swapping plain
    corridor-first for this measured three more warnings over eleven plans: the zone
    order cost some rooms the arrangement that kept them off the kitchen route.
    """
    spine = [room for room in rooms if room.kind is SpaceKind.CORRIDOR]
    rest = [room for room in rooms if room.kind is not SpaceKind.CORRIDOR]
    if not spine or len(rest) < 2:
        return random_tree(rooms, rng, weights)
    east_west = rng.random() < 0.5
    cell = {
        room.id: _ZONE_CELL[room.sector] if room.sector else (rng.randrange(3), rng.randrange(3))
        for room in rest
    }
    across = 0 if east_west else 1   # which coordinate chooses a room's side of the band
    along = 1 if east_west else 0    # which coordinate orders a row
    low = [room for room in rest if cell[room.id][across] == 0]
    high = [room for room in rest if cell[room.id][across] == 2]
    middle = [room for room in rest if cell[room.id][across] == 1]
    rng.shuffle(middle)
    for room in middle:
        (low if len(low) <= len(high) else high).append(room)
    if not low or not high:
        both = low + high
        rng.shuffle(both)
        half = max(1, len(both) // 2)
        low, high = both[:half], both[half:]

    def row(group: list[RoomSpec], by_zone: bool = True) -> Node:
        if by_zone:
            group = sorted(group, key=lambda room: (cell[room.id][along], rng.random()))
        node: Node = Leaf(group[0], weights[group[0].id])
        for room in group[1:]:
            node = Cut(vertical=east_west, left=node, right=Leaf(room, weights[room.id]))
        return node

    return Cut(
        vertical=not east_west,
        left=row(low),
        right=Cut(vertical=not east_west, left=row(spine, by_zone=False), right=row(high)),
    )


def road_columns_tree(
    rooms: list[RoomSpec], rng: random.Random, weights: dict[str, float], road: Facing
) -> Node:
    """Every room that needs the street at the road end of its own column.

    `road_first_tree` puts the car bay and the foyer in one strip, so both take the
    strip's depth. With the bay at its statutory 3.0 x 6.0 m that stopped working on a
    narrow plot: a 25x40's 5.6 m frontage cannot lay the bay along the road, so the bay
    runs 6 m back from it — and the foyer beside it had to as well, leaving too little
    house behind. Not one of 600 strip trees could be dimensioned. In columns the bay can
    run deep while the foyer stays shallow with the hall behind it.

    Columns stand side by side along the road; each holds one road room at its street
    end and a random tree of other rooms behind it.
    """
    front = [room for room in rooms if room.needs_road_access]
    rest = [room for room in rooms if not room.needs_road_access]
    if len(front) < 2 or not rest:
        return road_first_tree(rooms, rng, weights, road)
    front = front[:]
    rest = rest[:]
    rng.shuffle(front)
    rng.shuffle(rest)
    behind: list[list[RoomSpec]] = [[] for _ in front]
    for room in rest:
        behind[rng.randrange(len(front))].append(room)

    # Across a north or south road the columns stand side by side east-west, split by
    # vertical cuts; along an east or west road, north-south. Inside a column the cut
    # runs parallel to the road, and `place` gives a cut's right child the north or east
    # half — so the road room is the right child on a north or east road.
    across = road in (Facing.NORTH, Facing.SOUTH)
    street_is_right = road in (Facing.NORTH, Facing.EAST)
    columns: list[Node] = []
    for room, others in zip(front, behind):
        leaf: Node = Leaf(room, weights[room.id])
        if not others:
            columns.append(leaf)
            continue
        body = random_tree(others, rng, weights)
        columns.append(
            Cut(
                vertical=not across,
                left=body if street_is_right else leaf,
                right=leaf if street_is_right else body,
            )
        )
    tree = columns[0]
    for column in columns[1:]:
        tree = Cut(vertical=across, left=tree, right=column)
    return tree


def near_spine_tree(
    rooms: list[RoomSpec],
    rng: random.Random,
    weights: dict[str, float],
    road: Facing,
    near: set[str],
    beside_hall: set[str] = frozenset(),
    served: dict[str, list[str]] | None = None,
) -> Node:
    """A corridor running back from the road, with the rooms a brief wants near the
    entrance first on it.

    **Near the entrance is a tree shape, like the car bay on the road.** A brief's "my
    parents need a bedroom near the entrance" became a `NEAR` edge and a 10 m walk, and
    no candidate on a 40x60 met it: the corridor ran across the house and the bedroom
    that happened to be first on it was never the parents'. Here the corridor runs away
    from the road, the hall takes the road end of one row and the rooms the brief named
    take the road end of the other, so the walk is front door, hall, corridor, bedroom,
    in a few steps. A house tree for `road_first_tree`, in its own group.

    **The rooms open to the hall come straight after it.** Placed anywhere in the row, the
    dining room on JP Nagar landed with the kitchen between it and the hall: the brief's
    request was met and the living space was cut in two, since every candidate that kept
    them together put the parents at the back.
    **A kitchen's store and utility sit behind it, in its own slot.** Given places of
    their own in the row they landed across the corridor from the kitchen, two majors on
    JP Nagar; ordered after it, the row grew too long to dimension. In the kitchen's slot,
    split front to back, the row is no longer. The kitchen takes the outside wall and the
    service rooms the corridor side: tried the other way, the utility got the window and
    the kitchen, which needs it more, had none.
    """
    spine = [room for room in rooms if room.kind is SpaceKind.CORRIDOR]
    rest = [room for room in rooms if room.kind is not SpaceKind.CORRIDOR]
    if not spine or len(rest) < 2 or road is None:
        return random_tree(rooms, rng, weights)
    served = served or {}
    tucked = {room_id for ids in served.values() for room_id in ids}
    by_id = {room.id: room for room in rest}
    rest = [room for room in rest if room.id not in tucked]
    front_a = [room for room in rest if room.kind is SpaceKind.HALL]
    # Dining before kitchen: the kitchen joins the dining room, the dining room the hall.
    front_a += sorted(
        (room for room in rest if room.id in beside_hall and room not in front_a),
        key=lambda room: room.kind is SpaceKind.KITCHEN,
    )
    front_b = [room for room in rest if room.id in near and room not in front_a]
    others = [room for room in rest if room not in front_a and room not in front_b]
    rng.shuffle(others)
    split = max(0, min(len(others), len(others) // 2 + rng.randint(-1, 1)))
    row_a = front_a + others[:split]
    row_b = front_b + others[split:]
    if not row_a or not row_b:
        return random_tree(rooms, rng, weights)
    # The corridor runs away from the road: across an east or west road it runs east-west,
    # so the rows sit north and south of it and each row is ordered along x.
    east_west = road in (Facing.EAST, Facing.WEST)
    # `place` puts a cut's left child west (or south): the road end is the row's last leaf
    # on an east or north road, its first on a west or south one.
    road_end_last = road in (Facing.EAST, Facing.NORTH)

    def cell(room: RoomSpec, corridor_after: bool) -> Node:
        """The room, with whatever it serves tucked behind it, away from the corridor."""
        behind = [by_id[i] for i in served.get(room.id, []) if i in by_id]
        leaf: Node = Leaf(room, weights[room.id])
        if not behind:
            return leaf
        back: Node = Leaf(behind[0], weights[behind[0].id])
        for extra in behind[1:]:
            back = Cut(vertical=east_west, left=back, right=Leaf(extra, weights[extra.id]))
        # Across the row: `place` gives the right child the north or east part, which is
        # the corridor's side for the row before it.
        if corridor_after:
            return Cut(vertical=not east_west, left=leaf, right=back)
        return Cut(vertical=not east_west, left=back, right=leaf)

    def row(group: list[RoomSpec], corridor_after: bool) -> Node:
        ordered = list(reversed(group)) if road_end_last else group
        node: Node = cell(ordered[0], corridor_after)
        for room in ordered[1:]:
            node = Cut(vertical=east_west, left=node, right=cell(room, corridor_after))
        return node

    a_first = rng.random() < 0.5
    first, second = (row_a, row_b) if a_first else (row_b, row_a)
    sides = [row(first, corridor_after=True), row(second, corridor_after=False)]
    corridor: Node = Leaf(spine[0], weights[spine[0].id])
    for extra in spine[1:]:
        corridor = Cut(vertical=east_west, left=corridor, right=Leaf(extra, weights[extra.id]))
    return Cut(
        vertical=not east_west,
        left=sides[0],
        right=Cut(vertical=not east_west, left=corridor, right=sides[1]),
    )


def shaft_first_tree(
    rooms: list[RoomSpec],
    rng: random.Random,
    weights: dict[str, float],
    bounds: tuple[float, float, float, float],
    stair: RoomSpec,
    below: tuple[float, float, float, float],
    *,
    min_region_m: float = 1.0,
) -> Node | None:
    """A tree that cuts the stair below's own rectangle out first and tiles around it.

    **A stair is placed, and the rooms organise around it** — that is how an architect
    draws an upper floor, and it is not what random trees do. The 30x40 stilt plan put a
    straight flight along the ground floor's north wall, and no tree above it had a
    rectangle there: the first floor chose a dog-leg elsewhere and the house could not be
    climbed. Pinning the shaft as a constraint on arbitrary trees broke other plans twice
    (DECISIONS question 9); a tree whose cuts *are* the shaft's edges needs no pin, only
    Stage B's existing pull to land them.

    The floor divides into up to four regions around the shaft — a column either side of
    it and a band above and below it in its own column, or rows either side and columns
    beside it in its own row, chosen at random. A region too thin to be a room is left to
    its neighbour. Every other room goes to a region, the largest shortfall of area first,
    and each region is an ordinary random tree. None when a region would be left empty.
    """
    x0, y0, x1, y1 = bounds
    sx0, sy0, sx1, sy1 = below
    others = [room for room in rooms if room.id != stair.id]
    if not others:
        return Leaf(stair, weights[stair.id])

    def usable(a: float, b: float) -> bool:
        return b - a >= min_region_m

    columns_first = rng.random() < 0.5
    if columns_first:
        before = (x0, y0, sx0, y1) if usable(x0, sx0) else None
        after = (sx1, y0, x1, y1) if usable(sx1, x1) else None
        low = (sx0, y0, sx1, sy0) if usable(y0, sy0) else None
        high = (sx0, sy1, sx1, y1) if usable(sy1, y1) else None
    else:
        before = (x0, y0, x1, sy0) if usable(y0, sy0) else None
        after = (x0, sy1, x1, y1) if usable(sy1, y1) else None
        low = (x0, sy0, sx0, sy1) if usable(x0, sx0) else None
        high = (sx1, sy0, x1, sy1) if usable(sx1, x1) else None
    regions = {name: r for name, r in
               (("before", before), ("after", after), ("low", low), ("high", high)) if r}
    if not regions or len(others) < len(regions):
        return None

    def area(r) -> float:
        return (r[2] - r[0]) * (r[3] - r[1])

    ordered = others[:]
    rng.shuffle(ordered)
    members: dict[str, list[RoomSpec]] = {name: [] for name in regions}
    want = {name: area(r) for name, r in regions.items()}
    total_weight = sum(weights[room.id] for room in others)
    total_area = sum(want.values())
    # Seed each region with one room, largest region first, so none is left empty.
    for name in sorted(regions, key=lambda n: -want[n]):
        members[name].append(ordered.pop())
    for room in ordered:
        def deficit(name: str) -> float:
            share = want[name] / total_area * total_weight
            return share - sum(weights[r.id] for r in members[name])
        members[max(members, key=deficit)].append(room)

    def sub(name: str) -> Node:
        return random_tree(members[name], rng, weights)

    # `place` gives a vertical cut's right child the east part and a horizontal cut's
    # right child the north part.
    middle: Node = Leaf(stair, weights[stair.id])
    if "low" in regions:
        middle = Cut(vertical=not columns_first, left=sub("low"), right=middle)
    if "high" in regions:
        middle = Cut(vertical=not columns_first, left=middle, right=sub("high"))
    tree = middle
    if "before" in regions:
        tree = Cut(vertical=columns_first, left=sub("before"), right=tree)
    if "after" in regions:
        tree = Cut(vertical=columns_first, left=tree, right=sub("after"))
    return tree


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
