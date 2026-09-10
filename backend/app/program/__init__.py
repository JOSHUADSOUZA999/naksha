"""Stage ③ PROGRAM — `Brief` + `Envelope` to a room list and its constraint graph.

This module is the **deterministic floor**, the same role `llm/fallback.py` plays for
stage ①: a real expansion, not a stub, that runs with no model, no network and no key.
The LLM version comes later and will read more nuance out of a brief than these rules
can — but a plan owner whose API call failed should still see a house.

Emits a graph and never coordinates. Decision 1.
"""

from __future__ import annotations

import math
from typing import Any

from app.ir.enums import Facing, Relation, RoomKind, Sector, SpaceKind
from app.ir.envelope import Envelope
from app.ir.models import Brief
from app.ir.plan import AdjacencySpec, Program, RoomSpec
from app.rules import load_ruleset

SPACE_RULES = "spaces_v1"


class PorchDoesNotFit(ValueError):
    """`porch_in_setback` was asked for and the setback cannot hold a car.

    Carries the numbers, like the envelope errors do. "It does not fit" is not a
    finding anybody can act on; "the front setback is 2.19 m deep and a bay needs 3.0"
    names the rule to go and check.
    """

    def __init__(self, *, depth_m: float, along_m: float, needs: tuple[float, float]):
        self.depth_m, self.along_m, self.needs = depth_m, along_m, needs
        super().__init__(
            f"the front setback is {depth_m:.2f} m deep and {along_m:.2f} m along the "
            f"road, and a {needs[0]:.1f} x {needs[1]:.1f} m bay does not fit in it"
        )


def expand(
    brief: Brief,
    envelope: Envelope | None = None,
    *,
    porch_in_setback: bool = False,
) -> Program:
    """Turn "3BHK with a pooja room" into every room a house actually needs.

    The bedroom count is stated; the hall, kitchen, circulation and sanitary spaces
    are not, and a plan without them is not a house. Bathrooms come from the Brief,
    which stage ① already defaulted and disclosed rather than leaving null.
    """
    rules = load_ruleset(SPACE_RULES).data["spaces"]
    hints = brief.program
    rooms: list[RoomSpec] = []

    def add(kind: SpaceKind, room_id: str, *, floor: int = 1) -> str:
        rooms.append(_spec(kind, room_id, rules, floor=floor))
        return room_id

    # Core, in the order a person walks through the house.
    add(SpaceKind.FOYER, "foyer")
    hall = add(SpaceKind.HALL, "hall")
    kitchen = add(SpaceKind.KITCHEN, "kitchen")
    if hints.bedrooms >= 3:
        # Below 3BHK the dining sits inside the hall — a separate room on a small
        # plot costs a wall and buys nothing.
        add(SpaceKind.DINING, "dining")

    bedrooms = [add(SpaceKind.MASTER_BEDROOM, "bed1")]
    bedrooms += [add(SpaceKind.BEDROOM, f"bed{n}") for n in range(2, hints.bedrooms + 1)]

    baths = [add(SpaceKind.BATHROOM, f"bath{n}") for n in range(1, (hints.bathrooms or 1) + 1)]

    # Circulation. Nobody asks for it; every plan needs it.
    corridor = add(SpaceKind.CORRIDOR, "corridor")

    # What the user did name, carried through unchanged — except parking, which is
    # counted in `parking_bays` and built from it below.
    extra_ids: set[str] = set()
    for room in hints.extra_rooms:
        if room is not RoomKind.CAR_PARKING:
            extra_ids.add(add(SpaceKind.from_room_kind(room), room.value))

    # `parking_bays` is authoritative, and reading it here rather than off
    # `extra_rooms` is what makes the room list deterministic. The model lists
    # `car_parking` in `extra_rooms` on some runs and not others for the same brief;
    # sourcing a 15 m² bay from that choice made the programme swing 14% between
    # identical inputs, which is not something stage ⑤ can absorb.
    bays = hints.parking_bays or 0
    parking = [
        add(SpaceKind.CAR_PARKING, "car_parking" if bays == 1 else f"car_parking{n}")
        for n in range(1, bays + 1)
    ]
    if porch_in_setback and parking:
        _refuse_if_the_setback_is_too_shallow(envelope, rules)
    if porch_in_setback:
        # The statutory 18 m² bay is a quarter of a 30x40's ground floor, and moving it
        # into the front setback is what decides whether that plot fits a 3BHK at all.
        # Off by default because whether the setback may be built on is VERIFY.md Q1 —
        # unanswered, and answering it wrongly produces a plan that cannot be sanctioned.
        for index, room in enumerate(rooms):
            if room.id in parking:
                rooms[index] = room.model_copy(update={"outside_envelope": True})

    _assign_floors(rooms, hints.floors, envelope, rules)
    _grow(rooms, envelope)

    return Program(
        rooms=rooms,
        adjacencies=_wire(
            hall, kitchen, corridor, bedrooms, baths, parking,
            room_ids={room.id for room in rooms},
            extras=[room for room in rooms if room.id in extra_ids],
        ),
    )


