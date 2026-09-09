# DECISIONS — naksha

Append-only. Why the code is shaped the way it is, what broke, and what is still open.
`CLAUDE.md` is how to work here; `STATUS.md` is where we are; this is how we got here.

---

## Open questions — these need you, not me

**8. Every plan we call legal has rooms below their statutory minimums.** Found by
building stage ⑥, and it is the most consequential thing in this file.

Stage ⑤ checks `min_area_sq_m` and `min_width_m` against its own rectangles, and those
run to **wall centrelines**. The bye-laws mean **clear internal** size: a 2.1 m minimum
bedroom width is 2.1 m of floor, not 2.1 m between wall centres. So the legality check
is optimistic by half a wall on each side, on every room, systematically.

Measured across the four reference briefs, at 230 mm exterior and 115 mm interior:

| brief | unbuildable by tiling | below a minimum once walls are real |
|---|---|---|
| 30x40 3BHK | 3 | **16** |
| 30x50 3BHK | **0** | **7** |
| 40x60 3BHK | **0** | **6** |
| 50x80 4BHK | **0** | **7** |

Walls take about 10% of the floor area on a 40x60 — 112.9 m² tiled against 102.1 m²
clear. Three of the four briefs report a clean plan and are not one.

**The fix is not subtle, it is just expensive.** Stage ⑤ has to solve against *gross*
minimums: the clear figure plus a wall allowance, ~0.115 m per dimension for an interior
room and ~0.23 m where it meets the boundary. The difficulty is that the allowance
depends on which walls a room ends up against, which is not known until it is placed —
so it is either a conservative constant (tightening every room, including the ones that
did not need it) or a second pass.

**Why it is yours and not mine.** It tightens every brief at once, and the plots that
matter most are already the tightest: a 30x40 is 98% packed before this, and adding
115 mm to every room's minimum may make it infeasible outright. That is a product
decision — whether naksha refuses a 30x40 3BHK honestly, or keeps producing plans that
need a draughtsman to fix. Both are defensible; the current behaviour, which is to claim
legality it does not have, is not.

**Not silently changed.** `refine.breaches()` reports every one and the CLI prints them
under `[walls]`, so the discrepancy is visible while the decision is open. Room labels
in the SVG now show clear area rather than the tiled figure, which is the number a
person should be reading anyway.


**1. FAR for Bengaluru. Blocks stage ③'s room budget.**
Three candidates for the same 30×40 plot, none confirmed operative:

| Figure | Source | Status |
|---|---|---|
| 0.75 | BBMP Building Bye-laws 2003, Table 6 | superseded in practice? |
| **1.50** | RMP 2031 Vol. 6, Table 6 (Planning Zone A) | **encoded** — document stamped *(Draft)* |
| 1.75 | widely reported for RMP 2015 | secondary sources only |

RMP 2031 was published for objections in 2017; I could not establish it was ever
notified. If it wasn't, Bengaluru still sanctions against RMP 2015 and the encoded
figure is wrong by ~2×. **Needs someone who has sanctioned a plan recently.**

**2. `VastuStrictness` — I cannot find it.** Asked about four times as
`off | advisory | balanced | strict`. `grep -rn VastuStrictness` across the whole
working tree returns **0 hits**; the only Vastu enum is `VastuStance` =
`strict | moderate | ignore`, and enum enforcement is proven working at both the
schema and parse layers (`tests/test_schema_enforcement.py`). If that vocabulary lives
in a design doc outside this repo, point me at it and I will implement it — `advisory`
as a level distinct from `balanced` needs solver semantics defined.

**3. Inferred city spelling.** "Bengaluru" vs "Bangalore" — the question was answered
"Other" with no text. I used **Bengaluru** (canonical, matches the golden set). One
line in `_LOCALITIES` to change.

**4. The strategic fork, defaulted rather than answered.** Vertical slice to a rendered
plan, versus finishing ①② to a polished stop. I took the **vertical slice** after
asking twice. See `STATUS.md` → Next.

**7. Should a plan have to fill the maximum permitted footprint?** It does today, and
that is where the last visible defect comes from.

`Layout` requires exact tiling of the *envelope*, and the envelope is the largest
rectangle the bye-laws permit. But max coverage is a **ceiling, not a requirement** — a
house does not have to fill its setback envelope. So whatever the programme does not
ask for is forced into rooms anyway:

