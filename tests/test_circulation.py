"""The circulation engine: can a person move through the house the way it is meant to be used?

Every storey here is drawn by hand, with rooms as rectangles and doors placed between named
rooms, so each case tests the engine and not whatever the solver happens to draw today.
The numbered cases are the ten the circulation brief asked for. The rest pin the
corrections agreed along the way: reachable is not properly reached, an en-suite is
judged from its own bedroom, the front door leads through a foyer into the house, a
corridor is judged by its work, and a stair arrives on a landing.

JP Nagar is the one real plan, pinned as data in `golden/circulation_jpnagar.json`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.circulation import evaluate, semantics
from app.circulation.graph import ENTRY
from app.circulation.routes import reachable
from app.ir.enums import (
    CirculationRole,
    CorridorVerdict,
    EdgeKind,
    Facing,
    Grade,
    Health,
    OpeningKind,
    Relation,
    Zone,
)
from app.ir.enums import SpaceKind as K
from app.ir.enums import WallKind
from app.ir.layout import Layout, PlacedRoom
from app.ir.plan import AdjacencySpec, Program
from app.ir.refined import Opening, RefinedFloor
from app.program import spec_for
from app.refine import _clear_rect, _walls
from app.rules import load_ruleset

GOLDEN = Path(__file__).parent / "golden" / "circulation_jpnagar.json"


def storey(rooms, doors, *, front=None, gates=(), floor=1, connected=(), shafts=None):
    """A storey drawn by hand.

    `rooms` are `(id, kind, (x_min, y_min, x_max, y_max))` tiling a rectangle; each pair
    in `doors` gets a door in the wall the two rooms share; `front` and `gates` open onto
    a road along the south edge; `connected` is stage ③'s en-suite edge.
    """
    rules = load_ruleset("refine_v1").data["walls"]
    placed = [
        PlacedRoom(room_id=rid, x_min_m=x1, y_min_m=y1, x_max_m=x2, y_max_m=y2)
        for rid, _, (x1, y1, x2, y2) in rooms
    ]
    layout = Layout(
        rooms=placed,
        x_min_m=min(p.x_min_m for p in placed),
        y_min_m=min(p.y_min_m for p in placed),
        x_max_m=max(p.x_max_m for p in placed),
        y_max_m=max(p.y_max_m for p in placed),
        floor=floor,
        shafts=shafts or {},
        road_edges=[Facing.SOUTH],
    )
    program = Program(
        rooms=[spec_for(kind, rid, floor=floor) for rid, kind, _ in rooms],
        adjacencies=[
            AdjacencySpec(a=a, b=b, relation=Relation.CONNECTED) for a, b in connected
        ],
    )
    walls = _walls(layout, rules)
    openings = [_door(walls, a, b) for a, b in doors]
    if front is not None:
        openings.append(_on_the_road(walls, layout, front, OpeningKind.ENTRANCE))
    openings += [_on_the_road(walls, layout, bay, OpeningKind.VEHICLE) for bay in gates]
    drawn = RefinedFloor(
        floor=floor,
        walls=walls,
        openings=openings,
        clear={p.room_id: _clear_rect(p, layout, rules) for p in placed},
    )
    return layout, program, drawn


def _door(walls, a, b):
    shared = [w for w in walls if w.kind is WallKind.INTERIOR and set(w.rooms) == {a, b}]
    assert shared, f"{a} and {b} share no wall"
    wall = max(shared, key=lambda w: w.length_m)
    return Opening(
        wall_id=wall.id, kind=OpeningKind.DOOR, offset_m=wall.length_m / 2,
        width_m=min(0.9, wall.length_m * 0.8), connects=[a, b],
    )


def _on_the_road(walls, layout, room, kind):
    road = [
        w for w in walls
        if w.kind is WallKind.EXTERIOR and w.rooms == [room] and not w.is_vertical
        and abs(w.y1_m - layout.y_min_m) < 1e-6
    ]
    assert road, f"{room} has no wall on the road"
    wall = max(road, key=lambda w: w.length_m)
    return Opening(
        wall_id=wall.id, kind=kind, offset_m=wall.length_m / 2,
        width_m=min(1.0, wall.length_m * 0.8), connects=[room],
    )


def judged(*args, **kwargs):
    return evaluate(*storey(*args, **kwargs))


def rules_of(result, grade=None):
    return [f.rule for f in result.findings if grade is None or f.grade is grade]


def only(result, rule):
    found = [f for f in result.findings if f.rule == rule]
    assert len(found) == 1, [f"{f.rule}: {f.message}" for f in result.findings]
    return found[0]


def without(doors, *pairs):
    return [door for door in doors if door not in pairs]


# Upstairs: a stair, a landing corridor across the storey, every room off the landing.
UPSTAIRS = [
    ("stair", K.STAIRCASE, (0, 0, 3, 3.5)),
    ("bed3", K.BEDROOM, (3, 0, 10, 3.5)),
    ("corridor", K.CORRIDOR, (0, 3.5, 10, 5)),
    ("master", K.MASTER_BEDROOM, (0, 5, 4, 8)),
    ("bed2", K.BEDROOM, (4, 5, 7, 8)),
    ("bath", K.BATHROOM, (7, 5, 10, 8)),
]
OFF_THE_LANDING = [
    ("stair", "corridor"), ("corridor", "bed3"), ("corridor", "master"),
    ("corridor", "bed2"), ("corridor", "bath"),
]
THROUGH_THE_MASTER = without(OFF_THE_LANDING, ("corridor", "bed2")) + [("master", "bed2")]

# The same storey with a corridor far wider than its doors need.
WIDE_LANDING = [
    ("stair", K.STAIRCASE, (0, 0, 3, 3.5)),
    ("bed3", K.BEDROOM, (3, 0, 12, 3.5)),
    ("corridor", K.CORRIDOR, (0, 3.5, 12, 5.7)),
    ("master", K.MASTER_BEDROOM, (0, 5.7, 4.5, 9.5)),
    ("bed2", K.BEDROOM, (4.5, 5.7, 8.5, 9.5)),
    ("bath", K.BATHROOM, (8.5, 5.7, 12, 9.5)),
]

# Downstairs: the front door into a foyer, the foyer into the hall, the hall into the
# dining room, the dining room into the kitchen, and a guest WC off the hall.
GROUND = [
    ("foyer", K.FOYER, (0, 0, 2.5, 3)),
    ("hall", K.HALL, (2.5, 0, 12, 3)),
    ("kitchen", K.KITCHEN, (0, 3, 4, 8)),
    ("dining", K.DINING, (4, 3, 9, 8)),
    ("wc", K.WC, (9, 3, 12, 8)),
]
IN_SEQUENCE = [("foyer", "hall"), ("hall", "dining"), ("dining", "kitchen"), ("hall", "wc")]

# Downstairs with a bedroom off the hall and the only bathroom beyond it.
GUEST = [
    ("foyer", K.FOYER, (0, 0, 2.5, 3)),
    ("hall", K.HALL, (2.5, 0, 12, 3)),
    ("kitchen", K.KITCHEN, (0, 3, 4, 8)),
    ("dining", K.DINING, (4, 3, 8, 8)),
    ("bed1", K.BEDROOM, (8, 3, 12, 6)),
    ("bath", K.BATHROOM, (8, 6, 12, 8)),
]
GUEST_DOORS = [
    ("foyer", "hall"), ("hall", "dining"), ("dining", "kitchen"),
    ("hall", "bed1"), ("bed1", "bath"),
]


class TestTheTenCases:
    def test_1_a_bedroom_reached_through_a_bedroom_fails(self):
        result = judged(UPSTAIRS, THROUGH_THE_MASTER, floor=2)
        access = only(result, "circulation.access")
        assert access.grade is Grade.CRITICAL
        assert access.rooms == ["bed2"] and "master" in access.path
        assert not result.summary.passed and result.summary.health is Health.FAIL

    def test_2_a_bedroom_reached_through_a_bathroom_fails(self):
        doors = without(OFF_THE_LANDING, ("corridor", "bed2")) + [("bath", "bed2")]
        result = judged(UPSTAIRS, doors, floor=2)
        access = only(result, "circulation.access")
        assert access.grade is Grade.CRITICAL
        assert access.rooms == ["bed2"] and "bath" in access.path
        assert result.summary.health is Health.FAIL

    def test_3_every_bedroom_off_the_corridor_passes(self):
        result = judged(UPSTAIRS, OFF_THE_LANDING, floor=2)
        assert result.summary.passed and result.summary.health is Health.GOOD
        assert result.summary.critical == result.summary.major == 0
        assert all(row.status == "appropriate" for row in result.summary.access)

    def test_4_living_dining_kitchen_in_sequence_is_good(self):
        result = judged(GROUND, IN_SEQUENCE, front="foyer")
        walks = {j.id: j for j in result.summary.journeys}
        assert walks["living_to_dining"].score == walks["dining_to_kitchen"].score == 100
        assert "circulation.relationship" not in rules_of(result)
        assert result.summary.dimensions.relationships == 100
        assert result.summary.health is Health.GOOD

    def test_5_living_corridor_dining_corridor_kitchen_is_a_major_inefficiency(self):
        rooms = [
            ("foyer", K.FOYER, (0, 0, 2.5, 3)),
            ("hall", K.HALL, (2.5, 0, 12, 3)),
            ("corridor", K.CORRIDOR, (0, 3, 12, 4.5)),
            ("kitchen", K.KITCHEN, (0, 4.5, 4, 9)),
            ("dining", K.DINING, (4, 4.5, 9, 9)),
            ("wc", K.WC, (9, 4.5, 12, 9)),
        ]
        doors = [
            ("foyer", "hall"), ("hall", "corridor"), ("corridor", "dining"),
            ("corridor", "kitchen"), ("corridor", "wc"),
        ]
        result = judged(rooms, doors, front="foyer")
        indirect = {
            frozenset(f.rooms) for f in result.findings
            if f.rule == "circulation.relationship" and f.grade is Grade.MAJOR
        }
        assert indirect == {frozenset({"hall", "dining"}), frozenset({"dining", "kitchen"})}
        assert result.summary.passed  # inefficient, not broken

    def test_6_a_guest_through_a_bedroom_to_a_shared_bathroom_fails(self):
        result = judged(GUEST, GUEST_DOORS, front="foyer")
        access = only(result, "circulation.access")
        assert access.grade is Grade.CRITICAL and access.rooms == ["bath"]
        assert not result.summary.passed

    def test_6_a_guest_through_a_bedroom_to_its_own_en_suite_is_major(self):
        """The only bathroom downstairs belongs to the bedroom. It is properly reached
        from there, but a visitor has to walk through someone's room to use it."""
        result = judged(GUEST, GUEST_DOORS, front="foyer", connected=[("bed1", "bath")])
        crossing = only(result, "circulation.zone_crossing")
        assert crossing.grade is Grade.MAJOR and "bed1" in crossing.rooms
        assert result.summary.passed

    def test_7_a_stair_into_a_bedroom_that_leads_on_fails(self):
        rooms = [
            ("stair", K.STAIRCASE, (0, 0, 3, 3.5)),
            ("bed1", K.BEDROOM, (3, 0, 10, 3.5)),
            ("corridor", K.CORRIDOR, (0, 3.5, 10, 5)),
            ("bed2", K.BEDROOM, (0, 5, 6, 8)),
            ("bath", K.BATHROOM, (6, 5, 10, 8)),
        ]
        doors = [("stair", "bed1"), ("bed1", "corridor"), ("corridor", "bed2"), ("corridor", "bath")]
        result = judged(rooms, doors, floor=2)
        arrival = only(result, "circulation.stair_arrival")
        assert arrival.grade is Grade.MAJOR and "bed1" in arrival.rooms
        access = only(result, "circulation.access")
        assert access.grade is Grade.CRITICAL and {"bed2", "bath"} <= set(access.rooms)
        assert result.summary.health is Health.FAIL

    def test_7_a_stair_into_a_bedroom_with_nothing_beyond_is_major(self):
        """A room on the roof: the stair arrives in it, and nobody walks through it."""
        rooms = [
            ("stair", K.STAIRCASE, (0, 0, 3, 4)),
            ("bed1", K.BEDROOM, (3, 0, 8, 4)),
            ("bath", K.BATHROOM, (8, 0, 10.5, 4)),
        ]
        result = judged(
            rooms, [("stair", "bed1"), ("bed1", "bath")], floor=2, connected=[("bed1", "bath")]
        )
        assert only(result, "circulation.stair_arrival").grade is Grade.MAJOR
        assert result.summary.passed and result.summary.critical == 0

    def test_8_an_oversized_corridor_with_good_circulation_is_penalised_not_failed(self):
        result = judged(WIDE_LANDING, OFF_THE_LANDING, floor=2)
        assert only(result, "circulation.corridor").grade is Grade.MINOR
        [corridor] = result.summary.corridors
        assert corridor.verdict is CorridorVerdict.INEFFICIENT and corridor.essential
        assert result.summary.passed and result.summary.major == 0
        assert result.summary.dimensions.efficiency < 100

    def test_9_a_small_corridor_does_not_save_terrible_access(self):
        rooms = [
            ("stair", K.STAIRCASE, (0, 0, 3, 3.5)),
            ("corridor", K.CORRIDOR, (3, 0, 4.2, 3.5)),
            ("bed1", K.BEDROOM, (4.2, 0, 8, 3.5)),
            ("bed2", K.BEDROOM, (0, 3.5, 4.2, 7)),
            ("bed3", K.BEDROOM, (4.2, 3.5, 8, 7)),
        ]
        doors = [("stair", "corridor"), ("corridor", "bed1"), ("bed1", "bed3"), ("bed3", "bed2")]
        result = judged(rooms, doors, floor=2)
        assert result.summary.circulation_share < 0.22
        assert result.summary.dimensions.efficiency == 100
        assert not result.summary.passed and result.summary.health is Health.FAIL

    def test_10_vastu_placement_does_not_mend_broken_circulation(self):
        layout, program, floor = storey(UPSTAIRS, THROUGH_THE_MASTER, floor=2)
        placed = {p.room_id: p for p in layout.rooms}
        in_their_zones = program.model_copy(update={"rooms": [
            room.model_copy(update={"sector": layout.sector_of(placed[room.id])})
            for room in program.rooms
        ]})
        plain = evaluate(layout, program, floor)
        vastu = evaluate(layout, in_their_zones, floor)
        assert vastu.summary.health is Health.FAIL
        assert vastu.summary == plain.summary


