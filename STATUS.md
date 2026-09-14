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
.venv/bin/pytest -q                                  # 696 tests, no network, no key

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

**Last updated:** 2026-09-14 · 696 tests passing, offline, no key

> **naksha draws floor plans.** Text in, a dimensioned drawing out: walls with
> thickness, doors with swings, windows sized to the bye-laws, sanitaryware and beds,
> and a list of what is wrong with the result. Seven of eight stages are built. **Of the
> nine reference plots, one is clean, six are legal with named defects, and two are
> refused** — and both refusals are plots whose rooms cannot physically fit: their legal
> minimums come to 166% and 101% of the footprint. The 30x30 lost its clean result when ⑦
> learned to see a kitchen against a bathroom; the 40x60 gained one when corridor-first
> layouts let every room open off the corridor.

## What it produces, per plot

Measured 2026-09-14, seed 7, `--fallback-only`, with ⑦'s six checks — connecting stairs, a front
door into the house and rooms kept apart included — choosing among ⑤'s 12 best finished candidates per floor. A
seed now replays the same plan on any machine. Time is end to end, Python start-up
included.

| plot | storeys | result | time |
|---|---|---|---|
| 20x30 2BHK | G+2 | **refused** — 29.3 m² buildable and the car bay alone is 18; minimums are 166% of the footprint | 1.4 s |
| 25x40 2BHK | G+1 | legal · 2 warnings — the front door opens into the stair room · a bathroom, the corridor and the hall only through the kitchen | 2.2 s |
| 30x30 2BHK | G+1 | legal · 1 warning — the kitchen shares a wall with a bathroom | 1.9 s |
| 30x40 2BHK | G | legal · 2 warnings — a bedroom and a bathroom only through the kitchen · a bedroom with no window | 2.2 s |
| 30x40 3BHK | G+1 | **refused** — minimums are 101% of the footprint; `--stilt` is the answer | 3.1 s |
| 30x40 3BHK `--stilt` | stilt+2 | legal · 1 warning — no bathroom on the top floor. Its stairs connect only since 2026-09-14 | 1.3 s |
| 30x50 3BHK | G | legal · 4 warnings — the front door opens into a corridor · the hall, two bedrooms and a bathroom only through the kitchen · a bathroom only through a bedroom · a bedroom with no window | 2.8 s |
| 40x60 3BHK | G | **clean** | 2.9 s |
| 50x80 4BHK | G | legal · 2 warnings — a bathroom only through the study · hall glazed to 8% | 2.8 s |

An **error** is a plan naksha refuses: a room below its statutory minimum measured inside
its walls, a room with no route to it, a hall, kitchen or dining room reachable only
through a bedroom or bathroom, a stair that misses the stair below, or a car bay no driveway reaches. A **warning** is
legal and wrong: a route through a bedroom or the kitchen, a bedroom floor with no
bathroom, a room more than twice the size it should ever be, a room short of daylight, a
front door that does not open into the hall, a toilet against the kitchen or pooja room.

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
| ⑤ LAYOUT | **built** | Slicing tree (A) + CP-SAT (B), deeper on thin floors; a seed replays exactly. 1.2–3.0 s a plot end to end |
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

---

## Next

**1. Make the legal plans good.** Nothing in the reference set is refused any more but
the two plots whose rooms cannot fit. What ⑦ still reports, by what it costs a user: the
living room reached through the kitchen (25x40, 30x40 2BHK, 30x50) and bedrooms beyond it
(40x60) — in both drawings looked at, the foyer, a narrow strip beside the car bay,
opened into whichever room the tree put behind it; a bedroom with no window (30x50); no
bathroom on the stilt plan's top floor; a bathroom reached through the study (50x80).
The judge counts findings rather than weighing them, so one warning naming six rooms ties
with one naming one. Corridor-first layouts cleared the worst of the circulation; what
remains is the route through the kitchen on the 25x40 and the 30x50, a front door into a
stair room or corridor, the car bay's opening and size, and Vastu placement. Judging 24 or
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
- **Every car bay is drawn with a window** where a vehicle opening belongs. Stage ⑥ has
  no opening type for one yet.
- **Room names are drawn over bed and WC symbols**, which makes labels hard to read.
- **Sector (Vastu) satisfaction is the largest remaining penalty term.** Untouched on
  purpose: it is a preference, and every other defect outranked it.
