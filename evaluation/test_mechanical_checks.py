"""
Regression tests for evaluation/mechanical_checks.py (docs/EVALUATION_PROTOCOL.md
section 6.1, Part A). Run after any change:

    python -m evaluation.test_mechanical_checks

Plain script, no pytest, no network -- same convention as design/test_schemas.py.
"""

from __future__ import annotations

from design.schemas import Requirement, SystemType, TestCase
from evaluation.mechanical_checks import (
    ALL_ARMS, PooledCase, check_placeholder_contamination, check_technique_eligibility,
    check_traceability, count_duplicate_candidates, operator_system_type_map,
    run_part_a_checks, volume_per_requirement,
)

PASSED = 0
FAILED: list[str] = []


def ok(label: str, condition: bool = True) -> None:
    global PASSED
    if condition:
        PASSED += 1
    else:
        FAILED.append(label)
        print(f"    FAIL  {label}")


def section(name: str) -> None:
    print(f"\n{name}")


def case(id, requirement_ids, technique="boundary_value_analysis", title="t",
        steps=("s",), expected_result="e", preconditions=None) -> TestCase:
    return TestCase(id=id, requirement_ids=requirement_ids, technique_used=technique,
                    title=title, steps=list(steps), expected_result=expected_result,
                    preconditions=preconditions)


# ---------------------------------------------------------------------------------
# A1 -- Traceability
# ---------------------------------------------------------------------------------

section("A1 -- Traceability")
known = frozenset({"REQ-1", "REQ-2"})
pooled = [
    PooledCase(arm="P", case=case("TC-1", ["REQ-1"])),
    PooledCase(arm="B1", case=case("TC-2", ["REQ-1", "REQ-2"])),
    PooledCase(arm="B2", case=case("TC-3", ["REQ-1", "REQ-99"])),  # REQ-99 doesn't exist
]
violations = check_traceability(pooled, known)
ok("clean cases produce no violation", not any(v.case_id in ("TC-1", "TC-2") for v in violations))
ok("exactly one violation for the case naming an unknown id", len(violations) == 1)
ok("the violation names the unknown id, not the known one",
  violations[0].unknown_requirement_ids == ["REQ-99"])
ok("the violation records which arm produced it", violations[0].arm == "B2")
ok("a fully-known id set produces zero violations",
  check_traceability([PooledCase(arm="P", case=case("TC-4", ["REQ-1", "REQ-2"]))], known) == [])


# ---------------------------------------------------------------------------------
# A2 -- Technique eligibility (2026-08-17 fix: operator label, not Classifier output)
# ---------------------------------------------------------------------------------

section("A2 -- Technique eligibility uses the OPERATOR's label")
known2 = frozenset({"REQ-1", "REQ-2"})
operator_labels = {"REQ-1": SystemType.WEB, "REQ-2": SystemType.AI_SYSTEM}
pooled = [
    PooledCase(arm="P", case=case("TC-1", ["REQ-1"], technique="equivalence_partitioning")),
    PooledCase(arm="B1", case=case("TC-2", ["REQ-1"], technique="metamorphic")),  # ineligible
    PooledCase(arm="B2", case=case("TC-3", ["REQ-2"], technique="metamorphic")),  # eligible
]
result = check_technique_eligibility(pooled, known2, operator_labels)
ok("exactly one violation (the WEB requirement given an AI-only technique)",
  len(result.violations) == 1)
ok("the violation names the right case/requirement/technique/system_type",
  (result.violations[0].case_id, result.violations[0].requirement_id,
   result.violations[0].technique_used.value, result.violations[0].system_type) ==
  ("TC-2", "REQ-1", "metamorphic", SystemType.WEB))
ok("nothing is unlabeled when every referenced id has a real label",
  result.unlabeled_requirement_ids == [])

section("A2 -- a case naming two requirements with different system types is checked "
       "against EACH separately")
multi = PooledCase(arm="P", case=case("TC-5", ["REQ-1", "REQ-2"], technique="metamorphic"))
result = check_technique_eligibility([multi], known2, operator_labels)
ok("flagged for the WEB requirement (metamorphic ineligible there)",
  any(v.requirement_id == "REQ-1" for v in result.violations))
ok("NOT flagged for the AI_SYSTEM requirement (metamorphic is eligible there)",
  not any(v.requirement_id == "REQ-2" for v in result.violations))
ok("exactly one violation total for this case, not two", len(result.violations) == 1)

