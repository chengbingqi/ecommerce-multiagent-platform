from __future__ import annotations

import csv
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "evals/stage7/results"


def main() -> int:
    RESULTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    junit = RESULTS / f"stage7_security_{stamp}.junit.xml"
    command = [
        str(ROOT / ".venv/bin/pytest"),
        "-q",
        "tests/stage7/test_security_cases.py",
        f"--junitxml={junit}",
    ]
    completed = subprocess.run(command, cwd=ROOT, check=False)
    tree = ElementTree.parse(junit)
    cases: list[dict[str, str]] = []
    for testcase in tree.findall(".//testcase"):
        name = testcase.attrib.get("name", "")
        case_id = name.rsplit("[", 1)[-1].rstrip("]") if "[" in name else name
        status = "PASSED"
        error_code = ""
        if testcase.find("failure") is not None or testcase.find("error") is not None:
            status = "FAILED"
            error_code = "ASSERTION_FAILED"
        elif testcase.find("skipped") is not None:
            status = "BLOCKED"
            error_code = "EXTERNAL_GATE_UNAVAILABLE"
        cases.append(
            {
                "case_id": case_id,
                "status": status,
                "error_code": error_code,
                "evidence_path": str(junit.relative_to(ROOT)),
            }
        )
    counts = {
        status: sum(row["status"] == status for row in cases)
        for status in ("PASSED", "FAILED", "BLOCKED")
    }
    summary = {
        "manifest_version": "stage7_security_cases_v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "total": len(cases),
        "passed": counts["PASSED"],
        "failed": counts["FAILED"],
        "blocked": counts["BLOCKED"],
        "p0_pass_rate": counts["PASSED"] / len(cases) if cases else 0,
        "public_deployment_eligible": counts["FAILED"] == 0 and counts["BLOCKED"] == 0,
        "cases": cases,
    }
    json_path = RESULTS / f"stage7_security_{stamp}.json"
    csv_path = RESULTS / f"stage7_security_{stamp}.csv"
    summary_path = RESULTS / "stage7_security_summary.json"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=cases[0].keys() if cases else ["case_id"])
        writer.writeheader()
        writer.writerows(cases)
    print(
        json.dumps(
            {
                key: summary[key]
                for key in ("total", "passed", "failed", "blocked", "public_deployment_eligible")
            }
        )
    )
    return completed.returncode


if __name__ == "__main__":
    sys.exit(main())
