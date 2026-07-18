#!/usr/bin/env python3
"""Compare Ollama chat models with Stackchan's real persona prompt."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from persona import SYSTEM_PROMPT  # noqa: E402


PROMPTS = (
    "こんにちは、今日は何してた？",
    "仕事でちょっと疲れた",
    "X68000の魅力を一言で教えて",
    "なでなでしていい？",
    "明日は朝が早いんだ",
)


def chat(host: str, model: str, messages: list[dict], timeout: float) -> dict:
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "think": False,
        "keep_alive": "5m",
        "options": {
            "temperature": 0.5,
            "num_predict": 80,
            "num_ctx": 4096,
        },
    }
    request = urllib.request.Request(
        f"{host.rstrip('/')}/api/chat",
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"},
    )
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        result = json.load(response)
    result["wall_s"] = round(time.perf_counter() - started, 3)
    return result


def result_row(model: str, prompt: str, result: dict) -> dict:
    answer = result.get("message", {}).get("content", "").strip()
    eval_count = result.get("eval_count") or 0
    eval_ns = result.get("eval_duration") or 0
    return {
        "model": model,
        "prompt": prompt,
        "answer": answer,
        "chars": len(answer),
        "within_45_chars": len(answer) <= 45,
        "forbidden_marks": any(mark in answer for mark in "!?！？"),
        "wall_s": result["wall_s"],
        "load_s": round((result.get("load_duration") or 0) / 1e9, 3),
        "prompt_tps": round(
            (result.get("prompt_eval_count") or 0)
            / max((result.get("prompt_eval_duration") or 0) / 1e9, 1e-9),
            1,
        ),
        "generation_tps": round(eval_count / max(eval_ns / 1e9, 1e-9), 1),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("models", nargs="+")
    parser.add_argument("--host", default="http://127.0.0.1:11434")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument(
        "--no-think-prompt",
        action="store_true",
        help="Append Qwen's /no_think soft switch to each user message.",
    )
    args = parser.parse_args()

    rows = []
    for model in args.models:
        history: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        for prompt in PROMPTS:
            sent_prompt = f"{prompt}\n/no_think" if args.no_think_prompt else prompt
            messages = [*history, {"role": "user", "content": sent_prompt}]
            result = chat(args.host, model, messages, args.timeout)
            row = result_row(model, prompt, result)
            rows.append(row)
            history.extend(
                (
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": row["answer"]},
                )
            )
            print(json.dumps(row, ensure_ascii=False), flush=True)

    print(json.dumps({"results": rows}, ensure_ascii=False))


if __name__ == "__main__":
    main()
