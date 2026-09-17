# CLAUDE.md — naksha

AI floor-plan generation for Indian residential plots. Vastu-aware, NBC-compliant,
editable output. Target users: plot owners and small builders in tier-1/2 India.

**What exists right now: stages ① through ⑦** — text in, a drawing out: walls with
thickness, doors, windows, ventilators, fixtures, and a list of what is wrong with the
plan. Only ⑧ CRITIC is unbuilt, and it is optional by design. This file is how to work
here, not where we are: **`STATUS.md`** tracks what is done, blocked and next (start
there when resuming), and **`DECISIONS.md`** records why the code is shaped this way,
every bug found, and the open questions that need a human. When you add a stage, add it
at its real path — the tree here matches the full naksha layout so nothing has to move
later.

---

## THE FOUR DECISIONS

Everything in this codebase follows from these. If a change contradicts one, stop
and flag it rather than working around it.

1. **The LLM emits a graph. The solver emits geometry.**
   LLMs reason well about relationships and badly about coordinates. The LLM
   decides *what rooms, how big, next to what, which compass sector*. A
   constraint solver turns that into metric geometry. The LLM never outputs x/y.

2. **Layout is two stages.**
   Stage A (slicing tree) produces gap-free tilings *by construction*. Stage B
   (CP-SAT) tunes dimensions with adjacency already fixed. Single-stage CP-SAT
   could not find a feasible 10-room plan in 20 seconds; two-stage does it in ~2 s.
   That is what makes the product interactive.

3. **User edits become Constraints, never coordinates.**
   Dragging the kitchen east writes `Constraint(sector=SE, hard=True)`, not `x=5.36`.
   Store coordinates and "regenerate" wipes the user's work.

4. **Rules are versioned data, not code.**
   Vastu schools disagree; setbacks differ per city; NBC gets amended. Rules live
   as JSON in Postgres with a version stamped into every plan's provenance.

---

## PIPELINE

```
text ──① INTENT ─────► Brief          schema-locked, 2 retries      ← BUILT
                        + Questions   tier-gated, ≤3, never blocking  ← BUILT
     ──② ENVELOPE ───► buildable rect  setback ruleset lookup       ← BUILT*
     ──③ PROGRAM ────► rooms + adjacency graph + sector prefs      ← BUILT ×2
     ──④ FEASIBILITY ─✗─► explain, with options that were measured  ← BUILT
     ──⑤ LAYOUT ─────► Stage A slicing tree → Stage B CP-SAT        ← BUILT
     ──⑥ REFINE ─────► walls → doors → windows → fixtures           ← BUILT
     ──⑦ VALIDATE ───► circulation · access · sanitation · size · light · ventilation · legality ← BUILT
     ──⑧ CRITIC ─────► rerank + rationale (optional, behind a flag)
```

Only ①③⑧ use an LLM. Everything else is deterministic code.

---

## REPO

```
naksha/
  backend/app/
    config.py    settings + the .env bridge
    cli.py       naksha-intent entry point
    ir/          models.py · enums.py · units.py · envelope.py · plan.py
                 layout.py · refined.py · validation.py · circulation.py  ← THE CONTRACT
    llm/         intent.py · program.py · fallback.py · client.py · trace.py
                 prompts/    versioned, hashed into provenance
                 providers/  base.py (the seam) · anthropic_api
                             openai_api · claude_code
    rules/       clarify_v1 · setbacks_v1 · spaces_v1 · refine_v1 · circulation_v1
                 · furnish_v1 · stairs_v1.json
                                                             ← VERSIONED DATA
    envelope/    geometry.py (pure) · __init__.py (lookup + build)
    program/     __init__.py  ③ expansion · stacking · stilt
    feasibility/ __init__.py  ④ explain + measured options
    solver/      slicing.py (Stage A) · tuning.py (Stage B, CP-SAT) · score.py
    refine/      __init__.py  ⑥ walls · doors · windows · fixtures
    validator/   __init__.py  ⑦ circulation · access · sanitation · size · light
                              · ventilation · legality
    circulation/ graph · topology · journeys · analysis · scoring · diagnose
                 how a drawn storey is walked: ⑦'s circulation check
    export/      svg.py                                   ← DXF/PDF still to come
  frontend/      Vite + React + react-konva viewer        ← NODE 18+ (20 via nvm)
  tests/         test_ir_brief · test_ir_plan · test_units · test_fallback
                 test_intent · test_providers · test_config · test_cli
                 test_clarify · test_envelope · test_schema_enforcement
                 test_program · test_feasibility · test_solver
                 test_refine · test_validator · test_llm_program · test_circulation
                 test_furnish · test_stairs · test_brief_requirements · test_open_plan
                 golden/
                 benchmark/  the 14-plan regression set: cases, runner, baseline
```

Not yet built, at their eventual paths: `store/` · `api/`.

---

## RUNNING IT

Python **3.11+** is required (`StrEnum`, `datetime.UTC`, `Self`).

```bash
uv venv --python 3.12 && source .venv/bin/activate
uv pip install -e ".[dev]"
cp .env.example .env          # set one key, or NAKSHA_INTENT_PROVIDER=claude_code

pytest                        # 859 tests, no network, no key, and independent
                              # of whatever is in your .env — see conftest
pytest -m live                # real model; needs credentials
pytest -m benchmark           # the 14-plan regression set, ~40 s — run after any
                              # solver, search or circulation change; see tests/benchmark

naksha-intent "30x40 east facing site in Whitefield, 3BHK with pooja room"
naksha-intent -i              # interactive
naksha-intent -s --fallback-only "…"   # offline parser only, instant
naksha-intent -p claude_code -i        # subscription instead of an API key
```

