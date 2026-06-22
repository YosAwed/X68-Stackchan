"""Capture a Windows camera frame periodically and atomically update a JPEG."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

import cv2


BACKENDS = {
    "any": 0,
    "dshow": cv2.CAP_DSHOW,
    "msmf": cv2.CAP_MSMF,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--interval", type=float, default=180.0)
    parser.add_argument("--backend", choices=sorted(BACKENDS), default="dshow")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--warmup-frames", type=int, default=5)
    parser.add_argument("--jpeg-quality", type=int, default=85)
    parser.add_argument("--log", type=Path)
    parser.add_argument("--once", action="store_true")
    return parser.parse_args()


def configure_logging(log_path: Path | None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )


def open_camera(args: argparse.Namespace):
    backend = BACKENDS[args.backend]
    cap = cv2.VideoCapture(args.camera_index, backend) if backend else cv2.VideoCapture(args.camera_index)
    if args.width:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    if args.height:
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    if not cap.isOpened():
        cap.release()
        raise RuntimeError(f"camera {args.camera_index} could not be opened")
    return cap


def capture_frame(cap, warmup_frames: int):
    frame = None
    ok = False
    for _ in range(max(1, warmup_frames)):
        ok, frame = cap.read()
        if not ok:
            time.sleep(0.1)
    if not ok or frame is None:
        raise RuntimeError("camera read failed")
    return frame


def write_jpeg(output: Path, frame, jpeg_quality: int) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality])
    if not ok:
        raise RuntimeError("JPEG encode failed")
    tmp = output.with_suffix(output.suffix + ".tmp")
    tmp.write_bytes(bytes(encoded))
    os.replace(tmp, output)


def main() -> int:
    args = parse_args()
    configure_logging(args.log)
    logging.info(
        "starting camera capture: index=%s backend=%s output=%s interval=%ss",
        args.camera_index,
        args.backend,
        args.output,
        args.interval,
    )

    cap = None
    try:
        while True:
            try:
                if cap is None:
                    cap = open_camera(args)
                frame = capture_frame(cap, args.warmup_frames)
                write_jpeg(args.output, frame, args.jpeg_quality)
                logging.info("updated %s", args.output)
            except Exception:
                logging.exception("capture failed")
                if cap is not None:
                    cap.release()
                    cap = None

            if args.once:
                break
            time.sleep(max(1.0, args.interval))
    except KeyboardInterrupt:
        logging.info("stopping camera capture")
    finally:
        if cap is not None:
            cap.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