| brief | programme wants | envelope | surplus |
|---|---|---|---|
| 30x40 3BHK | 74.9 m² | 74.9 m² | 0% |
| 30x50 3BHK | 93.6 m² | 93.6 m² | 0% |
| 40x60 3BHK | 107.5 m² | 149.8 m² | **28%** |
| 50x80 4BHK | 130.0 m² | 249.7 m² | **48%** |

Where the surplus is zero the plans are clean. Where it is 28% and 48% the solver
produces a 34.8 m² corridor and a 30.4 m² bathroom — legal, gap-free, zero unbuildable,
and not a house.

**Two fixes were tried and neither works, which is what points at the invariant.**
Weighting Stage B's objective by 1/target prices a square metre by who receives it; it
measured 2-2 on penalty across four briefs and lost adjacency on two, because relative
L1 is still linear and its optimum still puts the whole surplus in one room, just a
larger one. Penalising oversize in `score` was kept — the defect now reaches
`violations`, which is what stage ④ shows a user — but it cannot fix anything either,
since every candidate carries the same forced surplus and the ranking has nothing
better to choose.

The fix is to let the plan occupy a sub-rectangle sized to the programme rather than
the whole envelope. That is the same exact-tiling invariant as question 6, biting a
second way, and it is a change to the IR contract — so it is yours.

**5. Deferred.** Tappable `options` on `Question` for the wizard UI (plot-size options
are region-dependent, so not static JSON). Area-only briefs ask "what size is the
plot?" when what's missing is the *shape*.

**6. The adjacency ceiling is breakable, and breaking it costs decision 2's second
half.** Measured, not argued — spike scripts, nothing in `backend/` changed.

The ~75% adjacency ceiling is a property of the slicing-tree representation, and three
attempts to tune past it failed because tuning was never the problem. Single-stage
CP-SAT with **every adjacency edge as a hard constraint** reaches 100% on every brief
tried, 5 seeds each, scored by `app.solver.score` against the production pipeline on
the same programme:

| brief | two-stage (fixed) | single-stage CP-SAT (cov ≥ 90%) |
|---|---|---|
| 40x60 3BHK + pooja | 0 unbuildable, penalty 130, **8/10** edges | 0, penalty 5, **10/10**, 2.2 s |
| 30x50 3BHK | 0, penalty 205, 5/9 | 0, penalty 10, **9/9**, 4.2 s |
| 50x80 4BHK + study | 0, penalty 130, 9/12 | 0, penalty 5, **12/12**, 10.4 s |

**These numbers replace an earlier version of this table that was measured against a
broken pipeline** — Stage B was silently dead (see the bug below), so the "slicing
tree" column was really Stage A output and showed 1–2 unbuildable rooms on two briefs.
With Stage B actually running, **the two-stage pipeline produces legal plans on every
brief**, and the whole of CP-SAT's advantage moves from *legality* to *preferences*.
That is a much weaker case for changing anything: the question is now "nicer plans",
not "buildable ones".

**Decision 2's premise still holds exactly as written.** Single-stage CP-SAT with
*exact* tiling (`sum(areas) == envelope area`) is intractable: one run returned OPTIMAL
in 8.5 s and I briefly claimed the premise had fallen — three re-runs all returned
UNKNOWN at 20 s. Exact tiling is what makes it hard. What the spike relaxes is the
tiling requirement itself, replacing it with a coverage floor.

That relaxation is the cost, and it is an IR change: `Layout._tiles_its_bounds_exactly`
is the invariant that would go. The justification is real — at 1:100 a 200 mm wall
between two rooms *is* a gap, so exact tiling means zero-thickness walls, a
simplification stage ⑥ has to undo anyway. But the leftover is not all walls, and the
coverage floor is what decides which:

| coverage floor | biggest dead pocket, across the four briefs | still solves |
|---|---|---|
| 90% | 3.0 – **11.2 m²** | 4/4 |
| 94% | 2.8 – 8.4 m² | 4/4 |
| **97%** | 1.4 – 3.2 m² | 4/4 |
| 99% | 0.2 – 0.8 m² | 3/4 (50x80 UNKNOWN at 25 s) |

At 90% the "gap" includes an 11.2 m² void wider than 2 m — a room nobody can enter,
not a wall. At 99% the pockets are genuinely wall-sized and the largest brief stops
solving. 97% is where both hold, and even there a 3.2 m² pocket needs stage ⑥ to
either absorb it into a neighbour or name it.

