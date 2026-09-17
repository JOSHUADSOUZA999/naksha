# STATUS — naksha

## Start here — resuming in a new session

Read in this order, then run the commands below:

1. **`CLAUDE.md`** — how to work here. The four decisions, module rules, conventions.
   Non-negotiable; if a change contradicts one, stop and flag it.
2. **`STATUS.md`** (this file) — where we are, what is blocked, what is next.
3. **`DECISIONS.md`** — why the code is shaped this way, every bug found and what it
   taught, corrections, and **the open questions that need a human**.

```bash
cd naksha
.venv/bin/pytest -q                                  # 848 tests, no network, no key
.venv/bin/pytest -m benchmark                        # the 14-plan regression set, ~40 s

# The whole pipeline, to a drawing on disk: ①②③④⑤⑥⑦
.venv/bin/python -m app.cli -s -e -P --allow-unverified --fallback-only \
  --svg /tmp/plan.svg "40x60 3bhk in Bengaluru with pooja room"

# The viewer. Needs Node 18+; nvm has 20 installed on this machine.
.venv/bin/python -m app.cli --allow-unverified --fallback-only --seed 7 \
  -L frontend/public/plan.json "40x60 3bhk in Bengaluru with pooja room"
cd frontend && npm run dev

.venv/bin/python -m app.cli -i                       # interactive, multi-line ok
```

```bash
# The real model. Needs ANTHROPIC_API_KEY in .env, or -p claude_code for a subscription.
.venv/bin/python -m app.cli -s -e -P --allow-unverified --no-fallback \
  "40x60 for a joint family"
```

`--fallback-only` skips the model entirely; `--no-fallback` is its opposite — fail
loudly rather than quietly substitute the regex parser, which is what you want while
evaluating the model. **Always check the stderr line:** `[model] …` means Claude
answered, `[fallback] …` means you are looking at regex output. `--allow-unverified`
is required for `-e` because the bye-law figures are unchecked — see blocker 1.

**The target-market plot** needs `--stilt`:
`.venv/bin/python -m app.cli -s --allow-unverified --fallback-only --stilt --svg /tmp/p.svg "30x40 east facing site in Whitefield, Bengaluru, 3BHK with pooja room"`

**Briefs worth trying:** `40x60 4bhk g+1 in Bengaluru with study and car parking,
strict vastu` · `20x30 2bhk in Bengaluru` (tight) · `30x40 north facing corner plot
3bhk in Bengaluru` (two road setbacks) · `make me a house` (no city → no envelope) ·
`40x60 for a joint family` (needs the model; the regex reads nothing).

---

Where the build actually is.

**Last updated:** 2026-09-17 · 848 tests passing, offline, no key

> **naksha draws floor plans.** Text in, a dimensioned drawing out: walls with
> thickness, doors with swings, windows sized to the bye-laws, a ventilator in every
> bathroom, sanitaryware and beds, room sizes in feet, and a list of what is wrong with
> the result, graded critical, major or minor, each with why and what to do. Seven of
> eight stages are built. **Of the nine reference plots, six are legal and three are
> refused.** Two refusals are plots whose rooms cannot physically fit, their legal minimums
> 166% and 101% of the footprint; the third is the 30x50, whose shared bathroom opens only
> off a bedroom in every candidate. None is clean now: the circulation engine grades how a
> house is walked, and it has something to say about every plan.

## Architect phases (2026-09-17, uncommitted)

The per-plot table below predates these. Done: **1** furniture shapes rooms, **2** the stair
as a fixed element, **5** brief requirements as constraints; plus the viewer's grade badge
and ⑥ placing furniture by search rather than greedily (bedrooms missing a bed or wardrobe
7 → 3 of 41). Not started: **3** planning grid, **4** zoning, **6** wet stacking, **7**
parking strategy, **8** open plan, **9** site, **10** architectural scoring. Every weight
tried and every trade: `DECISIONS.md`, "Legal is not usable", "The stair is a shape a step
dictates", "A brief's near the entrance is a constraint".