class TestReachableIsNotProperlyReached:
    def test_a_bedroom_behind_a_bedroom_is_reachable_and_still_has_no_access_of_its_own(self):
        result = judged(UPSTAIRS, THROUGH_THE_MASTER, floor=2)
        graph = result.summary.graph
        assert "bed2" in reachable(graph, graph.arrival, allow_outside=False)
        row = next(r for r in result.summary.access if r.room == "bed2")
        assert row.status == "no_independent_access" and row.hosts == ["master"]

    def test_the_door_that_makes_a_bedroom_a_passage_is_tagged_not_hidden(self):
        result = judged(UPSTAIRS, THROUGH_THE_MASTER, floor=2)
        [forced] = [
            e for e in result.summary.graph.edges if e.kind is EdgeKind.FORCED_PASS_THROUGH
        ]
        assert {forced.a, forced.b} == {"master", "bed2"} and forced.host == "master"

    def test_the_fix_names_the_wall_a_door_of_its_own_would_go_in(self):
        access = only(judged(UPSTAIRS, THROUGH_THE_MASTER, floor=2), "circulation.access")
        assert "its own door onto corridor" in access.fix


class TestAnEnSuiteIsJudgedFromItsBedroom:
    ROOMS = [
        ("stair", K.STAIRCASE, (0, 0, 3, 3.5)),
        ("bed3", K.BEDROOM, (3, 0, 10, 3.5)),
        ("corridor", K.CORRIDOR, (0, 3.5, 10, 5)),
        ("master", K.MASTER_BEDROOM, (0, 5, 4, 8)),
        ("suite", K.BATHROOM, (4, 5, 6.5, 8)),
        ("common", K.BATHROOM, (6.5, 5, 10, 8)),
    ]
    LANDING = [
        ("stair", "corridor"), ("corridor", "bed3"),
        ("corridor", "master"), ("corridor", "common"),
    ]
    OWNS = [("master", "suite")]

    def test_a_bathroom_opening_from_its_own_bedroom_is_right(self):
        result = judged(self.ROOMS, self.LANDING + [("master", "suite")], floor=2, connected=self.OWNS)
        assert "circulation.en_suite" not in rules_of(result)
        row = next(r for r in result.summary.access if r.room == "suite")
        assert row.status == "appropriate" and row.path == ["master", "suite"]

    def test_an_en_suite_off_the_corridor_is_a_shared_bathroom_and_major(self):
        result = judged(self.ROOMS, self.LANDING + [("corridor", "suite")], floor=2, connected=self.OWNS)
        assert only(result, "circulation.en_suite").grade is Grade.MAJOR
        assert result.summary.passed

    def test_an_en_suite_reached_through_another_bathroom_fails(self):
        """JP Nagar's first floor, drawn on purpose."""
        result = judged(self.ROOMS, self.LANDING + [("common", "suite")], floor=2, connected=self.OWNS)
        en_suite = only(result, "circulation.en_suite")
        assert en_suite.grade is Grade.CRITICAL and "common" in en_suite.path
        assert result.summary.health is Health.FAIL


