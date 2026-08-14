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