JSON goes to stdout and status to stderr, so `naksha-intent "…" | jq` works.

Without the venv activated, prefix with `.venv/bin/` — the console script and
`pytest` both live there. If `uv` is missing:
`curl -LsSf https://astral.sh/uv/install.sh | sh`.

---

## MODULE RULES

### `ir/` — build this first, in isolation, no AI, no solver

Everything downstream inherits its mistakes.

- All lengths **metres**, float. Angles degrees clockwise from north.
- Polygons counter-clockwise, **not closed** (first point ≠ last point).
- `model_config = ConfigDict(extra="forbid")` — unknown keys are bugs.
- Validate referential integrity on construction.
- `Provenance` records provider, model, prompt version, prompt hash, ruleset
  versions, solver seed. You cannot debug a non-deterministic pipeline without it.
- `BriefDraft` is what the model returns; `Brief` adds `raw_text`, which we attach
  ourselves. Keep that split — the model must not be able to paraphrase the user's
  own words, and it cannot if the schema it sees has no field for them.
- **Stage ① fills blanks rather than emitting nulls, and every fill is an
  `Assumption`** with a `confidence`. A null the user never chose is not neutrality:
  it pushes the same guess into a later stage where nobody can see it. Bathrooms,
  occupants, parking and floors all default; a locality that names its own city fills
  `city` at 0.95. What holds the line is that the guess is *visible* — the CLI prints
  the confidence column, and the selector in `intent.py` gates on it to decide what
  is worth asking. `Assumption` is structured for that reason; prose cannot be
  thresholded. Never write 1.0: a value you are certain of was read, not assumed.
- **`road_edges` is the stored field; `facing` and `corner_plot` are computed from
  it.** A plot can front two roads, and *every* road edge takes the larger road-side
  setback — a corner plot collapsed to one direction produces a wrong envelope, not a
  wrong label. `facing` is `road_edges[0]`, the primary frontage; `corner_plot` means
  two *adjacent* edges, and is false for a through plot (opposite edges), which is a
  different thing. Both are serialised so consumers and `| jq` still see them, and
  both are refused as inputs — a `facing` that disagrees with `road_edges[0]` raises
  rather than riding along stale. The model is never shown them: `computed_field`
  keeps them out of the validation schema.
- **`DerivedFieldsAreOutputOnly` is why the IR can read its own JSON.**
  `computed_field` and `extra="forbid"` contradict each other — derived values are
  serialised but rejected on input, so a strict model refuses its own output and the
  contract becomes write-only. Dropping is right for arithmetic (`area_sq_m` cannot
  meaningfully disagree; a mismatch means a stale file). `PlotSpec` keeps the stricter
  rule because `facing` names a *choice* someone might have hand-edited. **Every model
  with a computed field inherits it** — `Wall` and `Fixture` did not, and a drawn bundle
  could not be read back until a test round-tripped one.
- **`plan.py` has no coordinates, and that is the architecture.** Decision 1: the
  LLM decides *what rooms, how big, next to what, which sector*; the solver decides
  where. An `x` on a `RoomSpec` moves the hardest part of the problem to the component
  worst equipped for it, so a test asserts their absence. `RoomSpec` carries **two**
  areas — `min_area_sq_m` is the legal floor, `target_area_sq_m` is what it should be
  — because a solver given one number produces either cramped plans or infeasible
  ones; the gap is its slack. `min_width_m` and `max_aspect` both exist: area alone
  permits a 1 m × 9 m bedroom.
- **`SpaceKind` is a superset of `RoomKind`, enforced by a test.** `RoomKind` is what
  a person types; hall, kitchen and corridors are implied or never mentioned. A
  `RoomKind` with no counterpart would be silently dropped in expansion — the user
  asks for a pooja room and does not get one.
- `units.py` is the **only** place a non-metre number becomes metres. A conversion
  anywhere else is a bug. Indian inputs arrive in feet, sq ft, and gaj/yards.
- **It is also the only place metres become feet, and only for a person to read.**
  Nothing stores feet. Owners size a room as "12 by 14" and a house in sq ft while the
  bye-laws are metric, so a sentence gives both — `area_text` is "344 sq ft (32.0 m²)" —
  and a drawing, with room for one, labels each room in feet-and-inches and sq ft. The
  viewer mirrors the two in `frontend/src/units.ts`. Prompts to the model and IR
  validation errors stay metric: neither is read by an owner.

### `rules/` — versioned data, never constants in a module

Decision 4's home. Postgres is the eventual store; until `store/` exists these are
JSON files loaded like prompts — by name, with a sha256, so an edit without a version
bump is detectable rather than silent. Every ruleset that shaped a result is stamped
into `Provenance.ruleset_versions` as `name@hash`.

**For Bengaluru the operative document is the Karnataka Municipalities Model Building
Bye-Laws 2017** (UDD 14 TTP 2017 (P-4), 28-10-2017), not NBC 2016. NBC is the national
*model* code; Karnataka adopted and adapted it, and a BBMP sanction is checked against
the Karnataka version — citing NBC was one level too abstract. Eight figures in
`spaces_v1.json` are now `verified: true` with a clause reference, read first-hand from
BBMP's BPAS portal.

