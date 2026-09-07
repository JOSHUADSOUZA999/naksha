import { useEffect, useMemo, useState } from "react";
import { FloorPlan } from "./FloorPlan";
import type { PlanBundle, RoomSpec } from "./types";

export default function App() {
  const [bundle, setBundle] = useState<PlanBundle | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [floorIndex, setFloorIndex] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);

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
  const spec = selected ? specs.get(selected) : undefined;
  const placed = selected ? layout.rooms.find((r) => r.room_id === selected) : undefined;

  return (
    <div style={{ display: "flex", height: "100vh" }}>
      <div style={{ flex: 1, position: "relative", background: "#fff" }}>
        <FloorPlan
          layout={layout}
          specs={specs}
          width={window.innerWidth - 320}
          height={window.innerHeight}
          selected={selected}
          onSelect={setSelected}
        />
      </div>

      <aside style={{ width: 320, borderLeft: "1px solid #e5e5e5", padding: 20, overflowY: "auto" }}>
        <h1 style={{ fontSize: 15, margin: "0 0 4px" }}>{bundle.brief_text}</h1>
        <p style={{ fontSize: 11, color: "#888", margin: "0 0 16px" }}>
          seed {bundle.seed} · schematic, not a sanction drawing
        </p>

        <div style={{ display: "flex", gap: 6, marginBottom: 18 }}>
          {bundle.layouts.map((l, i) => (
            <button
              key={l.floor}
              onClick={() => { setFloorIndex(i); setSelected(null); }}
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

        {spec && placed ? (
          <section style={{ marginBottom: 20 }}>
            <h2 style={{ fontSize: 13, margin: "0 0 8px" }}>
              {spec.kind.replace(/_/g, " ")}
            </h2>
            <Row label="area" value={`${placed.area_sq_m.toFixed(1)} m²`}
                 note={`target ${spec.target_area_sq_m}, min ${spec.min_area_sq_m}`} />
            <Row label="size" value={`${placed.width_m.toFixed(2)} × ${placed.depth_m.toFixed(2)} m`}
                 note={`min width ${spec.min_width_m} m`} />
            {spec.sector && <Row label="wanted" value={spec.sector.replace(/_/g, " ")} />}
          </section>
        ) : (
          <p style={{ fontSize: 12, color: "#888" }}>Click a room. Scroll to zoom, drag to pan.</p>
        )}

        <h2 style={{ fontSize: 13, margin: "0 0 8px" }}>
          Unmet on this floor · score {layout.score.toFixed(0)}
        </h2>
        {layout.violations.length === 0 ? (
          <p style={{ fontSize: 12, color: "#16a34a" }}>Every constraint met.</p>
        ) : (
          <ul style={{ fontSize: 11.5, color: "#555", paddingLeft: 16, margin: 0, lineHeight: 1.55 }}>
            {layout.violations.map((v, i) => <li key={i}>{v}</li>)}
          </ul>
        )}
      </aside>
    </div>
  );
}

function Row({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, marginBottom: 5 }}>
      <span style={{ color: "#888" }}>{label}</span>
      <span style={{ textAlign: "right" }}>
        {value}
        {note && <em style={{ display: "block", color: "#aaa", fontSize: 10.5, fontStyle: "normal" }}>{note}</em>}
      </span>
    </div>
  );
}
