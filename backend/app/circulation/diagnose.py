"""Findings in words a plot owner can act on: what is wrong, why it matters, what to do.

Every sentence is built from the graph — room kinds, the route, the walls two rooms
share and how long they are — never from a room's name, so the same words are right for
any plan with the same defect. Lengths and areas go through `units`, feet first.
"""

from __future__ import annotations

from app.circulation import semantics
from app.circulation.graph import BELOW, ENTRY, OUTSIDE, STREET
from app.ir.circulation import CirculationGraph, WallContact
from app.ir.enums import CirculationRole, CorridorVerdict, Grade
from app.ir.units import area_text, length_text, square_feet
from app.ir.validation import Finding

# Rooms a door of one's own may properly open onto.
_OPEN_ONTO = (
    CirculationRole.ARRIVAL, CirculationRole.CIRCULATION,
    CirculationRole.SOCIAL, CirculationRole.OPEN,
)


def finding(
    rule: str, grade: Grade, message: str, rooms: list[str], *,
    why: str, fix: str, path: list[str] | None = None,
) -> Finding:
    return Finding(
        check="circulation", severity=grade.severity, grade=grade, rule=rule,
        message=message, rooms=rooms, why=why, fix=fix, path=path or [],
    )


def label(graph: CirculationGraph, node_id: str) -> str:
    """`bedroom (bed2)`, or just `hall` where the id already names the kind."""
    if node_id == ENTRY:
        return "the front door"
    if node_id == STREET:
        return "the street"
    if node_id == BELOW:
        return "the stair from below"
    node = graph.node(node_id)
    if node is None:
        return node_id
    kind = node.kind.replace("_", " ")
    return kind if node_id == node.kind else f"{kind} ({node_id})"


def names(graph: CirculationGraph, ids) -> str:
    labels = [label(graph, i) for i in ids]
    if len(labels) <= 1:
        return "".join(labels)
    return f"{', '.join(labels[:-1])} and {labels[-1]}"


def route_text(nodes) -> str:
    return " → ".join("front door" if n == ENTRY else n for n in nodes if n != STREET)


def plural(ids, one: str, many: str) -> str:
    return one if len(list(ids)) == 1 else many


def opening_onto(graph: CirculationGraph, room_id: str) -> tuple[WallContact, str] | None:
    """The longest wall a room shares with a space its own door could open onto."""
    best = None
    for contact in graph.contacts:
        if room_id not in (contact.a, contact.b):
            continue
        other = contact.b if contact.a == room_id else contact.a
        node = graph.node(other)
        if node is None or node.role not in _OPEN_ONTO:
            continue
        if best is None or contact.shared_m > best[0].shared_m:
            best = (contact, other)
    return best


def _own_door_fix(graph: CirculationGraph, room_id: str) -> str:
    room = label(graph, room_id)
    best = opening_onto(graph, room_id)
    if best is None:
        return (
            f"{room.capitalize()} has no wall on any corridor, landing or living space. "
            f"Bring the circulation to it — extend the corridor or landing along it — so "
            f"it can have a door of its own."
        )
    contact, other = best
    onto = label(graph, other)
    if not contact.door_possible:
        return (
            f"Extend {onto} along {room} so they share at least "
            f"{length_text(graph.required_door_m)} of wall, and give {room} its own door "
            f"onto it."
        )
    return (
        f"Give {room} its own door onto {onto}; they already share "
        f"{length_text(contact.shared_m)} of wall."
    )


def _short_wall(graph: CirculationGraph, room_id: str) -> str:
    """The geometric reason, when there is one: a wall too short for a door."""
    best = opening_onto(graph, room_id)
    if best is None or best[0].door_possible:
        return ""
    contact, other = best
    return (
        f" {label(graph, room_id).capitalize()} shares only "
        f"{length_text(contact.shared_m)} of wall with {label(graph, other)}, too little "
        f"for a door, which needs {length_text(graph.required_door_m)} — so the only "
        f"door it could get runs through another room."
    )


def no_front_door() -> Finding:
    return finding(
        "circulation.no_front_door", Grade.CRITICAL,
        "the plan has no front door, so no room is reachable at all", [],
        why="Without an entrance on a road-facing wall nothing in the house can be "
        "reached, whatever else the plan gets right.",
        fix="Put the foyer, or whichever room receives visitors, against the road so a "
        "front door can open into it.",
    )


