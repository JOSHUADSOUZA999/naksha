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
.venv/bin/pytest -q                                  # 675 tests, no network, no key

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

**Last updated:** 2026-09-13 · 675 tests passing, offline, no key

> **naksha draws floor plans.** Text in, a dimensioned drawing out: walls with
> thickness, doors with swings, windows sized to the bye-laws, sanitaryware and beds,
> and a list of what is wrong with the result. Seven of eight stages are built. **Of the
> nine reference plots, one is clean, six are legal with named defects, and two are
> refused** — and both refusals are plots whose rooms cannot physically fit: their legal
> minimums come to 166% and 101% of the footprint. Earlier the same day it was one,
> three and five, and the one "clean" plan had no front door.

## What it produces, per plot

Measured 2026-09-13, seed 7, `--fallback-only`, with ⑦'s six checks choosing among ⑤'s
12 best finished candidates per floor — one plot at a time on an idle machine, which
matters (see Known imperfections). Time is end to end, Python start-up included.

| plot | storeys | result | time |
|---|---|---|---|
| 20x30 2BHK | G+2 | **refused** — 29.3 m² buildable and the car bay alone is 18; minimums are 166% of the footprint | 1.7 s |
| 25x40 2BHK | G+1 | legal · 1 warning — a bathroom, the corridor and the hall only through the kitchen | 2.4 s |
| 30x30 2BHK | G+1 | **clean** | 2.0 s |
| 30x40 2BHK | G | legal · 1 warning — both bedrooms, both bathrooms, the corridor and the hall only through the kitchen | 2.4 s |
| 30x40 3BHK | G+1 | **refused** — minimums are 101% of the footprint; `--stilt` is the answer | 4.8 s |
| 30x40 3BHK `--stilt` | stilt+2 | legal · 1 warning — no bathroom on the top floor | 3.3 s |
| 30x50 3BHK | G | legal · 3 warnings — the hall, two bedrooms and a bathroom only through the kitchen · a bathroom only through a bedroom · a bedroom with no window | 3.0 s |
| 40x60 3BHK | G | legal · 1 warning — two bedrooms, both bathrooms and the corridor only through the kitchen | 2.7 s |
| 50x80 4BHK | G | legal · 2 warnings — a bathroom only through the study · hall glazed to 8% | 2.9 s |

An **error** is a plan naksha refuses: a room below its statutory minimum measured inside
its walls, a room with no route to it, a hall, kitchen or dining room reachable only
through a bedroom or bathroom, or a car bay no driveway reaches. A **warning** is
legal and wrong: a route through a bedroom or the kitchen, a bedroom floor with no
bathroom, a room more than twice the size it should ever be, a room short of daylight.

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

## Pipeline

| Stage | Status | Notes |
|---|---|---|
| ① INTENT | **built** | `text → Brief` + ≤3 clarifying questions |
| ② ENVELOPE | **built, gated** | Arithmetic done. Refuses without `--allow-unverified` |
| ③ PROGRAM | **built ×2** | Deterministic expansion *and* an LLM version in `llm/program.py` |
| ④ FEASIBILITY | **built** | Explains, and measures each option by running the solver |
| ⑤ LAYOUT | **built** | Slicing tree (A) + CP-SAT (B), deeper on thin floors. 1.7–4.8 s a plot end to end |
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
with one naming one.

**2. An editor.** The Konva viewer draws and selects; it does not edit. Decision 3 says
an edit becomes a *constraint* and the plan is re-solved — never a stored coordinate —
so this needs `api/` (the viewer reads a static `plan.json` today) and a constraint type
in the IR before any drag handle is worth building.

**3. DXF export.** v1 scope. `ir/refined.py` already stores centrelines, which is what
DXF wants. Open choice: add `ezdxf`, or write ASCII DXF R12 with no new dependency.

**4. PDF export**, and **5. Stage B shaft preference** (DECISIONS question 9).

## Known imperfections, in priority order

- **Two of nine reference plans are refused and six more carry warnings.** Every
  defect in the table above is real and was confirmed in the plan data, not only by eye.
- **Results depend on machine load.** Stage B stops each CP-SAT solve at 0.15 s, so a
  busy machine can return a different plan for the same seed: the 40x60 changed when
  nine plots ran at once. Unloaded, three runs gave identical layouts. Measure one plot
  at a time; see DECISIONS.
- **The stilt level mislabels its largest room** — a 40 m² staircase beside 5 m² of open
  ground. Question 9.
- **Every car bay is drawn with a window** where a vehicle opening belongs. Stage ⑥ has
  no opening type for one yet.
- **Room names are drawn over bed and WC symbols**, which makes labels hard to read.
- **Sector (Vastu) satisfaction is the largest remaining penalty term.** Untouched on
  purpose: it is a preference, and every other defect outranked it.
