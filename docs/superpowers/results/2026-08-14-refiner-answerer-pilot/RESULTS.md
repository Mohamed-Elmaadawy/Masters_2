# Refiner-answerer pilot: human vs. LLM answering clarifying questions (2026-08-14)

See `PREDICTIONS.md` for the prediction committed before running, and
`docs/superpowers/plans/2026-08-14-evaluation-design.md` for why this is a separate,
cheap question from the Q1/Q2 evaluation design.

## Setup

3 requirements, run twice, same config shape (`configs/run-a-human-v2.yaml` /
`configs/run-b-llm.yaml`, both copies of
`docs/superpowers/results/2026-08-14-pure-peering-smoke/configs/pure-peering-smoke.yaml`
with only `run_id`/`output_dir` changed), `gemini/gemini-3.6-flash`, `prompt_version:
v2`, `max_revisions: 3`, paid-tier key (invocation-only `GEMINI_API_KEY` override, no
code change — see the comment header of either config).

- **Run A** (`runs_run-a-human-v2/`): Mohamed answered every clarifying question with a
  fixed policy, his explicit choice mid-run: *"I don't know — the document doesn't
  define this."*, confidence `n`, every time. He did not consult the source XML (he
  asked to see the requirement text only, which I gave inline).
- **Run B** (`runs_run-b-llm/`): I answered, with `0000 - gamma j.xml` open, narrating
  doc-lookup-vs-judgement before each answer, confidence `y` every time.