class TestTheFrontDoorLeadsThroughAFoyerIntoTheHouse:
    def test_front_door_foyer_living_room_is_the_sequence_wanted(self):
        result = judged(GROUND, IN_SEQUENCE, front="foyer")
        assert "circulation.arrival_sequence" not in rules_of(result)

    def test_arriving_through_the_dining_room_reverses_the_hierarchy(self):
        rooms = [
            ("foyer", K.FOYER, (0, 0, 2.5, 3)),
            ("dining", K.DINING, (2.5, 0, 7, 3)),
            ("kitchen", K.KITCHEN, (7, 0, 12, 3)),
            ("hall", K.HALL, (0, 3, 12, 8)),
        ]
        doors = [("foyer", "dining"), ("dining", "hall"), ("dining", "kitchen")]
        result = judged(rooms, doors, front="foyer")
        sequence = only(result, "circulation.arrival_sequence")
        assert sequence.grade is Grade.MAJOR and "dining" in sequence.rooms
        assert sequence.path == [ENTRY, "foyer", "dining", "hall"]
        assert "Open foyer straight into hall" in sequence.fix
        assert result.summary.passed

    def test_a_house_entered_through_the_kitchen_is_one_finding_not_three(self):
        rooms = [
            ("foyer", K.FOYER, (0, 0, 2.5, 3)),
            ("kitchen", K.KITCHEN, (2.5, 0, 7, 3)),
            ("dining", K.DINING, (7, 0, 12, 3)),
            ("hall", K.HALL, (0, 3, 12, 8)),
        ]
        doors = [("foyer", "kitchen"), ("kitchen", "hall"), ("kitchen", "dining")]
        result = judged(rooms, doors, front="foyer")
        assert rules_of(result, Grade.MAJOR).count("circulation.access") == 1
        assert "circulation.arrival_sequence" not in rules_of(result)
        assert "circulation.zone_crossing" not in rules_of(result)


