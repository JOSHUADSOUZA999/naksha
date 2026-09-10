"""Stage ⑦ VALIDATE — the checks that only make sense on a finished plan.

The stage exists because of one measurement: every plan the pipeline called clean was
a house you could not walk through. On a 30x50 you came in the front door and reached
one room out of eleven, and nothing anywhere reported it — stage ⑤ scores adjacency,
and two rooms sharing a wall is not the same claim as being able to get between them.
"""

from __future__ import annotations

import pytest

from app.envelope import build_envelope
from app.ir.enums import OpeningKind, Relation, Severity, SpaceKind
from app.llm import fallback
from app.program import expand
from app.refine import refine
from app.solver import solve
from app.validator import CHECKS, validate

BRIEFS = {
    "30x40": "30x40 east facing site in Whitefield, Bengaluru, 3BHK with pooja room",
    "30x50": "30x50 3bhk in Bengaluru",
    "40x60": "40x60 3bhk in Bengaluru with pooja room",
    "50x80": "50x80 4bhk in Bengaluru with study",
}


@pytest.fixture(scope="module")
def plans():
    out = {}
    for name, text in BRIEFS.items():
        brief = fallback.parse(text)
        envelope = build_envelope(brief, allow_unverified=True)
        program = expand(brief, envelope)
        layout = solve(program, envelope, seed=7)[0]
        floor = refine(layout, program)
        out[name] = (layout, program, floor, validate(layout, program, floor))
    return out


@pytest.mark.parametrize("name", list(BRIEFS))
class TestYouCanWalkThroughTheHouse:
    def test_every_room_is_reachable_from_the_front_door(self, plans, name):
        """The check the stage was built for.

        Reachability over *doors*, not adjacency: a shared wall is stage ⑤'s claim and
        only a door in it lets anyone through. This held on none of the four briefs
        when it was first measured.

        Errors only. A warning here is the narrower defect — circulation running
        through a bedroom — which ⑥ accepts as a last resort when the alternative is a
        room with no way in, and which stage ⑤ separately scores on the tiling.
        """
        layout, _, _, report = plans[name]
        errors = [
            f for f in report.by_check("circulation") if f.severity is Severity.ERROR
        ]
        assert errors == [], errors[0].message if errors else ""

    def test_a_finding_names_the_rooms_it_is_about(self, plans, name):
        """A defect a user cannot locate on the drawing is one they cannot fix."""
        _, program, _, report = plans[name]
        known = {room.id for room in program.rooms}
        for finding in report.findings:
            assert set(finding.rooms) <= known, finding.message

    def test_the_report_says_what_it_looked_at(self, plans, name):
        """No findings has to mean "checked and clean", not "nothing ran"."""
        _, _, _, report = plans[name]
        assert report.checks_run == CHECKS


