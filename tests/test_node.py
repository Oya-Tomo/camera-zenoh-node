from __future__ import annotations

import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from queue import Queue
from threading import current_thread
from unittest.mock import MagicMock, call, patch

import numpy as np

import node
from config import CameraConfig, NodeConfig

NODE_CONFIG = NodeConfig.model_validate(
    {
        "zenoh_key_prefix": "camera/node",
        "cameras": [
            {
                "device_key": "front",
                "source": {
                    "path": "/dev/v4l/by-id/usb-Example_Front_Camera-video-index0"
                },
                "size": [1280, 720],
                "jpeg_quality": 90,
                "publisher": {
                    "publish_frequency_hz": 30.0,
                    "congestion_control": "drop",
                    "reliability": "best_effort",
                },
            },
            {
                "device_key": "rear",
                "source": {"index": 1},
                "size": [640, 480],
                "jpeg_quality": 85,
                "publisher": {
                    "publish_frequency_hz": 20,
                    "congestion_control": "block",
                    "reliability": "reliable",
                },
            },
        ],
    }
)


class ManualClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def advance_to(self, timestamp: float) -> None:
        self.now = max(self.now, timestamp)


class FakeEvent:
    def __init__(self, clock: ManualClock) -> None:
        self.clock = clock
        self.wait_calls: list[float] = []
        self._is_set = False

    def is_set(self) -> bool:
        return self._is_set

    def set(self) -> None:
        self._is_set = True

    def wait(self, timeout: float) -> bool:
        self.wait_calls.append(timeout)
        if not self._is_set:
            self.clock.advance(timeout)
        return self._is_set


class FakeCapture:
    def __init__(
        self,
        frame: np.ndarray,
        clock: ManualClock,
        grab_times: tuple[float, ...],
    ) -> None:
        self.frame = frame
        self.clock = clock
        self.grab_times = grab_times
        self.grab_count = 0
        self.retrieve_grab_counts: list[int] = []
        self.retrieve_times: list[float] = []

    def grab(self) -> bool:
        self.clock.advance_to(self.grab_times[self.grab_count])
        self.grab_count += 1
        return True

    def retrieve(self) -> tuple[bool, np.ndarray]:
        self.retrieve_grab_counts.append(self.grab_count)
        self.retrieve_times.append(self.clock())
        return True, self.frame


class FailedCapture:
    def grab(self) -> bool:
        return False

    def retrieve(self) -> tuple[bool, None]:
        raise AssertionError("retrieve must not be called after a failed grab")


class FailedRetrieveCapture:
    def grab(self) -> bool:
        return True

    def retrieve(self) -> tuple[bool, None]:
        return False, None


class FakePublisher:
    def __init__(
        self,
        event: FakeEvent,
        clock: ManualClock,
        *,
        stop_after: int,
        processing_times: tuple[float, ...] = (),
    ) -> None:
        self.event = event
        self.clock = clock
        self.stop_after = stop_after
        self.processing_times = processing_times
        self.payloads: list[bytes] = []

    def put(self, payload: bytes) -> None:
        self.payloads.append(payload)
        index = len(self.payloads) - 1
        if index < len(self.processing_times):
            self.clock.advance(self.processing_times[index])
        if len(self.payloads) >= self.stop_after:
            self.event.set()


class NodeCliTests(unittest.TestCase):
    def test_default_config_paths(self) -> None:
        args = node.build_parser().parse_args([])

        self.assertEqual(args.zenoh_config, Path("config/zenoh-config.json5"))
        self.assertEqual(args.node_config, Path("config/node-config.json5"))

    def test_removed_cli_overrides_are_rejected(self) -> None:
        old_arguments = (
            ("--mode", "peer"),
            ("--connect", "tcp/127.0.0.1:7447"),
            ("--listen", "tcp/0.0.0.0:7447"),
            ("--config", "zenoh.json5"),
            ("--device", "1"),
            ("--width", "1280"),
            ("--quality", "80"),
            ("--delay", "0.03"),
            ("--key", "camera/front"),
        )
        for arguments in old_arguments:
            with (
                self.subTest(arguments=arguments),
                redirect_stderr(StringIO()),
                self.assertRaises(SystemExit),
            ):
                node.build_parser().parse_args(arguments)