class TestACorridorIsJudgedByItsWork:
    def test_a_landing_every_bedroom_needs_is_essential_not_a_fault(self):
        result = judged(UPSTAIRS, OFF_THE_LANDING, floor=2)
        [corridor] = result.summary.corridors
        assert corridor.essential and corridor.verdict is CorridorVerdict.ESSENTIAL
        assert corridor.rooms_served == 5 and corridor.private_served == 4
        assert "circulation.corridor" not in rules_of(result)

    def test_a_corridor_no_room_needs_and_no_walk_uses_is_redundant(self):
        rooms = [
            ("stair", K.STAIRCASE, (0, 0, 3, 3.5)),
            ("bed3", K.BEDROOM, (3, 0, 8.5, 3.5)),
            ("spare", K.CORRIDOR, (8.5, 0, 10, 3.5)),
            ("corridor", K.CORRIDOR, (0, 3.5, 10, 5)),
            ("master", K.MASTER_BEDROOM, (0, 5, 4, 8)),
            ("bed2", K.BEDROOM, (4, 5, 7, 8)),
            ("bath", K.BATHROOM, (7, 5, 10, 8)),
        ]
        result = judged(rooms, OFF_THE_LANDING + [("corridor", "spare")], floor=2)
        verdicts = {c.room: c.verdict for c in result.summary.corridors}
        assert verdicts == {
            "corridor": CorridorVerdict.ESSENTIAL, "spare": CorridorVerdict.REDUNDANT,
        }
        redundant = only(result, "circulation.corridor")
        assert redundant.grade is Grade.MAJOR and redundant.rooms == ["spare"]


