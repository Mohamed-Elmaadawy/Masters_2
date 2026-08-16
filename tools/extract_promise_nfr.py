"""S1 (design/DESIGN_NOTES.md, "System changes to make before the evaluation freeze"):
extract the PROMISE NFR dataset (`datasets/EVALUATION_DATASETS.md`, item 2) into
`RequirementSet` JSON, one per `ProjectID`.

    python -m tools.extract_promise_nfr datasets/promise-nfr/promise_nfr.arff datasets/promise-nfr-extracted

The ARFF's own `@DATA` rows are already one requirement sentence per row -- like
Dalpiaz, no boundary judgement call to make. `ProjectID` (1-15) is the closest thing
this flat dataset has to a "document", so one `RequirementSet` is built per project id,
in row order.

The third ARFF column (`class`: F/A/L/LF/MN/O/PE/SC/SE/US/FT/PO -- functional vs.
labeled non-functional subcategories, Cleland-Huang et al.'s own taxonomy) is real
ground truth, but it is NOT `design.schemas.IssueCategory` or `SystemType` -- neither
this project's taxonomy nor a mapping between the two is decided anywhere. It is kept
in the manifest sidecar only (same "provenance lives beside the RequirementSet, not
inside it" pattern as tools/extract_pure_xml.py), available for whichever evaluation
question ends up wanting it, without silently asserting it means something in this
pipeline's own vocabulary.

Encoding: the file is Windows-1252, not UTF-8 (verified 2026-08-16 -- it contains
curly quotes/em-dashes that raise UnicodeDecodeError under utf-8). Parsed with the
stdlib `csv` module (`quotechar="'"`, matching the ARFF string-literal convention),
not a hand-rolled comma-split -- several `RequirementText` values contain internal
commas.

One file per project:

    promise-<project_id>.json           a RequirementSet, validated before writing
    promise-<project_id>.manifest.json  each requirement's original `class` label
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from design.schemas import Requirement, RequirementSet

EXTRACTOR_VERSION = 1


def doc_id_for(project_id: str) -> str:
    return f"promise-{project_id}"


def requirement_id_for(doc_id: str, position: int) -> str:
    return f"{doc_id.upper()}-{position + 1:04d}"


def read_rows(arff_path: Path) -> list[tuple[str, str, str]]:
    """Returns (project_id, requirement_text, class_label) tuples, in file order,
    skipping any row that is blank or doesn't parse to exactly 3 fields (none found in
    the real file as of 2026-08-16, but a malformed row must be visible, not silently
    dropped, if the file is ever re-fetched and differs)."""
    raw = arff_path.read_bytes().decode("cp1252")
    lines = raw.splitlines()
    data_start = next(i for i, line in enumerate(lines) if line.strip().upper() == "@DATA") + 1
    reader = csv.reader(lines[data_start:], quotechar="'", skipinitialspace=True)
    rows = []
    malformed = []
    for row in reader:
        if not row or not any(field.strip() for field in row):
            continue
        if len(row) != 3:
            malformed.append(row)
            continue
        rows.append((row[0].strip(), row[1].strip(), row[2].strip()))
    if malformed:
        raise ValueError(f"{arff_path}: {len(malformed)} row(s) did not parse to exactly "
                         f"3 fields, refusing to silently drop them: {malformed[:3]}")
    return rows


def extract_file(arff_path: Path) -> tuple[list[RequirementSet], list[dict]]:
    rows = read_rows(arff_path)
    by_project: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for project_id, text, class_label in rows:
        by_project[project_id].append((text, class_label))

    requirement_sets = []
    manifests = []
    for project_id in sorted(by_project, key=lambda p: int(p)):
        doc_id = doc_id_for(project_id)
        entries = by_project[project_id]
        requirements = [
            Requirement(id=requirement_id_for(doc_id, i), text=text, source_doc_id=doc_id)
            for i, (text, _class_label) in enumerate(entries)
        ]
        requirement_set = RequirementSet(doc_id=doc_id, requirements=requirements)
        requirement_sets.append(requirement_set)
        manifests.append({
            "doc_id": doc_id, "source_file": arff_path.name, "project_id": project_id,
            "counts": {"requirements": len(requirements)},
            "class_labels": {req.id: class_label for req, (_text, class_label)
                            in zip(requirements, entries)},
        })
    return requirement_sets, manifests


def extract(arff_path: Path, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    requirement_sets, manifests = extract_file(arff_path)

    summary = {
        "extractor_version": EXTRACTOR_VERSION,
        "extracted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "input_file": str(arff_path), "documents": [], "total_requirements": 0,
    }
    for requirement_set, manifest in zip(requirement_sets, manifests):
        doc_id = manifest["doc_id"]
        (output_dir / f"{doc_id}.json").write_text(
            requirement_set.model_dump_json(indent=2), encoding="utf-8", newline="\n")
        (output_dir / f"{doc_id}.manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
        summary["documents"].append({
            "doc_id": doc_id, "requirements": manifest["counts"]["requirements"]})
        summary["total_requirements"] += manifest["counts"]["requirements"]

    (output_dir / "extraction-summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
    return summary


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.extract_promise_nfr")
    parser.add_argument("arff_path", type=Path, help="datasets/promise-nfr/promise_nfr.arff")
    parser.add_argument("output_dir", type=Path,
                        help="Directory to write promise-<project_id>.json/.manifest.json into")
    args = parser.parse_args(argv)

    try:
        summary = extract(args.arff_path, args.output_dir)
    except (OSError, ValueError, ValidationError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    for doc in summary["documents"]:
        print(f"{doc['doc_id']:16s} {doc['requirements']:4d} requirements")
    print(f"{'TOTAL':16s} {summary['total_requirements']:4d} requirements "
         f"across {len(summary['documents'])} projects -> {args.output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