def no_stair(floor: int) -> Finding:
    return finding(
        "circulation.no_stair", Grade.CRITICAL,
        "this storey has no staircase, so there is no way up to it", [],
        why=f"Floor {floor} is only part of the house if a stair arrives on it.",
        fix="Give this storey a staircase standing over the staircase below.",
    )


def stair_misaligned(stairs: list[str]) -> Finding:
    return finding(
        "circulation.stair_misaligned", Grade.CRITICAL,
        f"{', '.join(stairs)} does not sit over the staircase below, so there is no way "
        f"up to this storey", stairs,
        why="A staircase is one shaft through the building; flights that do not stand "
        "over each other do not connect the storeys.",
        fix="Place this storey's staircase directly over the one below.",
    )


def unreachable(rooms: list[str], *, total: int, upstairs: bool) -> Finding:
    start = "the stair" if upstairs else "the front door"
    return finding(
        "circulation.unreachable", Grade.CRITICAL,
        f"{len(rooms)} of {total} rooms cannot be reached from {start}: "
        f"{', '.join(rooms)}", rooms,
        why="No sequence of doors leads to them, so as drawn they are not part of the "
        "house at all.",
        fix="Give each a door onto a corridor, landing or living space that is itself "
        "reachable.",
    )


def access(graph: CirculationGraph, grade: Grade, hosts: list[str], members) -> Finding:
    rooms = [node.id for node, _ in members]
    route = members[0][1]
    who, via = names(graph, rooms), names(graph, hosts)
    is_are = plural(hosts, "is", "are")
    if grade is Grade.CRITICAL:
        message = (
            f"{who} {plural(rooms, 'has', 'have')} no independent access from the "
            f"circulation network"
        )
        why = (
            f"{via.capitalize()} {is_are} being used as a passage to reach {who} "
            f"({route_text(route.nodes)}). A private room on the way to somewhere else "
            f"loses its privacy to everyone walking through it."
            + _short_wall(graph, rooms[0])
        )
    else:
        message = f"{who} can only be reached through {via}"
        why = (
            f"{via.capitalize()} {is_are} not circulation, but every walk to {who} "
            f"passes through {plural(hosts, 'it', 'them')} ({route_text(route.nodes)})."
        )
    fix = " ".join(_own_door_fix(graph, room) for room in rooms)
    return finding(
        "circulation.access", grade, message, rooms,
        why=why, fix=fix, path=list(route.nodes),
    )


def en_suite(graph: CirculationGraph, bath, route, grade: Grade) -> Finding:
    parent = route.nodes[0]
    suite, room = label(graph, parent), label(graph, bath.id)
    between = [n for n in route.nodes[1:-1] if n not in OUTSIDE]
    contact = graph.contact(bath.id, parent)
    if contact is not None and contact.door_possible:
        fix = (
            f"Open {room} directly from {suite}; they already share "
            f"{length_text(contact.shared_m)} of wall."
        )
    elif contact is not None:
        fix = (
            f"{room.capitalize()} shares only {length_text(contact.shared_m)} of wall with "
            f"{suite}. Place it against the bedroom so a door fits "
            f"({length_text(graph.required_door_m)})."
        )
    else:
        fix = (
            f"Move {room} beside {suite} so it opens from the bedroom — or make it a "
            f"shared bathroom off the corridor and give {suite} a bathroom of its own."
        )
    if grade is Grade.CRITICAL:
        message = (
            f"the en-suite of {suite}, {room}, is not connected to its suite and is "
            f"reached through {names(graph, route.hosts)}"
        )
        why = (
            f"An en-suite should open from its own bedroom. Here the way from the bedroom "
            f"runs {route_text(route.nodes)}, so {names(graph, route.hosts)} doubles as a "
            f"passage and the suite has no private bathroom."
        )
    else:
        message = (
            f"{room} is meant as the en-suite of {suite} but opens off "
            f"{names(graph, between)}, not the bedroom"
        )
        why = (
            "Reached by way of the circulation it is a shared bathroom, and the bedroom "
            "it was planned for has none of its own."
        )
    return finding(
        "circulation.en_suite", grade, message, [bath.id, parent],
        why=why, fix=fix, path=list(route.nodes),
    )


