import { useMemo, useRef, useState } from "react";
import { Stage, Layer, Rect, Text, Group, Line, Ellipse, Circle } from "react-konva";
import type Konva from "konva";
import type { Fixture, Layout, PlacedRoom, RefinedFloor, RoomSpec, Wall } from "./types";

const FILLS: Record<string, string> = {
  hall: "#eef2f7", dining: "#eef2f7", kitchen: "#fdf1e3",
  master_bedroom: "#f0f4ec", bedroom: "#f0f4ec",
  bathroom: "#e8f1f5", wc: "#e8f1f5",
  pooja: "#f7efe0", car_parking: "#f1f1f1",
  corridor: "#fafafa", foyer: "#fafafa", staircase: "#ededf3",
};

interface Props {
  layout: Layout;
  /** Stage ⑥'s drawing. Absent when the bundle stopped at ⑤, and the viewer then
   *  falls back to outlined rectangles — which is what it drew before ⑥ existed. */
  refined?: RefinedFloor;
  specs: Map<string, RoomSpec>;
  width: number;
  height: number;
  selected: string | null;
  onSelect: (roomId: string | null) => void;
}

export function FloorPlan({ layout, refined, specs, width, height, selected, onSelect }: Props) {
  const stageRef = useRef<Konva.Stage>(null);
  const [zoom, setZoom] = useState(1);

  const plan = {
    width: layout.x_max_m - layout.x_min_m,
    depth: layout.y_max_m - layout.y_min_m,
  };

  // Fit the plan to the viewport once, then let the wheel take over. Recomputed only
  // when the plan or the viewport changes, so zooming does not fight the fit.
  const base = useMemo(() => {
    const pad = 48;
    return Math.min((width - pad * 2) / plan.width, (height - pad * 2) / plan.depth);
  }, [width, height, plan.width, plan.depth]);

  const scale = base * zoom;

  /** Floor inside the plaster. Stage ⑤'s rectangles run to the wall centrelines and
   *  overstate every room by half a wall a side — the SVG renderer has shown the clear
   *  figure since ⑥ existed, and a viewer quoting the other one makes two consumers of
   *  one plan disagree about how big a bathroom is. */
  const clearArea = (room: PlacedRoom) => {
    const r = refined?.clear?.[room.room_id];
    return r ? Math.max(0, r[2] - r[0]) * Math.max(0, r[3] - r[1]) : room.area_sq_m;
  };
  const originX = (width - plan.width * scale) / 2;
  const originY = (height - plan.depth * scale) / 2;

  /** IR is y-north, the screen is y-down. Flipping here keeps north-is-up a
   *  presentation concern rather than something the solver has to know about. */
  const toScreen = (xM: number, yM: number) => ({
    x: originX + (xM - layout.x_min_m) * scale,
    y: originY + (layout.y_max_m - yM) * scale,
  });

  const onWheel = (e: Konva.KonvaEventObject<WheelEvent>) => {
    e.evt.preventDefault();
    setZoom((z) => Math.min(6, Math.max(0.4, z * (e.evt.deltaY < 0 ? 1.08 : 0.926))));
  };

  return (
    <Stage
      ref={stageRef}
      width={width}
      height={height}
      draggable
      onWheel={onWheel}
      // A click that hits no shape is a deselect. Without this the details panel
      // sticks on the last room forever and there is no way to dismiss it.
      onMouseDown={(e) => { if (e.target === e.target.getStage()) onSelect(null); }}
    >
      <Layer>
        {layout.rooms.map((room: PlacedRoom) => {
          const spec = specs.get(room.room_id);
          const kind = spec?.kind ?? "";
          const topLeft = toScreen(room.x_min_m, room.y_max_m);
          const w = room.width_m * scale;
          const h = room.depth_m * scale;
          const isSelected = selected === room.room_id;
          const undersized = spec ? room.area_sq_m < spec.min_area_sq_m : false;

          return (
            <Group
              key={room.room_id}
              onClick={() => onSelect(room.room_id)}
              onTap={() => onSelect(room.room_id)}
              onMouseEnter={() => {
                const c = stageRef.current?.container();
                if (c) c.style.cursor = "pointer";
              }}
              onMouseLeave={() => {
                const c = stageRef.current?.container();
                if (c) c.style.cursor = "default";
              }}
            >
              <Rect
                x={topLeft.x}
                y={topLeft.y}
                width={w}
                height={h}
                fill={FILLS[kind] ?? "#f6f6f6"}
                // Undersized rooms are outlined rather than tinted: the fill already
                // encodes what a room *is*, and losing that to show a problem trades
                // one piece of information for another.
                // No outline once real walls are drawn over the top: a 1.5 px stroke
                // on the tile plus a wall at its true thickness reads as a double
                // line, and the tile edge is the wall's *centreline* so the two do
                // not even coincide. Selection and undersize still need to show.
                stroke={isSelected ? "#2563eb" : undersized ? "#dc2626" : refined ? undefined : "#222"}
                strokeWidth={isSelected ? 3 : undersized ? 2.5 : refined ? 0 : 1.5}
              />
              {w > 56 && h > 30 && (
                <>
                  <Text
                    x={topLeft.x}
                    y={topLeft.y + h / 2 - 13}
                    width={w}
                    align="center"
                    text={kind.replace(/_/g, " ") || room.room_id}
                    fontSize={11}
                    fill="#111"
                    listening={false}
                  />
                  <Text
                    x={topLeft.x}
                    y={topLeft.y + h / 2 + 1}
                    width={w}
                    align="center"
                    text={`${clearArea(room).toFixed(1)} m²`}
                    fontSize={9.5}
                    fill="#666"
                    listening={false}
                  />
                </>
              )}
            </Group>
          );
        })}

        {/* Fixtures under the walls: a fixture is inside a room and a wall is the
            room's edge, so masonry over a bed reads right and a bed over masonry
            does not. */}
        {refined?.fixtures.map((f: Fixture, i: number) => {
          const tl = toScreen(f.x_min_m, f.y_max_m);
          const br = toScreen(f.x_max_m, f.y_min_m);
          const w = br.x - tl.x;
          const h = br.y - tl.y;
          const cx = tl.x + w / 2;
          const cy = tl.y + h / 2;
          return (
            <Group key={`fx${i}`} listening={false}>
              <Rect x={tl.x} y={tl.y} width={w} height={h} fill="#ffffff" opacity={0.55} />
              {["wc", "basin", "sink"].includes(f.kind) && (
                <Ellipse x={cx} y={cy} radiusX={w * 0.34} radiusY={h * 0.34}
                         stroke="#8a8a8a" strokeWidth={1} />
              )}
              {f.kind === "shower" && (
                <>
                  <Line points={[tl.x, tl.y, br.x, br.y]} stroke="#8a8a8a" strokeWidth={1} />
                  <Line points={[br.x, tl.y, tl.x, br.y]} stroke="#8a8a8a" strokeWidth={1} />
                </>
              )}
              {f.kind === "stove" && [0.3, 0.7].flatMap((fx) =>
                [0.3, 0.7].map((fy) => (
                  <Circle key={`${fx}-${fy}`} x={tl.x + w * fx} y={tl.y + h * fy}
                          radius={Math.min(w, h) * 0.16} stroke="#8a8a8a" strokeWidth={1} />
                ))
              )}
              {(f.kind === "bed" || f.kind === "single_bed") && (
                <Line points={
                  f.faces === "north" || f.faces === "south"
                    ? [tl.x, f.faces === "north" ? br.y - h * 0.22 : tl.y + h * 0.22,
                       br.x, f.faces === "north" ? br.y - h * 0.22 : tl.y + h * 0.22]
                    : [f.faces === "west" ? tl.x + w * 0.22 : br.x - w * 0.22, tl.y,
                       f.faces === "west" ? tl.x + w * 0.22 : br.x - w * 0.22, br.y]
                } stroke="#8a8a8a" strokeWidth={1} />
              )}
              {f.kind === "wardrobe" && (
                <Line points={[tl.x, tl.y, br.x, br.y]} stroke="#8a8a8a" strokeWidth={1} />
              )}
              <Rect x={tl.x} y={tl.y} width={w} height={h} stroke="#8a8a8a" strokeWidth={1} />
            </Group>
          );
        })}

        {/* Walls at their true thickness, then openings punched back out of them.
            Punching rather than splitting each wall in two keeps one Konva line per
            `Wall` in the IR — a viewer that silently re-partitions the geometry is one
            whose output cannot be traced back to what produced it. */}
        {refined?.walls.map((wall: Wall) => {
          const a = toScreen(wall.x1_m, wall.y1_m);
          const b = toScreen(wall.x2_m, wall.y2_m);
          return (
            <Line key={wall.id} points={[a.x, a.y, b.x, b.y]} stroke="#1c1c1c"
                  strokeWidth={wall.thickness_m * scale} lineCap="butt" listening={false} />
          );
        })}

        {refined?.openings.map((op, i) => {
          const wall = refined.walls.find((w) => w.id === op.wall_id);
          if (!wall) return null;
          const t = (o: number) => {
            const f = wall.length_m > 0 ? o / wall.length_m : 0;
            return toScreen(wall.x1_m + (wall.x2_m - wall.x1_m) * f,
                            wall.y1_m + (wall.y2_m - wall.y1_m) * f);
          };
          const a = t(op.offset_m - op.width_m / 2);
          const b = t(op.offset_m + op.width_m / 2);
          const isWindow = op.kind === "window";
          const room = op.connects.length ? layout.rooms.find(
            (r) => r.room_id === op.connects[op.connects.length - 1]) : undefined;

          // The leaf falls into the room being entered, and the arc sweeps from the
          // leaf round to the far reveal. Deriving both from the same two vectors is
          // what keeps this identical to the SVG renderer: the first version picked a
          // Konva rotation by hand and put half the swings outside the building.
          const radius = Math.hypot(b.x - a.x, b.y - a.y);
          const mid = t(op.offset_m);
          let inward = { x: 0, y: -1 };
          if (room) {
            const c = toScreen((room.x_min_m + room.x_max_m) / 2,
                               (room.y_min_m + room.y_max_m) / 2);
            inward = wall.is_vertical
              ? { x: c.x > mid.x ? 1 : -1, y: 0 }
              : { x: 0, y: c.y > mid.y ? 1 : -1 };
          }
          const deg = (v: { x: number; y: number }) =>
            (Math.atan2(v.y, v.x) * 180) / Math.PI;
          const fromLeaf = deg(inward);
          const toReveal = deg({ x: b.x - a.x, y: b.y - a.y });
          // ±90: the leaf and the wall are perpendicular, and the sign is which way
          // round. Normalised into (-180, 180] so it never takes the long way.
          const sweep = ((toReveal - fromLeaf + 540) % 360) - 180;
          const leaf = { x: a.x + inward.x * radius, y: a.y + inward.y * radius };
          // The quarter circle as an explicit polyline. Konva's `Arc` is a wedge, and
          // with innerRadius === outerRadius it degenerates into something that drew
          // near-full circles floating outside the building. Twelve segments is past
          // the point anyone can see the facets at this scale.
          const arc: number[] = [];
          for (let k = 0; k <= 12; k++) {
            const th = ((fromLeaf + (sweep * k) / 12) * Math.PI) / 180;
            arc.push(a.x + Math.cos(th) * radius, a.y + Math.sin(th) * radius);
          }

          return (
            <Group key={`op${i}`} listening={false}>
              <Line points={[a.x, a.y, b.x, b.y]} stroke="#ffffff" lineCap="butt"
                    strokeWidth={wall.thickness_m * scale + 1} />
              {isWindow ? (
                <Line points={[a.x, a.y, b.x, b.y]} stroke="#3f6f8f" strokeWidth={1.4} />
              ) : (
                <>
                  <Line points={[a.x, a.y, leaf.x, leaf.y]} stroke="#555" strokeWidth={1.1} />
                  <Line points={arc} stroke="#555" strokeWidth={1.1} dash={[3, 3]} />
                </>
              )}
            </Group>
          );
        })}

        {/* A porch in the setback: dashed and unfilled, because it is a slab and a
            roof rather than a room, and drawing it like one would claim built-up area
            the plan does not have. */}
        {refined?.outside.map((room: PlacedRoom) => {
          const tl = toScreen(room.x_min_m, room.y_max_m);
          return (
            <Group key={`out-${room.room_id}`} listening={false}>
              <Rect x={tl.x} y={tl.y} width={room.width_m * scale}
                    height={room.depth_m * scale} fill="#f4f4f2"
                    stroke="#8a8a8a" strokeWidth={1.4} dash={[6, 4]} />
              <Text x={tl.x} y={tl.y + (room.depth_m * scale) / 2 - 6}
                    width={room.width_m * scale} align="center"
                    text={(specs.get(room.room_id)?.kind ?? room.room_id).replace(/_/g, " ")}
                    fontSize={10.5} fill="#6b7075" />
            </Group>
          );
        })}
      </Layer>
    </Stage>
  );
}