Benchmark now: 21 errors, 44 major, 55 minor, 4 storeys failing, 36/144 zones, 36/78
two-sided air, 104/136 rooms furnished, every stair buildable (was 19/22), 17/22 drawn with
flights. Against phase 1's baseline that is +1 error (the refused 20x30), +9 majors (5 are
doors onto steps, a new finding) and −13 zones.

Known next: the kitchen, dining and hall still come out as full-depth strips when two car
bays take the road frontage (phase 7); five stairs still have a door onto the steps; a
near-entrance request on a single-storey 40x60 3BHK misses by 20 cm.

## What it produces, per plot

Measured 2026-09-15, seed 7, `--fallback-only`, from the 14-plan benchmark's baseline:
⑦'s checks, circulation graded by the engine, choosing among ⑤'s 12 best finished
candidates per floor, each storey chosen with the storeys above it. Time is the pipeline
alone, programme to checked drawing. Circulation is each storey's score out of 100.

| plot | storeys | result | circulation | time |
|---|---|---|---|---|
| 20x30 2BHK | G+2 | **refused** — 12 critical: minimums are 166% of the footprint, the stair cannot be reached, the hall has no window, the top stair misses the one below | 20 · 96 · 0 | 0.6 s |
| 25x40 2BHK | G+1 | legal · 1 major, 1 minor — a bathroom, the stair and the hall only through the kitchen | 79 · 100 | 3.4 s |
| 30x30 2BHK | G+1 | legal · 1 minor — the car bay has no door into the house | 93 · 100 | 1.8 s |
| 30x40 2BHK | G | legal · 2 major, 1 minor — both bedrooms, a bathroom, the corridor and the hall only through the kitchen · the master's en-suite opens off the corridor | 73 | 3.4 s |
| 30x40 3BHK | G+1 | **refused** — minimums are 101% of the footprint and there is no front door; `--stilt` is the answer | 0 · 98 | 1.0 s |
| 30x40 3BHK `--stilt` | stilt+2 | legal · 2 major, 5 minor — dining and kitchen not joined · no bathroom on the top floor | 92 · 88 · 98 | 0.8 s |
| 30x50 3BHK | G | **refused** — a shared bathroom reachable only through a bedroom · a bedroom with no window · the hall, dining and two bedrooms only through the kitchen | 30 | 4.2 s |
| 40x60 3BHK | G | legal · 2 major, 4 minor — both bedrooms, a bathroom, the corridor and the hall only through the kitchen · hall and dining not joined | 74 | 2.1 s |
| 50x80 4BHK | G | legal · 4 major, 3 minor — a 19.6 m² foyer · hall, dining and kitchen not joined · the master's en-suite off the corridor | 76 | 2.4 s |

A **critical** finding is a plan naksha refuses: a room below its statutory minimum
measured inside its walls; a room with no route to it, or reachable only through a
bedroom or bathroom; an en-suite reached through another private room; a stair that misses
the stair below or whose only way on is a bathroom; a bedroom or living room with no
window at all; no front door; a car bay no driveway reaches. A **major** one is legal and wrong: rooms only through the kitchen, a visitor's
arrival through the dining room, a living and dining room not joined, a room glazed short of
the bye-laws, a bedroom floor with no bathroom, a room twice the size it should be, a toilet
against the kitchen or pooja room. A **minor** one is worth optimising: a car porch with no
door into the house, a corridor wider or longer than its doors need.

**What moved this table, in order.** ⑤ grows *road-first* slicing trees — car bay and
foyer as a strip along the road, the house behind — beside the random pool, and ⑦ picks
among ⑤'s 12 best finished candidates instead of the lowest penalty winning. That
exposed a hole in ⑦: a ground floor with no front door was walked from its stair and
came back clean, and the "clean" 25x40 had no entrance. Then three fixes for the two
plots still refused for their car bay:

1. **The hill-climb stopped trading the front door for points.** On each plot the one
   road-first plan that dimensioned was legal before climbing and refused after: a swap
   moved the foyer off the street for 85–115 points of sector and adjacency.
2. **⑦ refuses living rooms behind a private room.** The climb fix alone produced a 30x50
   entered front door → foyer → bathroom → corridor → hall, and as a single warning the
   judge ranked it above a refused plan.
3. **⑤ searches past its shortlist on a thin floor.** CP-SAT refuses an undimensionable
   tree in under a millisecond, so stopping at the shortlist rationed something cheap.
   Past it, road-first trees dimensioned 1 in 100 on the 30x40 2BHK and 1 in 270 on the
   30x50. A floor whose minimums exceed its footprint skips the search, and roomy plots
   never reach it.
4. **⑦ checks that the stairs connect, and Stage B pulls each upstairs stair over the
   one below.** The stilt plan here had a first-floor stair metres from the ground-floor
   one; nothing checked, and it was reported legal. Plans also replay exactly now, since
   Stage B stops on work rather than on the clock.
5. **⑦ reports a front door that does not lead into the house, and rooms kept apart that
   share a wall.** No plan got better for it, and the 30x30 stopped being clean.
6. **⑤ grows corridor-first layouts** — the corridor across the floor, every other room
   keeping a wall on it — as a group beside the others. Route warnings fell from 10 to 7
   over thirteen plans at no cost in time, and the 40x60 came out clean.
7. **The car bay is a real 3 x 6 m bay with a gate.** The rule held 18 m² and 3 m of width
   and passed a 4.3 x 4.6 m bay, and every bay was drawn with a window. The 6 m length is
   now enforced; a bay running back from the road gets a 2.7 m gate and one lying along it
   a gate across its long side, like a car porch; and ⑦ refuses a bay a car cannot get
   into. No plan gained a warning. The tight plots that search past the shortlist take
   about 1.5 s longer.
8. **Vastu zones count for something.** The judge ranks missed zones after warnings and
   before the penalty, and ⑤ adds corridor trees that follow the compass. Over eleven plans
   zones met rose from 18 to 28 of 110, with no warning or time added.
9. **Every room that meets the outside gets air.** Across fourteen plans no bathroom had
   a ventilator (0 of 31, all beside an outside wall) and no room had windows on two sides
   (0 of 78). Now bathrooms get ventilators, stairs, corridors and pooja rooms a window,
   and rooms people live in a second window on a second outside wall: 31 of 78 get air
   from two sides. ⑦ warns about a bathroom that cannot breathe and records which rooms a
   breeze can cross; the judge prefers more of them, after Vastu. No warning moved.
10. **Sizes in feet.** Drawings label each room in feet-and-inches and sq ft; every
   message an owner reads gives sq ft with m² in brackets.
11. **Circulation is graded, and it picks the plan.** ⑦'s reachability walk became the
   circulation engine: access graded by what the best route must cross, the walks a house
   is used for, corridors judged by their work, a score no critical finding can pass. The
   judge ranks refusals with circulation first, then majors, Vastu, circulation quality,
   and chooses each storey with the storeys above it. Over the 14-plan benchmark, against
   the plans the old judge chose checked by the engine: critical findings 19 to 17,
   majors 30 to 24, storeys failing 5 to 4, zones 38 to 41. The 30x50 is refused for a
   defect ⑦ used to call a warning, and the JP Nagar 4BHK, whose first floor the engine
   fails, is now chosen with one that scores 100.
12. **A room people live in with no window is refused, and failures are ranked by the
   rooms they leave without access.** The judge had chosen a windowless bedroom on the
   30x40 2BHK to save a circulation finding; graded critical, it cannot. Measuring that
   showed failures were ranked by counting findings, which showed the 30x50 with seven
   rooms behind a private room instead of one bathroom behind a bedroom. Counted in
   rooms, no passing plan changed, and the 20x30 and 30x50 each count one more critical.

## The model path — first end-to-end runs, 2026-09-13

