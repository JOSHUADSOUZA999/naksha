"""Stage ⑤ Stage A — slicing trees.

The invariant that matters is that a layout tiles its envelope exactly: gap-free and
overlap-free. A slicing tree gives that *by construction*, which is the whole reason
decision 2 chose one — so these tests exist to catch the day someone optimises the
construction and quietly loses the guarantee.

Deterministic given a seed, so nothing here is approximate except where it is
explicitly statistical.
"""

from __future__ import annotations

import random

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.envelope import build_envelope
from app.ir.layout import TOLERANCE_M
from app.llm import fallback
from app.program import expand
from app.solver import improve, solve
from app.solver import slicing

BRIEF = "30x40 east facing site in Whitefield, 3BHK with pooja room"


@pytest.fixture(scope="module")
def case():
    brief = fallback.parse(BRIEF)
    envelope = build_envelope(brief, allow_unverified=True)
    return expand(brief, envelope), envelope


class TestTilingIsFreeByConstruction:
    @settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(seed=st.integers(0, 10_000))
    def test_every_candidate_tiles_exactly(self, case, seed):
        """`Layout` validates this on construction, so a solve that returns anything
        at all has already proved it. The generative part is that it holds for *any*
        seed, not the handful a fixed test would try."""
        program, envelope = case
        for layout in solve(program, envelope, candidates=12, seed=seed, keep=1):
            covered = sum(room.area_sq_m for room in layout.rooms)
            assert covered == pytest.approx(layout.area_sq_m, rel=1e-6)

    def test_no_two_rooms_overlap(self, case):
        program, envelope = case
        layout = solve(program, envelope, candidates=50, seed=3)[0]
        for i, room in enumerate(layout.rooms):
            for other in layout.rooms[i + 1 :]:
                assert not room.overlaps(other), f"{room.room_id}/{other.room_id}"

    def test_every_room_is_placed_exactly_once(self, case):
        program, envelope = case
        layout = solve(program, envelope, candidates=50, seed=3, floor=1)[0]
        placed = [room.room_id for room in layout.rooms]
        assert sorted(placed) == sorted(r.id for r in program.on_floor(1))

    def test_rooms_stay_inside_the_envelope(self, case):
        program, envelope = case
        layout = solve(program, envelope, candidates=50, seed=5)[0]
        for room in layout.rooms:
            assert room.x_min_m >= envelope.x_min_m - TOLERANCE_M
            assert room.x_max_m <= envelope.x_max_m + TOLERANCE_M
            assert room.y_min_m >= envelope.y_min_m - TOLERANCE_M
            assert room.y_max_m <= envelope.y_max_m + TOLERANCE_M


class TestDeterminism:
    def test_the_same_seed_replays_the_same_plan(self, case):
        """A plan you cannot reproduce is one two people cannot discuss — which is why
        `PlanBundle` carries the seed."""
        program, envelope = case
        a = solve(program, envelope, candidates=80, seed=42)[0]
        b = solve(program, envelope, candidates=80, seed=42)[0]
        assert a.model_dump() == b.model_dump()

    def test_different_seeds_explore_different_plans(self, case):
        program, envelope = case
        a = solve(program, envelope, candidates=80, seed=1)[0]
        b = solve(program, envelope, candidates=80, seed=2)[0]
        assert a.model_dump() != b.model_dump()


