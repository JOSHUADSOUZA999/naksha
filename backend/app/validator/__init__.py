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

Reachability turned out to be the smaller question. A bedroom that is the only way into
another bedroom is reachable; `app.circulation` asks whether each room is *properly*
reached, grades what it finds, and scores the storey, and this stage reports it.
"""

from __future__ import annotations

from collections import Counter

from app.circulation import evaluate
from app.ir.enums import Facing, Grade, OpeningKind, Relation, Severity, SpaceKind, WallKind
from app.ir.layout import TOLERANCE_M, Layout
from app.ir.plan import Program
from app.ir.refined import RefinedFloor
from app.ir.units import area_text, feet_and_inches, length_text
from app.rules import load_ruleset
from app.ir.validation import Finding, Report

CHECKS = [
    "circulation", "access", "sanitation", "size", "furnish", "stair", "brief", "light",
    "ventilation", "legality",
]

# Where a person sleeps, and where they wash: a storey with the first and none of the
# second is reported by `_sanitation`.
_BEDROOMS = {
    SpaceKind.BEDROOM, SpaceKind.MASTER_BEDROOM, SpaceKind.GUEST_ROOM,
    SpaceKind.SERVANT_ROOM,
}
_BATHS = {SpaceKind.BATHROOM, SpaceKind.WC}


def validate(layout: Layout, program: Program, floor: RefinedFloor) -> Report:
    """Every check, against the drawn plan.

    Circulation is the circulation engine's: it tells a room that is reachable from one
    that is properly reached, grades each finding, and scores the storey. Its summary goes
    on the report beside the findings.
    """
    circulation = evaluate(layout, program, floor)
    findings: list[Finding] = list(circulation.findings)
    findings += _access(layout, program, floor)
    findings += _sanitation(layout, program)
    # One finding per defect: a foyer the circulation engine already calls oversized is its
    # finding, with the why and the fix, not the size check's as well.
    foyers = {room for f in circulation.findings if f.rule == "circulation.foyer" for room in f.rooms}
    findings += [f for f in _size(layout, program, floor) if not set(f.rooms) & foyers]
    findings += _furnish(layout, program, floor)
    findings += _stairs(layout, program, floor)
    findings += _brief(layout, program, floor)
    findings += _light(program, floor)
    findings += _ventilation(program, floor)
    findings += _legality(program, floor)
    cross, single = _airflow(program, floor)
    return Report(
        floor=layout.floor, findings=findings, checks_run=list(CHECKS),
        cross_ventilated=cross, single_sided=single, circulation=circulation.summary,
    )


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

    windows = load_ruleset("refine_v1").data["windows"]
    fraction = windows["area_fraction"]
    habitable = set(windows["habitable_kinds"])
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

        if glazed <= 0 and room.kind.value in habitable:
            # **A room people live in with no window at all is refused.** Glazed short of
            # the fraction is a drawing to adjust; with none, the room is one the bye-laws
            # do not allow. As a warning it weighed the same as an en-suite off the
            # corridor, and the judge chose a windowless bedroom on the 30x40 2BHK.
            findings.append(
                Finding(
                    check="light", severity=Severity.ERROR,
                    message=f"{room.id} has no window at all, and a room people live in must have one",
                    rooms=[room.id],
                    why=(
                        f"The bye-laws ask a habitable room for openings of at least "
                        f"{fraction:.0%} of its floor; with none, it cannot be a bedroom or a "
                        f"living room."
                    ),
                    fix=(
                        f"Place {room.id} against an outside wall at least "
                        f"{length_text(windows['min_wall_m'])} long, so a window fits."
                    ),
                )
            )
            continue
        if glazed <= 0:
            # Not "habitable room" here: a room that wants light without being one the
            # bye-laws call habitable, such as a kitchen, has a figure of its own. The
            # ratio message keeps the term because that is where it carries its legal
            # meaning.
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

    In order: whether this stage refuses the storey; rooms below a legal minimum, measured
    inside the walls and then on the tiling; rooms the circulation leaves without proper
    access, then every other critical finding; major findings, whichever check made them;
    Vastu zones missed; circulation quality; minor findings; rooms open to the air on one
    side only; and stage ⑤'s own penalty as the tiebreak.

    **A storey with a critical finding never beats one without.** A critical finding means
    the storey does not work as a house — a bedroom that is the only way into another, a
    front door into nothing — and no quantity of anything ranked below it compensates.

    **Critical circulation is counted in rooms, not findings.** A finding groups every room
    behind the same host, so counted as findings, one naming seven rooms reached only through
    a private room weighed less than a bathroom behind a bedroom and a bedroom with no window,
    and the 30x50 showed the seven. Ranking failed storeys by the access credit instead chose
    one with a windowless hall and six major findings. Counted in rooms, the 30x50 shows the
    plan with one bathroom behind a bedroom.

    **Circulation leads the refusals, because a storey that cannot be walked is not a
    house** where a car bay off the road is a house with a parking problem. A 30x50 once had
    two candidates with one error each, a car bay off the road and a foyer with no street
    wall, and the lower penalty chose the house nobody could walk into.

    **What the brief asked for comes before every other major.** "My parents need a
    bedroom near the entrance" is the one thing the owner said; ranked as one major among
    many, the judge chose a 40x60 with three circulation majors and the parents at the
    back over one with four circulation majors and the parents by the door. A storey that
    ignores its brief is still a house, so it ranks after refusals, not with them.

    **Among the other major findings, a major is a major.** Ranked ahead of the others, circulation
    majors bought one fewer each with a bedroom that had no window at all on the 30x40
    2BHK and a hall with none on the 50x80; counted together, the 14 benchmark plans kept
    both windows, met two more Vastu zones, and failed and found exactly as much.

    **Vastu after the majors, circulation quality after Vastu.** Vastu is advisory, so no
    zone buys a major finding. Quality is a 0-100 number that almost never ties, so
    anything ranked after it is a tiebreak; ahead of the zones it would decide every choice
    the findings leave open and the zones would count for nothing. Minor findings already
    cost quality, and a room open to the air on one side, legal and often unavoidable,
    follows them.

    **The first element is a contract with stage ⑤.** It is truthy when this stage refuses
    the storey, and every element adds up across storeys, which is how `solver.plan` tells
    a storey that strands the one above it from one that does not.

    Handed to `solver.plan`, which takes a callable precisely so ⑤ does not have to
    import the stages downstream of it.
    """
    from app.refine import refine

    zoned = {room.id: room.sector for room in program.rooms if room.sector is not None}

    def key(layout: Layout) -> tuple:
        report = validate(layout, program, refine(layout, program, envelope))
        graded = _graded(report)
        critical = graded["circulation", Grade.CRITICAL] + graded["other", Grade.CRITICAL]
        missed_zones = sum(
            1 for placed in layout.rooms
            if placed.room_id in zoned and layout.sector_of(placed) is not zoned[placed.room_id]
        )
        quality = report.circulation.quality if report.circulation is not None else 100.0
        return (
            int(bool(critical or layout.unbuildable)),
            len(report.by_check("legality")),
            layout.unbuildable,
            _without_access(report, graded["circulation", Grade.CRITICAL]),
            graded["other", Grade.CRITICAL],
            len(report.by_check("brief")),
            graded["circulation", Grade.MAJOR] + graded["other", Grade.MAJOR],
            missed_zones,
            -quality,
            graded["circulation", Grade.MINOR] + graded["other", Grade.MINOR],
            len(report.single_sided),
            layout.score,
        )

    return key