Two of them had been **stricter than the law**: the second bedroom's minimum width
(2.4 m against the statutory 2.1 m) and the hall's (3.0 m / 11 m² against the
habitable-room floor of 2.4 m / 9.5 m²). A living room *is* a habitable room by
cl. 5.1.2.3, so those were practice figures wearing a legal label, and they were
rejecting plans the law permits. **Minimums are legal floors; targets are practice** —
keeping a convention in the `min` column silently narrows what can be built.

Note cl. 1(3): FAR, setback, coverage and height come from the **Master Plan and Zonal
Regulations**, not the bye-laws. That is why `setbacks_v1.json` is a separate question
and remains entirely unverified.

`clarify_v1.json` decides which assumptions earn a question. It is data because the
judgment in it is a *product* judgment that changes without the model changing: which
fields are expensive to get wrong, which are derived from which, and how many
questions a person will tolerate. Tuning it must never require a deploy of `llm/`.

### `envelope/` — stage ②, deterministic

**\*BUILT, but its rule data is unverified and it refuses to run by default.** The
arithmetic is done and tested; the BBMP figures behind it are transcribed from
secondary sources and marked `verified: false`, so `build_envelope` raises
`RulesetUnverified` unless passed `allow_unverified=True`. A wrong setback looks
exactly like a right one, and the output is a number someone acts on.

Two frames, and conflating them rotates every plan ninety degrees: a `PlotSpec` is
**road-relative** (`width_m` is frontage), an `Envelope` is **compass-aligned** (x
east, y north). `geometry.plot_extent` is the only conversion.

It declines in four typed ways — `CityUnknown`, `NoRulesetForCity`,
`RulesetUnverified`, `EnvelopeInfeasible` — each carrying the numbers. **A
neighbouring city's bye-laws are not a conservative default; they are a different
answer.** "No envelope, because the city is unknown" is a finding stage ④ acts on.

**Road width is two constraints, not one.** RMP 2031 §5.2(iii) demotes the FAR band
when the abutting road is narrower than the band expects — a 500 m² plot on a 6 m road
earns a 120 m² plot's FAR — and §5.2(iv) caps storeys by road width *irrespective of
the FAR earned*. `Envelope.max_far` uses **`base_far`**, never `total_far`: the excess
requires buying TDR, and budgeting against it sizes a programme that cannot be built.

Known gaps, all recorded in `setbacks_v1.json`: **the FAR figures are read first-hand
from RMP 2031 Vol. 6 Tables 6/7, but every page of that document is stamped
'(Draft)'** — published for objections in 2017, notification not established. If it
was never notified, Bengaluru still sanctions against RMP 2015 and these numbers are
wrong. Three candidates exist for the same 30x40 plot: 0.75 (Bye-laws 2003), 1.50
(RMP 2031 draft, what is encoded), 1.75 (reported for RMP 2015). Separately: Table 4
setbacks key on site *depth*, not area, so those bands are an approximation; the FAR
tables split by Planning Zone A/B with no locality-to-zone map; and only Bengaluru is
mapped.

### `program/` — stage ③, deterministic floor

Same role `llm/fallback.py` plays for ①: a real expansion that runs with no model, no
network and no key. The LLM version reads nuance these rules cannot — "joint family",
an elderly parent near the entrance — but a user whose call failed should still see a
house.

- **Emits a graph, never coordinates.** Decision 1, at the stage most tempted to break
  it.
- **Bedrooms open off the corridor, never the hall.** That is what circulation is for,
  and why privacy survives the tiling.
- **Kitchen and every bathroom carry a hard `SEPARATED` edge, and so do the pooja room
  and every bathroom.** A toilet sharing a kitchen or pooja-room wall is the placement
  Indian clients object to first, Vastu or not.
- **`parking_bays` is authoritative, not `extra_rooms`.** The model lists
  `car_parking` in `extra_rooms` on some runs and not others for the same brief;
  building the bay from that choice swung the programme 14% between identical inputs.
  `extra_rooms` records what the user *asked for*; the count says what to build.

### `solver/` — stage ⑤, Stage A and Stage B

Slicing trees: a binary tree of cuts whose leaves are rooms. Placing it divides one
rectangle recursively, so **the tiling is gap-free by construction** — no solver is
asked to rediscover that rooms must not overlap, which is what made single-stage
CP-SAT fail decision 2's twenty-second test.

- **Ranking is lexicographic: `(unbuildable, penalty)`.** A room below its legal
  minimum is not a worse version of a misplaced one, and no quantity of satisfied
  preferences compensates. Weighting alone was not enough — hill-climbing traded two
  unbuildable rooms for enough Vastu wins to come out ahead on total, and produced a
  2.2 m² hall. A large weight is not a substitute: whatever is big enough today stops
  being big enough as the room count grows.
- **Allocation shrinks towards minimums, never scales targets.**
  `slicing.effective_areas` gives each room its minimum plus a share of the leftover
  slack. Scaling targets uniformly pushes a room whose target barely exceeds its
  minimum *below* it, in a programme feasibility had already passed — which is
  exactly what `RoomSpec` carrying two areas was for, and the solver ignored it for
  long enough to ship the bug.
- **Hill-climbing swaps occupants, never geometry.** The rectangles stay where the
  tree put them and only the room-to-rectangle assignment changes, so no swap can
  open a gap.
- **The shortlist is hill-climbed *then* re-ranked**, because a candidate that looks
  mediocre before climbing can win after it.
- **A sector-aware shuffle was tried and removed** — see `DECISIONS.md`. It measured
  as a win against the *broken* objective and vanished once ranking was fixed.
