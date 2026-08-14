# Implementation log

What changed, why, and what it changed. Newest first. One entry per change.

This file is deliberately **not** where reasoning lives — that is `design/DESIGN_NOTES.md`.
An entry records the *event*: what was touched, which decision it implements, and what
measurably differed afterwards. Link to the `DESIGN_NOTES.md` section rather than
restating it.

Entry format:

```
## YYYY-MM-DD — one-line summary

**Changed:** files/modules touched, concretely.
**Why:** the decision or finding this implements — link to DESIGN_NOTES.md / a Known
Limitation number / a run's RESULTS.md.
**Impact:** what behaves or measures differently now, and how that was verified (tests
run and their counts, a re-run's numbers, or "documentation only — no behavioural
change").
```

An entry with an empty or hand-waved **Impact** is worse than no entry: "improved the
prompt" records nothing. If the impact is unknown because it has not been measured yet,
say exactly that and name the measurement that would settle it.

---

## 2026-08-14 — Prompt v2 batch: non_atomic definition + rewriter no-op rules, re-run on 4 scenarios

**Changed:** `orchestrator/example_prompts/quality_checker.txt` (non_atomic definition
tightened to "independently testable", one positive/one negative example added) and
`orchestrator/example_prompts/refiner_rewriter.txt` (three rules against inventing text
when an answer gives no concrete value, or confirms the requirement is already
correct/measurable), committed as `2178774`. New configs
`docs/superpowers/results/2026-08-11-behavior-scenarios/configs/scn-{04,07,10,12}-*-v2.yaml`
(`prompt_version: v2`, distinct `run_id`/`output_dir`, baseline `runs_scn-*` dirs from
2026-08-13 untouched). New
`docs/superpowers/results/2026-08-11-behavior-scenarios/RESULTS-V2.md`.

**Why:** implements `docs/superpowers/plans/2026-08-14-prompt-v2-batch.md` — the two
prompt-only fixes proposed for Known Limitation 8 (non_atomic over-flagging causal chains)
and Known Limitation 11 (Rewriter inventing text where none should appear), batched into
one re-run per the plan's cost/attribution argument.

**Impact:** re-ran the four scenarios containing every affected requirement (15
requirement-slots, PAID Gemini key, refusing answer policy unchanged) and compared every
requirement's outcome, quality-report categories, and rewrite text against the 2026-08-13
baseline run records. Of six pre-registered predictions: 5 held, 1 refuted
(`PURE-ERTMS-R2` still flagged `non_atomic` under the tightened definition — the model's
own explanation judges the two movement types genuinely independent, a defensible read).
All three regression guards passed: zero new `VALIDATION_FAILURE`, zero outcome changes
across all 15 slots, zero `COMPLETED`↔cap flips. Net token cost +1.3% (153,871 → 155,822
across the four scenarios). No prompt change made in response to the refutation. Full
per-prediction detail and quoted model output in RESULTS-V2.md; folded into
`design/DESIGN_NOTES.md` Known Limitations 8 and 11.

**Amended same day, after counting rewrites directly in both sets of run records.** The
outcome-level guards passed, but the entry above understated the effect. Text-changing
rewrites went **5 -> 0** (rewrites 19 -> 18, no-ops 14 -> **18**): every text change vanished,
not only the three targeted ones. Collateral: `PURE-ERTMS-R2` lost "shall **be able to**
supervise" -> "shall supervise" (a genuine improvement) and `ACTAPP-R2-AC1` lost the addition
of a missing actor ("**The system** identifies…") alongside the placeholder that was meant to
go.

Logical rather than defective — new rule 2 fires universally under a policy that never
supplies a value — but it means **refusing-policy runs can no longer distinguish "the rules
work" from "the Rewriter is disabled"**. Next measurement, named: replay the frozen live
transcript (`docs/superpowers/results/2026-08-14-live-answers/answers.json`) against v2, the
only run where answers carry content and the rules can be selective.

