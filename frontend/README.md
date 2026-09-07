# naksha viewer

Reads a `PlanBundle` and draws it with react-konva. Pan, zoom, click a room.

**Not verified.** Written on a machine with Node 16, which cannot run Vite 5 — the
code has never been executed. Expect to fix something on first run.

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