Three briefs through `-p claude_code` (claude-opus-5 on a Claude subscription, no API key),
full pipeline. ① answered first time on every brief, and ③ read what the rules cannot:
"40x60 for a joint family" became a 4BHK for six with two car bays, a ground-floor bedroom
with its own bathroom, and a family room and balcony upstairs.

**All three plans were refused — no ground floor had a front door — and the cause was
ours, not the model's.** Three defects on that path, all fixed:

1. `llm/program.py`'s merge built rooms by hand and copied one rule flag of five. Halls
   and corridors became private rooms; the foyer and the car bays never needed the street.
   Rooms are now built by `program.spec_for`, the function the offline path uses.
2. `--stilt` and `--porch-in-setback` never reached the model path.
3. `-P` and `-L` each called ③, so the room list printed was not the one drawn, at twice
   the cost.

Replayed offline through the fixes — `tests/golden/program_drafts.json` records the
model's answers — **all four model plans have no errors and no warnings on any floor**:
the 30x40 3BHK as G+1 and on a stilt, the 30x50, and the joint-family 40x60. The offline
expansion has warnings on every one of those plots. One recorded answer per brief at
seed 7 is a small sample, and the drawings show things ⑦ does not check: a 16.7 m²
staircase on the joint family's first floor, a 30x40 entered through the stair hall, and
a bathroom and a pooja room opening off a staircase. **Correction, 2026-09-14:** the
30x50 among them, and the live re-run below, had a stair that missed the stair beneath,
which ⑦ could not yet see. With the stair check and Stage B's shaft pull, all four
replays again have no errors and no warnings — now with stairs that connect.

Each live brief took 150–200 s end to end, most of it two ③ calls. **Re-run live after
the fixes**, the 30x40 3BHK with `--stilt` took 86 s against 153 s, drew three storeys —
car bay, foyer, stair and open ground; the living floor; the bedroom floor — printed the
programme it drew, and ⑦ found nothing on any floor — though its top-floor stair missed
the one below, which ⑦ could not yet see. The drawings still show what ⑦ does
not judge: a corridor on the living floor that leads nowhere, two bedrooms opening off the
stair landing, and a master bedroom smaller than one of the other bedrooms.

## Pipeline

| Stage | Status | Notes |
|---|---|---|
| ① INTENT | **built** | `text → Brief` + ≤3 clarifying questions |
| ② ENVELOPE | **built, gated** | Arithmetic done. Refuses without `--allow-unverified` |
| ③ PROGRAM | **built ×2** | Deterministic expansion *and* an LLM version in `llm/program.py`, first run end to end 2026-09-13 |
| ④ FEASIBILITY | **built** | Explains, and measures each option by running the solver |
| ⑤ LAYOUT | **built** | Slicing tree (A) + CP-SAT (B), deeper on thin floors; a seed replays exactly. 1.1–4.5 s a plot end to end |
| ⑥ REFINE | **built** | Walls, doors, windows, fixtures, porch in the setback |
| ⑦ VALIDATE | **built** | Circulation · access · sanitation · size · light · legality, per storey. Also picks ⑤'s plan |
| ⑧ CRITIC | not started | rerank + rationale, behind a flag — optional by design |

Also built since the last update: `--svg` export, the Konva viewer (runs, Node 20 via
nvm), and `PlanBundle` carrying ⑥'s drawing and ⑦'s findings so both consumers read
the same thing.

## ① INTENT — done

`text → Brief`, schema-constrained, 2 schema retries, then a deterministic offline
parser so a dead API is not a dead product. Versioned prompt hashed into provenance.
Model-agnostic behind a four-error provider seam (`anthropic` · `openai` ·
`claude_code`).

**Every guess is visible.** Stage ① fills blanks rather than emitting nulls, and each
fill is a structured `Assumption` with a confidence — a null nobody chose just pushes
the same guess into a stage where nobody can see it.