class FramePublisherTests(unittest.TestCase):
    def test_publish_frames_resizes_and_sends_jpeg(self) -> None:
        clock = ManualClock()
        event = FakeEvent(clock)
        capture = FakeCapture(
            np.zeros((8, 16, 3), dtype=np.uint8), clock, grab_times=(0.0,)
        )
        publisher = FakePublisher(event, clock, stop_after=1)

        node.publish_frames(
            capture,  # type: ignore[arg-type]
            publisher,  # type: ignore[arg-type]
            event,  # type: ignore[arg-type]
            device_key="front",
            size=(12, 6),
            quality=80,
            frequency_hz=10,
            clock=clock,
        )

        self.assertEqual(len(publisher.payloads), 1)
        jpeg = np.frombuffer(publisher.payloads[0], dtype=np.uint8)
        decoded = node.cv2.imdecode(jpeg, node.cv2.IMREAD_COLOR)
        if decoded is None:
            self.fail("published payload was not a valid JPEG")
        self.assertEqual(decoded.shape[:2], (6, 12))

    def test_publish_frames_uses_absolute_deadlines_without_drift(self) -> None:
        clock = ManualClock()
        event = FakeEvent(clock)
        capture = FakeCapture(
            np.zeros((2, 2, 3), dtype=np.uint8),
            clock,
            grab_times=tuple(index * 0.02 for index in range(16)),
        )
        publisher = FakePublisher(
            event,
            clock,
            stop_after=4,
            processing_times=(0.03, 0.03, 0.03, 0.03),
        )

        node.publish_frames(
            capture,  # type: ignore[arg-type]
            publisher,  # type: ignore[arg-type]
            event,  # type: ignore[arg-type]
            device_key="front",
            size=(2, 2),
            quality=80,
            frequency_hz=10,
            clock=clock,
        )

        self.assertEqual(len(capture.retrieve_times), 4)
        for actual, expected in zip(
            capture.retrieve_times, (0.0, 0.1, 0.2, 0.3), strict=True
        ):
            self.assertAlmostEqual(actual, expected)

    def test_publish_frames_drains_intermediate_camera_frames(self) -> None:
        clock = ManualClock()
        event = FakeEvent(clock)
        capture = FakeCapture(
            np.zeros((2, 2, 3), dtype=np.uint8),
            clock,
            grab_times=tuple(index * 0.02 for index in range(12)),
        )
        publisher = FakePublisher(event, clock, stop_after=3)

        node.publish_frames(
            capture,  # type: ignore[arg-type]
            publisher,  # type: ignore[arg-type]
            event,  # type: ignore[arg-type]
            device_key="front",
            size=(2, 2),
            quality=80,
            frequency_hz=10,
            clock=clock,
        )

        self.assertEqual(capture.retrieve_grab_counts, [1, 6, 11])
        self.assertEqual(len(publisher.payloads), 3)

    def test_publish_frames_skips_missed_slots_without_catch_up_burst(self) -> None:
        clock = ManualClock()
        event = FakeEvent(clock)
        capture = FakeCapture(
            np.zeros((2, 2, 3), dtype=np.uint8),
            clock,
            grab_times=tuple(index * 0.05 for index in range(8)),
        )
        publisher = FakePublisher(
            event,
            clock,
            stop_after=3,
            processing_times=(0.25, 0.0, 0.0),
        )

        with self.assertLogs(node.LOGGER, level="WARNING") as logs:
            node.publish_frames(
                capture,  # type: ignore[arg-type]
                publisher,  # type: ignore[arg-type]
                event,  # type: ignore[arg-type]
                device_key="front",
                size=(2, 2),
                quality=80,
                frequency_hz=10,
                clock=clock,
            )

        self.assertEqual(len(capture.retrieve_times), 3)
        self.assertAlmostEqual(capture.retrieve_times[0], 0.0)
        self.assertAlmostEqual(capture.retrieve_times[1], 0.25)
        self.assertAlmostEqual(capture.retrieve_times[2], 0.3)
        self.assertEqual(len(logs.output), 1)
        self.assertIn("missed 1 publish deadline", logs.output[0])

    def test_drop_log_thresholds_are_throttled(self) -> None:
        self.assertTrue(node._crossed_missed_slot_log_threshold(0, 1))
        self.assertFalse(node._crossed_missed_slot_log_threshold(1, 9))
        self.assertTrue(node._crossed_missed_slot_log_threshold(9, 10))
        self.assertFalse(node._crossed_missed_slot_log_threshold(10, 19))
        self.assertTrue(node._crossed_missed_slot_log_threshold(19, 20))
        self.assertTrue(node._crossed_missed_slot_log_threshold(0, 100_000))

    def test_publish_frames_does_not_log_after_stop_during_put(self) -> None:
        clock = ManualClock()
        event = FakeEvent(clock)
        capture = FakeCapture(
            np.zeros((2, 2, 3), dtype=np.uint8),
            clock,
            grab_times=(0.0, 0.05),
        )
        publisher = FakePublisher(
            event,
            clock,
            stop_after=2,
            processing_times=(0.25, 0.0),
        )

        with patch.object(node.LOGGER, "warning") as warning:
            node.publish_frames(
                capture,  # type: ignore[arg-type]
                publisher,  # type: ignore[arg-type]
                event,  # type: ignore[arg-type]
                device_key="front",
                size=(2, 2),
                quality=80,
                frequency_hz=10,
                clock=clock,
            )

        warning.assert_not_called()

    def test_publish_frames_stops_after_repeated_grab_failures(self) -> None:
        clock = ManualClock()
        event = FakeEvent(clock)
        with self.assertRaisesRegex(RuntimeError, "camera grab failed 10"):
            node.publish_frames(
                FailedCapture(),  # type: ignore[arg-type]
                MagicMock(),
                event,  # type: ignore[arg-type]
                device_key="front",
                size=(12, 6),
                quality=80,
                frequency_hz=10,
                clock=clock,
            )

        retry_waits = [
            timeout
            for timeout in event.wait_calls
            if timeout == node.CAPTURE_RETRY_DELAY_SECONDS
        ]
        self.assertEqual(len(retry_waits), node.MAX_CONSECUTIVE_CAPTURE_FAILURES - 1)

    def test_publish_frames_stops_after_repeated_retrieve_failures(self) -> None:
        clock = ManualClock()
        event = FakeEvent(clock)
        with self.assertRaisesRegex(RuntimeError, "camera retrieve failed 10"):
            node.publish_frames(
                FailedRetrieveCapture(),  # type: ignore[arg-type]
                MagicMock(),
                event,  # type: ignore[arg-type]
                device_key="front",
                size=(12, 6),
                quality=80,
                frequency_hz=10,
                clock=clock,
            )

        retry_waits = [
            timeout
            for timeout in event.wait_calls
            if timeout == node.CAPTURE_RETRY_DELAY_SECONDS
        ]
        self.assertEqual(len(retry_waits), node.MAX_CONSECUTIVE_CAPTURE_FAILURES - 1)


class NodeRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.node_config = NODE_CONFIG

    def test_shutdown_allows_cooperative_exit_before_closing_resources(self) -> None:
        stop_event = node.Event()
        worker_stopped = node.Event()

        def cooperative_worker() -> None:
            stop_event.wait()
            worker_stopped.set()

        thread = node.Thread(target=cooperative_worker, name="camera-front")
        thread.start()
        resources = MagicMock()
        resources.close.side_effect = lambda: self.assertFalse(thread.is_alive())

        node._shutdown_workers(stop_event, resources, [thread])

        self.assertTrue(worker_stopped.is_set())
        resources.close.assert_called_once()

    def test_run_uses_one_session_and_one_worker_per_camera(self) -> None:
        captures = [MagicMock(), MagicMock()]
        for capture in captures:
            capture.isOpened.return_value = True
        opened_session = MagicMock()
        session = opened_session.__enter__.return_value
        publisher_contexts = [MagicMock(), MagicMock()]
        publishers = [context.__enter__.return_value for context in publisher_contexts]
        session.declare_publisher.side_effect = publisher_contexts
        worker_calls: list[tuple[str, str]] = []

        def record_worker(
            camera: CameraConfig,
            capture: object,
            publisher: object,
            stop_event: object,
            failures: object,
        ) -> None:
            worker_calls.append((current_thread().name, camera.device_key))

        with (
            patch("node.zenoh.init_log_from_env_or"),
            patch("node.zenoh.open", return_value=opened_session) as open_session,
            patch("node.cv2.VideoCapture", side_effect=captures) as open_camera,
            patch("node._camera_worker", side_effect=record_worker),
            patch("node.WORKER_POLL_INTERVAL_SECONDS", 0),
            redirect_stdout(StringIO()),
        ):
            node.run(node.zenoh.Config(), self.node_config)

        open_session.assert_called_once()
        self.assertEqual(
            open_camera.call_args_list,
            [
                call("/dev/v4l/by-id/usb-Example_Front_Camera-video-index0"),
                call(1),
            ],
        )
        self.assertCountEqual(
            worker_calls,
            [("camera-front", "front"), ("camera-rear", "rear")],
        )
        session.declare_publisher.assert_has_calls(
            [
                call(
                    "camera/node/front",
                    encoding="image/jpeg",
                    congestion_control=node.zenoh.CongestionControl.DROP,
                    reliability=node.zenoh.Reliability.BEST_EFFORT,
                ),
                call(
                    "camera/node/rear",
                    encoding="image/jpeg",
                    congestion_control=node.zenoh.CongestionControl.BLOCK,
                    reliability=node.zenoh.Reliability.RELIABLE,
                ),
            ]
        )
        for capture in captures:
            capture.release.assert_called_once()
        for context in publisher_contexts:
            context.__exit__.assert_called_once()
        opened_session.__exit__.assert_called_once()
        self.assertEqual(len(publishers), 2)

    def test_run_releases_all_resources_when_second_camera_fails_to_open(self) -> None:
        captures = [MagicMock(), MagicMock()]
        captures[0].isOpened.return_value = True
        captures[1].isOpened.return_value = False
        opened_session = MagicMock()
        session = opened_session.__enter__.return_value
        publisher_context = MagicMock()
        session.declare_publisher.return_value = publisher_context

        with (
            patch("node.zenoh.init_log_from_env_or"),
            patch("node.zenoh.open", return_value=opened_session),
            patch("node.cv2.VideoCapture", side_effect=captures),
            redirect_stdout(StringIO()),
            self.assertRaisesRegex(RuntimeError, "could not open camera 'rear'"),
        ):
            node.run(node.zenoh.Config(), self.node_config)

        for capture in captures:
            capture.release.assert_called_once()
        publisher_context.__exit__.assert_called_once()
        opened_session.__exit__.assert_called_once()

    def test_run_releases_resources_when_second_publisher_fails(self) -> None:
        captures = [MagicMock(), MagicMock()]
        for capture in captures:
            capture.isOpened.return_value = True
        opened_session = MagicMock()
        session = opened_session.__enter__.return_value
        first_publisher_context = MagicMock()
        session.declare_publisher.side_effect = [
            first_publisher_context,
            node.zenoh.ZError("publisher failed"),
        ]

        with (
            patch("node.zenoh.init_log_from_env_or"),
            patch("node.zenoh.open", return_value=opened_session),
            patch("node.cv2.VideoCapture", side_effect=captures),
            redirect_stdout(StringIO()),
            self.assertRaisesRegex(node.zenoh.ZError, "publisher failed"),
        ):
            node.run(node.zenoh.Config(), self.node_config)

        for capture in captures:
            capture.release.assert_called_once()
        first_publisher_context.__exit__.assert_called_once()
        opened_session.__exit__.assert_called_once()

    def test_run_releases_resources_when_second_thread_fails_to_start(self) -> None:
        captures = [MagicMock(), MagicMock()]
        for capture in captures:
            capture.isOpened.return_value = True
        opened_session = MagicMock()
        session = opened_session.__enter__.return_value
        publisher_contexts = [MagicMock(), MagicMock()]
        session.declare_publisher.side_effect = publisher_contexts
        first_thread = MagicMock()
        first_thread.name = "camera-front"
        first_thread.is_alive.return_value = False
        second_thread = MagicMock()
        second_thread.start.side_effect = RuntimeError("thread failed")

        with (
            patch("node.zenoh.init_log_from_env_or"),
            patch("node.zenoh.open", return_value=opened_session),
            patch("node.cv2.VideoCapture", side_effect=captures),
            patch("node.Thread", side_effect=[first_thread, second_thread]),
            redirect_stdout(StringIO()),
            self.assertRaisesRegex(RuntimeError, "thread failed"),
        ):
            node.run(node.zenoh.Config(), self.node_config)

        first_thread.join.assert_called_once()
        for capture in captures:
            capture.release.assert_called_once()
        for context in publisher_contexts:
            context.__exit__.assert_called_once()
        opened_session.__exit__.assert_called_once()

    def test_run_propagates_worker_failure_and_releases_resources(self) -> None:
        captures = [MagicMock(), MagicMock()]
        for capture in captures:
            capture.isOpened.return_value = True
        opened_session = MagicMock()
        session = opened_session.__enter__.return_value
        publisher_contexts = [MagicMock(), MagicMock()]
        session.declare_publisher.side_effect = publisher_contexts

        def fail_front(
            camera: CameraConfig,
            capture: object,
            publisher: object,
            stop_event: object,
            failures: Queue[tuple[str, Exception]],
        ) -> None:
            if camera.device_key == "front":
                failures.put((camera.device_key, RuntimeError("read failed")))

        with (
            patch("node.zenoh.init_log_from_env_or"),
            patch("node.zenoh.open", return_value=opened_session),
            patch("node.cv2.VideoCapture", side_effect=captures),
            patch("node._camera_worker", side_effect=fail_front),
            patch("node.WORKER_POLL_INTERVAL_SECONDS", 0),
            redirect_stdout(StringIO()),
            self.assertRaisesRegex(RuntimeError, "camera 'front' failed: read failed"),
        ):
            node.run(node.zenoh.Config(), self.node_config)

        for capture in captures:
            capture.release.assert_called_once()
        for context in publisher_contexts:
            context.__exit__.assert_called_once()
        opened_session.__exit__.assert_called_once()

    def test_run_does_not_wait_indefinitely_for_unresponsive_worker(self) -> None:
        captures = [MagicMock(), MagicMock()]
        for capture in captures:
            capture.isOpened.return_value = True
        opened_session = MagicMock()
        session = opened_session.__enter__.return_value
        publisher_contexts = [MagicMock(), MagicMock()]
        session.declare_publisher.side_effect = publisher_contexts
        release_worker = node.Event()

        def fail_or_block(
            camera: CameraConfig,
            capture: object,
            publisher: object,
            stop_event: object,
            failures: Queue[tuple[str, Exception]],
        ) -> None:
            if camera.device_key == "front":
                failures.put((camera.device_key, RuntimeError("read failed")))
                return
            release_worker.wait()

        try:
            with (
                patch("node.zenoh.init_log_from_env_or"),
                patch("node.zenoh.open", return_value=opened_session),
                patch("node.cv2.VideoCapture", side_effect=captures),
                patch("node._camera_worker", side_effect=fail_or_block),
                patch("node.WORKER_SHUTDOWN_TIMEOUT_SECONDS", 0),
                redirect_stdout(StringIO()),
                self.assertLogs(node.LOGGER, level="ERROR") as logs,
                self.assertRaisesRegex(
                    RuntimeError, "camera 'front' failed: read failed"
                ),
            ):
                node.run(node.zenoh.Config(), self.node_config)
        finally:
            release_worker.set()

        self.assertEqual(len(logs.output), 1)
        self.assertIn("camera-rear", logs.output[0])
        for capture in captures:
            capture.release.assert_called_once()
        for context in publisher_contexts:
            context.__exit__.assert_called_once()

    def test_camera_worker_reports_failure_and_stops_siblings(self) -> None:
        stop_event = node.Event()
        failures: Queue[tuple[str, Exception]] = Queue()
        camera = self.node_config.cameras[0]

        with patch("node.publish_frames", side_effect=RuntimeError("encode failed")):
            node._camera_worker(
                camera,
                MagicMock(),
                MagicMock(),
                stop_event,
                failures,
            )

        self.assertTrue(stop_event.is_set())
        device_key, error = failures.get_nowait()
        self.assertEqual(device_key, "front")
        self.assertEqual(str(error), "encode failed")

    def test_main_uses_only_the_selected_config_files(self) -> None:
        zenoh_config = MagicMock()

        with (
            patch("node.load_node_config", return_value=self.node_config) as load,
            patch(
                "node.zenoh.Config.from_file", return_value=zenoh_config
            ) as from_file,
            patch("node.run") as run,
        ):
            result = node.main(
                [
                    "--zenoh-config",
                    "custom-zenoh.json5",
                    "--node-config",
                    "custom-node.json5",
                ]
            )

        self.assertEqual(result, 0)
        load.assert_called_once_with(Path("custom-node.json5"))
        from_file.assert_called_once_with(Path("custom-zenoh.json5"))
        run.assert_called_once_with(zenoh_config, self.node_config)


if __name__ == "__main__":
    unittest.main()
