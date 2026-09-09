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


class TestFixturesMakeARoomReadAsItsKind:
    """A 3.5 m² blue rectangle is a bathroom only to whoever placed it.

    These are the conventional marks — a bowl, four burners, a pillow band — and the
    job is a defensible arrangement for the user to push around, not solved furniture
    layout. CLAUDE.md's v1 editor is "adjustment, not authoring".
    """

    @pytest.mark.parametrize("name", list(BRIEFS))
    def test_no_fixture_escapes_its_room(self, floors, name):
        layout, _, floor = floors[name]
        for fixture in floor.fixtures:
            room = layout.by_id(fixture.room_id)
            assert room is not None
            assert room.x_min_m - TOLERANCE_M <= fixture.x_min_m
            assert fixture.x_max_m <= room.x_max_m + TOLERANCE_M
            assert room.y_min_m - TOLERANCE_M <= fixture.y_min_m
            assert fixture.y_max_m <= room.y_max_m + TOLERANCE_M

    @pytest.mark.parametrize("name", list(BRIEFS))
    def test_nothing_stands_where_a_door_swings(self, floors, name):
        """The one rule that cannot be left to the user.

        A WC drawn under the door is not a starting point, it is a mistake they have to
        notice before they can fix it. A fixture that will not fit clear is dropped.
        """
        from app.refine import _door_swings, _hits

        layout, _, floor = floors[name]
        for fixture in floor.fixtures:
            room = layout.by_id(fixture.room_id)
            box = (fixture.x_min_m, fixture.y_min_m, fixture.x_max_m, fixture.y_max_m)
            for zone in _door_swings(room, floor.walls, floor.openings):
                assert not _hits(box, zone), f"{fixture.kind.value} in {room.room_id}"

    @pytest.mark.parametrize("name", list(BRIEFS))
    def test_free_standing_fixtures_do_not_overlap_each_other(self, floors, name):
        """A bed through a wardrobe is not a small drawing error, it is two fixtures
        in the same cubic metre."""
        from app.ir.enums import FixtureKind
        from app.refine import _hits

        _, _, floor = floors[name]
        standing = [
            f for f in floor.fixtures
            if f.kind not in (FixtureKind.SINK, FixtureKind.STOVE)
        ]
        for i, a in enumerate(standing):
            for b in standing[i + 1:]:
                if a.room_id != b.room_id:
                    continue
                assert not _hits(
                    (a.x_min_m, a.y_min_m, a.x_max_m, a.y_max_m),
                    (b.x_min_m, b.y_min_m, b.x_max_m, b.y_max_m),
                ), f"{a.kind.value} clashes with {b.kind.value} in {a.room_id}"

    def test_a_bathroom_gets_sanitaryware_and_a_bedroom_gets_a_bed(self, floors):
        from app.ir.enums import FixtureKind

        _, program, floor = floors["50x80"]
        kinds = {room.id: room.kind for room in program.rooms}
        by_room: dict[str, set] = {}
        for fixture in floor.fixtures:
            by_room.setdefault(fixture.room_id, set()).add(fixture.kind)

        baths = [r for r, k in kinds.items() if k is SpaceKind.BATHROOM]
        beds = [r for r, k in kinds.items()
                if k in (SpaceKind.BEDROOM, SpaceKind.MASTER_BEDROOM)]
        assert any(FixtureKind.WC in by_room.get(r, set()) for r in baths)
        assert any(
            by_room.get(r, set()) & {FixtureKind.BED, FixtureKind.SINGLE_BED}
            for r in beds
        )

    def test_the_sink_and_hob_sit_in_the_counter(self, floors):
        """A sink on one wall and the counter on another is a kitchen nobody cooks in."""
        from app.ir.enums import FixtureKind
        from app.refine import _hits

        _, _, floor = floors["40x60"]
        counter = next(
            (f for f in floor.fixtures if f.kind is FixtureKind.COUNTER), None
        )
        assert counter is not None, "the fixture must have a counter to sit in"
        for fixture in floor.fixtures:
            if fixture.kind in (FixtureKind.SINK, FixtureKind.STOVE):
                assert fixture.room_id == counter.room_id
                assert _hits(
                    (fixture.x_min_m, fixture.y_min_m, fixture.x_max_m, fixture.y_max_m),
                    (counter.x_min_m, counter.y_min_m, counter.x_max_m, counter.y_max_m),
                )

    def test_the_schedule_is_rule_data(self):
        """Which fixtures a room gets is a judgment that changes without the code."""
        from app.rules import load_ruleset

        rules = load_ruleset("refine_v1").data
        assert "wc" in rules["schedules"]["bathroom"]
        assert rules["fixtures"]["bed"]["width_m"] > rules["fixtures"]["single_bed"]["width_m"]

    def test_furnishing_is_deterministic(self, floors):
        """A drawing that reshuffles itself between runs is one nobody can discuss."""
        layout, program, floor = floors["30x50"]
        again = refine(layout, program)
        assert [f.model_dump() for f in again.fixtures] == [
            f.model_dump() for f in floor.fixtures
        ]

    def test_the_drawing_carries_them(self, floors):
        from app.export.svg import render

        layout, program, floor = floors["40x60"]
        kinds = {room.id: room.kind.value for room in program.rooms}
        drawing = render(layout, kinds, title="t", refined=floor)
        assert "<ellipse" in drawing, "a WC and a basin need bowls to read as fixtures"
        assert drawing.count("<circle") >= 4, "the hob needs its burners"


