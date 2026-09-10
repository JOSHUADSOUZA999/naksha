"""Stage ③ PROGRAM — the deterministic expansion.

No model, no key: this is the floor under ③ exactly as `llm/fallback.py` is the floor
under ①. What it pins is that "3BHK with a pooja room" becomes every room a house
actually needs, and that the relationships between them survive into stage ⑤.
"""

from __future__ import annotations

import pytest

from app.envelope import build_envelope
from app.ir.enums import Relation, SpaceKind
from app.llm import fallback
from app.program import expand, fits
from app.rules import load_ruleset


def _program(text: str):
    return expand(fallback.parse(text))


def _kinds(text: str) -> list[str]:
    return [room.kind.value for room in _program(text).rooms]


class TestExpansion:
    def test_the_unstated_rooms_appear(self):
        """Nobody types "hall" or "corridor". A plan without them is not a house, and
        expanding a brief into only the rooms it named would produce one."""
        kinds = _kinds("30x40 3bhk in Bengaluru")
        assert {"foyer", "hall", "kitchen", "corridor"} <= set(kinds)

    def test_bhk_becomes_that_many_bedrooms(self):
        for n in (2, 3, 4):
            kinds = _kinds(f"30x40 {n}bhk in Bengaluru")
            bedrooms = kinds.count("bedroom") + kinds.count("master_bedroom")
            assert bedrooms == n

    def test_exactly_one_bedroom_is_the_master(self):
        assert _kinds("30x40 4bhk in Bengaluru").count("master_bedroom") == 1

    def test_bathrooms_come_from_the_brief(self):
        """Stage ① already defaulted and disclosed the count; ③ does not re-guess it."""
        brief = fallback.parse("30x40 3bhk in Bengaluru")
        assert _kinds("30x40 3bhk in Bengaluru").count("bathroom") == brief.program.bathrooms

    def test_named_rooms_survive_expansion(self):
        kinds = _kinds("30x40 3bhk in Bengaluru with pooja room and study")
        assert "pooja" in kinds and "study" in kinds

    def test_a_second_floor_gets_a_staircase(self):
        """Stairs appear exactly when a second floor does. Without an envelope there
        is no footprint to overflow, so the brief's own floor count stands."""
        assert "staircase" in _kinds("30x50 4bhk g+1 in Bengaluru")
        assert "staircase" not in _kinds("30x40 3bhk in Bengaluru")

    def test_the_site_can_force_a_floor_the_brief_did_not_ask_for(self):
        """A 3BHK needs ~70 m² at legal minimums; a 30x40 in Bengaluru leaves 55 m²
        buildable after setbacks. That is why those plots are built G+1 — the site
        decides, not the preference."""
        brief = fallback.parse("20x30 3bhk in Bengaluru")
        ground_only = expand(brief)
        with_site = expand(brief, build_envelope(brief, allow_unverified=True))
        assert {r.floor for r in ground_only.rooms} == {1}
        # How *many* extra floors the site forces depends on the figures, which are
        # still moving; that it forces one at all is the behaviour under test.
        assert len({r.floor for r in with_site.rooms}) > 1

    def test_a_small_plan_folds_dining_into_the_hall(self):
        """A separate dining room on a 2BHK costs a wall and buys nothing."""
        assert "dining" not in _kinds("30x40 2bhk in Bengaluru")
        assert "dining" in _kinds("30x40 3bhk in Bengaluru")


class TestRelationships:
    def test_bedrooms_open_off_the_corridor_not_the_hall(self):
        """What the corridor is for, and why privacy survives the tiling."""
        program = _program("30x40 3bhk in Bengaluru")
        for edge in program.adjacencies:
            pair = {edge.a, edge.b}
            if any(p.startswith("bed") for p in pair) and "hall" in pair:
                pytest.fail(f"bedroom opens directly off the hall: {edge}")

    def test_the_master_gets_the_en_suite(self):
        program = _program("30x40 3bhk in Bengaluru")
        assert any(
            {e.a, e.b} == {"bed1", "bath1"} and e.relation is Relation.CONNECTED
            for e in program.adjacencies
        )

    def test_no_toilet_shares_a_wall_with_the_kitchen(self):
        """The one placement every Indian client objects to, Vastu or not. Hard."""
        program = _program("30x40 3bhk in Bengaluru")
        separations = [
            e for e in program.adjacencies
            if e.relation is Relation.SEPARATED and "kitchen" in {e.a, e.b}
        ]
        assert separations, "kitchen/bathroom separation missing"
        assert all(e.hard for e in separations)

    def test_the_graph_references_only_real_rooms(self):
        """`Program` validates this on construction — this is the end-to-end proof
        that expansion never emits a dangling edge."""
        program = _program("30x50 4bhk g+1 in Bengaluru with study and car parking")
        ids = {room.id for room in program.rooms}
        for edge in program.adjacencies:
            assert {edge.a, edge.b} <= ids

    def test_no_room_carries_a_coordinate(self):
        """Decision 1, checked at the stage that would be tempted to break it."""
        dumped = _program("30x40 3bhk in Bengaluru").model_dump()
        assert "x" not in str(dumped.keys())


