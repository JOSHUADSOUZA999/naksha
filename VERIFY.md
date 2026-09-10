# Rule verification sheet

**For: an architect or engineer who has sanctioned a building plan in Bengaluru recently.**

naksha generates schematic floor plans from a plot description. Every dimension it
produces is computed from the figures below, and **not one of them has been checked
against a primary document** — they are transcribed from secondary sources and, in one
case, from a draft that may never have been notified. The software refuses to emit a
plan without an explicit override until they are confirmed.

You do not need to know anything about the software. Each question is standalone.
Ticking "as written" is a valid and useful answer.

Time: about 20 minutes. Questions are ordered by what they change.

---

## Q1 — Does a car porch count inside the buildable area?  ⚠ HIGHEST IMPACT

The 2017 bye-laws, cl. 5.1.8.2(a), set a private garage at **3.0 m × 6.0 m minimum**
— 18 m². On a 30x40 ft site the buildable footprint after setbacks is 55 m², so that
bay alone is a third of the ground floor.

In practice, is a covered car porch on a plot this size:

- [ ] built **inside** the setback lines, counting against buildable area — as we assume
- [ ] built **within the front setback** as a porch/stilt, not counting against it
- [ ] permitted either way, at the owner's choice
- [ ] something else: ______________________

**Does it count towards FAR?**  [ ] yes  [ ] no  [ ] only if enclosed on 3+ sides

**Does cl. 5.1.8.2's 3.0 × 6.0 m apply to an open porch**, or only to an enclosed
garage? ____________________________________

**Since this sheet was written we measured the strip itself, and it changes the
question.** A statutory bay is 3.0 × 6.0 m. The front setback on every plot naksha
models is shallower than 3.0 m:

| plot | road | front setback | bay fits? |
|---|---|---|---|
| 30x40 | east | 1.46 m | no |
| 30x50 | north | 1.83 m | no |
| 40x60 | north | 2.19 m | no |
| 50x80 | north | **2.93 m** | no — by seven centimetres |

So "built within the front setback" may be permitted and still be impossible: on plots
this size there is no setback deep enough to stand a car in. If the practice is real,
one of these is wrong — the setback figures (unverified, `setbacks_v1.json`), the
3.0 × 6.0 bay (cl. 5.1.8.2(a), verified), or our assumption that the porch is a
separate structure rather than **stilt parking under the house**.

**Which is it?**

- [ ] the setback figures are wrong — the real front setback on a 30x40 is ______ m
- [ ] a porch may project *over* the setback line, or the plot line
- [ ] an open standing space is permitted at less than 3.0 × 6.0 — the real figure is
      ______ × ______ m
- [ ] **it is stilt parking**, under the house, and a 30x40 3BHK is G+1 by necessity
- [ ] something else: ______________________

*What it changes:* moving the bay off the ground floor takes a 30x40 3BHK from
73.6 m² of statutory minimums against a 74.9 m² envelope — infeasible on every seed
tried — to 55.6 m², which solves. It is the single change that decides whether naksha
serves the commonest plot in the market. `--porch-in-setback` implements it and
currently refuses on all four plots, with the numbers, rather than drawing a house with
the car nowhere.

*Measured, using the statutory 18 m² bay:*

| ground floor | packed | legal layouts |
|---|---|---|
| bay inside the envelope | 95% | **0 / 60** |
| porch in the front setback | 62% | **15 / 60** |

**With the bay inside, no legal 3BHK exists on a 30x40 at all.** This one answer
decides whether naksha can serve the commonest site size in Bengaluru.

## Q2 — Which FAR applies?  ✅ ANSWERED

Read first-hand from **RMP-2015 Zoning Regulations (BDA, 2007), Table 10 — FAR and
Ground Coverage in Residential (Main)**:

| Plot size (m²) | Coverage (max) | FAR | Road width |
|---|---|---|---|
| **Up to 360** | **75%** | **1.75** | up to 12.0 m |
| 360–1000 | 65% | 2.25 | 12–18 m |
| 1000–2000 | 60% | 2.50 | 18–24 m |
| 2000–4000 | 55% | 3.00 | 24–30 m |

So a 30x40 (111.5 m²) gets **FAR 1.75, coverage 75%**. That confirms the widely
reported figure and rules out both 0.75 (Bye-laws 2003 Table 6) and 1.50 (RMP 2031,
which is a *draft*). Now encoded.

- [ ] correct  [ ] superseded by: ____________________________________

Also from Table 10 note (b), verbatim: *"If the road width is less than 9.0 m, then the
maximum height is restricted to 11.5 meters or Stilt +GF+2 floors (whichever is less)
irrespective of the FAR permissible."*  [ ] correct  [ ] no: ____________

---

## Q3 — Setbacks  ⚠ TWO THINGS TO CONFIRM

Read first-hand from **RMP-2015 Zoning Regulations, Table 8** (height up to 11.5 m,
plot up to 4000 m²):

| Width/Depth of site | Right | Left | Front | Rear |
|---|---|---|---|---|
| up to 6.0 m | 1.0 m | 0 | 1.0 m | 0 |
| 6.0–9.0 m | 1.0 m on all sides | | | |
| **above 9.0 m** | **8%** | **8%** | **12%** | **8%** |

**(a) Which dimension does each percentage apply to?**  ✅ ANSWERED

The draft 2025 amendment restates the row explicitly — *"12% of the depth of site"*
(front), *"8% of the depth of site"* (rear), *"8% of the width of site"* (left and
right). Our reading was right. A 30x40 (9.14 × 12.19 m) therefore gets front 1.46 m,
rear 0.98 m, sides 0.73 m each.

**(b) Is the 2025/2026 amendment in force?**  ⚠ THE OPEN QUESTION

We read notification **No. UDD 235 MNJ 2025(E), dated 11.11.2025** first-hand. It is
headed **"DRAFT REGULATIONS"**: the Government *"proposes to make"* the amendments,
invites objections within thirty days, and says *"They shall come into force from the
date of their final publication in the official Gazette."* Secondary sources report a
**final notification dated 05.01.2026**, which we have not seen.

If final, it substitutes Table 8 entirely and bands by **site area** rather than site
dimension:

| Site area (m²) | Front | Rear | Sides |
|---|---|---|---|
| up to 60 | 0.75 | – | 0.60 on any one side |
| **60–150** | **0.90** | **0.70** | **0.70 on any one side** |
| 150–4000 | 12% of depth | 8% of depth | 8% of width, both |

**A 30x40 is 111.5 m² and moves into the middle row** — a large relaxation for exactly
the plot size that matters most.

- [ ] the amendment is final and in force — use the table above
- [ ] still draft / rejected / modified: ____________________________________
- [ ] final notification number and date: ____________________________________

Also in that draft, please confirm which apply:
- [ ] plots up to 150 m² capped at 12.0 m excluding stilt (note 1)
- [ ] open staircase allowed **in the setback area** up to 750 m² (note 3)
- [ ] coverage in Tables 10 & 11 **not referred** for Table 8 plots (note 4)

## Q4 — Does road width restrict height as we think?

We cap storeys by the abutting road, irrespective of FAR earned:

| Road width | Max floors |
|---|---|
| under 9.5 m | ground + 1 |
| 9.5 – 12.5 m | ground + 2 (sites up to 240 m²) |
| over 12.5 m | higher |

- [ ] correct  [ ] corrections: ____________________________________

We also demote the FAR band when the road is narrower than the band expects
(RMP 2031 §5.2 iii). Is that applied in practice? [ ] yes  [ ] no

---

## Q5 — Minimum room sizes  ✅ MOSTLY ANSWERED

**Five figures are now confirmed** against the *Karnataka Municipalities Model Building
Bye-Laws 2017* (notification UDD 14 TTP 2017 (P-4), 28-10-2017), downloaded from BBMP's
own BPAS portal and read first-hand:

| Room | Figure | Clause |
|---|---|---|
| Habitable room, single/first | 9.5 m², min width 2.4 m | 5.1.2.3 |
| Habitable room, second | 7.5 m², min width **2.1 m** | 5.1.2.3 |
| Kitchen (separate dining) | 5.0 m², min width 1.8 m | 5.1.3.2 |
| Bathroom | 1.8 m², min width 1.2 m | 5.1.4.2 |
| Water closet | 1.1 m², min width 0.9 m | 5.1.4.2 |

Four matched what we had exactly. The second-bedroom width was wrong — we had 2.4 m,
the bye-laws say 2.1 m, so we were **stricter than the law** and rejecting legal plans.

**Still unverified, transcribed from practice, please correct:**

| Room | Min area | Min width |
|---|---|---|
| Living / hall | 11.0 m² | 3.0 m |
| Dining (separate) | 7.5 m² | 2.4 m |
| Passage / corridor | 2.0 m² | 0.9 m |
| Staircase | 5.0 m² | 1.0 m |
| Store, utility, pooja, balcony, veranda | see `spaces_v1.json` | |

Corrections: ____________________________________

**One question the code cannot answer:** is a **separate dining room** expected on a
30x40, or normally combined with the living room? The bye-laws allow a kitchen that
doubles as dining at 7.5 m² (cl. 5.1.3.2), which suggests the combined arrangement is
normal. We currently give every 3BHK a separate 7.5 m² dining, and it is often the
room that makes a plan infeasible. ____________________________________

**Also worth confirming:** cl. 1(3) of the 2017 bye-laws says FAR, setback, coverage
and height are governed by the **Master Plan / Zonal Regulations**, not by the
bye-laws. That is why Q2 and Q3 are separate questions — and why the answer to them
lives in the RMP, not here. Correct? [ ] yes [ ] no: ______________

## Q6 — One sanity check

For a **30x40 ft east-facing plot in Whitefield, 3BHK with a pooja room and covered
parking**, roughly what would you expect?

- Ground + how many floors? ____________
- Buildable footprint per floor? ____________ m²
- Total built area? ____________ m²

*We currently compute: 7.69 x 7.14 m footprint (55 m²), 167 m² total, G+1 required.*
If your numbers differ materially, that is the most useful single thing on this sheet.

---

## Q6 — How much window does a habitable room need?

naksha now sizes every window from the room's floor area rather than drawing a fixed
opening, because the code regulates area and not width. The figure it uses is
**one tenth of the floor area**, aggregate, per habitable room — transcribed from
practice knowledge and **not read first-hand**, which is why no clause number appears
beside it in `refine_v1.json`.

For a plan sanctioned in Bengaluru, is the operative figure:

- [ ] **1/10 of floor area** — as we assume
- [ ] 1/8
- [ ] 1/6
- [ ] a ventilation figure separate from the daylight one: ______________________

**Does it differ for a kitchen?**  [ ] no  [ ] yes: ______________________

**Is it measured on the openable area or the whole opening?**
[ ] openable only  [ ] whole opening including fixed lights

**Does a bathroom or WC have its own figure**, or is a mechanical vent accepted
instead? ____________________________________

*What it changes:* the window on a 19 m² hall is currently 0.93 m wide at 1.2 m high.
At 1/6 it would be 1.6 m, which changes which walls can carry it and therefore which
plans are drawable at all.


## What happens with the answers

Each becomes a line in `backend/app/rules/setbacks_v1.json` or `spaces_v1.json` with
`verified: true` and your name against it. They are **data, not code** — correcting a
figure is a one-line edit, no rebuild. Every plan records which revision produced it.

naksha produces **schematic drawings only**, not sanction drawings; a registered
architect's stamp is required for approval regardless. This sheet is about not wasting
that architect's time with a concept plan built on wrong numbers.