**The other cost is time.** 0.4 s to 25 s against the current 2–4 s, and the 20 s+ runs
return FEASIBLE rather than OPTIMAL — they are the time limit, not the answer. That is
over the interactive budget that decision 2's two-stage design was chosen to protect.

**Not taken.** It contradicts a stated decision and changes the IR contract, so it is
yours to make, not mine. The three options as I see them: keep the ceiling and let
stage ⑥ live with 75% adjacency; adopt single-stage at a 97% floor and pay the latency;
or keep two-stage and add adjacency as a hard constraint to **Stage B** only.

**The third option is now measured, and it is dead.** Over 20,000 topologies per brief,
trees meeting *every* edge do exist — 1 in 20,000 on a 40x60, 5 in 20,000 on a 30x40.
They are also undimensionable: Stage B proved infeasibility on all 200 of the
highest-adjacency topologies, on every brief. Within a slicing tree, satisfying the
adjacency graph and meeting the legal minimums are in direct conflict, so ranking Stage
A by adjacency cannot work — it selects precisely the trees Stage B must reject.

**My recommendation is now to leave this alone.** The fix below closed the gap that
mattered.

---

## Bugs found, and what each one taught

### Stage B ran, was correct, and had every result silently discarded

The worst-shaped bug in the project so far, because nothing looked wrong. `solve` kept
returning plans; they were simply never dimensioned ones.

`tuning.tune` builds its CP-SAT model on a 1 cm integer grid, `x0 = round(x_min * 100)`.
An envelope derived from feet does not land on that grid — 30 ft is 9.144 m, and after
setbacks the span was 7.68096 m. So the solver's rectangle stopped a fraction of a
centimetre short of the wall, every tuned layout left a ~0.03 m² ribbon,
`Layout._tiles_its_bounds_exactly` correctly called it a gap, and `solve` swallowed the
`ValueError` in a `try/except ValueError: continue` written for a *different* failure
(a topology that genuinely cannot be dimensioned). It then fell through to the Stage A
path, which returns a real plan. Measured after the fact: **0 of 24 tuned layouts
accepted on every feet-derived brief, 23 of 24 on a metric one.**

Three things kept it hidden, and each is worth taking separately.

**The control passed.** A metre brief — `12m x 18m` — gives cm-round bounds and works
perfectly. Anyone checking "does Stage B work" with a round number sees it work. Every
brief the product is actually for is quoted in feet, as `intent_v1.md`'s own opening
line says.

**The except clause was right for the case it was written for, and wrong for this one.**
`tune` returning `None` means "this topology is infeasible" and continuing is correct.
A `ValueError` from `Layout` means "I built something the IR refuses", which is a
different event entirely and was being handled as though it were the same.

**Every test asserted a property both paths share.** Tiling, non-overlap, containment,
determinism — a Stage A layout has all of them. Nothing asserted *which stage produced
the answer*, so 462 tests passed over a dead stage. The new test that would have caught
it from outside asserts the outcome instead: Stage B exists because "100% of Stage A
topologies broke a minimum width", so a plan whose narrowest room is above its minimum
came from Stage B and one below it did not.

**The fix** floors the grid *inwards* (`ceil` the minimum, `floor` the maximum) so it
sits inside the envelope, then snaps the outermost edges back onto the envelope's exact
floats. Inwards rather than nearest is the point: rounding to nearest puts the grid
millimetres *outside*, and snapping back would then shave a room the model had just
proved met its minimum — trading a visible gap for an invisible illegality. Interior
cuts stay on the grid and are shared between neighbours, so they still cancel.

**It exposed a second omission immediately.** Fixing the grid alone made the 50x80
*worse* — penalty 285 → 415, adjacency 9/12 → 5/12 — because `improve()` was only ever
called on the Stage A path. The tuned path had never run, so nobody noticed it skipped
hill-climbing. Adding it there is safe for the same reason it is safe in Stage A:
swapping occupants leaves the geometry alone and the acceptance test is lexicographic.

Result across four briefs, before → after:

| brief | before | after |
|---|---|---|
| 40x60 3BHK + pooja | 0 unbuildable, penalty 160, 5/10 edges | 0, penalty **130**, **8/10** |
| 30x50 3BHK | **2 unbuildable**, penalty 360, 6/9 | **0**, penalty **200**, 4/9 |
| 30x40 2BHK | **1 unbuildable**, penalty 210, 6/8 | **0**, penalty **70**, 6/8 |
| 50x80 4BHK + study | 0, penalty 285, 9/12 | 0, penalty **130**, 9/12 |