- **Stage B's cm grid must sit *inside* the envelope, and its outer edges snap back
  onto the envelope's exact floats.** `tune` solves on 1 cm integers; an envelope
  derived from feet does not land on that grid, so rounding to nearest left every tuned
  layout a fraction of a centimetre short of the wall — `Layout` called it a gap and
  `solve` discarded it silently. **Stage B was dead for every plot quoted in feet**,
  which is every brief the product is for. Ceil the minimum and floor the maximum so
  snapping outward can only *grow* a boundary room: rounding to nearest puts the grid
  outside and snapping back shaves a room the model just proved legal. Interior cuts
  stay on the grid and cancel between neighbours.
- **Hill-climb the tuned layout, not just the generated one.** The omission was
  invisible while the tuned path never ran, and fixing the grid alone made the largest
  brief measurably worse.

- **A room can also be too big, and `score` says so.** Exact tiling fixes the total
  area, so surplus the programme never asked for is forced into some room — 48% of the
  footprint on a 50x80, which produced a 30 m² bathroom. The penalty makes it visible
  in `violations`; it does not fix it, because every candidate carries the same
  surplus. Both a relative *and* an absolute test must fire, or a 5 m² pooja room
  against a 2.5 m² target gets flagged alongside it. See `DECISIONS.md` question 7.

- **Touching the road is a tree shape, so generate the shape.** Whether a room meets the
  plot edge is fixed by the slicing tree, not by CP-SAT's dimensions, and hardly any
  random tree puts the car bay on the road and still sizes legally.
  `slicing.road_first_tree` builds bay and foyer as a strip along the road with the house
  behind. **Add generator groups beside the random pool, never in place of it** — biasing
  generation has failed twice here, raising the average and lowering the best.
- **⑦ picks the plan, not the penalty.** `plan(judge=…)` keeps the 12 best finished
  candidates per floor and takes the minimum of `validator.judge`. The solver imports
  neither ⑥ nor ⑦; the judge is passed in. The lowest penalty had picked a 30x50 with no
  front door.
- **A storey is chosen with the storeys above it.** The stair a floor settles on is the
  one thing the floor above cannot change, and the judge ranks one storey at a time: on
  the JP Nagar 4BHK the best-ranked ground floor left a 5.5 x 1.7 m stair no first floor
  could stand on. When a storey ⑦ passes carries one it refuses, `plan` tries the next
  candidates below it, up to `STACK_TRIES`, and keeps the stack whose judge keys add up
  best. The key's first element is the contract: truthy when ⑦ refuses the storey. A
  first choice the floor above can stand on costs nothing.
- **A swap may not take the car bay or the front door off the street.** `improve` ranks
  `(unbuildable, off_the_road, penalty)`. On the 30x40 2BHK and the 30x50 the one
  road-first plan that dimensioned was legal before the climb and refused after it: a
  swap bought 85–115 points of sector and adjacency with the foyer's street wall.
- **Refusing a tree is nearly free, so a thin floor searches past its shortlist.** CP-SAT
  proves a topology undimensionable in well under a millisecond. When the shortlist
  dimensions fewer layouts than `solve` wants, `deeper` feeds it up to 2000 more
  road-first trees — that is what made the 30x40 2BHK and the 30x50 legal. A roomy plot
  never pays. **`tuning.cannot_fit` skips floors whose minimums exceed the footprint**
  (166% and 101% on the two hopeless plots; the thin ones that solve sit at 89–91%), and
  it shares `gross_minimum_cm2` with the model so the bound cannot drift from it.
- **Stage B's budget is work, not seconds.** `tune` stops on CP-SAT deterministic time
  (`TUNE_WORK`). A wall-clock limit stopped most solves wherever the machine had got to,
  and the same seed gave different plans run to run. Never add a wall-clock cap beside it.
- **The stair below is a pull in Stage B, not a pin.** `tune(anchors=…)` prices shaft
  misalignment at `SHAFT_PULL` cm² per cm against target areas; minimums stay
  constraints. A pin broke other plans twice, and ranking stair misses alone changed
  nothing, because no candidate had a rectangle over the shaft.
- **Corridor-first trees make the corridor reach the rooms.** Whether a bedroom can have
  a door to the corridor is the tree's shape, like the car bay's road. `spine_first_tree`
  runs the corridor across the floor with two rows cut across it. Added as a group after
  the random and road-first trees, it cut route warnings from 10 to 7 over thirteen plans
  at no cost in time.
- **Zone-aware corridor trees are added beside plain ones.** `zone_spine_tree` puts each
  room on the side of the corridor its Vastu zone wants and orders each row by the
  compass. Added, it raised zones met from 24 to 28 of 110 at no cost; swapped in for plain
  corridor-first, it cost three warnings.
- **A statutory length is a constraint, and narrow plots need columns to carry it.**
  `RoomSpec.min_length_m` — 6.0 m for a car bay — is enforced in Stage B. A road-first
  strip drags the foyer to the bay's depth, so the deeper search tries road-column trees
  once the strips are spent. Strips first: taking both in turn cost floors the strips
  had served.

- **A room is sized for its furniture, as a pull, never a constraint.** `furnish_v1` says
  what must fit across and along each kind of room; `RoomSpec.usable_sizes_m` carries it.
  Stage B prices each cm a room's sides fall short (`FIT_PULL`) and `score` charges per
  metre, capped at STRUCTURAL so it never counts as unbuildable. Target areas are blind to
  shape and handed back 7'6" x 19'10" bedrooms; the judge and the score alone moved
  nothing, because every finished candidate was already a strip. The space comes out of
  corridors — see `DECISIONS.md`, question 7.