Also unreconciled: this entry reports +1.3% token cost, while cost computed from the same
records is essentially flat ($0.3387 -> $0.3385) with attempts down 101 -> 98. Likely raw
tokens vs. weighted cost; settle which figure RESULTS-V2.md should carry.

## 2026-08-14 — Prompt v2 replayed against the frozen live-human transcript

**Changed:** new configs
`docs/superpowers/results/2026-08-14-live-answers/configs/scn-{08-clean,09-vague,
10-atomicity,04-conflict-numeric,11a-cap-generate,11b-cap-stop}-v2-live.yaml`
(`prompt_version: v2`, `run_id`/`output_dir` suffixed `-v2-live`, prompts unchanged from
the already-committed `orchestrator/example_prompts/`). New
`docs/superpowers/results/2026-08-14-live-answers/RESULTS-V2-LIVE.md`. No prompt,
schema, fixture, or driver edited.

**Why:** answers the question the same-day refusing-policy batch amendment raised —
under a policy that supplies no content, text-changing rewrites went 5→0, which cannot
distinguish "the rules work" from "the Rewriter is disabled." Replaying the same nine
requirement-slots' frozen live-human answers (`answers.json`, unchanged) against v2 is
the one comparison where answers carry real content and the rules can be selective. See
`design/DESIGN_NOTES.md` Known Limitation 11.

**Impact:** ran via `answering_policy_driver.py` (PAID Gemini key, transcript replay,
unchanged). Text-changing rewrites: 5/9 (v1-live) → 3/9 (v2-live) — not 5/9 → 0/9. All
three substantive v1 changes survived (`THEMAS-REQ-D`, `THEMAS-REQ-E`,
`PURE-THEMAS-R6-P`); both of the two named artifacts were suppressed (`AUTOGEN-US2`'s
deferral phrase, `PURE-THEMAS-R6`'s cosmetic reformat) — the rules are measurably
selective, not silencing. All 6 pre-registered predictions held (one, `THEMAS-REQ-E`,
with a caveat). Two regression guards tripped, both the same requirement and root
cause: 1 replay miss and 1 `COMPLETED`→`CAP_STOPPED` flip on `THEMAS-REQ-E`, traced to
v2's own round-1 rewrite introducing a new `incomplete` gap the frozen transcript has
no answer for — not a defect in either edited prompt, reported rather than patched
around. Zero new `VALIDATION_FAILURE`. Cost $0.2667 (v2-live) vs $0.2833 (v1-live,
`SESSION.md`), both measured from real token counts. Full per-prediction detail, quoted
model output, and three drift-warning examples in RESULTS-V2-LIVE.md; folded into
`design/DESIGN_NOTES.md` Known Limitation 11.

## 2026-08-14 — Live answer policy run: six scenarios, nine requirements, real human

**Changed:** new `docs/superpowers/results/2026-08-14-live-answers/` — `live_bridge_driver.py`
(file-bridge `HumanFns` calling `orchestrator/human_cli.py`'s real
`answer_questions_cli`/`decide_at_cap_cli` unchanged, with injected `input_fn`/`output_fn`
instead of a terminal), byte-identical copies of the six scenarios' configs/fixtures,
`extract_answers.py` (builds `answers.json` from the run records), `answers.json` itself,
`answering_policy_driver.py` (the replay driver, with `--self-test`), and `SESSION.md`.

**Why:** executes `docs/superpowers/plans/2026-08-14-live-answer-policy.md` — the measurement
that entry's "Impact" left open: what refinement does when a real human, not
`answer_policy_driver.py`'s refusal policy, answers. See Known Limitation 10 (downgraded
2026-08-13) and Known Limitation 11.

