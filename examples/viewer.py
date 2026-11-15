"""Display JPEG frames received over Zenoh with pygame."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from threading import Lock
from typing import Self

import cv2
import json5
import numpy as np
import pygame
import zenoh
from pydantic import (
    BaseModel,
    ConfigDict,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)

DEFAULT_VIEWER_CONFIG_PATH = Path(__file__).with_name("viewer-config.json5")
DEFAULT_ZENOH_CONFIG_PATH = Path(__file__).with_name("viewer-zenoh-config.json5")
WINDOW_NAME = "camera-zenoh-node"
DEFAULT_WINDOW_SIZE = (640, 480)
DISPLAY_POLL_INTERVAL_MS = 10
INVALID_FRAME_LOG_INTERVAL = 10
QUIT_KEYS = {pygame.K_ESCAPE, pygame.K_q}


def _validate_concrete_key(value: str, location: str) -> str:
    if not value.strip():
        raise ValueError(f"{location} must be a non-empty string")
    try:
        zenoh.KeyExpr(value)
    except zenoh.ZError as error:
        raise ValueError(
            f"{location} must be a valid Zenoh key expression: {error}"
        ) from error
    if "*" in value:
        raise ValueError(f"{location} must be a concrete Zenoh key without wildcards")
    return value


class ViewerConfig(BaseModel):
    """Name the camera to display under the node configuration's key rules."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    zenoh_key_prefix: StrictStr
    device_key: StrictStr

    @field_validator("zenoh_key_prefix")
    @classmethod
    def validate_zenoh_key_prefix(cls, value: str) -> str:
        return _validate_concrete_key(value, "zenoh_key_prefix")

    @field_validator("device_key")
    @classmethod
    def validate_device_key(cls, value: str) -> str:
        value = _validate_concrete_key(value, "device_key")
        if "/" in value:
            raise ValueError("device_key must be a single key segment")
        return value

    @property
    def camera_key(self) -> str:
        return f"{self.zenoh_key_prefix}/{self.device_key}"

    @model_validator(mode="after")
    def validate_camera_key(self) -> Self:
        _validate_concrete_key(self.camera_key, "derived camera key")
        return self


def load_viewer_config(path: Path) -> ViewerConfig:
    try:
        document = json5.loads(
            path.read_text(encoding="utf-8"), allow_duplicate_keys=False
        )
    except ValueError as error:
        raise ValueError(f"{path}: invalid JSON5: {error}") from error
    try:
        return ViewerConfig.model_validate(document)
    except ValidationError as error:
        raise ValueError(f"{path}: invalid viewer configuration:\n{error}") from error


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
        description="Display a Zenoh JPEG stream.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--viewer-config",
        type=Path,
        default=DEFAULT_VIEWER_CONFIG_PATH,
        metavar="FILE",
        help="Viewer JSON5 configuration file.",
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
    pygame.display.init()
    try:
        pygame.display.set_caption(WINDOW_NAME)
        screen = pygame.display.set_mode(DEFAULT_WINDOW_SIZE)
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
                    if not frame.flags.c_contiguous:
                        frame = np.ascontiguousarray(frame)
                    height, width = frame.shape[:2]
                    frame_size = (width, height)
                    if screen.get_size() != frame_size:
                        screen = pygame.display.set_mode(frame_size)
                    frame_surface = pygame.image.frombuffer(
                        frame.data, frame_size, "BGR"
                    )
                    screen.blit(frame_surface, (0, 0))
                    pygame.display.flip()

            for event in pygame.event.get():
                if event.type == pygame.QUIT or (
                    event.type == pygame.KEYDOWN and event.key in QUIT_KEYS
                ):
                    return
            pygame.time.wait(DISPLAY_POLL_INTERVAL_MS)
    finally:
        pygame.display.quit()


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
        viewer_config = load_viewer_config(args.viewer_config)
        zenoh_config = zenoh.Config.from_file(args.zenoh_config)
        run(zenoh_config, viewer_config.camera_key)
    except KeyboardInterrupt:
        print("\n[INFO] Stopped.")
    except (OSError, ValueError, cv2.error, pygame.error, zenoh.ZError) as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
