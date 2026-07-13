"""Publish JPEG-encoded camera frames over Zenoh."""

from __future__ import annotations

import argparse
import logging
import math
import sys
import time
from collections.abc import Callable, Sequence
from contextlib import ExitStack
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Thread
from typing import Annotated, Literal, Self

import cv2
import json5
import zenoh
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)

DEFAULT_ZENOH_CONFIG_PATH = Path("config/zenoh-config.json5")
DEFAULT_NODE_CONFIG_PATH = Path("config/node-config.json5")
MAX_CONSECUTIVE_CAPTURE_FAILURES = 10
CAPTURE_RETRY_DELAY_SECONDS = 0.1
WORKER_POLL_INTERVAL_SECONDS = 0.1
WORKER_COOPERATIVE_SHUTDOWN_SECONDS = 0.2
WORKER_SHUTDOWN_TIMEOUT_SECONDS = 2.0
MISSED_SLOT_LOG_INTERVAL = 10
DEADLINE_TOLERANCE_SECONDS = 1e-9
LOGGER = logging.getLogger(__name__)

CONGESTION_CONTROLS = {
    "drop": zenoh.CongestionControl.DROP,
    "block": zenoh.CongestionControl.BLOCK,
    "block_first": zenoh.CongestionControl.BLOCK_FIRST,
}
RELIABILITIES = {
    "best_effort": zenoh.Reliability.BEST_EFFORT,
    "reliable": zenoh.Reliability.RELIABLE,
}

NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
PositiveInt = Annotated[int, Field(strict=True, ge=1)]
JpegQuality = Annotated[int, Field(strict=True, ge=0, le=100)]
PositiveFiniteFloat = Annotated[float, Field(gt=0, allow_inf_nan=False)]
CongestionControlName = Literal["drop", "block", "block_first"]
ReliabilityName = Literal["best_effort", "reliable"]


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


class _ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CameraSource(_ConfigModel):
    index: NonNegativeInt | None = None
    path: StrictStr | None = None

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("path must be a non-empty string")
        return value

    @model_validator(mode="after")
    def validate_selector(self) -> Self:
        if (self.index is None) == (self.path is None):
            raise ValueError("exactly one of index or path is required")
        return self

    @property
    def opencv_source(self) -> int | str:
        if self.index is not None:
            return self.index
        assert self.path is not None
        return self.path


