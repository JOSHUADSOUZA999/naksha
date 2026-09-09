"""Stage ⑥ REFINE — walls with thickness, and the openings in them.

Stage ⑤ tiles exactly, so the walls it produces have no thickness: two rooms share a
line. Everything here is about undoing that simplification correctly, and the failures
worth catching are the ones a person sees before any aggregate does — a door swinging
into the neighbour's setback, a house with no way in, a window in an internal wall.
"""

from __future__ import annotations

import pytest

from app.envelope import build_envelope
from app.ir.enums import OpeningKind, Relation, SpaceKind, WallKind
from app.ir.layout import TOLERANCE_M
from app.llm import fallback
from app.program import expand
from app.refine import refine
from app.solver import plan, solve

BRIEFS = {
    "30x40": "30x40 east facing site in Whitefield, Bengaluru, 3BHK with pooja room",
    "30x50": "30x50 3bhk in Bengaluru",
    "40x60": "40x60 3bhk in Bengaluru with pooja room",
    "50x80": "50x80 4bhk in Bengaluru with study",
}


@pytest.fixture(scope="module")
def floors():
    """One refined storey per reference brief, solved once and shared."""
    out = {}
    for name, text in BRIEFS.items():
        brief = fallback.parse(text)
        envelope = build_envelope(brief, allow_unverified=True)
        program = expand(brief, envelope)
        layout = solve(program, envelope, seed=7)[0]
        out[name] = (layout, program, refine(layout, program))
    return out


@pytest.mark.parametrize("name", list(BRIEFS))
class TestEveryBriefProducesADrawableFloor:
    def test_walls_bound_only_rooms_that_exist(self, floors, name):
        layout, program, floor = floors[name]
        known = {room.id for room in program.rooms}
        for wall in floor.walls:
            assert set(wall.rooms) <= known
            assert wall.length_m > TOLERANCE_M

    def test_interior_walls_separate_two_rooms_and_exterior_walls_one(self, floors, name):
        _, _, floor = floors[name]
        for wall in floor.walls:
            expected = 2 if wall.kind is WallKind.INTERIOR else 1
            assert len(wall.rooms) == expected, f"{wall.id} bounds {wall.rooms}"

    def test_exterior_walls_lie_on_the_boundary_and_interior_ones_do_not(self, floors, name):
        """A wall's kind decides its thickness, so getting it wrong is a drawing that
        shows a half-brick partition holding up the roof."""
        layout, _, floor = floors[name]

        def on_boundary(wall) -> bool:
            if wall.is_vertical:
                return (
                    abs(wall.x1_m - layout.x_min_m) <= TOLERANCE_M
                    or abs(wall.x1_m - layout.x_max_m) <= TOLERANCE_M
                )
            return (
                abs(wall.y1_m - layout.y_min_m) <= TOLERANCE_M
                or abs(wall.y1_m - layout.y_max_m) <= TOLERANCE_M
            )

        for wall in floor.walls:
            assert on_boundary(wall) == (wall.kind is WallKind.EXTERIOR), wall.id

    def test_the_house_has_a_way_in(self, floors, name):
        """A plan with no front door is not a plan.

        This caught a real one: the 40x60's foyer had a 1.27 m road-facing wall against
        the 1.30 m a 1.0 m entrance and its clearances need, so the house came out
        sealed. Openings now narrow before they give up.
        """
        _, _, floor = floors[name]
        entrances = [o for o in floor.openings if o.kind is OpeningKind.ENTRANCE]
        assert len(entrances) == 1

    def test_the_entrance_is_on_a_road_facing_wall(self, floors, name):
        """The road constraint in stage ⑤ puts the foyer on the street; a front door on
        the wall facing the neighbour would quietly undo it."""
        from app.ir.enums import Facing

        layout, _, floor = floors[name]
        entrance = next(o for o in floor.openings if o.kind is OpeningKind.ENTRANCE)
        wall = floor.by_id(entrance.wall_id)

        assert wall.kind is WallKind.EXTERIOR
        faces = {
            Facing.WEST: wall.is_vertical and abs(wall.x1_m - layout.x_min_m) <= TOLERANCE_M,
            Facing.EAST: wall.is_vertical and abs(wall.x1_m - layout.x_max_m) <= TOLERANCE_M,
            Facing.SOUTH: not wall.is_vertical and abs(wall.y1_m - layout.y_min_m) <= TOLERANCE_M,
            Facing.NORTH: not wall.is_vertical and abs(wall.y1_m - layout.y_max_m) <= TOLERANCE_M,
        }
        assert any(faces.get(edge, False) for edge in layout.road_edges)

    def test_windows_go_in_exterior_walls_only(self, floors, name):
        _, _, floor = floors[name]
        for opening in floor.openings:
            if opening.kind is OpeningKind.WINDOW:
                assert floor.by_id(opening.wall_id).kind is WallKind.EXTERIOR

    def test_no_two_openings_overlap_in_one_wall(self, floors, name):
        """Two doors sharing a reveal is not a wide door, it is a hole."""
        _, _, floor = floors[name]
        for wall in floor.walls:
            spans = sorted(
                (o.offset_m - o.width_m / 2, o.offset_m + o.width_m / 2)
                for o in floor.openings_in(wall.id)
            )
            for (_, end), (start, _) in zip(spans, spans[1:]):
                assert start >= end - TOLERANCE_M


