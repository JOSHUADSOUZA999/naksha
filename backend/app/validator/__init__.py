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

from app.ir.enums import Facing, OpeningKind, Relation, Severity, SpaceKind, WallKind
from app.ir.layout import TOLERANCE_M, Layout
from app.ir.plan import Program
from app.ir.refined import RefinedFloor
from app.ir.units import area_text, length_text
from app.ir.validation import Finding, Report

CHECKS = [
    "circulation", "access", "sanitation", "size", "light", "ventilation", "legality",
]

# Where a person sleeps or bathes. A route may end in one of these; it may not pass
# through one — except into a bathroom its own bedroom was built to serve.
_BEDROOMS = {
    SpaceKind.BEDROOM, SpaceKind.MASTER_BEDROOM, SpaceKind.GUEST_ROOM,
    SpaceKind.SERVANT_ROOM,
}
_BATHS = {SpaceKind.BATHROOM, SpaceKind.WC}
# The rooms a house is lived in. Reached only through a private room, the plan has no
# honest way in at all — see `_through_private_rooms`.
_COMMON = {SpaceKind.HALL, SpaceKind.DINING, SpaceKind.KITCHEN}


def validate(layout: Layout, program: Program, floor: RefinedFloor) -> Report:
    """Every check, against the drawn plan."""
    findings: list[Finding] = []
    findings += _circulation(layout, program, floor)
    findings += _access(layout, program, floor)
    findings += _sanitation(layout, program)
    findings += _size(layout, program, floor)
    findings += _light(program, floor)
    findings += _ventilation(program, floor)
    findings += _legality(program, floor)
    cross, single = _airflow(program, floor)
    return Report(
        floor=layout.floor, findings=findings, checks_run=list(CHECKS),
        cross_ventilated=cross, single_sided=single,
    )


def _circulation(layout: Layout, program: Program, floor: RefinedFloor) -> list[Finding]:
    """Can you get from the front door to every room?

    Reachability over the *doors*, not the adjacency graph. Two rooms sharing a wall is
    stage ⑤'s claim; a door in that wall is ⑥'s, and only the second one lets anybody
    through. The graph is undirected because a door works both ways, and the walk
    starts at the entrance because a house is entered from the street — starting
    anywhere else would call a perfectly sealed cluster of rooms connected.
    """
    walk_in = {room.id for room in program.rooms if room.needs_door}

    # **Where you arrive depends on the storey.** A ground floor is entered from the
    # street; a first floor is entered off the stair, and demanding a front door up
    # there reported every upper floor in the project as unreachable — which is how a
    # 25x40 and a 30x30, both of them freshly working, came back with errors.
    entrance = next(
        (o for o in floor.openings if o.kind is OpeningKind.ENTRANCE), None
    )
    if entrance is not None and entrance.connects:
        start = entrance.connects[0]
    elif layout.floor > 1:
        start = next(
            (
                placed.room_id
                for placed in layout.rooms
                if program_kind(program, placed.room_id) is SpaceKind.STAIRCASE
            ),
            None,
        )
    else:
        # **Never the stair on the ground floor.** The fallback to the stair was written
        # for upper storeys and fired on floor 1 too whenever a stair existed — so a
        # ground floor with no front door was walked from its staircase instead, found
        # every room reachable, and could come back clean. The judge trusts this verdict,
        # which made it a way for a house nobody can enter to win.
        start = None
    if start is None:
        message = (
            "the plan has no front door, so no room is reachable at all"
            if layout.floor == 1
            else "this storey has no staircase, so there is no way up to it"
        )
        return [
            Finding(check="circulation", severity=Severity.ERROR, message=message)
        ]

    # **A stair that does not sit over the stair below is not a way up.** Upstairs the
    # walk starts at the staircase, and nothing asked whether that staircase met the one
    # on the storey beneath. A 30x40 3BHK from the model put the ground-floor stair in
    # the north-east and the first-floor stair in the north-west, zero overlap, and both
    # floors came back clean — as did a 30x50, the stilt plans and a 20x30. The judge
    # trusted that verdict, so a house nobody could climb was free to win. `layout.shafts`
    # carries the stair the storey below settled on; empty means solved alone, and then
    # there is nothing to compare with.
    below = layout.shafts.get(SpaceKind.STAIRCASE)
    if layout.floor > 1 and below is not None:
        stairs = [
            placed for placed in layout.rooms
            if program_kind(program, placed.room_id) is SpaceKind.STAIRCASE
        ]
        if not any(_stacked_share(stair, below) >= _STAIR_LANDING for stair in stairs):
            ids = sorted(stair.room_id for stair in stairs)
            return [
                Finding(
                    check="circulation",
                    severity=Severity.ERROR,
                    message=(
                        f"{', '.join(ids)} does not sit over the staircase below, "
                        "so there is no way up to this storey"
                    ),
                    rooms=ids,
                )
            ]

    graph: dict[str, set[str]] = defaultdict(set)
    for opening in floor.openings:
        if opening.kind is not OpeningKind.DOOR or len(opening.connects) != 2:
            continue
        a, b = opening.connects
        graph[a].add(b)
        graph[b].add(a)

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
    findings = _through_private_rooms(layout, program, floor, graph, start)

    # **The front door should lead into the house.** The model's 30x40 3BHK was entered
    # foyer → staircase → corridor → hall: every room reachable, no private room crossed,
    # so nothing here objected. The room you step into from the foyer is where the house
    # is lived in, not the stair hall. Only where this storey has a hall or dining room —
    # the ground floor of a stilt holds neither, and its entrance leads rightly to the stair.
    if entrance is not None:
        living = {
            placed.room_id for placed in layout.rooms
            if program_kind(program, placed.room_id) in (SpaceKind.HALL, SpaceKind.DINING)
        }
        if living and start not in living and not graph[start] & living:
            beyond = ", ".join(sorted(graph[start])) or "no other room"
            findings.append(
                Finding(
                    check="circulation",
                    severity=Severity.WARNING,
                    message=(
                        f"the front door does not lead into the house: {start} opens "
                        f"into {beyond}, not the {' or '.join(sorted(living))}"
                    ),
                    rooms=[start],
                )
            )

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
        # A car bay's light and air come through its opening to the road. It needs an
        # exterior wall for that opening, not for glass, and once ⑥ stopped drawing it a
        # window every bay in the project was reported as a room with none.
        if room.kind is SpaceKind.CAR_PARKING:
            continue
        area = floor.clear_area_sq_m(room.id) or 0.0
        glazed = floor.window_area_sq_m(room.id)
        if area <= 0 or glazed >= area * fraction - 1e-6:
            continue

        if glazed <= 0:
            # Not "habitable room" in the no-window case: `needs_exterior_wall` is also
            # true of a pooja room or a store, which nobody calls habitable. The ratio
            # message keeps the term because that is where it carries its legal meaning.
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


