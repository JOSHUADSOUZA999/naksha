import { useMemo, useRef, useState } from "react";
import { Stage, Layer, Rect, Text, Group } from "react-konva";
import type Konva from "konva";
import type { Layout, PlacedRoom, RoomSpec } from "./types";

const FILLS: Record<string, string> = {
  hall: "#eef2f7", dining: "#eef2f7", kitchen: "#fdf1e3",
  master_bedroom: "#f0f4ec", bedroom: "#f0f4ec",
  bathroom: "#e8f1f5", wc: "#e8f1f5",
  pooja: "#f7efe0", car_parking: "#f1f1f1",
  corridor: "#fafafa", foyer: "#fafafa", staircase: "#ededf3",
};

interface Props {
  layout: Layout;
  specs: Map<string, RoomSpec>;
  width: number;
  height: number;
  selected: string | null;
  onSelect: (roomId: string | null) => void;
}

export function FloorPlan({ layout, specs, width, height, selected, onSelect }: Props) {
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
                stroke={isSelected ? "#2563eb" : undersized ? "#dc2626" : "#222"}
                strokeWidth={isSelected ? 3 : undersized ? 2.5 : 1.5}
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
                    text={`${room.area_sq_m.toFixed(1)} m²`}
                    fontSize={9.5}
                    fill="#666"
                    listening={false}
                  />
                </>
              )}
            </Group>
          );
        })}
      </Layer>
    </Stage>
  );
}