- Both used the real `orchestrator/human_cli.py` path (`answer_questions_cli`/
  `decide_at_cap_cli`) via `orchestrator/cli.py`'s default wiring — no code written.
  Mechanically: I drove the CLI through `run`/`resume` with plain-file stdin redirection
  (not a fifo — tried one first, Python's `input()` raised `RuntimeError: lost
  sys.stdin` against a Git-Bash `mkfifo` on this Windows setup, so abandoned it),
  relaying each question here and piping the answer back in verbatim. One real
  mid-pilot mistake: my first Run-A attempt fed the *cumulative* answer history into
  each `resume` instead of only the newly-needed answer, because I assumed each resume
  replays the whole requirement — it doesn't, only the still-pending round replays
  (`orchestrator/pipeline.py`'s `pending_round` logic, already covered by
  `test_resume_mid_round_asks_human_when_answers_missing`). That misrouted one answer
  into the wrong prompt and wasted one real quality-checker call. Caught before it
  affected any reported data; restarted clean as `run-a-human-v2`.

**Tokens:** Run A 46,368; Run B 34,765. Total 81,133 — inside the 60-90k estimate,
neither run near the 60k per-run stop threshold.

## Side by side

| Req | Round | Question (paraphrased core ask) | Human answer (Run A) | My answer (Run B) |
|---|---|---|---|---|
| 0033 "easy to use" | 1 | measurable usability criteria? | "I don't know — the document doesn't define this." (n) | Invented SUS≥70 + 5-min purchase-flow completion (y) — **judgement**, nothing in doc |
| 0033 | 2 | same, reworded | "I don't know..." (n) | *(resolved in round 1, no round 2)* |
| 0034 "easy to learn" | 1 | quantifiable learnability benchmark? | "I don't know..." (n) | Invented 10-min first-task completion via "help bubbles", no external docs (y) — **partially retrieved**: "help bubbles" is lifted from sibling requirement PURE-GAMMA-J-0035 (same usability section, not one of the 3 picked); the 10-minute number is still invented |
| 0034 | 2 | same, reworded | "I don't know..." (n) | *(resolved in round 1)* |
| 0042 "easy to upgrade" | 1 | measurable upgrade criteria? | "I don't know..." (n) | Invented 15-min upgrade window, "no manual steps beyond confirming install" (y) — **partially retrieved**: the delivery mechanism ("via internet connection") is lifted from PURE-GAMMA-J-0049, USB-portability from 0043/0044, both real sibling requirements; the 15-minute number and "confirm install" step are invented |
| 0042 | 2 | same, reworded | "I don't know..." (n) | *(resolved in round 1)* |

Full per-round records (issues, exact question text, answers, rewrites) are in
`configs/runs_run-a-human-v2/run-a-human-v2/requirements/*.json` and
`configs/runs_run-b-llm/run-b-llm/requirements/*.json`.

## Retrieved vs. invented (the prediction check)

Predicted 0/3 retrieved. **Refuted, partially:** 0/3 were *purely* retrieved (no
question had a document-stated answer), but 2/3 (0034, 0042) were **partial
retrieval** — I pulled real facts from *sibling* requirements elsewhere in the same
document (help bubbles; internet-connection patch delivery; USB portability) that
the Quality Checker's question didn't ask about directly, then invented the missing
number on top. Only 0033 was pure invention start-to-finish. I hadn't anticipated
sibling-requirement context as a retrieval channel when I wrote the prediction — I'd
only checked whether *the requirement's own line* or the *glossary* had an answer,
and neither did. The document as a whole is a richer context source than either of
those two places alone.

**Plausibility:** yes, all three of my invented numbers (SUS≥70, 5 min, 10 min, 15 min)
read as completely ordinary, professional acceptance criteria. Nothing about their
phrasing marks them as fabricated — a reviewer skimming the rewritten requirement
would have no way to tell "SUS score of at least 70" was invented on the spot versus
sourced from an actual usability study. That is the concerning half of this result.

## Did the rewrites differ in substance, or only wording?

**Substance, not wording — and it's the whole outcome that differs, not a phrasing
choice.** Run A's rewriter received a refusing answer every round and, all three
rounds, **produced a byte-identical no-op rewrite** ("The system shall be easy to
use" / "...easy to learn" / "...easy to upgrade.", unchanged) — this is exactly Known
Limitation 10 (no-op rewrite accepted) reproducing live, and all three requirements
hit the revision cap and ended `cap_generated`. Run B's rewrites replaced the vague
adjective with a concrete, testable sentence each time and **passed the quality
check in round 1** every time — all three ended `completed`, no cap needed. This
is a first-order outcome difference (`completed` vs `cap_generated`), not a
second-order quality difference within similar outcomes.

**Future-work item recorded from this finding:** `design/DESIGN_NOTES.md`, "Future work,
adjacent to Limitation 11 — cite a standard instead of inventing a threshold" (2026-08-15,
documentation only). Proposes the Refiner name a measurable property and a citable source
standard (e.g. ISO/IEC 25010:2023's Interaction Capability characteristic, the System
Usability Scale as an instrument) instead of inventing a number, with the target value left
explicitly unset. Includes a verified `STANDARDS_REFERENCE` table, the ISO/IEC 25010:2023 /
25023:2016 version-mismatch caveat, and a note that the SUS≥68 benchmark is unverified. Also
records the threat-to-validity this pilot exposed: PURE's source authors are unreachable, so
on this corpus no answerer — human or LLM — can supply a real value, which reframes the banked
2026-08-14 live-answer comparison as measuring "does supplying a value help" rather than "does
a human help."

## Verdict: is an LLM answerer worth building properly?

**Worth prototyping further, not worth trusting as-is.** The pilot shows a real,
large effect — a willing-to-commit answerer (human or LLM) resolves ambiguity in one
round; a refusing answerer never does, regardless of how many rounds are allowed
(Known Limitation 10 again, from the other direction). That's the headline, and it
says the *policy* (commit vs. refuse) matters more than *who* holds the pen.

But the LLM-specific risk is exactly what this pilot surfaced: 2 of 3 answers smuggled
in fabricated numeric thresholds that are indistinguishable from genuine domain
knowledge once embedded in the rewritten requirement text. A human confidently
inventing "SUS ≥ 70" would carry the same risk — this isn't unique to an LLM answerer
— but an LLM answerer is the one that could plausibly be run unsupervised at scale,
which is exactly where undetectable fabrication is most dangerous. Building it
properly would need, at minimum, a way to flag "this number has no document source"
alongside the answer, not just the answer text — otherwise it's manufacturing false
precision, not resolving ambiguity. n=3 requirements, one document, one prompt
version: not enough to generalize past "worth a bigger pilot with a fabrication-flag
mechanism," which is a design question, not a code one, for whoever picks this up
next.
