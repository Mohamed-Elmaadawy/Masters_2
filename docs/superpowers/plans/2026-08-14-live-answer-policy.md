# Live answer policy — measuring whether refinement actually improves requirements

**The question this answers.** Every run to date (2026-08-10 groq, 2026-08-10 gemini,
2026-08-13 scenario suite) used `answer_policy_driver.py`, which deliberately declines to
supply missing information. 38 of 47 rewrites in the suite changed nothing, and every one
traced to that refusal — see `design/DESIGN_NOTES.md`, Known Limitation 10, "Suite result
2026-08-13". So the project currently has **no measurement of what refinement does when the
human engages**, and every reported `COMPLETED`/cap outcome describes the answer policy as
much as the pipeline.

**Do not edit `answer_policy_driver.py`.** It is what makes the three existing runs
comparable with each other. This is a *second* policy, added beside it.

---

## 1. Subset to answer live

Nine requirements, chosen where refinement is the thing under test. Full-suite live
answering is not worth the human time, and the extra scenarios test detection rather than
refinement.

| Scenario | Reqs | Why it's in |
|---|---|---|
| `scn-08-clean` | 1 | Control. A clean requirement should still pass with a live human. |
| `scn-09-vague` | 2 | `VAGUE_PRONOUN` — an answer *can* name the referent, so refinement should visibly work. |
| `scn-10-atomicity` | 2 | Contains the one genuine `NON_ATOMIC` case in the suite (`LUITEL-R7`) plus the unmeasurable-adjective case. |
| `scn-04-conflict-numeric` | 2 | A conflict a single requirement's answer *cannot* fix — the honest floor for what refinement can do. |
| `scn-11a-cap-generate` | 1 | Irreducibly vague; tests whether a real human also hits the cap. |
| `scn-11b-cap-stop` | 1 | Same requirement, opposite cap decision. |

Estimated cost: ~$0.22 at the suite's measured rate (~$0.025/requirement), so **$0.20–0.35**
allowing for extra rounds when answers actually resolve issues.

## 2. How to answer, during the live session

Rules, so the answers are defensible later:

- Answer **as the requirement's author would**, not as someone helping the pipeline pass a
  check. Never phrase an answer to satisfy the Quality Checker.
- Where the source document supports a concrete value, actor or condition, **give it**. This
  is the whole point: the existing policy refuses precisely here.
- Where you genuinely do not know, **say so explicitly and specifically** ("the document does
  not state the sitting duration; a real project would get it from the clinical protocol").
  That is a legitimate answer and is *not* the same as the existing policy's blanket refusal.
- Use `user_confirms_resolved: True` only for a real judgement disagreement with the flag —
  e.g. "this is one causal step, not two behaviours". Not as a way to move the loop along.
- Answer each question on its own terms. Do not look ahead at what other rounds will ask.

## 3. Freezing the transcript

Capture comes **from the run records**, not from a separate log: `ClarifyingQuestion` already
carries `id`, `issue_id`, `issue_category` and `question_text`, and each `RefinerAnswer`
carries `question_id`, `answer_text` and `user_confirms_resolved`. A parallel capture file
would be a second copy of the same facts, free to disagree with the record — the pattern
`CLAUDE.md` warns about under "two fields that must agree".

Extraction script writes one data file, `answers.json`:

```json
{
  "source_runs": ["<run dir>", "..."],
  "captured": "2026-08-__",
  "answers": {
    "THEMAS-REQ-D::vague_pronoun": [
      {
        "question_text": "What specific temperature values ... 'these limits' ...?",
        "answer_text": "<what you actually said>",
        "user_confirms_resolved": false,
        "revision_number": 1
      }
    ]
  },
  "fallback": {
    "answer_text": "Not covered by the recorded transcript for this requirement and issue category.",
    "user_confirms_resolved": false
  }
}
```

Key is `requirement_id::issue_category`, value is a **list** in the order asked — one
requirement can raise two issues of the same category in one round, and a later run may ask
in a different order.

## 4. Replay driver

`answering_policy_driver.py`, beside the existing one. Behaviour:

- load `answers.json`; for each question, pop the next unused entry for its
  `requirement_id::issue_category` key;
- **on a miss (unknown key, or list exhausted) return the fallback verbatim** — never
  improvise, never fall back to the refusing policy's texts, and count the misses;
- when a matched entry's stored `question_text` differs materially from the question being
  asked, emit one stderr warning naming both. A high warning count means the comparison is
  measuring two different conversations.
- reuse `decide_at_cap` from the existing driver, except for 11a/11b, where only that branch
  differs — same rule the suite already follows.

Report the miss count and the warning count in any results built from this policy. Both are
threats to the comparison, not incidentals.

## 5. What the comparison measures

Same six scenarios, refusing policy vs answering policy, same prompts, same models:

- outcome mix (`COMPLETED` / `CAP_STOPPED` / `CAP_GENERATED` / `ERROR`);
- rounds to termination per requirement;
- text-change rate — the inverse of the 81% no-op rate;
- **by hand, and this is the real result:** is each final `refined_text` actually better than
  the original? Placeholders inserted where a value already exists count as *worse* (Known
  Limitation 11);
- test plans produced (a requirement that never completes never reaches generation);
- tokens and cost;
- fallback misses and question-drift warnings.

## 6. Threats to validity, to be written up with the results

- The author of the requirements-under-test, the builder of the pipeline, and the answerer are
  the same person. Unavoidable in a solo thesis; state it rather than leaving it implicit.
- Answers were given knowing the pipeline's issue taxonomy, which a naive user would not know.
- n=1 per requirement. The comparison shows a direction, not a distribution.
- The subset is chosen where refinement is expected to help, so the answering policy's
  advantage is measured on favourable ground by construction.

---

## Prompt for the live session — paste into Claude Code at the repo root

```
Read docs/superpowers/plans/2026-08-14-live-answer-policy.md in full, then run an
INTERACTIVE live-answer session for the six scenarios its section 1 names.

Setup:
- Use the PAID Gemini key, GEMINI_API_KEY_PAID, via the pattern in
  docs/superpowers/results/2026-08-10-gemini-paid-tier-run/paid_gemini_driver.py.
- Output under docs/superpowers/results/2026-08-14-live-answers/, one run dir per
  scenario, reusing the existing scenario configs and fixtures unchanged.
- Human answers come from ME, typed live. Use orchestrator/human_cli.py's real
  interactive HumanFns -- do NOT script, guess, autofill or "helpfully" draft answers,
  and do not import answer_policy_driver.py's texts. For 11a use the generate decision
  at the cap and for 11b the stop decision; ask me if the cap decision is unclear.
- Print each clarifying question exactly as the model produced it, with its
  issue_category and the requirement's current text, then wait for my input. One
  question at a time.
- Do not modify anything under orchestrator/, design/, or the prompts.

After the runs finish:
1. Write the extraction script (new file under the results dir) that builds answers.json
   in exactly the shape section 3 specifies, reading only the run records. Include
   source_runs, captured date, and the fallback block.
2. Write answering_policy_driver.py per section 4: pop-in-order lookup, verbatim fallback
   on a miss with a miss counter, stderr warning on question-text drift, decide_at_cap
   reused from the existing driver.
3. Verify the driver offline: replay against the just-captured runs and confirm zero
   misses and zero drift warnings. Report both counts.
4. Write SESSION.md: what was asked, what I answered, per-requirement outcomes, rounds,
   text-change rate, tokens and cost. Quote the final refined_text for every requirement
   next to its original, unjudged -- I will assess better/worse myself.

Report measured numbers only. If a run fails, stop and report rather than continuing.
```