# Ground floor in a conventional Indian house: everything a visitor sees, everything
# with a service connection, and the car. Bedrooms are what goes up.
_STAYS_DOWN = {
    SpaceKind.FOYER, SpaceKind.HALL, SpaceKind.DINING, SpaceKind.KITCHEN,
    SpaceKind.CAR_PARKING, SpaceKind.POOJA, SpaceKind.UTILITY, SpaceKind.VERANDA,
    SpaceKind.STAIRCASE, SpaceKind.CORRIDOR,
}


def _assign_floors(
    rooms: list[RoomSpec], requested: int, envelope: Envelope | None, rules: dict[str, Any]
) -> None:
    """Put bedrooms upstairs when the ground floor cannot hold the programme.

    Mutates in place, and only when it has to. `requested` is what stage ① read from
    the brief; going above it is a decision the *site* forces, not a preference —
    a 3BHK at legal minimums needs 70 m² and a 30x40 in Bengaluru leaves 55 m²
    buildable after setbacks, which is exactly why those plots are built G+1.

    Without an envelope there is no footprint to overflow, so the brief's own floor
    count stands.
    """
    if envelope is None:
        _stack(rooms, requested, rules)
        return

    footprint = envelope.max_footprint_sq_m
    needed = sum(room.min_area_sq_m for room in rooms)
    forced = math.ceil(needed / footprint) if footprint > 0 else requested
    floors = min(max(requested, forced), envelope.max_floors)
    _stack(rooms, floors, rules, footprint)


def _stack(
    rooms: list[RoomSpec], floors: int, rules: dict[str, Any], footprint: float | None = None
) -> None:
    """Move rooms up until the ground floor fits, largest first.

    Greedy rather than a fixed split: moving half the movable rooms left 66 m² on a
    55 m² floor, which is the same failure as not splitting at all. The stop condition
    has to be the footprint, not a fraction.

    The master bedroom and one bathroom stay down whatever happens — that is the room
    an elderly parent uses, and it is what keeps the house liveable if the upper floor
    is delayed, which on a self-built Indian plot it very often is.
    """
    if floors <= 1:
        return

    stair = rules[SpaceKind.STAIRCASE.value]["min_area_sq_m"]
    corridor = rules[SpaceKind.CORRIDOR.value]["min_area_sq_m"]

    def by_size(candidates) -> list[RoomSpec]:
        return sorted(candidates, key=lambda r: r.min_area_sq_m, reverse=True)

    # Two passes. Keeping the master and one bathroom downstairs is a preference —
    # the room an elderly parent uses — and a preference yields to a hard constraint.
    # Protecting it past the point of feasibility produces no plan at all, which
    # serves that parent worse than stairs do.
    preferred = by_size(
        r for r in rooms if r.kind not in _STAYS_DOWN and r.id not in {"bed1", "bath1"}
    )
    reluctant = by_size(r for r in rooms if r.id in {"bed1", "bath1"})
    movable = preferred + reluctant

    def ground_load() -> float:
        # The staircase lands on the ground floor too, so it is part of the budget
        # being tested rather than an afterthought added once the split is decided.
        return sum(r.min_area_sq_m for r in rooms if r.floor == 1) + stair

    if footprint is None:
        # No envelope, so nothing to measure against — but the brief asked for the
        # floor, and answering "G+1" with a single storey ignores what it said.
        # Bedrooms go up, which is the conventional split.
        for room in preferred:
            object.__setattr__(room, "floor", 2)
    else:
        for room in movable:
            if ground_load() <= footprint:
                break
            object.__setattr__(room, "floor", 2)

    if any(room.floor > 1 for room in rooms):
        for floor in range(1, floors + 1):
            rooms.append(_spec(SpaceKind.STAIRCASE, f"stair{floor}", rules, floor=floor))
        rooms.append(_spec(SpaceKind.CORRIDOR, "corridor2", rules, floor=2))


def _spec(kind: SpaceKind, room_id: str, rules: dict[str, Any], *, floor: int) -> RoomSpec:
    rule = rules[kind.value]
    sector = rule["sector"]
    return RoomSpec(
        id=room_id,
        kind=kind,
        min_area_sq_m=rule["min_area_sq_m"],
        target_area_sq_m=rule["target_area_sq_m"],
        max_target_sq_m=(
            rule["max_target_sq_m"]
            if rule["max_target_sq_m"] > rule["target_area_sq_m"]
            else None
        ),
        min_width_m=rule["min_width_m"],
        max_aspect=rule["max_aspect"],
        sector=Sector(sector) if sector else None,
        needs_exterior_wall=rule["exterior_wall"],
        needs_road_access=rule["road_access"],
        needs_door=rule["walk_in"],
        is_through_route=rule["through_route"],
        floor=floor,
    )