class TestTheStairArrivesOnALanding:
    ROOMS = [
        ("stair", K.STAIRCASE, (0, 0, 3, 3.5)),
        ("bath", K.BATHROOM, (3, 0, 5.5, 3.5)),
        ("bed3", K.BEDROOM, (5.5, 0, 10, 3.5)),
        ("corridor", K.CORRIDOR, (0, 3.5, 10, 5)),
        ("master", K.MASTER_BEDROOM, (0, 5, 5, 8)),
        ("bed2", K.BEDROOM, (5, 5, 10, 8)),
    ]
    BEYOND = [("corridor", "bed3"), ("corridor", "master"), ("corridor", "bed2")]

    def test_a_shared_bathroom_off_the_stair_hall_is_ordinary(self):
        """The stair room includes its landing. Three benchmark plans were once failed
        for a common bathroom opening beside the stair."""
        doors = [("stair", "corridor"), ("stair", "bath")] + self.BEYOND
        result = judged(self.ROOMS, doors, floor=2)
        assert "circulation.stair_arrival" not in rules_of(result)
        assert result.summary.passed

    def test_a_stair_whose_only_way_on_is_a_bathroom_fails(self):
        doors = [("stair", "bath"), ("bath", "corridor")] + self.BEYOND
        result = judged(self.ROOMS, doors, floor=2)
        assert only(result, "circulation.stair_arrival").grade is Grade.CRITICAL
        assert result.summary.health is Health.FAIL


