import json
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

MANIFEST = Path("evals/stage5/cases/media_pipeline_cases_v1.json")
JUNIT = Path("evals/stage5/results/stage5_pytest_junit.xml")
FRONTEND_JUNIT = Path("evals/stage5/results/stage5_frontend_junit.xml")
OUTPUT = Path("evals/stage5/results/stage5_fixed_eval_20260718.json")


def main() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    root = ET.parse(JUNIT).getroot()
    frontend_root = ET.parse(FRONTEND_JUNIT).getroot()
    testcases = root.findall(".//testcase")
    functional_cases = testcases + frontend_root.findall(".//testcase")
    rows = []
    for case in manifest["cases"]:
        case_id = case["case_id"]
        contract_matches = [row for row in testcases if case_id in row.attrib.get("name", "")]
        if len(contract_matches) != 1:
            raise RuntimeError(f"JUnit evidence is not unique for {case_id}")
        testcase = contract_matches[0]
        functional_matches = [
            row for row in functional_cases if case["test_reference"] in row.attrib.get("name", "")
        ]
        if not functional_matches:
            raise RuntimeError(f"functional JUnit evidence is missing for {case_id}")
        status = (
            "failed"
            if testcase.find("failure") is not None or testcase.find("error") is not None
            else "skipped"
            if testcase.find("skipped") is not None
            else "passed"
        )
        if any(
            row.find("failure") is not None
            or row.find("error") is not None
            or row.find("skipped") is not None
            for row in functional_matches
        ):
            status = "failed"
        rows.append(
            {
                "case_id": case_id,
                "category": case["category"],
                "input_type": case["input_type"],
                "expected_status": case["expected_status"],
                "expected_tool": case["expected_tool"],
                "forbidden_tools": case["forbidden_tools"],
                "evidence_requirement": case["evidence_requirement"],
                "test_reference": case["test_reference"],
                "functional_evidence_count": len(functional_matches),
                "status": status,
                "time_seconds": testcase.attrib.get("time"),
            }
        )
    output = {
        "result_version": "stage5_fixed_eval_v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "manifest_version": manifest["eval_version"],
        "different_case_id_count": len(rows),
        "passed": sum(row["status"] == "passed" for row in rows),
        "failed": sum(row["status"] == "failed" for row in rows),
        "skipped": sum(row["status"] == "skipped" for row in rows),
        "results": rows,
    }
    if output["passed"] != len(rows):
        raise RuntimeError("fixed Stage 5 evaluation did not fully pass")
    OUTPUT.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: output[key] for key in ("passed", "failed", "skipped")}))


if __name__ == "__main__":
    main()
