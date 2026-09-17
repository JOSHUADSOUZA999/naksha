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

    # Not a room, and it has no `RoomKind` counterpart because nobody asks for one.
    # Open ground under a house built on a stilt: the level exists so a car can stand
    # beneath the building and the stair can come down, and the rest of it is left
    # open. It is a `SpaceKind` because stage ⑤ tiles *exactly* — a stilt level needs
    # something to absorb the area no room claims, or the tiling squeezes the car bay
    # to make three rooms fill the rectangle, which is how the first attempt failed.
    STILT = "stilt"

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
    # A short walk apart (`circulation_v1.near`). What a brief means by "near the
    # entrance" — a parent's bedroom a few steps from the front door, not necessarily
    # sharing its wall, and never a door nobody asked for.
    NEAR = "near"
    # One space in two rectangles: they share a wall and no wall is built in it. The living
    # and dining room of most new Indian houses, which a slicing tree otherwise cuts into
    # two strips side by side.
    OPEN = "open"


class WallKind(StrEnum):
    """Which side of the house a wall is on.

    It decides thickness — one brick outside, half a brick inside — and it decides
    what openings are allowed: a window goes in an exterior wall, a door between two
    rooms goes in an interior one.
    """

    EXTERIOR = "exterior"
    INTERIOR = "interior"


class OpeningKind(StrEnum):
    """A hole in a wall, by what it is for.

    `ENTRANCE` is distinct from `DOOR` because it is the one opening whose position is
    not a convenience: it is where the street meets the house, so it belongs on the
    road-facing wall of the foyer and is drawn wider.
    """

    DOOR = "door"
    ENTRANCE = "entrance"
    WINDOW = "window"
    # The car bay's opening to the road: a gap a car drives through, with no leaf to
    # swing and no glass. Every bay used to be drawn with a window, sealed.
    VEHICLE = "vehicle"
    # A small high opening for air rather than a view — what a bathroom or WC gets. Not a
    # `WINDOW`, because the bye-laws hold it to an area of its own rather than a tenth of
    # the floor, and because it is built differently: above head height, so nobody sees in.
    VENTILATOR = "ventilator"
    # No wall at all between two rooms that are one space: the whole shared wall, open.
    OPEN = "open"


class FixtureKind(StrEnum):
    """The things in a room that make it that kind of room.

    Deliberately a closed vocabulary rather than free text. A renderer has to know how
    to draw each one — a WC is not a rectangle, it is a rectangle with a bowl — and a
    schedule that could name anything would produce fixtures nothing can draw.
    """

    WC = "wc"
    BASIN = "basin"
    SHOWER = "shower"
    SINK = "sink"
    STOVE = "stove"
    COUNTER = "counter"
    BED = "bed"
    SINGLE_BED = "single_bed"
    WARDROBE = "wardrobe"
    # A stair's flights and the landing between them. `faces` on a flight is the way you
    # climb it, which is what a plan's arrow shows.
    FLIGHT = "flight"
    LANDING = "landing"


class Severity(StrEnum):
    """How much a finding matters.

    Two levels, not five. A validator whose findings need their own triage has moved
    the judgment back onto the reader, and the only question a plot owner actually asks
    is whether the plan is wrong or merely worth a look.
    """

    ERROR = "error"
    WARNING = "warning"


class Grade(StrEnum):
    """How much an architectural finding matters — the finer scale beside `Severity`.

    `Severity` stays the gate every check reports through: an error refuses the plan, a
    warning is worth a look. A grade says what kind of problem it is, and maps onto it:
    a critical finding is an error, a major or minor one a warning. Critical is a house
    that cannot be used the way it must be — a bedroom that is the way into another —
    major a significant inefficiency or zoning problem, minor an optimisation.
    """

    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"

    @property
    def severity(self) -> Severity:
        return Severity.ERROR if self is Grade.CRITICAL else Severity.WARNING


class Zone(StrEnum):
    """A space's place on the privacy gradient, from the street inwards.

    `SERVICE` runs beside the gradient rather than on it: a kitchen is not more private
    than a dining room, it is a different kind of room. `EXTERNAL` is the street side
    of the front door, and a car bay.
    """

    PUBLIC = "public"
    SEMI_PUBLIC = "semi_public"
    SEMI_PRIVATE = "semi_private"
    PRIVATE = "private"
    SERVICE = "service"
    EXTERNAL = "external"


class CirculationRole(StrEnum):
    """What a space is for when someone walks through the house.

    The role decides what passing *through* a room costs — nothing for a corridor, a
    failure for a bedroom — and the mapping from room kind to role is data, in
    `circulation_v1`, not a set of kinds written out in code.
    """

    ARRIVAL = "arrival"
    CIRCULATION = "circulation"
    SOCIAL = "social"
    SERVICE = "service"
    SACRED = "sacred"
    WORK = "work"
    PRIVATE = "private"
    SANITARY = "sanitary"
    VEHICLE = "vehicle"
    OPEN = "open"
    OUTSIDE = "outside"


class EdgeKind(StrEnum):
    """How two nodes of a circulation graph are joined.

    `FORCED_PASS_THROUGH` is a door that exists and must never be read as good
    circulation: the only way to a room runs through a room nobody should walk through.
    It stays drawn so the plan shows what was generated.
    """

    DIRECT_DOOR = "direct_door"
    OPEN_CONNECTION = "open_connection"
    FORCED_PASS_THROUGH = "forced_pass_through"
    STAIR_CONNECTION = "stair_connection"
    SERVICE_CONNECTION = "service_connection"
    EXTERNAL_CONNECTION = "external_connection"


class JourneyClass(StrEnum):
    """Who is walking, which decides the zones the walk should not have to cross."""

    VISITOR = "visitor"
    RESIDENT = "resident"
    PRIVATE = "private"
    SERVICE = "service"


class CorridorVerdict(StrEnum):
    """Whether a corridor earns its area.

    Deliberately not "does removing it strand rooms": a landing serving four bedrooms is
    supposed to, and is `ESSENTIAL`. The question is whether the circulation does
    necessary work in a reasonable amount of floor.
    """

    ESSENTIAL = "essential"
    EFFICIENT = "efficient"
    INEFFICIENT = "inefficient"
    REDUNDANT = "redundant"


class Health(StrEnum):
    GOOD = "good"
    FAIR = "fair"
    POOR = "poor"
    FAIL = "fail"