class TestACarBayIsAPorchUnlessTheWalkIsLong:
    def test_a_bay_beside_the_front_door_is_a_minor_point(self):
        rooms = [
            ("car", K.CAR_PARKING, (0, 0, 3.2, 6)),
            ("foyer", K.FOYER, (3.2, 0, 5.7, 3)),
            ("hall", K.HALL, (5.7, 0, 12, 3)),
            ("kitchen", K.KITCHEN, (3.2, 3, 7, 6)),
            ("dining", K.DINING, (7, 3, 12, 6)),
        ]
        doors = [("foyer", "hall"), ("hall", "dining"), ("dining", "kitchen")]
        result = judged(rooms, doors, front="foyer", gates=["car"])
        parking = only(result, "circulation.parking")
        assert parking.grade is Grade.MINOR and parking.rooms == ["car"]
        assert "door into foyer" in parking.fix

    def test_a_long_walk_round_to_the_front_door_is_major(self):
        rooms = [
            ("car", K.CAR_PARKING, (0, 0, 3.2, 6)),
            ("hall", K.HALL, (3.2, 0, 9.5, 3)),
            ("foyer", K.FOYER, (9.5, 0, 12, 3)),
            ("kitchen", K.KITCHEN, (3.2, 3, 7, 6)),
            ("dining", K.DINING, (7, 3, 12, 6)),
        ]
        doors = [("foyer", "hall"), ("hall", "dining"), ("dining", "kitchen")]
        result = judged(rooms, doors, front="foyer", gates=["car"])
        assert only(result, "circulation.parking").grade is Grade.MAJOR


class TestAStoreyNobodyCanEnter:
    def test_a_ground_floor_with_no_front_door_reports_that_alone(self):
        result = judged(GROUND, IN_SEQUENCE)
        assert rules_of(result) == ["circulation.no_front_door"]
        assert result.summary.score == 0 and result.summary.health is Health.FAIL

    def test_an_upper_floor_whose_stair_misses_the_one_below_has_no_way_up(self):
        below = PlacedRoom(room_id="stair1", x_min_m=7, y_min_m=5, x_max_m=10, y_max_m=8)
        result = judged(UPSTAIRS, OFF_THE_LANDING, floor=2, shafts={K.STAIRCASE: below})
        assert rules_of(result) == ["circulation.stair_misaligned"]
        assert not result.summary.passed


class TestTheRulesAreData:
    def test_every_room_kind_has_a_role_and_a_zone(self):
        kinds = semantics.data()["kinds"]
        for kind in K:
            assert kind.value in kinds, f"{kind.value} has no circulation rule"
            CirculationRole(kinds[kind.value]["role"])
            assert kinds[kind.value]["zone"] == "derived" or Zone(kinds[kind.value]["zone"])

    def test_every_role_is_priced_wherever_roles_are_looked_up(self):
        data = semantics.data()
        roles = {entry["role"] for name, entry in data["kinds"].items() if name != "_note"}
        assert roles <= set(data["pass_through"])
        assert roles <= set(data["stair_arrival"])
        assert roles <= set(data["scoring"]["importance"])

    def test_every_finding_says_why_and_what_to_do(self):
        cases = [
            judged(UPSTAIRS, THROUGH_THE_MASTER, floor=2),
            judged(GUEST, GUEST_DOORS, front="foyer"),
            judged(GUEST, GUEST_DOORS, front="foyer", connected=[("bed1", "bath")]),
            judged(WIDE_LANDING, OFF_THE_LANDING, floor=2),
            judged(GROUND, IN_SEQUENCE),
        ]
        for result in cases:
            rooms = {n.id for n in result.summary.graph.nodes if n.kind != "outside"}
            for f in result.findings:
                assert f.rule.startswith("circulation.") and f.why and f.fix, f.message
                assert f.severity is f.grade.severity
                assert set(f.rooms) <= rooms, f.message


