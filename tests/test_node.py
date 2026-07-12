from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

import node


class FakeCapture:
    def __init__(self, frame: np.ndarray) -> None:
        self.frame = frame
        self.read_count = 0

    def read(self) -> tuple[bool, np.ndarray | None]:
        self.read_count += 1
        if self.read_count == 1:
            return True, self.frame
        raise KeyboardInterrupt


class FailedCapture:
    def read(self) -> tuple[bool, None]:
        return False, None


class FakePublisher:
    def __init__(self) -> None:
        self.payloads: list[bytes] = []

    def put(self, payload: bytes) -> None:
        self.payloads.append(payload)


class NodeTests(unittest.TestCase):
    def test_defaults_follow_zenoh_zcam_demo(self) -> None:
        args = node.build_parser().parse_args([])

        self.assertEqual(args.key, "demo/zcam")
        self.assertEqual(args.width, 500)
        self.assertEqual(args.quality, 95)
        self.assertEqual(args.delay, 0.05)

    def test_command_line_options_override_zenoh_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "zenoh.json5"
            config_path.write_text(
                '{mode: "peer", connect: {endpoints: ["tcp/old:7447"]}}'
            )
            args = node.build_parser().parse_args(
                [
                    "--config",
                    str(config_path),
                    "--mode",
                    "client",
                    "--connect",
                    "tcp/127.0.0.1:7447",
                    "--listen",
                    "tcp/0.0.0.0:7448",
                    "--no-multicast-scouting",
                    "--cfg",
                    "transport/unicast/max_links:2",
                    "--cfg",
                    'mode:"peer"',
                ]
            )

            config = node.zenoh_config_from_args(args)

        self.assertEqual(json.loads(config.get_json("mode")), "peer")
        self.assertEqual(
            json.loads(config.get_json("connect/endpoints")),
            ["tcp/127.0.0.1:7447"],
        )
        self.assertEqual(
            json.loads(config.get_json("listen/endpoints")),
            ["tcp/0.0.0.0:7448"],
        )
        self.assertFalse(json.loads(config.get_json("scouting/multicast/enabled")))
        self.assertEqual(json.loads(config.get_json("transport/unicast/max_links")), 2)

    def test_invalid_cfg_is_reported(self) -> None:
        args = node.build_parser().parse_args(["--cfg", "missing-separator"])

        with self.assertRaisesRegex(ValueError, "expected KEY:VALUE"):
            node.zenoh_config_from_args(args)

    def test_publish_frames_resizes_and_sends_jpeg(self) -> None:
        capture = FakeCapture(np.zeros((8, 16, 3), dtype=np.uint8))
        publisher = FakePublisher()

        with (
            patch("node.time.sleep"),
            self.assertRaises(KeyboardInterrupt),
        ):
            node.publish_frames(
                capture,  # type: ignore[arg-type]
                publisher,  # type: ignore[arg-type]
                width=12,
                quality=80,
                delay=0,
            )

        self.assertEqual(len(publisher.payloads), 1)
        jpeg = np.frombuffer(publisher.payloads[0], dtype=np.uint8)
        decoded = node.cv2.imdecode(jpeg, node.cv2.IMREAD_COLOR)
        if decoded is None:
            self.fail("published payload was not a valid JPEG")
        self.assertEqual(decoded.shape[:2], (6, 12))

    def test_publish_frames_keeps_calculated_height_at_least_one(self) -> None:
        capture = FakeCapture(np.zeros((1, 3, 3), dtype=np.uint8))
        publisher = FakePublisher()

        with patch("node.time.sleep"), self.assertRaises(KeyboardInterrupt):
            node.publish_frames(
                capture,  # type: ignore[arg-type]
                publisher,  # type: ignore[arg-type]
                width=1,
                quality=80,
                delay=0,
            )

        jpeg = np.frombuffer(publisher.payloads[0], dtype=np.uint8)
        decoded = node.cv2.imdecode(jpeg, node.cv2.IMREAD_COLOR)
        if decoded is None:
            self.fail("published payload was not a valid JPEG")
        self.assertEqual(decoded.shape[:2], (1, 1))

    def test_publish_frames_stops_after_repeated_read_failures(self) -> None:
        with (
            patch("node.time.sleep") as sleep,
            self.assertRaisesRegex(RuntimeError, "camera read failed 10"),
        ):
            node.publish_frames(
                FailedCapture(),  # type: ignore[arg-type]
                FakePublisher(),  # type: ignore[arg-type]
                width=12,
                quality=80,
                delay=60,
            )

        self.assertEqual(sleep.call_count, node.MAX_CAMERA_READ_FAILURES - 1)
        sleep.assert_called_with(node.CAMERA_READ_RETRY_DELAY)

    def test_run_releases_resources_when_publishing_fails(self) -> None:
        capture = MagicMock()
        capture.isOpened.return_value = True
        opened_session = MagicMock()
        session = opened_session.__enter__.return_value
        declared_publisher = session.declare_publisher.return_value

        with (
            patch("node.zenoh.init_log_from_env_or"),
            patch("node.zenoh.open", return_value=opened_session),
            patch("node.cv2.VideoCapture", return_value=capture),
            patch("node.publish_frames", side_effect=RuntimeError("put failed")),
            self.assertRaisesRegex(RuntimeError, "put failed"),
        ):
            node.run(
                node.zenoh.Config(),
                device=0,
                key="demo/zcam",
                width=500,
                quality=95,
                delay=0.05,
            )

        capture.release.assert_called_once()
        session.declare_publisher.assert_called_once_with(
            "demo/zcam",
            encoding="image/jpeg",
            congestion_control=node.zenoh.CongestionControl.DROP,
            reliability=node.zenoh.Reliability.BEST_EFFORT,
        )
        declared_publisher.__exit__.assert_called_once()
        opened_session.__exit__.assert_called_once()

    def test_invalid_quality_is_rejected(self) -> None:
        with self.assertRaises(SystemExit):
            node.build_parser().parse_args(["--quality", "101"])

    def test_non_finite_delay_is_rejected(self) -> None:
        for value in ("nan", "inf", "-inf"):
            with self.subTest(value=value), self.assertRaises(SystemExit):
                node.build_parser().parse_args([f"--delay={value}"])


if __name__ == "__main__":
    unittest.main()
