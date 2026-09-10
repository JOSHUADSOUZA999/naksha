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

from app.ir.enums import OpeningKind, Severity
from app.ir.layout import Layout
from app.ir.plan import Program
from app.ir.refined import RefinedFloor
from app.ir.validation import Finding, Report

CHECKS = ["circulation", "light", "legality"]


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
    walk_in = {room.id for room in program.rooms if room.needs_door}
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
        if placed.room_id not in seen and placed.room_id in walk_in
    )

    # Reachable *only* through a bedroom is its own defect, and a plan can have it
    # while every room is technically reachable. A 40x60 came out with the route to the
    # master bedroom's bathroom running hall → dining → bed2 → corridor → bed1 → bath1:
    # every check passed and a bedroom was serving as a corridor.
    findings = _through_private_rooms(layout, program, floor, graph, entrance)
    if not stranded:
        return findings

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
        ),
        *findings,
    ]


def _light(program: Program, floor: RefinedFloor) -> list[Finding]:
    """Is every habitable room glazed to the area the bye-laws ask for?

    Presence was the wrong question and it took building ⑥'s window sizing to see it.
    A room with one token opening passed a "has a window" check while being nowhere
    near the one-tenth of floor area the code requires — and the check reported nothing,
    which is worse than reporting a number that is too small.

    `needs_exterior_wall` from the ruleset, the same flag `score` penalises a room for
    missing and `refine` places windows from. A second opinion written out here said a
    dining room needs daylight while `spaces_v1` says it does not; one of them was going
    to be wrong, and it is not a module's call to make.
    """
    from app.rules import load_ruleset

    fraction = load_ruleset("refine_v1").data["windows"]["area_fraction"]
    findings: list[Finding] = []

    for room in program.rooms:
        if not room.needs_exterior_wall or room.id not in floor.clear:
            continue
        area = floor.clear_area_sq_m(room.id) or 0.0
        glazed = floor.window_area_sq_m(room.id)
        if area <= 0 or glazed >= area * fraction - 1e-6:
            continue

        if glazed <= 0:
            # Not "habitable room" in the no-window case: `needs_exterior_wall` is
            # also true of a car porch, which needs to be open and is nobody's idea of
            # a habitable room. The ratio message keeps the term because that is where
            # it carries its legal meaning.
            message = f"{room.id} has no window at all"
        else:
            message = (
                f"{room.id} is glazed to {glazed / area:.0%} of its floor area, "
                f"below the {fraction:.0%} a habitable room needs"
            )
        findings.append(
            Finding(
                check="light", severity=Severity.WARNING, message=message,
                rooms=[room.id],
            )
        )
    return findings


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


def check(bundle):
    """Stage ⑦ over a whole `PlanBundle` — every storey validated, in one call.

    Requires `floors`, because every question worth asking here is about the drawing:
    a bundle that has not been through ⑥ has no doors, and "no room is reachable" would
    be true of it and mean nothing. Returns the bundle unchanged in that case rather
    than reporting a plan-shaped absence as a defect.
    """
    if not bundle.floors:
        return bundle
    reports = [
        validate(layout, bundle.program, floor)
        for layout, floor in zip(bundle.layouts, bundle.floors)
    ]
    return bundle.model_copy(update={"reports": reports})


def _through_private_rooms(layout, program, floor, graph, entrance) -> list[Finding]:
    """Circulation spaces reachable only by walking through somewhere private.

    Narrower than "a private room is never a passage", deliberately. An en-suite is
    reached through its bedroom and that is what an en-suite is — a first version
    flagged every one of them as a defect. What is wrong is a *corridor* you reach
    through a bedroom, which makes that bedroom a passage.

    Walks the doors ⑥ actually drew, where `score._unwalkable` walks the tiling before
    any exist. The two can disagree, and the disagreement is the useful part: ⑥ hangs a
    door off a bedroom as a last resort when the alternative is a room with no way in.
    """
    through = {r.id for r in program.rooms if r.is_through_route and r.needs_door}
    start = entrance.connects[0]

    seen = {start}
    stack = [start]
    while stack:
        for neighbour in graph.get(stack.pop(), ()):
            # Spine to spine only: a private room is where the walk stops.
            if neighbour in through and neighbour not in seen:
                seen.add(neighbour)
                stack.append(neighbour)

    detoured = sorted(
        r.room_id for r in layout.rooms if r.room_id in through and r.room_id not in seen
    )
    if not detoured:
        return []
    return [
        Finding(
            check="circulation",
            severity=Severity.WARNING,
            message=(
                f"{', '.join(detoured)} can only be reached by walking through a "
                f"bedroom or bathroom \u2014 circulation should not run through a "
                f"private room"
            ),
            rooms=detoured,
        )
    ]
