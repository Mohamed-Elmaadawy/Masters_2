"""The blinding tool (docs/EVALUATION_PROTOCOL.md section 6.1, "Procedure" steps 1-2):
pool every case from arms P, B1 and B2, strip arm identity, assign fresh opaque ids,
shuffle, and write the scoring file and the secret mapping to SEPARATE structures --
the mapping preserves enough to reconstruct arm, document and original case identity,
but the scoring file (what a rater opens first) carries neither.

Part B scoring itself (human judgement, B1-B5) is explicitly out of scope
("Part B is scored by hand and is not your work" -- handover Task 6) -- this module
stops at producing the two files.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import NamedTuple, Optional

from pydantic import BaseModel

from design.schemas import NonEmptyStr
from evaluation.atomic_io import atomic_write_json
from evaluation.mechanical_checks import Arm, PooledCase

EXIT_SUCCESS = 0
EXIT_CONFIG_ERROR = 2


class BlindedCase(BaseModel):
    """What a rater sees. No `arm`, no original `case.id`, no shuffle seed -- "strip
    arm identity" (protocol wording) means the PROVENANCE is hidden, not the case's
    own content: Part B's rubric (B1-B5) needs `requirement_ids`/`technique_used`/
    steps/etc to judge the case itself."""
    blind_id: NonEmptyStr
    doc_id: Optional[str] = None
    requirement_ids: list[NonEmptyStr]
    technique_used: str
    title: NonEmptyStr
    preconditions: Optional[str] = None
    steps: list[NonEmptyStr]
    expected_result: NonEmptyStr


class MappingEntry(BaseModel):
    """The secret: reconstructs arm, document and original case identity for one
    `blind_id`. Kept in a structure/file scoring never opens ("a separate file that is
    not opened until step 5"). Identity is `(arm, doc_id, original_case_id)`, not just
    `(arm, original_case_id)` (2026-08-17 fix) -- two different documents in the same
    arm may both legitimately contain a case id "TC-1"; only the triple is unique."""
    blind_id: NonEmptyStr
    arm: Arm
    doc_id: Optional[str] = None
    original_case_id: NonEmptyStr


class BlindingResult(BaseModel):
    scoring_file: list[BlindedCase]
    mapping_file: list[MappingEntry]
    # The exact seed that produced this shuffle, carried by construction (2026-08-17,
    # finding 4) -- `pool_and_blind` is the ONLY place a `BlindingResult` is normally
    # built, and it sets this from the very seed it used to build `random.Random`,
    # so there is no code path where this field could hold a seed other than the one
    # that actually produced `scoring_file`'s order. `write_blinding_result` reads
    # this field rather than taking a separate `seed` argument, which is what makes
    # "the seed recorded in the mapping is the exact seed that generated the shuffle"
    # true BY CONSTRUCTION rather than by caller discipline.
    seed: int

    def model_post_init(self, __context) -> None:
        # Same "two fields that must agree need something forcing it" pattern as
        # elsewhere in this project: the two files are produced together and must
        # stay the same size and share exactly the same blind_id set, or a scorer
        # and the eventual unblinding step would silently disagree about what exists.
        scoring_ids = [c.blind_id for c in self.scoring_file]
        mapping_ids = [m.blind_id for m in self.mapping_file]
        if len(scoring_ids) != len(set(scoring_ids)):
            raise ValueError(f"duplicate blind_id(s) in scoring_file: {scoring_ids}")
        if len(mapping_ids) != len(set(mapping_ids)):
            raise ValueError(f"duplicate blind_id(s) in mapping_file: {mapping_ids}")
        if len(scoring_ids) != len(mapping_ids):
            raise ValueError(
                f"scoring_file has {len(scoring_ids)} entries but mapping_file has "
                f"{len(mapping_ids)} -- cardinality must match exactly")
        if set(scoring_ids) != set(mapping_ids):
            raise ValueError(
                "scoring_file and mapping_file must carry exactly the same blind_id set; "
                f"scoring-only={set(scoring_ids) - set(mapping_ids)}, "
                f"mapping-only={set(mapping_ids) - set(scoring_ids)}")


def pool_and_blind(
    pooled_cases: list[PooledCase], seed: int, blind_id_prefix: str = "BLIND",
) -> BlindingResult:
    """Assigns fresh opaque ids and shuffles pooled_cases into two parallel outputs.

    `seed` is the ONLY randomness input (2026-08-17 fix): earlier this took an
    already-constructed `random.Random`, separately from `write_blinding_result`'s own
    `seed` argument -- nothing enforced that the two actually matched, so a caller
    could (accidentally or not) shuffle with one seed and record a DIFFERENT one in the
    mapping file, making the recorded seed a lie. `random.Random(seed)` is now
    constructed HERE, internally, and the same `seed` is carried on the returned
    `BlindingResult` for `write_blinding_result` to read -- there is exactly one seed
    value in the entire call chain, so a mismatch is not constructible.

    `pooled_cases` must be non-empty; blinding zero cases is a caller error, not a
    legitimate empty result.

    Identity is `(arm, doc_id, original_case_id)`, not `(arm, original_case_id)`
    (2026-08-17 fix): two different documents pooled into the same arm may legitimately
    both contain a case id "TC-1" -- only the triple, not the pair, is guaranteed
    unique. If the pool spans more than one distinct `doc_id`, EVERY entry must carry a
    real (non-`None`) `doc_id`, or the mapping's reconstruction becomes ambiguous for
    whichever entries left it unset -- rejected up front rather than silently blinding
    an ambiguous pool. A single-document pool (every entry sharing one `doc_id`,
    including all sharing `None`) has no such ambiguity and is unaffected."""
    if not pooled_cases:
        raise ValueError("pool_and_blind requires at least one case")

    distinct_doc_ids = {pc.doc_id for pc in pooled_cases}
    if len(distinct_doc_ids) > 1 and None in distinct_doc_ids:
        missing = [f"{pc.arm}/{pc.case.id}" for pc in pooled_cases if pc.doc_id is None]
        raise ValueError(
            "pooling cases from more than one document requires doc_id on EVERY entry, "
            "for unambiguous (arm, doc_id, original_case_id) reconstruction -- missing "
            f"doc_id on: {missing}")

    seen_by_arm_doc: dict = {}
    for pc in pooled_cases:
        key = (pc.arm, pc.doc_id)
        seen = seen_by_arm_doc.setdefault(key, set())
        if pc.case.id in seen:
            raise ValueError(
                f"duplicate case id {pc.case.id!r} within arm {pc.arm!r} doc_id "
                f"{pc.doc_id!r} -- identity must be unique per (arm, doc_id, "
                "original_case_id)")
        seen.add(pc.case.id)

    n = len(pooled_cases)
    width = len(str(n))
    blind_ids = [f"{blind_id_prefix}-{i:0{width}d}" for i in range(1, n + 1)]

    rng = random.Random(seed)
    shuffled_order = list(range(n))
    rng.shuffle(shuffled_order)

    scoring, mapping = [], []
    for blind_id, original_index in zip(blind_ids, shuffled_order):
        pc = pooled_cases[original_index]
        scoring.append(BlindedCase(
            blind_id=blind_id, doc_id=pc.doc_id, requirement_ids=pc.case.requirement_ids,
            technique_used=pc.case.technique_used.value, title=pc.case.title,
            preconditions=pc.case.preconditions, steps=pc.case.steps,
            expected_result=pc.case.expected_result))
        mapping.append(MappingEntry(
            blind_id=blind_id, arm=pc.arm, doc_id=pc.doc_id, original_case_id=pc.case.id))

    return BlindingResult(scoring_file=scoring, mapping_file=mapping, seed=seed)


class BlindingWritePaths(NamedTuple):
    scoring_path: Path
    mapping_path: Path


def write_blinding_result(
    result: BlindingResult, scoring_path: Path, mapping_path: Path,
) -> BlindingWritePaths:
    """Writes the two files a real blinding run produces, atomically, each. The
    shuffle seed (`result.seed` -- the exact seed `pool_and_blind` used, see
    `BlindingResult`'s own docstring) is recorded ONLY in the mapping file's metadata
    -- the scoring file must not carry anything that could help a rater (or anyone
    re-deriving the shuffle) recover provenance, and the seed is exactly that kind of
    information: an attacker (or an over-curious rater) with the seed and the scoring
    file's own order could reconstruct the pooling order the seed was applied to.

    `scoring_path` and `mapping_path` must be different files -- writing both to the
    same path would let whichever write happens second silently destroy the other,
    which defeats the entire point of a separate secret file.
    """
    if scoring_path == mapping_path:
        raise ValueError(
            f"scoring_path and mapping_path must be different files, both were {scoring_path}")

    scoring_payload = {"entries": [c.model_dump(mode="json") for c in result.scoring_file]}
    mapping_payload = {
        "seed": result.seed,
        "entries": [m.model_dump(mode="json") for m in result.mapping_file],
    }

    atomic_write_json(scoring_path, scoring_payload)
    atomic_write_json(mapping_path, mapping_payload)
    return BlindingWritePaths(scoring_path=scoring_path, mapping_path=mapping_path)


# ---------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------

def main(argv: list) -> int:
    parser = argparse.ArgumentParser(
        description="Pool P/B1/B2 test cases, strip arm identity, shuffle, and write "
                    "a scoring file and a separate secret mapping file.")
    parser.add_argument("pooled_cases", type=Path,
                        help="JSON file: a list of PooledCase objects "
                             '({"arm": ..., "doc_id": ..., "case": {...TestCase...}})')
    parser.add_argument("--scoring-output", type=Path, required=True)
    parser.add_argument("--mapping-output", type=Path, required=True)
    parser.add_argument("--seed", type=int, required=True,
                        help="Shuffle seed -- required, not defaulted, so every real "
                             "blinding run is reproducible and the seed used is always "
                             "on record (in the mapping file only).")
    args = parser.parse_args(argv)

    try:
        raw = json.loads(args.pooled_cases.read_text())
        pooled = [PooledCase.model_validate(item) for item in raw]
        result = pool_and_blind(pooled, seed=args.seed)
        write_blinding_result(result, args.scoring_output, args.mapping_output)
    except (OSError, ValueError) as e:
        print(f"Configuration/input error: {e}")
        return EXIT_CONFIG_ERROR

    print(f"{len(result.scoring_file)} case(s) blinded. "
         f"Scoring file: {args.scoring_output}. Mapping file (secret): {args.mapping_output}.")
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
