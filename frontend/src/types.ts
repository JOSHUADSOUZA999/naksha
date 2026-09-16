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
  kind: "door" | "entrance" | "window" | "vehicle" | "ventilator";
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

/** How much a finding matters. Critical refuses the storey; major and minor are warnings. */
export type Grade = "critical" | "major" | "minor";

/** Stage ⑦. A finding names the rooms it is about, because a defect the user cannot
 *  locate on the drawing is one they cannot fix. */
export interface Finding {
  check: string;
  severity: "error" | "warning";
  message: string;
  rooms: string[];
  /** The fields below are absent from plans checked before findings were graded. */
  grade?: Grade;
  rule?: string | null;
  why?: string | null;
  fix?: string | null;
  /** The route involved, room by room. `@entry` is the doorstep outside the front door,
   *  `@street` the road, `@below` the flight up from the storey beneath. */
  path?: string[];
}

/** Mirrors `app.ir.circulation`: how a storey is walked, and the evidence for its score. */
export interface CirculationNode {
  id: string;
  kind: string;
  role: string;
  zone: string;
}

export interface CirculationEdge {
  a: string;
  b: string;
  kind: string;
  width_m: number | null;
  /** Where a walk passes through: the midpoint of the opening. */
  at: [number, number] | null;
}

export interface CirculationGraph {
  floor: number;
  arrival: string | null;
  nodes: CirculationNode[];
  edges: CirculationEdge[];
}

export interface Journey {
  id: string;
  journey_class: string;
  weight: number;
  origin: string;
  destination: string;
  reachable: boolean;
  path: string[];
  distance_m: number;
  doors: number;
  transitions: number;
  turns: number;
  privacy_crossings: number;
  inappropriate: string[];
  backtracking: number;
  forced_pass_through: boolean;
  external: boolean;
  score: number;
}

export interface Corridor {
  room: string;
  area_sq_m: number;
  length_m: number;
  width_m: number;
  share: number;
  rooms_served: number;
  private_served: number;
  branches: number;
  dead_end_m: number;
  journey_weight: number;
  alternatives: number;
  essential: boolean;
  verdict: "essential" | "efficient" | "inefficient" | "redundant";
}

/** Each part of the score, 0-100. Null where it does not apply to the storey. */
export interface Dimensions {
  connectivity: number;
  relationships: number;
  privacy: number;
  journeys: number;
  efficiency: number;
  vertical: number | null;
  arrival: number | null;
}

export interface CirculationSummary {
  ruleset: string;
  passed: boolean;
  health: "good" | "fair" | "poor" | "fail";
  score: number;
  /** Before critical findings take their share: tells two failed storeys apart. */
  quality: number;
  dimensions: Dimensions;
  circulation_share: number;
  critical: number;
  major: number;
  minor: number;
  journeys: Journey[];
  corridors: Corridor[];
  graph: CirculationGraph;
}

export interface Report {
  floor: number;
  findings: Finding[];
  checks_run: string[];
  errors: number;
  ok: boolean;
  /** Rooms that want fresh air and get it from two sides, and those that do not.
   *  Measurements rather than findings; absent from plans drawn before they existed. */
  cross_ventilated?: string[];
  single_sided?: string[];
  /** Absent from plans checked before the circulation engine existed. */
  circulation?: CirculationSummary | null;
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
