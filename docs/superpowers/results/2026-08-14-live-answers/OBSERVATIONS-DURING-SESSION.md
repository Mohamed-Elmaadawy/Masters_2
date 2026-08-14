# Observations made during the live-answering session (2026-08-14)

Written *while* answering, before the run finished. These are things noticed at the prompt
that would be lost by the time `SESSION.md` gets written. Not conclusions — leads to check
against the run records afterwards, then fold into `design/DESIGN_NOTES.md`.

**Provenance.** Authored by the assistant session running alongside the live answering (the
operator asked for the observations to be recorded somewhere before they were forgotten), not
by the agent executing the runs and not by any background process. Two different confidence
levels are mixed here deliberately:

- **Verified by direct inspection** of `datasets/requirements-xml/XMLZIPFile/1998 - themas.xml`:
  every quotation from SRS-005/006/009/010, the `LO ≤ T ≤ LT` corruption, the Condition 1 /
  Condition 2 definitions, and the "these limits" / "this module" referents. These were read out
  of the file, not inferred.
- **Unverified leads**, flagged as such in each item: frequency claims, how often a shape
  recurs, and anything phrased as "check after the run". Confirm against the run records before
  any of it reaches `SESSION.md` or the design notes.

---

## 1. `LO = T_LT` is a corrupted inequality, not domain notation

Known Limitation 5's anchor example is **not** an undefined abbreviation. The source
(`datasets/requirements-xml/XMLZIPFile/1998 - themas.xml`, SRS-010) reads:

> "…output a request to turn on the heating unit if `LO ≤ T ≤ LT` or the cooling unit if
> `UT ≤ T ≤ UO`."

`LO = T_LT` is `LO ≤ T ≤ LT` with the `≤` signs flattened to `=` and a space lost. The same
corruption appears throughout the document: "If `T = LO` or `UO = T`", "Condition 1:
`LT = T = UT`".

Both `LO` and `LT` are defined one paragraph above the requirement that uses them
(`LT = TSET − TD`, `LO = TSET − OD`).

**Why it matters:** no detector could resolve `LO = T_LT`, because it is not a meaningful
expression. Limitation 5 has now been reframed three times — undefined notation, then missing
document context, now corrupted input — and this third framing is the one supported by the
source.

**Wider risk, unmeasured:** PURE's XML is extracted from PDFs and mathematical notation did
not survive. Every inequality in this document is damaged. Before PURE carries evaluation
weight, check how widespread this is — a corpus that silently mangles `≤` into `=` affects far
more than one limitation, and any requirement containing a comparison is suspect.

## 2. Every vague reference so far resolved from the source document

Three for three, all resolvable from context the Quality Checker never sees:

| Requirement | Phrase | Resolves to | Where |
|---|---|---|---|
| `THEMAS-REQ-D` | "these limits" | the overtemperature limits | previous sentence, SRS-009 |
| `THEMAS-REQ-E` | "this condition" | Condition 2 (`LO ≤ T < LT` or `UT < T ≤ UO`) | same section, SRS-010 |
| `THEMAS-REQ-E` | "this module" | Determine H/C Mode (SRS-010) | the process's own section |

The vagueness is an artefact of **excerpting one sentence from a structured SRS**, not a
defect in the original document. Direct support for Limitation 5's "input problem, not
taxonomy problem" reframing — and it applies to `VAGUE_PRONOUN` generally, not only to
notation.

## 3. Naming a referent is not enough — the loop can recurse

`THEMAS-REQ-E` round 1 rewrote "this condition" to "Condition 2" (and silently repaired the
mangled inequality to `LO <= T < LT`, which is a genuine improvement). Round 2 flagged it
`VAGUE_PRONOUN` **again**, because "Condition 2" is itself defined outside the requirement.

Left alone this loops: every rewrite names something defined elsewhere, the checker cannot see
elsewhere, and it re-flags until the cap. The only exit is **inlining the actual criteria**,
which is what the round-2 answer supplied.

Check after the run: did round 3 accept the inlined version? If yes, this is a clean live
demonstration that the refine loop converges only when the human supplies content the pipeline
structurally cannot fetch for itself.

## 4. The human has no way to say "real issue, cannot be fixed here"

On `LUITEL-R7` the honest position is: the flag is correct, the fix is a document-level split,
and no rewrite of this requirement can do it. The available channels are:

- `user_confirms_resolved: True` — means "resolved, stop raising it". False here.
- `False` — correct, but the issue is re-asked every round until the cap fires.

There is no third option. So a genuinely unfixable-in-place issue is indistinguishable, in the
record, from one the human simply keeps failing to resolve.

Same shape as Known Limitation 8's schema gap, appearing in the **human channel** rather than
in `RefinedRequirement`. Related to the proposal there to reuse `CAP_STOPPED` with an explicit
`cap_reason`; a `RefinerAnswer` flag meaning "acknowledged, out of scope for refinement" would
be the human-side equivalent. Not designed, not adopted — recorded so it is not rediscovered.