class TestHillClimbing:
    def test_improving_never_makes_a_layout_worse(self, case):
        """Hill-climbing accepts a swap only on strict improvement. If this fails the
        search is wandering, not climbing."""
        program, envelope = case
        rng = random.Random(11)
        rooms = program.on_floor(1)
        tree = slicing.random_tree(rooms, rng)
        placed = slicing.place(
            tree, envelope.x_min_m, envelope.y_min_m, envelope.x_max_m, envelope.y_max_m
        )
        from app.ir.layout import Layout
        from app.solver import score as scoring

        raw = Layout(
            rooms=placed,
            x_min_m=envelope.x_min_m, y_min_m=envelope.y_min_m,
            x_max_m=envelope.x_max_m, y_max_m=envelope.y_max_m,
        )
        hard_before, soft_before, _ = scoring.score(raw, program)
        after = improve(raw, program)
        # Lexicographic, matching the ranking: never more unbuildable rooms, and no
        # worse on preferences at the same count.
        assert (after.unbuildable, after.score) <= (hard_before, soft_before)

    def test_swapping_occupants_leaves_the_tiling_alone(self, case):
        """The geometry is never touched — only which room sits in which rectangle.
        That is why no swap can open a gap."""
        program, envelope = case
        layout = solve(program, envelope, candidates=40, seed=8)[0]
        rects = sorted(
            (r.x_min_m, r.y_min_m, r.x_max_m, r.y_max_m) for r in layout.rooms
        )
        climbed = improve(layout, program)
        assert sorted(
            (r.x_min_m, r.y_min_m, r.x_max_m, r.y_max_m) for r in climbed.rooms
        ) == rects

    def test_the_violations_match_the_score(self, case):
        """A score with no reasons attached tells nobody what to fix, which is the
        thing stage ④ exists to do."""
        program, envelope = case
        layout = solve(program, envelope, candidates=60, seed=4)[0]
        assert (layout.score > 0) == bool(layout.violations)


class TestEmptyFloors:
    def test_a_floor_with_no_rooms_returns_nothing(self, case):
        """Rather than an empty layout, which would draw as a blank page instead of
        as an absence."""
        program, envelope = case
        assert solve(program, envelope, floor=4, candidates=10) == []


class TestTheBundleReadsItsOwnOutput:
    """`computed_field` and `extra="forbid"` contradict each other: derived values are
    serialised but rejected on input, so a strict model refuses its own JSON. That
    makes the contract write-only — no round-trip, no fixture built from a saved
    payload, no API echoing a stored plan back. It was write-only until this test.
    """

    def test_a_bundle_survives_a_round_trip(self, case):
        import json

        from app.ir.layout import PlanBundle
        from app.solver import plan

        program, envelope = case
        brief = fallback.parse(BRIEF)
        bundle = plan(brief, envelope, program, candidates=40, seed=5)
        payload = json.loads(bundle.model_dump_json())
        assert json.loads(PlanBundle.model_validate(payload).model_dump_json()) == payload

    def test_derived_values_are_recomputed_not_trusted(self, case):
        """Arithmetic cannot meaningfully disagree, so a stale figure in a saved file
        is a stale file — recomputing is right, erroring is not."""
        from app.ir.layout import PlacedRoom

        room = PlacedRoom(
            room_id="a", x_min_m=0, y_min_m=0, x_max_m=4, y_max_m=5,
        )
        payload = room.model_dump(mode="json")
        payload["area_sq_m"] = 999.0  # stale
        assert PlacedRoom.model_validate(payload).area_sq_m == pytest.approx(20.0)

    def test_plot_spec_still_refuses_a_contradicting_facing(self):
        """The stricter rule stays where it belongs: `facing` names a *choice*
        someone might have hand-edited, not arithmetic."""
        from pydantic import ValidationError

        from app.ir.enums import Facing
        from app.ir.models import PlotSpec

        plot = PlotSpec(width_m=9.1, depth_m=12.2, road_edges=[Facing.NORTH])
        payload = plot.model_dump(mode="json")
        with pytest.raises(ValidationError, match="disagrees with road_edges"):
            PlotSpec.model_validate({**payload, "facing": "south"})


