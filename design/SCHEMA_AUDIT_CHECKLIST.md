# Schema Weak-Point Checklist

A repeatable methodology for finding gaps in `schemas.py`. Run this whenever the
schema changes significantly, before treating a design as "done," or periodically as
a sanity check. Each lens below is a distinct way a schema can silently be wrong, and
each one has already caught a real issue in this project (noted for calibration).

## 1. Cardinality audit

For every field that references another entity, ask: **is this really 1:1, or could
it legitimately be 1:N, N:1, or N:M?** Don't assume a relationship is a fixed pair
just because that's the easiest thing to model.

*Caught before:* `ConsistencyConflict` was a fixed pair (`requirement_id_a/b`) but a
conflict can genuinely involve 3+ requirements jointly. `TestCase.requirement_id` was
singular but a single test case can cover multiple requirements at once.

## 2. Symmetry audit

When two concepts in the schema look structurally similar, check whether they're
modeled the same way — and if not, whether the difference is *actually justified* or
just an inconsistency. Write down the reason, don't just leave it implicit.

*Caught before:* Conflicts and dependencies looked similar at first glance but needed
different cardinality treatment (conflicts can be an inseparable group; dependencies
are always decomposable pairwise edges) — the audit forced articulating *why*.

## 3. Degenerate/boundary-case audit

What happens at zero, one, or an empty list for every field? Are `min_length` /
`min_items` constraints present everywhere they should logically be required?

*Caught before:* `ConsistencyConflict.requirement_ids` needed `min_length=2`.
`TestCase.requirement_ids` needed `min_length=1`. `TestStrategy.techniques` needed
`min_length=1` (fixed).

*Caught in the 2026-08-05 verification pass:* every free-text `str` field that's
supposed to always be non-empty (`rationale`, `explanation`, `question_text`,
`Requirement.text`) had no `min_length=1`, so an LLM call returning `""` would
silently pass validation. Same gap on three list fields that should never
legitimately be empty: `RefinerTurn.questions`, `TestCase.steps`,
`TestPlan.test_cases`. All ten now have `min_length=1`. See `DESIGN_NOTES.md`.

## 4. Lifecycle audit

For every entity, ask: how does it get created, revised, and resolved? Does the
schema capture enough to know its status at each stage, not just its current
snapshot?

*Caught before:* `Issue` needed a stable `id` (not just a category) to track a
specific issue instance across Refiner revisions. `RefinerAnswer.user_confirms_resolved`
was added because a resolution status wasn't otherwise capturable.

## 5. Traceability audit

Can every output be traced back to exactly what produced it, and forward to what
consumes it? Look for objects that reference "an issue" or "a question" only by
category/type rather than by a specific instance id.

*Caught before:* `ClarifyingQuestion` originally only recorded `issue_category`, not
which specific `Issue` it addressed — a real gap when a requirement has two issues of
the same category.

## 6. Concurrency/independence audit

If two parts of the pipeline run independently (per-requirement stages processing
different requirements, possibly out of order or in parallel), can their outputs
conflict, duplicate, or contradict each other without either side knowing?

*Caught before:* Test Case Generation running once per requirement means two
requirements linked by a dependency can each independently produce a near-duplicate
integration test case, since neither run knows what the other produced.

## 7. Evaluability audit

For every field an LLM fills in, ask: **could a human or a program actually check
whether it was done correctly?** If the answer is "not really, you'd just have to
trust it," that's a weak point — even if the field looks reasonable on its face.

*Caught before:* Pairwise-testing coverage is checkable in principle, but an LLM
can't reliably guarantee it by prompting alone (a plausible-looking guess can silently
miss required pairs). Technique selection needed explicit rules rather than free LLM
judgment, precisely so the `rationale` field has something concrete to be checked
against.

## 8. Empirical audit

Take real or realistic input data and manually trace it through the schema, field by
field, end to end. Note anywhere it breaks, forces an awkward fit, or silently loses
information that was present in the source text. This is the one lens the other seven
can't substitute for — everything else is reasoning from first principles about the
schema's shape; this is the only one that tests it against something it didn't design
itself around.

## How to run a pass

1. Go through lenses 1-7 against the current schema, one at a time, asking the bolded
   question for every relevant field/model.
2. Run an empirical pass (lens 8) with actual example requirements, not hypothetical
   ones — real text surfaces gaps hypothetical reasoning misses.
3. For anything found: decide if it's worth fixing now, deferring (document why, and
   the path to fixing it later), or accepting as a permanent, documented limitation.
4. Log the outcome in `DESIGN_NOTES.md` either way — a rejected idea with reasoning is
   as valuable as an accepted one, since it prevents re-litigating the same question
   later without remembering why.