def _ventilation(program: Program, floor: RefinedFloor) -> list[Finding]:
    """Does every bathroom have a way for its air to get out?

    The bye-laws give a bathroom or WC an opening of its own to the open air — a
    ventilator of a least area — rather than a share of its floor, and nothing checked
    it: stage ⑥ gave openings only to rooms that had to touch the outside, so all
    thirty-one bathrooms across fourteen plans were drawn sealed, every one of them
    beside an outside wall a ventilator could have gone in.

    Grouped by cause, one finding each, because the fixes differ. A bathroom with no
    outside wall needs the layout changed; one whose wall could not take a ventilator,
    or took too small a one, needs the drawing changed.
    """
    from app.rules import load_ruleset

    air = load_ruleset("refine_v1").data["ventilation"]
    kinds = {SpaceKind(kind) for kind in air["ventilator_kinds"]}
    least = air["ventilator_min_area_sq_m"]
    outside = {
        room for wall in floor.walls if wall.kind is WallKind.EXTERIOR for room in wall.rooms
    }

    walled_in: list[str] = []
    unvented: list[str] = []
    too_small: list[str] = []
    for room in program.rooms:
        if room.kind not in kinds or room.id not in floor.clear:
            continue
        area = floor.ventilation_area_sq_m(room.id)
        if area >= least - 1e-6:
            continue
        if area > 0:
            too_small.append(room.id)
        elif room.id in outside:
            unvented.append(room.id)
        else:
            walled_in.append(room.id)

    findings: list[Finding] = []
    for rooms, message in (
        (walled_in, "no outside wall, so no ventilator: its air has nowhere to go"),
        (unvented, "no ventilator in its outside wall"),
    ):
        if rooms:
            verb = "has" if len(rooms) == 1 else "have"
            findings.append(
                Finding(
                    check="ventilation", severity=Severity.WARNING,
                    message=f"{', '.join(rooms)} {verb} {message}", rooms=rooms,
                )
            )
    if too_small:
        verb = "is" if len(too_small) == 1 else "are"
        findings.append(
            Finding(
                check="ventilation", severity=Severity.WARNING,
                message=(
                    f"{', '.join(too_small)} {verb} ventilated through less than the "
                    f"{area_text(least)} a bathroom needs"
                ),
                rooms=too_small,
            )
        )
    return findings


