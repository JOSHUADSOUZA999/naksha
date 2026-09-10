# naksha viewer

Reads a `PlanBundle` and draws it with react-konva. Pan, zoom, click a room.

**Runs.** Node 20.20.2 via nvm, `npm install`, `tsc -b` clean, `vite build` clean,
and the plan renders headless with no console errors. Two things were wrong on that
first run and both were only visible in the picture:

- **Door swings were drawn outside the building.** Konva's `Arc` is a *wedge*, and with
  `innerRadius === outerRadius` it degenerates — it drew near-full circles floating off
  the plan. The swing is now an explicit twelve-segment polyline, computed from the same
  two vectors the SVG renderer uses, so the two cannot drift apart.
- **Room areas disagreed with the SVG.** The viewer quoted stage ⑤'s rectangles, which
  run to the wall centrelines and overstate every room by half a wall a side. It now
  reads `clear` from the refined floor, which is the figure a person should see.

## Requires Node 18+

Node 16 reached end of life in September 2023. Check with `node --version`, and if it
is below 18:

```bash
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
# restart the shell, then
nvm install 20 && nvm use 20
```

## Run

```bash
# 1. generate a plan (from naksha/, no key needed with --fallback-only)
.venv/bin/python -m app.cli --fallback-only --allow-unverified \
  -L frontend/public/plan.json --seed 7 \
  "30x40 east facing site in Whitefield, 3BHK with pooja room"

# 2. serve it
cd frontend && npm install && npm run dev
```

`public/plan.json` is committed with a sample so the viewer has something to draw
before you generate your own.

## Why Konva and not react-planner

react-planner is an *authoring* tool with its own scene model. naksha's editor is for
**adjustment, not authoring** — the AI authors — and decision 3 says a user edit
becomes a `Constraint`, never a coordinate. Adopting a library that owns its own
geometry would mean maintaining a two-way mapping against `Layout`, or giving up
`Layout` as the source of truth.

Konva is a rendering primitive: naksha keeps its data model, Konva draws and
hit-tests. See `DECISIONS.md`.

## Why this is Konva and not plain SVG

Honestly, at ~36 shapes per floor it did not need to be. SVG in React would give
hit-testing for free from the browser. Konva earns its place at the *next* step —
`Transformer` handles for wall dragging, snapping, and pan/zoom over the several
hundred shapes stage ⑥ will add once walls, doors and dimension lines exist.