section("A2 -- a requirement present but genuinely unlabeled is REPORTED, not skipped")
labels_with_gap = {"REQ-1": SystemType.WEB, "REQ-2": None}  # operator hasn't labeled REQ-2 yet
pooled = [
    PooledCase(arm="P", case=case("TC-1", ["REQ-1"], technique="equivalence_partitioning")),
    PooledCase(arm="B1", case=case("TC-2", ["REQ-2"], technique="metamorphic")),
]
result = check_technique_eligibility(pooled, known2, labels_with_gap)
ok("no violation is raised for the unlabeled requirement (cannot check)",
  not any(v.requirement_id == "REQ-2" for v in result.violations))
ok("but the gap IS surfaced in unlabeled_requirement_ids, not silently dropped",
  result.unlabeled_requirement_ids == ["REQ-2"])

section("A2 -- a requirement id with NO entry at all in the mapping is REJECTED, "
       "not silently skipped (distinct from the genuinely-unlabeled case above) -- "
       "but ONLY when that id is actually known")
incomplete_mapping = {"REQ-1": SystemType.WEB}  # REQ-2 missing as a key entirely
pooled = [PooledCase(arm="P", case=case("TC-1", ["REQ-1", "REQ-2"]))]
try:
    check_technique_eligibility(pooled, known2, incomplete_mapping)
    ok("a KNOWN requirement id absent as a KEY raises ValueError", False)
except ValueError as e:
    ok("a KNOWN requirement id absent as a KEY raises ValueError", True)
    ok("the error names the missing id", "REQ-2" in str(e))

section("A2/A1 coexistence (2026-08-17, second fix): a case naming an id A1 already "
       "flags as UNKNOWN does not require a fabricated operator label for it -- A2 "
       "simply skips it, no rejection, no unlabeled entry either")
known_narrow = frozenset({"REQ-1"})  # REQ-99 is NOT in the known set at all
labels_covering_only_known = {"REQ-1": SystemType.WEB}  # no entry for REQ-99 -- and none needed
pooled = [
    PooledCase(arm="P", case=case("TC-1", ["REQ-1"], technique="equivalence_partitioning")),
    PooledCase(arm="B1", case=case("TC-2", ["REQ-99"], technique="metamorphic")),  # unknown id
]
result = check_technique_eligibility(pooled, known_narrow, labels_covering_only_known)
ok("no ValueError is raised even though REQ-99 has no entry in the label mapping at all",
  True)  # reaching this line at all is the assertion -- an exception would have aborted the script
ok("REQ-99 produces no eligibility violation (unchecked, not ineligible)",
  not any(v.requirement_id == "REQ-99" for v in result.violations))
ok("REQ-99 does NOT appear in unlabeled_requirement_ids either -- it isn't 'unlabeled', "
  "it's simply not a real requirement, which is a different, already-reported fact (A1's)",
  "REQ-99" not in result.unlabeled_requirement_ids)
ok("only the real, known requirement's eligibility is actually evaluated",
  result.unlabeled_requirement_ids == [] and result.violations == [])

section("A2 -- operator_system_type_map builds the mapping from RequirementRunRecord."
       "operator_system_type, never from .classification.system_type")


class _FakeClassification:
    def __init__(self, system_type):
        self.system_type = system_type


class _FakeRequirementRunRecord:
    """Stands in for design.schemas.RequirementRunRecord -- only the two attributes
    operator_system_type_map actually reads. A deliberately WRONG classification
    system_type is set on every record so the test would fail loudly if the map ever
    read the wrong field."""
    def __init__(self, req_id, operator_system_type):
        self.requirement = Requirement(id=req_id, text="irrelevant")
        self.operator_system_type = operator_system_type
        self.classification = _FakeClassification(SystemType.AI_SYSTEM)  # deliberately wrong


records = [
    _FakeRequirementRunRecord("REQ-1", SystemType.WEB),
    _FakeRequirementRunRecord("REQ-2", None),  # not labeled yet
]
built = operator_system_type_map(records, frozenset({"REQ-1", "REQ-2", "REQ-3"}))
ok("REQ-1's operator label is used, not its (deliberately wrong) classification",
  built["REQ-1"] == SystemType.WEB)
ok("REQ-2 with no operator label yet maps to None, not silently omitted",
  built["REQ-2"] is None)
ok("REQ-3 with no record at all still gets an entry (None), covering the full known set",
  built["REQ-3"] is None)
ok("the map covers exactly known_requirement_ids, no more, no less",
  set(built) == {"REQ-1", "REQ-2", "REQ-3"})


