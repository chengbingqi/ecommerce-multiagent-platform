import json
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest

from app.media.asr import ASRResult, ASRSegment
from app.media.pipeline import SUBTITLE_FONT_NAME, build_candidates, render_ass, render_srt
from app.media.probe import MediaProbeError, probe_media_file, validate_probe
from app.media.scoring import CandidateInput, score_candidate


@pytest.mark.asyncio
async def test_real_ffprobe_accepts_fixed_h264_aac(fixed_video: Path) -> None:
    result = await probe_media_file(fixed_video, 10)
    validate_probe(result, 60)
    assert result.video_codec == "h264"
    assert result.audio_codec == "aac"
    assert result.width == 320
    assert result.height == 180
    assert 9_000 <= result.duration_ms <= 11_000


@pytest.mark.asyncio
async def test_corrupt_media_is_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "fake.mp4"
    bad.write_bytes(b"not a video")
    with pytest.raises(MediaProbeError):
        await probe_media_file(bad, 5)


def test_highlight_score_is_deterministic_and_explainable() -> None:
    candidate = CandidateInput(
        start_ms=0,
        end_ms=20_000,
        text="今日重点商品优惠，商品值得关注",
        keyword_hits=2,
        has_audio=True,
        scene_complete=True,
        black_ratio=Decimal("0.1"),
        silence_ratio=Decimal("0.2"),
        overlap_ratio=Decimal("0.0"),
    )
    first = score_candidate(candidate)
    second = score_candidate(candidate)
    assert first == second
    assert set(first) == {
        "version",
        "keyword",
        "text_relevance",
        "audio",
        "scene",
        "length",
        "black_penalty",
        "silence_penalty",
        "overlap_penalty",
        "total",
    }
    assert Decimal("0") <= Decimal(first["total"]) <= Decimal("1")


def test_candidates_and_subtitles_stay_within_clip() -> None:
    transcript = ASRResult(
        language="zh",
        segments=[ASRSegment(1_000, 4_000, "蓝牙耳机限时优惠")],
    )
    rows = build_candidates([0, 5_000, 10_000], transcript, ["耳机"], 3, 8, 5, True)
    assert rows
    candidate, scores = rows[0]
    assert candidate.start_ms >= 0
    assert candidate.end_ms <= 10_000
    assert Decimal(scores["keyword"]) > 0
    subtitle = render_srt(transcript, candidate.start_ms, candidate.end_ms)
    assert "-->" in subtitle
    assert "蓝牙耳机" in subtitle


def test_real_ffmpeg_burns_utf8_chinese_ass_into_video_pixels(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    subtitle = tmp_path / "subtitle.ass"
    burned = tmp_path / "burned.mp4"
    font_dir = Path(__file__).parents[2] / "backend" / "fonts"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x240:d=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(source),
        ],
        check=True,
        timeout=30,
    )
    ass, cue_count = render_ass(
        ASRResult("zh", [ASRSegment(0, 2_000, "中文商品字幕已烧录")]),
        0,
        2_000,
        320,
        240,
    )
    subtitle.write_text(ass, encoding="utf-8")
    assert cue_count == 1
    assert SUBTITLE_FONT_NAME in ass
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(source),
            "-vf",
            f"ass=filename={subtitle}:fontsdir={font_dir}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(burned),
        ],
        check=True,
        timeout=30,
    )

    def frame(path: Path) -> bytes:
        completed = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-ss",
                "1",
                "-i",
                str(path),
                "-frames:v",
                "1",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "-",
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        return completed.stdout

    plain_frame = frame(source)
    burned_frame = frame(burned)
    assert len(plain_frame) == len(burned_frame) == 320 * 240 * 3
    changed_pixels = sum(
        1 for left, right in zip(plain_frame, burned_frame, strict=True) if abs(left - right) >= 20
    )
    assert changed_pixels > 1_000
    probed = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(burned)],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    stream_types = [row["codec_type"] for row in json.loads(probed.stdout)["streams"]]
    assert stream_types.count("video") == 1
    assert stream_types.count("audio") == 1
    assert "subtitle" not in stream_types


def test_zero_audio_is_penalized_not_fabricated() -> None:
    with_audio = score_candidate(CandidateInput(0, 10_000, "商品", 1, True, True))
    without_audio = score_candidate(CandidateInput(0, 10_000, "商品", 1, False, True))
    assert Decimal(without_audio["audio"]) < Decimal(with_audio["audio"])
    assert Decimal(without_audio["total"]) < Decimal(with_audio["total"])
