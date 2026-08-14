# Analysis — 2026-08-11 behavior scenarios, real run

Companion to `RESULTS.md` (per-scenario hard/soft, tokens, cross-cutting tallies).
This file is the "what does it mean" pass — read `RESULTS.md` first for the raw
numbers. n=1 per scenario throughout; every claim below is scoped to this one run,
not generalized beyond it. `gemini-3.6-flash`, `temperature: 1.0`, paid tier,
2026-08-13.

---

## What the pipeline detected, in one paragraph

Every detection floor case passed: the numeric contradiction (S4), the three-way
conflict (S5, at the full bar — one conflict, all three ids, not two pairwise), the
planted 3-cycle (S6, verified programmatically via `find_cycles()`), and — the
genuinely uncertain one — the latent, non-numeric conflict in S3 (patient
sitting-because-busy). Both controls (S2, S8) produced zero false positives. The
dependency mapper found both known links in the 8-requirement dilution document (S7)
with zero extras, a materially better precision result than the ground truth's
"lower bound, watch for 15+ link explosions" framing anticipated. Two unplanted 2-node
dependency cycles turned up on their own (S3, S5), and both correctly routed to
`CIRCULAR_DEPENDENCY` on exactly the requirements actually in the cycle — not on
requirements merely adjacent to it (S5's `PURE-THEMAS-R4-P2` was excluded correctly).
Across the whole suite: zero schema-validation failures, zero transport failures, zero
wrong-requirement-id mismatches, in 263 attempts.

## What it missed, and what the miss means

`LO = T_LT` (S9) was never flagged, under any category, in any round — Known
Limitation 5 stands exactly as documented; see its own section below. `infeasible_for_type`
never fired anywhere in the suite, including on the run's one AI-flavored
classification (S12) — not evidence the category is broken, just evidence it needs a
harder-to-classify requirement than any fixture in this suite provided to observe it at
all. And two requirements (`LUITEL-R7`, `THEMAS-REQ-B`) reached `RunOutcome.COMPLETED`
through a mechanism that should worry a reader more than a category miss would: the
Rewriter made no edit, and the Quality Checker, re-run on byte-identical text one round
later, silently reversed its own verdict — not just on the issue the human had
confirmed resolved, but on a second issue nobody had touched. This is Known Limitation
10, now observed 3 times total (once in the 2026-08-10 run, twice here), and it is the
single finding in this run most likely to matter for anyone citing this pipeline's
`COMPLETED` rate as a quality signal: `COMPLETED` does not mean "the Quality Checker
verified this text is clean" as reliably as the state's name suggests.

---

## S5 — one three-way conflict, or two pairwise?

**One three-way conflict.** `ConsistencyChecker` returned a single
`ConsistencyConflict` naming all three ids (`PURE-THEMAS-R4`, `-P1`, `-P2`), and the
explanation reasons about the three-way interaction directly: *"PURE-THEMAS-R4
mandates a maximum limit on active units, PURE-THEMAS-R4-P1 sets this maximum to three
units, and PURE-THEMAS-R4-P2 requires turning on four heating units simultaneously
during a system-wide cold start. These requirements jointly contradict each other
because activating four units simultaneously exceeds the specified maximum limit of
three."* This is not a restatement of two separate pairwise findings stitched together
— it explicitly invokes the three-way arithmetic (cap of 3 vs a cold start of 4) that
no pairwise comparison could produce alone (R4-P1 alone is satisfiable, R4-P2 alone is
satisfiable, R4 alone is satisfiable; only the conjunction of all three fails). This is
a genuine, positive finding about whole-document consistency checking: on this fixture,
at n=1, it is not secretly reasoning pairwise and merging results — it is reasoning
about the whole set at once, exactly as `ConsistencyConflict.requirement_ids` was
designed (2026-08-11 design note) to allow. The caveat is unavoidable at n=1: one hit
does not establish this generalizes to larger or subtler multi-way conflicts, and
`EVALUATION_DATASETS.md`'s planned scale experiment is the actual test of that.

---

## S9 — was "LO = T_LT" flagged?

**No.** Across `THEMAS-REQ-E`'s three rounds, every issue raised was `VAGUE_PRONOUN`
("this condition," "this module"). `LO = T_LT` — undefined domain notation — was never
named, in any round, under any category (not even folded into a broader complaint about
the requirement's clarity). Known Limitation 5 is confirmed exactly as `CLAUDE.md`
documents it, and **no update to `DESIGN_NOTES.md` is warranted** — the note's own
caveat only calls for revision if the notation *is* caught, which it was not. Worth
restating why this makes sense given the pipeline's structure, since it's easy to read
a miss as "the model just isn't good enough": the Quality Checker is never shown a
glossary or any other part of the document beyond the one requirement string. `LO =
T_LT` is not distinguishable, from the Quality Checker's vantage point, from a term
that's defined three paragraphs earlier in a real SRS — the miss is a genuine input
limitation (the checker cannot know what it hasn't been shown), not a detection-quality
gap that a better prompt or model would close. That's exactly the 2026-08-11 reframing
`CLAUDE.md`'s Known Limitation 5 entry already states; this run gives it its first real
citation.

---

## S10 — LUITEL-R7's refined text, read by hand

**The text was never rewritten.** `refined_text` after round 0 is byte-identical to
the original: *"The system shall generate reports on inventory levels, product
movement, and sales history."* The Quality Checker correctly flagged `NON_ATOMIC` on
this span in round 0 (a genuine multi-behavior case — three independently testable
report types, not a causal chain), the answer policy confirmed it resolved ("keep this
as one requirement... splitting would fragment one atomic step" — the policy's actual
reasoning for `NON_ATOMIC` is to *not* split, which is itself a defensible but
consequential choice baked into the shared policy, worth flagging as a threat to
validity: a different, splitting-favorable policy might have produced a genuinely
different requirement text here), and the Rewriter then made zero changes to the text
it was told was resolved.

The schema cannot see this — `refined_text` is a valid string whether or not it
represents one behavior or three, and this scenario exists specifically because nothing
else in the project checks it. What *is* worth reporting, because it wasn't anticipated
in the ground truth: the **Test Generator partially compensated** for the requirement
staying un-split. It produced four test cases — three via `equivalence_partitioning`,
one per report type (`TC-9-LUITEL-R7-1` inventory, `-2` product movement, `-3` sales
history), plus one `use_case` end-to-end case. So at the *test* level, the three
behaviors are in fact tested as three separable partitions. But every one of those four
cases carries `requirement_ids: ["LUITEL-R7"]` — a single id, because there is only one
id to give them. If the inventory-levels report and the sales-history report have
independently different acceptance criteria or independently regress, nothing in the
record can distinguish "the inventory partition failed" from "the sales-history
partition failed" by filtering on requirement id — the atomicity problem the schema
cannot see at the requirement level reappears, in a milder form, as a traceability gap
at the test-case level. The Test Generator effectively recovered the granularity the
Rewriter didn't, but only inside `title`/`technique_used` text, not in any
machine-checkable field.

---

## S12 — did ACTAPP-R2-AC1 classify as AI_SYSTEM or MOBILE?

**`AI_SYSTEM`.** Rationale: *"Identifying complex user activities such as driving from
sensor data is a human activity recognition classification task where the system
behavior relies on a machine-learning model to make accurate predictions."* This is the
**first non-`other` classification in any real run of this pipeline** — every prior
real run (2026-08-10, and every other requirement in this suite) classified `other`.
Per the ground truth, both `AI_SYSTEM` and `MOBILE` are defensible answers here (the
app is mobile, the behavior is ML), and `AI_SYSTEM` is **not treated as a failure
either way** — it is recorded as evidence that the per-requirement classifier *can*
break out of the `other` default when the requirement text gives it a strong enough
signal ("accurately identifies," an implicit classification task), which is itself a
partial answer to whether Known Limitation 9's classifier stage is "buying nothing":
on this one requirement, it clearly did something non-trivial.

What this run cannot answer: which *technique* would follow from that classification.
`ACTAPP-R2-AC1` hit `RunOutcome.CAP_STOPPED` (on `ambiguous_term`/`incomplete`, then
`non_verifiable` after the Rewriter inserted a literal `[measurable accuracy threshold
TBD]` placeholder) before ever reaching Strategy Selection, so whether it would have
picked `metamorphic`, `statistical_threshold`, or `adversarial` — the actual Layer 2
question S12 was built to probe for this requirement — is unanswered. Classification
happens early in the pipeline (independent of the refine loop resolving), which is why
this half of the question has an answer at all; technique routing happens only after a
requirement passes, which is why the other half doesn't.

---

## S1 — does the dependency link change PURE-ERTMS-R8's test cases?

**Not observable for R8 directly — R8 never reached test generation, in any of the
three real runs it appeared in (S1, S13, S7).** In every case, `PURE-ERTMS-R8` ("The
DMI shall indicate the result of the self-test") was flagged `INCOMPLETE` round after
round, the shared answer policy's `INCOMPLETE` response declines to invent the missing
trigger/condition, and the revision cap fires at round 3 with `test_strategy`/
`test_plan` both `None`. This is not sampling noise: the same text, the same policy,
three different document contexts (2 requirements twice, 8 requirements once), three
identical outcomes. **Report this plainly as the useful result it is**: under this
answer policy, this specific requirement text is currently unable to reach test
generation at all, which means Known Limitations 1, 6, and 7 — every one of which
depends on seeing what `dependencies_for(R8)` actually does to R8's generated tests —
remain unanswerable from R8's side with this policy, this fixture, and this model,
regardless of how many more times it's run.

What *is* observable, because `dependencies_for` matches either side of a
`DependencyLink` (not just the "from" side item 16's language centers on), is the
effect on `PURE-ERTMS-R7` — the "to" side of the same link — and it replicated
identically across both S1 and S7:

- **(a) Does any case list both R8 and R7 in `requirement_ids`?** Yes, in both runs.
  S1: `TC-13-PURE-ERTMS-R7-2`, `requirement_ids: ["PURE-ERTMS-R7", "PURE-ERTMS-R8"]`. S7:
  `TC-13-PURE-ERTMS-R7-2`, same pairing.
- **(b) Does any precondition or step mention the self-test or R7 without citing it?**
  Yes — both runs' `TC-...-R7-2` describe an end-to-end "power on → self-test executes
  → result becomes available for DMI display" sequence in the steps/expected_result
  text, referencing the downstream DMI-display behavior (R8's actual content)
  conceptually without citing `PURE-ERTMS-R8`'s id inside that text (the id citation
  only happens in the structured `requirement_ids` field, answered in (a)).
- **(c) Would these cases look different if `relevant_dependencies` had been empty?**
  Very likely yes. In both runs, the Strategy Selector's `rationale` field explicitly
  names the dependency as the stated reason for selecting `use_case` testing at all
  (S1: *"downstream dependencies (such as PURE-ERTMS-R8 depending on this
  requirement), justifying 'use_case' testing"*; S7: *"has a direct dependency
  relationship with downstream requirements such as result display
  (PURE-ERTMS-R8)"*), and the second test case exists specifically to exercise that
  cross-requirement sequence. Without the dependency, the stated justification for
  `use_case` (as opposed to `state_based` alone) disappears, and with it, plausibly,
  the entire second test case.

This is the strongest evidence to date — still n=2, both real runs this scenario has
ever been attempted, not independent samples of different fixtures — that dependency
context visibly and reproducibly changes generated test content. It just has not yet
been observed on the specific side of the link (the "from"/dependent requirement) that
Known Limitations 1, 6, and 7 actually care about, because that side keeps hitting the
revision cap for an unrelated reason (its own `INCOMPLETE` wording) before it ever gets
there. Untangling that would need either a different `INCOMPLETE`-free dependent-side
fixture, or a less conservative answer policy for `INCOMPLETE` — either is a defensible
next step, but both are changes to the setup, not something this run's results should
be stretched to answer.

---

## Threats to validity, restated for this run specifically

- **n=1 per scenario, as planned and stated in advance** (spec doc). Every "hit" above
  could be sampling; the two-run replications (S1/S7 on R7's dependency effect; the S3/
  S6 unplanted-cycle routing; the no-op-rewrite pattern across S10/S12) are the only
  places this run has anything beyond n=1, and even those are two runs of overlapping
  material, not independent samples.
- **The answer policy shapes which scenarios can produce a `COMPLETED` requirement at
  all.** Every category except `NON_ATOMIC` is scripted to decline resolving the issue
  — which is a defensible, stated choice (avoids fabricating grounding the policy
  doesn't have), but it means most requirements with more than a trivial issue will hit
  the revision cap rather than complete, which in turn blocks Strategy
  Selection/Test Generation from ever running on them. Several of this run's
  "unanswerable this run" results (S1's R8, S12's `ACTAPP-R2-AC1`/`LUITEL-R1`) are
  downstream of this policy choice, not of the pipeline's own detection quality.
  A less conservative policy would answer different questions than this one does, and
  would not be comparable to the 2026-08-10 runs — which is exactly why it wasn't
  changed for this suite.
- **Planted fixtures (S4, S5, S6) are cleaner than real conflicts** — S4/S5's detection
  hits bound nothing about a real SRS's contradictions, only that the mechanism fires at
  all on unambiguous cases.
- **`temperature: 1.0`**, unchanged from the first real run, for comparability. Every
  soft result above could look different at `temperature: 0`.