class TestDoorsComeFromTheGraphNotFromGeometry:
    """`Relation` has carried ADJACENT and CONNECTED separately since the IR was
    written, precisely so ⑥ could tell a shared wall from a shared wall with a door."""

    @pytest.mark.parametrize("name", list(BRIEFS))
    def test_a_door_exists_only_where_the_programme_asked_for_one(self, floors, name):
        _, program, floor = floors[name]
        wanted = {
            frozenset({edge.a, edge.b})
            for edge in program.adjacencies
            if edge.relation is Relation.CONNECTED
        }
        for opening in floor.openings:
            if opening.kind is OpeningKind.DOOR:
                assert frozenset(opening.connects) in wanted

    @pytest.mark.parametrize("name", list(BRIEFS))
    def test_an_adjacent_edge_never_gets_a_door(self, floors, name):
        """The pooja room sits beside the hall; it does not open off it."""
        _, program, floor = floors[name]
        adjacent_only = {
            frozenset({edge.a, edge.b})
            for edge in program.adjacencies
            if edge.relation is Relation.ADJACENT
        }
        doors = {
            frozenset(o.connects) for o in floor.openings if o.kind is OpeningKind.DOOR
        }
        assert not (doors & adjacent_only)

    def test_an_unsatisfied_edge_gets_no_door_rather_than_an_invented_one(self, floors):
        """Stage ⑤ does not place every CONNECTED pair against each other, and `score`
        already counts each miss. Drawing a door through a third room would hide a
        defect the ranking is measuring."""
        _, program, floor = floors["40x60"]
        wanted = sum(
            1 for e in program.adjacencies if e.relation is Relation.CONNECTED
        )
        drawn = sum(1 for o in floor.openings if o.kind is OpeningKind.DOOR)
        assert drawn < wanted, "fixture must include an unsatisfied edge"

    def test_a_bathroom_door_is_narrower_than_a_bedroom_door(self, floors):
        _, program, floor = floors["50x80"]
        kinds = {room.id: room.kind for room in program.rooms}
        widths = {
            frozenset(o.connects): o.width_m
            for o in floor.openings
            if o.kind is OpeningKind.DOOR
        }
        service = [
            w for pair, w in widths.items()
            if any(kinds[r] in (SpaceKind.BATHROOM, SpaceKind.WC) for r in pair)
        ]
        habitable = [
            w for pair, w in widths.items()
            if all(kinds[r] not in (SpaceKind.BATHROOM, SpaceKind.WC) for r in pair)
        ]
        assert service and habitable
        assert max(service) <= min(habitable)


class TestRuleDataRatherThanConstants:
    def test_dimensions_come_from_the_ruleset(self):
        """Decision 4. Wall thickness and door widths are dimensions, and CLAUDE.md's
        rule is that dimensions come from `rules/`."""
        from app.rules import load_ruleset

        rules = load_ruleset("refine_v1").data
        assert rules["walls"]["exterior_thickness_m"] > rules["walls"]["interior_thickness_m"]
        assert rules["doors"]["entrance_width_m"] >= rules["doors"]["internal_width_m"]
        assert rules["doors"]["internal_width_m"] > rules["doors"]["service_width_m"]

    def test_thickness_follows_the_ruleset_not_a_literal(self, floors):
        from app.rules import load_ruleset

        walls = load_ruleset("refine_v1").data["walls"]
        _, _, floor = floors["30x50"]
        for wall in floor.walls:
            expected = (
                walls["exterior_thickness_m"]
                if wall.kind is WallKind.EXTERIOR
                else walls["interior_thickness_m"]
            )
            assert wall.thickness_m == expected


class TestTheDrawingCanBeRendered:
    def test_svg_carries_the_walls_and_the_openings(self, floors):
        from app.export.svg import render

        layout, program, floor = floors["40x60"]
        kinds = {room.id: room.kind.value for room in program.rooms}
        drawing = render(layout, kinds, title="t", refined=floor)

        from xml.etree import ElementTree

        ElementTree.fromstring(drawing)
        # One stroke per wall, plus one white punch-out per opening.
        assert drawing.count("<line") >= len(floor.walls) + len(floor.openings)
        assert "stroke-dasharray" in drawing, "doors need a swing arc to read as doors"

    def test_a_door_never_swings_outside_the_building(self, floors):
        """The first version chose a side by axis alone and the front door swung north,
        out of the plan and across the title block."""
        from app.export.svg import _inward

        layout, _, floor = floors["40x60"]

        def px(x_m, y_m):
            return (x_m, -y_m)          # the renderer's y-flip, scale-free

        for opening in floor.openings:
            if opening.kind is OpeningKind.WINDOW:
                continue
            wall = floor.by_id(opening.wall_id)
            nx, ny = _inward(wall, opening, layout, px)
            hx, hy = wall.point_at(opening.offset_m)
            # A step along the normal must land inside the plan, not outside it.
            step = min(opening.width_m, 0.5)
            tx, ty = hx + nx * step, hy - ny * step      # undo the flip for y
            assert layout.x_min_m - TOLERANCE_M <= tx <= layout.x_max_m + TOLERANCE_M
            assert layout.y_min_m - TOLERANCE_M <= ty <= layout.y_max_m + TOLERANCE_M

    def test_the_cli_writes_a_refined_drawing(self, tmp_path, capsys):
        from app.cli import main

        out = tmp_path / "plan.svg"
        assert main([
            "--svg", str(out), "--fallback-only", "--allow-unverified", "--seed", "7",
            BRIEFS["30x50"],
        ]) == 0
        assert "walls" in capsys.readouterr().err
        assert "stroke-dasharray" in out.read_text(encoding="utf-8")
