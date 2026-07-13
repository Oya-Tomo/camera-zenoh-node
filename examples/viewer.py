"""Display JPEG frames received over Zenoh with OpenCV."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path
from threading import Lock

import cv2
import numpy as np
import zenoh

DEFAULT_ZENOH_CONFIG_PATH = Path(__file__).with_name("viewer-zenoh-config.json5")
WINDOW_NAME = "camera-zenoh-node"
DISPLAY_POLL_INTERVAL_MS = 10
INVALID_FRAME_LOG_INTERVAL = 10
QUIT_KEYS = {27, ord("q")}


def parse_concrete_key(value: str) -> str:
    if not value.strip():
        raise argparse.ArgumentTypeError("camera key must not be blank")
    try:
        zenoh.KeyExpr(value)
    except zenoh.ZError as error:
        raise argparse.ArgumentTypeError(f"invalid Zenoh key: {error}") from error
    if "*" in value:
        raise argparse.ArgumentTypeError("camera key must not contain wildcards")
    return value


class LatestFrame:
    """Share only the newest JPEG between the Zenoh and display threads."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._jpeg: bytes | None = None
        self._sequence = 0

    def update(self, jpeg: bytes) -> None:
        with self._lock:
            self._jpeg = jpeg
            self._sequence += 1

    def snapshot(self) -> tuple[bytes | None, int]:
        with self._lock:
            return self._jpeg, self._sequence


def decode_jpeg(jpeg: bytes) -> cv2.typing.MatLike | None:
    if not jpeg:
        return None
    encoded = np.frombuffer(jpeg, dtype=np.uint8)
    try:
        return cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    except cv2.error:
        return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Display a Zenoh JPEG stream with OpenCV.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "key_expression",
        type=parse_concrete_key,
        help="Concrete Zenoh key carrying JPEG frames.",
    )
    parser.add_argument(
        "--zenoh-config",
        type=Path,
        default=DEFAULT_ZENOH_CONFIG_PATH,
        metavar="FILE",
        help="Zenoh JSON5 configuration file.",
    )
    return parser


def display_frames(latest_frame: LatestFrame) -> None:
    displayed_sequence = 0
    invalid_frames = 0
    try:
        cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_AUTOSIZE)
        while True:
            jpeg, sequence = latest_frame.snapshot()
            if jpeg is not None and sequence != displayed_sequence:
                frame = decode_jpeg(jpeg)
                displayed_sequence = sequence
                if frame is None:
                    invalid_frames += 1
                    if (
                        invalid_frames == 1
                        or invalid_frames % INVALID_FRAME_LOG_INTERVAL == 0
                    ):
                        print(
                            f"[WARNING] Could not decode JPEG frame "
                            f"({invalid_frames} total).",
                            file=sys.stderr,
                        )
                else:
                    cv2.imshow(WINDOW_NAME, frame)

            key = cv2.waitKey(DISPLAY_POLL_INTERVAL_MS) & 0xFF
            if key in QUIT_KEYS:
                return
            try:
                window_visible = cv2.getWindowProperty(
                    WINDOW_NAME, cv2.WND_PROP_VISIBLE
                )
            except cv2.error:
                return
            if window_visible < 1:
                return
    finally:
        with suppress(cv2.error):
            cv2.destroyWindow(WINDOW_NAME)


def run(zenoh_config: zenoh.Config, key_expression: str) -> None:
    latest_frame = LatestFrame()

    def on_sample(sample: zenoh.Sample) -> None:
        latest_frame.update(sample.payload.to_bytes())

    zenoh.init_log_from_env_or("error")
    with (
        zenoh.open(zenoh_config) as session,
        session.declare_subscriber(key_expression, on_sample),
    ):
        print(f"[INFO] Subscribed to '{key_expression}'.")
        print("[INFO] Press q, ESC, or close the window to quit.")
        display_frames(latest_frame)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        zenoh_config = zenoh.Config.from_file(args.zenoh_config)
        run(zenoh_config, args.key_expression)
    except KeyboardInterrupt:
        print("\n[INFO] Stopped.")
    except (OSError, ValueError, cv2.error, zenoh.ZError) as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