def _without_access(report: Report, critical_findings: int) -> int:
    """Rooms the circulation leaves without proper access: each room whose best route is
    critical, or every room on a storey nobody can enter. Never fewer than the critical
    circulation findings, for a defect that names no room of its own."""
    summary = report.circulation
    if summary is None:
        return critical_findings
    if summary.critical and not summary.access:
        rooms = sum(1 for node in summary.graph.nodes if node.walk_in)
    else:
        rooms = sum(1 for row in summary.access if row.grade is Grade.CRITICAL)
    return max(rooms, critical_findings)


def _graded(report: Report) -> Counter:
    """Findings counted by grade, circulation apart from every other check."""
    return Counter(
        ("circulation" if finding.check == "circulation" else "other", finding.grade)
        for finding in report.findings
    )


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


def _furnish(layout: Layout, program: Program, floor: RefinedFloor) -> list[Finding]:
    """A legal room that cannot hold what it is for.

    Every room here passed its area, width and aspect limits, and the JP Nagar 40x60 still
    drew a 6'3" x 15'6" kitchen, a 7'3" x 15'6" dining room and an 8'1" wide hall. The
    bye-laws do not say a bedroom must hold a bed, so this is never refused: a foot or
    more short of the nearest arrangement is major — the furniture does not go in — and
    less is minor, a clearance squeezed. Measured inside the plaster, like the minimums.
    """
    rules = load_ruleset("furnish_v1").data
    major_m = rules["grading"]["major_shortfall_m"]
    tolerance_m = rules["grading"]["tolerance_m"]
    findings: list[Finding] = []
    for spec in program.rooms:
        rect = floor.clear.get(spec.id)
        if rect is None or not spec.usable_sizes_m:
            continue
        width, depth = rect[2] - rect[0], rect[3] - rect[1]
        short_m = spec.furnishing_shortfall_m(width, depth)
        if short_m <= tolerance_m:
            continue
        need = min(
            spec.usable_sizes_m,
            key=lambda size: max(0.0, size[0] - min(width, depth), size[1] - max(width, depth)),
        )
        holds = (
            rules["rooms"][spec.kind.value]["holds"] if spec.kind.value in rules["rooms"]
            # A stair's comfortable size is its flights at an easy rise, from stairs_v1.
            else "a stair at a 175 mm rise an elderly parent climbs without resting"
        )
        major = short_m > major_m
        findings.append(
            Finding(
                check="furnish",
                severity=Severity.WARNING,
                grade=Grade.MAJOR if major else Grade.MINOR,
                rule="furnish.fit",
                message=(
                    f"{spec.id} is {feet_and_inches(min(width, depth))} x "
                    f"{feet_and_inches(max(width, depth))} inside its walls; "
                    f"{holds} needs {feet_and_inches(need[0])} x {feet_and_inches(need[1])}"
                ),
                rooms=[spec.id],
                why=(
                    f"Legal, and {length_text(short_m)} short of the furniture it exists for"
                    + (" — it does not go in." if major else " — it goes in with too little room to use it.")
                ),
                fix=(
                    f"Widen the {spec.kind.value.replace('_', ' ')} at the expense of a room with "
                    "space to spare, or exchange it with a better-proportioned room."
                ),
            )
        )
    return findings