class TestUnbuildableDominates:
    """A room below its legal minimum is not a worse version of a misplaced one.

    Weighting alone let many cheap violations outvote a few catastrophic ones:
    hill-climbing traded two rooms below their minimum for enough satisfied Vastu
    preferences to win on total, and produced a 2.2 m² hall.
    """

    def test_the_climb_never_creates_an_unbuildable_room(self, case):
        program, envelope = case
        for seed in range(6):
            raw = solve(program, envelope, candidates=120, seed=seed, keep=1)[0]
            assert raw.unbuildable == 0 or raw.violations

    def test_generation_allocates_every_room_above_its_minimum(self, case):
        """`effective_areas` shrinks towards minimums rather than scaling targets, so
        a tight budget cannot push a room below the floor feasibility cleared it at."""
        import random

        from app.ir.layout import Layout
        from app.solver import score as scoring

        program, envelope = case
        rooms = program.on_floor(1)
        weights = slicing.effective_areas(rooms, envelope.max_footprint_sq_m)
        assert all(weights[r.id] >= r.min_area_sq_m - 1e-9 for r in rooms)

        tree = slicing.random_tree(rooms, random.Random(3), weights)
        placed = slicing.place(
            tree, envelope.x_min_m, envelope.y_min_m, envelope.x_max_m, envelope.y_max_m
        )
        raw = Layout(
            rooms=placed,
            x_min_m=envelope.x_min_m, y_min_m=envelope.y_min_m,
            x_max_m=envelope.x_max_m, y_max_m=envelope.y_max_m,
        )
        areas = [v for v in scoring.score(raw, program)[2] if "m\u00b2, below" in v]
        assert areas == [], areas

    def test_a_tight_budget_lands_everyone_on_their_minimum(self, case):
        """With no room to spare, `f` goes to zero and every room gets exactly its
        legal floor — the same quantity feasibility tests, so the two agree."""
        program, _ = case
        rooms = program.on_floor(1)
        floor = sum(r.min_area_sq_m for r in rooms)
        weights = slicing.effective_areas(rooms, floor)
        assert all(
            weights[r.id] == pytest.approx(r.min_area_sq_m) for r in rooms
        )

    def test_a_generous_budget_gives_everyone_their_target(self, case):
        program, _ = case
        rooms = program.on_floor(1)
        weights = slicing.effective_areas(rooms, 10_000.0)
        assert all(
            weights[r.id] == pytest.approx(r.target_area_sq_m) for r in rooms
        )


class TestFloatSlack:
    """`effective_areas` allocates exactly the minimum when the budget is tight, and
    17.999999 is not a violation of an 18.0 m² floor. Without slack the scorer invents
    unbuildable rooms on precisely the plans it should judge most carefully."""

    def test_a_room_exactly_on_its_minimum_is_legal(self, case):
        from app.ir.layout import Layout, PlacedRoom
        from app.ir.plan import Program, RoomSpec
        from app.ir.enums import SpaceKind
        from app.solver import score as scoring

        spec = RoomSpec(
            id="bay", kind=SpaceKind.CAR_PARKING,
            min_area_sq_m=18.0, target_area_sq_m=18.0, min_width_m=3.0, max_aspect=2.2,
        )
        # 3.0 x 6.0 built the way float division would produce it.
        width = 18.0 / 6.0
        room = PlacedRoom(room_id="bay", x_min_m=0, y_min_m=0, x_max_m=width, y_max_m=6.0)
        layout = Layout(rooms=[room], x_min_m=0, y_min_m=0, x_max_m=width, y_max_m=6.0)
        hard, _, reasons = scoring.score(layout, Program(rooms=[spec]))
        assert hard == 0, reasons