def _wire(
    hall: str,
    kitchen: str,
    corridor: str,
    bedrooms: list[str],
    baths: list[str],
    parking: list[str],
    room_ids: set[str],
    *,
    dining: str = "dining",
    extras: list[RoomSpec] | None = None,
) -> list[AdjacencySpec]:
    """The relationships that make a room list a house rather than a pile of rectangles.

    Only the ones that are true of essentially every Indian home go in here. Anything
    situational — a bedroom for an elderly parent near the entrance — is exactly what
    the LLM version reads out of a brief and this one cannot.
    """
    extras = extras or []
    edges = [
        AdjacencySpec(a="foyer", b=hall, relation=Relation.CONNECTED),
        AdjacencySpec(a=hall, b=kitchen, relation=Relation.CONNECTED),
        AdjacencySpec(a=hall, b=corridor, relation=Relation.CONNECTED),
    ]
    # Bedrooms open off the corridor, not off each other and not off the hall — that
    # is what the corridor is for, and it is why privacy survives the tiling.
    edges += [AdjacencySpec(a=corridor, b=bed, relation=Relation.CONNECTED) for bed in bedrooms]

    # The master gets the en-suite stage ① already assumed and disclosed.
    if baths:
        edges.append(AdjacencySpec(a=bedrooms[0], b=baths[0], relation=Relation.CONNECTED))
    edges += [AdjacencySpec(a=corridor, b=bath, relation=Relation.CONNECTED) for bath in baths[1:]]

    # A toilet sharing a wall with the kitchen is the one placement every Indian
    # client objects to, Vastu or not. Hard, and hard on purpose.
    edges += [
        AdjacencySpec(a=kitchen, b=bath, relation=Relation.SEPARATED, hard=True)
        for bath in baths
    ]

    # Everything a person walks into needs a door, and until stage ⑦ existed nothing
    # checked. `dining` had no edge of any kind and the rooms a user names — pooja,
    # study, store — got none either, so they were unreachable *by construction*: the
    # plan drew them, the scorer was happy, and there was no way in. Measured on a
    # 30x50, you entered the front door and reached one room out of eleven.
    #
    # Where each one opens off is the ordinary arrangement rather than a rule: service
    # rooms off the kitchen, anything private off the corridor for the same reason
    # bedrooms are, everything else off the hall.
    # Soft. That a dining room opens off the hall is where the door *should* be, not
    # whether there is one — stage ⑥ guarantees the plan is walkable and these say what
    # the ordinary arrangement looks like. Hard edges here would only add violations
    # stage ⑤ cannot satisfy and would outvote things that matter more.
    if dining in room_ids:
        edges.append(
            AdjacencySpec(a=hall, b=dining, relation=Relation.CONNECTED, hard=False)
        )

    _OFF_THE_KITCHEN = {SpaceKind.UTILITY, SpaceKind.STORE}
    _OFF_THE_CORRIDOR = {SpaceKind.GUEST_ROOM, SpaceKind.SERVANT_ROOM, SpaceKind.STUDY}
    for room in extras:
        host = (
            kitchen if room.kind in _OFF_THE_KITCHEN
            else corridor if room.kind in _OFF_THE_CORRIDOR
            else hall
        )
        edges.append(
            AdjacencySpec(a=host, b=room.id, relation=Relation.CONNECTED, hard=False)
        )
    # Reachable from the entrance, not through the living room.
    edges += [
        AdjacencySpec(a="foyer", b=bay, relation=Relation.ADJACENT, hard=False)
        for bay in parking
    ]
    return edges


