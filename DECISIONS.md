# DECISIONS — naksha

Append-only. Why the code is shaped the way it is, what broke, and what is still open.
`CLAUDE.md` is how to work here; `STATUS.md` is where we are; this is how we got here.

---

## Open questions — these need you, not me

**9. A stilt level cannot get both its labels and its staircase right, and the reason
is structural.**

`--stilt` unblocks the 30x40 3BHK — three levels, zero unbuildable, zero errors. The
drawing of its ground level is still wrong in one place: the staircase is 40.1 m² and
the open ground beside it is 5.2, when those two figures belong the other way round.

**Three fixes were tried and each traded the defect for a worse one.**

*Give the stilt a large minimum so the tiling cannot raid it.* The tiling handed the
large rectangle to the car bay instead and the stilt fell below a floor it had no
business having — one room unbuildable.

*Fix the allocator so surplus follows headroom rather than proportion.* This was a real
bug and the fix was kept: rooms with no ceiling no longer grow, which is why the car bay
sits at 20 m² and not 46.9. It did not move the staircase, because the staircase's
rectangle is not chosen by the allocator.

*Give the stilt a minimum of 60% of the open ground.* This labels the level correctly —
stilt 42.7, staircase 6.2 — and breaks the shaft: `stair3 does not land on the staircase
on the floor below`. A house you cannot get upstairs in is worse than a mislabelled
room, so this was reverted.

**The conflict is real and it is not a weighting problem.** On a stilt the staircase
must be *small* (it is a stair, not a hall) and *positioned under the stair above* (it
is one shaft). The slicing tree can satisfy either: a large stair rectangle overlaps the
upper stair easily, and a small one rarely lands beneath it. What it has no mechanism
for is placing a rectangle of a given size at a given position, which is what a shaft
actually requires. The big staircase is the solver correctly preferring a 40-point
oversize penalty to a 90-point broken shaft.

This is the same wall as question 6 from a third direction: the representation cannot
express "this room, here".

**I said Stage B could, and that pinning shafts there would be a bounded change. It is
not, and the attempt is worth recording.** A leaf's extent is already a pair of
expressions in the CP-SAT model, so requiring one to cover a core rectangle is four
linear constraints — the mechanism is as easy as it looked. What it costs is the
problem.

*Pinned to the floor below's shaft*, the position is an accident of one storey's tiling
that every floor above inherits. Topologies that cannot reach it tune infeasible, fall
back to the un-tuned Stage A path, and come out at **0% overlap** — worse than the
75%-of-the-smaller rule it replaced.

*Pinned to one core in the zone every storey shares*, chosen once, it works: the shaft
genuinely stacks at 90% and 91% on a 30x40 stilt, and the oversized staircase halves
from 42.7 m² to 21.3. But a hard constraint is a hard constraint. **A 30x30 that had
been clean came back with a room below its minimum**, because the topologies that
satisfy the pin are not the topologies that dimension well, and when none does the
fallback is unpinned.

So the cost is not paid by the plan being fixed; it is paid by other plans. Both
attempts are reverted. What this needs is for the pin to be a *preference Stage B can
trade* rather than a constraint it must meet — an objective term, which is a change to
what Stage B optimises rather than to what it is allowed to return.


**8. ~~Every plan we call legal has rooms below their statutory minimums.~~ Fixed —
and it did not need the decision I asked for.**

Stage ⑤ checked `min_area_sq_m` and `min_width_m` against rectangles that run to **wall
centrelines**, while the bye-laws mean **clear internal** size. Every legality check was
optimistic by half a wall on each side, systematically. Three of the four reference
briefs reported a clean plan and carried six or seven rooms below a minimum.

I wrote this up as a product decision on the grounds that tightening the minimums might
make a 30x40 3BHK infeasible outright. **That was speculation, and this file's own rule
is that options are measured, not guessed.** Measured:

| brief | before | after | clean solves, 6 seeds |
|---|---|---|---|
| 30x40 3BHK | 3 unbuildable, 16 breaches | 8 unbuildable, 8 breaches | 0 / 6 |
| 30x50 3BHK | 0 unbuildable, **7 breaches** | 0, **0** | 1 / 6 |
| 40x60 3BHK | 0 unbuildable, **6 breaches** | 0, **0** | 6 / 6 |
| 50x80 4BHK | 0 unbuildable, **7 breaches** | 0, **0** | 6 / 6 |