class TestWhatTheOldWalkWasBuiltToCatch:
    """Stage ⑦'s reachability walk grew a rule for each of these, every one found by
    looking at a drawing it had called clean. The engine replaced the walk, so each is
    asked of the engine."""

    def test_a_ground_floor_with_a_stair_and_no_front_door_is_refused(self):
        """The 25x40: the walk fell back to the staircase on a ground floor, found every
        room, and called a house nobody could enter clean."""
        rooms = [("stair", K.STAIRCASE, (0, 0, 3, 4)), ("hall", K.HALL, (3, 0, 10, 4))]
        result = judged(rooms, [("stair", "hall")], floor=1)
        assert rules_of(result) == ["circulation.no_front_door"]

    def test_a_house_entered_through_a_bedroom_fails(self):
        """The 30x50: front door, foyer, a bedroom, and only then the hall."""
        rooms = [
            ("foyer", K.FOYER, (0, 0, 2.5, 3)),
            ("bed1", K.BEDROOM, (2.5, 0, 7, 3)),
            ("kitchen", K.KITCHEN, (7, 0, 12, 3)),
            ("hall", K.HALL, (0, 3, 12, 8)),
        ]
        doors = [("foyer", "bed1"), ("bed1", "hall"), ("hall", "kitchen")]
        access = only(judged(rooms, doors, front="foyer"), "circulation.access")
        assert access.grade is Grade.CRITICAL and {"hall", "kitchen"} <= set(access.rooms)

    def test_a_bedroom_behind_the_kitchen_is_major(self):
        """The 40x60: every bedroom lay beyond the kitchen. A kitchen is somewhere you
        walk through to a utility, not the way to the bedrooms."""
        rooms = [
            ("foyer", K.FOYER, (0, 0, 2.5, 3)),
            ("hall", K.HALL, (2.5, 0, 12, 3)),
            ("kitchen", K.KITCHEN, (0, 3, 5, 8)),
            ("bed1", K.BEDROOM, (5, 3, 12, 8)),
        ]
        doors = [("foyer", "hall"), ("hall", "kitchen"), ("kitchen", "bed1")]
        result = judged(rooms, doors, front="foyer")
        access = only(result, "circulation.access")
        assert access.grade is Grade.MAJOR and access.rooms == ["bed1"]
        assert result.summary.passed

    def test_a_front_door_through_the_stair_hall_and_a_corridor_is_roundabout(self):
        """The model's 30x40 3BHK was entered foyer, staircase, corridor, hall, and
        nothing objected: every room reachable, no private room crossed."""
        rooms = [
            ("foyer", K.FOYER, (0, 0, 2.5, 3)),
            ("stair", K.STAIRCASE, (2.5, 0, 6, 3)),
            ("corridor", K.CORRIDOR, (6, 0, 7.5, 3)),
            ("kitchen", K.KITCHEN, (7.5, 0, 12, 3)),
            ("hall", K.HALL, (0, 3, 12, 8)),
        ]
        doors = [("foyer", "stair"), ("stair", "corridor"), ("corridor", "hall"), ("hall", "kitchen")]
        sequence = only(judged(rooms, doors, front="foyer"), "circulation.arrival_sequence")
        assert sequence.grade is Grade.MINOR and sequence.path[-1] == "hall"
        direct = judged(rooms, doors + [("foyer", "hall")], front="foyer")
        assert "circulation.arrival_sequence" not in rules_of(direct)

    def test_a_second_honest_route_clears_the_room(self):
        """Every route, not the shortest. One good way in is enough."""
        result = judged(UPSTAIRS, OFF_THE_LANDING + [("master", "bed2")], floor=2)
        row = next(r for r in result.summary.access if r.room == "bed2")
        assert row.status == "appropriate"
        assert "circulation.access" not in rules_of(result)

    def test_an_en_suite_behind_someone_else_s_bedroom_fails(self):
        """The exception is the edge stage ③ drew, not any bathroom beside a bed."""
        rooms = TestAnEnSuiteIsJudgedFromItsBedroom.ROOMS[:-1] + [
            ("bed2", K.BEDROOM, (6.5, 5, 10, 8)),
        ]
        doors = [
            ("stair", "corridor"), ("corridor", "bed3"), ("corridor", "master"),
            ("corridor", "bed2"), ("bed2", "suite"),
        ]
        result = judged(rooms, doors, floor=2, connected=[("master", "suite")])
        en_suite = only(result, "circulation.en_suite")
        assert en_suite.grade is Grade.CRITICAL and "bed2" in en_suite.path