class TestFeasibility:
    """Two budgets, and conflating them was the bug. `max_built_area_sq_m` is FAR —
    floor area over *every* storey. `max_footprint_sq_m` is what one floor holds, and
    it is far smaller. Checking only FAR passed a programme that cannot be laid out,
    and stage ⑤ then returned tilings with every room at half its minimum."""

    def _case(self, text: str):
        brief = fallback.parse(text)
        envelope = build_envelope(brief, allow_unverified=True)
        return expand(brief, envelope), envelope

    def test_a_3bhk_on_a_30x40_fits_once_it_is_stacked(self):
        program, envelope = self._case("30x40 east facing 3bhk in Whitefield with pooja room")
        ok, why = fits(program, envelope)
        assert ok, why

    def test_the_same_programme_on_one_floor_does_not(self):
        """The finding the vertical slice existed to surface."""
        brief = fallback.parse("20x30 3bhk in Bengaluru")
        envelope = build_envelope(brief, allow_unverified=True)
        flat = expand(brief)  # no envelope, so nothing forces a second floor
        ok, why = fits(flat, envelope)
        assert not ok
        assert "buildable" in why and "floor 1" in why

    def test_a_refusal_names_the_floors_needed(self):
        """Stage ④ has to explain, not just decline."""
        brief = fallback.parse("20x30 3bhk in Bengaluru")
        _, why = fits(expand(brief), build_envelope(brief, allow_unverified=True))
        assert "floors" in why

    def test_it_measures_against_legal_minimums(self):
        """Failing at minimums means *no* arrangement exists — the distinction ④ needs
        to explain itself rather than re-planning twice and giving up."""
        program, envelope = self._case("30x40 east facing 3bhk in Whitefield")
        assert program.min_area_sq_m < program.target_area_sq_m
        ok, why = fits(program, envelope)
        assert ok and "m²" in why

    def test_the_footprint_not_the_far_budget_is_the_per_floor_limit(self):
        _, envelope = self._case("30x40 east facing 3bhk in Whitefield")
        assert envelope.max_footprint_sq_m < envelope.max_built_area_sq_m


class TestRulesAreData:
    def test_every_space_kind_has_a_rule(self):
        """A kind with no entry raises a KeyError mid-expansion — after the model has
        already been paid for."""
        rules = load_ruleset("spaces_v1").data["spaces"]
        assert {s.value for s in SpaceKind} == set(rules)

    def test_the_minimums_declare_themselves_unchecked(self):
        """These are minimum *legal* room sizes. A plan built to a wrong minimum is
        not a smaller mistake than a wrong setback.

        `spaces_v1` keys its blocks by room name where `setbacks_v1` nests them in
        lists — the checker has to find both, or a whole ruleset silently reads as
        verified."""
        rules = load_ruleset("spaces_v1")
        spaces = rules.data["spaces"]
        # Five were confirmed first-hand against the Karnataka Model Building
        # Bye-Laws 2017 and now carry `verified: true` with a clause reference. The
        # rest are still transcribed from practice, and the checker must still find
        # them — a partially verified ruleset is the easiest kind to mistake for a
        # finished one.
        assert 0 < len(rules.unverified) < len(spaces)
        # Every unchecked entry is reported, and no checked one is — stated as a
        # property so verifying another room does not fail the suite.
        unchecked = {n for n, r in spaces.items() if not r.get("verified")}
        assert {u.split(".", 1)[1] for u in rules.unverified} == unchecked

    def test_a_verified_figure_says_what_it_was_checked_against(self):
        """`verified: true` with no citation ages badly — nobody can tell against
        which document, or which amendment of it."""
        for name, rule in load_ruleset("spaces_v1").data["spaces"].items():
            if rule.get("verified"):
                assert "2017" in rule["source"], name
                assert "cl." in rule["source"], name

    def test_targets_are_never_below_minimums(self):
        """`RoomSpec` rejects it, so a bad rule file would fail at expansion time."""
        for name, rule in load_ruleset("spaces_v1").data["spaces"].items():
            assert rule["target_area_sq_m"] >= rule["min_area_sq_m"], name