# ---------------------------------------------------------------------------------
# A3 -- Placeholder contamination (2026-08-17 fix: pre-registered keywords, not any bracket)
# ---------------------------------------------------------------------------------

section("A3 -- Placeholder contamination: positive cases (known placeholder shapes)")
positives = [
    PooledCase(arm="B1", case=case("TC-P1", ["REQ-1"], title="Check [TBD: threshold] is respected")),
    PooledCase(arm="B1", case=case("TC-P2", ["REQ-1"], title="t",
                                  steps=["verify [configurable timeout] elapsed"])),
    PooledCase(arm="B1", case=case("TC-P3", ["REQ-1"], title="t",
                                  preconditions="[FIXME: fill in setup]")),
    PooledCase(arm="B1", case=case("TC-P4", ["REQ-1"], title="[placeholder title]")),
    PooledCase(arm="B1", case=case("TC-P5", ["REQ-1"], title="t",
                                  expected_result="result is [TODO]")),
    # 2026-08-17, second fix: the real historically-observed placeholder
    # (AUTOGEN-US3, Known Limitation 11) is now pre-registered by exact phrase and
    # must be caught, not accepted as a known gap.
    PooledCase(arm="B1", case=case("TC-P6", ["REQ-1"], title="t",
                                  preconditions="user has [specified user needs]")),
]
violations = check_placeholder_contamination(positives)
flagged = {v.case_id for v in violations}
ok("[TBD: ...] is flagged", "TC-P1" in flagged)
ok("[configurable ...] is flagged", "TC-P2" in flagged)
ok("[FIXME: ...] is flagged", "TC-P3" in flagged)
ok("[placeholder ...] is flagged", "TC-P4" in flagged)
ok("[TODO] is flagged", "TC-P5" in flagged)
ok("[specified user needs] (the real observed instance) is flagged", "TC-P6" in flagged)
ok("all six known placeholder shapes are caught", len(flagged) == 6)
ok("case-insensitive match on the exact phrase too",
  check_placeholder_contamination([PooledCase(arm="P", case=case(
      "TC-CI", ["REQ-1"], title="t", preconditions="[SPECIFIED USER NEEDS]"))]) != [])

section("A3 -- Placeholder contamination: negative cases (legitimate bracket notation "
       "that must NOT be flagged -- this is what the old any-bracket rule got wrong)")
negatives = [
    PooledCase(arm="P", case=case("TC-N1", ["REQ-1"], title="Value is within [0,1]")),
    PooledCase(arm="P", case=case("TC-N2", ["REQ-1"], title="t",
                                 steps=["Press [Ctrl+C] to cancel"])),
    PooledCase(arm="P", case=case("TC-N3", ["REQ-1"], title="Covers [REQ-1]")),
    PooledCase(arm="P", case=case("TC-N4", ["REQ-1"], title="t",
                                 expected_result="Array contains [1, 2, 3]")),
    PooledCase(arm="P", case=case("TC-N5", ["REQ-1"], title="Login succeeds")),
]
violations = check_placeholder_contamination(negatives)
flagged = {v.case_id for v in violations}
ok("[0,1] (a value range) is NOT flagged", "TC-N1" not in flagged)
ok("[Ctrl+C] (a keystroke) is NOT flagged", "TC-N2" not in flagged)
ok("[REQ-1] (an id reference) is NOT flagged", "TC-N3" not in flagged)
ok("[1, 2, 3] (a literal array) is NOT flagged", "TC-N4" not in flagged)
ok("plain text with no brackets is NOT flagged", "TC-N5" not in flagged)
ok("zero false positives across every negative case", violations == [])

section("A3 -- still-open gap, stated: an UNREGISTERED placeholder phrase (neither a "
       "keyword nor an exact-registered phrase) is still not caught -- the tradeoff is "
       "narrower now, not eliminated")
unregistered = PooledCase(arm="B1", case=case("TC-U1", ["REQ-1"],
                                             preconditions="user has [unspecified future needs]"))
ok("a genuinely unregistered placeholder shape is still not flagged -- documented "
  "tradeoff, not a bug", check_placeholder_contamination([unregistered]) == [])


# ---------------------------------------------------------------------------------
# A4 -- Duplicate candidates
# ---------------------------------------------------------------------------------