The 30x50 loses adjacency (6/9 → 4/9) while going from two unbuildable rooms to none.
That is the lexicographic ranking working: a legal plan with fewer satisfied
preferences beats an illegal one with more.

**The lesson, which is the same one as the sector-jitter and graph-tree reverts.** I
measured the adjacency ceiling three times against this pipeline and concluded the
representation was at fault. Two of those three measurements were taken against a
pipeline whose second stage was not running. *Before concluding that a design is at its
limit, verify the design is actually executing.*


**Confidence laundering — twice.** `bathrooms: 0.8` reported on top of a
`bedrooms: 0.4` guess. Fixed per-field for `bedrooms`; you then spotted `plot size` as
a second root with four children. *Lesson: patching a class of bug at the site that
produces it will miss the next site.* Now one DAG-driven ceiling covers every root,
transitively, taking the minimum over parents — and runs on the model's output too,
because the model launders identically.

**The clarifier always found something to ask.** Bottom-2 by confidence means there is
always a lowest row. *That is not the same as a row worth asking about.* Fixed with a
tier gate: cost-of-being-wrong decides *whether* to ask, confidence only orders what
survives. `30x60 east facing 2bhk in Pune` now asks nothing.

**`"no parking"` matched the car-parking room pattern** on the word it was declining —
the brief listed a car park *and* zero bays. *Lesson: a regex that matches a noun will
match its negation.*

**`"north east corner"` produced road edges `north_east + south_east`** — stepping 90°
from a diagonal instead of splitting it into the two cardinals the phrase names. The
prompt already said the right thing; the parser disagreed with it.

**A pasted multi-line brief silently became two briefs**, and both halves parsed. The
tail — "Vastu is very important" — never reached the model. *Worse than an error,
because nothing failed.*

**`"Vastu is very important"` read as `moderate`**, because the parser required the
literal word "strict". *The system overriding the person.*

**`Ruleset.unverified` only walked `data["authorities"]`.** `spaces_v1` keys its blocks
by room name instead, so all 20 unchecked NBC minimums read as **verified**. Found
while writing a test that said `assert ... or True` — a vacuous test whose only value
was that writing it exposed the real gap. *Lesson: a checker that knows one document
shape silently passes every other shape.*

**`_assumption_table` wrapped only the reason column**, so one long value set the
column width for the whole table and pushed lines past 80 columns.

---

**The clarifier could not ask the question that mattered.** `"40x60 for a joint
family"` has no city; stage ② hard-failed with `CityUnknown`; the clarifier asked
about bedrooms and facing. The prompt correctly tells the model to leave `city` null
when nothing places it — and the model then records *no assumption*, so the selector
has nothing to gate on. *Lesson: a stage that can only act on what was written down
needs the gaps written down too.* `_record_gaps` now synthesises the assumption
deterministically on both paths, rather than asking the prompt for it.

**Stage ③ built parking from `extra_rooms` instead of `parking_bays`.** The model
lists `car_parking` in `extra_rooms` on some runs and not others for the same brief,
so a 15 m² bay appeared or vanished — a 14% swing in the programme from
nondeterminism, not input. *Lesson: when two fields can express the same fact, name
one authoritative and read only it.* Found by running the same brief twice.

**The test suite was not isolated from a developer's `.env`.** A single
`NAKSHA_INTENT_PROVIDER` silently redirected eight provider-inference tests. Two
independent sources had to be neutralised — `os.environ`, which `load_dotenv` fills at
import, and pydantic-settings' own `env_file` read, which bypasses `os.environ`.
Underneath it, `test_dotenv_reaches_os_environ` skipped writing its fixture whenever a
real `.env` existed and fell into a branch asserting only "something loaded" — so the
test guarding the credential bridge passed vacuously for every developer who had one.

---

**The scorer let cheap violations outvote catastrophic ones.** Hill-climbing traded
two rooms below their legal minimum for enough satisfied Vastu preferences to win on
total score, producing a 2.2 m² hall and calling it an improvement. `ILLEGAL` was
weighted 100 against `PREFERENCE` 5 — expensive, but tradeable. Ranking is now
lexicographic on `(unbuildable, penalty)`. *Lesson: when one class of failure is
categorically unacceptable, weight it out of the comparison entirely — a weight big
enough today stops being big enough as the problem grows.*