- **A stair's shape is a constraint computed from a step.** `stairs_v1` holds rise, tread,
  flight width and storey height; `program.stair_sizes` turns them into dog-leg and straight
  rectangles on `RoomSpec.min_sizes_m`, and at least one must fit — in Stage B, `score` and
  `refine.breaches`. An upper stair is held to the type below (`_same_flight_as_below`), and
  `shaft_first_tree` cuts the stair below's rectangle out first. Never pin a shaft in an
  arbitrary tree (DECISIONS question 9).
- **"Near" is a 10 m proper walk, and a tree shape.** `Relation.NEAR` never adds a door.
  `score._programme_walk` estimates the walk through shared-wall midpoints and must keep
  matching ⑦'s routes; `near_spine_tree` is the group that can meet it.

`max_aspect` is per-kind data, not a constant. A corridor is *supposed* to be
elongated; flagging one as "a corridor, not a corridor" was the rule mistaking the
shape for the defect.

### `feasibility/` — stage ④, and the reason ⑤ is allowed to fail

*Explain, and offer options that were actually measured.* "Infeasible" is useless to a
plot owner; "your ground floor is 90% packed — moving the car porch into the setback
makes 11 of 60 layouts legal" is a decision they can take.

- **Options are measured, never guessed.** Each candidate change is applied and the
  solver run against it. Affordable only because Stage A generates in microseconds
  and Stage B *proves* infeasibility in milliseconds.
- **The verdict is a solve probability, not a hit rate.** 5 in 60 sounds dire and is
  not: ⑤ tries 24 topologies, so it lands a plan 88% of the time. 1 in 60 is 33%, and
  that is the number a user should hear.
- **Nothing is silently reduced.** A plot owner who asked for a separate dining room
  is told what dropping it costs, not quietly deprived of it.
- **The probe searches as deep as ⑤ does.** Twice the probe and the solver drifted apart
  and ④ called plots infeasible that ⑤ then solved. `_probe(past_the_shortlist=True)`
  walks `solver.deeper` and stops at the first legal plan, and an option that is legal
  only there says so in words rather than as a count out of 24.

### `refine/` — stage ⑥, deterministic

- **Walls are IR geometry, never a rendering trick.** A `Wall` is a centreline plus a
  thickness, stored in `ir/refined.py`, because a wall in SVG is a stroke while in DXF
  it is a centreline on a layer. Every renderer reads the IR; none invents thickness.
- **Legality is measured inside the walls.** Stage ⑤'s rectangles run to wall
  centrelines; the bye-laws mean clear internal size. `refine.breaches` recomputes it
  independently of the scorer — a guard that shares its implementation with what it
  guards catches nothing.
- **The adjacency graph is the door schedule, and a minimum, not the whole of it.**
  `CONNECTED` edges get doors; `_connect` adds what circulation needs on top.
  **Join the circulation spine first, then attach private rooms** — a single greedy
  pass once reached the corridor through a bedroom via a perfectly reasonable
  kitchen → bedroom door.
- **`SEPARATED` is hard, checked before anything is weighed.** An edit once left it
  behind an unconditional `continue` and a bathroom got a door into a kitchen.
- **Upper storeys are entered off the stair, not a front door.** Both ⑥ and ⑦ once
  started their walk at the entrance and gave up upstairs.
- **Openings narrow before they give up.** Missing a doorway by three centimetres is a
  reason to draw a narrower door, not a house with no way in.
- **A car bay gets a gate, not a window, and the gate depends on which way it lies.**
  `_vehicle_openings` puts a 2.7 m gate in the short side of a bay running back from the
  road, and a gate across nearly the whole long side — at least 5.4 m, car-porch style —
  of one lying along it. ⑦ refuses a bay on the road with no gate, or with a narrow gate
  along the road, and its daylight rule skips car bays. Requiring every bay to be driven
  into nose first refused three single-storey plans; the user chose the car porch.
- **Every room that meets the outside gets air, and what it gets is data.** `refine_v1`'s
  `ventilation` block: a bathroom a ventilator, a stair, corridor, pooja room or foyer a
  window, a dining room on an outside wall glazed like a habitable room, and a room people
  live in a second window on a second outside wall. Only rooms that *had* to touch the
  outside used to get an opening — all 31 bathrooms in fourteen plans were sealed and no
  room had air from two sides. **Only a leaf swings:** a ventilator counted as a door
  swing kept the WC off the wall it belongs against.

### `validator/` — stage ⑦

Runs once, on the drawing — which is what separates it from `score`, which ranks
candidates tens of thousands of times. `Report.checks_run` exists so that no findings
means "checked and clean" rather than "nothing ran". One finding per defect, not per
room: twelve "cannot reach the kitchen" lines make one large problem look like twelve
small ones. **Test a check by breaking a plan on purpose**, never by waiting for a brief
that happens to fail — that test goes vacuous the day the pipeline improves.

- **Look at the drawings.** ⑦ once had three checks and called five bad plans clean: a
  house entered through a bedroom, a second bedroom reached only through the master, a
  27.7 m² bathroom, car bays no driveway reaches. Every one was invisible to the suite
  and obvious in the picture. A check that has never failed on a real plan is not yet
  known to work.
- **A route may end in a private room, never pass through one** — except into the
  bathroom stage ③ connected to that bedroom, which is what an en-suite is. Narrowing
  the rule to "is the corridor reached through a bedroom" is what hid the defects above.
  The circulation engine asks it now, of the best route to every room: through a bedroom
  or a bathroom is critical, through the kitchen major.
