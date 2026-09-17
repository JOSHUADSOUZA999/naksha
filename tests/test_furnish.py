"""Legal is not usable: rooms sized for the furniture they exist for.

The JP Nagar 40x60 drew a 6'3" x 15'6" kitchen, a 7'3" x 15'6" dining room and an 8'1"
wide hall. Each cleared its area, width and aspect limits; none holds its furniture the
way a person would place it. These tests build those rooms on purpose rather than waiting
for a brief that happens to produce them, because that test goes vacuous the day the
solver stops producing them.

Three places act on it, and each is tested apart: stage ⑦ reports it, `score` prices it
so the hill-climb can avoid it, and Stage B pulls dimensions towards it. None of them
refuses a room — furnishing is practice, and the law is already the constraints.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.ir.enums import Grade, Severity, SpaceKind
from app.ir.layout import Layout, PlacedRoom
from app.ir.plan import Program, RoomSpec
from app.ir.units import to_metres
from app.program import spec_for, usable_sizes
from app.rules import load_ruleset
from app.solver import score as scoring
from app.solver import tuning
from app.solver.slicing import Cut, Leaf
from app.validator import CHECKS, _furnish


def ft(feet: int, inches: int = 0) -> float:
    return to_metres(feet + inches / 12, "ft")


def _clear(room_id: str, width_m: float, depth_m: float):
    """A refined floor that knows one room's clear rectangle, and nothing else ⑦'s fit
    check reads."""
    return SimpleNamespace(clear={room_id: (0.0, 0.0, width_m, depth_m)})


def _findings(kind: SpaceKind, width_m: float, depth_m: float):
    spec = spec_for(kind, "room")
    layout = Layout(
        rooms=[PlacedRoom(room_id="room", x_min_m=0, y_min_m=0, x_max_m=width_m, y_max_m=depth_m)],
        x_min_m=0, y_min_m=0, x_max_m=width_m, y_max_m=depth_m,
    )
    return _furnish(layout, Program(rooms=[spec]), _clear("room", width_m, depth_m))


class TestTheRuleIsData:
    def test_every_entry_names_a_room_kind_and_a_fixture_that_exist(self):
        """A typo in a fixture name would size every bedroom from nothing."""
        rules = load_ruleset("furnish_v1").data
        fixtures = load_ruleset("refine_v1").data["fixtures"]
        for kind, rule in rules["rooms"].items():
            SpaceKind(kind)
            for arrangement in rule["arrangements"]:
                for entry in arrangement["across"] + arrangement["along"]:
                    if "fixture" in entry:
                        assert entry["side"] in fixtures[entry["fixture"]]
                    else:
                        assert entry["m"] > 0 and entry["for"]

    def test_it_is_marked_as_practice_not_law(self):
        """Nothing here is a bye-law, so nothing may read as checked against one."""
        rules = load_ruleset("furnish_v1")
        assert rules.unverified, "furnish_v1 claims to be verified"
        assert {path.split(".")[-1] for path in rules.unverified} >= set(rules.data["rooms"])

    def test_the_bed_a_room_is_sized_for_is_the_bed_it_is_drawn_with(self, monkeypatch):
        """Fixtures are looked up, not copied: a catalogue change reaches the fit."""
        from app import rules as rules_module

        real = rules_module.load_ruleset("refine_v1")
        data = json.loads(json.dumps(real.data))
        data["fixtures"]["bed"]["width_m"] = 1.8  # a king
        wider = rules_module.Ruleset(version="refine_v1", data=data, sha256=real.sha256)
        monkeypatch.setattr(
            "app.program.load_ruleset",
            lambda name: wider if name == "refine_v1" else rules_module.load_ruleset(name),
        )
        assert usable_sizes(SpaceKind.BEDROOM)[0] == (3.0, 3.35)

    def test_rooms_get_their_sizes_through_spec_for(self):
        """Both stage ③ front ends build rooms with `spec_for`, so both carry the fit."""
        assert spec_for(SpaceKind.KITCHEN, "k").usable_sizes_m == [(2.2, 2.55)]
        assert spec_for(SpaceKind.CORRIDOR, "c").usable_sizes_m == []

    def test_a_bedroom_is_sized_for_a_double_bed_and_drawn_with_one(self):
        """A second bedroom is as often a parent's as a child's."""
        schedule = load_ruleset("refine_v1").data["schedules"]
        assert schedule["bedroom"][0] == "bed"
        assert schedule["guest_room"][0] == "bed"


class TestShortfall:
    @given(
        st.floats(0.5, 6.0), st.floats(0.5, 6.0),
    )
    def test_turning_a_room_does_not_change_whether_it_is_furnished(self, w, d):
        spec = spec_for(SpaceKind.BEDROOM, "bed")
        assert spec.furnishing_shortfall_m(w, d) == spec.furnishing_shortfall_m(d, w)

    @given(st.floats(0.5, 6.0), st.floats(0.5, 6.0), st.floats(0.0, 1.0))
    def test_a_bigger_room_is_never_further_from_fitting(self, w, d, grow):
        spec = spec_for(SpaceKind.KITCHEN, "k")
        assert spec.furnishing_shortfall_m(w + grow, d) <= spec.furnishing_shortfall_m(w, d) + 1e-9

    def test_the_nearest_arrangement_decides(self):
        """A 10 x 10 ft bedroom holds a bed pushed against a wall, not one walked round."""
        spec = spec_for(SpaceKind.BEDROOM, "bed")
        assert spec.furnishing_shortfall_m(2.9, 2.9) == 0.0

    def test_a_room_with_nothing_to_fit_is_never_short(self):
        assert spec_for(SpaceKind.CORRIDOR, "c").furnishing_shortfall_m(0.9, 12.0) == 0.0

    def test_sizes_must_be_short_side_first(self):
        with pytest.raises(ValueError, match="short side, long side"):
            RoomSpec(
                id="k", kind=SpaceKind.KITCHEN, min_area_sq_m=5, target_area_sq_m=9,
                min_width_m=1.8, usable_sizes_m=[(3.0, 2.0)],
            )


class TestStageSevenReportsTheRoomsTheDrawingsShowed:
    @pytest.mark.parametrize(
        ("kind", "width_m", "depth_m", "grade"),
        [
            (SpaceKind.KITCHEN, ft(6, 3), ft(15, 6), Grade.MINOR),
            (SpaceKind.DINING, ft(7, 3), ft(15, 6), Grade.MINOR),
            (SpaceKind.HALL, ft(8, 1), ft(15, 6), Grade.MAJOR),
            (SpaceKind.BEDROOM, ft(7, 2), ft(14, 3), Grade.MAJOR),
            (SpaceKind.BATHROOM, ft(2, 5), ft(8, 2), Grade.MAJOR),
        ],
        ids=["kitchen 6'3 x 15'6", "dining 7'3 x 15'6", "hall 8'1 wide", "bedroom 7'2 wide",
             "bathroom 2'5 wide"],
    )
    def test_a_strip_room_is_reported(self, kind, width_m, depth_m, grade):
        findings = _findings(kind, width_m, depth_m)
        assert [f.grade for f in findings] == [grade]
        assert findings[0].rooms == ["room"]
        assert findings[0].why and findings[0].fix

    @pytest.mark.parametrize(
        ("kind", "width_m", "depth_m"),
        [
            (SpaceKind.KITCHEN, ft(8), ft(10)),
            (SpaceKind.DINING, ft(10), ft(12)),
            (SpaceKind.HALL, ft(12), ft(14)),
            (SpaceKind.BEDROOM, ft(10), ft(10)),
            (SpaceKind.BATHROOM, ft(5), ft(8)),
        ],
    )
    def test_a_room_a_person_would_furnish_is_not(self, kind, width_m, depth_m):
        assert _findings(kind, width_m, depth_m) == []

    def test_short_by_a_centimetre_is_not_a_finding(self):
        """The clearances are conventions, not tolerances anyone builds to."""
        assert _findings(SpaceKind.DINING, 2.39, 3.5) == []

    def test_it_is_never_refused(self):
        """A legal room is not an illegal one, however badly it furnishes."""
        for f in _findings(SpaceKind.BEDROOM, 2.1, 6.0):
            assert f.severity is Severity.WARNING

    def test_the_message_says_what_does_not_fit_in_feet(self):
        [finding] = _findings(SpaceKind.KITCHEN, ft(6, 3), ft(15, 6))
        assert "6'3\"" in finding.message and "counter" in finding.message

    def test_the_check_is_listed_as_run(self):
        assert "furnish" in CHECKS


class TestTheSearchCanSeeIt:
    @staticmethod
    def _one_room(kind: SpaceKind, width_m: float, depth_m: float):
        layout = Layout(
            rooms=[PlacedRoom(room_id="room", x_min_m=0, y_min_m=0, x_max_m=width_m, y_max_m=depth_m)],
            x_min_m=0, y_min_m=0, x_max_m=width_m, y_max_m=depth_m,
        )
        return scoring.score(layout, Program(rooms=[spec_for(kind, "room")]))

    def test_a_strip_kitchen_costs_more_than_a_square_one(self):
        """Walls included: the rectangles run to centrelines."""
        _, strip, reasons = self._one_room(SpaceKind.KITCHEN, 2.13, 4.95)
        _, square, _ = self._one_room(SpaceKind.KITCHEN, 2.9, 3.4)
        assert strip > square
        assert any("short of the furniture" in r for r in reasons)

    def test_no_shortfall_counts_as_unbuildable(self):
        """Capped below ILLEGAL: a hard-to-furnish room is not an unbuildable one."""
        unbuildable, _, reasons = self._one_room(SpaceKind.HALL, 2.9, 12.0)
        furnish = [r for r in reasons if "furniture" in r]
        assert furnish and unbuildable == sum(1 for r in reasons if "minimum" in r)
        assert scoring.FURNISH_PER_M * 12 > scoring.STRUCTURAL  # the cap is what holds it

    def test_stage_b_widens_a_strip_when_a_neighbour_can_spare_it(self, monkeypatch):
        """Target areas alone are indifferent to shape: 9 m² over a 4 m depth is a 2.25 m
        kitchen, a strip. The pull takes width from the hall, which has it to spare."""
        kitchen = spec_for(SpaceKind.KITCHEN, "kitchen")
        hall = spec_for(SpaceKind.HALL, "hall")
        tree = Cut(vertical=True, left=Leaf(kitchen, 9.0), right=Leaf(hall, 27.0))
        bounds = (0.0, 0.0, 9.0, 4.0)
        targets = {"kitchen": 9.0, "hall": 27.0}

        def kitchen_width(pull: int) -> float:
            monkeypatch.setattr(tuning, "FIT_PULL", pull)
            rooms = tuning.tune(tree, bounds, targets)
            placed = next(r for r in rooms if r.room_id == "kitchen")
            return placed.x_max_m - placed.x_min_m

        assert kitchen_width(0) == pytest.approx(2.25, abs=0.01)
        assert kitchen_width(tuning.FIT_PULL if tuning.FIT_PULL else 3000) >= 2.2 + 0.23 - 0.01

    def test_the_pull_never_makes_a_room_illegal(self, monkeypatch):
        """With no width to spare the legal minimum still holds, whatever the pull."""
        monkeypatch.setattr(tuning, "FIT_PULL", 10**6)
        kitchen = spec_for(SpaceKind.KITCHEN, "kitchen")
        bath = spec_for(SpaceKind.BATHROOM, "bath")
        tree = Cut(vertical=True, left=Leaf(kitchen, 9.0), right=Leaf(bath, 3.5))
        rooms = tuning.tune(tree, (0.0, 0.0, 3.9, 3.0), {"kitchen": 9.0, "bath": 3.5})
        assert rooms is not None
        widths = {r.room_id: r.x_max_m - r.x_min_m for r in rooms}
        assert widths["bath"] >= bath.min_width_m + 0.23 - 1e-6
        assert widths["kitchen"] >= kitchen.min_width_m + 0.23 - 1e-6
