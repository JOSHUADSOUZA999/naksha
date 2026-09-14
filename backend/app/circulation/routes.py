"""The route a walk would actually take through a storey, and what it costs.

Two questions, answered in order, because they do not fold into one number. First:
what is the least bad kind of room this walk must pass through — nothing, a living
room, the kitchen, a bedroom? Then, among routes no worse than that: the fewest such
rooms, then the shortest walk. A bedroom crossed is not a longer version of a corridor
crossed, and no saving in distance buys one.

Distances run room to room through door midpoints, square to the walls. Turns are read
from which walls a walk enters and leaves each room by.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass

from app.circulation import semantics
from app.circulation.graph import OUTSIDE, centre
from app.ir.circulation import CirculationEdge, CirculationGraph, CirculationNode
from app.ir.enums import EdgeKind, Grade

_THRESHOLDS: tuple[Grade | None, ...] = (None, Grade.MINOR, Grade.MAJOR, Grade.CRITICAL)


@dataclass(frozen=True)
class Route:
    nodes: tuple[str, ...]
    edges: tuple[CirculationEdge, ...]
    worst: Grade | None
    hosts: tuple[str, ...]
    distance_m: float
    turns: int

    @property
    def intermediates(self) -> tuple[str, ...]:
        return tuple(n for n in self.nodes[1:-1] if n not in OUTSIDE)

    @property
    def transitions(self) -> int:
        return len(self.intermediates)

    @property
    def doors(self) -> int:
        return sum(
            1 for e in self.edges
            if e.kind is not EdgeKind.STAIR_CONNECTION and not {e.a, e.b} <= OUTSIDE
        )

    @property
    def external(self) -> bool:
        return any(n in OUTSIDE for n in self.nodes[1:-1])

    @property
    def outside_m(self) -> float:
        """How far the walk runs outside the house, from the opening it leaves by to the
        one it comes back in at."""
        return sum(
            _manhattan(self.edges[i - 1].at, self.edges[i].at)
            for i in range(1, len(self.nodes) - 1)
            if self.nodes[i] in OUTSIDE
        )


def host_grade(node: CirculationNode, target: CirculationNode) -> Grade | None:
    """What walking through `node` costs on the way to `target`.

    A bedroom is a proper way into its own en-suite, and only into that.
    """
    if node.id in target.parents:
        return None
    return semantics.pass_through(node.role, target.role)


def best_route(
    graph: CirculationGraph,
    origin: str,
    target: str,
    *,
    allow_outside: bool = False,
    removed: frozenset[str] = frozenset(),
) -> Route | None:
    """The best route from `origin` to `target`, or None if there is none at all."""
    goal = graph.node(target)
    if goal is None or graph.node(origin) is None or origin in removed or target in removed:
        return None
    if origin == target:
        return Route((origin,), (), None, (), 0.0, 0)
    for threshold in _THRESHOLDS:
        route = _search(graph, origin, goal, threshold, allow_outside, removed)
        if route is not None:
            return route
    return None


def reachable(
    graph: CirculationGraph,
    origin: str,
    *,
    allow_outside: bool = True,
    removed: frozenset[str] = frozenset(),
) -> set[str]:
    """Every node some route reaches, whatever it passes through."""
    if origin in removed or graph.node(origin) is None:
        return set()
    seen = {origin}
    stack = [origin]
    while stack:
        here = stack.pop()
        if here != origin and here in OUTSIDE and not allow_outside:
            continue
        for other, _ in graph.neighbours(here):
            if other not in seen and other not in removed:
                seen.add(other)
                stack.append(other)
    return seen


def _search(graph, origin, goal, threshold, allow_outside, removed) -> Route | None:
    rank = semantics.rank(threshold)
    start = graph.node(origin)
    begin = centre(start.rect) if start.rect else None
    # States are (node, the edge it was entered by), so a room's crossing is costed
    # from the door you came in by to the door you leave by.
    counter = 0
    frontier = [(0, 0.0, counter, origin, None)]
    best: dict[tuple[str, int | None], tuple[int, float]] = {(origin, None): (0, 0.0)}
    back: dict[tuple[str, int | None], tuple[str, int | None] | None] = {(origin, None): None}
    edges = graph.edges
    index = {id(edge): i for i, edge in enumerate(edges)}
    done: tuple[str, int | None] | None = None

    while frontier:
        count, dist, _, here, entered = heapq.heappop(frontier)
        state = (here, entered)
        if best.get(state, (count, dist)) < (count, dist):
            continue
        if here == goal.id:
            done = state
            break
        if here != origin:
            if here in OUTSIDE and not allow_outside:
                continue
        position = edges[entered].at if entered is not None else begin
        for other, edge in graph.neighbours(here):
            if other in removed or other == origin:
                continue
            node = graph.node(other)
            if node is None:
                continue
            step = count
            if other != goal.id:
                if other in OUTSIDE:
                    if not allow_outside:
                        continue
                else:
                    grade = host_grade(node, goal)
                    if semantics.rank(grade) > rank:
                        continue
                    step += 1 if grade is not None else 0
            walk = dist + _manhattan(position, edge.at)
            key = (other, index[id(edge)])
            if (step, walk) < best.get(key, (10**9, float("inf"))):
                best[key] = (step, walk)
                back[key] = state
                counter += 1
                heapq.heappush(frontier, (step, walk, counter, other, index[id(edge)]))

    if done is None:
        return None
    states = []
    cursor: tuple[str, int | None] | None = done
    while cursor is not None:
        states.append(cursor)
        cursor = back[cursor]
    states.reverse()
    nodes = tuple(node for node, _ in states)
    path_edges = tuple(edges[i] for _, i in states[1:])
    end = goal.rect and centre(goal.rect)
    distance = best[done][1] + (_manhattan(path_edges[-1].at, end) if path_edges else 0.0)
    hosts = tuple(
        n for n in nodes[1:-1]
        if n not in OUTSIDE and host_grade(graph.node(n), goal) is not None
    )
    worst = semantics.worst(*(host_grade(graph.node(n), goal) for n in hosts))
    turns = sum(
        _turns(path_edges[i], path_edges[i + 1]) for i in range(len(path_edges) - 1)
    )
    return Route(nodes, path_edges, worst, hosts, distance, turns)


def _manhattan(a, b) -> float:
    if a is None or b is None:
        return 0.0
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _turns(into: CirculationEdge, out: CirculationEdge) -> int:
    """Direction changes crossing one room, from the walls its two doors sit in.

    Doors in walls at right angles: one turn. In opposite walls: straight through when
    roughly in line, otherwise a jog of two. In the same wall: a U-turn, two.
    """
    if into.vertical is None or out.vertical is None or into.at is None or out.at is None:
        return 0
    if into.vertical != out.vertical:
        return 1
    aligned = semantics.data()["journeys"]["scoring"]["aligned_doors_m"]
    across, along = (0, 1) if into.vertical else (1, 0)
    if abs(into.at[across] - out.at[across]) < 1e-6:
        return 2
    return 0 if abs(into.at[along] - out.at[along]) <= aligned else 2
