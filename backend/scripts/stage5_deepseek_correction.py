"""Evidence-constrained DeepSeek correction experiment for Stage 5 raw ASR."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from app.core.config import get_settings

PROMPT_VERSION = "asr_correction_evidence_v1.0.0"
MAX_CALLS = 3
MAX_TOKENS = 1400
NUMBER_PATTERN = re.compile(r"[0-9零〇一二两兩三四五六七八九十百千万萬]+")


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def validate_correction(
    raw_segments: list[dict[str, Any]], parsed: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    returned = parsed.get("segments")
    if not isinstance(returned, list):
        raise ValueError("response segments are missing")
    by_id = {str(row.get("segment_id")): row for row in returned if isinstance(row, dict)}
    output = []
    rejected: list[str] = []
    for raw in raw_segments:
        segment_id = raw["segment_id"]
        candidate = by_id.get(segment_id)
        if candidate is None:
            rejected.append(f"{segment_id}:missing")
            output.append({**raw, "corrected_text": raw["text"], "fallback": True})
            continue
        corrected = str(candidate.get("corrected_text", "")).strip()
        changes = candidate.get("changes", [])
        uncertain = candidate.get("uncertain_spans", [])
        if (
            not corrected
            or not isinstance(changes, list)
            or not isinstance(uncertain, list)
            or NUMBER_PATTERN.findall(corrected) != NUMBER_PATTERN.findall(raw["text"])
        ):
            rejected.append(f"{segment_id}:invalid_or_numeric_change")
            output.append({**raw, "corrected_text": raw["text"], "fallback": True})
            continue
        output.append(
            {
                **raw,
                "corrected_text": corrected,
                "changes": changes,
                "uncertain_spans": uncertain,
                "fallback": False,
            }
        )
    return output, rejected


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--comparison", required=True, type=Path)
    parser.add_argument("--lexicon", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError("refusing to overwrite DeepSeek correction evidence")
    comparison = json.loads(args.comparison.read_text())
    lexicon = json.loads(args.lexicon.read_text())
    if comparison.get("ground_truth_usage") != "not_read_by_runner":
        raise RuntimeError("ASR comparison is not isolated from ground truth")
    baseline = next(row for row in comparison["configs"] if row["name"] == "A_current_baseline")
    raw_segments = baseline["segments"]
    chunks = [raw_segments[index::MAX_CALLS] for index in range(MAX_CALLS)]
    # Restore contiguous chunks instead of round-robin order.
    chunk_size = (len(raw_segments) + MAX_CALLS - 1) // MAX_CALLS
    chunks = [
        raw_segments[index : index + chunk_size]
        for index in range(0, len(raw_segments), chunk_size)
    ][:MAX_CALLS]
    settings = get_settings()
    api_key = settings.deepseek_api_key.get_secret_value()
    if not api_key:
        raise RuntimeError("DeepSeek is not configured")
    system_prompt = (
        "You correct Chinese ecommerce livestream ASR using only the supplied raw segments, "
        "neighbor context, and approved catalog terms. Return strict JSON with a segments array. "
        "For every segment preserve segment_id and output corrected_text, changes, "
        "uncertain_spans. "
        "Each change must contain from, to, reason, evidence_segment_ids. Never change, normalize, "
        "add, or remove any Arabic or Chinese numeric expression, price, quantity, discount, SKU, "
        "or timestamp. Never invent a brand or product. If evidence is insufficient, keep raw text "
        "and put the span in uncertain_spans. Do not merge/split segments or create timestamps."
    )
    calls: list[dict[str, Any]] = []
    corrected_segments: list[dict[str, Any]] = []
    async with httpx.AsyncClient(
        base_url=settings.deepseek_base_url.rstrip("/"),
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=settings.llm_timeout_seconds,
        trust_env=False,
    ) as client:
        for call_index, chunk in enumerate(chunks, start=1):
            input_document = {
                "prompt_version": PROMPT_VERSION,
                "approved_catalog_terms": lexicon["terms"],
                "segments": [
                    {
                        "segment_id": row["segment_id"],
                        "start_seconds": row["start_seconds"],
                        "end_seconds": row["end_seconds"],
                        "raw_text": row["text"],
                        "average_log_probability": row["average_log_probability"],
                    }
                    for row in chunk
                ],
            }
            input_hash = digest(input_document)
            started = datetime.now(UTC)
            try:
                response = await client.post(
                    "/chat/completions",
                    json={
                        "model": settings.deepseek_model,
                        "temperature": 0,
                        "max_tokens": MAX_TOKENS,
                        "response_format": {"type": "json_object"},
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {
                                "role": "user",
                                "content": json.dumps(input_document, ensure_ascii=False),
                            },
                        ],
                    },
                )
                response.raise_for_status()
                body = response.json()
                content = body["choices"][0]["message"]["content"]
                parsed = json.loads(content)
                corrected, rejected = validate_correction(chunk, parsed)
                corrected_segments.extend(corrected)
                calls.append(
                    {
                        "call_index": call_index,
                        "status": "SUCCEEDED",
                        "model": str(body.get("model", settings.deepseek_model)),
                        "prompt_version": PROMPT_VERSION,
                        "input_sha256": input_hash,
                        "output_sha256": digest(parsed),
                        "latency_ms": int((datetime.now(UTC) - started).total_seconds() * 1000),
                        "usage": body.get("usage", {}),
                        "rejected_segment_changes": rejected,
                        "parsed_output": parsed,
                    }
                )
            except Exception as exc:
                corrected_segments.extend(
                    {**row, "corrected_text": row["text"], "fallback": True} for row in chunk
                )
                calls.append(
                    {
                        "call_index": call_index,
                        "status": "FALLBACK",
                        "model": settings.deepseek_model,
                        "prompt_version": PROMPT_VERSION,
                        "input_sha256": input_hash,
                        "output_sha256": None,
                        "latency_ms": int((datetime.now(UTC) - started).total_seconds() * 1000),
                        "error_type": type(exc).__name__,
                    }
                )
    result = {
        "schema_version": "stage5_deepseek_asr_correction_v1",
        "created_at": datetime.now(UTC).isoformat(),
        "ground_truth_usage": "not_read_by_runner",
        "source_config": baseline["config_version"],
        "model": settings.deepseek_model,
        "prompt_version": PROMPT_VERSION,
        "temperature": 0,
        "max_tokens_per_call": MAX_TOKENS,
        "timeout_seconds": settings.llm_timeout_seconds,
        "max_calls": MAX_CALLS,
        "actual_calls": len(calls),
        "approved_lexicon": {
            "source": lexicon["source"],
            "tool": lexicon["tool"],
            "tool_version": lexicon["tool_version"],
            "terms": lexicon["terms"],
            "evidence_refs": lexicon["evidence_refs"],
        },
        "raw_segments": raw_segments,
        "corrected_segments": corrected_segments,
        "calls": calls,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as destination:
        json.dump(result, destination, ensure_ascii=False, indent=2)
        destination.write("\n")
    print(
        json.dumps(
            {
                "calls": len(calls),
                "succeeded": sum(row["status"] == "SUCCEEDED" for row in calls),
                "fallback": sum(row["status"] == "FALLBACK" for row in calls),
                "changed_segments": sum(
                    row["corrected_text"] != row["text"] for row in corrected_segments
                ),
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