There was no trade to make. Two of the three plots that mattered were always legally
buildable — the solver simply did not know what it was solving for. `score` now measures
each side inside its own wall (exterior on the boundary, partition elsewhere, the same
rule `refine._clear_rect` uses) and Stage B carries a conservative allowance because it
cannot know which sides land on the boundary until the tree is placed.

**Only the 30x40 got worse, and honestly so:** it now reports eight rooms it cannot
build rather than three. The plot was never solvable; the old number was just a smaller
lie. That returns the question to where it belongs — the car porch, VERIFY.md Q1.

**Two things fell out of it.**

*The 30x50 is marginal, not comfortable.* It solves cleanly on 1 seed in 6. That was
invisible while the check was wrong.

*The feasibility verdict was compounding a probability it no longer had.* "5 in 60, and
⑤ tries 24, so 88%" needs those 24 to be independent draws, and they stopped being draws
when the shortlist became ranked. Stage ④ now runs the same ranked shortlist stage ⑤
does and counts how many of *those* come out legal. The counts separate the briefs
where the compounded rate did not — 0 / 0 / 4 / 1 against clean solves of 0 / 1 / 6 / 6.
The old formula called the 50x80 undependable at 71% while it laid out cleanly every
time.

*And the probe and the solver had drifted apart twice* — first tuning unranked trees,
then ranking without the adjacency graph or the road interleave. `solver.shortlist_for`
is now the single function both call, which is the only thing that keeps them honest
about each other.

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

### The car bay off the road was a search problem, not a weight

Three plots were refused because the car bay touched no road. ⑤ already penalised that,
and the penalty lost, because there was almost nothing better to pick: whether a room
touches the plot edge is decided by the slicing tree's *shape*, CP-SAT only sizes it,
and hardly any random tree put the bay on the road and still dimensioned legally.
`slicing.road_first_tree` builds that shape on purpose — bay and foyer as a strip along
the road, a random tree for the house behind — and 300 of them are **added** to the
random pool, whose indices are untouched. Biasing the random pool has failed twice here:
it raised the average and lowered the best.

Road-first alone made the 25x40 worse by ⑦'s count, because the lowest penalty was no
longer the plan ⑦ liked. So ⑦ now chooses: `plan(judge=…)` keeps the 12 best finished
candidates per floor and takes the minimum of `validator.judge` — errors, then errors
that leave rooms unreachable, then unbuildable rooms, warnings, penalty. The solver
still imports neither ⑥ nor ⑦; the judge is a function passed in. Unreachable rooms rank
above other errors because on the 30x50 the tie between two refused plans went to the
lower penalty, and that was the one with no front door.

### A house with no front door came back clean

⑦'s walk starts where you arrive: the front door on the ground floor, the staircase
upstairs. The staircase fallback fired on *any* storey with no entrance, so a ground
floor where ⑥ found no road-facing wall for a door was walked from its stair, reached
every room, and reported nothing. That did no harm while ⑦ only reported. It did harm as
soon as ⑦ started picking plans: the judge prefers fewer findings, so a sealed house beat
one you could enter. **The 25x40 in STATUS as the one clean plot had no entrance** —
replayed under the old rule it has 0 entrance openings; with the fix the judge picks one
that has a front door and one warning.

*Lesson: a check people only read can be wrong without anyone acting on it. Once a check
ranks candidates it is an objective, and the optimiser finds its holes. ⑤'s penalty
failed the same way. Every signal the judge reads needs a test that breaks a plan on
purpose, including plans it would call clean.*

### The hill-climb traded the front door for points

On the two plots still refused for their car bay, the 30x40 2BHK and the 30x50, the one
road-first topology that dimensioned was a plan ⑦ passed *before* hill-climbing — no
errors, two warnings — and refused after it. A swap moved the foyer off the street and
the penalty fell 400 → 285 and 410 → 325: sector and adjacency wins outweighed the
`INACCESSIBLE` weight. `improve` now ranks `(unbuildable, off_the_road, penalty)`. Road
access sits after unbuildable and never before it — ahead of it, the shortlist once
traded legal rooms for it.

*Lesson: enough small wins beat any weight eventually, which CLAUDE.md already says about
unbuildable rooms. A defect ⑦ refuses belongs in the key, not in the sum.*

### A house entered through a bathroom scored one warning

With the climb fixed, the 30x50 came out "legal": front door → foyer → bath2 → corridor →
hall, with two bedrooms opening off the dining room. ⑦ counted it as one warning of two,
so the judge ranked it above a plan refused for its car bay. A hall, kitchen or dining
room reachable only through a bedroom or bathroom is now an **error**, because the way in
does not lead into the house. A bedroom reached through another bedroom stays a warning:
bad, and livable.