def _airflow(program: Program, floor: RefinedFloor) -> tuple[list[str], list[str]]:
    """Which rooms get air from two sides, and which from one or none.

    A measurement, not a finding. A bedroom open to the air on one side is legal, and on
    any floor some rooms cannot be anything else — there are four corners — so a warning
    for each would bury the real defects and, through the judge, trade them for breezes.
    It goes on the `Report` for a person to read and for the judge to prefer once
    everything that matters more is equal.
    """
    from app.rules import load_ruleset

    kinds = {
        SpaceKind(kind) for kind in load_ruleset("refine_v1").data["ventilation"]["cross_kinds"]
    }
    cross: list[str] = []
    single: list[str] = []
    for room in program.rooms:
        if room.kind not in kinds or room.id not in floor.clear:
            continue
        (cross if len(floor.air_sides(room.id)) >= 2 else single).append(room.id)
    return cross, single


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


def judge(program: Program, envelope=None):
    """A sort key that ranks finished candidates by what this stage would say of them.

    Errors first, then whether the house can be entered and walked, then rooms below a
    minimum, then warnings, then Vastu zones missed, then rooms open to the air on one
    side only, then stage ⑤'s own penalty as the tiebreak — so a plan ⑦ refuses never
    beats one it passes, however much better its penalty, and neither a Vastu gain nor a
    breeze buys a warning.

    **Refusals are not all equal, and counting them as if they were picked the worst.**
    A 30x50 had two candidates, each with one error: one whose car bay did not touch the
    road, and one whose foyer had no street wall at all, so no front door could be drawn.
    Tied on errors and warnings, the lower penalty won — a house nobody can walk into,
    chosen over one whose car has to park on the street. A circulation error means the
    plan does not function as a house; every other error is about part of it. So it
    ranks worst among refusals.

    Handed to `solver.plan`, which takes a callable precisely so ⑤ does not have to
    import the stages downstream of it.
    """
    from app.refine import refine

    zoned = {room.id: room.sector for room in program.rooms if room.sector is not None}

    def key(layout: Layout) -> tuple:
        report = validate(layout, program, refine(layout, program, envelope))
        unusable = sum(
            1 for finding in report.by_check("circulation")
            if finding.severity is Severity.ERROR
        )
        # **Vastu after warnings, before the penalty.** Advisory, so a missed zone never
        # outranks a route through a bedroom or a room with no window — but inside the
        # penalty it was one 5-point term among dozens, and plans met 18 zones in 110.
        # Counted here, the same candidates met 24.
        missed_zones = sum(
            1 for placed in layout.rooms
            if placed.room_id in zoned and layout.sector_of(placed) is not zoned[placed.room_id]
        )
        # **Air after Vastu, before the penalty.** Advisory in the same way: a room open to
        # the air on one side only is legal and often unavoidable, so it never outranks a
        # warning — but among plans otherwise equal, the one whose living rooms and
        # bedrooms a breeze can cross should win. Measured over fourteen plans: ranked
        # before the zones it gave five more rooms two-sided air and cost five zones, and
        # on the 30x40 2BHK it bought one with a bedroom that has no window at all. After
        # them it costs nothing. Most of the gain is stage ⑥'s — no room of 78 had air
        # from two sides, and 30 do before any ranking; this tier adds one.
        return (
            report.errors,
            unusable,
            layout.unbuildable,
            len(report.findings) - report.errors,
            missed_zones,
            len(report.single_sided),
            layout.score,
        )

    return key


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