class TestParkingIsDeterministic:
    """`parking_bays` is authoritative; `extra_rooms` is not.

    The model lists `car_parking` in `extra_rooms` on some runs and not others for the
    same brief. Sourcing a 15 m² bay from that choice made the programme swing 14%
    between identical inputs — nondeterminism stage ⑤ cannot absorb, and it was our
    bug, not the model's: `expand()` never read `parking_bays` at all.
    """

    def _bays(self, brief) -> list[str]:
        return [r.id for r in expand(brief).rooms if r.kind is SpaceKind.CAR_PARKING]

    def test_a_bay_is_built_whether_or_not_the_model_named_the_room(self):
        from app.ir.enums import RoomKind

        named = fallback.parse("30x40 3bhk in Bengaluru with car parking")
        unnamed = fallback.parse("30x40 3bhk in Bengaluru")
        assert RoomKind.CAR_PARKING in named.program.extra_rooms
        assert RoomKind.CAR_PARKING not in unnamed.program.extra_rooms
        assert named.program.parking_bays == unnamed.program.parking_bays == 1
        assert self._bays(named) == self._bays(unnamed) == ["car_parking"]

    def test_declining_parking_builds_no_bay(self):
        brief = fallback.parse("30x40 3bhk in Bengaluru, no parking")
        assert brief.program.parking_bays == 0
        assert self._bays(brief) == []

    def test_two_bays_become_two_rooms(self):
        brief = fallback.parse("30x40 3bhk in Bengaluru")
        brief = brief.model_copy(
            update={"program": brief.program.model_copy(update={"parking_bays": 2})}
        )
        assert self._bays(brief) == ["car_parking1", "car_parking2"]

    def test_the_bay_is_reached_from_the_entrance(self):
        """Not through the living room."""
        program = expand(fallback.parse("30x40 3bhk in Bengaluru"))
        assert any(
            {e.a, e.b} == {"foyer", "car_parking"} for e in program.adjacencies
        )

    def test_a_named_room_is_never_duplicated(self):
        """`car_parking` in `extra_rooms` plus a bay from `parking_bays` must not
        produce two rooms — `Program` would reject the duplicate id anyway."""
        program = expand(fallback.parse("30x40 3bhk in Bengaluru with car parking"))
        assert len(self._bays(fallback.parse("30x40 3bhk in Bengaluru with car parking"))) == 1
        assert len({r.id for r in program.rooms}) == len(program.rooms)


class TestAGenerousSiteBuysABiggerHouse:
    """`target_area_sq_m` is what a room should be on a tight plot, and it was the only
    figure there — so a 50x80 sized its programme exactly as a 30x40 does and left 47%
    of its permitted footprint unbuilt. Correct arithmetic, wrong house.
    """

    @staticmethod
    def _case(text: str):
        from app.envelope import build_envelope
        from app.llm import fallback

        brief = fallback.parse(text)
        envelope = build_envelope(brief, allow_unverified=True)
        return expand(brief, envelope), envelope

    def test_a_roomy_plot_grows_its_rooms(self):
        from app.ir.enums import SpaceKind

        tight, _ = self._case("30x50 3bhk in Bengaluru")
        roomy, _ = self._case("50x80 4bhk in Bengaluru with study")

        def hall(program):
            return next(r for r in program.rooms if r.kind is SpaceKind.HALL)

        assert hall(roomy).target_area_sq_m > hall(tight).target_area_sq_m

    def test_a_tight_plot_grows_nothing(self):
        """There is nothing to spend. Growing here would push a programme that already
        overflows further past its envelope."""
        from app.rules import load_ruleset

        spaces = load_ruleset("spaces_v1").data["spaces"]
        program, envelope = self._case("30x40 east facing 3bhk in Whitefield with pooja room")
        assert sum(r.target_area_sq_m for r in program.on_floor(1)) > envelope.max_footprint_sq_m
        for room in program.rooms:
            assert room.target_area_sq_m == spaces[room.kind.value]["target_area_sq_m"]

    def test_no_room_grows_past_its_ceiling(self):
        """The ceiling is the load-bearing half. Without one the surplus lands in
        whichever room the solver happens to pick, which is the defect `footprint` was
        introduced to fix."""
        program, _ = self._case("50x80 4bhk in Bengaluru with study")
        for room in program.rooms:
            if room.max_target_sq_m is not None:
                assert room.target_area_sq_m <= room.max_target_sq_m + 1e-9

    def test_the_car_bay_never_grows(self):
        """18 m² is what the bye-laws set, on any plot."""
        from app.ir.enums import SpaceKind

        for text in ("30x50 3bhk in Bengaluru", "50x80 4bhk in Bengaluru with study"):
            program, _ = self._case(text)
            bay = next(
                (r for r in program.rooms if r.kind is SpaceKind.CAR_PARKING), None
            )
            if bay is not None:
                assert bay.max_target_sq_m is None
                assert bay.target_area_sq_m == 18.0

    def test_growth_never_touches_a_minimum(self):
        """Only upward, and only the target. A minimum is a statutory floor and has
        nothing to do with how generous the site is."""
        from app.rules import load_ruleset

        spaces = load_ruleset("spaces_v1").data["spaces"]
        program, _ = self._case("50x80 4bhk in Bengaluru with study")
        for room in program.rooms:
            assert room.min_area_sq_m == spaces[room.kind.value]["min_area_sq_m"]
            assert room.target_area_sq_m >= room.min_area_sq_m