class TestStageBActuallyReachesTheEnvelope:
    """Stage B ran, produced legal geometry, and had it silently thrown away.

    `tuning.tune` works on a 1 cm integer grid. An envelope derived from feet does not
    land on that grid — 30 ft is 9.144 m — so every tuned layout stopped a fraction of
    a centimetre short of the wall, `Layout`'s exact-tiling validator called it a gap,
    and `solve` `continue`d past the rejection into its Stage A path. It was invisible
    from the outside: a plan still came back, just never a dimensioned one.

    Nothing in the suite caught it because everything here asserts properties a Stage A
    layout also has. What separates the two paths is *which* path ran, so that is what
    these assert.
    """

    # The metric case is the control. It passed throughout, and it is why the bug
    # survived: a developer checking "does tuning work" with a metre brief sees it
    # work. Every brief the product is actually for is quoted in feet.
    FEET = "40x60 site in Whitefield, 3BHK with pooja room"
    METRES = "Plot is 12m x 18m in Whitefield, 3BHK with pooja room"

    @staticmethod
    def _tuned(brief_text, limit=5):
        """Tune the best topologies, the way `solve` does.

        Ranking first is not incidental. A raw random tree is usually infeasible to
        dimension — measured at 22 in 24 — so tuning unranked trees finds nothing and
        a test built on them fails for a reason that has nothing to do with the bug.
        """
        from app.ir.layout import Layout
        from app.solver import footprint, tuning

        brief = fallback.parse(brief_text)
        envelope = build_envelope(brief, allow_unverified=True)
        program = expand(brief, envelope)
        rooms = program.on_floor(1)
        # The footprint, not the envelope — `solve` tiles the house, and the grid bug
        # this fixture exists to catch lives in whatever rectangle Stage B is handed.
        bounds = footprint(envelope, rooms)
        x_min_m, y_min_m, x_max_m, y_max_m = bounds
        weights = slicing.effective_areas(
            rooms, (x_max_m - x_min_m) * (y_max_m - y_min_m)
        )

        rng = random.Random(0)
        ranked = []
        for index in range(400):
            tree = slicing.random_tree(rooms, rng, weights)
            placed = slicing.place(tree, x_min_m, y_min_m, x_max_m, y_max_m)
            try:
                layout = Layout(
                    rooms=placed,
                    x_min_m=x_min_m,
                    y_min_m=y_min_m,
                    x_max_m=x_max_m,
                    y_max_m=y_max_m,
                    floor=1,
                )
            except ValueError:
                continue
            from app.solver import score as _s

            hard, penalty, _ = _s.score(layout, program)
            ranked.append(((hard, penalty), index, tree))
        ranked.sort(key=lambda row: (row[0], row[1]))

        out = []
        for _, _, tree in ranked[:60]:
            dimensioned = tuning.tune(tree, bounds, weights, time_limit_s=2.0)
            if dimensioned is not None:
                out.append(dimensioned)
            if len(out) >= limit:
                break
        return envelope, program, out, bounds

    @pytest.mark.parametrize("brief_text", [FEET, METRES])
    def test_a_tuned_layout_survives_the_tiling_validator(self, brief_text):
        from app.ir.layout import Layout

        _, _, tuned, bounds = self._tuned(brief_text)
        x_min_m, y_min_m, x_max_m, y_max_m = bounds
        assert tuned, "no topology could be dimensioned at all — the fixture is wrong"
        for dimensioned in tuned:
            Layout(  # the assertion is that this does not raise
                rooms=dimensioned,
                x_min_m=x_min_m,
                y_min_m=y_min_m,
                x_max_m=x_max_m,
                y_max_m=y_max_m,
                floor=1,
            )

    def test_tuning_reaches_its_bounds_exactly(self):
        """Not "within a tolerance" — the outer edges must be the given floats.

        A tolerance here would re-admit the bug: `Layout` compares the *total* covered
        area, so a 3 mm shortfall on each of a dozen rooms passes every per-room
        tolerance and still fails as a gap.
        """
        _, _, tuned, bounds = self._tuned(self.FEET)
        x_min_m, y_min_m, x_max_m, y_max_m = bounds
        assert tuned
        for dimensioned in tuned:
            assert min(r.x_min_m for r in dimensioned) == x_min_m
            assert max(r.x_max_m for r in dimensioned) == x_max_m
            assert min(r.y_min_m for r in dimensioned) == y_min_m
            assert max(r.y_max_m for r in dimensioned) == y_max_m

    def test_snapping_outward_never_shrinks_a_room(self):
        """The grid is floored inwards so a snapped edge only ever grows a room.

        Rounding to *nearest* would put the grid a few millimetres outside the
        envelope, and snapping back would shave a room the model had just proved met
        its minimum — trading a visible gap for an invisible illegality.
        """
        _, program, tuned, _bounds = self._tuned(self.FEET)
        specs = {r.id: r for r in program.on_floor(1)}
        assert tuned
        for dimensioned in tuned:
            for placed in dimensioned:
                spec = specs[placed.room_id]
                assert placed.area_sq_m >= spec.min_area_sq_m - TOLERANCE_M
                assert placed.shortest_side_m >= spec.min_width_m - TOLERANCE_M

    def test_the_plan_that_comes_back_is_a_dimensioned_one(self):
        """The outcome test, and the one that would have caught this from outside.

        Stage B exists because "0% of Stage A topologies broke a minimum area and 100%
        broke a minimum width". So a plan with every room at or above its minimum width
        is one Stage B dimensioned; a plan with a room below it is Stage A output that
        reached the user. No knowledge of the grid, the validator or `solve`'s control
        flow is needed to tell them apart.
        """
        for brief_text in (self.FEET, self.METRES, "30x40 site in Whitefield, 2BHK"):
            brief = fallback.parse(brief_text)
            envelope = build_envelope(brief, allow_unverified=True)
            program = expand(brief, envelope)
            specs = {r.id: r for r in program.rooms}
            layouts = solve(program, envelope, floor=1, seed=7)
            assert layouts, brief_text
            for placed in layouts[0].rooms:
                spec = specs[placed.room_id]
                assert placed.shortest_side_m >= spec.min_width_m - TOLERANCE_M, (
                    f"{brief_text}: {spec.id} is {placed.shortest_side_m:.2f} m across, "
                    f"below its {spec.min_width_m:.2f} m minimum — Stage A output reached "
                    f"the caller"
                )