def stair_arrival(graph, stair: str, rooms: list[str], grade: Grade, kind: str) -> Finding:
    flight, into = label(graph, stair), names(graph, rooms)
    if kind == "no_exit":
        message, why, fix = (
            f"{flight} opens onto nothing on this storey",
            "A stair must arrive somewhere a person can walk on from.",
            "Give the stair a landing or corridor on this storey.",
        )
    elif kind == "sanitary":
        message, why, fix = (
            f"{flight} opens straight into {into}",
            "A stair whose only way on is a bathroom is never acceptable: everyone "
            "coming up walks through it to reach the rest of the storey.",
            "Make the stair open onto a landing or corridor, and move the bathroom's "
            "door off the stair.",
        )
    elif kind == "extra_private":
        message, why, fix = (
            f"{flight} also opens straight into {into}",
            "The stair already has a landing; a private room opening onto the stair "
            "itself is on the way up for everyone who climbs it.",
            f"Move the door of {into} onto the landing.",
        )
    else:
        message = f"{flight} arrives in {into} rather than a landing or corridor"
        why = (
            "Arriving in a living room can work in a family home, but a landing is what "
            "gives the bedrooms their privacy."
            if grade is Grade.MINOR else
            f"Coming upstairs means walking into {into}, which then becomes the way to "
            f"the rest of the storey."
        )
        fix = "Give the stair a landing or corridor that the bedrooms open off."
    return finding(
        "circulation.stair_arrival", grade, message, [stair, *rooms], why=why, fix=fix,
    )


def arrival_sequence(graph, route, rooms: list[str], grade: Grade, *, reversed_: bool) -> Finding:
    living = route.nodes[-1]
    lounge, via = label(graph, living), names(graph, rooms)
    arrival = next((n for n in route.nodes[1:-1] if n not in OUTSIDE), None)
    contact = graph.contact(arrival, living) if arrival is not None else None
    if contact is not None and contact.door_possible:
        fix = (
            f"Open {label(graph, arrival)} straight into {lounge}; they already share "
            f"{length_text(contact.shared_m)} of wall."
        )
    elif arrival is not None:
        fix = (
            f"Place {lounge} directly beyond {label(graph, arrival)} so the front door leads "
            f"into it, and reach {via} from there."
        )
    else:
        fix = f"Make the front door lead into {lounge}."
    if reversed_:
        message = f"the front door leads through {via} before reaching {lounge}"
        why = (
            f"A visitor should arrive through the foyer into the living room and go on from "
            f"there. Passing {via} first reverses the public hierarchy: the house is entered "
            f"through its middle ({route_text(route.nodes)})."
        )
    else:
        message = f"the way from the front door to {lounge} detours through {via}"
        why = (
            f"The arrival works but is roundabout ({route_text(route.nodes)}): more than the "
            f"foyer stands between the front door and {lounge}."
        )
    return finding(
        "circulation.arrival_sequence", grade, message, [living, *rooms],
        why=why, fix=fix, path=list(route.nodes),
    )


def parking(graph, members, grade: Grade, case: str) -> Finding:
    bays = [bay for bay, _ in members]
    cars, route = names(graph, bays), members[0][1]
    if case == "through_rooms":
        via = names(graph, route.hosts)
        message = f"from {cars} the way into the house runs through {via}"
        why = (
            f"Everyone arriving by car, and everything they carry, passes through {via} "
            f"({route_text(route.nodes)})."
        )
    else:
        walks = " and ".join(length_text(r.outside_m) for _, r in members)
        message = f"{cars} {plural(bays, 'has', 'have')} no door into the house"
        if case == "short_walk":
            why = (
                f"It works as a car porch: from the gate to the front door is {walks} along "
                f"the front of the house. A door from the bay into the house would keep that "
                f"walk indoors, in the rain or with shopping."
            )
        else:
            why = (
                f"From the car you walk out through the vehicle gate and {walks} round to the "
                f"front door ({route_text(route.nodes)}): a long way outside, in the rain or "
                f"with shopping."
            )
    fixes = []
    for bay in bays:
        car, best = label(graph, bay), opening_onto(graph, bay)
        if best is None:
            fixes.append(
                f"Place {car} beside the foyer or a corridor so it can have a door into the house."
            )
        elif best[0].door_possible:
            fixes.append(
                f"Give {car} a door into {label(graph, best[1])}; they already share "
                f"{length_text(best[0].shared_m)} of wall."
            )
        else:
            fixes.append(
                f"Lengthen the wall {car} shares with {label(graph, best[1])} to at least "
                f"{length_text(graph.required_door_m)} so a door fits."
            )
    return finding(
        "circulation.parking", grade, message, bays,
        why=why, fix=" ".join(fixes), path=list(route.nodes),
    )


