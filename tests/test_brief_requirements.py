"""What the brief asks for is a constraint, not a sentence.

"My parents are elderly and need a bedroom on the ground floor near the entrance" reached
stage ③ as a floor number and a clause in `why`, and JP Nagar drew the parents' bedroom at
the back of the house. Stage ③ now writes a `NEAR` edge; ⑤ estimates the walk and grows
trees that can meet it; ⑦ walks it through the doors; the judge ranks an unmet request
ahead of every other major finding.
"""

from __future__ import annotations

import random

from app.ir.enums import Facing, Relation, SpaceKind
from app.ir.layout import Layout, PlacedRoom
from app.ir.plan import AdjacencySpec, Program
from app.llm.client import load_prompt
from app.program import spec_for
from app.rules import load_ruleset
from app.solver import slicing
from app.solver.score import _programme_walk, score


def _row(*rooms):
    """Rooms side by side along x, 3 m each and 4 m deep."""
    placed = [
        PlacedRoom(room_id=rid, x_min_m=3.0 * i, y_min_m=0, x_max_m=3.0 * (i + 1), y_max_m=4)
        for i, (rid, _) in enumerate(rooms)
    ]
    layout = Layout(rooms=placed, x_min_m=0, y_min_m=0, x_max_m=3.0 * len(rooms), y_max_m=4)
    return layout, [spec_for(kind, rid) for rid, kind in rooms]


class TestTheModelIsToldHowToSayIt:
    def test_the_prompt_teaches_near(self):
        prompt = load_prompt("program_v3")
        assert "near" in prompt.text and "foyer" in prompt.text

    def test_the_limit_is_data(self):
        assert load_ruleset("circulation_v1").data["near"]["max_walk_m"] == 10.0


class TestTheSolverEstimatesTheWalk:
    def test_a_room_off_the_corridor_beside_the_foyer_is_near(self):
        layout, specs = _row(("foyer", SpaceKind.FOYER), ("corridor", SpaceKind.CORRIDOR),
                             ("bed", SpaceKind.BEDROOM))
        program = Program(rooms=specs, adjacencies=[
            AdjacencySpec(a="corridor", b="bed", relation=Relation.CONNECTED),
            AdjacencySpec(a="bed", b="foyer", relation=Relation.NEAR, hard=True),
        ])
        assert _programme_walk(layout, program, "foyer", "bed") <= 10.0
        assert not any("not near" in r for r in score(layout, program)[2])

    def test_a_room_at_the_end_of_a_long_row_is_not(self):
        layout, specs = _row(("foyer", SpaceKind.FOYER), ("c1", SpaceKind.CORRIDOR),
                             ("c2", SpaceKind.CORRIDOR), ("c3", SpaceKind.CORRIDOR),
                             ("c4", SpaceKind.CORRIDOR), ("bed", SpaceKind.BEDROOM))
        program = Program(rooms=specs, adjacencies=[
            AdjacencySpec(a="c4", b="bed", relation=Relation.CONNECTED),
            AdjacencySpec(a="bed", b="foyer", relation=Relation.NEAR, hard=True),
        ])
        assert _programme_walk(layout, program, "foyer", "bed") > 10.0
        assert any("bed is not near foyer" in r for r in score(layout, program)[2])

    def test_a_bedroom_entered_off_the_dining_room_is_no_short_way(self):
        """The circulation engine grades that major; the estimate must not count it."""
        layout, specs = _row(("foyer", SpaceKind.FOYER), ("dining", SpaceKind.DINING),
                             ("bed", SpaceKind.BEDROOM))
        program = Program(rooms=specs, adjacencies=[
            AdjacencySpec(a="bed", b="foyer", relation=Relation.NEAR, hard=True),
        ])
        assert _programme_walk(layout, program, "foyer", "bed") == float("inf")

    def test_a_shared_wall_without_a_door_is_not_near(self):
        """A bedroom against the foyer with no door the programme asked for is as far as
        its real door makes it — which is how ⑦ walks it."""
        layout, specs = _row(("foyer", SpaceKind.FOYER), ("bed", SpaceKind.BEDROOM))
        program = Program(rooms=specs, adjacencies=[
            AdjacencySpec(a="bed", b="foyer", relation=Relation.NEAR, hard=True),
        ])
        assert _programme_walk(layout, program, "foyer", "bed") == float("inf")