class TestCirculationIsMeasuredNotAssumed:
    def test_removing_a_door_strands_the_rooms_behind_it(self, plans):
        """The check has to fail when the plan is broken, or it is decoration.

        Built by deleting a door rather than by finding a bad brief: a test that waits
        for the pipeline to produce a defect stops testing anything the day it stops
        producing one.
        """
        layout, program, floor, report = plans["40x60"]
        assert report.ok or not report.by_check("circulation")

        doors = [o for o in floor.openings if o.kind is OpeningKind.DOOR]
        assert doors
        for index in range(len(doors)):
            keep = [o for o in floor.openings if o is not doors[index]]
            severed = floor.model_copy(update={"openings": keep})
            findings = validate(layout, program, severed).by_check("circulation")
            if findings:
                assert findings[0].severity is Severity.ERROR
                assert findings[0].rooms
                return
        pytest.fail("no single door was load-bearing — the plan cannot be severed")

    def test_a_plan_with_no_front_door_reports_that_first(self, plans):
        """Not "eleven rooms unreachable", which describes the symptom. The house has
        no way in, and that is one defect."""
        layout, program, floor, _ = plans["30x50"]
        sealed = floor.model_copy(
            update={
                "openings": [
                    o for o in floor.openings if o.kind is not OpeningKind.ENTRANCE
                ]
            }
        )
        findings = validate(layout, program, sealed).by_check("circulation")
        assert len(findings) == 1
        assert "no front door" in findings[0].message

    def test_one_finding_for_a_disconnected_plan_not_one_per_room(self, plans):
        """Twelve "you cannot reach the kitchen" lines make one large problem look
        like twelve small ones."""
        layout, program, floor, _ = plans["50x80"]
        stripped = floor.model_copy(
            update={
                "openings": [
                    o for o in floor.openings if o.kind is not OpeningKind.DOOR
                ]
            }
        )
        findings = [
            f for f in validate(layout, program, stripped).by_check("circulation")
            if f.severity is Severity.ERROR
        ]
        assert len(findings) == 1
        assert len(findings[0].rooms) > 1

    def test_a_car_porch_is_not_stranded_by_having_no_internal_door(self, plans):
        """It is entered from the street. Demanding a door from the house would push a
        driveway through the hall."""
        _, program, _, report = plans["50x80"]
        parking = [
            room.id for room in program.rooms if room.kind is SpaceKind.CAR_PARKING
        ]
        assert parking
        for finding in report.by_check("circulation"):
            assert not set(finding.rooms) & set(parking)


class TestTheOtherChecks:
    def test_a_room_below_a_minimum_is_an_error(self, plans):
        """The 30x40 is 98% packed and stage ⑤ already refuses it. Stage ⑦ asks again,
        through `refine.breaches`, so a plan reaching a person has been checked by
        something that did not also produce it."""
        _, _, _, report = plans["30x40"]
        legality = report.by_check("legality")
        assert legality
        assert all(f.severity is Severity.ERROR for f in legality)
        assert not report.ok

    def test_a_legal_plan_reports_no_errors(self, plans):
        for name in ("30x50", "40x60", "50x80"):
            _, _, _, report = plans[name]
            assert report.ok, [f.message for f in report.findings if f.severity is Severity.ERROR]

    def test_a_window_finding_is_a_warning_not_an_error(self, plans):
        """A bedroom with no window is worth seeing and is not a reason to refuse the
        plan — the wall may simply be too short to hold an opening."""
        for _, _, _, report in plans.values():
            for finding in report.by_check("light"):
                assert finding.severity is Severity.WARNING

    def test_a_report_cannot_carry_findings_from_a_check_that_did_not_run(self):
        """Referential integrity, per the `ir/` rule — a check that silently stopped
        running would otherwise read as a pass."""
        from app.ir.validation import Finding, Report

        with pytest.raises(ValueError, match="not listed as run"):
            Report(
                findings=[
                    Finding(check="vastu", severity=Severity.WARNING, message="x")
                ],
                checks_run=["circulation"],
            )


class TestDoorsAddedForCirculationRespectTheProgramme:
    def test_no_door_crosses_a_separated_edge(self, plans):
        """A toilet opening into a kitchen would satisfy reachability by making the
        plan worse."""
        for _, program, floor, _ in plans.values():
            forbidden = {
                frozenset({e.a, e.b})
                for e in program.adjacencies
                if e.relation is Relation.SEPARATED
            }
            for opening in floor.openings:
                if opening.kind is OpeningKind.DOOR:
                    assert frozenset(opening.connects) not in forbidden

    def test_connecting_doors_are_added_only_where_needed(self, plans):
        """Enough to walk the house, not a door in every wall. A plan where every
        neighbouring pair is joined has no privacy left in it."""
        for name, (_, _, floor, _) in plans.items():
            doors = sum(1 for o in floor.openings if o.kind is OpeningKind.DOOR)
            partitions = sum(
                1 for w in floor.walls if len(w.rooms) == 2
            )
            assert doors < partitions, name