def fits(program: Program, envelope: Envelope) -> tuple[bool, str]:
    """Does the programme fit? The seed of stage ④.

    **Two budgets, and conflating them is the bug this docstring exists to prevent.**
    `max_built_area_sq_m` is FAR — floor area summed over *every* storey.
    `max_footprint_sq_m` is what one floor can hold, and it is far smaller: on a 30x40
    in Bengaluru the footprint is 55 m² against a FAR budget of 167 m². Checking only
    the FAR budget passes a programme that physically cannot be laid out, and stage ⑤
    then returns tilings where every room is half its minimum — a valid tiling of an
    impossible plan.

    Measured at `min_area_sq_m`, every room at its legal floor: failing there means no
    arrangement exists, not that this one was unlucky.
    """
    footprint = envelope.max_footprint_sq_m
    floors_used = sorted({room.floor for room in program.rooms})

    for floor in floors_used:
        needed = sum(room.min_area_sq_m for room in program.on_floor(floor))
        if needed > footprint:
            storeys = math.ceil(program.min_area_sq_m / footprint)
            advice = (
                f" — needs about {storeys} floors, and the road width allows "
                f"{envelope.max_floors}"
                if storeys <= envelope.max_floors
                else f" — even {envelope.max_floors} floors is not enough"
            )
            return False, (
                f"floor {floor} needs {needed:.1f} m² at legal minimums but only "
                f"{footprint:.1f} m² is buildable{advice}"
            )

    budget = envelope.max_built_area_sq_m
    if program.min_area_sq_m > budget:
        return False, (
            f"needs {program.min_area_sq_m:.1f} m² at legal minimums but FAR allows "
            f"{budget:.1f} m² — short by {program.min_area_sq_m - budget:.1f} m²"
        )

    target = program.target_area_sq_m
    per_floor_target = max(
        sum(r.target_area_sq_m for r in program.on_floor(f)) for f in floors_used
    )
    if per_floor_target > footprint:
        return True, (
            f"fits at minimums, but the busiest floor wants {per_floor_target:.1f} m² "
            f"of {footprint:.1f} m² buildable — rooms will be tight"
        )
    return True, f"{target:.1f} m² of {budget:.1f} m² allowed, across {len(floors_used)} floor(s)"


def _grow(rooms: list[RoomSpec], envelope: Envelope | None) -> None:
    """Spend a generous site on bigger rooms rather than on lawn.

    The targets in `spaces_v1` are what a room should be when the plot is tight, and
    they were the only figures there — so a 50x80 sized its programme exactly as a
    30x40 does and left 47% of its permitted footprint unbuilt. Correct arithmetic and
    the wrong house: an owner paying for that site wants a bigger hall, not more grass.

    Grows the floor with the most on it, proportionally to how much headroom each room
    has, and stops at the ceilings. Proportional rather than equal because a hall with
    14 m² of headroom and a kitchen with 5 m² should not both get the same 3 m² — and
    stopping at ceilings is what keeps the surplus from all landing in one room, which
    is the defect `footprint` was introduced to fix and would otherwise reintroduce.

    Mutates in place, and only upward: nothing here can push a room below a minimum.
    """
    if envelope is None:
        return

    for floor in {room.floor for room in rooms}:
        on_floor = [room for room in rooms if room.floor == floor]
        growable = [room for room in on_floor if room.max_target_sq_m is not None]
        if not growable:
            continue

        wanted = sum(room.target_area_sq_m for room in on_floor)
        spare = envelope.max_footprint_sq_m - wanted
        if spare <= 0:
            continue

        headroom = {
            room.id: room.max_target_sq_m - room.target_area_sq_m for room in growable
        }
        total = sum(headroom.values())
        if total <= 0:
            continue

        share = min(1.0, spare / total)
        for index, room in enumerate(rooms):
            if room.floor != floor or room.id not in headroom:
                continue
            rooms[index] = room.model_copy(
                update={
                    "target_area_sq_m": room.target_area_sq_m + headroom[room.id] * share
                }
            )


def _refuse_if_the_setback_is_too_shallow(
    envelope: Envelope | None, rules: dict[str, Any]
) -> None:
    """Check the strip can hold a car before promising it one.

    **Measured across the four reference plots and it fits on none of them.** The
    deepest front setback in the set is 2.93 m against the 3.0 m a statutory garage
    needs — the 50x80 misses by seven centimetres and the 30x40 by half the bay. So the
    honest answer to "may the porch go in the setback" is that on plots this size there
    is no setback to put it in, whatever the bye-laws permit.

    Refusing is the point. Moving the bay off the ground floor is what makes a 30x40
    3BHK feasible at all, and it would have been easy to ship that as a flag and let
    the drawing show a house with a car nowhere. That is the shape of every bug this
    codebase has found.
    """
    if envelope is None:
        return

    bay = rules[SpaceKind.CAR_PARKING.value]
    # A bay is 3.0 x 6.0; either dimension may run along the road.
    short = bay["min_width_m"]
    long = bay["min_area_sq_m"] / short

    road = envelope.road_edges[0]
    depth = envelope.setbacks[road]
    if road in (Facing.NORTH, Facing.SOUTH):
        along = (envelope.x_max_m - envelope.x_min_m) + (
            envelope.setbacks[Facing.EAST] + envelope.setbacks[Facing.WEST]
        )
    else:
        along = (envelope.y_max_m - envelope.y_min_m) + (
            envelope.setbacks[Facing.NORTH] + envelope.setbacks[Facing.SOUTH]
        )

    if (depth >= short and along >= long) or (depth >= long and along >= short):
        return
    raise PorchDoesNotFit(depth_m=depth, along_m=along, needs=(short, long))
