"""Pull one document out of a JSON file containing a LIST of documents (e.g.
datasets/requirements_dataset.json, 10 documents) and write it as a standalone
RequirementSet JSON file -- the shape orchestrator/cli.py's `run` subcommand takes as
its INPUT argument.

    python -m orchestrator.extract_document DATASET.json DOC_ID OUTPUT.json

Validates what it writes (RequirementSet.model_validate) so a malformed extraction
fails here, at extraction time, not inside the CLI. Deliberately not a data pipeline:
one file in, one doc_id selected, one file out -- nothing about the extraction is
configurable beyond that.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from design.schemas import RequirementSet


def extract_document(dataset_path: Path, doc_id: str) -> RequirementSet:
    documents = json.loads(dataset_path.read_text())
    if not isinstance(documents, list):
        raise ValueError(f"{dataset_path} is not a JSON list of documents (got {type(documents).__name__})")

    matches = [d for d in documents if isinstance(d, dict) and d.get("doc_id") == doc_id]
    if not matches:
        known = sorted(d.get("doc_id") for d in documents if isinstance(d, dict) and d.get("doc_id"))
        raise ValueError(f"no document with doc_id {doc_id!r} in {dataset_path} -- known ids: {known}")
    if len(matches) > 1:
        raise ValueError(f"doc_id {doc_id!r} is not unique in {dataset_path} ({len(matches)} matches)")

    return RequirementSet.model_validate(matches[0])


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m orchestrator.extract_document")
    parser.add_argument("dataset", type=Path, help="Path to a JSON file holding a list of documents")
    parser.add_argument("doc_id", help="doc_id of the document to extract")
    parser.add_argument("output", type=Path, help="Path to write the standalone RequirementSet JSON to")
    args = parser.parse_args(argv)

    try:
        requirement_set = extract_document(args.dataset, args.doc_id)
    except (OSError, ValueError, ValidationError, json.JSONDecodeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    args.output.write_text(requirement_set.model_dump_json(indent=2))
    print(f"Wrote {args.output} ({len(requirement_set.requirements)} requirement(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