**A clarifier that usually asks nothing.** Tier gates, confidence only sorts what
survives: 0.7 for blocking fields, 0.5 for consequential, never for the rest. A
dependency DAG collapses questions one answer would settle, and caps a derived value's
confidence at its source's. Budget of 3 is a ceiling, not a quota.

```
30x60 east facing 2bhk in Pune            → asks nothing
make me a house                           → city, plot size, bedrooms
30x40 north facing corner plot in Bengaluru → road edges
```

## ② ENVELOPE — built, gated

`Brief → buildable rectangle + FAR/coverage/storey caps`. Deterministic; no model, no
network. Two frames kept apart deliberately: a `PlotSpec` is road-relative
(`width_m` is frontage), an `Envelope` is compass-aligned. Conflating them rotates
every plan 90°.

Declines in four typed ways, each carrying its numbers — `CityUnknown`,
`NoRulesetForCity`, `RulesetUnverified`, `EnvelopeInfeasible`. A neighbouring city's
bye-laws are not a conservative default; they are a different answer.

```
30x40 in Whitefield, 9m road   → 7.69 × 7.14 m · FAR 1.5 · ≤2 floors · built ≤167.2 m²
same plot, corner              → 5.14 × 7.69 m   (both roads take the front setback)
30x40 in Pune                  → NoRulesetForCity
```

---

## ⑤ LAYOUT — built, and Stage B was dead until 2026-09-06

Both stages exist and run. The finding of this session is that **Stage B was silently
producing nothing for every plot quoted in feet** — its CP-SAT model works on a 1 cm
grid, a feet-derived envelope does not land on it, so `Layout` rejected every tuned
result as a gap and `solve` fell through to Stage A without a word. Measured: 0 of 24
tuned layouts accepted on each feet brief, 23 of 24 on a metric one. Fixed by flooring
the grid inwards and snapping the outer edges back to the envelope's exact floats; see
`DECISIONS.md`. Hill-climbing was also missing from the tuned path, and adding it was
necessary — the grid fix alone made the largest brief worse.

Effect, four briefs, before → after:

| brief | before | after |
|---|---|---|
| 40x60 3BHK + pooja | 0 unbuildable, penalty 160, 5/10 edges | 0, penalty 130, 8/10 |
| 30x50 3BHK | **2 unbuildable**, penalty 360, 6/9 | **0**, penalty 200, 4/9 |
| 30x40 2BHK | **1 unbuildable**, penalty 210, 6/8 | **0**, penalty 70, 6/8 |
| 50x80 4BHK + study | 0, penalty 285, 9/12 | 0, penalty 130, 9/12 |

**Every brief now produces a legal plan.** That is new — two of the four were placing
rooms below their statutory minimums.

Two things are known and open, both traced to `Layout` requiring exact tiling of the
*maximum permitted* footprint: adjacency tops out well short of the graph (question 6)
and surplus area is forced into rooms, producing a 30 m² bathroom on a 50x80
(question 7). Neither is a tuning problem and both change the IR, so both are in
`DECISIONS.md` under "these need you, not me".

## Blockers

**1. The car porch — answered in practice, not in law.** A 30x40 3BHK fails on one
room: the statutory 3.0 × 6.0 m bay, squeezed to 2.2 m² on a ground floor that is
otherwise fine. The front setback cannot take the bay on any plot naksha models — 1.46 m
on a 30x40, 2.93 m on a 50x80, against the 3.0 m a bay needs — so `--porch-in-setback`
refuses with those numbers. `--stilt` lifts the house off its parking instead and the
plan comes out legal. A stilt is a design answer rather than a permission, which is why
it does not wait on VERIFY.md Q1. What Q1 still has to settle is whether stilt area sits
outside FAR, which naksha does not model.

**2. Unverified rule data, by file.** `setbacks_v1`: 0 of 10 bands verified, so
`build_envelope` raises `RulesetUnverified` without `allow_unverified=True`. The FAR
figure *is* resolved — RMP-2015 Table 10 gives 1.75, read first-hand — but the setback
bands behind it are not. `spaces_v1`: 11 verified with clause references, 10 not.
`refine_v1`: 0 of 4 — wall thickness, door widths, window fraction, fixture sizes are
all practice.

