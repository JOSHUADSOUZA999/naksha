"""The stair is a fixed element: a shape a step dictates, one type through the building,
drawn as flights, entered from a landing.

A staircase used to be sized like any room — 5 m², 1.0 m wide — and the JP Nagar plan drew
a 12'3" x 4'8" strip no flight of sixteen risers fits in. Every test here builds its case
on purpose rather than waiting for a plan that happens to show it.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.ir.enums import FixtureKind, Grade, SpaceKind
from app.ir.layout import Layout, PlacedRoom
from app.ir.plan import Program
from app.program import spec_for, stair_sizes
from app.rules import load_ruleset
from app.solver import _same_flight_as_below
from app.solver import score as scoring
from app.solver import slicing, tuning
from app.solver.slicing import Cut, Leaf


def _stair_layout(width_m: float, depth_m: float) -> tuple[Layout, Program]:
    layout = Layout(
        rooms=[PlacedRoom(room_id="stair", x_min_m=0, y_min_m=0, x_max_m=width_m, y_max_m=depth_m)],
        x_min_m=0, y_min_m=0, x_max_m=width_m, y_max_m=depth_m,
    )
    return layout, Program(rooms=[spec_for(SpaceKind.STAIRCASE, "stair")])


class TestTheShapeIsComputedFromAStep:
    def test_sixteen_risers_as_a_dog_leg_or_a_straight_run(self):
        """3.0 m at 190 mm is 16 risers: two flights of 8, 7 treads each, a 1.0 m landing."""
        assert stair_sizes("legal") == [(2.0, 2.75), (1.0, 4.5)]

    def test_a_taller_storey_reshapes_every_stair(self, monkeypatch):
        """Stored as rise, tread and height, never as rectangles someone retypes."""
        from app import rules as rules_module

        real = rules_module.load_ruleset("stairs_v1")
        data = json.loads(json.dumps(real.data))
        data["legal"]["floor_to_floor_m"] = 3.3   # 18 risers
        taller = rules_module.Ruleset(version="stairs_v1", data=data, sha256=real.sha256)
        monkeypatch.setattr(
            "app.program.load_ruleset",
            lambda name: taller if name == "stairs_v1" else rules_module.load_ruleset(name),
        )
        assert stair_sizes("legal") == [(2.0, 3.0), (1.0, 5.0)]

    def test_a_staircase_carries_it_as_a_constraint(self):
        spec = spec_for(SpaceKind.STAIRCASE, "stair")
        assert spec.min_sizes_m == stair_sizes("legal")
        assert spec_for(SpaceKind.BEDROOM, "bed").min_sizes_m == []


class TestAStairNoFlightFitsInIsIllegal:
    def test_the_jp_nagar_strip_is_unbuildable(self):
        """12'3" x 4'8" inside its walls held no flight; it passed area and width."""
        layout, program = _stair_layout(3.73 + 0.23, 1.42 + 0.23)
        unbuildable, _, reasons = scoring.score(layout, program)
        assert unbuildable >= 1
        assert any("smallest shape a staircase must hold" in r for r in reasons)

    def test_breaches_says_what_it_needs_in_feet(self):
        from app.refine import breaches

        floor = SimpleNamespace(clear={"stair": (0.0, 0.0, 3.73, 1.42)})
        _, program = _stair_layout(4.0, 2.0)
        [message] = [m for m in breaches(floor, program) if "staircase needs" in m]
        assert "6'7\"" in message  # the 2.0 m dog-leg

    @pytest.mark.parametrize(("w", "d"), [(2.0, 2.75), (2.75, 2.0), (1.0, 4.5), (1.2, 6.0)])
    def test_a_stair_that_holds_a_template_is_legal(self, w, d):
        from app.refine import breaches

        floor = SimpleNamespace(clear={"stair": (0.0, 0.0, w, d)})
        _, program = _stair_layout(w, d)
        assert not [m for m in breaches(floor, program) if "staircase needs" in m]

    def test_stage_b_will_not_dimension_a_strip(self):
        """1.1 m deep: past the 1.0 m legal width and too shallow for either template once
        the walls are counted, however long. A 1.6 m band would hold a straight run."""
        stair = spec_for(SpaceKind.STAIRCASE, "stair")
        assert tuning.tune(Leaf(stair, 7.0), (0.0, 0.0, 5.0, 1.1), {"stair": 7.0}) is None
        assert tuning.tune(Leaf(stair, 7.0), (0.0, 0.0, 7.0, 1.6), {"stair": 7.0}) is not None

    def test_stage_b_turns_a_band_into_a_dog_leg_when_it_can(self):
        stair = spec_for(SpaceKind.STAIRCASE, "stair")
        store = spec_for(SpaceKind.STORE, "store")
        tree = Cut(vertical=True, left=Leaf(stair, 7.0), right=Leaf(store, 4.0))
        rooms = tuning.tune(tree, (0.0, 0.0, 6.0, 3.0), {"stair": 7.0, "store": 4.0})
        placed = next(r for r in rooms if r.room_id == "stair")
        clear = (placed.x_max_m - placed.x_min_m - 0.23, placed.y_max_m - placed.y_min_m - 0.23)
        assert stair.minimum_shape_shortfall_m(*clear) == 0.0


