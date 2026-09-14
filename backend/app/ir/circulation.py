"""What the circulation engine produces: how a storey is walked, and what that says.

Stage ⑦ used to ask one question of a drawing — can every room be reached — and a plan
could answer yes while a bedroom was the way into another bedroom. These models carry
the difference between *reachable* and *properly reached*: a graph of every way through
the storey, the access each room actually has, the walks the house is used for, and a
score that no critical failure can pass.

Everything here is measured off stage ⑥'s drawing. Nothing is stored twice: a door is
still an `Opening`, and an edge in this graph points at the same wall and the same gap.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.ir.base import DerivedFieldsAreOutputOnly
from app.ir.enums import (
    CirculationRole,
    CorridorVerdict,
    EdgeKind,
    Grade,
    Health,
    JourneyClass,
    Zone,
)

Point = tuple[float, float]
Rect = tuple[float, float, float, float]


class CirculationNode(BaseModel):
    """A room, or one of the two places outside it a walk can start: the street and the
    doorstep."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    kind: str = Field(
        description="The room's `SpaceKind`, or `outside` for the street and doorstep."
    )
    role: CirculationRole
    zone: Zone
    walk_in: bool = Field(
        description="Must have proper access of its own. False for a car bay, which is "
        "entered from the street, and for the outside nodes."
    )
    parents: list[str] = Field(
        default_factory=list,
        description="The bedrooms a bathroom belongs to — stage ③'s en-suite edges. An "
        "en-suite is judged from its bedroom, not from the corridor.",
    )
    rect: Rect | None = Field(
        default=None, description="Inside the walls. None for the outside nodes."
    )


class CirculationEdge(BaseModel):
    """One way between two nodes, and enough about it to say why a route is good or bad."""

    model_config = ConfigDict(extra="forbid")

    a: str
    b: str
    kind: EdgeKind
    width_m: float | None = None
    at: Point | None = Field(
        default=None, description="Midpoint of the opening, where a walk passes through."
    )
    vertical: bool | None = Field(
        default=None,
        description="Whether the opening sits in a north-south wall. Turns are counted "
        "from which walls a walk enters and leaves a room by.",
    )
    host: str | None = Field(
        default=None,
        description="On a forced pass-through: the room made to serve as a passage.",
    )


class WallContact(BaseModel):
    """Two rooms that share a wall, with or without a door in it.

    What lets a finding say "bed2 shares 0.11 m with the corridor and a door needs
    1.05 m" rather than "bed2 is badly connected" — adjacency and access are different
    claims, and the difference is usually a length.
    """

    model_config = ConfigDict(extra="forbid")

    a: str
    b: str
    shared_m: float = Field(ge=0)
    door_possible: bool
    has_door: bool


class CirculationGraph(BaseModel):
    """Every way through one storey."""

    model_config = ConfigDict(extra="forbid")

    floor: int = Field(ge=1, le=4)
    arrival: str | None = Field(
        description="Where a person arrives on this storey: the doorstep outside the "
        "front door, or the staircase coming up. None when there is no way in."
    )
    required_door_m: float = Field(gt=0, description="The shortest wall a door fits in.")
    nodes: list[CirculationNode]
    edges: list[CirculationEdge]
    contacts: list[WallContact] = Field(default_factory=list)

    def node(self, node_id: str) -> CirculationNode | None:
        return next((n for n in self.nodes if n.id == node_id), None)

    def neighbours(self, node_id: str) -> list[tuple[str, CirculationEdge]]:
        return [
            (edge.b if edge.a == node_id else edge.a, edge)
            for edge in self.edges
            if node_id in (edge.a, edge.b)
        ]

    def contact(self, a: str, b: str) -> WallContact | None:
        return next((c for c in self.contacts if {c.a, c.b} == {a, b}), None)


AccessStatus = Literal[
    "appropriate", "inappropriate", "no_independent_access", "unreachable"
]


