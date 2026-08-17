"""Part A mechanical checks (docs/EVALUATION_PROTOCOL.md section 6.1, "computed, no
rater"), run over the pooled test cases from arms P, B1 and B2 for one document.

Every field name below is checked against `design/schemas.py`'s actual `TestCase`
before use, per the protocol's own instruction ("Confirm every field name against
design/schemas.py before implementing; these describe properties, not an assumed
schema") -- `id`, `requirement_ids`, `technique_used`, `title`, `preconditions`,
`steps`, `expected_result` all exist there exactly as read here.

A2 (technique eligibility) needs a `SystemType` per requirement to check
`ELIGIBLE_TECHNIQUES` against. B1/B2 run no Classifier -- that IS the treatment being
withheld (docs/EVALUATION_PROTOCOL.md section 3) -- so this module never asks a
baseline for one, and it never reads arm P's `Classification` output either (2026-08-17
fix: an earlier version of this module used arm P's Classifier output here, which is
wrong -- the Classifier's own accuracy is itself something the evaluation measures
(docs/EVALUATION_PROTOCOL.md section 6, "Classifier: accuracy against the operator's
label"), so scoring A2 against it would score every arm against a label that might
itself be wrong, and silently launder Classifier error into every arm's eligibility
rate equally). The correct shared reference is the OPERATOR's own label
(`design/schemas.py`'s `RequirementRunRecord.operator_system_type`, captured via
`orchestrator/cli.py`'s `label-system-type` subcommand, S2) -- independently captured
ground truth, not a stage output. `check_technique_eligibility` takes
`known_requirement_ids` AND `operator_system_type_by_requirement_id: dict[str,
Optional[SystemType]]`.

A1/A2 coexistence (2026-08-17, second fix): an id A1 already flags as unknown (not in
`known_requirement_ids` at all) is SKIPPED by A2, not required to have a label -- a
case naming a nonexistent requirement is A1's finding alone, and forcing a caller to
fabricate an operator label for an id that was never a real requirement would be
inventing ground truth for something that doesn't exist. For every id that IS known,
`operator_system_type_by_requirement_id` must have an entry: missing entirely is a
caller/config error (raises `ValueError` -- the mapping was not built to cover the
pool being scored); present with value `None` means the operator genuinely has not
labeled it yet, reported in `unlabeled_requirement_ids`, never silently skipped.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Literal, Optional

from pydantic import BaseModel, Field

from design.schemas import ELIGIBLE_TECHNIQUES, NonEmptyStr, SystemType, TestCase, TestTechnique

Arm = Literal["P", "B1", "B2"]
ALL_ARMS: tuple[Arm, ...] = ("P", "B1", "B2")


class PooledCase(BaseModel):
    """One test case, tagged with which arm produced it -- the unit A1-A5 and the
    blinding tool (evaluation/blinding.py) both operate over. `doc_id` is optional and
    carried through only for provenance once cases from more than one document are
    pooled together at the full-evaluation scale; A1-A5 themselves are scoped to one
    document's `known_requirement_ids`/`operator_system_type_by_requirement_id` per
    call and never read this field."""
    arm: Arm
    doc_id: Optional[str] = None
    case: TestCase


# ---------------------------------------------------------------------------------
# A1 -- Traceability
# ---------------------------------------------------------------------------------

class TraceabilityViolation(BaseModel):
    arm: Arm
    case_id: NonEmptyStr
    unknown_requirement_ids: list[NonEmptyStr] = Field(..., min_length=1)


def check_traceability(
    pooled_cases: list[PooledCase], known_requirement_ids: frozenset,
) -> list[TraceabilityViolation]:
    """A1: every id in `requirement_ids` resolves to a requirement in the input set.
    `known_requirement_ids` is the input `RequirementSet`'s own ids (plus derived
    fragment ids, once S4 lands -- not built, see design/DESIGN_NOTES.md's S4
    deferral entry, 2026-08-17)."""
    violations = []
    for pc in pooled_cases:
        unknown = [rid for rid in pc.case.requirement_ids if rid not in known_requirement_ids]
        if unknown:
            violations.append(TraceabilityViolation(
                arm=pc.arm, case_id=pc.case.id, unknown_requirement_ids=unknown))
    return violations


# ---------------------------------------------------------------------------------
# A2 -- Technique eligibility
# ---------------------------------------------------------------------------------

class TechniqueEligibilityViolation(BaseModel):
    arm: Arm
    case_id: NonEmptyStr
    requirement_id: NonEmptyStr
    system_type: SystemType
    technique_used: TestTechnique


class TechniqueEligibilityResult(BaseModel):
    violations: list[TechniqueEligibilityViolation]
    # Requirement ids referenced by at least one pooled case, where the operator has
    # genuinely not captured a system-type label yet (present in the input mapping
    # with value None) -- reported, not silently dropped from the result.
    unlabeled_requirement_ids: list[NonEmptyStr]


def check_technique_eligibility(
    pooled_cases: list[PooledCase],
    known_requirement_ids: frozenset,
    operator_system_type_by_requirement_id: dict,
) -> TechniqueEligibilityResult:
    """A2: the declared technique must be in `ELIGIBLE_TECHNIQUES` for each named
    requirement's OPERATOR-labeled `SystemType` (see this module's docstring for why
    it is the operator's label, not the Classifier's). A case naming several
    requirements is checked against EACH of their system types separately -- eligible
    for one, ineligible for another, is a real, reportable outcome, not collapsed into
    a single pass/fail.

    A requirement id NOT in `known_requirement_ids` is skipped entirely here -- A1
    already reports it as unknown, and this function must not additionally demand an
    operator label for a requirement that was never real (see this module's docstring,
    "A1/A2 coexistence"). For every id that IS known, `operator_system_type_by_
    requirement_id` MUST have an entry -- a missing key is rejected outright
    (`ValueError`), not treated as "unlabeled", because it means the mapping itself was
    built wrong (it was not made to cover the known set), which is a different, worse
    problem than a requirement the operator genuinely hasn't gotten to yet (value
    `None`, which IS accepted and reported in `unlabeled_requirement_ids`)."""
    referenced_known = {
        rid for pc in pooled_cases for rid in pc.case.requirement_ids
        if rid in known_requirement_ids}
    missing_keys = referenced_known - set(operator_system_type_by_requirement_id)
    if missing_keys:
        raise ValueError(
            "operator_system_type_by_requirement_id has no entry at all for known "
            f"requirement id(s) {sorted(missing_keys)} -- every KNOWN requirement id "
            "referenced by a pooled case must be a key (value None if the operator "
            "genuinely has not labeled it yet)")

    violations = []
    unlabeled: set = set()
    for pc in pooled_cases:
        for rid in pc.case.requirement_ids:
            if rid not in known_requirement_ids:
                continue  # A1's finding, not A2's -- no label required for a nonexistent id
            system_type = operator_system_type_by_requirement_id[rid]
            if system_type is None:
                unlabeled.add(rid)
                continue
            if pc.case.technique_used not in ELIGIBLE_TECHNIQUES[system_type]:
                violations.append(TechniqueEligibilityViolation(
                    arm=pc.arm, case_id=pc.case.id, requirement_id=rid,
                    system_type=system_type, technique_used=pc.case.technique_used))
    return TechniqueEligibilityResult(
        violations=violations, unlabeled_requirement_ids=sorted(unlabeled))


def operator_system_type_map(
    requirement_records, known_requirement_ids: frozenset,
) -> dict:
    """Builds the `operator_system_type_by_requirement_id` mapping `check_technique_
    eligibility` requires, from a real run's `RequirementRunRecord`s (e.g.
    `orchestrator.pipeline.read_document_run(run_dir).requirement_records`) --
    reading `.operator_system_type`, never `.classification.system_type`. Every id in
    `known_requirement_ids` gets an entry (`None` if that requirement has no record or
    no label yet), so the result always covers the whole set -- `check_technique_
    eligibility`'s missing-key rejection can only fire on a caller error, never on a
    requirement that is simply unlabeled."""
    by_id = {r.requirement.id: r.operator_system_type for r in requirement_records}
    return {rid: by_id.get(rid) for rid in known_requirement_ids}


# ---------------------------------------------------------------------------------
# A3 -- Placeholder contamination
# ---------------------------------------------------------------------------------

# Pre-registered, not broad: two fixed, documented lists, matched only inside square
# brackets. Replaces an earlier "any [...] span" rule (2026-08-17 fix) that flagged
# legitimate bracket notation with no relation to an unresolved placeholder --
# `[0,1]` (a value range), `[Ctrl+C]` (a keystroke), `[REQ-1]` (an id reference),
# `[1, 2, 3]` (a literal array) all matched the old rule and are all real, defensible
# test-case text. A MECHANICAL check (docs/EVALUATION_PROTOCOL.md section 6.1: "Part A
# is mechanical and unarguable") must not produce an arguable false positive, so this
# still trades recall for precision on purpose -- but "pre-registered" is not the same
# as "keyword-only": a real historically-observed placeholder can be registered by its
# exact bracket content even when it shares no keyword with the others.
#
# `_PLACEHOLDER_EXACT_PHRASES` (2026-08-17, second fix) registers `[specified user
# needs]` (AUTOGEN-US3, design/DESIGN_NOTES.md Known Limitation 11) individually, by
# its exact text -- it contains none of the keywords below, so the keyword rule alone
# cannot catch it, and it is not similar enough to `[Ctrl+C]`/`[REQ-1]`/`[0,1]`/
# `[1, 2, 3]` for any single general pattern to separate it from them without either
# missing it or flagging those. Adding a new exact phrase here requires a new dated
# citation of a real observed instance, same discipline as the keyword list --
# neither list grows from guessing what MIGHT be a placeholder.
_PLACEHOLDER_KEYWORDS = ("tbd", "todo", "fixme", "configurable", "placeholder")
_PLACEHOLDER_EXACT_PHRASES = ("specified user needs",)
_PLACEHOLDER_RE = re.compile(
    r"\[[^\[\]]*\b(?:" + "|".join(_PLACEHOLDER_KEYWORDS) + r")\b[^\[\]]*\]"
    r"|\[(?:" + "|".join(re.escape(p) for p in _PLACEHOLDER_EXACT_PHRASES) + r")\]",
    re.IGNORECASE)


class PlaceholderViolation(BaseModel):
    arm: Arm
    case_id: NonEmptyStr
    field: str
    spans: list[str] = Field(..., min_length=1)


def _placeholder_spans(text: Optional[str]) -> list[str]:
    if text is None:
        return []
    return _PLACEHOLDER_RE.findall(text)


def check_placeholder_contamination(pooled_cases: list[PooledCase]) -> list[PlaceholderViolation]:
    """A3: no pre-registered unresolved-placeholder marker in any of a case's text
    fields. See the module-level `_PLACEHOLDER_RE` comment for exactly what is and is
    not matched, and why."""
    violations = []
    for pc in pooled_cases:
        fields = {
            "title": pc.case.title,
            "preconditions": pc.case.preconditions,
            "expected_result": pc.case.expected_result,
            **{f"steps[{i}]": s for i, s in enumerate(pc.case.steps)},
        }
        for field_name, text in fields.items():
            spans = _placeholder_spans(text)
            if spans:
                violations.append(PlaceholderViolation(
                    arm=pc.arm, case_id=pc.case.id, field=field_name, spans=spans))
    return violations


# ---------------------------------------------------------------------------------
# A4 -- Duplicate candidates (advisory only)
# ---------------------------------------------------------------------------------

class DuplicateGroup(BaseModel):
    """Advisory only -- see docs/EVALUATION_PROTOCOL.md section 6.1 A4 and
    design/DESIGN_NOTES.md's declined-list entry for test-case de-duplication
    (Known Limitation 1): never merged, never deleted, by any code in this
    package."""
    requirement_ids: list[NonEmptyStr] = Field(..., min_length=1)
    cases: list[PooledCase] = Field(..., min_length=2)


def count_duplicate_candidates(pooled_cases: list[PooledCase]) -> list[DuplicateGroup]:
    """A4: cases whose `requirement_ids` sets are identical, grouped. Order-insensitive
    (a case naming [REQ-1, REQ-2] and one naming [REQ-2, REQ-1] are the same
    candidate set) but arm-insensitive too -- a suspected duplicate across P and a
    baseline is exactly as reportable as one within a single arm, since both are
    "two cases claiming to cover the same requirement combination"."""
    groups: dict[frozenset, list[PooledCase]] = defaultdict(list)
    for pc in pooled_cases:
        groups[frozenset(pc.case.requirement_ids)].append(pc)
    return [
        DuplicateGroup(requirement_ids=sorted(key), cases=cases)
        for key, cases in groups.items() if len(cases) > 1
    ]


# ---------------------------------------------------------------------------------
# A5 -- Volume
# ---------------------------------------------------------------------------------

def volume_per_requirement(
    pooled_cases: list[PooledCase], known_requirement_ids: frozenset,
    arms: tuple = ALL_ARMS,
) -> dict:
    """A5: cases per requirement, per arm -- EVERY `(arm, requirement_id)` combination
    from `arms x known_requirement_ids`, not just the ones with at least one case.
    2026-08-17 fix: the previous version only emitted entries that had >=1 case, so a
    baseline that silently omitted an entire requirement (arguably the single most
    important A5 finding -- "this arm didn't even attempt this requirement") was
    indistinguishable from "this combination was never checked". A case naming
    several requirements counts toward each of them -- "how many cases at least
    partly cover this requirement, in this arm"."""
    counts: dict[tuple, int] = {(arm, rid): 0 for arm in arms for rid in known_requirement_ids}
    for pc in pooled_cases:
        for rid in pc.case.requirement_ids:
            key = (pc.arm, rid)
            if key in counts:  # a foreign/unknown id is A1's finding, not A5's to count
                counts[key] += 1
    return counts


# ---------------------------------------------------------------------------------
# Combined report
# ---------------------------------------------------------------------------------

class MechanicalReport(BaseModel):
    traceability_violations: list[TraceabilityViolation]
    technique_eligibility: TechniqueEligibilityResult
    placeholder_violations: list[PlaceholderViolation]
    duplicate_groups: list[DuplicateGroup]
    volume_per_requirement: dict[str, int]  # "{arm}::{requirement_id}" -> count, JSON-key-safe


def run_part_a_checks(
    pooled_cases: list[PooledCase], known_requirement_ids: frozenset,
    operator_system_type_by_requirement_id: dict,
) -> MechanicalReport:
    volume = volume_per_requirement(pooled_cases, known_requirement_ids)
    return MechanicalReport(
        traceability_violations=check_traceability(pooled_cases, known_requirement_ids),
        technique_eligibility=check_technique_eligibility(
            pooled_cases, known_requirement_ids, operator_system_type_by_requirement_id),
        placeholder_violations=check_placeholder_contamination(pooled_cases),
        duplicate_groups=count_duplicate_candidates(pooled_cases),
        volume_per_requirement={f"{arm}::{rid}": count for (arm, rid), count in volume.items()},
    )