## 5. `LUITEL-R7` will cap for two different reasons

Its `non_atomic` issue is unfixable in place (needs a split); its `incomplete` issue is
unknowable (no source document — it is an isolated illustrative sentence from a paper, so no
trigger is specified anywhere). When writing this up, attribute the cap to **both**, not to the
split alone.

## 6. The Rewriter can launder an unanswerable gap into specification-shaped prose

`AUTOGEN-US2`, round 1 -> round 2:

- before: "As a user, I want a product that is **reliable and efficient** so that I can depend
  on it."
- after: "…that is reliable and efficient **according to performance and reliability metrics
  defined by the product owner**, so that I can depend on it."

The human answer supplied no threshold (none exists — see item 8). The Rewriter turned that
absence into text that *reads* like a specification while adding nothing testable: a tester
still cannot write a test, and "defined by the product owner" simply defers the decision.

**Sibling of Known Limitation 11, and arguably the worse shape.** 11 is a placeholder inserted
where a value already existed; this is a *deferral* inserted where no value exists. Both change
the appearance of the text without changing its testability — but `[TBD: measurable value]` is
visibly unfinished, whereas "as defined by the product owner" looks like a deliberate design
decision and could survive a careless human review.

**Good news, and it should be reported as such:** the Quality Checker re-flagged it, so the
pipeline did not accept the laundering. Count how often this shape appears and whether the
checker catches it every time — the failure mode only becomes dangerous when it slips through.

## 7. One throwaway requirement can consume three questions with one real answer between them

`AUTOGEN-US2` raised `ambiguous_term` twice ("reliable", "efficient") plus `non_atomic` in a
single round, producing three questions whose honest answers were near-identical ("no source,
no domain, cannot say"). Reasonable behaviour — they are genuinely different terms — but it is
a human-cost the design does not account for.

Worth measuring across the suite: questions per requirement per round, and how many of them the
human could meaningfully distinguish. Relevant to any claim about human effort.

## 8. `NON_ATOMIC` flags structure, not whether splitting is worth doing

Both `LUITEL-R7` and `AUTOGEN-US2` are technically non-atomic. Splitting `LUITEL-R7` yields
three genuinely testable requirements; splitting `AUTOGEN-US2` yields two equally unmeasurable
ones, because the real defect there is the undefined terms, not the bundling.

So the category identifies a structural property and says nothing about the value of acting on
it. Relevant to Known Limitation 8's `list[str]` question: a split mechanism would fire on both
cases equally, and only one of them benefits.

## 9. The corpus splits into two groups and the comparison must respect it

Requirements from real SRS documents (THEMAS, ERTMS) have a source to answer *from* — the live
answers added real content there. Requirements from illustrative or LLM-generated sentences
(`AUTOGEN-*`, `LUITEL-*`) have no document behind them, so the honest live answer is close to
what the refusing policy already said.

The two policies will therefore **agree** on the second group. Report the refusing-vs-answering
comparison per requirement and split by group; aggregate outcome counts would wash out the
effect being measured and understate it.

## 10. Known Limitation 7 observed live — stale consistency analysis re-flags a fixed conflict

Predicted before the round ran, then confirmed. `PURE-THEMAS-R6-P` (planted at 5°F) conflicts
with `PURE-THEMAS-R6` (3°F, the source value).

- Round 1: human answered "3°F — this requirement has the wrong number".
- The Rewriter **applied the fix**: the text now reads "up to 3 degrees Fahrenheit", matching
  `R6`. The conflict is genuinely gone.
- Round 2: the Quality Checker flagged `inconsistent` **anyway**, because the consistency
  report was computed once on the original text before refinement began and never re-runs.

This is the strongest available evidence for Known Limitation 7, and better than the reasoning
recorded there: the human's answer *worked*, the pipeline fixed the document, and the pipeline
then failed to notice its own fix.

**Consequence worth carrying into the fix discussion:** the only thing that stops this looping
to the cap is a human setting `user_confirms_resolved: True` in round 2 — the first correct use
of that flag in this session. So the pipeline currently depends on a person noticing that its
document-level analysis has gone stale. That is a stronger argument for the "advisory post-pass"
or "phase the pipeline" options than the cost analysis already in the notes.

Contrast with its partner `PURE-THEMAS-R6`, where the human gave a correct, complete, actionable
answer and the pipeline still could not use it, because the fix belonged to a different
requirement. The pair isolates two different failures cleanly: R6 = architectural (one
requirement in scope), R6-P = staleness (analysis never re-runs).

## 11. The answering policy is not uniformly generous, by design

The trigger question on `LUITEL-R7` got an answer close to what the refusing policy would say,
because the source genuinely does not contain the information. That is the correct behaviour
and not a failure of the experiment: the answering policy answers *where the source supports
an answer*. What distinguishes it is specificity — naming what is missing and what would
settle it, rather than a blanket template.

Consequence for the comparison: the two policies will agree on some requirements. Report the
per-requirement breakdown, not just aggregate outcome counts, or the effect will look smaller
than it is.