**The solver allocated by target and feasibility checked minimums, so they disagreed.**
`fits()` passed a programme at 49.3 m² of minimums into a 55 m² footprint; the solver
then scaled every room to ~76% of *target* and put car parking at 11.4 m² against a
13.5 m² floor. `RoomSpec` carries two areas precisely so the gap can absorb a tight
budget — and the solver used only one of them. Allocation now shrinks towards
minimums. *Lesson: two components deriving the same quantity by different routes will
disagree; make one of them read the other's answer.*

**I tuned a parameter against a broken metric, and the tuning evaporated.** A
sector-aware shuffle measured as a clear win — mean total 209 biased against 251
unbiased — and I wrote the sweep table into the source as justification. Once ranking
became lexicographic, the same comparison gave 2056 biased against 2024 unbiased:
inside the noise. The bias had been optimising exactly what the flawed objective
overvalued. Removed. *Lesson: tuning measures the objective, not the product. Fix the
objective first.*

**Graph-driven Stage A: measured, and rejected.** Stage ③ now emits 22 relationships
where the deterministic version emitted 15, and the solver realised only ~74% of them
— the failures being circulation, which means rooms you cannot walk between. Building
the slicing tree by recursive bisection of the adjacency graph *is* better per tree:
5.77 of 12 edges satisfied against 4.63, and CP-SAT dimensions 9/60 of them against
6/60. End to end it is not: 74% random, 71% graph, 73% mixed, across 8 seeds and 3
briefs. All inside the noise.

**Second time the same shape of idea has failed here** (a sector-aware shuffle was the
first). *Lesson: `solve` generates 900 candidates and keeps the best, so biasing
generation raises the mean and lowers the ceiling — and only the ceiling is taken.
Before adding a third bias, check whether selection is already doing the work.* More
candidates does not help either: 900 gives 75%, 3000 gives 73%.

`graph_tree` is kept, off the default path, documented with the numbers. It becomes
the right tool the moment generation stops being free.

**~75% of adjacency edges is therefore a ceiling of the current representation, not a
tuning problem.** Doing better needs adjacency to be a *constraint* rather than a
scored preference — which in a slicing tree is structural, not dimensional, so CP-SAT
cannot absorb it either. That is a representation change, and worth being deliberate
about rather than iterating into.

**Mining the actual bye-laws found errors in both directions.** Eleven of twenty
`spaces_v1` figures are now verified first-hand against the Karnataka Municipalities
Model Building Bye-Laws 2017 (UDD 14 TTP 2017 (P-4)), downloaded from BBMP's own BPAS
portal. Four were exact. Four were wrong:

| figure | had | statutory | direction |
|---|---|---|---|
| second bedroom width | 2.4 m | 2.1 m (cl. 5.1.2.3) | stricter than law — rejecting legal plans |
| hall | 11.0 m² / 3.0 m | 9.5 m² / 2.4 m (cl. 5.1.2.3) | stricter than law |
| corridor width | 0.9 m | 1.0 m (cl. 5.2.3(2)) | **permissive — allowed an illegal corridor** |
| car bay | 13.5 m² / 2.5 m | 18.0 m² / 3.0 m (cl. 5.1.8.2a) | **permissive — allowed an undersized bay** |

*Lesson: a figure transcribed from practice errs in both directions, and the permissive
errors are the dangerous ones — a plan too strict is merely cramped, a plan too loose
cannot be sanctioned.* The hall case was the subtle one: there is no "living room"
clause because cl. 5.1.2.3 defines a habitable room as one used for living and eating,
so 11 m² / 3.0 m was a **practice figure wearing a legal label**. Minimums are legal
floors; targets are practice. A convention in the `min` column silently narrows what
can be built.

**And NBC was the wrong document class for Bengaluru.** NBC 2016 is the national
*model* code; Karnataka adopted and adapted it, and a BBMP sanction is checked against
the Karnataka version. Cl. 1(3) further splits it: FAR, setback, coverage, height and
parking come from the **Master Plan / Zonal Regulations**, not the bye-laws — which is
why `setbacks_v1.json` remains entirely unverified and is a separate question.

---

## Corrections to things I got wrong

**The `claude_code` adapter is not misclassifying.** I reported twice that it treats
schema failures as transport failures and skips both retries, and that stage ③ would
inherit the hole. Reading the SDK: `ResultMessage.api_error_status` is documented as
carrying the HTTP status *"when `is_error` is True and `subtype` is 'success'"* — so
`error result: success` is the CLI's shape for a **failed API call** (429/500/529).
`ProviderUnavailable` is correct, and skipping schema retries is right: no number of
them fixes a rate limit. Only the wording was wrong, and is now translated. **The live
failures in this session were subscription rate limits.**