@pytest.fixture(scope="module")
def jp_nagar():
    data = json.loads(GOLDEN.read_text())
    program = Program.model_validate(data["program"])
    return [
        (Layout.model_validate(layout), program, RefinedFloor.model_validate(floor))
        for layout, floor in zip(data["layouts"], data["floors"])
    ]


class TestJPNagar:
    """The 40x60 G+1 4BHK, as drawn at 7a3393a. Stage ⑦ reported its first floor as one
    warning: a bedroom reached through another bedroom, and the master's en-suite reached
    through a second bathroom."""

    def test_the_first_floor_fails_on_exactly_its_two_critical_defects(self, jp_nagar):
        result = evaluate(*jp_nagar[1])
        critical = {
            (f.rule, tuple(f.rooms)) for f in result.findings if f.grade is Grade.CRITICAL
        }
        assert critical == {
            ("circulation.access", ("bed2",)),
            ("circulation.en_suite", ("bath2", "master")),
        }
        assert result.summary.health is Health.FAIL

    def test_bedroom_two_is_explained_by_the_wall_it_shares(self, jp_nagar):
        access = only(evaluate(*jp_nagar[1]), "circulation.access")
        assert "0.11 m" in access.why and "1.05 m" in access.why
        assert "bed3" in access.path
        assert access.fix.startswith("Extend corridor (corridor2) along bedroom (bed2)")

    def test_the_ground_floor_passes_with_the_problems_it_does_have(self, jp_nagar):
        result = evaluate(*jp_nagar[0])
        assert result.summary.passed
        assert {
            ("circulation.arrival_sequence", Grade.MAJOR),
            ("circulation.relationship", Grade.MAJOR),
            ("circulation.share", Grade.MAJOR),
            ("circulation.foyer", Grade.MAJOR),
            ("circulation.parking", Grade.MINOR),
        } <= {(f.rule, f.grade) for f in result.findings}
        indirect = {
            frozenset(f.rooms) for f in result.findings
            if f.rule == "circulation.relationship" and f.grade is Grade.MAJOR
        }
        assert indirect == {frozenset({"hall", "dining"}), frozenset({"dining", "kitchen"})}
        pooja = [f.grade for f in result.findings if "pooja" in f.rooms]
        assert pooja == [Grade.MINOR]
        assert 0.31 <= result.summary.circulation_share <= 0.33


class TestNamesDecideNothing:
    def test_renaming_every_room_changes_no_verdict(self, jp_nagar):
        for layout, program, floor in jp_nagar:
            mapping = {room.id: f"space{i}" for i, room in enumerate(program.rooms)}
            before = evaluate(layout, program, floor)
            after = evaluate(*_renamed(layout, program, floor, mapping))
            assert _verdict(after) == _verdict(before, mapping)


def _renamed(layout, program, floor, mapping):
    def rid(value):
        return mapping.get(value, value)

    p = program.model_dump(mode="json")
    for room in p["rooms"]:
        room["id"] = rid(room["id"])
    for edge in p["adjacencies"]:
        edge["a"], edge["b"] = rid(edge["a"]), rid(edge["b"])
    lay = layout.model_dump(mode="json")
    for room in lay["rooms"]:
        room["room_id"] = rid(room["room_id"])
    f = floor.model_dump(mode="json")
    for wall in f["walls"]:
        wall["rooms"] = [rid(r) for r in wall["rooms"]]
    for opening in f["openings"]:
        opening["connects"] = [rid(r) for r in opening["connects"]]
    for fixture in f["fixtures"]:
        fixture["room_id"] = rid(fixture["room_id"])
    for outside in f["outside"]:
        outside["room_id"] = rid(outside["room_id"])
    f["clear"] = {rid(k): v for k, v in f["clear"].items()}
    return (
        Layout.model_validate(lay),
        Program.model_validate(p),
        RefinedFloor.model_validate(f),
    )


def _verdict(result, mapping=None):
    rename = (mapping or {}).get
    s = result.summary
    return (
        s.health, s.score, s.quality, s.dimensions, s.circulation_share,
        sorted(
            (f.rule, f.grade.value, tuple(sorted(rename(r, r) for r in f.rooms)))
            for f in result.findings
        ),
    )
