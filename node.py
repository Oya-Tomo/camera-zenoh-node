"""Publish JPEG-encoded camera frames over Zenoh."""

from __future__ import annotations

import argparse
import math
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import cv2
import json5
import zenoh

DEFAULT_ZENOH_CONFIG_PATH = Path("config/zenoh-config.json5")
DEFAULT_NODE_CONFIG_PATH = Path("config/node-config.json5")
MAX_CAMERA_READ_FAILURES = 10
CAMERA_READ_RETRY_DELAY = 0.1

CONGESTION_CONTROLS = {
    "drop": zenoh.CongestionControl.DROP,
    "block": zenoh.CongestionControl.BLOCK,
    "block_first": zenoh.CongestionControl.BLOCK_FIRST,
}
RELIABILITIES = {
    "best_effort": zenoh.Reliability.BEST_EFFORT,
    "reliable": zenoh.Reliability.RELIABLE,
}


@dataclass(frozen=True)
class CameraConfig:
    device: int
    width: int
    jpeg_quality: int


@dataclass(frozen=True)
class PublisherConfig:
    key_expression: str
    frame_delay_seconds: float
    congestion_control: zenoh.CongestionControl
    reliability: zenoh.Reliability


@dataclass(frozen=True)
class NodeConfig:
    camera: CameraConfig
    publisher: PublisherConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="camera-zenoh-node",
        description="Capture camera frames and publish them over Zenoh.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--zenoh-config",
        type=Path,
        default=DEFAULT_ZENOH_CONFIG_PATH,
        metavar="FILE",
        help="Zenoh JSON5 configuration file.",
    )
    parser.add_argument(
        "--node-config",
        type=Path,
        default=DEFAULT_NODE_CONFIG_PATH,
        metavar="FILE",
        help="Camera publisher JSON5 configuration file.",
    )
    return parser


def _object(value: object, location: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{location} must be an object")
    return cast(dict[str, object], value)


def _validate_keys(
    values: Mapping[str, object], expected: set[str], location: str
) -> None:
    missing = expected - values.keys()
    unknown = values.keys() - expected
    if missing:
        raise ValueError(f"{location} is missing: {', '.join(sorted(missing))}")
    if unknown:
        raise ValueError(f"{location} has unknown keys: {', '.join(sorted(unknown))}")


def _integer(
    values: Mapping[str, object],
    key: str,
    location: str,
    *,
    minimum: int,
    maximum: int | None = None,
) -> int:
    value = values[key]
    if type(value) is not int:
        raise ValueError(f"{location}.{key} must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        expected = f"{minimum}..{maximum}" if maximum is not None else f">= {minimum}"
        raise ValueError(f"{location}.{key} must be {expected}")
    return value


def _non_negative_number(
    values: Mapping[str, object], key: str, location: str
) -> float:
    value = values[key]
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{location}.{key} must be a finite number >= 0")
    try:
        number = float(value)
    except OverflowError:
        raise ValueError(f"{location}.{key} must be a finite number >= 0") from None
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{location}.{key} must be a finite number >= 0")
    return number


def _non_empty_string(values: Mapping[str, object], key: str, location: str) -> str:
    value = values[key]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{location}.{key} must be a non-empty string")
    return value


def _choice[Choice](
    values: Mapping[str, object],
    key: str,
    choices: Mapping[str, Choice],
    location: str,
) -> Choice:
    value = _non_empty_string(values, key, location)
    if value not in choices:
        raise ValueError(
            f"{location}.{key} must be one of: {', '.join(sorted(choices))}"
        )
    return choices[value]


def _key_expression(values: Mapping[str, object], key: str, location: str) -> str:
    value = _non_empty_string(values, key, location)
    try:
        zenoh.KeyExpr(value)
    except zenoh.ZError as error:
        raise ValueError(
            f"{location}.{key} must be a valid Zenoh key expression: {error}"
        ) from error
    if "*" in value:
        raise ValueError(
            f"{location}.{key} must be a concrete Zenoh key without wildcards"
        )
    return value


def load_node_config(path: Path) -> NodeConfig:
    try:
        document = json5.loads(
            path.read_text(encoding="utf-8"), allow_duplicate_keys=False
        )
    except ValueError as error:
        raise ValueError(f"{path}: invalid JSON5: {error}") from error

    root = _object(document, str(path))
    _validate_keys(root, {"camera", "publisher"}, str(path))

    camera = _object(root["camera"], f"{path}.camera")
    _validate_keys(camera, {"device", "width", "jpeg_quality"}, f"{path}.camera")

    publisher = _object(root["publisher"], f"{path}.publisher")
    _validate_keys(
        publisher,
        {
            "key_expression",
            "frame_delay_seconds",
            "congestion_control",
            "reliability",
        },
        f"{path}.publisher",
    )

    return NodeConfig(
        camera=CameraConfig(
            device=_integer(camera, "device", f"{path}.camera", minimum=0),
            width=_integer(camera, "width", f"{path}.camera", minimum=1),
            jpeg_quality=_integer(
                camera, "jpeg_quality", f"{path}.camera", minimum=0, maximum=100
            ),
        ),
        publisher=PublisherConfig(
            key_expression=_key_expression(
                publisher, "key_expression", f"{path}.publisher"
            ),
            frame_delay_seconds=_non_negative_number(
                publisher, "frame_delay_seconds", f"{path}.publisher"
            ),
            congestion_control=_choice(
                publisher,
                "congestion_control",
                CONGESTION_CONTROLS,
                f"{path}.publisher",
            ),
            reliability=_choice(
                publisher,
                "reliability",
                RELIABILITIES,
                f"{path}.publisher",
            ),
        ),
    )


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


def run(zenoh_config: zenoh.Config, node_config: NodeConfig) -> None:
    zenoh.init_log_from_env_or("error")
    camera = node_config.camera
    publisher_config = node_config.publisher

    print("[INFO] Open Zenoh session...")
    with zenoh.open(zenoh_config) as session:
        print(f"[INFO] Open camera device {camera.device}...")
        capture = cv2.VideoCapture(camera.device)
        if not capture.isOpened():
            capture.release()
            raise RuntimeError(f"could not open camera device {camera.device}")

        try:
            with session.declare_publisher(
                publisher_config.key_expression,
                encoding="image/jpeg",
                congestion_control=publisher_config.congestion_control,
                reliability=publisher_config.reliability,
            ) as publisher:
                print(
                    f"[INFO] Publishing {camera.width}px JPEG frames on "
                    f"'{publisher_config.key_expression}'..."
                )
                print("[INFO] Press CTRL-C to quit.")
                publish_frames(
                    capture,
                    publisher,
                    width=camera.width,
                    quality=camera.jpeg_quality,
                    delay=publisher_config.frame_delay_seconds,
                )
        finally:
            capture.release()


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        node_config = load_node_config(args.node_config)
        zenoh_config = zenoh.Config.from_file(args.zenoh_config)
        run(zenoh_config, node_config)
    except KeyboardInterrupt:
        print("\n[INFO] Stopped.")
    except (RuntimeError, ValueError, OSError, cv2.error, zenoh.ZError) as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
