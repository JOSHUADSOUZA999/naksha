"""Closed vocabularies shared across the pipeline.

These are `str` enums so they serialise as readable JSON in provenance records and
in the schema handed to the model — an LLM picks correctly from `north_east` far
more reliably than from an integer code.
"""

from __future__ import annotations

from enum import StrEnum


class Facing(StrEnum):
    """Which way the plot's primary road frontage points.

    Drives Vastu sector scoring and, via the setback ruleset, which edge carries the
    larger front setback. Degrees are clockwise from north, matching the IR-wide
    angle convention.
    """

    NORTH = "north"
    NORTH_EAST = "north_east"
    EAST = "east"
    SOUTH_EAST = "south_east"
    SOUTH = "south"
    SOUTH_WEST = "south_west"
    WEST = "west"
    NORTH_WEST = "north_west"

    @property
    def degrees(self) -> float:
        """Bearing in degrees clockwise from north."""
        return _FACING_DEGREES[self]


_FACING_DEGREES: dict[Facing, float] = {
    Facing.NORTH: 0.0,
    Facing.NORTH_EAST: 45.0,
    Facing.EAST: 90.0,
    Facing.SOUTH_EAST: 135.0,
    Facing.SOUTH: 180.0,
    Facing.SOUTH_WEST: 225.0,
    Facing.WEST: 270.0,
    Facing.NORTH_WEST: 315.0,
}


class VastuStance(StrEnum):
    """How hard the Vastu constraints bind during layout.

    `STRICT` makes sector preferences hard constraints in Stage B and may render a
    brief infeasible; `MODERATE` keeps them as soft objective terms; `IGNORE` drops
    them from the objective entirely but still reports the score, because the score
    is advisory output regardless of whether it steered the solve.
    """

    STRICT = "strict"
    MODERATE = "moderate"
    IGNORE = "ignore"


class RoomKind(StrEnum):
    """Rooms a user can ask for by name at the brief stage.

    Deliberately not the full room taxonomy — stage ③ PROGRAM expands a Brief into
    the complete room list (corridors, utility, staircase). This is only what a
    person actually types.
    """

    POOJA = "pooja"
    STUDY = "study"
    CAR_PARKING = "car_parking"
    STORE = "store"
    UTILITY = "utility"
    GUEST_ROOM = "guest_room"
    SERVANT_ROOM = "servant_room"
    BALCONY = "balcony"
    VERANDA = "veranda"
    OFFICE = "office"


class SpaceKind(StrEnum):
    """Every space a plan can contain — the full taxonomy stage ③ expands into.

    Deliberately wider than `RoomKind`, and a **superset** of it. `RoomKind` is what a
    person names out loud in a brief; hall, kitchen, bedrooms and bathrooms are implied
    by "3BHK" and never typed, and circulation is not something anyone asks for at all.
    Stage ③'s whole job is turning the first vocabulary into this one.

    A test asserts the superset property, so adding a `RoomKind` without its
    counterpart here fails rather than silently dropping the room in expansion.
    """

    # Implied by BHK — never stated, always present.
    HALL = "hall"
    DINING = "dining"
    KITCHEN = "kitchen"
    BEDROOM = "bedroom"
    MASTER_BEDROOM = "master_bedroom"
    BATHROOM = "bathroom"
    WC = "wc"

    # Circulation. Nobody asks for a corridor; a plan without one does not work.
    FOYER = "foyer"
    CORRIDOR = "corridor"
    STAIRCASE = "staircase"

    # The brief-stage vocabulary, carried through unchanged.
    POOJA = "pooja"
    STUDY = "study"
    CAR_PARKING = "car_parking"
    STORE = "store"
    UTILITY = "utility"
    GUEST_ROOM = "guest_room"
    SERVANT_ROOM = "servant_room"
    BALCONY = "balcony"
    VERANDA = "veranda"
    OFFICE = "office"

    @classmethod
    def from_room_kind(cls, kind: RoomKind) -> SpaceKind:
        """A brief-stage room, as a plan space. Total by construction."""
        return cls(kind.value)


class Sector(StrEnum):
    """Vastu zones of a plot. Eight compass sectors plus the centre.

    Distinct from `Facing`, which names a direction a *road* points. A sector is a
    region of the plan, and the centre has no compass equivalent: the brahmasthan is
    meant to stay open, so "no sector" and "the middle" are different claims and a
    nullable `Facing` could not express both.

    Advisory throughout — CLAUDE.md's legal position is that Vastu output is guidance,
    and the ruleset version that produced a score is always shown.
    """

    NORTH = "north"
    NORTH_EAST = "north_east"
    EAST = "east"
    SOUTH_EAST = "south_east"
    SOUTH = "south"
    SOUTH_WEST = "south_west"
    WEST = "west"
    NORTH_WEST = "north_west"
    BRAHMASTHAN = "brahmasthan"


class Relation(StrEnum):
    """How two spaces must sit relative to one another.

    The vocabulary stage ⑤ solves against. `ADJACENT` and `CONNECTED` differ in what
    stage ⑥ does later — a shared wall versus a shared wall with a door in it — but
    both constrain the tiling identically, so the distinction has to survive ⑤ rather
    than being flattened into it.
    """

    ADJACENT = "adjacent"
    CONNECTED = "connected"
    SEPARATED = "separated"