def _through_private_rooms(layout, program, floor, graph, start) -> list[Finding]:
    """Rooms you can only get to by walking through a bedroom, a bathroom, or the kitchen.

    **This used to check only the corridor, and ⑦ called five bad plans clean.** It was
    narrowed that way to stop it flagging en-suites, and the narrowing threw out
    everything else: a 30x50 you entered *through a bedroom*, a second bedroom reachable
    only through the master, a stilt house where the stairs led through the master
    bedroom to the living room. All of them passed, because none of them routed the
    corridor itself through a bedroom.

    The en-suite is handled as what it is instead: a bathroom reached through the
    bedroom stage ③ connected it to. Every other passage through a private room is
    reported, and so is a bedroom, stair or corridor that can only be reached through
    the kitchen — a kitchen is a room you may walk through to a utility behind it, not
    the way to the bedrooms.

    Walks every route, not the shortest one. A room is only reported if *no* door-route
    avoids the room in question — a plan with one bad route and one good one is fine.
    """
    kinds = {r.id: r.kind for r in program.rooms}
    through = {r.id for r in program.rooms if r.is_through_route}
    walk_in = {r.id for r in program.rooms if r.needs_door}
    en_suites = {
        frozenset({edge.a, edge.b})
        for edge in program.adjacencies
        if edge.relation is Relation.CONNECTED
        and {kinds.get(edge.a), kinds.get(edge.b)} & _BATHS
        and {kinds.get(edge.a), kinds.get(edge.b)} & _BEDROOMS
    }

    def walk(kitchen_passable: bool) -> set[str]:
        seen = {start}
        stack = [start]
        while stack:
            here = stack.pop()
            passable = here == start or (
                here in through
                and (kitchen_passable or kinds.get(here) is not SpaceKind.KITCHEN)
            )
            for neighbour in graph.get(here, ()):
                if neighbour in seen:
                    continue
                if passable or (
                    frozenset({here, neighbour}) in en_suites
                    and kinds.get(neighbour) in _BATHS
                ):
                    seen.add(neighbour)
                    stack.append(neighbour)
        return seen

    everything = {start}
    stack = [start]
    parents: dict[str, str] = {}
    while stack:
        here = stack.pop()
        for neighbour in graph.get(here, ()):
            if neighbour not in everything:
                everything.add(neighbour)
                parents[neighbour] = here
                stack.append(neighbour)

    placed = [r.room_id for r in layout.rooms if r.room_id in walk_in]
    honest = walk(kitchen_passable=True)
    no_kitchen = walk(kitchen_passable=False)
    findings: list[Finding] = []

    detoured = sorted(r for r in placed if r in everything and r not in honest)
    if detoured:
        via = sorted({
            node
            for room in detoured
            for node in _ancestors(room, parents, start)
            if node not in through
        })
        message = (
            f"{', '.join(detoured)} can only be reached by walking through a "
            f"bedroom or bathroom ({', '.join(via)})"
        )
        # **Behind a private room, the living rooms are an error, not a warning.** A
        # second bedroom reached through the master is a bad plan somebody could live
        # in. A hall, kitchen or dining room reached only that way means the way in does
        # not lead into the house: the 30x50 went front door, foyer, *bathroom*, and only
        # then corridor and hall. As one warning among others, the judge ranked that
        # above a plan refused for its car bay.
        common = [room for room in detoured if kinds.get(room) in _COMMON]
        if common:
            among = (
                "" if len(common) == len(detoured)
                else f", the {', '.join(common)} among them"
            )
            message += f"{among} — so every way in passes through one"
        findings.append(
            Finding(
                check="circulation",
                severity=Severity.ERROR if common else Severity.WARNING,
                message=message,
                rooms=detoured,
            )
        )

    via_kitchen = sorted(
        r for r in placed
        if r in honest and r not in no_kitchen
        # The hall too. Both plots the deeper search made legal were entered foyer →
        # kitchen → hall, and this said nothing, because the list stopped at bedrooms,
        # baths, stairs and corridors. A dining room behind the kitchen is an ordinary
        # arrangement; the room a visitor is received in is not.
        and kinds.get(r) in _BEDROOMS | _BATHS | {
            SpaceKind.STAIRCASE, SpaceKind.CORRIDOR, SpaceKind.HALL,
        }
    )
    if via_kitchen:
        findings.append(
            Finding(
                check="circulation",
                severity=Severity.WARNING,
                message=f"{', '.join(via_kitchen)} can only be reached through the kitchen",
                rooms=via_kitchen,
            )
        )
    return findings


# How much of the smaller of two stacked stairs they must share to be one flight. A
# flight arrives where the one below leaves; the storeys tile independently, so the
# rectangles are never identical to the centimetre, but at half a flight lands a metre
# off its landing. Measured here, not borrowed from `score` — a guard that shares its
# implementation with what it guards catches nothing.
_STAIR_LANDING = 0.75


