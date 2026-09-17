import { useEffect, useMemo, useState } from "react";
import { FloorPlan, type Highlight } from "./FloorPlan";
import { GRADE_STYLE, bySeverity, gradeOf } from "./grades";
import type {
  CirculationGraph, CirculationSummary, Corridor, Dimensions, Finding, Journey, PlanBundle, RoomSpec,
} from "./types";
import { feetAndInches, squareFeet } from "./units";

/** How a storey's circulation reads at a glance. Fair and poor both need improvement; the
 *  number says how much. */
const HEALTH: Record<CirculationSummary["health"], { label: string; colour: string }> = {
  good: { label: "Good", colour: "#2e7d32" },
  fair: { label: "Needs improvement", colour: "#a15c07" },
  poor: { label: "Needs improvement", colour: "#b4460a" },
  fail: { label: "Critical issue", colour: "#c62828" },
};

const SECTION = { fontSize: 13, margin: "0 0 8px" } as const;
const EYEBROW = {
  fontSize: 10, fontWeight: 600, letterSpacing: "0.08em", textTransform: "uppercase",
  color: "#777", margin: "0 0 4px",
} as const;

export default function App() {
  const [bundle, setBundle] = useState<PlanBundle | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [floorIndex, setFloorIndex] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  // Which finding is open. Undefined until someone chooses: a storey that fails then
  // leads with its first critical finding, explained, and one that passes opens nothing.
  const [open, setOpen] = useState<number | null | undefined>(undefined);
  const [advanced, setAdvanced] = useState(false);

  useEffect(() => {
    fetch("/plan.json")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(setBundle)
      .catch((e) => setError(String(e)));
  }, []);

  const specs = useMemo(() => {
    const map = new Map<string, RoomSpec>();
    bundle?.program.rooms.forEach((r) => map.set(r.id, r));
    return map;
  }, [bundle]);

  if (error) {
    return (
      <p style={{ padding: 32, color: "#b91c1c" }}>
        Could not load <code>/plan.json</code>: {error}
        <br />
        Generate one with:{" "}
        <code>
          naksha-intent --allow-unverified -L frontend/public/plan.json "30x40 …"
        </code>
      </p>
    );
  }
  if (!bundle) return <p style={{ padding: 32 }}>Loading…</p>;

  const layout = bundle.layouts[floorIndex];
  // Positional, matching how the backend builds them — one drawing and one report per
  // layout, in order. Absent when the bundle stopped at stage ⑤.
  const refined = bundle.floors?.[floorIndex];
  const report = bundle.reports?.[floorIndex];
  const circulation = report?.circulation ?? undefined;
  const findings = report ? [...report.findings].sort(bySeverity) : [];
  const current = open === undefined
    ? (findings.length && gradeOf(findings[0]) === "critical" ? 0 : null)
    : open;
  const focused = current === null ? undefined : findings[current];
  const highlight: Highlight | null = focused
    ? { rooms: focused.rooms, path: focused.path ?? [], grade: gradeOf(focused) }
    : null;

  const spec = selected ? specs.get(selected) : undefined;
  const placed = selected ? layout.rooms.find((r) => r.room_id === selected) : undefined;
  // Inside the walls, like the drawing's labels: the centreline rectangle credits every
  // room with half a wall a side.
  const inside = selected ? refined?.clear?.[selected] : undefined;
  const clear = placed && (inside
    ? { across: inside[2] - inside[0], deep: inside[3] - inside[1] }
    : { across: placed.width_m, deep: placed.depth_m });
  const label = (id: string) => nodeLabel(id, specs);

  const chooseFloor = (i: number) => {
    setFloorIndex(i);
    setSelected(null);
    setOpen(undefined);
  };

  return (
    <div style={{ display: "flex", height: "100vh" }}>
      <div style={{ flex: 1, position: "relative", background: "#fff" }}>
        <FloorPlan
          layout={layout}
          refined={refined}
          specs={specs}
          width={window.innerWidth - 340}
          height={window.innerHeight}
          selected={selected}
          onSelect={setSelected}
          highlight={highlight}
          graph={circulation?.graph}
        />
      </div>

      <aside style={{ width: 340, borderLeft: "1px solid #e5e5e5", padding: 20, overflowY: "auto", boxSizing: "border-box" }}>
        <h1 style={{ fontSize: 15, margin: "0 0 4px" }}>{bundle.brief_text}</h1>
        <p style={{ fontSize: 11, color: "#888", margin: "0 0 16px" }}>
          seed {bundle.seed} · schematic, not a sanction drawing
        </p>

        <div style={{ display: "flex", gap: 6, marginBottom: 18 }}>
          {bundle.layouts.map((l, i) => (
            <button
              key={l.floor}
              type="button"
              onClick={() => chooseFloor(i)}
              style={{
                padding: "5px 11px", fontSize: 12, cursor: "pointer",
                border: "1px solid " + (i === floorIndex ? "#2563eb" : "#d4d4d4"),
                background: i === floorIndex ? "#eff6ff" : "#fff", borderRadius: 5,
              }}
            >
              {l.floor === 1 ? "Ground" : `Floor ${l.floor - 1}`}
            </button>
          ))}
        </div>

        {circulation && <CirculationScore summary={circulation} findings={findings} />}

        {findings.length > 0 && (
          <section style={{ marginBottom: 20 }}>
            <h2 style={SECTION}>Diagnostics</h2>
            <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "grid", gap: 6 }}>
              {findings.map((f, i) => {
                const style = GRADE_STYLE[gradeOf(f)];
                const expanded = current === i;
                return (
                  <li key={i}>
                    <button
                      type="button"
                      aria-expanded={expanded}
                      onClick={() => {
                        setOpen(expanded ? null : i);
                        if (f.rooms.length) setSelected(f.rooms[0]);
                      }}
                      style={{
                        display: "block", width: "100%", textAlign: "left", font: "inherit",
                        fontSize: 12, lineHeight: 1.45, cursor: "pointer", color: "#1f1f1f",
                        padding: "7px 9px", border: "none", borderRadius: 4,
                        borderLeft: `3px solid ${style.ink}`, background: style.wash,
                      }}
                    >
                      <span style={{ ...EYEBROW, display: "block", margin: "0 0 2px", color: style.ink }}>
                        {style.label}
                      </span>
                      {capitalise(f.message)}
                    </button>
                    {expanded && (f.why || f.fix) && (
                      <div style={{ fontSize: 12, lineHeight: 1.5, padding: "8px 10px 2px 12px", color: "#333" }}>
                        {f.why && (
                          <>
                            <h3 style={EYEBROW}>Why it matters</h3>
                            <p style={{ margin: "0 0 10px" }}>{f.why}</p>
                          </>
                        )}
                        {f.fix && (
                          <>
                            <h3 style={EYEBROW}>Recommended fix</h3>
                            <p style={{ margin: "0 0 6px" }}>{f.fix}</p>
                          </>
                        )}
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          </section>
        )}

        {report && (report.cross_ventilated?.length || report.single_sided?.length) ? (
          <section style={{ marginBottom: 20 }}>
            <h2 style={SECTION}>Air</h2>
            {/* Not findings: a room open on one side is legal. Shown so an owner can see
                which rooms a breeze can cross and which it cannot. */}
            <Row label="from two sides" value={report.cross_ventilated?.join(", ") || "none"} />
            <Row label="one side only" value={report.single_sided?.join(", ") || "none"} />
          </section>
        ) : null}

        {spec && placed && clear ? (
          <section style={{ marginBottom: 20 }}>
            <h2 style={SECTION}>
              {spec.kind.replace(/_/g, " ")}
            </h2>
            {/* Feet for the owner, metres beneath: the minimums are metric. */}
            <Row label="area" value={squareFeet(clear.across * clear.deep)}
                 note={`${(clear.across * clear.deep).toFixed(1)} m² · min ${spec.min_area_sq_m} m², target ${spec.target_area_sq_m} m²`} />
            <Row label="size" value={`${feetAndInches(clear.across)} × ${feetAndInches(clear.deep)}`}
                 note={`${clear.across.toFixed(2)} × ${clear.deep.toFixed(2)} m · min width ${spec.min_width_m} m`} />
            {spec.sector && <Row label="wanted" value={spec.sector.replace(/_/g, " ")} />}
          </section>
        ) : (
          <p style={{ fontSize: 12, color: "#888" }}>Click a room or a finding. Scroll to zoom, drag to pan.</p>
        )}

        <button
          type="button"
          aria-expanded={advanced}
          onClick={() => setAdvanced(!advanced)}
          style={{
            font: "inherit", fontSize: 12, color: "#2563eb", background: "none", border: "none",
            padding: 0, margin: "4px 0 14px", cursor: "pointer",
          }}
        >
          {advanced ? "Hide advanced" : "Advanced"}
        </button>

        {advanced && (
          <div>
            {focused?.path?.length ? (
              <section style={{ marginBottom: 16 }}>
                <h2 style={SECTION}>Route of the open finding</h2>
                <p style={{ fontSize: 12, lineHeight: 1.5, margin: 0 }}>
                  {focused.path.filter((n) => n !== "@street").map(label).join(" → ")}
                </p>
              </section>
            ) : null}
            {circulation && <DimensionRows summary={circulation} />}
            {circulation && circulation.journeys.length > 0 && (
              <JourneyTable journeys={circulation.journeys} weighted={circulation.dimensions.journeys} label={label} />
            )}
            {circulation && circulation.corridors.length > 0 && (
              <CorridorRows corridors={circulation.corridors} label={label} />
            )}
            {circulation && <GraphSummary graph={circulation.graph} label={label} />}

            <h2 style={SECTION}>
              Unmet on this floor · score {layout.score.toFixed(0)}
            </h2>
            {layout.violations.length === 0 ? (
              <p style={{ fontSize: 12, color: "#16a34a" }}>Every constraint met.</p>
            ) : (
              <ul style={{ fontSize: 11.5, color: "#555", paddingLeft: 16, margin: 0, lineHeight: 1.55 }}>
                {layout.violations.map((v, i) => <li key={i}>{v}</li>)}
              </ul>
            )}
            {circulation && (
              <p style={{ fontSize: 10.5, color: "#999", margin: "14px 0 0" }}>rules {circulation.ruleset}</p>
            )}
          </div>
        )}
      </aside>
    </div>
  );
}

/** The counts are of every finding on the storey, not the circulation engine's alone: they
 *  sit above the whole list, and a badge saying 3 major over four major findings reads as
 *  a bug in the plan rather than a scope in the counter. */
function CirculationScore({ summary, findings }: { summary: CirculationSummary; findings: Finding[] }) {
  const health = HEALTH[summary.health];
  return (
    <section style={{ marginBottom: 20 }}>
      <h2 style={EYEBROW}>Circulation</h2>
      <div style={{ display: "flex", alignItems: "baseline", gap: 4, margin: "2px 0 4px" }}>
        <span style={{ fontSize: 34, fontWeight: 600, lineHeight: 1, fontVariantNumeric: "tabular-nums" }}>
          {Math.round(summary.score)}
        </span>
        <span style={{ fontSize: 13, color: "#888" }}>/ 100</span>
      </div>
      <p style={{ display: "flex", alignItems: "center", gap: 6, margin: "0 0 12px", fontSize: 12.5 }}>
        <span aria-hidden="true" style={{ width: 8, height: 8, borderRadius: "50%", background: health.colour }} />
        <strong style={{ color: health.colour, fontWeight: 600 }}>{health.label}</strong>
        {!summary.passed && (
          <span style={{ color: "#999" }}>· {Math.round(summary.quality)} before its critical issues</span>
        )}
      </p>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 6 }}>
        {(["critical", "major", "minor"] as const).map((grade) => {
          const count = findings.filter((f) => gradeOf(f) === grade).length;
          const style = GRADE_STYLE[grade];
          return (
            <div key={grade} style={{ border: "1px solid #e8e8e8", borderRadius: 5, padding: "6px 8px" }}>
              <div style={{ ...EYEBROW, margin: 0, color: count ? style.ink : "#999" }}>{style.label}</div>
              <div style={{ fontSize: 18, fontWeight: 600, fontVariantNumeric: "tabular-nums", color: count ? style.ink : "#bbb" }}>
                {count}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

const DIMENSION_NAMES: [keyof Dimensions, string][] = [
  ["connectivity", "Access"],
  ["relationships", "Rooms together"],
  ["privacy", "Privacy"],
  ["journeys", "Everyday walks"],
  ["efficiency", "Circulation area"],
  ["vertical", "Stair"],
  ["arrival", "Arrival"],
];

function DimensionRows({ summary }: { summary: CirculationSummary }) {
  return (
    <section style={{ marginBottom: 16 }}>
      <h2 style={SECTION}>Score, part by part</h2>
      {DIMENSION_NAMES.map(([key, name]) => {
        const value = summary.dimensions[key];
        if (value === null) return null;
        const colour = value >= 80 ? "#2e7d32" : value >= 60 ? "#a15c07" : "#c62828";
        return (
          <div key={key} style={{ display: "grid", gridTemplateColumns: "112px 1fr 26px", alignItems: "center", gap: 8, fontSize: 11.5, marginBottom: 4 }}>
            <span style={{ color: "#666" }}>{name}</span>
            <span style={{ height: 5, background: "#eee", borderRadius: 3, overflow: "hidden" }}>
              <span style={{ display: "block", height: "100%", width: `${value}%`, background: colour }} />
            </span>
            <span style={{ textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{Math.round(value)}</span>
          </div>
        );
      })}
      <p style={{ fontSize: 11, color: "#888", margin: "6px 0 0" }}>
        Corridors and foyers take {Math.round(summary.circulation_share * 100)}% of the floor.
      </p>
    </section>
  );
}

const TH = { padding: "3px 6px 3px 0", fontWeight: 500 } as const;
const TD = { padding: "4px 6px 4px 0", verticalAlign: "top" } as const;

function JourneyTable(
  { journeys, weighted, label }: { journeys: Journey[]; weighted: number; label: (id: string) => string },
) {
  return (
    <section style={{ marginBottom: 16 }}>
      <h2 style={SECTION}>Journeys · weighted score {Math.round(weighted)}</h2>
      <div style={{ overflowX: "auto" }}>
        <table style={{ borderCollapse: "collapse", fontSize: 11, width: "100%", fontVariantNumeric: "tabular-nums" }}>
          <thead>
            <tr style={{ color: "#888", textAlign: "left" }}>
              <th style={TH}>walk</th>
              <th style={TH} title="metres walked, door to door">m</th>
              <th style={TH} title="rooms passed through">via</th>
              <th style={TH}>turns</th>
              <th style={TH} title="rooms more private than either end">privacy</th>
              <th style={TH}>score</th>
            </tr>
          </thead>
          <tbody>
            {journeys.map((j, i) => (
              <tr key={i} style={{ borderTop: "1px solid #f0f0f0" }}
                  title={j.path.filter((n) => n !== "@street").map(label).join(" → ")}>
                <td style={TD}>
                  {label(j.origin)} → {label(j.destination)}
                  {j.inappropriate.length > 0 && (
                    <span style={{ color: GRADE_STYLE.critical.ink }}> · through {j.inappropriate.map(label).join(", ")}</span>
                  )}
                </td>
                <td style={TD}>{j.reachable ? j.distance_m.toFixed(1) : "—"}</td>
                <td style={TD}>{j.transitions}</td>
                <td style={TD}>{j.turns}</td>
                <td style={TD}>{j.privacy_crossings}</td>
                <td style={{ ...TD, fontWeight: 600 }}>{Math.round(j.score)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function CorridorRows({ corridors, label }: { corridors: Corridor[]; label: (id: string) => string }) {
  return (
    <section style={{ marginBottom: 16 }}>
      <h2 style={SECTION}>Corridors</h2>
      {corridors.map((c) => (
        <Row
          key={c.room}
          label={label(c.room)}
          value={c.verdict}
          note={`${squareFeet(c.area_sq_m)} · ${feetAndInches(c.width_m)} wide · ${c.rooms_served} doors`
            + (c.dead_end_m > 0.05 ? ` · ${feetAndInches(c.dead_end_m)} dead end` : "")}
        />
      ))}
    </section>
  );
}

function GraphSummary({ graph, label }: { graph: CirculationGraph; label: (id: string) => string }) {
  const rooms = graph.nodes.filter((n) => !n.id.startsWith("@")).length;
  const forced = graph.edges.filter((e) => e.kind === "forced_pass_through");
  return (
    <section style={{ marginBottom: 16 }}>
      <h2 style={SECTION}>Graph</h2>
      <p style={{ fontSize: 11.5, color: "#555", margin: "0 0 6px", lineHeight: 1.5 }}>
        {rooms} rooms and {graph.edges.length} connections, entered at{" "}
        {graph.arrival ? label(graph.arrival) : "no point at all"}.
      </p>
      {forced.length > 0 && (
        <ul style={{ fontSize: 11.5, color: "#555", paddingLeft: 16, margin: 0, lineHeight: 1.55 }}>
          {forced.map((e, i) => (
            <li key={i}>{label(e.a)} ↔ {label(e.b)}: a room used as a passage</li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Row({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 12, fontSize: 12, marginBottom: 5 }}>
      <span style={{ color: "#888" }}>{label}</span>
      <span style={{ textAlign: "right" }}>
        {value}
        {note && <em style={{ display: "block", color: "#aaa", fontSize: 10.5, fontStyle: "normal" }}>{note}</em>}
      </span>
    </div>
  );
}

/** A graph node as a person reads it: the doorstep and the street by name, a room by its
 *  kind with its id where the id is not already the kind. */
function nodeLabel(id: string, specs: Map<string, RoomSpec>): string {
  if (id === "@entry") return "front door";
  if (id === "@street") return "street";
  if (id === "@below") return "stair from below";
  const kind = specs.get(id)?.kind.replace(/_/g, " ");
  return kind && kind !== id ? `${kind} (${id})` : id;
}

function capitalise(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}
