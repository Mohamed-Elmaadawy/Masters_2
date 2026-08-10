"""
Regression tests for orchestrator/extract_document.py. Run after any change there:

    python -m orchestrator.test_extract_document

Plain script, no pytest -- same convention as orchestrator/test_cli.py.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from design.schemas import RequirementSet
from orchestrator.extract_document import main

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


def write_dataset(tmp_path: Path, documents: list[dict]) -> Path:
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(documents))
    return path


DOC_A = {"doc_id": "DOC-A", "requirements": [
    {"id": "REQ-A1", "text": "The system shall do a thing.", "source_doc_id": "DOC-A"}]}
DOC_B = {"doc_id": "DOC-B", "requirements": [
    {"id": "REQ-B1", "text": "The system shall do another thing.", "source_doc_id": "DOC-B"},
    {"id": "REQ-B2", "text": "The system shall do a third thing.", "source_doc_id": "DOC-B"}]}


def test_extracts_selected_document_and_writes_valid_json() -> None:
    section("extracting an existing doc_id writes a standalone, valid RequirementSet")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        dataset_path = write_dataset(tmp_path, [DOC_A, DOC_B])
        output_path = tmp_path / "out.json"

        code = main([str(dataset_path), "DOC-B", str(output_path)])

        ok("exit code is 0", code == 0)
        ok("output file exists", output_path.exists())
        written = RequirementSet.model_validate_json(output_path.read_text())
        ok("doc_id matches the selected document", written.doc_id == "DOC-B")
        ok("both of DOC-B's requirements are present",
           {r.id for r in written.requirements} == {"REQ-B1", "REQ-B2"})
        ok("DOC-A's requirement was not pulled in",
           "REQ-A1" not in {r.id for r in written.requirements})


def test_unknown_doc_id_fails_with_exit_1_and_lists_known_ids() -> None:
    section("a doc_id not in the dataset exits 1 and names the known ids, no file written")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        dataset_path = write_dataset(tmp_path, [DOC_A, DOC_B])
        output_path = tmp_path / "out.json"

        code = main([str(dataset_path), "DOC-NOPE", str(output_path)])

        ok("exit code is 1", code == 1)
        ok("no output file was written", not output_path.exists())


def test_duplicate_doc_id_fails_with_exit_1() -> None:
    section("a doc_id matching more than one document exits 1, no file written")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        dataset_path = write_dataset(tmp_path, [DOC_A, {**DOC_A}])
        output_path = tmp_path / "out.json"

        code = main([str(dataset_path), "DOC-A", str(output_path)])

        ok("exit code is 1", code == 1)
        ok("no output file was written", not output_path.exists())


def test_malformed_extraction_fails_at_extraction_time() -> None:
    section("a document that fails RequirementSet validation exits 1, no file written")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        bad_doc = {"doc_id": "DOC-BAD", "requirements": []}  # min_length=1 violated
        dataset_path = write_dataset(tmp_path, [bad_doc])
        output_path = tmp_path / "out.json"

        code = main([str(dataset_path), "DOC-BAD", str(output_path)])

        ok("exit code is 1", code == 1)
        ok("no output file was written", not output_path.exists())


def test_dataset_not_a_list_fails_with_exit_1() -> None:
    section("a dataset file that isn't a JSON list exits 1, no file written")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        dataset_path = tmp_path / "dataset.json"
        dataset_path.write_text(json.dumps({"doc_id": "DOC-A"}))
        output_path = tmp_path / "out.json"

        code = main([str(dataset_path), "DOC-A", str(output_path)])

        ok("exit code is 1", code == 1)
        ok("no output file was written", not output_path.exists())


def test_real_dataset_every_document_extracts_cleanly() -> None:
    section("every document in datasets/requirements_dataset.json extracts and validates")
    dataset_path = Path(__file__).resolve().parent.parent / "datasets" / "requirements_dataset.json"
    documents = json.loads(dataset_path.read_text(encoding="utf-8"))
    ok("dataset has the expected 10 documents", len(documents) == 10)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for doc in documents:
            output_path = tmp_path / f"{doc['doc_id']}.json"
            code = main([str(dataset_path), doc["doc_id"], str(output_path)])
            ok(f"{doc['doc_id']} extracts with exit 0", code == 0)
            ok(f"{doc['doc_id']}'s output is a valid RequirementSet",
               RequirementSet.model_validate_json(output_path.read_text()).doc_id == doc["doc_id"])


ALL_TESTS = [
    test_extracts_selected_document_and_writes_valid_json,
    test_unknown_doc_id_fails_with_exit_1_and_lists_known_ids,
    test_duplicate_doc_id_fails_with_exit_1,
    test_malformed_extraction_fails_at_extraction_time,
    test_dataset_not_a_list_fails_with_exit_1,
    test_real_dataset_every_document_extracts_cleanly,
]


if __name__ == "__main__":
    for test in ALL_TESTS:
        test()
    print(f"\n{PASSED} checks passed, {len(FAILED)} failed")
    if FAILED:
        raise SystemExit(1)
