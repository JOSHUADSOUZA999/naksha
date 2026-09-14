"""The walks a house is used for, and the rooms that belong together.

Reachability says a room can be got to; a journey says how. Each walk in
`circulation_v1` — a visitor from the front door to the living room, a resident from the
car into the house, the family from the stair to each bedroom — is walked on the best
route the plan allows and measured: how far, through how many rooms and doors, with how
many turns, past which zones it should not cross, and whether it had to go outside or
through a forced pass-through. Relationships ask the narrower question of whether rooms
used together are joined directly; chains ask whether a daily sequence of walks keeps
doubling back.

Shortest is not the goal. A foyer between the front door and the living room is an
allowed room on that walk, not a detour.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.circulation import diagnose, semantics
from app.circulation.graph import ENTRY, OUTSIDE, STREET
from app.circulation.routes import Route, best_route, host_grade
from app.circulation.topology import Topology
from app.ir.circulation import CirculationGraph, CirculationNode, Journey
from app.ir.enums import CirculationRole, EdgeKind, Grade, JourneyClass, Zone
from app.ir.validation import Finding


@dataclass
class JourneyAnalysis:
    journeys: list[Journey] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    relationships: float = 100.0
    walking: float = 100.0
    arrival: float | None = None
    vertical: list[float] = field(default_factory=list)


def rooms_for(graph: CirculationGraph, role: str) -> list[CirculationNode]:
    """The rooms a role name stands for on this storey, from `circulation_v1.roles`."""
    spec = semantics.data()["roles"].get(role)
    if spec is None or (spec.get("ground_only") and graph.floor != 1):
        return []

    def pick(kinds):
        return [
            n for n in graph.nodes
            if n.kind in kinds and not (spec.get("shared_only") and n.parents)
        ]

    found = pick(spec["kinds"]) or pick(spec.get("fallback", []))
    if not found and spec.get("owned_fallback"):
        # No shared one on this storey: a guest has to use a bedroom's own bathroom,
        # and walks through that bedroom to reach it.
        found = [n for n in graph.nodes if n.kind in spec["kinds"]]
    return found


def analyse(topo: Topology) -> JourneyAnalysis:
    out = JourneyAnalysis()
    if topo.blocked:
        return out
    graph = topo.graph
    rules = semantics.data()["journeys"]
    failed = {a.room for a in topo.access if a.grade is Grade.CRITICAL}
    walked: dict[str, list[tuple[Journey, Route | None, dict]]] = {}

    for spec in rules["list"]:
        for origin, destination in _pairs(graph, spec):
            outside_ok = graph.node(origin).role is CirculationRole.VEHICLE
            route = best_route(graph, origin, destination, allow_outside=outside_ok)
            journey = _measure(graph, spec, origin, destination, route, rules["scoring"])
            out.journeys.append(journey)
            walked.setdefault(spec["id"], []).append((journey, route, spec))

    rows = [row for rows in walked.values() for row in rows]
    # A room whose access is already reported is left to that finding: a house entered
    # through the kitchen is one defect, not an access finding, a reversed arrival and
    # a visitor crossing the service zone.
    reported = {a.room for a in topo.access if semantics.rank(a.grade) >= 2}
    parked: list[tuple[str, Route]] = []
    for journey, route, spec in rows:
        if route is None or journey.destination in reported:
            continue
        if "arrival_sequence" in spec.get("checks", []):
            if (found := _arrival_sequence(graph, route)) is not None:
                out.findings.append(found)
        if "parking" in spec.get("checks", []):
            parked.append((journey.origin, route))
    out.findings += _parking(graph, parked)
    out.findings += _zone_crossings(graph, rows, rules["classes"], reported)
    out.findings += _chains(graph, walked, rules)
    out.relationships = _relationships(graph, failed, out.findings)

    weight = sum(j.weight for j in out.journeys)
    if weight:
        out.walking = sum(j.weight * j.score for j in out.journeys) / weight
    arrival = [j.score for j, _, spec in rows if spec.get("dimension") == "arrival"]
    if graph.node(ENTRY) is not None and arrival:
        out.arrival = sum(arrival) / len(arrival)
    # Vertical walks are the ones that use the stair: on a storey with no stair, the
    # walk from the living room to the bedrooms is a journey, not vertical circulation.
    stairs = {
        n.id for n in graph.nodes
        if n.kind in semantics.data()["efficiency"]["vertical_kinds"]
    }
    out.vertical = [
        j.score for j, _, spec in rows
        if spec.get("dimension") == "vertical" and stairs & {j.origin, j.destination}
    ]
    return out


def _resolve(graph: CirculationGraph, token: str) -> list[str]:
    if token == "entry":
        return [ENTRY] if graph.node(ENTRY) is not None else []
    if token == "arrival_room":
        return [
            edge.b if edge.a == ENTRY else edge.a
            for edge in graph.edges
            if ENTRY in (edge.a, edge.b) and STREET not in (edge.a, edge.b)
        ]
    if token == "bedroom_arrival":
        if graph.floor > 1 and graph.arrival and graph.arrival not in OUTSIDE:
            return [graph.arrival]
        living = rooms_for(graph, "living")
        return [living[0].id] if living else _resolve(graph, "arrival_room")
    if token.startswith("each:"):
        role = token.removeprefix("each:")
        if role == "bedroom_without_en_suite":
            owned = {parent for node in graph.nodes for parent in node.parents}
            return [n.id for n in rooms_for(graph, "bedroom") if n.id not in owned]
        return [n.id for n in rooms_for(graph, role)]
    return [n.id for n in rooms_for(graph, token)]


def _pairs(graph: CirculationGraph, spec: dict) -> list[tuple[str, str]]:
    pairs = []
    for origin in _resolve(graph, spec["from"]):
        if spec["to"] == "its:en_suite":
            targets = [n.id for n in graph.nodes if origin in n.parents]
        else:
            targets = [t for t in _resolve(graph, spec["to"]) if t != origin]
            if not spec["to"].startswith("each:") and len(targets) > 1:
                targets = [_nearest(graph, origin, targets)]
        pairs += [(origin, target) for target in targets if target != origin]
    return pairs


def _nearest(graph, origin: str, targets: list[str]) -> str:
    def cost(target):
        route = best_route(graph, origin, target)
        if route is None:
            return (9, 0, float("inf"))
        return (semantics.rank(route.worst), route.transitions, route.distance_m)

    return min(targets, key=cost)


def _measure(graph, spec, origin, destination, route, scoring) -> Journey:
    base = dict(
        id=spec["id"], journey_class=JourneyClass(spec["class"]),
        weight=spec["weight"], origin=origin, destination=destination,
    )
    if route is None:
        return Journey(**base, reachable=False, score=0.0)
    goal = graph.node(destination)
    ends = [z for z in (graph.node(origin).zone, goal.zone) if z is not Zone.EXTERNAL]
    ceiling = max((semantics.ZONE_RANK[z] for z in ends), default=0)
    between = [graph.node(n) for n in route.intermediates]
    graded = [(n.id, host_grade(n, goal)) for n in between]
    crossings = sum(1 for n in between if semantics.ZONE_RANK[n.zone] > ceiling)
    allow = spec.get("allow", 1)
    score = 100.0
    score -= scoring["extra_transition"] * max(0, route.transitions - allow)
    score -= scoring["turn_over_budget"] * max(0, route.turns - (allow + 1))
    score -= scoring["privacy_crossing"] * crossings
    score -= sum(scoring["crossing"][g.value] for _, g in graded if g is not None)
    score -= scoring["external"] if route.external else 0
    score -= scoring["per_metre_over"] * max(
        0.0, route.distance_m - scoring["distance_budget_m"]
    )
    return Journey(
        **base, reachable=True, path=list(route.nodes),
        distance_m=round(route.distance_m, 2), doors=route.doors,
        transitions=route.transitions, turns=route.turns, privacy_crossings=crossings,
        inappropriate=[n for n, g in graded if semantics.rank(g) >= 2],
        forced_pass_through=any(e.kind is EdgeKind.FORCED_PASS_THROUGH for e in route.edges),
        external=route.external, score=max(0.0, min(100.0, score)),
    )


def _arrival_sequence(graph, route: Route) -> Finding | None:
    """From the front door to the living room: a foyer is right, one corridor is
    ordinary, a dining room or kitchen on the way reverses the public hierarchy."""
    rules = semantics.data()["arrival_sequence"]
    free, reversing = set(rules["free_roles"]), set(rules["reversing_roles"])
    between = [graph.node(n) for n in route.intermediates]
    reversed_rooms = [n.id for n in between if n.role.value in reversing]
    if reversed_rooms:
        grade = semantics.grade(rules["reversed"])
        return diagnose.arrival_sequence(graph, route, reversed_rooms, grade, reversed_=True)
    tolerated = rules["tolerated"]
    extra = []
    for role, allowed in tolerated.items():
        rooms = [n.id for n in between if n.role.value == role]
        extra += rooms if len(rooms) > allowed else []
    extra += [
        n.id for n in between
        if n.role.value not in free and n.role.value not in tolerated
        and n.role.value not in reversing
    ]
    if extra:
        grade = semantics.grade(rules["roundabout"])
        return diagnose.arrival_sequence(graph, route, extra, grade, reversed_=False)
    return None


def _parking(graph, parked: list[tuple[str, Route]]) -> list[Finding]:
    """How people get from the car into the house, one finding per kind of trouble.

    A short walk outside from the gate to the front door is how a car porch works and
    is minor; a long one, or a way in through rooms nobody should cross, is major.
    """
    rules = semantics.data()["parking"]
    groups: dict[tuple, list[tuple[str, Route]]] = {}
    for bay, route in parked:
        if semantics.rank(route.worst) >= 2:
            case = "through_rooms"
        elif route.external:
            case = "short_walk" if route.outside_m <= rules["outside_walk_m"] else "long_walk"
        else:
            continue
        grade = semantics.grade(rules[case])
        if grade is not None:
            hosts = route.hosts if case == "through_rooms" else ()
            groups.setdefault((case, grade, hosts), []).append((bay, route))
    return [
        diagnose.parking(graph, members, grade, case)
        for (case, grade, _), members in groups.items()
    ]


def _zone_crossings(graph, rows, classes, reported) -> list[Finding]:
    """Walks that pass through zones their walker should not have to enter — a visitor
    through the kitchen. Critical crossings are already access failures."""
    crossed: dict[tuple[str, str], list[Journey]] = {}
    for journey, route, _ in rows:
        avoid = set(classes[journey.journey_class.value]["avoid_zones"])
        if route is None or not avoid or journey.destination in reported:
            continue
        goal = graph.node(journey.destination)
        for room in route.intermediates:
            node = graph.node(room)
            if node.zone.value in avoid and host_grade(node, goal) is not Grade.CRITICAL:
                crossed.setdefault((journey.journey_class.value, room), []).append(journey)
    return [
        diagnose.zone_crossing(
            graph, JourneyClass(cls), room, journeys,
            semantics.grade(classes[cls]["grade"]),
        )
        for (cls, room), journeys in crossed.items()
        if semantics.grade(classes[cls]["grade"]) is not None
    ]


def _chains(graph, walked, rules) -> list[Finding]:
    """A daily sequence of walks that keeps returning through the same rooms."""
    findings = []
    for chain in rules["chains"]:
        legs = [walked.get(leg, []) for leg in chain["legs"]]
        if any(len(leg) != 1 or leg[0][1] is None for leg in legs):
            continue
        seen: set[str] = set()
        revisited: list[str] = []
        path: list[str] = []
        for index, [(journey, route, _)] in enumerate(legs):
            nodes = list(route.nodes)
            fresh = nodes if index == 0 else nodes[1:]
            again = [n for n in fresh if n in seen and n not in OUTSIDE]
            if again:
                journey.backtracking = len(again)
                journey.score = max(0.0, journey.score - rules["scoring"]["backtrack"] * len(again))
            revisited += again
            seen.update(nodes)
            path += fresh
        # A leg that is itself indirect is already reported, as a relationship or an
        # arrival, and the doubling back is its consequence: it costs the score, not a
        # second finding. Reported only when every leg on its own is fine.
        grade = semantics.grade(chain["grade"])
        within = all(leg[0][0].transitions <= leg[0][2].get("allow", 1) for leg in legs)
        if revisited and within and grade is not None:
            findings.append(diagnose.backtracking(graph, revisited, path, grade))
    return findings


def _relationships(graph, failed, findings: list[Finding]) -> float:
    """Rooms used together, joined directly or not. Returns the 0-100 score."""
    rules = semantics.data()["relationships"]
    total = weight = 0.0
    for pair in rules["pairs"]:
        sources = [n for role in pair["a"] for n in rooms_for(graph, role)]
        for target in rooms_for(graph, pair["b"]):
            options = [s for s in sources if s.id != target.id]
            if not options:
                continue
            routes = [(s, best_route(graph, s.id, target.id)) for s in options]
            routes = [(s, r) for s, r in routes if r is not None]
            weight += pair["weight"]
            if not routes:
                continue
            source, route = min(
                routes,
                key=lambda sr: (semantics.rank(sr[1].worst), sr[1].transitions, sr[1].distance_m),
            )
            steps = min(route.transitions, 2)
            total += pair["weight"] * rules["scores"][pair["want"]][steps]
            grade = semantics.grade(rules["grades"][pair["want"]][steps])
            if pair["weight"] < rules["strong_weight"]:
                grade = semantics.weaker(grade)
            if grade is not None and not {source.id, target.id} & failed:
                findings.append(diagnose.relationship(
                    graph, source.id, target.id, route, grade, pair,
                    alternatives=[s.id for s in options],
                ))
    return total / weight if weight else 100.0
