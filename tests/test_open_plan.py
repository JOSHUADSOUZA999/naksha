"""Living and dining as one space: two rectangles, no wall between.

A slicing tree cut JP Nagar's hall and dining room into strips side by side, 8'3" and
7'3" wide. An architect does not wall them apart: most new Indian houses open the living
room into the dining room. The solver still places two rectangles — its trees are
untouched — and everything downstream reads them as one space.
"""

from __future__ import annotations

from app.ir.enums import OpeningKind, Relation, SpaceKind
from app.ir.layout import Layout, PlacedRoom
from app.ir.plan import AdjacencySpec, Program, open_pair_shortfall_m
from app.program import spec_for, usable_sizes
from app.refine import refine
from app.solver.score import score
from app.validator import validate


def _pair(hall_w: float, dining_w: float, depth: float, relation: Relation):
    """A hall and a dining room side by side along x, on the north wall, with a corridor
    south of both so the floor has somewhere to be entered from."""
    layout = Layout(
        rooms=[
            PlacedRoom(room_id="hall", x_min_m=0, y_min_m=2, x_max_m=hall_w, y_max_m=2 + depth),
            PlacedRoom(room_id="dining", x_min_m=hall_w, y_min_m=2, x_max_m=hall_w + dining_w,
                       y_max_m=2 + depth),
            PlacedRoom(room_id="foyer", x_min_m=0, y_min_m=0, x_max_m=hall_w + dining_w, y_max_m=2),
        ],
        x_min_m=0, y_min_m=0, x_max_m=hall_w + dining_w, y_max_m=2 + depth,
    )
    program = Program(
        rooms=[spec_for(SpaceKind.HALL, "hall"), spec_for(SpaceKind.DINING, "dining"),
               spec_for(SpaceKind.FOYER, "foyer")],
        adjacencies=[
            AdjacencySpec(a="hall", b="dining", relation=relation),
            AdjacencySpec(a="foyer", b="hall", relation=Relation.CONNECTED),
        ],
    )
    return layout, program


class TestTheWallIsNotBuilt:
    def test_an_open_edge_becomes_an_opening_the_length_of_the_wall(self):
        layout, program = _pair(2.7, 2.4, 5.5, Relation.OPEN)
        floor = refine(layout, program)
        [opening] = [o for o in floor.openings if o.kind is OpeningKind.OPEN]
        wall = next(w for w in floor.walls if w.id == opening.wall_id)
        assert set(opening.connects) == {"hall", "dining"}
        assert opening.width_m == wall.length_m
        assert not [o for o in floor.openings if o.kind is OpeningKind.DOOR
                    and set(o.connects) == {"hall", "dining"}]

    def test_a_connected_edge_still_gets_a_door(self):
        layout, program = _pair(2.7, 2.4, 5.5, Relation.CONNECTED)
        floor = refine(layout, program)
        assert not [o for o in floor.openings if o.kind is OpeningKind.OPEN]

    def test_the_circulation_engine_walks_it_as_open(self):
        from app.circulation.graph import build
        from app.ir.enums import EdgeKind

        layout, program = _pair(2.7, 2.4, 5.5, Relation.OPEN)
        graph = build(layout, program, refine(layout, program))
        assert any(e.kind is EdgeKind.OPEN_CONNECTION and {e.a, e.b} == {"hall", "dining"}
                   for e in graph.edges)

    def test_the_offline_programme_opens_living_into_dining(self):
        from app.envelope import build_envelope
        from app.llm import fallback
        from app.program import expand

        brief = fallback.parse("40x60 3bhk in Bengaluru with pooja room")
        program = expand(brief, build_envelope(brief, allow_unverified=True))
        kinds = {r.id: r.kind for r in program.rooms}
        assert any(e.relation is Relation.OPEN
                   and {kinds[e.a], kinds[e.b]} == {SpaceKind.HALL, SpaceKind.DINING}
                   for e in program.adjacencies)


class TestOneSpaceIsFurnishedAsOne:
    def test_two_strips_open_to_each_other_hold_a_sofa_and_a_table(self):
        """8'3" and 7'3" strips, 17'6" deep: each is short alone, together they are not."""
        hall, dining = usable_sizes(SpaceKind.HALL), usable_sizes(SpaceKind.DINING)
        assert open_pair_shortfall_m(hall, dining, 2.51 + 2.21 + 0.115, 5.6) == 0.0

    def test_side_by_side_and_one_behind_the_other_are_both_tried(self):
        hall, dining = usable_sizes(SpaceKind.HALL), usable_sizes(SpaceKind.DINING)
        assert open_pair_shortfall_m(hall, dining, 5.4, 3.6) == 0.0     # side by side
        assert open_pair_shortfall_m(hall, dining, 3.6, 6.0) == 0.0     # in line
        assert open_pair_shortfall_m(hall, dining, 2.5, 4.0) > 1.0      # neither

    def test_the_walled_strips_are_reported_and_the_open_ones_are_not(self):
        closed = _pair(2.75, 2.45, 5.9, Relation.CONNECTED)
        opened = _pair(2.75, 2.45, 5.9, Relation.OPEN)
        closed_report = validate(*closed, refine(*closed))
        open_report = validate(*opened, refine(*opened))
        assert {r for f in closed_report.by_check("furnish") for r in f.rooms} >= {"hall"}
        assert open_report.by_check("furnish") == []

    def test_the_solver_prices_the_pair_not_the_strips(self):
        _, _, closed = score(*_pair(2.75, 2.45, 5.9, Relation.CONNECTED))
        _, _, opened = score(*_pair(2.75, 2.45, 5.9, Relation.OPEN))
        assert any("hall is" in r and "short of the furniture" in r for r in closed)
        assert not any("furniture" in r for r in opened)


class TestOneSpaceIsLitAsOne:
    def test_a_hall_open_to_a_glazed_dining_room_is_not_refused(self):
        """Lit through either part. Walled off with no window of its own, it is refused."""
        layout = Layout(
            rooms=[
                PlacedRoom(room_id="foyer", x_min_m=0, y_min_m=0, x_max_m=2, y_max_m=4),
                PlacedRoom(room_id="hall", x_min_m=2, y_min_m=0, x_max_m=5, y_max_m=4),
                PlacedRoom(room_id="dining", x_min_m=5, y_min_m=0, x_max_m=8, y_max_m=4),
            ],
            x_min_m=0, y_min_m=0, x_max_m=8, y_max_m=4,
        )
        # The hall touches the outside on its north and south walls here, so strip its
        # windows off by hand: the rule under test is the sharing, not the placing.
        for relation, refused in ((Relation.OPEN, False), (Relation.CONNECTED, True)):
            program = Program(
                rooms=[spec_for(SpaceKind.FOYER, "foyer"), spec_for(SpaceKind.HALL, "hall"),
                       spec_for(SpaceKind.DINING, "dining")],
                adjacencies=[AdjacencySpec(a="hall", b="dining", relation=relation),
                             AdjacencySpec(a="foyer", b="hall", relation=Relation.CONNECTED)],
            )
            floor = refine(layout, program)
            floor = floor.model_copy(update={"openings": [
                o for o in floor.openings
                if not (o.kind is OpeningKind.WINDOW and o.connects == ["hall"])
            ]})
            from app.validator import _light

            blind = [f for f in _light(program, floor) if "hall has no window" in f.message]
            assert bool(blind) is refused, relation