class Access(BaseModel):
    """How one room is actually reached — reachable and properly reached, told apart."""

    model_config = ConfigDict(extra="forbid")

    room: str
    status: AccessStatus
    grade: Grade | None = Field(
        default=None,
        description="None when the access is proper. Otherwise the worst room the best "
        "available route must pass through, graded by what that room is for.",
    )
    path: list[str] = Field(default_factory=list)
    hosts: list[str] = Field(
        default_factory=list,
        description="Rooms on that route nobody should have to walk through.",
    )


class Isolation(BaseModel):
    """What becomes unreachable when one private room is taken out of the graph."""

    model_config = ConfigDict(extra="forbid")

    removed: str
    stranded: list[str]


class Journey(BaseModel):
    """One walk the house is used for, measured on the best route it allows."""

    model_config = ConfigDict(extra="forbid")

    id: str
    journey_class: JourneyClass
    weight: float = Field(ge=0)
    origin: str
    destination: str
    reachable: bool
    path: list[str] = Field(default_factory=list)
    distance_m: float = Field(default=0.0, ge=0)
    doors: int = Field(default=0, ge=0)
    transitions: int = Field(default=0, ge=0)
    turns: int = Field(default=0, ge=0)
    privacy_crossings: int = Field(default=0, ge=0)
    inappropriate: list[str] = Field(default_factory=list)
    backtracking: int = Field(default=0, ge=0)
    dead_end: bool = False
    forced_pass_through: bool = False
    external: bool = False
    score: float = Field(ge=0, le=100)


class Corridor(BaseModel):
    """A corridor judged by the work it does, not by existing."""

    model_config = ConfigDict(extra="forbid")

    room: str
    area_sq_m: float = Field(ge=0)
    length_m: float = Field(ge=0)
    width_m: float = Field(ge=0)
    share: float = Field(ge=0, description="Of the storey's floor area.")
    rooms_served: int = Field(ge=0)
    private_served: int = Field(ge=0)
    branches: int = Field(
        ge=0, description="Other circulation spaces it joins: a stair, a foyer, another corridor."
    )
    dead_end_m: float = Field(
        ge=0, description="The longest stretch at either end past its last door."
    )
    journey_weight: float = Field(
        ge=0, description="How much of the house's weighted walking passes through it."
    )
    alternatives: int = Field(
        ge=0,
        description="Pairs of rooms it joins that share a wall long enough for a door of "
        "their own — direct connections the corridor may be standing in for.",
    )
    essential: bool = Field(
        description="Some room would lose its proper access without it. Not a fault."
    )
    verdict: CorridorVerdict


class Dimensions(BaseModel):
    """The seven parts of the circulation score, each 0-100. None where a part does not
    apply to the storey — arrival upstairs, vertical circulation with no stair."""

    model_config = ConfigDict(extra="forbid")

    connectivity: float = Field(ge=0, le=100)
    relationships: float = Field(ge=0, le=100)
    privacy: float = Field(ge=0, le=100)
    journeys: float = Field(ge=0, le=100)
    efficiency: float = Field(ge=0, le=100)
    vertical: float | None = Field(default=None, ge=0, le=100)
    arrival: float | None = Field(default=None, ge=0, le=100)


class CirculationSummary(DerivedFieldsAreOutputOnly):
    """Stage ⑦'s circulation verdict on one storey, and the evidence behind it."""

    model_config = ConfigDict(extra="forbid")

    ruleset: str = Field(description="`circulation_v1@hash` — which rules produced this.")
    passed: bool = Field(description="False whenever there is a critical finding.")
    health: Health
    score: float = Field(
        ge=0, le=100,
        description="The weighted quality, reduced by the validity a critical failure "
        "takes away. A failed storey cannot score well.",
    )
    quality: float = Field(
        ge=0, le=100,
        description="Before any critical failure is applied, so two failed plans can "
        "still be told apart.",
    )
    dimensions: Dimensions
    circulation_share: float = Field(
        ge=0, description="Corridors and foyers as a fraction of the storey's floor."
    )
    critical: int = Field(ge=0)
    major: int = Field(ge=0)
    minor: int = Field(ge=0)
    access: list[Access] = Field(default_factory=list)
    isolation: list[Isolation] = Field(default_factory=list)
    journeys: list[Journey] = Field(default_factory=list)
    corridors: list[Corridor] = Field(default_factory=list)
    graph: CirculationGraph
