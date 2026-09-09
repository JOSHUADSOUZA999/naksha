"""Stage ⑦ VALIDATE — is this finished plan actually a house?

Runs once, on the drawing, after ⑥ has put in walls and doors. That is what separates
it from `score`, which ranks candidates during the search and must stay cheap enough to
run tens of thousands of times.

**The check that justified building this stage is circulation.** Every plan the
pipeline called clean was a house you could not walk through: on a 30x50 you came in
the front door and reached one room out of eleven. Nothing measured it, because nothing
had reason to — stage ⑤ scores adjacency, and two rooms sharing a wall is not the same
claim as being able to get from one to the other. Doors are ⑥'s output, so reachability
is the first question that can only be asked here.
"""

from __future__ import annotations

from collections import defaultdict, deque

from app.ir.enums import OpeningKind, Severity, SpaceKind
from app.ir.layout import Layout
from app.ir.plan import Program
from app.ir.refined import RefinedFloor
from app.ir.validation import Finding, Report

CHECKS = ["circulation", "light", "legality"]

# Spaces a person is expected to walk into. A shaft or a duct would not be; everything
# here is somewhere someone stands.
_REACHABLE_KINDS = {
    SpaceKind.HALL, SpaceKind.DINING, SpaceKind.KITCHEN, SpaceKind.BEDROOM,
    SpaceKind.MASTER_BEDROOM, SpaceKind.GUEST_ROOM, SpaceKind.SERVANT_ROOM,
    SpaceKind.BATHROOM, SpaceKind.WC, SpaceKind.POOJA, SpaceKind.STUDY,
    SpaceKind.OFFICE, SpaceKind.STORE, SpaceKind.UTILITY, SpaceKind.CORRIDOR,
    SpaceKind.FOYER, SpaceKind.STAIRCASE,
}

# Rooms the bye-laws want lit and ventilated — the same set `spaces_v1` marks
# `exterior_wall`, asked here of the drawing rather than of the tiling.
_NEEDS_LIGHT = {
    SpaceKind.HALL, SpaceKind.DINING, SpaceKind.KITCHEN, SpaceKind.BEDROOM,
    SpaceKind.MASTER_BEDROOM, SpaceKind.GUEST_ROOM, SpaceKind.STUDY, SpaceKind.OFFICE,
}


def validate(layout: Layout, program: Program, floor: RefinedFloor) -> Report:
    """Every check, against the drawn plan."""
    findings: list[Finding] = []
    findings += _circulation(layout, program, floor)
    findings += _light(program, floor)
    findings += _legality(program, floor)
    return Report(floor=layout.floor, findings=findings, checks_run=list(CHECKS))


def _circulation(layout: Layout, program: Program, floor: RefinedFloor) -> list[Finding]:
    """Can you get from the front door to every room?

    Reachability over the *doors*, not the adjacency graph. Two rooms sharing a wall is
    stage ⑤'s claim; a door in that wall is ⑥'s, and only the second one lets anybody
    through. The graph is undirected because a door works both ways, and the walk
    starts at the entrance because a house is entered from the street — starting
    anywhere else would call a perfectly sealed cluster of rooms connected.
    """
    kinds = {room.id: room.kind for room in program.rooms}
    entrance = next(
        (o for o in floor.openings if o.kind is OpeningKind.ENTRANCE), None
    )
    if entrance is None or not entrance.connects:
        return [
            Finding(
                check="circulation",
                severity=Severity.ERROR,
                message="the plan has no front door, so no room is reachable at all",
            )
        ]

    graph: dict[str, set[str]] = defaultdict(set)
    for opening in floor.openings:
        if opening.kind is not OpeningKind.DOOR or len(opening.connects) != 2:
            continue
        a, b = opening.connects
        graph[a].add(b)
        graph[b].add(a)

    start = entrance.connects[0]
    seen = {start}
    queue = deque([start])
    while queue:
        for neighbour in graph[queue.popleft()]:
            if neighbour not in seen:
                seen.add(neighbour)
                queue.append(neighbour)

    stranded = sorted(
        placed.room_id
        for placed in layout.rooms
        if placed.room_id not in seen
        and kinds.get(placed.room_id) in _REACHABLE_KINDS
    )
    if not stranded:
        return []

    # One finding, not one per room. Twelve separate "you cannot reach the kitchen"
    # lines describe a single defect — the plan is not connected — and splitting it up
    # makes one large problem look like twelve small ones.
    return [
        Finding(
            check="circulation",
            severity=Severity.ERROR,
            message=(
                f"{len(stranded)} of {len(layout.rooms)} rooms cannot be reached from "
                f"the front door: {', '.join(stranded)}"
            ),
            rooms=stranded,
        )
    ]


def _light(program: Program, floor: RefinedFloor) -> list[Finding]:
    """A habitable room with no window.

    `score` already penalises a room with no *exterior wall*, and this is the stricter
    question: the wall may exist and still be too short to hold an opening, in which
    case the tiling passed and the drawing has a bedroom with no daylight.
    """
    kinds = {room.id: room.kind for room in program.rooms}
    lit = {
        room
        for opening in floor.openings
        if opening.kind is OpeningKind.WINDOW
        for room in opening.connects
    }
    dark = sorted(
        room_id
        for room_id, kind in kinds.items()
        if kind in _NEEDS_LIGHT and room_id not in lit and room_id in floor.clear
    )
    return [
        Finding(
            check="light",
            severity=Severity.WARNING,
            message=f"{room_id} is a habitable room with no window",
            rooms=[room_id],
        )
        for room_id in dark
    ]


def _legality(program: Program, floor: RefinedFloor) -> list[Finding]:
    """Rooms below a statutory minimum, measured on the clear floor.

    Delegates to `refine.breaches`, which recomputes independently of the scorer. Stage
    ⑤ should already have refused these; the value of asking again here is that a plan
    reaching a person has been checked by something that did not also produce it.
    """
    from app.refine import breaches

    return [
        Finding(
            check="legality",
            severity=Severity.ERROR,
            message=message,
            rooms=[message.split()[0]],
        )
        for message in breaches(floor, program)
    ]
