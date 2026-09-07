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