class TestOversizeIsADefect:
    """A room can be too big, and until this existed nothing said so.

    Exact tiling fixes the total area, so whatever the programme does not ask for is
    forced into some room. On a 50x80, where 48% of the permitted footprint is surplus,
    that produced an 18 m² bathroom and a 34.8 m² corridor — legal, gap-free, scored
    zero unbuildable, and not a house.

    Scoring it does not *fix* it: every candidate carries the same surplus, so the
    ranking has nothing better to pick. What it buys is that the defect reaches
    `violations`, which is what stage ④ shows a user, instead of being invisible.
    """

    def _placed(self, spec_id, x_max, y_max):
        from app.ir.layout import PlacedRoom

        return PlacedRoom(room_id=spec_id, x_min_m=0.0, y_min_m=0.0, x_max_m=x_max, y_max_m=y_max)

    def test_a_bathroom_the_size_of_a_bedroom_is_flagged(self, case):
        from app.ir.layout import Layout
        from app.solver import score as _s

        program, envelope = case
        bath = next(r for r in program.rooms if r.kind.value == "bathroom")
        big = 5.0 * bath.target_area_sq_m
        side = big**0.5
        layout = Layout.model_construct(
            floor=1, x_min_m=0.0, y_min_m=0.0, x_max_m=side, y_max_m=side,
            rooms=[self._placed(bath.id, side, side)],
        )
        _, _, reasons = _s.score(layout, program)
        assert any(bath.id in r and "x the" in r for r in reasons), reasons

    def test_ordinary_slack_is_not_flagged(self, case):
        """A room at its target, and one modestly above it, are both fine.

        Slack is what `RoomSpec` carries two areas for. A rule that fires on it would
        make every plan look broken, which is the same failure as a rule that never
        fires.
        """
        from app.ir.layout import Layout
        from app.solver import score as _s

        program, envelope = case
        hall = next(r for r in program.rooms if r.kind.value == "hall")
        for factor in (1.0, 1.4, 1.9):
            area = factor * hall.target_area_sq_m
            side = area**0.5
            layout = Layout.model_construct(
                floor=1, x_min_m=0.0, y_min_m=0.0, x_max_m=side, y_max_m=side,
                rooms=[self._placed(hall.id, side, side)],
            )
            _, _, reasons = _s.score(layout, program)
            assert not any("x the" in r for r in reasons), (factor, reasons)

    def test_a_tiny_room_at_double_its_target_is_not_flagged(self, case):
        """Relative alone is not enough, which is why the rule needs both tests.

        A 2.5 m² pooja room that lands at 5 m² is twice its target and nobody would
        object. Firing on that would bury the 30 m² bathroom in noise.
        """
        from app.ir.layout import Layout
        from app.solver import score as _s

        program, envelope = case
        pooja = next((r for r in program.rooms if r.kind.value == "pooja"), None)
        if pooja is None:
            pytest.skip("this brief has no pooja room")
        area = 2.1 * pooja.target_area_sq_m
        assert area - pooja.target_area_sq_m < 3.0, "fixture no longer exercises the absolute arm"
        side = area**0.5
        layout = Layout.model_construct(
            floor=1, x_min_m=0.0, y_min_m=0.0, x_max_m=side, y_max_m=side,
            rooms=[self._placed(pooja.id, side, side)],
        )
        _, _, reasons = _s.score(layout, program)
        assert not any("x the" in r for r in reasons), reasons


