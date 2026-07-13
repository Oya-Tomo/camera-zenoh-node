from __future__ import annotations

import copy
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

import node

VALID_NODE_CONFIG: dict[str, object] = {
    "camera": {
        "device": 0,
        "width": 500,
        "jpeg_quality": 95,
    },
    "publisher": {
        "key_expression": "camera/frame",
        "frame_delay_seconds": 0.05,
        "congestion_control": "drop",
        "reliability": "best_effort",
    },
}


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


def load_document(document: object) -> node.NodeConfig:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "node-config.json5"
        path.write_text(json.dumps(document), encoding="utf-8")
        return node.load_node_config(path)


class NodeTests(unittest.TestCase):
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
            ("--no-multicast-scouting",),
            ("--cfg", "mode:'peer'"),
            ("--device", "1"),
            ("--width", "1280"),
            ("--quality", "80"),
            ("--delay", "0.03"),
            ("--key", "camera/front"),
            ("-m", "peer"),
            ("-e", "tcp/127.0.0.1:7447"),
            ("-l", "tcp/0.0.0.0:7447"),
            ("-c", "zenoh.json5"),
            ("-w", "1280"),
            ("-q", "80"),
            ("-d", "0.03"),
            ("-k", "camera/front"),
        )
        for arguments in old_arguments:
            with (
                self.subTest(arguments=arguments),
                redirect_stderr(StringIO()),
                self.assertRaises(SystemExit),
            ):
                node.build_parser().parse_args(arguments)

    def test_example_configs_are_valid(self) -> None:
        node_config = node.load_node_config(Path("config/node-config.example.json"))
        zenoh_config = node.zenoh.Config.from_file(
            Path("config/zenoh-config.example.json5")
        )

        self.assertEqual(node_config.publisher.key_expression, "camera/frame")
        self.assertEqual(json.loads(zenoh_config.get_json("mode")), "peer")

    def test_node_config_accepts_json5(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "node-config.json5"
            path.write_text(
                """
                {
                  // JSON5 comments and trailing commas are supported.
                  camera: {device: 1, width: 1280, jpeg_quality: 80},
                  publisher: {
                    key_expression: 'camera/front',
                    frame_delay_seconds: 0,
                    congestion_control: 'block',
                    reliability: 'reliable',
                  },
                }
                """,
                encoding="utf-8",
            )

            config = node.load_node_config(path)

        self.assertEqual(config.camera.device, 1)
        self.assertEqual(config.camera.width, 1280)
        self.assertEqual(
            config.publisher.congestion_control, node.zenoh.CongestionControl.BLOCK
        )
        self.assertEqual(config.publisher.reliability, node.zenoh.Reliability.RELIABLE)

    def test_node_config_rejects_duplicate_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "node-config.json5"
            path.write_text(
                """
                {
                  camera: {device: 0, device: 1, width: 500, jpeg_quality: 95},
                  publisher: {
                    key_expression: 'camera/frame',
                    frame_delay_seconds: 0.05,
                    congestion_control: 'drop',
                    reliability: 'best_effort',
                  },
                }
                """,
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "(?i)duplicate key"):
                node.load_node_config(path)

    def test_node_config_rejects_missing_and_unknown_keys(self) -> None:
        missing = copy.deepcopy(VALID_NODE_CONFIG)
        del missing["camera"]["width"]  # type: ignore[index]
        unknown = copy.deepcopy(VALID_NODE_CONFIG)
        unknown["publisher"]["topic"] = "camera/frame"  # type: ignore[index]

        for document, message in (
            (missing, "is missing: width"),
            (unknown, "has unknown keys: topic"),
        ):
            with (
                self.subTest(message=message),
                self.assertRaisesRegex(ValueError, message),
            ):
                load_document(document)

    def test_node_config_rejects_invalid_values(self) -> None:
        cases = (
            (("camera", "device"), True, "device must be an integer"),
            (("camera", "width"), 0, "width must be >= 1"),
            (("camera", "jpeg_quality"), 101, "jpeg_quality must be 0..100"),
            (
                ("publisher", "frame_delay_seconds"),
                float("nan"),
                "frame_delay_seconds must be a finite number >= 0",
            ),
            (
                ("publisher", "congestion_control"),
                "discard",
                "congestion_control must be one of",
            ),
            (("publisher", "reliability"), "sometimes", "reliability must be one of"),
            (
                ("publisher", "key_expression"),
                "camera//frame",
                "must be a valid Zenoh key expression",
            ),
            (
                ("publisher", "key_expression"),
                "camera/*",
                "must be a concrete Zenoh key without wildcards",
            ),
            (
                ("publisher", "key_expression"),
                "camera/**",
                "must be a concrete Zenoh key without wildcards",
            ),
            (
                ("publisher", "frame_delay_seconds"),
                10**4000,
                "frame_delay_seconds must be a finite number >= 0",
            ),
        )

        for (section, key), value, message in cases:
            document = copy.deepcopy(VALID_NODE_CONFIG)
            document[section][key] = value  # type: ignore[index]
            with (
                self.subTest(section=section, key=key),
                self.assertRaisesRegex(ValueError, message),
            ):
                load_document(document)

    def test_publish_frames_resizes_and_sends_jpeg(self) -> None:
        capture = FakeCapture(np.zeros((8, 16, 3), dtype=np.uint8))
        publisher = FakePublisher()

        with patch("node.time.sleep"), self.assertRaises(KeyboardInterrupt):
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
        node_config = load_document(VALID_NODE_CONFIG)

        with (
            patch("node.zenoh.init_log_from_env_or"),
            patch("node.zenoh.open", return_value=opened_session),
            patch("node.cv2.VideoCapture", return_value=capture),
            patch("node.publish_frames", side_effect=RuntimeError("put failed")),
            redirect_stdout(StringIO()),
            self.assertRaisesRegex(RuntimeError, "put failed"),
        ):
            node.run(node.zenoh.Config(), node_config)

        capture.release.assert_called_once()
        session.declare_publisher.assert_called_once_with(
            "camera/frame",
            encoding="image/jpeg",
            congestion_control=node.zenoh.CongestionControl.DROP,
            reliability=node.zenoh.Reliability.BEST_EFFORT,
        )
        declared_publisher.__exit__.assert_called_once()
        opened_session.__exit__.assert_called_once()

    def test_main_uses_only_the_selected_config_files(self) -> None:
        node_config = load_document(VALID_NODE_CONFIG)
        zenoh_config = MagicMock()

        with (
            patch("node.load_node_config", return_value=node_config) as load,
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
        run.assert_called_once_with(zenoh_config, node_config)


if __name__ == "__main__":
    unittest.main()
