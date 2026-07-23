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

import cv2
import zenoh

from config import CameraConfig, NodeConfig, load_node_config

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

            key_expression = node_config.publisher_key(camera)
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
