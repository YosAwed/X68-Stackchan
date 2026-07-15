"""Generate matched VOICEVOX/Kokoro samples and a latency report."""

from __future__ import annotations

import argparse
import csv
import io
import json
import time
import wave
from datetime import datetime
from pathlib import Path

from tts_kokoro import TTS as KokoroTTS
from tts_voicevox import TTS as VoicevoxTTS

SERVER_DIR = Path(__file__).resolve().parent

DEFAULT_PROMPTS = (
    "おはよう。今日も一緒にのんびり過ごそうね。",
    "ぺけ子ちゃんは、ただいま起動しました。",
    "午前十時三十分になりました。そろそろ休憩しない？",
    "X68000とCoreS3をWi-Fiで接続しています。",
    "えへへ、頭を撫でてもらうと、ちょっと照れちゃうな。",
    "明日の東京は二十三度です。傘を忘れないでね。",
    "CPU使用率は八十五パーセント。少し忙しそうだね。",
    "大丈夫？ 無理しないで、今日はゆっくり休もう。",
)


def _wav_duration(wav_bytes: bytes) -> float:
    with wave.open(io.BytesIO(wav_bytes), "rb") as reader:
        return reader.getnframes() / reader.getframerate()


def _load_prompts(args: argparse.Namespace) -> list[str]:
    if args.text:
        return args.text
    if args.prompts_file:
        lines = Path(args.prompts_file).read_text(encoding="utf-8").splitlines()
        return [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]
    return list(DEFAULT_PROMPTS)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate matched VOICEVOX/Kokoro WAV files and timing data."
    )
    parser.add_argument("--text", action="append", help="Prompt to compare; repeatable")
    parser.add_argument("--prompts-file", help="UTF-8 text file with one prompt per line")
    parser.add_argument("--output-dir", help="Output directory")
    args = parser.parse_args()

    prompts = _load_prompts(args)
    if not prompts:
        parser.error("no prompts provided")

    output_dir = (
        Path(args.output_dir).expanduser()
        if args.output_dir
        else SERVER_DIR / "data" / "tts_ab" / datetime.now().strftime("%Y%m%d-%H%M%S")
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    init_started = time.perf_counter()
    voicevox = VoicevoxTTS()
    voicevox_init_ms = (time.perf_counter() - init_started) * 1000
    init_started = time.perf_counter()
    kokoro = KokoroTTS()
    kokoro_init_ms = (time.perf_counter() - init_started) * 1000
    backends = (("voicevox", voicevox), ("kokoro", kokoro))

    rows: list[dict] = []
    for index, text in enumerate(prompts, start=1):
        for backend_name, backend in backends:
            started = time.perf_counter()
            wav = backend.synthesize(text)
            elapsed_ms = (time.perf_counter() - started) * 1000
            filename = f"{index:02d}_{backend_name}.wav"
            (output_dir / filename).write_bytes(wav)
            row = {
                "index": index,
                "backend": backend_name,
                "latency_ms": round(elapsed_ms, 1),
                "duration_s": round(_wav_duration(wav), 3),
                "bytes": len(wav),
                "file": filename,
                "text": text,
            }
            rows.append(row)
            print(
                f"{index:02d} {backend_name:8s} "
                f"{elapsed_ms:7.1f} ms  {row['duration_s']:6.3f} s  {filename}"
            )

    with (output_dir / "report.csv").open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    summary = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "output_dir": str(output_dir),
        "backend_init_ms": {
            "voicevox": round(voicevox_init_ms, 1),
            "kokoro": round(kokoro_init_ms, 1),
        },
        "average_latency_ms": {
            name: round(
                sum(row["latency_ms"] for row in rows if row["backend"] == name)
                / len(prompts),
                1,
            )
            for name, _ in backends
        },
        "samples": rows,
    }
    (output_dir / "report.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"\nReport: {output_dir / 'report.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
