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
from app.ir.layout import TOLERANCE_M
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

        Errors only, and only this error. Circulation running through a bedroom is the
        narrower defect — ⑥ accepts it as a last resort when the alternative is a room
        with no way in — and it has tests of its own. Once the living rooms are behind
        it that defect is an error too, but every room on such a route can still be
        *reached*, and reaching is the claim this test makes.
        """
        layout, program, floor, report = plans[name]
        errors = [
            f for f in report.by_check("circulation")
            if f.severity is Severity.ERROR and "by walking through" not in f.message
        ]
        if not any(o.kind is OpeningKind.ENTRANCE for o in floor.openings):
            # Stage ⑤ put the foyer off the street, so there is no front door to walk
            # from. That is a defect ⑦ must report — and only that one.
            assert errors and all("no front door" in f.message for f in errors)
            return
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
        """The 30x50 used to be in this list, and the list was wrong: its car bay does
        not touch the road, so no car can reach it. That was true all along — ⑦ simply
        had no check for it until the drawings were looked at."""
        for name in ("40x60", "50x80"):
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

    def test_a_room_is_reported_once_not_as_both_unreachable_and_detoured(self, plans):
        """Severing every circulation door strands rooms outright.

        This test used to expect those rooms to *also* be reported as "reached through
        a bedroom", and the old corridor-only rule obliged. That was double-counting: a
        room with no route at all is one defect — unreachable, an error — and calling
        it a detour as well makes one problem look like two. Rooms that still have a
        route, and only a bad one, are the detours.
        """
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
        assert findings, "severing the circulation must be reported"

        stranded = {
            r for f in findings if f.severity is Severity.ERROR for r in f.rooms
        }
        detoured = {
            r for f in findings if "through a bedroom" in f.message for r in f.rooms
        }
        assert not stranded & detoured

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


class TestEveryDefectTheDrawingsShowed:
    """Each of these was found by looking at the drawings, on plots ⑦ called clean.

    Every test breaks a plan on purpose — none waits for a brief that happens to be bad,
    because that test goes vacuous the day the pipeline stops producing the defect.
    """

    @staticmethod
    def _row(*rooms, through=()):
        """A strip of rooms side by side, 2 m each, with a programme to match."""
        from app.ir.layout import Layout, PlacedRoom
        from app.ir.plan import Program, RoomSpec

        placed = [
            PlacedRoom(room_id=rid, x_min_m=2.0 * i, y_min_m=0, x_max_m=2.0 * (i + 1), y_max_m=3)
            for i, (rid, _) in enumerate(rooms)
        ]
        layout = Layout(rooms=placed, x_min_m=0, y_min_m=0, x_max_m=2.0 * len(rooms), y_max_m=3)
        specs = [
            RoomSpec(
                id=rid, kind=kind, min_area_sq_m=1.0, target_area_sq_m=2.0,
                min_width_m=0.5, max_aspect=10.0, is_through_route=rid in through,
            )
            for rid, kind in rooms
        ]
        return layout, specs

    def test_a_bedroom_reached_only_through_another_bedroom_is_reported(self):
        """The 30x40 2BHK: bed2 was only reachable through the master bedroom."""
        from app.ir.plan import Program
        from app.validator import _through_private_rooms

        layout, specs = self._row(
            ("foyer", SpaceKind.FOYER), ("bed1", SpaceKind.MASTER_BEDROOM),
            ("bed2", SpaceKind.BEDROOM), through={"foyer"},
        )
        graph = {"foyer": {"bed1"}, "bed1": {"foyer", "bed2"}, "bed2": {"bed1"}}
        findings = _through_private_rooms(layout, Program(rooms=specs), None, graph, "foyer")
        assert any("bed2" in f.rooms and "through a bedroom" in f.message for f in findings)
        # A bad plan somebody could still live in: reported, not refused.
        assert all(f.severity is Severity.WARNING for f in findings)

    def test_an_en_suite_is_not_a_defect(self):
        """A bathroom reached through the bedroom stage ③ connected it to is what an
        en-suite is. The first version of this rule flagged every one."""
        from app.ir.enums import Relation
        from app.ir.plan import AdjacencySpec, Program
        from app.validator import _through_private_rooms

        layout, specs = self._row(
            ("foyer", SpaceKind.FOYER), ("bed1", SpaceKind.MASTER_BEDROOM),
            ("bath1", SpaceKind.BATHROOM), through={"foyer"},
        )
        program = Program(
            rooms=specs,
            adjacencies=[AdjacencySpec(a="bed1", b="bath1", relation=Relation.CONNECTED)],
        )
        graph = {"foyer": {"bed1"}, "bed1": {"foyer", "bath1"}, "bath1": {"bed1"}}
        assert _through_private_rooms(layout, program, None, graph, "foyer") == []

    def test_a_bathroom_behind_someone_else_s_bedroom_is_not_an_en_suite(self):
        """The exception is the edge stage ③ drew, not any bathroom next to a bed."""
        from app.ir.plan import Program
        from app.validator import _through_private_rooms

        layout, specs = self._row(
            ("foyer", SpaceKind.FOYER), ("bed1", SpaceKind.BEDROOM),
            ("bath2", SpaceKind.BATHROOM), through={"foyer"},
        )
        graph = {"foyer": {"bed1"}, "bed1": {"foyer", "bath2"}, "bath2": {"bed1"}}
        findings = _through_private_rooms(layout, Program(rooms=specs), None, graph, "foyer")
        assert any("bath2" in f.rooms for f in findings)

    def test_a_house_entered_through_a_bedroom_is_reported(self):
        """The 30x50: the front door opened into a bedroom and every room lay beyond it."""
        from app.ir.plan import Program
        from app.validator import _through_private_rooms

        layout, specs = self._row(
            ("foyer", SpaceKind.FOYER), ("bed3", SpaceKind.BEDROOM),
            ("hall", SpaceKind.HALL), ("kitchen", SpaceKind.KITCHEN),
            through={"foyer", "hall", "kitchen"},
        )
        graph = {
            "foyer": {"bed3"}, "bed3": {"foyer", "hall"},
            "hall": {"bed3", "kitchen"}, "kitchen": {"hall"},
        }
        findings = _through_private_rooms(layout, Program(rooms=specs), None, graph, "foyer")
        flagged = {r for f in findings for r in f.rooms}
        assert {"hall", "kitchen"} <= flagged
        # With the hall and kitchen behind it, the front door does not lead into the
        # house at all — refused, so the judge can never prefer it to a plan with a
        # smaller defect.
        assert any(f.severity is Severity.ERROR for f in findings)

    def test_a_ground_floor_with_no_front_door_is_refused_even_with_a_stair(self):
        """The 25x40 once listed as the one clean plot had no entrance. The walk fell back
        to the staircase — a rule written for upper storeys — found every room, and
        reported nothing; the judge then preferred that house to every one you could
        walk into."""
        from types import SimpleNamespace

        from app.ir.plan import Program
        from app.validator import _circulation

        layout, specs = self._row(
            ("stair", SpaceKind.STAIRCASE), ("hall", SpaceKind.HALL), through={"hall"},
        )
        sealed = SimpleNamespace(openings=[])
        ground = layout.model_copy(update={"floor": 1})
        findings = _circulation(ground, Program(rooms=specs), sealed)
        assert [f.severity for f in findings] == [Severity.ERROR]
        assert "no front door" in findings[0].message

    def test_an_upper_floor_is_still_entered_from_its_stair(self):
        """The other half: upstairs there is no front door to demand."""
        from types import SimpleNamespace

        from app.ir.enums import OpeningKind
        from app.ir.plan import Program
        from app.validator import _circulation

        layout, specs = self._row(
            ("stair", SpaceKind.STAIRCASE), ("hall", SpaceKind.HALL), through={"hall"},
        )
        door = SimpleNamespace(kind=OpeningKind.DOOR, connects=("stair", "hall"))
        upper = layout.model_copy(update={"floor": 2})
        findings = _circulation(upper, Program(rooms=specs), SimpleNamespace(openings=[door]))
        assert not any(f.severity is Severity.ERROR for f in findings)

    def test_an_upper_floor_whose_stair_misses_the_one_below_has_no_way_up(self):
        """The model's 30x40 3BHK: the ground-floor stair in the north-east, the
        first-floor stair in the north-west, zero overlap — and both floors came back
        clean, because nothing here compared a storey with the one below it."""
        from types import SimpleNamespace

        from app.ir.enums import OpeningKind
        from app.ir.layout import PlacedRoom
        from app.ir.plan import Program
        from app.validator import _circulation

        layout, specs = self._row(
            ("stair", SpaceKind.STAIRCASE), ("hall", SpaceKind.HALL), through={"hall"},
        )
        floor = SimpleNamespace(
            openings=[SimpleNamespace(kind=OpeningKind.DOOR, connects=("stair", "hall"))]
        )

        def stair_below_at(x_min):
            return {
                SpaceKind.STAIRCASE: PlacedRoom(
                    room_id="stair1", x_min_m=x_min, y_min_m=0, x_max_m=x_min + 2.0, y_max_m=3
                )
            }

        missed = layout.model_copy(update={"floor": 2, "shafts": stair_below_at(2.0)})
        findings = _circulation(missed, Program(rooms=specs), floor)
        assert [f.severity for f in findings] == [Severity.ERROR]
        assert "no way up" in findings[0].message

        # The same storey with its stair over the one below is an ordinary first floor.
        landed = layout.model_copy(update={"floor": 2, "shafts": stair_below_at(0.0)})
        assert not any(
            f.severity is Severity.ERROR
            for f in _circulation(landed, Program(rooms=specs), floor)
        )

    def test_a_bedroom_behind_the_kitchen_is_reported(self):
        """The 40x60: every bedroom lay beyond the kitchen. A kitchen is somewhere you
        walk through to a utility, not the way to the bedrooms."""
        from app.ir.plan import Program
        from app.validator import _through_private_rooms

        layout, specs = self._row(
            ("foyer", SpaceKind.FOYER), ("kitchen", SpaceKind.KITCHEN),
            ("bed1", SpaceKind.BEDROOM), through={"foyer", "kitchen"},
        )
        graph = {"foyer": {"kitchen"}, "kitchen": {"foyer", "bed1"}, "bed1": {"kitchen"}}
        findings = _through_private_rooms(layout, Program(rooms=specs), None, graph, "foyer")
        assert any("through the kitchen" in f.message and "bed1" in f.rooms for f in findings)

    def test_the_hall_behind_the_kitchen_is_reported(self):
        """The 30x40 2BHK and the 30x50, once the deeper search made them legal: both
        were entered foyer → kitchen → hall, and ⑦ listed only the bedrooms beyond it."""
        from app.ir.plan import Program
        from app.validator import _through_private_rooms

        layout, specs = self._row(
            ("foyer", SpaceKind.FOYER), ("kitchen", SpaceKind.KITCHEN),
            ("hall", SpaceKind.HALL), through={"foyer", "kitchen", "hall"},
        )
        graph = {"foyer": {"kitchen"}, "kitchen": {"foyer", "hall"}, "hall": {"kitchen"}}
        findings = _through_private_rooms(layout, Program(rooms=specs), None, graph, "foyer")
        assert any("through the kitchen" in f.message and "hall" in f.rooms for f in findings)
        # A kitchen is a room people do walk through: reported, not refused.
        assert all(f.severity is Severity.WARNING for f in findings)

    def test_a_front_door_that_does_not_lead_into_the_house_is_reported(self):
        """The model's 30x40 3BHK was entered foyer → staircase → corridor → hall. Every
        room was reachable and no private room was crossed, so nothing objected."""
        from types import SimpleNamespace

        from app.ir.enums import OpeningKind
        from app.ir.plan import Program
        from app.validator import _circulation

        layout, specs = self._row(
            ("foyer", SpaceKind.FOYER), ("stair", SpaceKind.STAIRCASE),
            ("hall", SpaceKind.HALL), through={"foyer", "stair", "hall"},
        )
        entrance = SimpleNamespace(kind=OpeningKind.ENTRANCE, connects=("foyer",))

        def door(a, b):
            return SimpleNamespace(kind=OpeningKind.DOOR, connects=(a, b))

        via_stair = SimpleNamespace(
            openings=[entrance, door("foyer", "stair"), door("stair", "hall")]
        )
        findings = _circulation(layout, Program(rooms=specs), via_stair)
        assert any("does not lead into the house" in f.message for f in findings)
        assert all(f.severity is Severity.WARNING for f in findings)

        direct = SimpleNamespace(
            openings=[entrance, door("foyer", "hall"), door("stair", "hall")]
        )
        assert not any(
            "does not lead into the house" in f.message
            for f in _circulation(layout, Program(rooms=specs), direct)
        )

    def test_rooms_kept_apart_that_share_a_wall_are_reported(self):
        """The model's 30x40 3BHK put its pooja room against a bathroom. `score` knew and
        the penalty lost; ⑦ did not look."""
        from app.ir.enums import Relation
        from app.ir.plan import AdjacencySpec, Program
        from app.validator import _kept_apart

        layout, specs = self._row(("pooja", SpaceKind.POOJA), ("bath1", SpaceKind.BATHROOM))
        apart = Program(
            rooms=specs,
            adjacencies=[
                AdjacencySpec(a="pooja", b="bath1", relation=Relation.SEPARATED, hard=True)
            ],
        )
        findings = _kept_apart(layout, apart)
        assert [set(f.rooms) for f in findings] == [{"pooja", "bath1"}]
        assert findings[0].severity is Severity.WARNING
        # No edge, no complaint: the rule is the programme's, not a list in ⑦.
        assert _kept_apart(layout, Program(rooms=specs)) == []

    def test_a_second_honest_route_clears_the_room(self):
        """Every route, not the shortest. One good way in is enough."""
        from app.ir.plan import Program
        from app.validator import _through_private_rooms

        layout, specs = self._row(
            ("foyer", SpaceKind.FOYER), ("bed1", SpaceKind.BEDROOM),
            ("hall", SpaceKind.HALL), through={"foyer", "hall"},
        )
        graph = {
            "foyer": {"bed1", "hall"}, "bed1": {"foyer", "hall"}, "hall": {"foyer", "bed1"},
        }
        assert _through_private_rooms(layout, Program(rooms=specs), None, graph, "foyer") == []

    def test_a_car_bay_off_the_road_is_an_error(self, plans):
        """A bay no driveway reaches does not satisfy the parking requirement. Built by
        moving the road, so the test does not depend on a plan getting this wrong."""
        from app.ir.enums import Facing

        layout, program, floor, report = plans["40x60"]
        assert not report.by_check("access"), "fixture must start with a reachable bay"

        bay = next(
            r for r in layout.rooms
            if next(s for s in program.rooms if s.id == r.room_id).kind is SpaceKind.CAR_PARKING
        )
        sides = {
            Facing.NORTH: abs(bay.y_max_m - layout.y_max_m) <= TOLERANCE_M,
            Facing.SOUTH: abs(bay.y_min_m - layout.y_min_m) <= TOLERANCE_M,
            Facing.EAST: abs(bay.x_max_m - layout.x_max_m) <= TOLERANCE_M,
            Facing.WEST: abs(bay.x_min_m - layout.x_min_m) <= TOLERANCE_M,
        }
        elsewhere = next(side for side, touches in sides.items() if not touches)
        moved = layout.model_copy(update={"road_edges": [elsewhere]})

        findings = validate(moved, program, floor).by_check("access")
        assert findings and findings[0].severity is Severity.ERROR
        assert bay.room_id in findings[0].rooms

    def test_a_bedroom_floor_with_no_bathroom_is_reported(self, plans):
        """The 30x40 stilt house's top floor: two bedrooms, no toilet."""
        layout, program, floor, _ = plans["50x80"]
        no_baths = program.model_copy(
            update={
                "rooms": [
                    r.model_copy(update={"kind": SpaceKind.STORE})
                    if r.kind in (SpaceKind.BATHROOM, SpaceKind.WC) else r
                    for r in program.rooms
                ]
            }
        )
        findings = validate(layout, no_baths, floor).by_check("sanitation")
        assert findings and findings[0].severity is Severity.WARNING

    def test_a_grossly_oversized_room_is_reported(self, plans):
        """The 50x80's 27.7 m² bathroom passed because ⑦ only measured rooms that were
        too small. Built by lowering a room's ceiling below the space it was given."""
        layout, program, floor, _ = plans["40x60"]
        biggest = max(
            (r for r in layout.rooms),
            key=lambda r: floor.clear_area_sq_m(r.room_id) or 0,
        )
        spec = next(s for s in program.rooms if s.id == biggest.room_id)
        area = floor.clear_area_sq_m(biggest.room_id)
        assert area > 2 * spec.min_area_sq_m + 3, "fixture room must be well above its minimum"

        shrunk = program.model_copy(
            update={
                "rooms": [
                    r.model_copy(update={
                        "target_area_sq_m": r.min_area_sq_m, "max_target_sq_m": None,
                    }) if r.id == biggest.room_id else r
                    for r in program.rooms
                ]
            }
        )
        findings = validate(layout, shrunk, floor).by_check("size")
        assert any(biggest.room_id in f.rooms for f in findings)