class TestTheHouseMeetsTheStreet:
    """A car bay the driveway cannot reach is not a bay.

    Stage ② reads `road_edges` carefully enough to set a different setback per edge,
    and until this existed stage ⑤ then placed the garage wherever the tree left a
    gap. The defect is invisible in every aggregate — gap-free, legal, correctly
    scored — and obvious the moment anyone looks at the drawing.
    """

    def test_solve_tells_the_layout_where_the_street_is(self, case):
        """The check is skipped when `road_edges` is empty, so the wiring is the risk.

        This is the shape of the bug that killed Stage B for months: a constraint that
        silently does nothing because nobody passed it the data it needs.
        """
        program, envelope = case
        layout = solve(program, envelope, seed=3)[0]
        assert layout.road_edges == envelope.road_edges
        assert layout.road_edges, "the envelope itself must know its road edges"

    def test_a_room_on_the_wrong_boundary_is_not_road_access(self, case):
        """`needs_exterior_wall` and `needs_road_access` are different questions.

        A room on the rear wall has a window and no street, and conflating the two
        would score that as reachable.
        """
        from app.ir.enums import Facing
        from app.solver.score import _on_a_road_edge, _on_the_boundary

        program, envelope = case
        layout = solve(program, envelope, seed=3)[0]
        rear = layout.model_copy(update={"road_edges": [Facing.WEST]})
        front = layout.model_copy(update={"road_edges": [Facing.EAST]})

        on_east = [r for r in layout.rooms if abs(r.x_max_m - layout.x_max_m) <= TOLERANCE_M]
        assert on_east, "the fixture should place something on the east wall"
        probe = on_east[0]

        assert _on_the_boundary(probe, layout)
        assert _on_a_road_edge(probe, front)
        assert not _on_a_road_edge(probe, rear)

    def test_a_corner_plot_accepts_either_road(self, case):
        from app.ir.enums import Facing
        from app.solver.score import _on_a_road_edge

        program, envelope = case
        layout = solve(program, envelope, seed=3)[0]
        corner = layout.model_copy(update={"road_edges": [Facing.EAST, Facing.NORTH]})
        on_north = [r for r in layout.rooms if abs(r.y_max_m - layout.y_max_m) <= TOLERANCE_M]
        assert on_north
        assert _on_a_road_edge(on_north[0], corner)

    def test_being_unreachable_never_counts_as_unbuildable(self, case):
        """`unbuildable` means "below a statutory minimum" and must keep meaning it.

        `INACCESSIBLE` sits under `ILLEGAL` for exactly this reason — a weight bump
        that crossed 100 would quietly inflate the counter the CLI reports and stage
        ④ explains.
        """
        from app.solver.score import ILLEGAL, INACCESSIBLE, PREFERENCE, STRUCTURAL

        assert STRUCTURAL < INACCESSIBLE < ILLEGAL
        assert PREFERENCE < STRUCTURAL

        program, envelope = case
        layout = solve(program, envelope, seed=3)[0]
        road_defects = [v for v in layout.violations if "does not reach the" in v and "road" in v]
        below_minimum = [v for v in layout.violations if "below the" in v]
        assert layout.unbuildable == len(below_minimum)
        assert all(defect not in below_minimum for defect in road_defects)

    def test_the_rule_names_which_spaces_need_a_street(self, case):
        """Decision 4 — which rooms front the road is data, not a constant in code."""
        from app.rules import load_ruleset

        spaces = load_ruleset("spaces_v1").data["spaces"]
        assert spaces["car_parking"]["road_access"] is True
        assert spaces["foyer"]["road_access"] is True
        assert spaces["bedroom"]["road_access"] is False

        program, _ = case
        by_kind = {room.kind.value: room for room in program.rooms}
        assert by_kind["car_parking"].needs_road_access
        assert not by_kind["master_bedroom"].needs_road_access