class TestWallsChangeWhatALegalRoomIs:
    """Stage ⑤ measures to wall centrelines; the bye-laws mean clear internal size.

    DECISIONS question 8. Not fixed here — fixing it tightens every brief and may make
    a 30x40 3BHK infeasible outright, which is a product decision. What is fixed is the
    silence: the discrepancy is reported and the labels show the honest number.
    """

    def test_the_clear_area_is_smaller_than_the_tiled_one(self, floors):
        layout, _, floor = floors["40x60"]
        for room in layout.rooms:
            clear = floor.clear_area_sq_m(room.room_id)
            assert clear is not None
            assert clear < room.area_sq_m

    def test_each_side_is_inset_by_its_own_wall(self, floors):
        """A boundary side loses half of 230 mm, an internal one half of 115 mm.
        Using one figure for both would misreport every room on the perimeter."""
        from app.rules import load_ruleset

        walls = load_ruleset("refine_v1").data["walls"]
        outer, inner = walls["exterior_thickness_m"] / 2, walls["interior_thickness_m"] / 2
        layout, _, floor = floors["40x60"]

        for room in layout.rooms:
            x_min, _, _, _ = floor.clear[room.room_id]
            on_boundary = abs(room.x_min_m - layout.x_min_m) <= TOLERANCE_M
            expected = room.x_min_m + (outer if on_boundary else inner)
            assert abs(x_min - expected) < 1e-9

    def test_the_breach_is_reported_rather_than_hidden(self, floors):
        """Three of the four reference briefs report zero unbuildable rooms and are
        not clean once the walls are real. A wrong number nobody can see is the exact
        failure mode this codebase keeps finding."""
        from app.refine import breaches

        layout, program, floor = floors["30x50"]
        assert layout.unbuildable == 0, "fixture must be a plan the solver calls clean"
        assert breaches(floor, program), "and it must still breach a minimum"

    def test_fixtures_stand_on_the_clear_floor_not_the_tiled_one(self, floors):
        """Furnishing against stage ⑤'s rectangle pushes everything into the masonry."""
        layout, _, floor = floors["50x80"]
        for fixture in floor.fixtures:
            x_min, y_min, x_max, y_max = floor.clear[fixture.room_id]
            assert fixture.x_min_m >= x_min - 1e-9
            assert fixture.y_min_m >= y_min - 1e-9
            assert fixture.x_max_m <= x_max + 1e-9
            assert fixture.y_max_m <= y_max + 1e-9

    def test_the_label_shows_the_clear_area(self, floors):
        from app.export.svg import render

        layout, program, floor = floors["40x60"]
        kinds = {room.id: room.kind.value for room in program.rooms}
        drawing = render(layout, kinds, title="t", refined=floor)

        big = max(layout.rooms, key=lambda r: r.area_sq_m)
        clear = floor.clear_area_sq_m(big.room_id)
        assert f"{clear:.1f} m²" in drawing
        assert f"{big.area_sq_m:.1f} m²" not in drawing
