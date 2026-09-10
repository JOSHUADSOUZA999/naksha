"""The stage ③ contract: rooms, sizes, and the constraint graph.

No solver and no model — `Program` is data, so every case here is exact. The point of
testing it before ③ exists is the same reason `ir/` is built first: everything
downstream inherits its mistakes, and an adjacency naming a room that does not exist
surfaces as an unsatisfiable constraint deep inside the solver where nothing can say
which line caused it.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.ir.enums import Relation, RoomKind, Sector, SpaceKind
from app.ir.plan import AdjacencySpec, Program, RoomSpec


def _room(room_id: str, **overrides) -> RoomSpec:
    base = {
        "id": room_id,
        "kind": SpaceKind.BEDROOM,
        "min_area_sq_m": 9.5,
        "target_area_sq_m": 12.0,
        "min_width_m": 2.4,
    }
    return RoomSpec(**{**base, **overrides})


class TestVocabulary:
    def test_space_kind_is_a_superset_of_room_kind(self):
        """Stage ③ expands the brief vocabulary into the plan one. A `RoomKind` with
        no counterpart here would be silently dropped during expansion — the user asks
        for a pooja room and simply does not get one."""
        assert {r.value for r in RoomKind} <= {s.value for s in SpaceKind}

    def test_every_brief_room_converts(self):
        for kind in RoomKind:
            assert SpaceKind.from_room_kind(kind).value == kind.value

    def test_the_taxonomy_covers_what_bhk_implies(self):
        """Nobody types "hall" or "corridor"; a plan without them is not a house."""
        implied = {"hall", "kitchen", "bedroom", "bathroom", "corridor", "staircase"}
        assert implied <= {s.value for s in SpaceKind}

    def test_sector_has_a_centre_and_facing_does_not(self):
        """The brahmasthan is a real place in the plan. A nullable `Facing` cannot say
        both "no preference" and "the middle"."""
        assert Sector.BRAHMASTHAN.value == "brahmasthan"
        assert "brahmasthan" not in {s.value for s in SpaceKind}


class TestRoomSpec:
    def test_two_areas_because_a_solver_needs_slack(self):
        """One number produces either cramped plans or infeasible ones."""
        room = _room("bed1", min_area_sq_m=9.5, target_area_sq_m=14.0)
        assert room.min_area_sq_m < room.target_area_sq_m

    def test_a_target_below_the_legal_minimum_is_rejected(self):
        with pytest.raises(ValidationError, match="below the minimum"):
            _room("bed1", min_area_sq_m=12.0, target_area_sq_m=9.0)

    def test_min_width_and_aspect_both_exist_on_purpose(self):
        """Area alone permits a 1 m x 9 m bedroom. Each blocks a different degenerate
        shape, so neither replaces the other."""
        room = _room("bed1")
        assert room.min_width_m > 0
        assert room.max_aspect >= 1.0

    def test_no_coordinates_anywhere(self):
        """Decision 1. The LLM emits a graph; the solver emits geometry. An x on this
        model is the whole architecture leaking."""
        forbidden = {"x", "y", "x_m", "y_m", "origin", "position", "rect", "polygon"}
        assert not forbidden & set(RoomSpec.model_fields)
        assert not forbidden & set(Program.model_fields)


class TestReferentialIntegrity:
    def test_an_adjacency_must_name_rooms_that_exist(self):
        with pytest.raises(ValidationError, match="unknown room"):
            Program(
                rooms=[_room("hall")],
                adjacencies=[AdjacencySpec(a="hall", b="ghost", relation=Relation.ADJACENT)],
            )

    def test_room_ids_are_unique(self):
        """Two rooms sharing an id makes every adjacency to it ambiguous."""
        with pytest.raises(ValidationError, match="duplicate room ids"):
            Program(rooms=[_room("bed"), _room("bed")])

    def test_a_room_cannot_neighbour_itself(self):
        with pytest.raises(ValidationError, match="cannot be adjacent to itself"):
            AdjacencySpec(a="hall", b="hall", relation=Relation.ADJACENT)

    def test_the_same_pair_cannot_appear_twice(self):
        """(a, b) and (b, a) are one edge. Two of them invites one hard "connected"
        and one soft "separated" — a contradiction better refused than resolved."""
        with pytest.raises(ValidationError, match="duplicate adjacency"):
            Program(
                rooms=[_room("hall"), _room("kitchen")],
                adjacencies=[
                    AdjacencySpec(a="hall", b="kitchen", relation=Relation.CONNECTED),
                    AdjacencySpec(a="kitchen", b="hall", relation=Relation.SEPARATED),
                ],
            )

    def test_a_program_needs_at_least_one_room(self):
        with pytest.raises(ValidationError):
            Program(rooms=[])


class TestBudgets:
    def test_min_area_is_what_feasibility_tests(self):
        """Stage ④ compares this against the envelope's built-area budget."""
        program = Program(rooms=[_room("a", min_area_sq_m=10.0), _room("b", min_area_sq_m=5.5)])
        assert program.min_area_sq_m == pytest.approx(15.5)

    def test_target_area_is_never_below_min_area(self):
        program = Program(rooms=[_room("a"), _room("b")])
        assert program.target_area_sq_m >= program.min_area_sq_m

    def test_floors_are_tiled_separately(self):
        """Stage ⑤ solves one rectangle per floor."""
        program = Program(rooms=[_room("a", floor=1), _room("b", floor=2), _room("c", floor=1)])
        assert [r.id for r in program.on_floor(1)] == ["a", "c"]
        assert [r.id for r in program.on_floor(2)] == ["b"]


