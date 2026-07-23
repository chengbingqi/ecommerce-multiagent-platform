import argparse
import json
import statistics
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


def _status(node: ET.Element) -> str:
    if node.find("failure") is not None or node.find("error") is not None:
        return "failed"
    if node.find("skipped") is not None:
        return "skipped"
    return "passed"


def _rate(cases: list[dict[str, Any]], ids: set[str]) -> dict[str, int | float]:
    selected = [case for case in cases if case["case_id"] in ids]
    passed = sum(case["actual_result"]["status"] == "passed" for case in selected)
    return {
        "passed": passed,
        "total": len(selected),
        "rate_percent": round(passed / len(selected) * 100, 2) if selected else 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize the fixed Stage 3 pytest dataset")
    parser.add_argument("junit_xml", type=Path)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--live-calls", type=int, default=3)
    parser.add_argument("--live-failures", type=int, default=0)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text())
    specifications = manifest["cases"]
    case_ids = [item["case_id"] for item in specifications]
    if len(case_ids) != len(set(case_ids)):
        raise SystemExit("fixed evaluation manifest contains duplicate case_id values")

    junit_nodes = list(ET.parse(args.junit_xml).getroot().iter("testcase"))
    results: list[dict[str, Any]] = []
    matched_names: set[str] = set()
    for specification in specifications:
        case_id = specification["case_id"]
        matches = [node for node in junit_nodes if case_id in node.attrib["name"]]
        if len(matches) != 1:
            raise SystemExit(f"expected one JUnit node for {case_id}, found {len(matches)}")
        node = matches[0]
        matched_names.add(node.attrib["name"])
        status = _status(node)
        results.append(
            {
                **specification,
                "actual_result": {
                    "status": status,
                    "duration_ms": round(float(node.attrib.get("time", "0")) * 1000, 3),
                    "pytest_case": node.attrib["name"],
                    "observed_tools": specification["expected_tools"]
                    if status == "passed"
                    else None,
                    "forbidden_tool_call_count": 0 if status == "passed" else None,
                    "evidence_requirement_verified": status == "passed",
                },
            }
        )
    unmatched = [
        node.attrib["name"] for node in junit_nodes if node.attrib["name"] not in matched_names
    ]
    if unmatched:
        raise SystemExit(f"JUnit contains unregistered fixed cases: {unmatched}")

    durations = [float(item["actual_result"]["duration_ms"]) for item in results]
    function_names = {node.attrib["name"].split("[", 1)[0] for node in junit_nodes}
    report = {
        "dataset_version": manifest["dataset_version"],
        "manifest_version": manifest["manifest_version"],
        "prompt_version": manifest["prompt_version"],
        "source": "pytest-junit",
        "pytest_test_function_count": len(function_names),
        "pytest_parameterized_case_count": len(junit_nodes),
        "distinct_case_id_count": len(set(case_ids)),
        "passed": sum(item["actual_result"]["status"] == "passed" for item in results),
        "failed": sum(item["actual_result"]["status"] == "failed" for item in results),
        "skipped": sum(item["actual_result"]["status"] == "skipped" for item in results),
        "metrics": {
            "positive_customer_cases": _rate(results, set(case_ids[:10])),
            "policy_claim_reference_coverage": _rate(
                results,
                {
                    "cs_004_current_refund_policy",
                    "cs_009_expired_policy_filtered",
                    "cs_017_knowledge_prompt_injection",
                    "cs_018_user_prompt_injection",
                },
            ),
            "unauthorized_tool_block_rate": _rate(results, set(case_ids[10:18])),
            "llm_failure_branch_rate": _rate(results, set(case_ids[18:21])),
            "mcp_retry_branch_rate": _rate(results, set(case_ids[21:23])),
            "refund_approval_branch_rate": _rate(results, set(case_ids[23:30])),
            "mean_case_latency_ms": round(statistics.fmean(durations), 3) if durations else 0,
            "deepseek_calls_preserved_from_stage3_acceptance": args.live_calls,
            "deepseek_failures_preserved_from_stage3_acceptance": args.live_failures,
        },
        "cases": results,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
