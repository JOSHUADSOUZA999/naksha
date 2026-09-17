"""Stage ⑥'s drawing as a graph of the ways through a storey.

Built from what is drawn — every door, the front door, the car gates, the flight up from
the storey below — and from every wall two rooms share, door or not. The second half is
what lets a finding say *why* a room has no proper access: not "bed2 is unreachable" but
"bed2 shares 0.11 m with the corridor, and a door needs 1.05 m".
"""

from __future__ import annotations

from app.circulation import semantics
from app.ir.circulation import (
    CirculationEdge,
    CirculationGraph,
    CirculationNode,
    WallContact,
)
from app.ir.enums import (
    CirculationRole,
    EdgeKind,
    OpeningKind,
    Relation,
    SpaceKind,
    WallKind,
    Zone,
)
from app.ir.layout import Layout
from app.ir.plan import Program
from app.ir.refined import RefinedFloor
from app.rules import load_ruleset

ENTRY = "@entry"    # the doorstep outside the front door
STREET = "@street"  # the road side of the plot, where the gates and the front door open
BELOW = "@below"    # the flight arriving from the storey beneath
OUTSIDE = frozenset({ENTRY, STREET, BELOW})


def required_door_m() -> float:
    """The shortest wall a door fits in, from the rules stage ⑥ places doors by."""
    doors = load_ruleset("refine_v1").data["doors"]
    return doors["service_width_m"] + 2 * doors["clearance_m"]


def centre(rect: tuple[float, float, float, float]) -> tuple[float, float]:
    return ((rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2)


def build(layout: Layout, program: Program, floor: RefinedFloor) -> CirculationGraph:
    specs = {room.id: room for room in program.rooms}
    placed = [p for p in layout.rooms if p.room_id in specs]
    kinds = {p.room_id: specs[p.room_id].kind.value for p in placed}
    roles = {room_id: semantics.role_of(kind) for room_id, kind in kinds.items()}

    # An en-suite belongs to the bedroom stage ③ connected it to. Read from the
    # programme's edges, never from names or positions.
    parents: dict[str, list[str]] = {}
    for edge in program.adjacencies:
        if edge.relation is not Relation.CONNECTED:
            continue
        for bath, bed in ((edge.a, edge.b), (edge.b, edge.a)):
            if (
                roles.get(bath) is CirculationRole.SANITARY
                and roles.get(bed) is CirculationRole.PRIVATE
            ):
                parents.setdefault(bath, []).append(bed)

    walls = {wall.id: wall for wall in floor.walls}
    edges: list[CirculationEdge] = []
    doored: set[str] = set()
    for opening in floor.openings:
        wall = walls.get(opening.wall_id)
        if wall is None or not opening.connects:
            continue
        where = dict(
            width_m=opening.width_m,
            at=wall.point_at(opening.offset_m),
            vertical=wall.is_vertical,
        )
        if opening.kind is OpeningKind.DOOR and len(opening.connects) == 2:
            a, b = opening.connects
            service = {roles.get(a), roles.get(b)} == {CirculationRole.SERVICE}
            kind = EdgeKind.SERVICE_CONNECTION if service else EdgeKind.DIRECT_DOOR
            edges.append(CirculationEdge(a=a, b=b, kind=kind, **where))
            doored.add(wall.id)
        elif opening.kind is OpeningKind.OPEN and len(opening.connects) == 2:
            a, b = opening.connects
            edges.append(CirculationEdge(a=a, b=b, kind=EdgeKind.OPEN_CONNECTION, **where))
            doored.add(wall.id)
        elif opening.kind is OpeningKind.ENTRANCE:
            edges.append(
                CirculationEdge(
                    a=ENTRY, b=opening.connects[0], kind=EdgeKind.EXTERNAL_CONNECTION, **where
                )
            )
            edges.append(
                CirculationEdge(a=STREET, b=ENTRY, kind=EdgeKind.EXTERNAL_CONNECTION, **where)
            )
        elif opening.kind is OpeningKind.VEHICLE:
            edges.append(
                CirculationEdge(
                    a=opening.connects[0], b=STREET, kind=EdgeKind.EXTERNAL_CONNECTION, **where
                )
            )

    stairs = [room_id for room_id, kind in kinds.items() if kind == SpaceKind.STAIRCASE.value]
    if layout.floor > 1:
        for stair in stairs:
            rect = floor.clear.get(stair)
            edges.append(
                CirculationEdge(
                    a=BELOW, b=stair, kind=EdgeKind.STAIR_CONNECTION,
                    at=centre(rect) if rect else None,
                )
            )

    nodes = [
        CirculationNode(
            id=room_id,
            kind=kinds[room_id],
            role=roles[room_id],
            zone=semantics.zone_of(kinds[room_id], shared=room_id not in parents)
            or _derived_zone(room_id, edges, kinds, parents),
            walk_in=specs[room_id].needs_door,
            parents=parents.get(room_id, []),
            rect=floor.clear.get(room_id),
        )
        for room_id in kinds
    ]
    used = {end for edge in edges for end in (edge.a, edge.b)} & OUTSIDE
    for outside in sorted(used):
        nodes.append(
            CirculationNode(
                id=outside, kind="outside", role=CirculationRole.OUTSIDE,
                zone=Zone.SEMI_PRIVATE if outside == BELOW else Zone.EXTERNAL,
                walk_in=False,
            )
        )

    need = required_door_m()
    contacts = [
        WallContact(
            a=wall.rooms[0], b=wall.rooms[1], shared_m=wall.length_m,
            door_possible=wall.length_m >= need - 1e-9, has_door=wall.id in doored,
        )
        for wall in floor.walls
        if wall.kind is WallKind.INTERIOR and len(wall.rooms) == 2
    ]

    if ENTRY in used:
        arrival = ENTRY
    elif layout.floor > 1 and stairs:
        arrival = stairs[0]
    else:
        arrival = None
    return CirculationGraph(
        floor=layout.floor, arrival=arrival, required_door_m=need,
        nodes=nodes, edges=edges, contacts=contacts,
    )


def _derived_zone(room_id, edges, kinds, parents) -> Zone:
    """A corridor or stair is as private as what opens off it, and never more than
    semi-private: it is still the way between rooms, not a room of its own."""
    ranks = []
    for edge in edges:
        if edge.kind not in (EdgeKind.DIRECT_DOOR, EdgeKind.SERVICE_CONNECTION):
            continue
        if room_id not in (edge.a, edge.b):
            continue
        other = edge.b if edge.a == room_id else edge.a
        zone = semantics.zone_of(kinds.get(other, ""), shared=other not in parents)
        if zone is not None:
            ranks.append(semantics.ZONE_RANK[zone])
    return Zone.SEMI_PRIVATE if ranks and max(ranks) >= 2 else Zone.SEMI_PUBLIC
