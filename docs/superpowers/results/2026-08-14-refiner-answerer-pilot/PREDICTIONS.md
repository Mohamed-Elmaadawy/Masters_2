# Predictions, written before running (2026-08-14)

Question this pilot answers: when the Refiner asks a clarifying question, does it
matter whether a human or an LLM answers it, and does having the source document open
change anything? Nothing more than that.

**Requirements picked:** PURE-GAMMA-J-0033 ("shall be easy to use"), -0034 ("shall be
easy to learn"), -0042 ("shall be easy to upgrade") — from `pure-gamma-j` (32
`<glossary_item>` entries in the source XML, unlike `pure-peering`'s zero). All three
are bare one-line clauses in the SRS's Usability/Maintainability sections
(`0000 - gamma j.xml` lines 606-664) with **zero elaboration** anywhere else in the
document, and the 32 glossary entries are all RE-methodology jargon (actor, business
rule, use case, precondition) — none of it usability-domain.

**Prediction:** because the document contains nothing that resolves "how easy",
expect **0 of 3** of my (run-B) answers to be retrievable from the document — all 3
invented from judgement. Expect the rewrites to differ from the human's in specifics
(invented numbers/personas) but not in kind: both a human and an LLM are guessing,
since the source is silent on this question, so neither answer should actually be
grounded in the document.

**Refuted if:** any glossary entry or nearby text in the source actually answers one
of the three questions.
