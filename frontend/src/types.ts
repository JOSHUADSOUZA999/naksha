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

export interface PlanBundle {
  brief_text: string;
  program: { rooms: RoomSpec[] };
  layouts: Layout[];
  seed: number;
  total_score: number;
}
