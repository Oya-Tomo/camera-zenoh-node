"""Publish JPEG-encoded camera frames over Zenoh."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections.abc import Callable, Sequence

import cv2
import zenoh

MAX_CAMERA_READ_FAILURES = 10
CAMERA_READ_RETRY_DELAY = 0.1


def bounded_int(minimum: int, maximum: int | None = None) -> Callable[[str], int]:
    def parse(value: str) -> int:
        parsed = int(value)
        if parsed < minimum or (maximum is not None and parsed > maximum):
            expected = (
                f"{minimum}..{maximum}" if maximum is not None else f">= {minimum}"
            )
            raise argparse.ArgumentTypeError(f"must be {expected}")
        return parsed

    return parse


def non_negative_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        raise argparse.ArgumentTypeError("must be a finite number >= 0")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="camera-zenoh-node",
        description="Capture camera frames and publish them over Zenoh.",
    )
    parser.add_argument(
        "-m",
        "--mode",
        choices=("peer", "client"),
        help="The Zenoh session mode.",
    )
    parser.add_argument(
        "-e",
        "--connect",
        metavar="ENDPOINT",
        action="append",
        help="Zenoh endpoints to connect to.",
    )
    parser.add_argument(
        "-l",
        "--listen",
        metavar="ENDPOINT",
        action="append",
        help="Zenoh endpoints to listen on.",
    )
    parser.add_argument(
        "-c",
        "--config",
        metavar="FILE",
        help="A Zenoh configuration file.",
    )
    parser.add_argument(
        "--no-multicast-scouting",
        action="store_true",
        help="Disable multicast scouting.",
    )
    parser.add_argument(
        "--cfg",
        metavar="KEY:VALUE",
        action="append",
        default=[],
        help="Apply an arbitrary Zenoh JSON5 configuration override.",
    )
    parser.add_argument(
        "--device",
        type=bounded_int(0),
        default=0,
        help="OpenCV camera device index (default: 0).",
    )
    parser.add_argument(
        "-w",
        "--width",
        type=bounded_int(1),
        default=500,
        help="Width of published frames (default: 500).",
    )
    parser.add_argument(
        "-q",
        "--quality",
        type=bounded_int(0, 100),
        default=95,
        help="JPEG quality from 0 to 100 (default: 95).",
    )
    parser.add_argument(
        "-d",
        "--delay",
        type=non_negative_float,
        default=0.05,
        help="Additional delay after each frame in seconds (default: 0.05).",
    )
    parser.add_argument(
        "-k",
        "--key",
        default="demo/zcam",
        help="Zenoh key expression (default: demo/zcam).",
    )
    return parser


def zenoh_config_from_args(args: argparse.Namespace) -> zenoh.Config:
    config = zenoh.Config.from_file(args.config) if args.config else zenoh.Config()
    if args.mode:
        config.insert_json5("mode", json.dumps(args.mode))
    if args.connect:
        config.insert_json5("connect/endpoints", json.dumps(args.connect))
    if args.listen:
        config.insert_json5("listen/endpoints", json.dumps(args.listen))
    if args.no_multicast_scouting:
        config.insert_json5("scouting/multicast/enabled", json.dumps(False))
    for override in args.cfg:
        try:
            key, value = override.split(":", 1)
        except ValueError:
            raise ValueError(
                f"invalid --cfg value {override!r}; expected KEY:VALUE"
            ) from None
        config.insert_json5(key, value)
    return config


def publish_frames(
    capture: cv2.VideoCapture,
    publisher: zenoh.Publisher,
    *,
    width: int,
    quality: int,
    delay: float,
) -> None:
    jpeg_options = [cv2.IMWRITE_JPEG_QUALITY, quality]
    consecutive_failures = 0

    while True:
        success, frame = capture.read()
        if (
            not success
            or frame is None
            or frame.size == 0
            or frame.ndim < 2
            or frame.shape[1] == 0
        ):
            consecutive_failures += 1
            if consecutive_failures >= MAX_CAMERA_READ_FAILURES:
                raise RuntimeError(
                    f"camera read failed {consecutive_failures} consecutive times"
                )
            time.sleep(CAMERA_READ_RETRY_DELAY)
            continue

        consecutive_failures = 0
        height = max(1, round(frame.shape[0] * width / frame.shape[1]))
        frame = cv2.resize(frame, (width, height))
        encoded, jpeg = cv2.imencode(".jpg", frame, jpeg_options)
        if not encoded:
            raise RuntimeError("failed to encode camera frame as JPEG")

        publisher.put(jpeg.tobytes())
        time.sleep(delay)


def run(
    config: zenoh.Config,
    *,
    device: int,
    key: str,
    width: int,
    quality: int,
    delay: float,
) -> None:
    zenoh.init_log_from_env_or("error")

    print("[INFO] Open Zenoh session...")
    with zenoh.open(config) as session:
        print(f"[INFO] Open camera device {device}...")
        capture = cv2.VideoCapture(device)
        if not capture.isOpened():
            capture.release()
            raise RuntimeError(f"could not open camera device {device}")

        try:
            with session.declare_publisher(
                key,
                encoding="image/jpeg",
                congestion_control=zenoh.CongestionControl.DROP,
                reliability=zenoh.Reliability.BEST_EFFORT,
            ) as publisher:
                print(f"[INFO] Publishing {width}px JPEG frames on '{key}'...")
                print("[INFO] Press CTRL-C to quit.")
                publish_frames(
                    capture,
                    publisher,
                    width=width,
                    quality=quality,
                    delay=delay,
                )
        finally:
            capture.release()


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        config = zenoh_config_from_args(args)
        run(
            config,
            device=args.device,
            key=args.key,
            width=args.width,
            quality=args.quality,
            delay=args.delay,
        )
    except KeyboardInterrupt:
        print("\n[INFO] Stopped.")
    except (RuntimeError, ValueError, OSError, cv2.error, zenoh.ZError) as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