- **Walk every route, not the shortest.** One honest way into a room is enough.
- **A car bay off the road is an error**, not a warning: it does not satisfy the parking
  requirement that put it in the programme.

- **The judge turns every check into an objective, and an optimiser finds its holes.**
  The walk once fell back to the staircase on a *ground* floor with no front door, called
  the house clean, and the judge preferred it to every house you could enter. The
  staircase start is for upper storeys only. `judge` ranks critical circulation first
  among refusals.
- **Any room reached only through a bedroom or bathroom is critical.** A second bedroom
  reached through the master used to be a warning, "bad and livable"; the circulation
  brief made it a failure, because that bedroom has no privacy and the master has become
  a corridor. Living rooms behind a private room were errors already, since a 30x50 went
  front door, foyer, *bathroom*, corridor, hall, and as one warning the judge preferred it
  to a plan refused for its car bay.
- **Behind the kitchen is major, the dining room included.** Both plots the deeper search
  made legal were entered foyer → kitchen → hall and ⑦ said nothing. A dining room
  reachable only through the kitchen used to go unreported as an ordinary arrangement;
  the engine reports it, because guests cross the working kitchen to reach the table.
- **Compare a storey with the one below.** Upstairs the walk starts at the stair, and a
  stair that does not sit over the stair below is no way up — ⑦ refuses it, reading
  `layout.shafts`. Nothing checked this, and three plans called clean could not be
  climbed.
- **The judge ranks refusals, then majors, then Vastu, then circulation quality.** In
  order: refused at all, rooms below a legal minimum, rooms the circulation leaves without
  proper access, other critical findings, major findings of any check, missed zones, circulation quality, minor
  findings, one-sided rooms, penalty. Vastu is advisory, so no zone buys a major finding,
  but inside the penalty it lost to everything: plans met 18 zones in 110, and counting
  them here raised that to 24. Quality ranks after the zones because it almost never
  ties, so anything after it is a tiebreak. **Majors are counted together:** ranked ahead
  of the rest, circulation majors bought one fewer with a windowless bedroom on the 30x40
  2BHK and a windowless hall on the 50x80. **Critical circulation is counted in rooms:**
  counted as findings, one naming seven rooms behind a private room weighed less than two
  about a room each, and the 30x50 was shown with the seven.
- **The front door leads into the house.** A model's 3BHK was entered foyer → staircase
  → corridor → hall and passed everything. The engine walks a visitor's arrival: a foyer
  into the living room is right, one corridor between them is ordinary, two circulation
  spaces are roundabout (minor), and a dining room or kitchen on the way reverses the
  hierarchy (major).
- **Rooms the programme keeps apart must not share a wall** — a toilet against the
  kitchen or the pooja room. Reported from the programme's `SEPARATED` edges, so the rule
  lives in stage ③, not in a list in ⑦.
- **A bathroom that cannot breathe is a warning; air from two sides is a measurement.**
  `_ventilation` groups bathrooms by cause — no outside wall, no ventilator, too small a
  one — because the fixes differ. Which rooms get air from two sides goes on
  `Report.cross_ventilated` and `single_sided`, never as a finding: a floor has four
  corners. The judge counts one-sided rooms after circulation quality and minor findings;
  ranked before the zones, over fourteen plans, it cost five zones and bought a breeze with
  a windowless bedroom.
- **A legal room that cannot hold its furniture is reported, never refused.** `furnish`
  grades a foot or more short of the nearest arrangement major, less minor, within 5 cm
  nothing. Fixtures come from `refine_v1`, so the bed a room is sized for is the one drawn.
- **What the brief asked for ranks before every other major.** `_brief` walks each `NEAR`
  edge through the doors; an unmet one is major, and the judge counts it right after the
  refusals.
- **Rooms open to each other are one space.** `Relation.OPEN` becomes a wall-length
  `OpeningKind.OPEN`: furnished as a pair (`open_pair_shortfall_m`), and lit as a pair only
  to rescue a room short on its own. Every walk — ⑥'s, the engine's, the solver's — passes
  through it.
- **A door must open onto a landing.** ⑥ draws a stair's flights clear of its doors and puts
  a door into a stair at an end of its wall; `_stairs` reports a stair it could not draw.
- **A room people live in with no window at all is refused.** `light` refuses a room the
  bye-laws call habitable (`refine_v1.windows.habitable_kinds`) with no window, and warns
  when one is glazed short of the fraction or a kitchen has none. As a warning it weighed
  the same as an en-suite off the corridor, and the judge chose a windowless bedroom on the
  30x40 2BHK and a windowless hall on the 50x80.

### `circulation/` — the engine stage ⑦ will judge circulation with

**Stage ⑦'s circulation check.** `validate` calls `evaluate(layout, program, floor)` on
every drawn storey: its graded findings are the report's circulation findings, its
`CirculationSummary` goes on `Report.circulation`, and the judge ranks by both. The
reachability walk ⑦ used to do itself is gone, and every defect that walk was built to
catch has a test in `test_circulation.py`.

- **Reachable is not properly reached.** A room behind a bedroom is reachable and still a
  critical failure. Access is graded by the worst room the *best* route must cross, by
  what that room is for: through a bedroom or bathroom is critical, through the kitchen
  major.
- **Everything it believes about a room kind is `circulation_v1.json`**: role, zone, what
  walking through it costs, the journeys, the relationships, every threshold. No module
  keeps its own list of private rooms and no rule reads a room's id; a test renames every
  room in JP Nagar and requires the same verdict.