**Impact:** measured, not estimated. 9 requirement-slots: 4 `COMPLETED`, 4 `CAP_STOPPED`, 1
`CAP_GENERATED`. Text-change rate 5/9 (55.6%; 4/9 substantive, one is unit-format-only) vs.
the refusing-policy baseline on the identical fixtures, 4/9 (44.4%) — both well above the
full 47-item suite's 19%, confirming the plan's own "favorable ground" threat to validity.
One clean case where the live-human answer fixed a cross-requirement conflict the refusing
policy structurally cannot (`PURE-THEMAS-R6-P`, 5°F → 3°F, reached `COMPLETED`) and one case
where the refusing policy's "text changed" was bracket-placeholder insertion, not content
(`AUTOGEN-US3`, `AUTOGEN-US2` — Known Limitation 11's pattern, both directions now observed).
Cost: $0.2833 measured from real `prompt_tokens`/`completion_tokens` at $1.50/1M in +
$7.50/1M out, within the plan's $0.20–0.35 estimate. `answering_policy_driver.py --self-test`
replayed all 16 turns / 27 questions from this session's own records: 0 misses, 0 drift
warnings. Full breakdown, original/final text for every requirement, and two methodology
incidents (one briefly non-verbatim answer, caught and the affected run restarted; one
echoed-message glitch, caught and not recorded) are in `SESSION.md`.

---

## 2026-08-14 — Annotated-corpus check; glossary fix deferred on evidence; S9 result recorded

**Changed:** `docs/superpowers/plans/2026-08-14-evaluation-design.md` (two sections added on the
annotated XML subset and the glossary decision); `design/DESIGN_NOTES.md` Known Limitation 5
(S9 result).

**Why:** before building an extractor, checked what the committed corpus actually contains; and
before adding schema fields for the limitation-5 fix, checked whether the measurement gating it
had already been taken.

**Impact:** two findings, one of which cancelled a planned schema change.

- **Extraction is far cheaper than assumed.** 6 of 18 files in
  `datasets/requirements-xml/XMLZIPFile/` carry explicit `<req id>` elements — 1,018
  requirements, 819 of them in five documents not yet spent. "What counts as a requirement" is
  therefore PURE's own annotation decision, citable, with no inference and no loss rate. The
  79-document PDF corpus is not needed to start.
- **The glossary fix is deferred, not adopted.** 171 `<glossary_item>` term/meaning pairs exist,
  which would have made the proposed pre-pass cheap — but S9 shows `LO = T_LT` was **never
  flagged** across three rounds, so there is no wrong judgement for definitions to correct, and
  THEMAS's glossary does not contain `LO`/`LT` anyway. A free measurement is named instead: run
  ~20 requirements from `eirene_fun`/`gamma j` without a glossary and count whether
  glossary-defined domain terms get falsely flagged. The schema change (a `GlossaryTerm` model
  plus one optional `RequirementSet.glossary` field) is scoped in the plan but not made.

No code touched; `design/schemas.py` unchanged, so no diagram regeneration required.

---

## 2026-08-14 — Evaluation design and document-reanalysis plan written (design only)

**Changed:** two new plans —
`docs/superpowers/plans/2026-08-14-evaluation-design.md` and
`docs/superpowers/plans/2026-08-14-document-reanalysis-plan.md`.

**Why:** the behaviour work has established what the pipeline *does*; the evaluation phase has
to establish whether it *helps*, and needed a design before any corpus extraction begins.
Known Limitation 7's fix needed a decision recorded either way.

**Impact:** none — nothing run, no code touched. Decisions recorded:

- **The paper's primary claim is structural, not qualitative.** The pipeline produces test
  artifacts that are structurally valid, traceable and technique-grounded by construction;
  that is measurable automatically against the existing 326 schema checks, with no rubric and
  no rater. Content quality becomes a secondary, blinded, hand-scored layer on a smaller
  sample.
- **Baseline fairness constraint:** the one-shot arm must be given the *same output schema*,
  or the structural comparison is a trivial win and dismissible. Refinement trajectory and
  document-level context are to be reported as structural differences, not scored as wins.
- **Ablation, not cross-system comparison**, with the reasoning to be stated in the
  methodology rather than left as an unexplained omission.
- **Q2 (per-stage model sensitivity)** is a separate configuration-only study, scored against
  the existing scenario suite's ground truth, hypothesis pre-registered. ~$10–15.
- **Known Limitation 7: recommended NOT to build now.** The plan leads with that
  recommendation and, if overridden, specifies a branch, five separately-revertible commits,
  a config flag defaulting to off, and resume changes last with a mutation check.

---

## 2026-08-14 — PURE extraction-corruption scan: confined to one document, not corpus-wide

**Changed:** `design/DESIGN_NOTES.md`, Known Limitation 5 — measurement appended under the
live-session evidence.

**Why:** the live session found `LO = T_LT` to be a flattened `LO <= T <= LT`, which raised an
unmeasured risk that PDF-to-XML extraction had damaged comparisons across the PURE corpus.
Several fixtures are PURE-derived, so this had to be settled before more runs depend on them.

**Impact:** the risk is narrowed, and the alarm I raised is partly withdrawn. Scanned all 18
files of `datasets/requirements-xml/XMLZIPFile/` for comparison chains, underscore-joined
tokens, and surviving Unicode math:

- 6 of 18 documents contain any mathematical `=`; most SRS text is prose.
- `1998 - themas.xml` is the **only** file with the flattened-comparison signature.
- `2006 - eirene sys 15.xml` retains 7 Unicode math symbols, proving extraction can preserve
  them — so this is per-document (source PDF encoding), not systemic.
- `2007-ertms.xml` is unaffected, so the ERTMS fixtures are sound.

Explains why it looked systemic: THEMAS is the document this project has used for the schema
spot-check, all three 2026-08-10 runs, and several fixtures. Scope limit recorded: the 79-file
full corpus is unparsed, so nothing is known about it — the scan should be repeated when an
extractor for it is built.

---

## 2026-08-14 — Live-answer session results folded into the design notes

**Changed:** `design/DESIGN_NOTES.md` — Known Limitations 5, 7, 8, 10 and 11 extended with
live-session evidence (11 generalised from one defect into a three-variant pattern);
`docs/superpowers/results/2026-08-14-live-answers/SESSION.md` (provenance of
`OBSERVATIONS-DURING-SESSION.md` corrected — it was written by the assisting session at the
operator's request, not by an unknown background process).

**Why:** the live-answering run (same file, previous entry) settled several questions the
notes had recorded as open or reasoned-only.

**Impact:** documentation only; numbers verified directly against the run records rather than
taken from the run's own write-up (4 `COMPLETED` / 4 `CAP_STOPPED` / 1 `CAP_GENERATED`, 88
calls, $0.2833 — all confirmed). What changed in the record:

- **7** — now has a live demonstration instead of an argument: `PURE-THEMAS-R6-P` was fixed by
  the human's answer (5°F -> 3°F) and re-flagged `inconsistent` anyway from the pre-refinement
  consistency report. The loop exited only because the human set `user_confirms_resolved: True`,
  so the design currently depends on a person noticing its analysis has gone stale.
- **11** — generalised to one pattern with three variants: placeholder where a value exists
  (`LUITEL-R1`), deferral where none exists (`AUTOGEN-US2`), cosmetic edit where the human asked
  for none (`PURE-THEMAS-R6`). Each alters appearance without altering testability.
- **10** — the threat-to-validity note can now cite a run where the human *did* answer.
  Substantive change rate 4/9 for both policies; the difference is in substance, not rate. Also
  records that a refusing answer produced a **false** `COMPLETED` on `LUITEL-R7`, so earlier
  runs' success counts are inflated in the pipeline's favour.
- **8** — the one genuine `NON_ATOMIC` case now has a human on record confirming the split is
  correct and the pipeline unable to perform it; plus the observation that `NON_ATOMIC` flags
  structure rather than whether splitting is worth doing.
- **5** — three vague references all resolved from the source document, and `LO = T_LT` shown to
  be a corrupted `LO <= T <= LT` from PDF-to-XML extraction. Raises an unmeasured corpus risk:
  every inequality in that document is damaged.

**Named next measurement:** count the three limitation-11 variants and the PURE extraction
damage in the next suite; all are n=1 today.

---

## 2026-08-14 — Live answer policy planned (not yet run)

**Changed:** new `docs/superpowers/plans/2026-08-14-live-answer-policy.md`.

**Why:** Known Limitation 10's downgrade left the project unable to measure refinement
effectiveness at all — every run so far used an answer policy that declines to answer. See
`design/DESIGN_NOTES.md`, Known Limitation 10, "Suite result 2026-08-13".

**Impact:** none yet — plan only, nothing run. The plan fixes six scenarios / nine
requirements (~$0.20–0.35), the live-answering rules, the `answers.json` freeze format keyed
on `requirement_id::issue_category`, a replay driver whose fallback is explicit and counted,
and the metrics for the refusing-vs-answering comparison. The measurement that would make
this entry non-empty is that comparison; it has not happened.

Decision recorded here rather than in DESIGN_NOTES: the transcript is captured **from the run
records**, not from a parallel log, because `ClarifyingQuestion` already stores
`issue_id`/`issue_category`/`question_text` and a second copy could disagree with the record.

---

## 2026-08-14 — Known Limitations reconciled with the 2026-08-13 suite results

**Changed:** `design/DESIGN_NOTES.md` — "Suite result 2026-08-13" blocks appended to Known
Limitations 1, 3, 6, 7, 8, 9 and 10, plus new Known Limitation 11; `CLAUDE.md`
("Known-open, deliberately": limitation 10 downgraded, 11 added).

**Why:** the suite refuted or altered four previously recorded positions. Sources:
`docs/superpowers/results/2026-08-11-behavior-scenarios/` run records, tallied directly from
the JSON rather than from the run's own write-ups.

**Impact:** documentation only. Positions that changed, each now carrying the evidence:

- **7** — the qualification added earlier the same day is **withdrawn**. The Test Generator
  does use dependency context (`TC-13-PURE-ERTMS-R7-2` cites both ends of the `R8 -> R7`
  link), so the dependency half is the damaging one as originally written.
- **8** — 5 `non_atomic` flags, ~2 wrong, and `LUITEL-R7` correctly caught. The "all 14 flags
  are false positives" generalisation from THEMAS-only data was too strong; the definition fix
  is now polish rather than a rescue.
- **9** — the empirical half is dead: `mobile` 2 and `ai_system` 1 alongside `other` 31, and
  `INFEASIBLE_FOR_TYPE` fired zero times. The structural half (three members, one technique
  pool) stands and is now the whole limitation.
- **10** — downgraded from defect to threat to validity. 38/47 no-op rewrites all trace to the
  refusing answer policy; no no-op followed an informative answer, and all 9 text-changing
  rewrites inserted placeholders rather than invented facts. Fixes (b) and (c) dropped as
  unnecessary; fix (a) retained.
- **1, 3, 6** — annotated with measurements rather than reversed: spanning cases are now known
  to occur (1), `PERFORMANCE` selected zero times including where expected (3), and the strict
  `TestPlan` rule still never fired though the risk case is closer (6).
- **11 (new)** — the Rewriter tagged `LUITEL-R1` as needing a measurable value beside its own
  `5s` threshold. n=1; counting it in the next suite is the named next measurement.

---

## 2026-08-14 — Known Limitations reworked against real run data; two new limitations added

**Changed:** `design/DESIGN_NOTES.md` (~700 lines added across Known Limitations 1–3, 5–8,
plus new entries 9 and 10 and a "split is not a rewrite" analysis under 8); `CLAUDE.md`
("Known-open, deliberately" list, DESIGN_NOTES line count 2,500 → 3,100);
`docs/superpowers/plans/2026-08-11-behavior-scenarios-RUN-PROMPT.md` (four suite-wide
tallies, five pre-registered predictions, an S1 dependency check, an S12 note).

**Why:** a review of the three real runs of 2026-08-10 found that several limitations were
justified by reasoning that the project's own data contradicted — see `DESIGN_NOTES.md`,
Known Limitations 3 (embedded *is* in the corpus), 6 (the strict rule has never fired), 9
(new: three `SystemType` members share one technique pool) and 10 (new: no-op rewrites
accepted).

**Impact:** documentation only — no behavioural change. Verified that nothing in `design/`
or `orchestrator/` was touched: full suites green afterwards (schemas 326, arch diagrams
88, diagrams 13, harness 464, CLI 39, stages 163, rotation 18, config 95 = 1,206 checks),
all 14 scenario configs load, all 12 fixtures validate.

**Date correction, same day:** the notes added on this date were first labelled
`2026-08-11` inside `DESIGN_NOTES.md` (anchored on the behavior-scenario plan's filename
rather than the calendar). Fixed: 15 date markers in `design/DESIGN_NOTES.md`, 4 in
`CLAUDE.md`, and 1 in the run prompt now read `2026-08-14`. References to the
`plans/2026-08-11-*` and `results/2026-08-11-*` paths were deliberately left alone — those
are real filenames, not dates of authorship.

---

## 2026-08-13 — Behavior scenario suite executed against the paid Gemini API (first full pass)

**Changed:** no source changes. Added
`docs/superpowers/results/2026-08-11-behavior-scenarios/` — `run_driver.py`, `RESULTS.md`,
`ANALYSIS.md`, and 14 run directories under `configs/runs_scn-*/`.

**Why:** the suite exists to measure behaviour that Known Limitations 1, 3, 5, 6, 7, 8, 9
and 10 all depend on and that no test can supply. Plan:
`docs/superpowers/plans/2026-08-11-behavior-scenarios.md`.

**Impact:** 14/14 scenarios ran; 263 API calls; 345,235 prompt + 43,241 completion tokens;
**$0.84** (pre-run estimate was ~260 calls and $0.55–$1.05, so the estimate held). S13
correctly reached `DocumentOutcome.DEGRADED`. Results that change previously recorded
positions:

- **Test generation does use dependency context** — `TC-13-PURE-ERTMS-R7-2` cites both
  `PURE-ERTMS-R7` and `PURE-ERTMS-R8`, the first multi-requirement test case in the
  project. Refutes prediction 3 and unblocks Known Limitations 1, 6 and 7.
- **`non_atomic` is more accurate than the 2026-08-10 data suggested** — 5 flags, of which
  `LUITEL-R7` (three independent reports) is a correct catch; 2 remain conjunction-splits.
  Partly refutes prediction 1.
- **Classification is no longer uniformly `other`** — 31 `other`, 2 `mobile`, 1
  `ai_system`. Refutes the empirical half of Known Limitation 9.
- **`infeasible_for_type` never fired** across 34 requirements, so the 2026-08-10 false
  positive did not recur.
- **`PERFORMANCE` was still never selected**, including on `LUITEL-R1` where S12's ground
  truth expected it. Known Limitation 3's practical half stands.
- **38 of 47 rewrites (81%) changed nothing**, and every one traces to the scripted answer
  policy declining to supply information (3 human overrides, 35 refusals). No no-op
  followed an answer that carried real information, so this is **not** a Rewriter defect —
  it downgrades Known Limitation 10 to a threat to validity: refinement effectiveness
  cannot be measured with an answer policy that refuses to answer.
- **New defect found:** the Rewriter inserted `[TBD: measurable value]` into `LUITEL-R1`,
  which already stated `5s` — degrading a measurable requirement. Not invention (all 9
  text-changing rewrites inserted placeholders, never invented facts), but a real quality
  regression. Not yet written up in `DESIGN_NOTES.md`.
