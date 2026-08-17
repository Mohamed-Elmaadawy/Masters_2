"""
Regression tests for evaluation/blinding.py (docs/EVALUATION_PROTOCOL.md section 6.1,
"Procedure" steps 1-2). Run after any change:

    python -m evaluation.test_blinding

Plain script, no pytest, no network.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from design.schemas import TestCase
from evaluation.blinding import (
    BlindedCase, BlindingResult, MappingEntry, pool_and_blind, write_blinding_result,
)
from evaluation.mechanical_checks import PooledCase

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


def case(id, requirement_ids) -> TestCase:
    # Title/steps/expected_result deliberately do NOT embed `id` as substring text --
    # blinding strips the STRUCTURAL id/arm fields; it makes no promise about
    # scrubbing prose that happens to coincide with an id string, and a fixture that
    # accidentally does that would make the "no id leakage" test below meaningless
    # (it would be checking for accidental content, not the real guarantee).
    return TestCase(id=id, requirement_ids=requirement_ids, technique_used="boundary_value_analysis",
                    title="Login with valid credentials succeeds",
                    steps=["Enter valid username and password", "Submit the login form"],
                    expected_result="User is redirected to the dashboard")


POOLED = [
    PooledCase(arm="P", doc_id="DOC-1", case=case("P-TC-1", ["REQ-1"])),
    PooledCase(arm="P", doc_id="DOC-1", case=case("P-TC-2", ["REQ-2"])),
    PooledCase(arm="B1", doc_id="DOC-1", case=case("B1-TC-1", ["REQ-1"])),
    PooledCase(arm="B1", doc_id="DOC-1", case=case("B1-TC-2", ["REQ-2"])),
    PooledCase(arm="B2", doc_id="DOC-1", case=case("B2-TC-1", ["REQ-1"])),
]


section("Separation -- scoring file carries no arm identity")
result = pool_and_blind(POOLED, seed=1)
ok("scoring_file has one entry per pooled case", len(result.scoring_file) == len(POOLED))
ok("mapping_file has one entry per pooled case", len(result.mapping_file) == len(POOLED))
ok("BlindedCase has no 'arm' attribute at all", not hasattr(result.scoring_file[0], "arm"))
ok("BlindedCase has no 'case_id'/original id field",
  not hasattr(result.scoring_file[0], "case_id") and not hasattr(result.scoring_file[0], "original_case_id"))
ok("BlindedCase has no 'seed' field", not hasattr(result.scoring_file[0], "seed"))
ok("scoring_file content is drawn from the real cases (title text is preserved)",
  {c.title for c in result.scoring_file} == {pc.case.title for pc in POOLED})

section("Reconstruction -- the mapping file alone can recover arm + doc + original identity")
blind_to_original = {m.blind_id: (m.arm, m.doc_id, m.original_case_id) for m in result.mapping_file}
reconstructed = {blind_to_original[m.blind_id] for m in result.mapping_file}
expected = {(pc.arm, pc.doc_id, pc.case.id) for pc in POOLED}
ok("every (arm, doc_id, original_case_id) triple is recoverable from the mapping alone",
  reconstructed == expected)
ok("doc_id is preserved in the mapping for provenance",
  all(m.doc_id == "DOC-1" for m in result.mapping_file))

section("Fresh opaque ids -- blind_id reveals nothing about original id or order")
ok("blind ids do not contain any original case id as a substring",
  all(pc.case.id not in m.blind_id for pc, m in zip(POOLED, result.mapping_file)))
ok("blind ids are unique", len({m.blind_id for m in result.mapping_file}) == len(POOLED))

section("Shuffle -- scoring_file order is not the pooling order (deterministic with a seed)")
original_order = [pc.case.id for pc in POOLED]
scoring_original_order = [blind_to_original[c.blind_id][2] for c in result.scoring_file]
ok("scoring_file order differs from the original pooling order for this seed",
  scoring_original_order != original_order)

section("Determinism -- same seed produces the same shuffle and the same blind ids")
result_again = pool_and_blind(POOLED, seed=1)
ok("re-running with the same seed reproduces the exact scoring_file order",
  [c.blind_id for c in result.scoring_file] == [c.blind_id for c in result_again.scoring_file])
ok("re-running with the same seed reproduces the exact mapping",
  [(m.blind_id, m.arm, m.doc_id, m.original_case_id) for m in result.mapping_file] ==
  [(m.blind_id, m.arm, m.doc_id, m.original_case_id) for m in result_again.mapping_file])

section("A different seed produces a different shuffle (not hardcoded/no-op)")
result_diff = pool_and_blind(POOLED, seed=2)
ok("a different seed changes the scoring_file order",
  [c.blind_id for c in result.scoring_file] != [c.blind_id for c in result_diff.scoring_file]
  or [blind_to_original[c.blind_id] for c in result.scoring_file] !=
     [{m.blind_id: (m.arm, m.doc_id, m.original_case_id) for m in result_diff.mapping_file}[c.blind_id]
      for c in result_diff.scoring_file])

section("Guard rails -- pool_and_blind")
try:
    pool_and_blind([], seed=1)
    ok("blinding zero cases raises", False)
except ValueError:
    ok("blinding zero cases raises", True)

single = pool_and_blind([POOLED[0]], seed=1)
ok("a single case still produces one scoring entry and one mapping entry",
  len(single.scoring_file) == 1 and len(single.mapping_file) == 1)


# ---------------------------------------------------------------------------------
# Finding 3 (2026-08-17, second fix): identity is (arm, doc_id, original_case_id),
# not (arm, original_case_id) -- two documents may share a case id in the same arm.
# ---------------------------------------------------------------------------------

section("Cross-document identity -- the SAME case id in the SAME arm but DIFFERENT "
       "documents is legitimate, not a duplicate")
cross_doc = [
    PooledCase(arm="P", doc_id="DOC-1", case=case("TC-1", ["REQ-1"])),
    PooledCase(arm="P", doc_id="DOC-2", case=case("TC-1", ["REQ-1"])),  # same id, different doc
]
cross_result = pool_and_blind(cross_doc, seed=1)
ok("both cases are blinded, no rejection", len(cross_result.scoring_file) == 2)
ok("the mapping distinguishes them by doc_id, not just arm+id",
  {(m.arm, m.doc_id, m.original_case_id) for m in cross_result.mapping_file} ==
  {("P", "DOC-1", "TC-1"), ("P", "DOC-2", "TC-1")})
ok("each gets its own distinct blind_id",
  len({m.blind_id for m in cross_result.mapping_file}) == 2)

section("Cross-document identity -- a TRUE duplicate (same arm, same doc_id, same "
       "case id) is still rejected")
true_dup = [
    PooledCase(arm="P", doc_id="DOC-1", case=case("TC-1", ["REQ-1"])),
    PooledCase(arm="P", doc_id="DOC-1", case=case("TC-1", ["REQ-2"])),  # identical triple
]
try:
    pool_and_blind(true_dup, seed=1)
    ok("a true (arm, doc_id, id) duplicate raises", False)
except ValueError as e:
    ok("a true (arm, doc_id, id) duplicate raises", True)
    ok("the error names the duplicated id", "TC-1" in str(e))

section("Cross-document identity -- the SAME id across DIFFERENT arms, same doc, "
       "is still fine (arm is still part of the composite key)")
same_id_diff_arms = [
    PooledCase(arm="P", doc_id="DOC-1", case=case("TC-1", ["REQ-1"])),
    PooledCase(arm="B1", doc_id="DOC-1", case=case("TC-1", ["REQ-1"])),
]
ok("same id across two different arms in the same document does not raise",
  len(pool_and_blind(same_id_diff_arms, seed=1).scoring_file) == 2)

section("Cross-document identity -- pooling more than one document REQUIRES doc_id "
       "on every entry; a mix of a real doc_id and None is rejected as ambiguous")
mixed_doc_ids = [
    PooledCase(arm="P", doc_id="DOC-1", case=case("TC-1", ["REQ-1"])),
    PooledCase(arm="P", doc_id=None, case=case("TC-2", ["REQ-1"])),  # no doc_id at all
]
try:
    pool_and_blind(mixed_doc_ids, seed=1)
    ok("a multi-document pool with a missing doc_id raises", False)
except ValueError as e:
    ok("a multi-document pool with a missing doc_id raises", True)
    ok("the error names the entry missing doc_id", "TC-2" in str(e))

section("Cross-document identity -- a SINGLE-document pool where every entry shares "
       "doc_id=None is fine (no ambiguity, nothing to disambiguate against)")
all_none = [
    PooledCase(arm="P", doc_id=None, case=case("TC-1", ["REQ-1"])),
    PooledCase(arm="B1", doc_id=None, case=case("TC-2", ["REQ-1"])),
]
ok("a single-document pool with doc_id=None throughout does not raise",
  len(pool_and_blind(all_none, seed=1).scoring_file) == 2)

section("Guard rails -- BlindingResult itself rejects mismatched scoring/mapping "
       "cardinality and blind_id sets (constructed directly, bypassing pool_and_blind)")
try:
    BlindingResult(
        scoring_file=[BlindedCase(blind_id="B-1", requirement_ids=["REQ-1"],
                                  technique_used="boundary_value_analysis", title="t",
                                  steps=["s"], expected_result="e")],
        mapping_file=[
            MappingEntry(blind_id="B-1", arm="P", original_case_id="TC-1"),
            MappingEntry(blind_id="B-2", arm="P", original_case_id="TC-2"),
        ],
        seed=1)
    ok("mismatched cardinality (1 scoring vs 2 mapping) raises", False)
except ValueError as e:
    ok("mismatched cardinality (1 scoring vs 2 mapping) raises", True)
    ok("the error names cardinality, not something unrelated", "cardinality" in str(e))

try:
    BlindingResult(
        scoring_file=[
            BlindedCase(blind_id="B-1", requirement_ids=["REQ-1"],
                       technique_used="boundary_value_analysis", title="t",
                       steps=["s"], expected_result="e"),
            BlindedCase(blind_id="B-2", requirement_ids=["REQ-1"],
                       technique_used="boundary_value_analysis", title="t",
                       steps=["s"], expected_result="e"),
        ],
        mapping_file=[
            MappingEntry(blind_id="B-1", arm="P", original_case_id="TC-1"),
            MappingEntry(blind_id="B-3", arm="P", original_case_id="TC-2"),  # id mismatch
        ],
        seed=1)
    ok("equal cardinality but disjoint blind_id sets still raises", False)
except ValueError as e:
    ok("equal cardinality but disjoint blind_id sets still raises", True)


# ---------------------------------------------------------------------------------
# Finding 4 (2026-08-17, second fix): the seed recorded in the mapping is the exact
# seed that generated the shuffle, BY CONSTRUCTION -- no independent rng/seed inputs.
# ---------------------------------------------------------------------------------

section("Seed truthfulness -- BlindingResult carries the exact seed pool_and_blind used")
r7 = pool_and_blind(POOLED, seed=7)
ok("result.seed equals the seed actually passed", r7.seed == 7)
r99 = pool_and_blind(POOLED, seed=99)
ok("a different call with a different seed carries THAT seed", r99.seed == 99)
ok("two different seeds are not accidentally equal on the result (sanity)", r7.seed != r99.seed)

section("Seed truthfulness -- there is no parameter anywhere for write_blinding_result "
       "to receive a DIFFERENT seed than the one that produced the shuffle")
import inspect  # noqa: E402
sig = inspect.signature(write_blinding_result)
ok("write_blinding_result has no 'seed' parameter at all -- it can only read "
  "result.seed, never be handed a different one", "seed" not in sig.parameters)


# ---------------------------------------------------------------------------------
# write_blinding_result -- operational writer/CLI
# ---------------------------------------------------------------------------------

section("write_blinding_result -- separate paths mandatory")
with tempfile.TemporaryDirectory() as tmp:
    same_path = Path(tmp) / "same.json"
    try:
        write_blinding_result(result, same_path, same_path)
        ok("writing scoring and mapping to the SAME path raises", False)
    except ValueError as e:
        ok("writing scoring and mapping to the SAME path raises", True)
        ok("no file was written before the rejection", not same_path.exists())

section("write_blinding_result -- writes two real, separate, valid files")
with tempfile.TemporaryDirectory() as tmp:
    scoring_path = Path(tmp) / "scoring.json"
    mapping_path = Path(tmp) / "mapping.json"
    result42 = pool_and_blind(POOLED, seed=42)
    paths = write_blinding_result(result42, scoring_path, mapping_path)
    ok("returns the two paths it wrote", paths.scoring_path == scoring_path
      and paths.mapping_path == mapping_path)
    ok("scoring file exists on disk", scoring_path.exists())
    ok("mapping file exists on disk", mapping_path.exists())
    ok("no leftover .tmp file after a successful write",
      not (Path(tmp) / (scoring_path.name + ".tmp")).exists()
      and not (Path(tmp) / (mapping_path.name + ".tmp")).exists())

    scoring_data = json.loads(scoring_path.read_text())
    mapping_data = json.loads(mapping_path.read_text())
    ok("scoring file has the right entry count", len(scoring_data["entries"]) == len(POOLED))
    ok("mapping file has the right entry count", len(mapping_data["entries"]) == len(POOLED))

    section("write_blinding_result -- seed recorded ONLY in the mapping file, and it "
           "is the exact seed pool_and_blind used")
    ok("mapping file's top-level metadata carries the exact seed used (42)",
      mapping_data["seed"] == 42)
    ok("scoring file has NO top-level 'seed' key at all", "seed" not in scoring_data)

    section("write_blinding_result -- scoring artifact provably contains no arm/id/"
           "seed/mapping data")
    scoring_raw_text = scoring_path.read_text()
    ok("scoring file's raw text does not contain the literal string 'seed'",
      "seed" not in scoring_raw_text)
    ok("scoring file's raw text does not contain the literal string 'arm'",
      "arm" not in scoring_raw_text)
    ok("scoring file's raw text does not contain any original case id",
      all(pc.case.id not in scoring_raw_text for pc in POOLED))
    ok("scoring file's raw text does not contain any original_case_id key",
      "original_case_id" not in scoring_raw_text)
    ok("every scoring entry key is from the exact allowed set (no smuggled field)",
      all(set(entry) <= {"blind_id", "doc_id", "requirement_ids", "technique_used",
                         "title", "preconditions", "steps", "expected_result"}
          for entry in scoring_data["entries"]))

    section("write_blinding_result -- atomicity: a write that fails partway never "
           "leaves a half-written file at the real path")
    before_scoring = scoring_path.read_text()
    nonexistent_dir_path = Path(tmp) / "does-not-exist" / "mapping.json"
    try:
        write_blinding_result(result42, scoring_path, nonexistent_dir_path)
        ok("writing to a nonexistent directory raises", False)
    except OSError:
        ok("writing to a nonexistent directory raises", True)
    ok("the real mapping file was not left in a half-written state at the failed path",
      not nonexistent_dir_path.exists())
    ok("the scoring file is still valid, complete JSON after the failed mapping write",
      len(json.loads(scoring_path.read_text())["entries"]) == len(POOLED))


print(f"\n{PASSED}/{PASSED + len(FAILED)} checks passed")
if FAILED:
    print("FAILED:")
    for label in FAILED:
        print(f"  - {label}")

import sys
sys.exit(0 if not FAILED else 1)
