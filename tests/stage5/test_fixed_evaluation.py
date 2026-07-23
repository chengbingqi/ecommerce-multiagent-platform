import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CASES_PATH = ROOT / "evals/stage5/cases/media_pipeline_cases_v1.json"
CASES = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]


def test_stage5_case_ids_are_unique_and_categories_complete() -> None:
    identifiers = [case["case_id"] for case in CASES]
    assert len(identifiers) == len(set(identifiers)) == 52
    assert {case["category"] for case in CASES} == {
        "upload",
        "mcp",
        "pipeline",
        "security",
        "frontend",
    }


@pytest.mark.parametrize("case", CASES, ids=[case["case_id"] for case in CASES])
def test_stage5_fixed_case_contract(case: dict) -> None:
    assert case["case_id"].startswith("s5-")
    assert case["input_type"]
    assert case["expected_status"]
    assert "expected_tool" in case
    assert isinstance(case["forbidden_tools"], list)
    assert case["evidence_requirement"]
    assert case["test_reference"]
    if case["expected_status"] == "FORBIDDEN":
        assert case["forbidden_tools"]
    if case["category"] == "security":
        assert case["evidence_requirement"] in {
            "denial",
            "validation",
            "fixed-whitelist",
            "fixed-graph",
            "public-ref",
        }