def _stairs(layout: Layout, program: Program, floor: RefinedFloor) -> list[Finding]:
    """A staircase whose flights cannot be drawn clear of its doors.

    Stage ⑥ draws a stair's flights wherever no door opens onto the steps; a stair it
    could not draw is one every arrangement of which puts a doorway on a tread — you
    step out of a door onto the eleventh step. Major rather than critical: the stair
    exists and is legal in size, and moving a door fixes it. A stair below its legal
    shape is legality's finding, not this one.
    """
    from app.refine import breaches

    illegal = {message.split()[0] for message in breaches(floor, program)}
    drawn = {f.room_id for f in floor.fixtures if f.kind.value == "flight"}
    findings = []
    for spec in program.rooms:
        if spec.kind is not SpaceKind.STAIRCASE or spec.id not in floor.clear:
            continue
        if spec.id in drawn or spec.id in illegal:
            continue
        findings.append(
            Finding(
                check="stair",
                severity=Severity.WARNING,
                grade=Grade.MAJOR,
                rule="stair.door_onto_steps",
                message=f"a door into {spec.id} opens onto its steps: no flight fits clear of it",
                rooms=[spec.id],
                why="A door must open onto a landing. Stepping through a doorway onto a tread is how people fall.",
                fix="Move the door to the foot of the flight or to the landing, or turn the stair.",
            )
        )
    return findings


def _brief(layout: Layout, program: Program, floor: RefinedFloor) -> list[Finding]:
    """What the brief asked for that the drawing does not give.

    A brief's "my parents are elderly and need a bedroom near the entrance" reached stage
    ③ as a floor assignment and a sentence in `why`, and nothing downstream acted on the
    sentence: JP Nagar drew the parents' bedroom at the back of the house. Stage ③ now
    writes it as a `NEAR` edge, and this walks it through the doors, the way the
    circulation engine walks every journey: near is `circulation_v1.near.max_walk_m`.

    Major: a house that ignores the one thing its owner asked for is not a small miss.
    """
    from app.circulation.graph import build
    from app.circulation.routes import best_route
    from app.circulation.semantics import data as circulation_rules

    placed = {room.room_id for room in layout.rooms}
    near = [
        edge for edge in program.adjacencies
        if edge.relation is Relation.NEAR and edge.a in placed and edge.b in placed
    ]
    if not near:
        return []
    limit = circulation_rules()["near"]["max_walk_m"]
    graph = build(layout, program, floor)
    findings = []
    for edge in near:
        route = best_route(graph, edge.b, edge.a)
        # A proper walk only: through a pooja room or another bedroom is a short way
        # nobody should take. Through the hall is how a house is walked (minor).
        proper = route is not None and route.worst in (None, Grade.MINOR)
        if proper and route.distance_m <= limit:
            continue
        if route is None:
            walk = "no way between them"
        elif not proper:
            walk = f"the only short way crosses {', '.join(route.hosts)}"
        else:
            walk = f"a {route.distance_m:.1f} m walk"
        findings.append(
            Finding(
                check="brief",
                severity=Severity.WARNING,
                grade=Grade.MAJOR,
                rule="brief.near",
                message=(
                    f"{edge.a} is not near {edge.b}, which the brief asked for: {walk}, "
                    f"against {limit:.0f} m"
                ),
                rooms=sorted([edge.a, edge.b]),
                why="The brief asked for these rooms a few steps apart — for a parent who "
                    "should not cross the house, or a room a visitor should reach at once.",
                fix=f"Open {edge.a} off the space {edge.b} opens onto, or move it beside {edge.b}.",
            )
        )
    return findings
