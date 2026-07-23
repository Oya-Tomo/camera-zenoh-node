from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

import zenoh

from config import NodeConfig, load_node_config

VALID_NODE_CONFIG: dict[str, object] = {
    "zenoh_key_prefix": "camera/node",
    "cameras": [
        {
            "device_key": "front",
            "source": {"path": "/dev/v4l/by-id/usb-Example_Front_Camera-video-index0"},
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


def load_document(document: object) -> NodeConfig:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "node-config.json5"
        path.write_text(json.dumps(document), encoding="utf-8")
        return load_node_config(path)


class ConfigTests(unittest.TestCase):
    def test_example_configs_are_valid(self) -> None:
        node_config = load_node_config(Path("config/node-config.example.json5"))
        zenoh_config = zenoh.Config.from_file(Path("config/zenoh-config.example.json5"))

        self.assertEqual(node_config.zenoh_key_prefix, "camera")
        self.assertEqual(
            [camera.device_key for camera in node_config.cameras], ["front", "rear"]
        )
        self.assertEqual(
            [node_config.publisher_key(camera) for camera in node_config.cameras],
            ["camera/front", "camera/rear"],
        )
        self.assertEqual(json.loads(zenoh_config.get_json("mode")), "peer")

    def test_node_config_accepts_json5_and_both_source_types(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "node-config.json5"
            path.write_text(
                """
                {
                  // JSON5 comments and trailing commas are supported.
                  zenoh_key_prefix: 'site/camera',
                  cameras: [
                    {
                      device_key: 'front',
                      source: {path: '/dev/v4l/by-id/front'},
                      size: [1280, 720],
                      jpeg_quality: 80,
                      publisher: {
                        publish_frequency_hz: 30,
                        congestion_control: 'block',
                        reliability: 'reliable',
                      },
                    },
                    {
                      device_key: 'rear',
                      source: {index: 1},
                      size: [640, 480],
                      jpeg_quality: 75,
                      publisher: {
                        publish_frequency_hz: 15.5,
                        congestion_control: 'drop',
                        reliability: 'best_effort',
                      },
                    },
                  ],
                }
                """,
                encoding="utf-8",
            )

            config = load_node_config(path)

        self.assertEqual(config.cameras[0].source.opencv_source, "/dev/v4l/by-id/front")
        self.assertEqual(config.cameras[1].source.opencv_source, 1)
        self.assertEqual(config.cameras[0].publisher.publish_frequency_hz, 30.0)

    def test_node_config_rejects_duplicate_json_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "node-config.json5"
            path.write_text(
                "{zenoh_key_prefix: 'a', zenoh_key_prefix: 'b', cameras: []}"
            )

            with self.assertRaisesRegex(ValueError, "(?i)duplicate key"):
                load_node_config(path)

    def test_node_config_rejects_old_base_key(self) -> None:
        document = copy.deepcopy(VALID_NODE_CONFIG)
        document["base_key"] = document.pop("zenoh_key_prefix")

        with self.assertRaisesRegex(ValueError, "zenoh_key_prefix|base_key"):
            load_document(document)

    def test_node_config_rejects_empty_camera_list_and_unknown_fields(self) -> None:
        for document, message in (
            ({"zenoh_key_prefix": "camera", "cameras": []}, "too_short"),
            (
                {**copy.deepcopy(VALID_NODE_CONFIG), "endpoint": "tcp/localhost:7447"},
                "extra_forbidden",
            ),
        ):
            with (
                self.subTest(message=message),
                self.assertRaisesRegex(ValueError, message),
            ):
                load_document(document)

    def test_node_config_rejects_invalid_source_selectors(self) -> None:
        cases = (
            ({}, "exactly one of index or path"),
            ({"index": 0, "path": "/dev/video0"}, "exactly one of index or path"),
            ({"index": -1}, "greater_than_equal"),
            ({"index": True}, "int_type"),
            ({"path": " "}, "path must be a non-empty string"),
            ({"serial": "abc"}, "extra_forbidden"),
        )
        for source, message in cases:
            document = copy.deepcopy(VALID_NODE_CONFIG)
            document["cameras"][0]["source"] = source  # type: ignore[index]
            with (
                self.subTest(source=source),
                self.assertRaisesRegex(ValueError, message),
            ):
                load_document(document)

    def test_node_config_rejects_invalid_keys(self) -> None:
        cases = (
            ("zenoh_key_prefix", "camera/*", "concrete Zenoh key"),
            ("zenoh_key_prefix", "camera//node", "valid Zenoh key expression"),
            ("device_key", "front/left", "single key segment"),
            ("device_key", "*", "concrete Zenoh key"),
        )
        for field, value, message in cases:
            document = copy.deepcopy(VALID_NODE_CONFIG)
            if field == "zenoh_key_prefix":
                document[field] = value
            else:
                document["cameras"][0][field] = value  # type: ignore[index]
            with (
                self.subTest(field=field, value=value),
                self.assertRaisesRegex(ValueError, message),
            ):
                load_document(document)

    def test_node_config_rejects_duplicate_camera_identity(self) -> None:
        duplicate_key = copy.deepcopy(VALID_NODE_CONFIG)
        duplicate_key["cameras"][1]["device_key"] = "front"  # type: ignore[index]
        duplicate_source = copy.deepcopy(VALID_NODE_CONFIG)
        duplicate_source["cameras"][1]["source"] = copy.deepcopy(  # type: ignore[index]
            duplicate_source["cameras"][0]["source"]  # type: ignore[index]
        )

        for document, message in (
            (duplicate_key, "duplicate device_key"),
            (duplicate_source, "duplicate camera source"),
        ):
            with (
                self.subTest(message=message),
                self.assertRaisesRegex(ValueError, message),
            ):
                load_document(document)

    def test_node_config_rejects_invalid_camera_and_publisher_values(self) -> None:
        cases = (
            ("size", [0, 720], "greater_than_equal"),
            ("size", [1280], "missing"),
            ("size", [1280, 720, 3], "too_long"),
            ("size", [True, 720], "int_type"),
            ("jpeg_quality", 101, "less_than_equal"),
            ("publish_frequency_hz", 0, "greater_than"),
            ("publish_frequency_hz", -1, "greater_than"),
            ("publish_frequency_hz", float("inf"), "finite_number"),
            ("publish_frequency_hz", float("nan"), "finite_number"),
            ("publish_frequency_hz", 5e-324, "must produce a finite period"),
            ("publish_frequency_hz", True, "must be a number"),
            ("publish_frequency_hz", "30", "must be a number"),
            ("congestion_control", "discard", "literal_error"),
            ("reliability", "sometimes", "literal_error"),
        )
        for field, value, message in cases:
            document = copy.deepcopy(VALID_NODE_CONFIG)
            camera = document["cameras"][0]  # type: ignore[index]
            target = (
                camera if field in {"size", "jpeg_quality"} else camera["publisher"]
            )
            target[field] = value
            with (
                self.subTest(field=field, value=value),
                self.assertRaisesRegex(ValueError, message),
            ):
                load_document(document)


if __name__ == "__main__":
    unittest.main()