- **An en-suite is judged from its own bedroom**, read from stage ③'s `CONNECTED` edge.
  Opening off the corridor it is a shared bathroom (major); reached through another
  bathroom it fails.
- **A corridor is judged by its work.** Removing a landing strands the bedrooms off it,
  which makes it essential, not wrong. Redundant means no room needs it and no walk uses
  it; inefficient means area, width or dead end out of proportion to its doors.
- **The stair room includes its landing.** A shared bathroom opening off it is ordinary;
  only a stair whose sole way on is a bathroom is critical. The first rule failed three
  benchmark plans for a common bathroom beside the stair.
- **One finding per defect.** A room whose access is already reported is left to that
  finding: a house entered through the kitchen is one access finding, not also a reversed
  arrival and a visitor crossing the service zone.
- **A critical finding fails the storey whatever the score**, and multiplies the score by
  `critical_validity`, while `quality` keeps two failures comparable. Good also needs no
  major finding.
- **Test by drawing the defect.** `test_circulation.py` builds each storey by hand with
  explicit doors, and JP Nagar is pinned as data in `golden/circulation_jpnagar.json`, so
  no test waits for the solver to draw a bad plan.

### `llm/`

- Always schema-constrained output. Never parse free text.
- **The clarification selector lives in `intent.py`, not a stage of its own.** It
  runs after extraction, reads `brief.assumptions`, and usually returns nothing.
  **The tier gates; confidence only sorts what survives.** Each tier carries the bar
  under which it is worth interrupting — 0.7 blocking, 0.5 consequential, never for
  the rest. A brief stating its dimensions, orientation and bedroom count asks nothing
  at all. Taking the bottom N by confidence instead always finds something to ask,
  because there is always a lowest row; that is not the same as a row worth asking
  about, and it is how a clarifier turns into a form. **The budget is a ceiling on an
  already-gated set, not a quota to fill.** Questions never block — the Brief is
  complete before they are selected, and each carries the value that stands if it goes
  unanswered.
- **One graph, two uses, in `clarify_v1.json`.** `depends_on` records that a value was
  read off another. It supplies the **ceiling** (`cap_derived_confidence`: no
  inference outranks its source, transitively, by the minimum over parents — `floors`
  hangs off both `plot size` and `bedrooms`) and the **collapse** (a guess whose
  parent is also a guess is not worth its own question; answering the parent
  re-derives it). Both roots matter: patching this per-field at the site that produced
  it is what missed `plot size` the first time. The ceiling runs on the model's output
  as well as the parser's — the model launders too.
- Prompts are **versioned files** in `llm/prompts/`, referenced in provenance
  by name *and* sha256 — a silent prompt edit must be detectable.
- 2 retries on schema failure, then **deterministic fallback**. The LLM being
  down must never break generation entirely.
- **Stage ③'s model path builds rooms with `program.spec_for`**, the offline expansion's
  own function, and keeps only the model's id, kind, floor and sector. A hand-built
  `RoomSpec` copied one rule flag of five, and every model-drawn plan had no front door.
  The user's site choices (`--stilt`, `--porch-in-setback`) go through
  `program.apply_site_choices` on both paths.
- **The CLI runs ③ once per brief**, and `-P` and `-L` share the result. Two calls
  printed one programme and drew another.
- **A path that needs credentials gets a recorded replay.** `tests/golden/program_drafts.json`
  holds real ③ answers and `tests/test_llm_program.py` replays them through a scripted
  provider — the only way that path runs offline.
- **The suite is isolated from your `.env`.** An autouse fixture clears `NAKSHA_*`
  from `os.environ` *and* neutralises pydantic-settings' own `env_file` read — they
  are independent sources and clearing one fixes nothing. Without it a single
  `NAKSHA_INTENT_PROVIDER` in a developer's `.env` silently redirects every
  provider-inference test; eight failed exactly that way.
- **`allow_fallback=False` / `--no-fallback` opts out, and exists for one reason:**
  when you are evaluating the model rather than serving a user, a silent substitution
  is the worst outcome available — the output looks fine and answers a different
  question. Serving a user, the default stands. Check the stderr line either way:
  `[model] …` means the model answered, `[fallback] …` means it did not.
- Schema retries and transport retries are separate budgets. The SDK already
  retries 429/5xx twice; do not layer our schema retries on top of that.
- Every call traced when Langfuse is configured; a no-op when it isn't.

**Deviation from the original spec, on purpose:** the design doc named `instructor`
for structured output. We use each SDK's native structured outputs instead —
`messages.parse(output_format=…)` on Anthropic, `responses.parse(text_format=…)` on
OpenAI. Both enforce the JSON schema server-side and return a validated Pydantic
instance. Same guarantee, one fewer dependency. If you reintroduce `instructor`, say
why here.

### `llm/providers/` — the vendor seam

**Model-agnostic by requirement.** naksha runs on any supported model — Claude,
GPT, or anything served behind an OpenAI-compatible endpoint. This is a product
constraint, not a preference, and it is the reason an abstraction exists here when
the "no abstraction tax" rule kept LangChain out. The tax is paid once, at one seam,
for a stated requirement.

`StructuredCaller` is the whole interface: *give me an instance of this Pydantic
class, or raise one of four normalised errors.*

```
ProviderOutputInvalid   retryable — bad schema, truncated, or no parsed object
ProviderRefused         safety classifiers declined
ProviderUnavailable     auth, network, rate limit, 5xx
ProviderNotInstalled    the SDK for this provider isn't installed
```