Looking at the plans that passed next found one more hole. Both newly legal plots were
entered foyer → kitchen → hall and ⑦ said nothing, because its through-the-kitchen list
named bedrooms, baths, stairs and corridors but not the hall. Now it names the hall (a
warning). A dining room behind the kitchen is ordinary and stays unreported.

*Lesson, again: once ⑦ chooses the plan, every severity is a weight — and each plan the
judge newly accepts is where the next hole is.*

### Refusing a tree costs under a millisecond, and the search stopped at the shortlist

Why the two plots stayed refused after the climb fix: `solve` tunes only its shortlist,
and on these floors two or three topologies in it dimensioned. Tuning 6000 trees per
group instead, seed 12345:

| plot | random trees that dimension | road-first trees that dimension | of those, passed by ⑦ outright |
|---|---|---|---|
| 30x40 2BHK | 11 | 60 (1 in 100) | 21 |
| 30x50 | 5 | 22 (1 in 270) | 14 |

at about 0.9 ms a tree, the ones that dimension included. So `solve` now carries on past
the shortlist with `deeper` — up to 2000 road-first trees on their own seeded stream — but
only when the shortlist dimensioned fewer layouts than it wants. Roomy plots never reach
it (the 50x80 stayed at 2.7 s end to end); the 30x50 went from 1.2 s to 3.0 s, and both
plots came out legal.

On the two hopeless plots it bought nothing for 0.9–1.5 s a solve. `tuning.cannot_fit`
now skips a floor whose rooms' gross minimums exceed the footprint. The skip is exact:
tiling makes the leaf areas sum to the footprint, and the model gives every leaf at least
`gross_minimum_cm2`, which the bound and the model share. The reference floors separate
cleanly — 166% and 101% for the hopeless two, 89–91% for the thin floors that solve,
41–58% for the roomy ones.

Stage ④'s probe walks the same generator when the shortlist holds no legal plan, since
the probe and the solver have drifted apart twice. It mattered at once: on the 30x40
3BHK, ④ now reports that folding the dining area into the hall gives a legal layout past
the shortlist — an option the shortlist-only probe would have called "still no legal
layout".

### The same seed can give a different plan on a busy machine

Stage B stops each CP-SAT solve at 0.15 s. On an idle machine the result replays — three
sequential runs of the 40x60 produced identical layouts — but with nine plots measured at
once the 40x60 came back as a different plan, because a solve cut short by load keeps a
different incumbent. **Not fixed.** CP-SAT's deterministic time limit
(`max_deterministic_time`) is the likely answer and is untested. Until then, measure one
plot at a time.

### The model path had never run end to end, and every plan it drew had no front door

Stage ③'s model version was built, and never exercised offline. It first ran end to end
on 2026-09-13 through the `claude_code` provider: three briefs, three refused plans, the
same defect each time — no front door. The model was not the cause.

`_merge` built each `RoomSpec` by hand and copied `needs_exterior_wall` from the rules,
but not `needs_road_access`, `needs_door`, `is_through_route` or `max_target_sq_m`. Every
foyer and car bay was indifferent to the street, so the solver never put the foyer on it
and ⑥ found no road-facing wall for a door; every hall and corridor was a private room,
so ⑦ reported corridors as rooms you walk through a bedroom to reach. Rooms are now built
by `program.spec_for`, the offline expansion's own function, keeping only what is the
model's to decide: id, kind, floor and sector.

Two more on the same path. `--stilt` and `--porch-in-setback` reached the offline
expansion and nowhere else, so the model path dropped them without a word; both paths now
apply them through `program.apply_site_choices`, outside ③'s retry loop so a porch the
setback cannot hold is refused rather than sent back to the model. And the CLI called ③
once for `-P` and again for `-L`: a 30x50 was printed with `mbed` and a WC and drawn with
`master` and none, at twice the cost.

The model's answers are recorded in `tests/golden/program_drafts.json` and replayed
through a scripted provider — how the fix was measured without spending the subscription.
All four plans (the 30x40 as G+1 and on a stilt, the 30x50, the joint-family 40x60) come
out with no errors and no warnings on any floor. Then live again, once: the 30x40 3BHK with
`--stilt` took 86 s end to end against 153 s, drew all three storeys, printed the
programme it drew, and came out with no findings on any floor.

*Lesson: a second front end to a stage needs the same construction function, not a copy
of some of its arguments. And a path that only runs with credentials needs a recorded
replay, or nothing offline ever exercises it.*

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