class TestTheJudgeRanksRefusalsBySeverity:
    """A 30x50 had two refused candidates, one error each: a car bay off the road, and a
    foyer with no street wall, so no front door. Tied on errors, the lower penalty won —
    a house nobody could walk into. Tested by feeding the judge two reports directly, so
    it does not depend on any brief producing that tie."""

    def test_a_house_you_can_enter_beats_one_you_cannot(self, plans, monkeypatch):
        import app.validator as validator_module
        from app.ir.validation import Finding, Report

        layout, program, _, _ = plans["40x60"]
        cannot_enter = layout.model_copy(update={"score": 10.0})
        car_on_street = layout.model_copy(update={"score": 500.0})

        def fake_validate(candidate, _program, _floor):
            if candidate.score == cannot_enter.score:
                finding = Finding(
                    check="circulation", severity=Severity.ERROR,
                    message="the plan has no front door, so no room is reachable at all",
                )
            else:
                finding = Finding(
                    check="access", severity=Severity.ERROR,
                    message="car_parking does not touch the north road",
                )
            return Report(findings=[finding], checks_run=list(CHECKS))

        monkeypatch.setattr(validator_module, "validate", fake_validate)
        key = validator_module.judge(program)
        assert key(car_on_street) < key(cannot_enter), (
            "a far better penalty must not buy a house with no way in"
        )

    def test_a_passing_plan_still_beats_any_refused_one(self, plans, monkeypatch):
        import app.validator as validator_module
        from app.ir.validation import Finding, Report

        layout, program, _, _ = plans["40x60"]
        refused = layout.model_copy(update={"score": 1.0})
        passing = layout.model_copy(update={"score": 999.0})

        def fake_validate(candidate, _program, _floor):
            findings = (
                [Finding(check="access", severity=Severity.ERROR, message="x")]
                if candidate.score == refused.score else []
            )
            return Report(findings=findings, checks_run=list(CHECKS))

        monkeypatch.setattr(validator_module, "validate", fake_validate)
        key = validator_module.judge(program)
        assert key(passing) < key(refused)
