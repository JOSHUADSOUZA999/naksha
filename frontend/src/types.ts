/** Mirrors `app.ir.layout.PlanBundle`. Kept hand-written and narrow on purpose:
 *  the viewer reads a subset, and a generated mirror of the whole IR would couple
 *  every backend field change to a frontend rebuild. */

export interface PlacedRoom {
  room_id: string;
  x_min_m: number;
  y_min_m: number;
  x_max_m: number;
  y_max_m: number;
  width_m: number;
  depth_m: number;
  area_sq_m: number;
}

export interface Layout {
  rooms: PlacedRoom[];
  x_min_m: number;
  y_min_m: number;
  x_max_m: number;
  y_max_m: number;
  floor: number;
  score: number;
  violations: string[];
}

export interface RoomSpec {
  id: string;
  kind: string;
  min_area_sq_m: number;
  target_area_sq_m: number;
  min_width_m: number;
  sector: string | null;
  floor: number;
}

/** Stage ⑥. `Wall` carries a CENTRELINE and a thickness, not two faces — the wall
 *  sits on the line stage ⑤ tiled to and eats equally into the rooms either side, so
 *  the viewer strokes the centreline at `thickness_m` rather than reconstructing
 *  edges. Mirrors `app.ir.refined`. */
export interface Wall {
  id: string;
  x1_m: number;
  y1_m: number;
  x2_m: number;
  y2_m: number;
  thickness_m: number;
  kind: "exterior" | "interior";
  rooms: string[];
  length_m: number;
  is_vertical: boolean;
}

/** A hole in one wall, at an offset along it. Never free-floating: an opening that
 *  carried its own coordinates could drift away from the wall it is a hole in. */
export interface Opening {
  wall_id: string;
  kind: "door" | "entrance" | "window";
  offset_m: number;
  width_m: number;
  height_m: number | null;
  connects: string[];
}

export interface Fixture {
  kind: string;
  room_id: string;
  x_min_m: number;
  y_min_m: number;
  x_max_m: number;
  y_max_m: number;
  faces: string;
}

export interface RefinedFloor {
  floor: number;
  walls: Wall[];
  openings: Opening[];
  fixtures: Fixture[];
  outside: PlacedRoom[];
  clear: Record<string, [number, number, number, number]>;
}

/** Stage ⑦. A finding names the rooms it is about, because a defect the user cannot
 *  locate on the drawing is one they cannot fix. */
export interface Finding {
  check: string;
  severity: "error" | "warning";
  message: string;
  rooms: string[];
}

export interface Report {
  floor: number;
  findings: Finding[];
  checks_run: string[];
  errors: number;
  ok: boolean;
}

export interface PlanBundle {
  brief_text: string;
  program: { rooms: RoomSpec[] };
  layouts: Layout[];
  /** Empty when the bundle stopped at stage ⑤ — the viewer then draws rectangles. */
  floors: RefinedFloor[];
  reports: Report[];
  seed: number;
  total_score: number;
}