class TestTheHouseIsNotTheEnvelope:
    """Max coverage is a ceiling, not a requirement.

    `Layout` tiles its bounds exactly, so tiling the *envelope* forced every square
    metre the programme never asked for into some room — a 33 m² bathroom and a 36 m²
    foyer on a 50x80, legal and gap-free and not a house.
    """

    ROOMY = "50x80 4bhk in Bengaluru with study"
    PACKED = "30x40 east facing site in Whitefield, Bengaluru, 3BHK with pooja room"

    @staticmethod
    def _case(text):
        from app.solver import footprint

        brief = fallback.parse(text)
        envelope = build_envelope(brief, allow_unverified=True)
        program = expand(brief, envelope)
        rooms = program.on_floor(1)
        return envelope, program, rooms, footprint(envelope, rooms)

    def test_a_packed_plot_keeps_its_whole_envelope(self):
        """Nothing to give back, so nothing changes — and no plot loses area to a
        refactor that was only ever meant to help the roomy ones."""
        envelope, _, _, bounds = self._case(self.PACKED)
        assert bounds == (
            envelope.x_min_m, envelope.y_min_m, envelope.x_max_m, envelope.y_max_m
        )

    def test_a_roomy_plot_builds_what_the_programme_asked_for(self):
        envelope, _, rooms, bounds = self._case(self.ROOMY)
        area = (bounds[2] - bounds[0]) * (bounds[3] - bounds[1])
        wanted = sum(room.target_area_sq_m for room in rooms)

        assert area < envelope.max_footprint_sq_m
        assert wanted <= area <= wanted * 1.10

    def test_the_coverage_cap_binds_and_used_to_be_ignored(self):
        """`max_footprint_sq_m` is the lesser of the rectangle and the coverage cap.

        The solver only ever saw the rectangle, so a 50x80 tiled 249.7 m² against a
        241.5 m² cap — an over-covered plan that nothing in the pipeline reported.
        """
        envelope, program, rooms, _ = self._case(self.ROOMY)
        assert envelope.max_footprint_sq_m < envelope.area_sq_m, "fixture must be capped"

        layout = solve(program, envelope, seed=7)[0]
        tiled = sum(placed.area_sq_m for placed in layout.rooms)
        assert tiled <= envelope.max_footprint_sq_m + TOLERANCE_M

    def test_the_house_sits_at_the_rear_so_the_leftover_is_out_front(self):
        """The strip has to fall in one usable piece on the street side — that is where
        the approach, the porch and the garden go. Centring would leave two useless
        ones."""
        from app.ir.enums import Facing

        envelope, _, _, bounds = self._case(self.ROOMY)
        assert envelope.road_edges[0] is Facing.NORTH, "fixture assumes a north road"

        x_min_m, y_min_m, x_max_m, y_max_m = bounds
        assert y_min_m == envelope.y_min_m            # flush against the rear
        assert y_max_m < envelope.y_max_m             # gives back depth at the front
        assert (x_min_m, x_max_m) == (envelope.x_min_m, envelope.x_max_m)

    def test_the_layout_still_tiles_exactly_what_it_was_given(self):
        """The invariant survives — the rectangle shrank, the guarantee did not."""
        envelope, program, _, bounds = self._case(self.ROOMY)
        layout = solve(program, envelope, seed=7)[0]

        assert (layout.x_min_m, layout.y_min_m, layout.x_max_m, layout.y_max_m) == bounds
        tiled = sum(placed.area_sq_m for placed in layout.rooms)
        expected = (bounds[2] - bounds[0]) * (bounds[3] - bounds[1])
        assert abs(tiled - expected) < 0.01