**3. Only Bengaluru is mapped.** Pune, Hyderabad, Chennai, Mysuru return
`NoRulesetForCity`.

**4. Two figures need an architect.** VERIFY.md Q6: window area as a fraction of floor
area, encoded at 1/10 and transcribed from practice. At 1/6 a hall window goes from
0.93 m to 1.6 m, which changes which walls can carry one.

**5. The live golden set has never been run end to end.** `pytest -m live` still has
not been executed as a suite.

**6. The 20x30 is a real refusal.** 29.3 m² buildable after setbacks and the bay is 18
of it; `--stilt` improves it from 8 errors to 6 and does not make it legal.

## Decisions taken, and why

- **`road_edges` is stored; `facing` and `corner_plot` are computed.** A corner plot
  collapsed to one direction under-sets-back its second road — a wrong envelope, not a
  wrong label. Both are serialised for consumers but refused as inputs.
- **`base_far`, never `total_far`.** The excess needs TDR bought. Budgeting against it
  would size a programme that cannot legally be built.
- **`city` is `blocking`.** It was `defaultable` while nothing consumed it; ② cannot
  select a ruleset without it.
- **Rules are data with citations.** Each band names its source and carries
  `verified: false` until checked. `load_ruleset` warns while any remain.
- **Enum enforcement is proven, not assumed.** Closed vocabularies are enforced both in
  the schema sent to the provider and on parse. `test_schema_enforcement.py` discovers
  models and enums *by inspection*, so ③'s `RoomSpec` and `AdjacencySpec` are covered
  the moment they exist.

## Circulation — graded, and choosing the plan

Stage ⑦'s circulation check is the engine in `backend/app/circulation/`, its rules in
`rules/circulation_v1.json` (practice, marked `verified: false`). Each report carries graded
findings with why and fix, and a summary: score and health, seven dimensions, every room's
access, the journeys and the corridor verdicts. The CLI prints a `[circulation]` line per
storey and the fix under each critical finding. The viewer shows the score and health, the
critical, major and minor counts, and each finding with why it matters and the fix, its
rooms and route drawn on the plan; an advanced view lists the score's parts, the journeys,
the corridors and the graph. It type-checks and builds, and has not yet been looked at in a
browser. How the judge's order was measured, and what the chosen plans give up for it, is in
DECISIONS.md.

---

## Next

**Agreed next, in order — the circulation plan.** Phase 1 is built; its viewer panel still
needs a look in a browser. Phase 2, measured on the same 14 plans: door-aware adjacency in ⑤, ranking
by proper access, the staircase minimum size, and a landing-first upper-floor generator.
Then repair and fallback, and the docs. The road strip that takes the whole frontage stays
in the imperfections below.

**1. Make the legal plans good.** Nothing in the reference set is refused any more but
the two plots whose rooms cannot fit. What ⑦ still reports, by what it costs a user: the
living room reached through the kitchen (25x40, 30x40 2BHK, 30x50) and bedrooms beyond it
(40x60) — in both drawings looked at, the foyer, a narrow strip beside the car bay,
opened into whichever room the tree put behind it; a bedroom with no window (30x50); no
bathroom on the stilt plan's top floor; a bathroom reached through the study (50x80).
The judge counts findings rather than weighing them, so one warning naming six rooms ties
with one naming one. Corridor-first layouts cleared the worst of the circulation; what
remains is the route through the kitchen on the 25x40 and the 30x50, a front door into a
stair room or corridor, and Vastu placement. Judging 24 or
36 candidates instead of 12 barely helps — improvements have to come from what ⑤
generates.

**2. Measure the model path over more briefs.** Three briefs have been through the model
live, the 30x40 twice. Its plans beat the offline expansion on every replay and on the
live re-run; if that holds over more briefs, the offline expansion is the fallback it was
meant to be, and the reference table above should be measured on the model path.