---

## Design decisions and their reasons

**`road_edges` is stored; `facing` and `corner_plot` are computed.** A plot can front
two roads and *every* road edge takes the larger setback. Collapsed to one direction, a
corner plot's second road is silently un-set-back — a wrong envelope, not a wrong
label. Visible cost: same site, one extra road, envelope drops 55.0 → 39.6 m².

**`base_far`, never `total_far`.** The excess requires buying TDR. A plot owner
building one house has not, and budgeting against it sizes a programme that cannot
legally be built.

**`city` is `blocking` in the clarifier.** It was `defaultable` while nothing consumed
it; stage ② cannot select a ruleset without one.

**Rules are data with citations.** Every band names its source and carries
`verified: false` until checked; the loader warns while any remain, and
`build_envelope` refuses outright. A wrong setback looks exactly like a right one.

**`SpaceKind` is a superset of `RoomKind`, enforced by a test.** `RoomKind` is what a
person types; hall, kitchen and corridors are implied or never mentioned. A `RoomKind`
without a counterpart would be dropped in expansion — the user asks for a pooja room
and does not get one.

**Two areas per room.** `min_area_sq_m` is the legal floor, `target_area_sq_m` what it
should be. One number produces either cramped plans or infeasible ones; the gap is the
solver's slack. `min_width_m` *and* `max_aspect` both exist because area alone permits
a 1 m × 9 m bedroom.

**No coordinates in `ir/plan.py`, asserted by a test.** Decision 1 — the LLM emits a
graph, the solver emits geometry. An `x` on a `RoomSpec` is that architecture leaking.

**Bedrooms open off the corridor, never the hall.** That is what circulation is for,
and it is why privacy survives the tiling.

**Kitchen and every bathroom carry a hard `SEPARATED` edge.** A toilet sharing a
kitchen wall is the one placement every Indian client objects to, Vastu or not.

---

## Sources for the rule data

- BBMP Building Bye-laws 2003 §9, Table 6 — https://indiankanoon.org/doc/31235699/
- Bangalore setback tables (secondary) — https://infralens.in/dcr/setbacks/bangalore
- RMP 2031 Vol. 6 Zoning Regulations, Tables 6/7 and §5.2(iii)(iv), read first-hand —
  https://data-opencity.sgp1.cdn.digitaloceanspaces.com/Documents/Recent/Bengaluru-BDA-RMP-2031-Volume_6_Zoning_Regulations.pdf
- NBC 2016 Part 3 minimum room sizes — **not read first-hand**, transcribed from
  practice knowledge and flagged `verified: false` throughout `spaces_v1.json`.

---

## Frontend

**Konva, not react-planner.** [react-planner](https://github.com/cvdlab/react-planner)
is the tempting match — an actual React floor-plan editor, right domain. It is the
wrong shape. It owns its own scene model and expects the user to *draw*, while
naksha's editor is for **adjustment, not authoring** (CLAUDE.md scope) and decision 3
says a user edit becomes a `Constraint`, never a coordinate. Adopting it means a
two-way mapping against `Layout` or abandoning `Layout` as the source of truth —
precisely the abstraction tax that kept LangChain out. It also showed 77 open issues
and no surfaced release; react-konva declares React 19 support and 0 open issues.

**Konva over plain SVG is a bet on the next step, not this one.** At ~36 shapes per
floor, SVG in React would give hit-testing free from the browser and add no
dependency. Konva earns its keep when stage ⑥ adds walls, doors, fixtures and
dimension lines — several hundred shapes — and when wall-dragging needs `Transformer`
handles and snapping. Worth revisiting if that step changes shape.

**Three exports, one source, rendered independently.** `Layout` → SVG (screen,
gallery) · PDF (sharing) · DXF (the registered architect who must stamp the sanction
drawings). **Never SVG → DXF:** a wall in SVG is a stroke with a width; in DXF it is
geometry on a `WALLS` layer with a centreline, and doors are blocks with swing arcs.
Converting means reconstructing semantics that were deliberately discarded one step
earlier.

**`PlanBundle` is the backend/frontend contract**, a model rather than a dict because
that edge is exactly where CLAUDE.md's "no bare dicts crossing a module edge" applies.
It carries `seed` because a plan you cannot reproduce is one two people cannot discuss.