def _stacked_share(upper, lower) -> float:
    """The fraction of the smaller rectangle that two stacked rooms have in common."""
    wide = min(upper.x_max_m, lower.x_max_m) - max(upper.x_min_m, lower.x_min_m)
    tall = min(upper.y_max_m, lower.y_max_m) - max(upper.y_min_m, lower.y_min_m)
    if wide <= 0 or tall <= 0:
        return 0.0
    smaller = min(
        (upper.x_max_m - upper.x_min_m) * (upper.y_max_m - upper.y_min_m),
        (lower.x_max_m - lower.x_min_m) * (lower.y_max_m - lower.y_min_m),
    )
    return wide * tall / smaller


def _ancestors(room: str, parents: dict[str, str], start: str) -> list[str]:
    """The rooms between the entrance and this one, on the route the walk found."""
    out: list[str] = []
    here = parents.get(room)
    while here is not None and here != start:
        out.append(here)
        here = parents.get(here)
    return out


def _access(
    layout: Layout, program: Program, floor: RefinedFloor | None = None
) -> list[Finding]:
    """Can a car actually reach the car bay?

    An error, not a warning: a bay no driveway reaches does not satisfy the parking
    requirement that put it in the programme. Stage ⑤ penalises this and the penalty
    can lose — a 30x50, a 30x30 and a 30x40 2BHK all came out with the bay against a
    side boundary, and ⑦ called two of them clean because nothing here asked.

    Recomputed from the layout rather than imported from `score`, for the reason
    `refine.breaches` is: a check that shares its implementation with what it checks
    cannot catch it going wrong.
    """
    kinds = {r.id: r.kind for r in program.rooms}
    specs = {r.id: r for r in program.rooms}
    outside = {r.id for r in program.rooms if r.outside_envelope}
    findings: list[Finding] = []
    for placed in layout.rooms:
        if kinds.get(placed.room_id) is not SpaceKind.CAR_PARKING:
            continue
        if placed.room_id in outside or not layout.road_edges:
            continue
        if not _on_a_road_boundary(placed, layout):
            roads = "/".join(edge.value for edge in layout.road_edges)
            findings.append(
                Finding(
                    check="access",
                    severity=Severity.ERROR,
                    message=(
                        f"{placed.room_id} does not touch the {roads} road, so no car can "
                        f"reach it"
                    ),
                    rooms=[placed.room_id],
                )
            )
        # Touching the road is not an opening onto it. Every bay used to be drawn with a
        # window, sealed, and passed; the drawing has to show a way in for a car.
        elif floor is not None:
            gates = [
                opening for opening in floor.openings
                if opening.kind is OpeningKind.VEHICLE and placed.room_id in opening.connects
            ]
            if not gates:
                findings.append(
                    Finding(
                        check="access",
                        severity=Severity.ERROR,
                        message=f"{placed.room_id} has no opening a car can drive through",
                        rooms=[placed.room_id],
                    )
                )
            elif _lies_along_the_road(placed, layout):
                # Swung into from the road, like a car porch, so the gate has to span the
                # long side. A 2.7 m gap in a 6 m wall, 3 m deep, cannot be turned into.
                from app.rules import load_ruleset

                needed = load_ruleset("refine_v1").data["vehicles"]["side_gate_min_m"]
                widest = max(gate.width_m for gate in gates)
                if widest < needed - TOLERANCE_M:
                    findings.append(
                        Finding(
                            check="access",
                            severity=Severity.ERROR,
                            message=(
                                f"{placed.room_id} lies along the road with a {length_text(widest)} "
                                f"gate: a car turning in needs about {length_text(needed)}"
                            ),
                            rooms=[placed.room_id],
                        )
                    )
    return findings


def _lies_along_the_road(placed, layout: Layout) -> bool:
    """Is the bay's long side the one on the street, on every road edge it touches?

    From the layout rectangle, not from `score`: a check that shares its implementation
    with what it checks catches nothing. A corner bay whose short side meets either road
    can be driven into nose first, so it does not lie along the road.
    """
    wide = placed.x_max_m - placed.x_min_m
    deep = placed.y_max_m - placed.y_min_m
    sides = []
    for edge in layout.road_edges:
        if edge is Facing.NORTH and abs(placed.y_max_m - layout.y_max_m) <= TOLERANCE_M:
            sides.append(wide > deep)
        elif edge is Facing.SOUTH and abs(placed.y_min_m - layout.y_min_m) <= TOLERANCE_M:
            sides.append(wide > deep)
        elif edge is Facing.EAST and abs(placed.x_max_m - layout.x_max_m) <= TOLERANCE_M:
            sides.append(deep > wide)
        elif edge is Facing.WEST and abs(placed.x_min_m - layout.x_min_m) <= TOLERANCE_M:
            sides.append(deep > wide)
    return bool(sides) and all(sides)