**3. An editor.** The Konva viewer draws and selects; it does not edit. Decision 3 says
an edit becomes a *constraint* and the plan is re-solved — never a stored coordinate —
so this needs `api/` (the viewer reads a static `plan.json` today) and a constraint type
in the IR before any drag handle is worth building.

**4. DXF export.** v1 scope. `ir/refined.py` already stores centrelines, which is what
DXF wants. Open choice: add `ezdxf`, or write ASCII DXF R12 with no new dependency.

**5. PDF export**, and **6. Stage B shaft preference** (DECISIONS question 9).

## Known imperfections, in priority order

- **A staircase can be a box no stair fits in.** The JP Nagar 4BHK's ground-floor stair is
  1.5 × 3.5 m inside its walls: too narrow for a stair that turns (about 2 m) and too short
  for a straight one (about 3.75 m of steps at ordinary sizes, before any landing). The
  upstairs stair is a different box, 3.0 × 2.6 m, and ⑦ passed the pair because they
  overlap by 76% against a 75% bar. The rule holds a stair to 5 m² and 1.0 m of width only.
- **A road-first strip takes the whole frontage.** On the JP Nagar 4BHK two cars and a
  foyer need about 43 m² and the strip is 64: one bay came out 28.8 m², the foyer a 2 × 6 m
  passage, and the kitchen 6.8 m² against a 14 m² target, with 60% of the ground floor
  parking and circulation. It also takes the north-east corner the pooja room wants.
- **The judge counts majors; it does not weigh them.** One finding naming six rooms behind
  the kitchen ties with one naming a single room, and circulation quality, which does see
  the difference, ranks after the zones. The 30x40 2BHK keeps the plan entered through its
  kitchen, 73/100, over one at 89 whose bedroom has no window at all: the two tie on
  majors, and the first meets a zone. DECISIONS.md question 11 is the better fix.
- **Vastu is still mostly missed: 28 of 110 zones.** The live 30x40 3BHK meets 3 of 12,
  for geometric reasons: its east car bay and foyer take the north-east corner the pooja
  room wants, and its stair, fixed on the west by the ground floor, blocks the south-west
  the master bedroom wants upstairs. Choosing floors together is the untried lever.
- **Most rooms get air from one side only: 47 of 78.** A corridor with a row of rooms
  either side leaves most rooms one outside wall, and ⑥ can only open the walls ⑤ gives
  it. More would take shallower plans or an internal courtyard, neither generated yet.
- **An interior dining room has no window.** `spaces_v1` lets a dining room sit inside the
  house, as it commonly does, though it is a habitable room. The usual answer is to open
  it wide to the hall, which the IR cannot draw: every room is walled with doors.
- **Two of nine reference plans are refused and six more carry warnings.** Every
  defect in the table above is real and was confirmed in the plan data, not only by eye.
- **Upper-floor staircases absorb surplus area**, and the shaft pull makes it worse
  where the tree leaves the stair against a wall the shaft is not on — 17.7 m² on the
  25x40's first floor. ⑦ says nothing below twice a room's growth ceiling, and a
  staircase's is 9.5 m², so the 19 m² line is not crossed.
- **`fits` says yes when the grown programme is over FAR.** It checks legal minimums
  against each floor and against FAR, then prints the grown target total beside the FAR
  budget without comparing the two — on a stilt, "yes — 224.8 m² of 195.1 m² allowed".
  Three full storeys are over the cap unless stilt parking is exempt from FAR, which is
  VERIFY.md Q1, so what that line should say waits on the answer.
- **The stilt level mislabels its largest room** — a 40 m² staircase beside 5 m² of open
  ground. Question 9.
- **Room names are drawn over bed and WC symbols**, which makes labels hard to read.
- **Sector (Vastu) satisfaction is the largest remaining penalty term.** Untouched on
  purpose: it is a preference, and every other defect outranked it.