_WALKERS = {
    "visitor": "visitors", "resident": "residents", "private": "the family",
    "service": "service trips",
}


def zone_crossing(graph, walker, room: str, journeys, grade: Grade) -> Finding:
    through = label(graph, room)
    ends = sorted({journey.destination for journey in journeys})
    zone = graph.node(room).zone.value.replace("_", "-")
    who = _WALKERS.get(walker.value, walker.value)
    message = f"{who} walk through {through} to reach {names(graph, ends)}"
    why = (
        f"{through.capitalize()} is {zone} space, and a {walker.value} walk should not have "
        f"to cross it ({route_text(journeys[0].path)})."
    )
    fix = (
        f"Give {names(graph, ends)} a way in that avoids {through}: a door off the foyer, "
        f"the living room or a corridor."
    )
    return finding(
        "circulation.zone_crossing", grade, message, [room, *ends],
        why=why, fix=fix, path=list(journeys[0].path),
    )


def backtracking(graph, revisited: list[str], path: list[str], grade: Grade) -> Finding:
    again = sorted(set(revisited))
    start, end = label(graph, path[0]), label(graph, path[-1])
    message = (
        f"the everyday walk from {start} to {end} doubles back through {names(graph, again)}"
    )
    why = (
        f"Walked as a sequence ({route_text(path)}), each leg returns through rooms the last "
        f"one already crossed, so the same spaces carry the same trips twice."
    )
    fix = (
        f"Arrange the rooms on this walk so each opens into the next, instead of every leg "
        f"going back through {names(graph, again)}."
    )
    return finding("circulation.backtracking", grade, message, again, why=why, fix=fix, path=list(path))


def relationship(
    graph, a: str, b: str, route, grade: Grade, pair: dict, *, alternatives=(),
) -> Finding:
    first, second = label(graph, a), label(graph, b)
    between = [n for n in route.nodes[1:-1] if n not in OUTSIDE]
    via = names(graph, between)
    strong = semantics.data()["relationships"]["strong_weight"]
    together = (
        "used together every day" if pair["weight"] >= strong
        else "meant to be close to each other"
    )
    others = [room for room in alternatives if room != a]
    if pair["want"] == "direct" and others:
        either = " or ".join(label(graph, room) for room in [a, *others])
        message = (
            f"{second} does not open off {either}: the nearest way, from {first}, runs "
            f"through {via}"
        )
    elif pair["want"] == "direct":
        message = f"{first} and {second} are not joined directly: the way between runs through {via}"
    else:
        message = f"{second} is a long way from {first}: the way runs through {via}"
    why = (
        f"{first.capitalize()} and {second} are {together}, so every trip between them "
        f"passes through {via} ({route_text(route.nodes)})."
    )
    contact = graph.contact(a, b)
    if contact is not None and contact.door_possible:
        fix = (
            f"Open a door or a wide opening between {first} and {second}; they already share "
            f"{length_text(contact.shared_m)} of wall."
        )
    elif contact is not None:
        fix = (
            f"{first.capitalize()} and {second} share only {length_text(contact.shared_m)} of "
            f"wall; lengthen it to at least {length_text(graph.required_door_m)} and open it up."
        )
    else:
        fix = f"Place {second} against {first} so they can open into each other; today they share no wall."
    return finding(
        "circulation.relationship", grade, message, [a, b],
        why=why, fix=fix, path=list(route.nodes),
    )