class TestOneJudgmentInOnePlace:
    """Which rooms need a door, and which need daylight, are rule data.

    Both were hardcoded sets in code, and one of them existed *twice* — an identical
    list of kinds in `refine` and in `validator`. Two copies of the same judgment drift,
    and this pair would have drifted silently: stage ⑥ guaranteeing a door to one set
    while stage ⑦ checked another reads as a clean plan.
    """

    def test_which_rooms_need_a_door_comes_from_the_ruleset(self):
        from app.rules import load_ruleset

        spaces = load_ruleset("spaces_v1").data["spaces"]
        assert spaces["bedroom"]["walk_in"] is True
        assert spaces["car_parking"]["walk_in"] is False, "entered from the street"

    def test_the_two_stages_read_the_same_flag(self, plans):
        """Not the same *value* — the same field. Stage ⑥ connects `needs_door` rooms
        and stage ⑦ strands them, so a room can never be one stage's business and not
        the other's."""
        _, program, floor, report = plans["50x80"]
        walk_in = {room.id for room in program.rooms if room.needs_door}
        assert walk_in, "the fixture must have rooms to walk into"

        stranded = {r for f in report.by_check("circulation") for r in f.rooms}
        assert stranded <= walk_in

    def test_daylight_follows_the_ruleset_not_a_second_opinion(self, plans):
        """The validator carried its own list of habitable rooms and it disagreed with
        `spaces_v1`: it wanted a window in every dining room, the ruleset does not
        require one. `score` and `refine` were already reading the ruleset, so the
        outlier was the check."""
        _, program, _, report = plans["30x50"]
        needs_light = {r.id for r in program.rooms if r.needs_exterior_wall}
        flagged = {f.rooms[0] for f in report.by_check("light")}
        assert flagged <= needs_light

        dining = [r for r in program.rooms if r.kind is SpaceKind.DINING]
        assert dining and not dining[0].needs_exterior_wall
        assert dining[0].id not in flagged

    def test_a_room_that_needs_light_and_has_none_is_still_flagged(self, plans):
        """The rule did not become lax when the ruleset took over the judgment.

        Built by stripping the windows rather than by finding a brief with a landlocked
        hall: the 40x60 used to have one and no longer does, and a test that waits for
        the pipeline to produce a defect stops testing anything the day it stops
        producing one.
        """
        layout, program, floor, _ = plans["40x60"]
        hall = next(r for r in program.rooms if r.kind is SpaceKind.HALL)
        assert hall.needs_exterior_wall

        blind = floor.model_copy(
            update={
                "openings": [
                    o for o in floor.openings
                    if not (o.kind is OpeningKind.WINDOW and hall.id in o.connects)
                ]
            }
        )
        findings = validate(layout, program, blind).by_check("light")
        assert any(hall.id in f.rooms for f in findings)


class TestGlazingIsMeasuredNotCountedPresence:
    """"Has a window" passed a room with one token opening nowhere near the tenth of
    floor area the code requires — and reported nothing, which is worse than reporting
    a number that is too small."""

    def test_an_under_glazed_room_is_reported_with_its_ratio(self, plans):
        layout, program, floor, _ = plans["50x80"]
        glazed = next(
            o for o in floor.openings if o.kind is OpeningKind.WINDOW
        )
        pinched = floor.model_copy(
            update={
                "openings": [
                    o.model_copy(update={"width_m": 0.3}) if o is glazed else o
                    for o in floor.openings
                ]
            }
        )
        findings = validate(layout, program, pinched).by_check("light")
        assert any("below the" in f.message and "%" in f.message for f in findings)

    def test_a_fully_glazed_room_is_not_reported(self, plans):
        """Stage ⑥ sizes to the fraction, so ⑦ must agree with it on every room that
        got a window at all — the two read the same figure from the same ruleset."""
        from app.rules import load_ruleset

        fraction = load_ruleset("refine_v1").data["windows"]["area_fraction"]
        for name in ("30x50", "50x80"):
            _, program, floor, report = plans[name]
            flagged = {f.rooms[0] for f in report.by_check("light")}
            for room in program.rooms:
                if not room.needs_exterior_wall or room.id not in floor.clear:
                    continue
                area = floor.clear_area_sq_m(room.id)
                if floor.window_area_sq_m(room.id) >= area * fraction - 1e-6:
                    assert room.id not in flagged, f"{name}/{room.id}"

    def test_a_room_with_no_window_says_so_plainly(self, plans):
        """Not "glazed to 0%", which reads as a shortfall to make up. Having no window
        and having too little window are different problems and read differently."""
        layout, program, floor, _ = plans["50x80"]
        lit = next(
            r for r in program.rooms
            if r.needs_exterior_wall and floor.window_area_sq_m(r.id) > 0
        )
        blind = floor.model_copy(
            update={
                "openings": [
                    o for o in floor.openings
                    if not (o.kind is OpeningKind.WINDOW and lit.id in o.connects)
                ]
            }
        )
        findings = [
            f for f in validate(layout, program, blind).by_check("light")
            if lit.id in f.rooms
        ]
        assert findings
        assert "no window" in findings[0].message
        assert "%" not in findings[0].message