class TestTheBundleIsTheFrontendContract:
    """`PlanBundle` is what crosses the backend/frontend edge, and `types.ts` mirrors
    it by hand. The mirror cannot be typechecked here — this machine runs Node 16 and
    the viewer needs 18+ — so the shape is asserted from the Python side instead.

    A renamed field would otherwise surface as a blank drawing in a browser nobody on
    this machine can open.
    """

    @staticmethod
    def _bundle():
        from app.envelope import build_envelope
        from app.llm import fallback
        from app.program import expand
        from app.refine import draw
        from app.solver import plan
        from app.validator import check

        brief = fallback.parse("40x60 3bhk in Bengaluru with pooja room")
        envelope = build_envelope(brief, allow_unverified=True)
        program = expand(brief, envelope)
        return check(draw(plan(brief, envelope, program, seed=7), envelope))

    def test_a_full_bundle_carries_the_drawing_and_the_findings(self):
        bundle = self._bundle()
        assert bundle.floors and bundle.reports
        assert len(bundle.floors) == len(bundle.layouts) == len(bundle.reports)

    def test_stage_five_alone_still_produces_a_valid_bundle(self):
        """`floors` defaults rather than being required: the bundle is stage ⑤'s
        output and has to stay valid before ⑥ has run. A viewer reading one draws
        rectangles, which is what it drew before ⑥ existed."""
        from app.envelope import build_envelope
        from app.llm import fallback
        from app.program import expand
        from app.solver import plan

        brief = fallback.parse("30x50 3bhk in Bengaluru")
        envelope = build_envelope(brief, allow_unverified=True)
        bundle = plan(brief, envelope, expand(brief, envelope), seed=7)
        assert bundle.floors == [] and bundle.reports == []

    def test_the_json_carries_every_field_the_viewer_reads(self):
        """Named explicitly rather than dumped-and-eyeballed. These are the keys
        `types.ts` declares, and each one is read by `FloorPlan.tsx` or `App.tsx`."""
        import json

        payload = json.loads(json.dumps(self._bundle().model_dump(mode="json")))

        assert {"brief_text", "program", "layouts", "floors", "reports", "seed"} <= set(payload)

        floor = payload["floors"][0]
        assert {"walls", "openings", "fixtures", "outside", "floor"} <= set(floor)
        assert {"id", "x1_m", "y1_m", "x2_m", "y2_m", "thickness_m", "kind",
                "rooms", "length_m", "is_vertical"} <= set(floor["walls"][0])
        assert {"wall_id", "kind", "offset_m", "width_m", "height_m",
                "connects"} <= set(floor["openings"][0])
        assert {"kind", "room_id", "x_min_m", "y_min_m", "x_max_m", "y_max_m",
                "faces"} <= set(floor["fixtures"][0])

        report = payload["reports"][0]
        assert {"floor", "findings", "checks_run", "errors", "ok"} <= set(report)

    def test_an_opening_can_be_located_without_reconstructing_geometry(self):
        """The viewer resolves `wall_id` and walks `offset_m` along the centreline.
        Both have to be present and consistent or a door lands in mid-air."""
        floor = self._bundle().floors[0]
        walls = {w.id: w for w in floor.walls}
        for opening in floor.openings:
            wall = walls[opening.wall_id]
            assert 0 <= opening.offset_m <= wall.length_m
            assert opening.width_m / 2 <= max(opening.offset_m,
                                              wall.length_m - opening.offset_m) + 1e-6
