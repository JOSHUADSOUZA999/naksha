"""Privacy and efficiency: which private rooms are exposed, and what the passage costs.

Topology says whether each room is properly reached; journeys say how the house is
walked. Two questions remain for a storey. **Privacy**: does a private room open
straight off the doorstep, or off a room visitors are received in? **Efficiency**: how
much of the floor is passage, and does each corridor and foyer do work in proportion to
its size?

A corridor is judged by its work, never by existing. Taking one away and stranding the
bedrooms off it is what a landing is for: that makes it essential, not wrong. It is
redundant only when no room needs it and no journey uses it, and inefficient when it
spends far more area, width or dead end than its doors need.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.circulation import diagnose, semantics
from app.circulation.graph import OUTSIDE
from app.circulation.journeys import JourneyAnalysis, rooms_for
from app.circulation.routes import best_route
from app.circulation.topology import Topology
from app.ir.circulation import CirculationEdge, CirculationGraph, CirculationNode, Corridor
from app.ir.enums import CirculationRole, CorridorVerdict, EdgeKind, Grade
from app.ir.validation import Finding

# Openings a person walks through, the front door included.
_WALKED = (
    EdgeKind.DIRECT_DOOR, EdgeKind.SERVICE_CONNECTION, EdgeKind.OPEN_CONNECTION,
    EdgeKind.FORCED_PASS_THROUGH, EdgeKind.EXTERNAL_CONNECTION,
)
_WAYS = (CirculationRole.ARRIVAL, CirculationRole.CIRCULATION)
_KEPT_APART = (CirculationRole.PRIVATE, CirculationRole.SANITARY)
_VERDICT_GRADES = {
    CorridorVerdict.REDUNDANT: "redundant",
    CorridorVerdict.INEFFICIENT: "inefficient",
}


@dataclass
class Analysis:
    findings: list[Finding] = field(default_factory=list)
    share: float = 0.0
    efficiency: float = 100.0
    corridors: list[Corridor] = field(default_factory=list)


def analyse(topo: Topology, walks: JourneyAnalysis) -> Analysis:
    graph = topo.graph
    rules = semantics.data()["efficiency"]
    areas = _areas(graph, rules["excluded_kinds"])
    total = sum(areas.values())
    passage = sorted(
        (
            (room, area) for room, area in areas.items()
            if graph.node(room).kind in rules["horizontal_kinds"]
        ),
        key=lambda item: (-item[1], item[0]),
    )
    out = Analysis(share=sum(area for _, area in passage) / total if total else 0.0)
    out.efficiency = _band_score(out.share, rules)
    if topo.blocked:
        return out

    reported = {row.room for row in topo.access if semantics.rank(row.grade) >= 2}
    out.findings += _exposure(graph, reported)
    out.findings += _share(graph, out.share, passage, rules)
    for node in graph.nodes:
        if node.rect is None or node.kind not in rules["horizontal_kinds"]:
            continue
        if node.role is CirculationRole.CIRCULATION:
            corridor = _corridor(topo, walks, node, total, rules["corridor"])
            out.corridors.append(corridor)
            key = _VERDICT_GRADES.get(corridor.verdict)
            grade = semantics.grade(rules["grades"][key]) if key else None
            if grade is not None:
                out.findings.append(diagnose.corridor(graph, corridor, grade, rules["corridor"]))
        elif node.role is CirculationRole.ARRIVAL:
            found = _foyer(graph, node, rules)
            if found is not None:
                out.findings.append(found)
    return out


def _areas(graph: CirculationGraph, excluded: list[str]) -> dict[str, float]:
    """Floor inside the walls, leaving out car bays and stilts: parking is not the house."""
    return {
        node.id: (node.rect[2] - node.rect[0]) * (node.rect[3] - node.rect[1])
        for node in graph.nodes
        if node.rect is not None and node.id not in OUTSIDE and node.kind not in excluded
    }


def _band_score(share: float, rules: dict) -> float:
    """Full marks up to the minor band, falling to `at_major` across it, then steeply."""
    bands, scores = rules["share"], rules["scores"]
    if share <= bands["minor"]:
        return float(scores["at_minor"])
    if share <= bands["major"]:
        across = (share - bands["minor"]) / (bands["major"] - bands["minor"])
        return scores["at_minor"] + across * (scores["at_major"] - scores["at_minor"])
    over = (share - bands["major"]) * 100
    return max(0.0, scores["at_major"] - scores["per_point_over_major"] * over)


def _share(graph, share: float, passage, rules: dict) -> list[Finding]:
    bands, grades = rules["share"], rules["grades"]
    if share > bands["major"]:
        limit, grade = bands["major"], semantics.grade(grades["share_major"])
    elif share > bands["minor"]:
        limit, grade = bands["minor"], semantics.grade(grades["share_minor"])
    else:
        return []
    if grade is None or not passage:
        return []
    return [diagnose.circulation_share(graph, share, limit, passage, grade)]


def _exposure(graph: CirculationGraph, reported: set[str]) -> list[Finding]:
    """Private rooms opening straight off the doorstep or a room visitors are received in.

    One finding per host and grade, naming every room that opens off it that way.
    """
    rules = semantics.data()["exposure"]["rules"]
    exposed: dict[tuple[str, Grade], list[str]] = {}
    for edge in graph.edges:
        if edge.kind not in _WALKED:
            continue
        for host_id, target_id in ((edge.a, edge.b), (edge.b, edge.a)):
            if target_id in OUTSIDE or target_id in reported:
                continue
            host, target = graph.node(host_id), graph.node(target_id)
            if host is None or target is None:
                continue
            grade = _exposure_grade(rules, host, target)
            if grade is None:
                continue
            rooms = exposed.setdefault((host_id, grade), [])
            if target_id not in rooms:
                rooms.append(target_id)
    return [
        diagnose.exposure(graph, host, rooms, grade)
        for (host, grade), rooms in exposed.items()
    ]


def _exposure_grade(
    rules: list[dict], host: CirculationNode, target: CirculationNode
) -> Grade | None:
    for rule in rules:
        if host.role.value not in rule["host_roles"]:
            continue
        if target.role.value not in rule["target_roles"]:
            continue
        if "target_zones" in rule and target.zone.value not in rule["target_zones"]:
            continue
        return semantics.grade(rule["grade"])
    return None


def _corridor(
    topo: Topology, walks: JourneyAnalysis, node: CirculationNode, total: float, limits: dict
) -> Corridor:
    graph = topo.graph
    x1, y1, x2, y2 = node.rect
    along_x = (x2 - x1) >= (y2 - y1)
    length, width = (x2 - x1, y2 - y1) if along_x else (y2 - y1, x2 - x1)
    area = length * width
    doors = [(other, edge) for other, edge in graph.neighbours(node.id) if other not in OUTSIDE]
    served = sorted({other for other, _ in doors})
    roles = {room: graph.node(room).role for room in served}

    # Rooms off it that could open into each other instead: a living room and a dining
    # room it stands between. Private rooms are left out; a door between two bedrooms is
    # not an alternative to a corridor.
    rooms = [r for r in served if roles[r] not in _WAYS and roles[r] not in _KEPT_APART]
    alternatives = sum(
        1
        for i, a in enumerate(rooms)
        for b in rooms[i + 1:]
        if (contact := graph.contact(a, b)) is not None
        and contact.door_possible and not contact.has_door
    )

    essential = _essential(topo, node.id)
    used = sum(j.weight for j in walks.journeys if node.id in j.path[1:-1])
    dead_end = _dead_end(node.rect, along_x, [edge for _, edge in doors])
    inefficient = (
        (bool(served) and area / len(served) > limits["area_per_room_sq_m"])
        or width > limits["useful_width_m"]
        or dead_end > limits["dead_end_m"]
    )
    if not essential and used == 0:
        verdict = CorridorVerdict.REDUNDANT
    elif inefficient:
        verdict = CorridorVerdict.INEFFICIENT
    elif essential:
        verdict = CorridorVerdict.ESSENTIAL
    else:
        verdict = CorridorVerdict.EFFICIENT
    return Corridor(
        room=node.id,
        area_sq_m=round(area, 2),
        length_m=round(length, 2),
        width_m=round(width, 2),
        share=round(area / total, 4) if total else 0.0,
        rooms_served=len(served),
        private_served=sum(1 for r in served if roles[r] in _KEPT_APART),
        branches=sum(1 for r in served if roles[r] in _WAYS),
        dead_end_m=round(dead_end, 2),
        journey_weight=round(used, 2),
        alternatives=alternatives,
        essential=essential,
        verdict=verdict,
    )


def _essential(topo: Topology, corridor: str) -> bool:
    """Whether some room would lose its proper access without this corridor.

    Only rooms whose best route runs through it can lose anything, so only those are
    walked again with it taken out.
    """
    removed = frozenset({corridor})
    for row in topo.access:
        if corridor not in row.path[1:-1]:
            continue
        route = best_route(topo.graph, row.path[0], row.room, removed=removed)
        if route is None or semantics.rank(route.worst) > semantics.rank(row.grade):
            return True
    return False


def _dead_end(rect, along_x: bool, edges: list[CirculationEdge]) -> float:
    """The longest stretch at either end of a corridor past its last door."""
    lo, hi = (rect[0], rect[2]) if along_x else (rect[1], rect[3])

    def clamp(value: float) -> float:
        return min(hi, max(lo, value))

    spans = []
    for edge in edges:
        if edge.at is None:
            continue
        at = edge.at[0] if along_x else edge.at[1]
        # A door in a long side covers its width along the corridor; one in an end wall
        # is a point at that end.
        half = (edge.width_m or 0.0) / 2 if edge.vertical is not along_x else 0.0
        spans.append((clamp(at - half), clamp(at + half)))
    if not spans:
        return hi - lo
    return max(0.0, min(s for s, _ in spans) - lo, hi - max(e for _, e in spans))


def _foyer(graph: CirculationGraph, node: CirculationNode, rules: dict) -> Finding | None:
    """A foyer larger than a transition needs, judged by its shape and what it opens into.

    Somewhat oversized is minor. Well oversized, or somewhat oversized and also a long
    passage or not opening into the living room, is major.
    """
    spec, grades = rules["foyer"], rules["grades"]
    x1, y1, x2, y2 = node.rect
    long, short = max(x2 - x1, y2 - y1), min(x2 - x1, y2 - y1)
    area = long * short
    if area / spec["reference_sq_m"] < spec["oversize_minor"]:
        return None
    passage = short > 0 and long / short > spec["passage_aspect"]
    living = {n.id for n in rooms_for(graph, "living")}
    into_living = any(other in living for other, _ in graph.neighbours(node.id))
    worse = area / spec["reference_sq_m"] >= spec["oversize_major"] or passage or not into_living
    grade = semantics.grade(grades["foyer_major" if worse else "foyer_minor"])
    if grade is None:
        return None
    return diagnose.foyer(
        graph, node.id, area, spec["reference_sq_m"], grade,
        passage=passage, into_living=into_living,
    )