class PublisherConfig(_ConfigModel):
    publish_frequency_hz: PositiveFiniteFloat
    congestion_control: CongestionControlName
    reliability: ReliabilityName

    @field_validator("publish_frequency_hz", mode="before")
    @classmethod
    def validate_frequency_type(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("publish_frequency_hz must be a number")
        return value

    @field_validator("publish_frequency_hz")
    @classmethod
    def validate_frequency_period(cls, value: float) -> float:
        try:
            period = 1.0 / value
        except OverflowError:
            raise ValueError(
                "publish_frequency_hz must produce a finite period"
            ) from None
        if not math.isfinite(period) or period <= 0:
            raise ValueError("publish_frequency_hz must produce a finite period")
        return value


class CameraConfig(_ConfigModel):
    device_key: StrictStr
    source: CameraSource
    size: tuple[PositiveInt, PositiveInt]
    jpeg_quality: JpegQuality
    publisher: PublisherConfig

    @field_validator("device_key")
    @classmethod
    def validate_device_key(cls, value: str) -> str:
        value = _validate_concrete_key(value, "device_key")
        if "/" in value:
            raise ValueError("device_key must be a single key segment")
        return value


class NodeConfig(_ConfigModel):
    base_key: StrictStr
    cameras: Annotated[tuple[CameraConfig, ...], Field(min_length=1)]

    @field_validator("base_key")
    @classmethod
    def validate_base_key(cls, value: str) -> str:
        return _validate_concrete_key(value, "base_key")

    @model_validator(mode="after")
    def validate_cameras(self) -> Self:
        device_keys: set[str] = set()
        sources: set[int | str] = set()
        for camera in self.cameras:
            if camera.device_key in device_keys:
                raise ValueError(f"duplicate device_key: {camera.device_key!r}")
            device_keys.add(camera.device_key)

            source = camera.source.opencv_source
            if source in sources:
                raise ValueError(f"duplicate camera source: {source!r}")
            sources.add(source)

            _validate_concrete_key(
                f"{self.base_key}/{camera.device_key}", "derived publisher key"
            )
        return self


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


def load_node_config(path: Path) -> NodeConfig:
    try:
        document = json5.loads(
            path.read_text(encoding="utf-8"), allow_duplicate_keys=False
        )
    except ValueError as error:
        raise ValueError(f"{path}: invalid JSON5: {error}") from error
    try:
        return NodeConfig.model_validate(document)
    except ValidationError as error:
        raise ValueError(f"{path}: invalid node configuration:\n{error}") from error


def publish_frames(
    capture: cv2.VideoCapture,
    publisher: zenoh.Publisher,
    stop_event: Event,
    *,
    device_key: str,
    size: tuple[int, int],
    quality: int,
    frequency_hz: float,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    jpeg_options = [cv2.IMWRITE_JPEG_QUALITY, quality]
    consecutive_grab_failures = 0
    consecutive_retrieve_failures = 0
    period = 1.0 / frequency_hz
    deadline_tolerance = min(DEADLINE_TOLERANCE_SECONDS, period * 1e-6)
    schedule_start: float | None = None
    next_slot = 0
    missed_slots = 0

    while not stop_event.is_set():
        success = capture.grab()
        if not success:
            consecutive_grab_failures += 1
            if consecutive_grab_failures >= MAX_CONSECUTIVE_CAPTURE_FAILURES:
                raise RuntimeError(
                    f"camera grab failed {consecutive_grab_failures} consecutive times"
                )
            if stop_event.wait(CAPTURE_RETRY_DELAY_SECONDS):
                return
            continue

        consecutive_grab_failures = 0
        grabbed_at = clock()
        newly_missed_slots = 0
        if schedule_start is not None:
            deadline = schedule_start + next_slot * period
            if grabbed_at < deadline - deadline_tolerance:
                continue
            overdue = max(0.0, grabbed_at - deadline)
            newly_missed_slots = math.floor((overdue + deadline_tolerance) / period)

        success, frame = capture.retrieve()
        if (
            not success
            or frame is None
            or frame.size == 0
            or frame.ndim < 2
            or frame.shape[1] == 0
        ):
            consecutive_retrieve_failures += 1
            if consecutive_retrieve_failures >= MAX_CONSECUTIVE_CAPTURE_FAILURES:
                raise RuntimeError(
                    "camera retrieve failed "
                    f"{consecutive_retrieve_failures} consecutive times"
                )
            if stop_event.wait(CAPTURE_RETRY_DELAY_SECONDS):
                return
            continue

        consecutive_retrieve_failures = 0
        if schedule_start is None:
            schedule_start = grabbed_at
        frame = cv2.resize(frame, size)
        encoded, jpeg = cv2.imencode(".jpg", frame, jpeg_options)
        if not encoded:
            raise RuntimeError("failed to encode camera frame as JPEG")

        publisher.put(jpeg.tobytes())
        if stop_event.is_set():
            return

        previous_missed_slots = missed_slots
        missed_slots += newly_missed_slots
        next_slot += newly_missed_slots + 1
        if newly_missed_slots == 0:
            continue

        if _crossed_missed_slot_log_threshold(previous_missed_slots, missed_slots):
            LOGGER.warning(
                "Camera '%s' missed %d publish deadline(s) in total "
                "(%d since the previous frame).",
                device_key,
                missed_slots,
                newly_missed_slots,
            )


def _crossed_missed_slot_log_threshold(previous: int, current: int) -> bool:
    if previous == 0 and current > 0:
        return True
    return current // MISSED_SLOT_LOG_INTERVAL > previous // MISSED_SLOT_LOG_INTERVAL


def _camera_worker(
    camera: CameraConfig,
    capture: cv2.VideoCapture,
    publisher: zenoh.Publisher,
    stop_event: Event,
    worker_failures: Queue[tuple[str, Exception]],
) -> None:
    try:
        publish_frames(
            capture,
            publisher,
            stop_event,
            device_key=camera.device_key,
            size=camera.size,
            quality=camera.jpeg_quality,
            frequency_hz=camera.publisher.publish_frequency_hz,
        )
    except Exception as error:
        worker_failures.put((camera.device_key, error))
        stop_event.set()


def _shutdown_workers(
    stop_event: Event, resources: ExitStack, threads: Sequence[Thread]
) -> None:
    stop_event.set()
    shutdown_deadline = time.monotonic() + WORKER_SHUTDOWN_TIMEOUT_SECONDS
    cooperative_deadline = min(
        shutdown_deadline,
        time.monotonic() + WORKER_COOPERATIVE_SHUTDOWN_SECONDS,
    )
    for thread in threads:
        thread.join(max(0.0, cooperative_deadline - time.monotonic()))

    try:
        resources.close()
    finally:
        for thread in threads:
            if thread.is_alive():
                thread.join(max(0.0, shutdown_deadline - time.monotonic()))

    unresponsive = [thread.name for thread in threads if thread.is_alive()]
    if unresponsive:
        LOGGER.error(
            "Camera worker(s) did not stop within %.1f seconds: %s",
            WORKER_SHUTDOWN_TIMEOUT_SECONDS,
            ", ".join(unresponsive),
        )


def run(zenoh_config: zenoh.Config, node_config: NodeConfig) -> None:
    zenoh.init_log_from_env_or("error")

    print("[INFO] Open Zenoh session...")
    with zenoh.open(zenoh_config) as session, ExitStack() as resources:
        camera_resources: list[
            tuple[CameraConfig, cv2.VideoCapture, zenoh.Publisher]
        ] = []
        for camera in node_config.cameras:
            opencv_source = camera.source.opencv_source
            print(f"[INFO] Open camera '{camera.device_key}' from {opencv_source!r}...")
            capture = cv2.VideoCapture(opencv_source)
            resources.callback(capture.release)
            if not capture.isOpened():
                raise RuntimeError(
                    f"could not open camera '{camera.device_key}' from {opencv_source!r}"
                )

            key_expression = f"{node_config.base_key}/{camera.device_key}"
            publisher = resources.enter_context(
                session.declare_publisher(
                    key_expression,
                    encoding="image/jpeg",
                    congestion_control=CONGESTION_CONTROLS[
                        camera.publisher.congestion_control
                    ],
                    reliability=RELIABILITIES[camera.publisher.reliability],
                )
            )
            print(
                f"[INFO] Publishing {camera.size[0]}x{camera.size[1]} JPEG frames on "
                f"'{key_expression}' at {camera.publisher.publish_frequency_hz:g} Hz..."
            )
            camera_resources.append((camera, capture, publisher))

        stop_event = Event()
        worker_failures: Queue[tuple[str, Exception]] = Queue()
        started_threads: list[Thread] = []
        try:
            for camera, capture, publisher in camera_resources:
                thread = Thread(
                    target=_camera_worker,
                    args=(camera, capture, publisher, stop_event, worker_failures),
                    name=f"camera-{camera.device_key}",
                    daemon=True,
                )
                thread.start()
                started_threads.append(thread)

            print("[INFO] Press CTRL-C to quit.")
            while True:
                try:
                    device_key, error = worker_failures.get(
                        timeout=WORKER_POLL_INTERVAL_SECONDS
                    )
                except Empty:
                    if not any(thread.is_alive() for thread in started_threads):
                        return
                    continue
                raise RuntimeError(f"camera '{device_key}' failed: {error}") from error
        finally:
            _shutdown_workers(stop_event, resources, started_threads)


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