section("A4 -- Duplicate candidates (advisory only)")
pooled = [
    PooledCase(arm="P", case=case("TC-1", ["REQ-1", "REQ-2"])),
    PooledCase(arm="B1", case=case("TC-2", ["REQ-2", "REQ-1"])),  # same set, different order
    PooledCase(arm="B2", case=case("TC-3", ["REQ-1"])),           # different set, no duplicate
]
groups = count_duplicate_candidates(pooled)
ok("exactly one duplicate group found", len(groups) == 1)
ok("the group's requirement_ids is order-insensitive", groups[0].requirement_ids == ["REQ-1", "REQ-2"])
ok("the group contains both cases, across arms", {c.case.id for c in groups[0].cases} == {"TC-1", "TC-2"})
ok("a singleton case-set produces no group", not any(
    "TC-3" in {c.case.id for c in g.cases} for g in groups))


# ---------------------------------------------------------------------------------
# A5 -- Volume (2026-08-17 fix: zero counts for every combination, omission stays visible)
# ---------------------------------------------------------------------------------

section("A5 -- Volume: every {P,B1,B2} x known-requirement-id combination is present")
known5 = frozenset({"REQ-1", "REQ-2"})
pooled = [
    PooledCase(arm="P", case=case("TC-1", ["REQ-1"])),
    PooledCase(arm="P", case=case("TC-2", ["REQ-1", "REQ-2"])),
    PooledCase(arm="B1", case=case("TC-3", ["REQ-1"])),
    # B2 produces NOTHING for either requirement -- must still show up as zero, not
    # be absent from the result entirely.
]
counts = volume_per_requirement(pooled, known5)
ok("P/REQ-1 counted twice (TC-1 and TC-2 both name it)", counts[("P", "REQ-1")] == 2)
ok("P/REQ-2 counted once", counts[("P", "REQ-2")] == 1)
ok("B1/REQ-1 counted once, separately from P's count", counts[("B1", "REQ-1")] == 1)
ok("B1/REQ-2 is explicitly zero, not absent from the dict",
  ("B1", "REQ-2") in counts and counts[("B1", "REQ-2")] == 0)
ok("B2/REQ-1 is explicitly zero -- B2 omitted this requirement entirely and that stays visible",
  ("B2", "REQ-1") in counts and counts[("B2", "REQ-1")] == 0)
ok("B2/REQ-2 is explicitly zero too", ("B2", "REQ-2") in counts and counts[("B2", "REQ-2")] == 0)
ok("the result has exactly len(ALL_ARMS) x len(known_requirement_ids) entries",
  len(counts) == len(ALL_ARMS) * len(known5))

section("A5 -- a case naming a requirement OUTSIDE known_requirement_ids does not "
       "silently grow the volume table (that is A1's finding, not A5's to count)")
foreign = [PooledCase(arm="P", case=case("TC-9", ["REQ-1", "REQ-FOREIGN"]))]
counts = volume_per_requirement(foreign, known5)
ok("only the known id is counted", counts[("P", "REQ-1")] == 1)
ok("the foreign id never appears as a key", not any(rid == "REQ-FOREIGN" for _, rid in counts))
ok("the table size is still exactly arms x known ids, unaffected by the foreign id",
  len(counts) == len(ALL_ARMS) * len(known5))


# ---------------------------------------------------------------------------------
# Combined report
# ---------------------------------------------------------------------------------

section("run_part_a_checks -- combined report shape")
report = run_part_a_checks(
    pooled_cases=[
        PooledCase(arm="P", case=case("TC-1", ["REQ-1"], technique="equivalence_partitioning")),
        PooledCase(arm="B1", case=case("TC-2", ["REQ-99"])),
    ],
    known_requirement_ids=frozenset({"REQ-1"}),
    # REQ-99 has NO entry at all -- and needs none, since it isn't in
    # known_requirement_ids (A1's finding alone, per the 2026-08-17 second fix).
    operator_system_type_by_requirement_id={"REQ-1": SystemType.WEB},
)
ok("traceability catches the foreign id", len(report.traceability_violations) == 1)
ok("technique_eligibility does not require or report REQ-99 at all -- it is unknown, "
  "not unlabeled", report.technique_eligibility.unlabeled_requirement_ids == [])
ok("volume dict uses JSON-safe string keys", "P::REQ-1" in report.volume_per_requirement)
ok("volume dict includes the zero entries too", "B2::REQ-1" in report.volume_per_requirement
  and report.volume_per_requirement["B2::REQ-1"] == 0)
ok("report round-trips through JSON", report.model_validate_json(report.model_dump_json()) == report)


print(f"\n{PASSED}/{PASSED + len(FAILED)} checks passed")
if FAILED:
    print("FAILED:")
    for label in FAILED:
        print(f"  - {label}")

import sys
sys.exit(0 if not FAILED else 1)