def exposure(graph, host: str, targets: list[str], grade: Grade) -> Finding:
    rooms, them = names(graph, targets), plural(targets, "it", "them")
    if host == ENTRY:
        message = f"the front door opens straight into {rooms}"
        why = (
            f"Everyone who comes to the door sees into {rooms}, and a visitor's first step "
            f"into the house is a step into {them}."
        )
        fix = (
            f"Put a foyer or the living room between the front door and {rooms}, and open "
            f"{them} off a corridor or landing instead."
        )
        where = list(targets)
    else:
        off = label(graph, host)
        message = f"{rooms} {plural(targets, 'opens', 'open')} straight off {off}"
        why = (
            f"{off.capitalize()} is where visitors arrive or are received; a door from it "
            f"straight into {rooms} puts {plural(targets, 'a private room', 'private rooms')} "
            f"on the visitors' route."
        )
        fix = (
            f"Move the {plural(targets, 'door', 'doors')} of {rooms} onto a corridor or "
            f"landing that visitors do not use."
        )
        where = [*targets, host]
    return finding(
        "circulation.exposure", grade, message, where,
        why=why, fix=fix, path=[host, targets[0]],
    )


def circulation_share(graph, share: float, limit: float, contributors, grade: Grade) -> Finding:
    parts = ", ".join(f"{label(graph, room)} {square_feet(area)}" for room, area in contributors)
    message = f"corridors and foyers take {share:.0%} of this floor"
    why = (
        f"Passage is floor nobody lives in ({parts}). Above {limit:.0%}, a plan is paying for "
        f"getting about rather than for rooms."
    )
    fix = (
        f"Shorten or narrow {label(graph, contributors[0][0])} so rooms open onto rooms or a "
        f"short landing rather than onto long passage."
    )
    return finding(
        "circulation.share", grade, message, [room for room, _ in contributors], why=why, fix=fix,
    )


def foyer(graph, room: str, area: float, reference: float, grade: Grade, *,
          passage: bool, into_living: bool) -> Finding:
    name = label(graph, room)
    message = (
        f"{name} is {area_text(area)}, {area / reference:.1f}× the {area_text(reference)} a "
        f"foyer needs"
    )
    reasons = []
    if passage:
        reasons.append("it is a long passage rather than a place to arrive")
    if not into_living:
        reasons.append("it does not open into the living room")
    why = (
        "A foyer is a transition: somewhere to take off shoes and be received before the "
        "living room. "
        + (f"Here {' and '.join(reasons)}." if reasons else "This one is larger than that job needs.")
    )
    fix = (
        f"Bring {name} down toward {area_text(reference)}, open it into the living room, and "
        f"give the area saved to the rooms beyond it."
    )
    return finding("circulation.foyer", grade, message, [room], why=why, fix=fix)


def corridor(graph, result, grade: Grade, limits: dict) -> Finding:
    name = label(graph, result.room)
    if result.verdict is CorridorVerdict.REDUNDANT:
        message = f"{name} does no circulation work the house needs"
        why = (
            f"Every room it serves keeps proper access without it, and none of the everyday "
            f"walks pass through it: {square_feet(result.area_sq_m)} of passage for nothing."
        )
        fix = f"Remove {name} and give its floor to the rooms beside it."
    else:
        excess = []
        if result.rooms_served and result.area_sq_m / result.rooms_served > limits["area_per_room_sq_m"]:
            excess.append(f"{square_feet(result.area_sq_m)} for {result.rooms_served} doors")
        if result.width_m > limits["useful_width_m"]:
            excess.append(
                f"{length_text(result.width_m)} wide where {length_text(limits['useful_width_m'])} serves"
            )
        if result.dead_end_m > limits["dead_end_m"]:
            excess.append(f"a {length_text(result.dead_end_m)} dead end")
        message = f"{name} spends more floor than its doors need: {', '.join(excess)}"
        why = (
            "It does necessary work, since rooms open off it, but a corridor is judged by what "
            "it serves, and this one is bigger than that."
        )
        fix = f"Narrow or shorten {name} to what its doors need and give the rest to the rooms along it."
    return finding("circulation.corridor", grade, message, [result.room], why=why, fix=fix)
