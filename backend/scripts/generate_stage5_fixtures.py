import hashlib
import json
import subprocess
from pathlib import Path

FIXTURE_ROOT = Path("/data/temp/media-stage5/fixtures")
SOURCE_AUDIO = FIXTURE_ROOT / "jfk-openai-whisper-31243ba.flac"
SOURCE_AUDIO_SHA256 = "63a4b1e4c1dc655ac70961ffbf518acd249df237e5a0152faae9a4a836949715"
SOURCE_AUDIO_URL = "https://raw.githubusercontent.com/openai/whisper/31243ba/tests/jfk.flac"
COLORS = (
    ("red", "blue", "green"),
    ("purple", "yellow", "navy"),
    ("orange", "teal", "white"),
    ("pink", "black", "cyan"),
    ("brown", "lime", "gray"),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def generate(index: int) -> dict[str, object]:
    colors = COLORS[index % len(COLORS)]
    path = FIXTURE_ROOT / f"supported-{index + 1:02}.mp4"
    speech = (
        "And so, my fellow Americans, ask not what your country can do for you; "
        "ask what you can do for your country."
    )
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    for color in colors:
        command.extend(["-f", "lavfi", "-i", f"color=c={color}:s=640x360:d=4:r=25"])
    command.extend(
        [
            "-i",
            str(SOURCE_AUDIO),
            "-filter_complex",
            "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v];[3:a]apad=pad_dur=12,atrim=duration=12[a]",
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            "-shortest",
            str(path),
        ]
    )
    subprocess.run(command, check=True, stdin=subprocess.DEVNULL, timeout=90)
    return {
        "fixture_id": f"stage5-supported-{index + 1:02}",
        "filename": path.name,
        "sha256": sha256(path),
        "duration_seconds": 12,
        "resolution": "640x360",
        "speech": speech,
        "keywords": ["country", "Americans"],
        "evaluation_scope": "automated_pipeline",
        "fixture_type": "synthetic_color",
        "human_review_eligible": False,
    }


def main() -> None:
    FIXTURE_ROOT.mkdir(parents=True, exist_ok=True, mode=0o750)
    if not SOURCE_AUDIO.is_file() or sha256(SOURCE_AUDIO) != SOURCE_AUDIO_SHA256:
        raise SystemExit(
            "trusted Stage5 source audio is missing or has the wrong SHA-256; "
            "fetch the pinned OpenAI Whisper test fixture first"
        )
    rows = [generate(index) for index in range(10)]
    manifest = {
        "manifest_version": "stage5_media_fixtures_v1",
        "generator": "ffmpeg-fixed-trusted-audio-v1",
        "source_audio": {
            "url": SOURCE_AUDIO_URL,
            "sha256": SOURCE_AUDIO_SHA256,
            "license_context": "OpenAI Whisper public ASR test fixture",
        },
        "fixtures": rows,
    }
    (FIXTURE_ROOT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"generated": len(rows), "manifest_version": manifest["manifest_version"]}))


if __name__ == "__main__":
    main()