class TestOneStairThroughTheBuilding:
    def test_the_storey_above_climbs_the_type_below(self):
        """A straight flight below and a dog-leg above are both legal and no way up."""
        below = PlacedRoom(room_id="s1", x_min_m=0, y_min_m=0, x_max_m=6.5, y_max_m=1.3)
        upstairs = [spec_for(SpaceKind.STAIRCASE, "s2", floor=2), spec_for(SpaceKind.BEDROOM, "b", floor=2)]
        held = _same_flight_as_below(upstairs, {SpaceKind.STAIRCASE: below})
        assert held[0].min_sizes_m == [(1.0, 4.5)]
        assert held[1] is upstairs[1]

    def test_a_stair_below_that_holds_nothing_changes_nothing(self):
        below = PlacedRoom(room_id="s1", x_min_m=0, y_min_m=0, x_max_m=1.5, y_max_m=1.5)
        upstairs = [spec_for(SpaceKind.STAIRCASE, "s2", floor=2)]
        assert _same_flight_as_below(upstairs, {SpaceKind.STAIRCASE: below}) == upstairs

    def test_the_stair_first_tree_cuts_the_shaft_out_first(self):
        """Tuned with the shaft as its anchor, the stair lands on the stair below."""
        import random

        from app.solver.score import _overlap

        stair = spec_for(SpaceKind.STAIRCASE, "stair2", floor=2)
        rooms = [stair] + [spec_for(SpaceKind.BEDROOM, f"bed{i}", floor=2) for i in range(3)]
        bounds = (0.0, 0.0, 10.0, 8.0)
        below = (0.0, 6.5, 6.5, 8.0)   # a straight flight along the north wall
        weights = {r.id: r.target_area_sq_m for r in rooms}
        landed = []
        for seed in range(20):
            tree = slicing.shaft_first_tree(rooms, random.Random(seed), weights, bounds, stair, below)
            if tree is None:
                continue
            tuned = tuning.tune(tree, bounds, weights, anchors={"stair2": below})
            if tuned is None:
                continue
            placed = next(r for r in tuned if r.room_id == "stair2")
            landed.append(_overlap(placed, PlacedRoom(room_id="s", x_min_m=below[0], y_min_m=below[1],
                                                      x_max_m=below[2], y_max_m=below[3])))
        assert landed and max(landed) >= 0.95

    def test_a_stair_under_open_sky_is_ranked_as_refused(self):
        """Weighted, several strip bedrooms' furnishing cost outvoted it."""
        layout = Layout(
            rooms=[PlacedRoom(room_id="stair", x_min_m=0, y_min_m=4, x_max_m=2.23, y_max_m=7),
                   PlacedRoom(room_id="hall", x_min_m=2.23, y_min_m=4, x_max_m=6, y_max_m=7),
                   PlacedRoom(room_id="bed", x_min_m=0, y_min_m=0, x_max_m=6, y_max_m=4)],
            x_min_m=0, y_min_m=0, x_max_m=6, y_max_m=7, shaft_zone=(0, 0, 6, 4),
        )
        program = Program(rooms=[spec_for(SpaceKind.STAIRCASE, "stair"), spec_for(SpaceKind.HALL, "hall"),
                                 spec_for(SpaceKind.BEDROOM, "bed")])
        assert scoring.off_the_shaft(layout, program) == 1


class TestTheDrawingShowsAStair:
    def test_a_door_into_a_stair_goes_at_an_end_of_the_wall(self):
        from app.refine import _fit

        wall = SimpleNamespace(id="w", length_m=6.0)
        centred, _ = _fit(wall, 0.9, 0.1, [])
        at_end, _ = _fit(wall, 0.9, 0.1, [], at_an_end=True)
        assert centred == 3.0 and at_end == pytest.approx(0.55)

    def test_a_drawable_stair_gets_flights_and_a_landing(self):
        from app.refine import _stair_flights

        layout, program = _stair_layout(2.2 + 0.23, 3.2 + 0.23)
        rules = load_ruleset("refine_v1").data
        fixtures = _stair_flights(layout, program, [], [], rules)
        kinds = [f.kind for f in fixtures]
        assert kinds.count(FixtureKind.FLIGHT) == 2 and kinds.count(FixtureKind.LANDING) == 1
        room = layout.rooms[0]
        for f in fixtures:
            assert room.x_min_m <= f.x_min_m and f.x_max_m <= room.x_max_m
            assert room.y_min_m <= f.y_min_m and f.y_max_m <= room.y_max_m

    def test_a_stair_with_no_drawable_flight_is_reported(self):
        from app.validator import _stairs

        layout, program = _stair_layout(2.23, 3.0)
        floor = SimpleNamespace(clear={"stair": (0.0, 0.0, 2.0, 2.77)}, fixtures=[])
        [finding] = _stairs(layout, program, floor)
        assert finding.grade is Grade.MAJOR and finding.rooms == ["stair"]

    def test_an_illegal_stair_is_legality_s_finding_not_this_one(self):
        from app.validator import _stairs

        layout, program = _stair_layout(4.0, 1.65)
        floor = SimpleNamespace(clear={"stair": (0.0, 0.0, 3.73, 1.42)}, fixtures=[])
        assert _stairs(layout, program, floor) == []
