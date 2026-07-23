"""Collect pixel and stream evidence for Stage 5 burned-in subtitles."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any, cast

TIMESTAMP = re.compile(r"(?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2}),(?P<millis>\d{3})")


def command(arguments: list[str]) -> None:
    subprocess.run(arguments, check=True, capture_output=True, text=True)


def seconds(value: str) -> float:
    match = TIMESTAMP.fullmatch(value)
    if match is None:
        raise ValueError(f"invalid SRT timestamp: {value}")
    return (
        int(match["hour"]) * 3600
        + int(match["minute"]) * 60
        + int(match["second"])
        + int(match["millis"]) / 1000
    )


def parse_ppm(path: Path) -> tuple[int, int, bytes]:
    content = path.read_bytes()
    if not content.startswith(b"P6\n"):
        raise ValueError(f"unsupported PPM: {path}")
    header, pixels = content.split(b"\n255\n", 1)
    dimensions = [line for line in header.splitlines()[1:] if not line.startswith(b"#")]
    width, height = (int(value) for value in dimensions[-1].split())
    expected = width * height * 3
    if len(pixels) != expected:
        raise ValueError(f"invalid PPM byte count: {path}")
    return width, height, pixels


def region_delta(
    left: bytes, right: bytes, width: int, start_y: int, end_y: int
) -> dict[str, float | int]:
    start = start_y * width * 3
    end = end_y * width * 3
    deltas = [abs(a - b) for a, b in zip(left[start:end], right[start:end], strict=True)]
    pixel_deltas = [max(deltas[index : index + 3]) for index in range(0, len(deltas), 3)]
    return {
        "pixel_count": len(pixel_deltas),
        "changed_pixels_over_40": sum(value > 40 for value in pixel_deltas),
        "changed_pixel_ratio_over_40": round(
            sum(value > 40 for value in pixel_deltas) / len(pixel_deltas), 6
        ),
        "mean_absolute_channel_delta": round(sum(deltas) / len(deltas), 6),
    }


def probe(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=index,codec_type,codec_name,pix_fmt",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return cast(dict[str, Any], json.loads(result.stdout))


def validate_srt(path: Path, duration: float) -> int:
    invalid = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if " --> " not in line:
            continue
        start_text, end_text = line.split(" --> ", 1)
        start = seconds(start_text)
        end = seconds(end_text)
        if start < 0 or end > duration + 0.001 or start >= end:
            invalid += 1
    return invalid


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument(
        "--sample",
        action="append",
        required=True,
        help="candidate_id:relative_seconds",
    )
    args = parser.parse_args()

    after = json.loads((args.output_dir / "after_candidates.json").read_text(encoding="utf-8"))
    records = {record["candidate_id"]: record for record in after["records"]}
    evidence_dir = args.output_dir / "subtitle_frame_evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    samples: list[dict[str, Any]] = []
    for sample in args.sample:
        candidate_id, relative_text = sample.rsplit(":", 1)
        relative = float(relative_text)
        record = records[candidate_id]
        absolute = float(record["start_seconds"]) + relative
        clip_path = args.output_dir / "after_assets" / candidate_id / "clip.mp4"
        source_ppm = evidence_dir / f"{candidate_id}_source.ppm"
        burned_ppm = evidence_dir / f"{candidate_id}_burned.ppm"
        source_png = evidence_dir / f"{candidate_id}_source.png"
        burned_png = evidence_dir / f"{candidate_id}_burned.png"
        for input_path, at, ppm_path, png_path in (
            (args.source, absolute, source_ppm, source_png),
            (clip_path, relative, burned_ppm, burned_png),
        ):
            command(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-nostdin",
                    "-loglevel",
                    "error",
                    "-y",
                    "-ss",
                    str(at),
                    "-i",
                    str(input_path),
                    "-frames:v",
                    "1",
                    str(ppm_path),
                ]
            )
            command(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-nostdin",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(ppm_path),
                    "-frames:v",
                    "1",
                    str(png_path),
                ]
            )
        width, height, source_pixels = parse_ppm(source_ppm)
        burned_width, burned_height, burned_pixels = parse_ppm(burned_ppm)
        if (width, height) != (burned_width, burned_height):
            raise ValueError("source and burned frames have different dimensions")
        top = region_delta(source_pixels, burned_pixels, width, 0, height * 2 // 5)
        subtitle_region = region_delta(source_pixels, burned_pixels, width, height * 3 // 5, height)
        samples.append(
            {
                "candidate_id": candidate_id,
                "relative_seconds": relative,
                "absolute_seconds": round(absolute, 3),
                "source_frame": str(source_png.relative_to(args.output_dir)),
                "burned_frame": str(burned_png.relative_to(args.output_dir)),
                "width": width,
                "height": height,
                "top_region_delta": top,
                "subtitle_region_delta": subtitle_region,
                "subtitle_to_top_changed_pixel_ratio": round(
                    float(subtitle_region["changed_pixel_ratio_over_40"])
                    / max(float(top["changed_pixel_ratio_over_40"]), 0.000001),
                    3,
                ),
                "streams": probe(clip_path)["streams"],
            }
        )

    subtitle_errors: list[dict[str, Any]] = []
    for record in after["records"]:
        candidate_id = record["candidate_id"]
        srt_path = args.output_dir / "after_assets" / candidate_id / "subtitle.srt"
        if not srt_path.exists():
            continue
        invalid_count = validate_srt(srt_path, float(record["duration"]))
        if invalid_count:
            subtitle_errors.append(
                {"candidate_id": candidate_id, "invalid_cue_count": invalid_count}
            )
    output = {
        "sample_count": len(samples),
        "comparison": "同一绝对时刻源视频无字幕帧与最终候选烧录帧",
        "samples": samples,
        "downloaded_candidate_srt_count": sum(
            (args.output_dir / "after_assets" / record["candidate_id"] / "subtitle.srt").exists()
            for record in after["records"]
        ),
        "subtitle_out_of_bounds_count": sum(item["invalid_cue_count"] for item in subtitle_errors),
        "subtitle_timing_errors": subtitle_errors,
    }
    (args.output_dir / "subtitle_burn_in_evidence.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