def _on_a_road_boundary(placed, layout: Layout) -> bool:
    reaches = {
        Facing.NORTH: abs(placed.y_max_m - layout.y_max_m) <= TOLERANCE_M,
        Facing.SOUTH: abs(placed.y_min_m - layout.y_min_m) <= TOLERANCE_M,
        Facing.EAST: abs(placed.x_max_m - layout.x_max_m) <= TOLERANCE_M,
        Facing.WEST: abs(placed.x_min_m - layout.x_min_m) <= TOLERANCE_M,
    }
    return any(reaches.get(edge, False) for edge in layout.road_edges)


def _sanitation(layout: Layout, program: Program) -> list[Finding]:
    """A floor people sleep on, with nowhere on it to wash.

    A warning rather than an error: the house still works, via the stairs, and on a
    small plot that may be the trade an owner chooses. It is reported because stage ③
    stacks bedrooms upstairs and bathrooms down, and a 30x40 stilt house came out with
    its top floor holding two bedrooms and no toilet while ⑦ said nothing.
    """
    kinds = {r.id: r.kind for r in program.rooms}
    here = {placed.room_id: kinds.get(placed.room_id) for placed in layout.rooms}
    findings = _kept_apart(layout, program)
    sleeping = sorted(rid for rid, kind in here.items() if kind in _BEDROOMS)
    if sleeping and not any(kind in _BATHS for kind in here.values()):
        findings.append(
            Finding(
                check="sanitation",
                severity=Severity.WARNING,
                message=(
                    f"floor {layout.floor} has bedrooms ({', '.join(sleeping)}) and no "
                    f"bathroom"
                ),
                rooms=sleeping,
            )
        )
    return findings


def _kept_apart(layout: Layout, program: Program) -> list[Finding]:
    """Rooms the programme keeps apart that the tiling put wall to wall.

    A toilet against the kitchen or against the pooja room is the placement Indian
    clients object to first. `score` penalises it and the penalty can lose: the model's
    30x40 3BHK put its pooja room against a bathroom and ⑦ said nothing. A warning, not
    an error — the house works — and one finding per pair, naming both rooms. Wall to
    wall means a shared length of wall; rooms meeting at a corner are not touching.
    """
    placed = {room.room_id: room for room in layout.rooms}
    findings: list[Finding] = []
    for edge in program.adjacencies:
        if edge.relation is not Relation.SEPARATED:
            continue
        a, b = placed.get(edge.a), placed.get(edge.b)
        if a is not None and b is not None and a.touches(b):
            findings.append(
                Finding(
                    check="sanitation",
                    severity=Severity.WARNING,
                    message=(
                        f"{edge.a} shares a wall with {edge.b}, which the programme "
                        f"keeps apart"
                    ),
                    rooms=sorted([edge.a, edge.b]),
                )
            )
    return findings


def _size(layout: Layout, program: Program, floor: RefinedFloor) -> list[Finding]:
    """A room more than twice the largest it should ever be.

    `score` penalises this and the penalty loses whenever exact tiling has surplus with
    nowhere better to go — a 50x80 shipped a 27.7 m² bathroom, and a stilt level a
    40 m² "staircase", both passing ⑦ because it only measured rooms that were too
    *small*. Measured on the clear floor, and only when the excess is also more than
    3 m², so a 5 m² pooja room against a 2.5 m² target is not a defect.

    The open ground under a stilt is excluded: absorbing surplus is its job.
    """
    specs = {r.id: r for r in program.rooms}
    findings: list[Finding] = []
    for placed in layout.rooms:
        spec = specs.get(placed.room_id)
        if spec is None or spec.kind is SpaceKind.STILT:
            continue
        ceiling = spec.max_target_sq_m or spec.target_area_sq_m
        area = floor.clear_area_sq_m(placed.room_id) or placed.area_sq_m
        if area > 2 * ceiling and area - ceiling > 3.0:
            findings.append(
                Finding(
                    check="size",
                    severity=Severity.WARNING,
                    message=(
                        f"{placed.room_id} is {area_text(area)}, {area / ceiling:.1f}x the "
                        f"{area_text(ceiling)} a {spec.kind.value.replace('_', ' ')} should "
                        f"ever be"
                    ),
                    rooms=[placed.room_id],
                )
            )
    return findings


def program_kind(program: Program, room_id: str):
    """The `SpaceKind` of a placed room, or None if the programme does not know it."""
    return next((r.kind for r in program.rooms if r.id == room_id), None)