class TestTheTreeCanMeetIt:
    def test_the_near_room_takes_the_road_end_of_its_row(self):
        rooms = [spec_for(SpaceKind.HALL, "hall"), spec_for(SpaceKind.CORRIDOR, "corridor"),
                 spec_for(SpaceKind.BEDROOM, "parents"), spec_for(SpaceKind.BEDROOM, "bed2"),
                 spec_for(SpaceKind.KITCHEN, "kitchen")]
        weights = {r.id: r.target_area_sq_m for r in rooms}
        for seed in range(10):
            tree = slicing.near_spine_tree(rooms, random.Random(seed), weights, Facing.EAST, {"parents"})
            placed = {p.room_id: p for p in slicing.place(tree, 0, 0, 12, 10)}
            same_row = [p for p in placed.values() if p.room_id != "parents"
                        and abs(p.y_min_m - placed["parents"].y_min_m) < 1e-6
                        and p.room_id != "corridor"]
            assert all(placed["parents"].x_max_m >= p.x_max_m - 1e-6 for p in same_row)
            assert placed["hall"].x_max_m == max(p.x_max_m for p in placed.values()
                                                 if abs(p.y_min_m - placed["hall"].y_min_m) < 1e-6)


    def test_the_rooms_open_to_the_hall_follow_it(self):
        """Placed anywhere in the row, JP Nagar's dining room landed with the kitchen
        between it and the hall, and the open living space could not be built."""
        rooms = [spec_for(SpaceKind.HALL, "hall"), spec_for(SpaceKind.CORRIDOR, "corridor"),
                 spec_for(SpaceKind.BEDROOM, "parents"), spec_for(SpaceKind.DINING, "dining"),
                 spec_for(SpaceKind.KITCHEN, "kitchen"), spec_for(SpaceKind.STORE, "store")]
        weights = {r.id: r.target_area_sq_m for r in rooms}
        for seed in range(10):
            tree = slicing.near_spine_tree(rooms, random.Random(seed), weights, Facing.EAST,
                                           {"parents"}, {"dining"})
            placed = {p.room_id: p for p in slicing.place(tree, 0, 0, 12, 10)}
            assert placed["hall"].touches(placed["dining"]), seed


class TestStageSevenWalksIt:
    def test_an_unmet_request_is_a_major_finding(self):

        from app.validator import _brief

        layout, specs = _row(("foyer", SpaceKind.FOYER), ("c1", SpaceKind.CORRIDOR),
                             ("c2", SpaceKind.CORRIDOR), ("c3", SpaceKind.CORRIDOR),
                             ("c4", SpaceKind.CORRIDOR), ("bed", SpaceKind.BEDROOM))
        program = Program(rooms=specs, adjacencies=[
            AdjacencySpec(a="bed", b="foyer", relation=Relation.NEAR, hard=True),
        ])
        from app.refine import refine

        floor = refine(layout, program)
        [finding] = _brief(layout, program, floor)
        assert finding.grade.value == "major" and set(finding.rooms) == {"bed", "foyer"}

    def test_the_judge_ranks_it_before_other_majors(self):
        import inspect

        from app.validator import judge

        source = inspect.getsource(judge)
        assert source.index('len(report.by_check("brief"))') < source.index(
            'graded["circulation", Grade.MAJOR] + graded["other", Grade.MAJOR]'
        )


class TestTheModelCannotAskForTheImpossible:
    def test_a_car_bay_near_the_foyer_is_dropped_at_the_merge(self):
        """The live model asked for one beside the parents' bedroom. A bay has no door into
        the house, so no walk could ever meet it; the parents' request survives."""
        from app.ir.plan import ProgramDraft, RoomRequest
        from app.llm.program import _merge

        draft = ProgramDraft(
            rooms=[RoomRequest(id="foyer", kind=SpaceKind.FOYER),
                   RoomRequest(id="bed1", kind=SpaceKind.BEDROOM),
                   RoomRequest(id="park1", kind=SpaceKind.CAR_PARKING)],
            adjacencies=[AdjacencySpec(a="bed1", b="foyer", relation=Relation.NEAR, hard=True),
                         AdjacencySpec(a="park1", b="foyer", relation=Relation.NEAR, hard=True)],
        )
        near = [(e.a, e.b) for e in _merge(draft).adjacencies if e.relation is Relation.NEAR]
        assert near == [("bed1", "foyer")]


class TestTheServiceRoomsShareTheKitchensSlot:
    def test_the_utility_opens_off_the_kitchen_which_keeps_the_dining_room(self):
        """Across the corridor from the kitchen, JP Nagar's utility was two majors; after
        it in the row, the row grew too long to dimension."""
        rooms = [spec_for(SpaceKind.HALL, "hall"), spec_for(SpaceKind.CORRIDOR, "corridor"),
                 spec_for(SpaceKind.BEDROOM, "parents"), spec_for(SpaceKind.DINING, "dining"),
                 spec_for(SpaceKind.KITCHEN, "kitchen"), spec_for(SpaceKind.UTILITY, "utility"),
                 spec_for(SpaceKind.BATHROOM, "bath")]
        weights = {r.id: r.target_area_sq_m for r in rooms}
        for seed in range(10):
            tree = slicing.near_spine_tree(rooms, random.Random(seed), weights, Facing.EAST,
                                           {"parents"}, {"dining", "kitchen"}, {"kitchen": ["utility"]})
            placed = {p.room_id: p for p in slicing.place(tree, 0, 0, 14, 10)}
            assert placed["kitchen"].touches(placed["utility"]), seed
            assert placed["kitchen"].touches(placed["dining"]), seed
            assert placed["utility"].touches(placed["corridor"]), seed

    def test_only_rooms_the_kitchen_alone_serves_are_tucked(self):
        from app.solver import _served_by_kitchen

        rooms = [spec_for(SpaceKind.KITCHEN, "kitchen"), spec_for(SpaceKind.UTILITY, "utility"),
                 spec_for(SpaceKind.STORE, "store"), spec_for(SpaceKind.CORRIDOR, "corridor")]
        program = Program(rooms=rooms, adjacencies=[
            AdjacencySpec(a="kitchen", b="utility", relation=Relation.CONNECTED),
            AdjacencySpec(a="corridor", b="store", relation=Relation.CONNECTED),
        ])
        assert _served_by_kitchen(program, rooms) == {"kitchen": ["utility"]}