class TestCirculationDoesNotRunThroughBedrooms:
    """CLAUDE.md: "bedrooms open off the corridor, never the hall — that is what
    circulation is for, and why privacy survives the tiling."

    Stage ⑥ broke it the moment it started adding doors for connectivity. A 40x60 came
    out with the corridor reachable only through a bedroom, so the route to the master
    bedroom's bathroom ran hall → dining → bed2 → corridor → bed1 → bath1. Every room
    reachable, every check passing, and a plan nobody would live in.
    """

    def test_an_en_suite_is_not_a_defect(self, plans):
        """The narrowing that makes the rule usable.

        A bathroom off its bedroom is reached *through* that bedroom, and that is what
        an en-suite is. A first version flagged every one of them — the rule is about
        the circulation spine, not about private rooms having neighbours.
        """
        from app.ir.enums import SpaceKind

        for _, program, _, report in plans.values():
            flagged = {r for f in report.by_check("circulation") for r in f.rooms}
            kinds = {r.id: r.kind for r in program.rooms}
            for room in flagged:
                assert kinds[room] not in (SpaceKind.BATHROOM, SpaceKind.WC), room

    def test_the_finding_names_circulation_spaces_only(self, plans):
        for _, program, _, report in plans.values():
            through = {r.id for r in program.rooms if r.is_through_route}
            for finding in report.by_check("circulation"):
                if finding.severity is Severity.WARNING:
                    assert set(finding.rooms) <= through, finding.message

    def test_a_corridor_reached_through_a_bedroom_is_reported(self, plans):
        """Built by severing the spine rather than by finding a bad brief."""
        layout, program, floor, _ = plans["50x80"]
        through = {r.id for r in program.rooms if r.is_through_route}
        spine_doors = [
            o for o in floor.openings
            if o.kind is OpeningKind.DOOR and set(o.connects) <= through
        ]
        assert spine_doors, "the fixture must join two circulation spaces"

        severed = floor.model_copy(
            update={"openings": [o for o in floor.openings if o not in spine_doors]}
        )
        findings = validate(layout, program, severed).by_check("circulation")
        assert any("through a bedroom" in f.message for f in findings)

    def test_stage_five_scores_the_same_property_on_the_tiling(self, plans):
        """Before any door exists. ⑤ has to prefer tilings ⑥ can wire honestly — the
        alternative is ⑥ discovering there is no honest wiring left."""
        from app.solver.score import _unwalkable

        layout, program, _, _ = plans["50x80"]
        assert _unwalkable(layout, program) == []

    def test_it_is_rare_enough_to_need_its_own_shortlist(self, plans):
        """Measured: 35 tilings in 2000 on a 30x50 admit a privacy-respecting route,
        and 3 in 2000 on a 30x40. That is why `solve` interleaves a walkable group into
        the tuning shortlist rather than trusting the ranking to surface them."""
        from app.solver import _interleave

        merged = _interleave([("a", 1), ("a", 2)], [("b", 2), ("b", 3)], [("c", 4)])
        assert [row[1] for row in merged] == [1, 2, 4, 3]