class TestThePorchInTheSetback:
    """Moving the 18 m² bay off the ground floor is what decides whether a 30x40 fits
    a 3BHK. Whether the setback may hold it is VERIFY.md Q1 — unanswered — so the flag
    is off by default and refuses when the strip is too shallow.
    """

    @staticmethod
    def _case(text: str):
        from app.envelope import build_envelope
        from app.llm import fallback

        brief = fallback.parse(text)
        return brief, build_envelope(brief, allow_unverified=True)

    def test_moving_the_bay_off_the_ground_floor_makes_a_30x40_feasible(self):
        """The measurement the whole flag exists for: 73.6 m² of minimums against a
        74.9 m² envelope becomes 55.6, and stage ④ flips."""
        from app.feasibility import assess

        brief, envelope = self._case(
            "30x40 east facing site in Whitefield, Bengaluru, 3BHK with pooja room"
        )
        grounded = expand(brief, envelope)
        assert not assess(grounded, envelope, floor=1).feasible

        deep = self._deepen(envelope)
        lifted = expand(brief, deep, porch_in_setback=True)
        assert sum(r.min_area_sq_m for r in lifted.on_floor(1)) < sum(
            r.min_area_sq_m for r in grounded.on_floor(1)
        )

    @staticmethod
    def _deepen(envelope):
        """A front setback that can actually take a bay.

        Needed because none of the real ones can — which is the finding, not a gap in
        the fixture."""
        from app.ir.enums import Facing

        return envelope.model_copy(
            update={"setbacks": {**envelope.setbacks, envelope.road_edges[0]: 3.2}}
        )

    def test_no_reference_plot_has_a_setback_deep_enough(self):
        """The measurement that stopped this becoming a feature.

        The deepest front setback in the set is 2.93 m against the 3.0 m a statutory
        bay needs — the 50x80 misses by seven centimetres. So on plots this size there
        is no setback to put a porch in, whatever the bye-laws permit.
        """
        from app.program import PorchDoesNotFit

        for text in (
            "30x40 east facing site in Whitefield, Bengaluru, 3BHK with pooja room",
            "30x50 3bhk in Bengaluru",
            "40x60 3bhk in Bengaluru with pooja room",
            "50x80 4bhk in Bengaluru with study",
        ):
            brief, envelope = self._case(text)
            with pytest.raises(PorchDoesNotFit):
                expand(brief, envelope, porch_in_setback=True)

    def test_the_refusal_carries_the_numbers(self):
        """"It does not fit" is not a finding anybody can act on."""
        from app.program import PorchDoesNotFit

        brief, envelope = self._case("40x60 3bhk in Bengaluru with pooja room")
        with pytest.raises(PorchDoesNotFit) as caught:
            expand(brief, envelope, porch_in_setback=True)
        assert "2.19 m deep" in str(caught.value)
        assert "3.0 x 6.0" in str(caught.value)

    def test_the_bay_leaves_the_tiled_programme_but_not_the_house(self):
        from app.ir.enums import SpaceKind

        brief, envelope = self._case("50x80 4bhk in Bengaluru with study")
        program = expand(brief, self._deepen(envelope), porch_in_setback=True)

        tiled = {r.id for r in program.on_floor(1)}
        every = {r.id for r in program.all_on_floor(1)}
        bay = next(r for r in program.rooms if r.kind is SpaceKind.CAR_PARKING)

        assert bay.outside_envelope
        assert bay.id not in tiled
        assert bay.id in every

    def test_it_is_placed_in_the_strip_and_never_inside_the_house(self):
        from app.refine import refine
        from app.solver import solve

        brief, envelope = self._case("50x80 4bhk in Bengaluru with study")
        deep = self._deepen(envelope)
        program = expand(brief, deep, porch_in_setback=True)
        layout = solve(program, deep, seed=7)[0]
        floor = refine(layout, program, deep)

        assert len(floor.outside) == 1
        porch = floor.outside[0]
        assert porch.area_sq_m >= 18.0 - 1e-6
        # Beyond the tiled rectangle on the road side, and touching it.
        assert porch.y_min_m >= layout.y_max_m - 1e-6
        assert porch.room_id not in {r.room_id for r in layout.rooms}

    def test_off_by_default(self):
        """The rule behind it is unverified, so nothing gets it by accident."""
        brief, envelope = self._case("50x80 4bhk in Bengaluru with study")
        program = expand(brief, envelope)
        assert not any(room.outside_envelope for room in program.rooms)
