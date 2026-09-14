"""Hard topology: does every room have the access its use demands?

Reachable and properly reached are different claims. A room is *reachable* if some
chain of doors gets there. It has *proper access* only if the best chain passes through
nothing that should not be walked through — and an en-suite only if it opens from its
own bedroom. The difference is graded by what the worst room on the way is for:
through a bedroom or a bathroom is critical, through the kitchen major.

A door stage ⑥ added as a last resort stays in the drawing and is tagged here as a
forced pass-through, so the plan shows what was generated and nothing reads it as
good circulation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.circulation import diagnose, semantics
from app.circulation.graph import OUTSIDE
from app.circulation.routes import Route, best_route, host_grade, reachable
from app.ir.circulation import Access, CirculationGraph, CirculationNode, Isolation
from app.ir.enums import CirculationRole, EdgeKind, Grade, SpaceKind, Zone
from app.ir.layout import Layout
from app.ir.validation import Finding

_DOORS = (EdgeKind.DIRECT_DOOR, EdgeKind.SERVICE_CONNECTION, EdgeKind.OPEN_CONNECTION)


@dataclass
class Topology:
    graph: CirculationGraph
    access: list[Access] = field(default_factory=list)
    isolation: list[Isolation] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    routes: dict[str, Route] = field(default_factory=dict)
    blocked: bool = False


def analyse(graph: CirculationGraph, layout: Layout) -> Topology:
    """Access for every room on the storey, and the ways the storey itself is entered."""
    if graph.arrival is None:
        finding = (
            diagnose.no_front_door() if layout.floor == 1 else diagnose.no_stair(layout.floor)
        )
        return Topology(graph=graph, findings=[finding], blocked=True)
    misaligned = _misaligned_stairs(graph, layout)
    if misaligned:
        return Topology(
            graph=graph, findings=[diagnose.stair_misaligned(misaligned)], blocked=True
        )

    topo = Topology(graph=graph)
    rooms = [n for n in graph.nodes if n.walk_in and n.id not in OUTSIDE]
    reach = reachable(graph, graph.arrival, allow_outside=False)
    stranded = [n.id for n in rooms if n.id not in reach]
    if stranded:
        topo.findings.append(
            diagnose.unreachable(stranded, total=len(rooms), upstairs=layout.floor > 1)
        )

    forced: dict[int, str] = {}
    groups: dict[tuple, list[tuple[CirculationNode, Route]]] = {}
    for node in rooms:
        if node.id in stranded:
            topo.access.append(
                Access(room=node.id, status="unreachable", grade=Grade.CRITICAL)
            )
            continue
        if node.id == graph.arrival:
            topo.access.append(Access(room=node.id, status="appropriate", path=[node.id]))
            continue
        if node.parents and _en_suite(graph, node, topo, forced):
            continue
        route = best_route(graph, graph.arrival, node.id)
        topo.routes[node.id] = route
        topo.access.append(_access(node, route))
        if route.worst in (Grade.MAJOR, Grade.CRITICAL):
            worst_hosts = tuple(sorted(
                host for host in route.hosts
                if host_grade(graph.node(host), node) is route.worst
            ))
            groups.setdefault((route.worst, worst_hosts), []).append((node, route))
            if route.worst is Grade.CRITICAL:
                _mark_forced(graph, node, route, forced)

    for (grade, hosts), members in groups.items():
        topo.findings.append(diagnose.access(graph, grade, list(hosts), members))

    if forced:
        topo.graph = graph.model_copy(update={"edges": [
            edge.model_copy(update={"kind": EdgeKind.FORCED_PASS_THROUGH, "host": forced[i]})
            if i in forced else edge
            for i, edge in enumerate(graph.edges)
        ]})

    if layout.floor > 1:
        topo.findings += _stair_arrival(topo.graph)

    for node in rooms:
        if node.role not in (CirculationRole.PRIVATE, CirculationRole.SANITARY):
            continue
        if node.id in stranded:
            continue
        without = reachable(
            graph, graph.arrival, allow_outside=False, removed=frozenset({node.id})
        )
        lost = sorted(
            n.id for n in rooms
            if n.id in reach and n.id not in without and n.id != node.id
        )
        topo.isolation.append(Isolation(removed=node.id, stranded=lost))
    return topo


def _access(node: CirculationNode, route: Route) -> Access:
    if route.worst is None:
        status = "appropriate"
    elif route.worst is Grade.CRITICAL:
        status = "no_independent_access"
    else:
        status = "inappropriate"
    return Access(
        room=node.id, status=status, grade=route.worst,
        path=list(route.nodes), hosts=list(route.hosts),
    )


def _en_suite(graph, node, topo, forced) -> bool:
    """Judge a bathroom from the bedroom it belongs to. False if no bedroom reaches it,
    in which case it is judged as a shared room instead."""
    rules = semantics.data()["en_suite"]
    parents = [p for p in node.parents if graph.node(p) is not None]
    for other, edge in graph.neighbours(node.id):
        if other in parents and edge.kind in _DOORS:
            topo.access.append(
                Access(room=node.id, status="appropriate", path=[other, node.id])
            )
            return True
    routes = [r for r in (best_route(graph, p, node.id) for p in parents) if r is not None]
    if not routes:
        return False
    route = min(routes, key=lambda r: (semantics.rank(r.worst), r.transitions, r.distance_m))
    critical = route.worst is Grade.CRITICAL
    grade = semantics.grade(rules["via_private" if critical else "via_circulation"])
    topo.routes[node.id] = route
    topo.access.append(Access(
        room=node.id,
        status="no_independent_access" if grade is Grade.CRITICAL else "inappropriate",
        grade=grade, path=list(route.nodes), hosts=list(route.hosts),
    ))
    if critical:
        _mark_forced(graph, node, route, forced)
    topo.findings.append(diagnose.en_suite(graph, node, route, grade))
    return True


def _mark_forced(graph, target, route, forced) -> None:
    """Tag each door that leaves a room nobody should walk through on this route."""
    positions = {id(edge): i for i, edge in enumerate(graph.edges)}
    for i, host in enumerate(route.nodes[1:-1], start=1):
        if host in OUTSIDE:
            continue
        if host_grade(graph.node(host), target) is Grade.CRITICAL:
            forced.setdefault(positions[id(route.edges[i])], host)


def _stair_arrival(graph: CirculationGraph) -> list[Finding]:
    """What the stair lets a person walk on to, on the storey it arrives at.

    The stair room includes its landing, so a door off it is a door off circulation and
    a shared bathroom opening there is ordinary. With a landing or corridor to walk on
    to, only a private room opening straight off the stair is a fault, and a small one.
    Without one, the best room the flight opens into decides, and a stair whose only
    way on is a bathroom is never acceptable.
    """
    rules = semantics.data()["stair_arrival"]
    stair = graph.arrival
    exits = [graph.node(other) for other, _ in graph.neighbours(stair) if other not in OUTSIDE]
    if not exits:
        return [diagnose.stair_arrival(graph, stair, [], Grade.CRITICAL, "no_exit")]
    graded = [(node, semantics.grade(rules.get(node.role.value, "major"))) for node in exits]
    if any(grade is None for _, grade in graded):
        extra = [
            node.id for node, _ in graded
            if node.role is CirculationRole.PRIVATE
            or (node.role is CirculationRole.SANITARY and node.zone is Zone.PRIVATE)
        ]
        grade = semantics.grade(rules["extra_private"])
        if not extra or grade is None:
            return []
        return [diagnose.stair_arrival(graph, stair, extra, grade, "extra_private")]
    onward = [(node, grade) for node, grade in graded if node.role is not CirculationRole.SANITARY]
    if not onward:
        grade = semantics.grade(rules["sanitary"])
        rooms = [node.id for node, _ in graded]
        return [] if grade is None else [
            diagnose.stair_arrival(graph, stair, rooms, grade, "sanitary")
        ]
    best = min((grade for _, grade in onward), key=semantics.rank)
    rooms = [node.id for node, grade in onward if grade is best]
    return [diagnose.stair_arrival(graph, stair, rooms, best, "no_landing")]


def _misaligned_stairs(graph: CirculationGraph, layout: Layout) -> list[str]:
    """Stairs on an upper storey that do not stand over the stair below."""
    below = layout.shafts.get(SpaceKind.STAIRCASE)
    if layout.floor == 1 or below is None:
        return []
    share = semantics.data()["vertical"]["landing_share"]
    stairs = [
        placed for placed in layout.rooms
        if (node := graph.node(placed.room_id)) is not None
        and node.kind == SpaceKind.STAIRCASE.value
    ]
    if not stairs or any(_stacked_share(stair, below) >= share for stair in stairs):
        return []
    return sorted(stair.room_id for stair in stairs)


def _stacked_share(upper, lower) -> float:
    """The fraction of the smaller rectangle two stacked rooms have in common."""
    wide = min(upper.x_max_m, lower.x_max_m) - max(upper.x_min_m, lower.x_min_m)
    tall = min(upper.y_max_m, lower.y_max_m) - max(upper.y_min_m, lower.y_min_m)
    if wide <= 0 or tall <= 0:
        return 0.0
    smaller = min(
        (upper.x_max_m - upper.x_min_m) * (upper.y_max_m - upper.y_min_m),
        (lower.x_max_m - lower.x_min_m) * (lower.y_max_m - lower.y_min_m),
    )
    return wide * tall / smaller