- **`intent.py` names no SDK.** If a vendor exception reaches the stage, the seam
  has a hole — retry and fallback logic would then silently work for one SDK only.
  `tests/test_providers.py::TestAdaptersAgree` is the guard.
- **Routing is by model id**, because that is the one thing a user always knows:
  `claude-*` → anthropic, `gpt-*`/`o1`/`o3`/`o4` → openai. `NAKSHA_INTENT_PROVIDER`
  overrides for ids the table can't place.
- **`claude_code` is opt-in only and never inferred.** `claude-opus-5` means the
  API. Routing an API model id to a local subscription-backed install would change
  where the work runs, what it costs and which limits apply — that must be a
  decision, not a default.
- **Provider SDKs are extras** — `".[anthropic]"`, `".[openai]"`, `".[claude-code]"`,
  or `".[all]"`. Imports are deferred into the factories, so one installed SDK is
  enough and a missing one is a clear `ProviderNotInstalled`.
- **Provenance records `provider` alongside `model`.** Regressions are often
  provider-shaped rather than model-shaped.
- **Never guess an SDK's surface.** Both adapters were written against signatures
  read off the installed package. Vendors disagree in ways you will not predict —
  Anthropic raises a bare `TypeError` at *request* time for a missing key while
  OpenAI raises `OpenAIError` at *construction*, and both had to be normalised
  before the fallback path worked for either.
- Adding a provider: implement `StructuredCaller`, register a factory and a prefix.
  Nothing in `intent.py` changes.

**Do not add:** LangChain · image-generation models (they produce non-geometric
garbage — walls don't close) · a custom geometry kernel.

---

## CONVENTIONS

- Type hints everywhere. `from __future__ import annotations`.
- Pydantic models for all boundaries. No bare dicts crossing a module edge.
- `snake_case` Python. No magic numbers — dimensions come from `rules/` once it exists.
- Docstrings explain **why**, not what.
- `pytest` with `hypothesis` for anything geometric or numeric — property-based
  testing catches the invariant violations that example-based tests miss.
- Golden set (`tests/golden/`) runs on every prompt change. In a non-deterministic
  pipeline you cannot otherwise tell improvement from regression.
  **This is architecture, not QA.**
- Tests must pass with no network and no API key. Live-model tests are marked
  `@pytest.mark.live` and deselected by default.

---

## MODEL

Default `claude-opus-5`; set `NAKSHA_INTENT_MODEL` to anything supported. The
provider follows from the id, so switching vendors is one env var:

```
NAKSHA_INTENT_MODEL=claude-opus-5   + ANTHROPIC_API_KEY
NAKSHA_INTENT_MODEL=gpt-5.2         + OPENAI_API_KEY
NAKSHA_INTENT_PROVIDER=claude_code  + a Claude Code login   (dev only, no key)
```

`claude_code` runs against a Pro/Max subscription through a local Claude Code
install. It keeps the schema guarantee — `ClaudeAgentOptions.output_format` takes
the Messages API's `json_schema` shape and the result carries `structured_output` —
so it is a real provider, not a prose-parsing fallback. It is still development
only: a process per call, a CLI that must exist on the box, subscription limits
rather than API limits, and `asyncio.run` under the hood, so it refuses to run
inside an existing event loop. The Celery worker in phase 7 needs an API provider.

Construct clients with **no** `api_key` argument. Each SDK has its own resolution
chain — Anthropic reads `ANTHROPIC_API_KEY`, then `ANTHROPIC_AUTH_TOKEN`, then an
`ant auth login` profile; OpenAI reads `OPENAI_API_KEY` and `OPENAI_BASE_URL`.
Passing an explicit key breaks the profile path and the gateway path.

An unset env var does not mean there are no credentials. A *set but empty* one is
worse than unset: it outranks the profile and authenticates as empty.

**`.env` needs an explicit bridge.** `config.py` calls `load_dotenv` at import,
anchored to the project root so it works from any cwd. This is not redundant with
pydantic-settings' `env_file`: that only populates `Settings`, while the provider
SDKs read `os.environ`. Without the bridge a correctly-filled `.env` produces no
error and no key — every request fails auth and silently lands on the fallback.

Adaptive thinking is on by default on Claude Opus 5 — leave it on. Disabling it is
cheaper-looking but carries a documented failure mode where `<thinking>` tags leak
into visible output.

---

## SCOPE — v1

**In:** rectangular plots · **ground + 1** · 2–3BHK · Vastu scoring · wall drag +
numeric dimensions · door/window adjustment · DXF/PDF export · candidate gallery.

*Was "ground floor only", which contradicted the target market.* Measured: a 3BHK does
not fit one floor until **40x60 ft**, and even a 2BHK is 98% packed on a 30x40 — the
commonest site size in south-Indian layouts and `fallback.py`'s own default. Serving
30x40 and G+1 are the same decision. The cost lands on ⑥ REFINE: stairs, floor-to-floor
alignment, and a drawing per storey.

**Out:** irregular plots · multi-floor · 3D · BIM/IFC · collaboration · cost
estimation · structural analysis · curved walls.

The editor's job in v1 is **adjustment, not authoring**. The AI authors; the user
adjusts.

---

## LEGAL POSITIONING

In India, plan approval requires a registered architect's or engineer's stamp. naksha
produces **schematic / concept stage** drawings, not sanction drawings. This must
appear in the product UI, not only in the ToS.

Vastu output is **advisory**. Always show which ruleset version produced a score.
Schools genuinely disagree; the user or their consultant owns the verdict.
