import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import ANY, MagicMock, call, patch

import cv2
import numpy as np
import pygame

from examples import viewer

EXAMPLE_VIEWER_CONFIG_PATH = Path(viewer.__file__).with_name(
    "viewer-config.example.json5"
)
EXAMPLE_ZENOH_CONFIG_PATH = Path(viewer.__file__).with_name(
    "viewer-zenoh-config.example.json5"
)


class LatestFrameTests(unittest.TestCase):
    def test_snapshot_returns_only_the_latest_frame(self) -> None:
        latest_frame = viewer.LatestFrame()

        self.assertEqual(latest_frame.snapshot(), (None, 0))
        latest_frame.update(b"first")
        latest_frame.update(b"second")

        self.assertEqual(latest_frame.snapshot(), (b"second", 2))


class ViewerArgumentTests(unittest.TestCase):
    def test_config_paths_default_to_runtime_files_next_to_the_viewer(self) -> None:
        args = viewer.build_parser().parse_args([])

        self.assertEqual(
            args.viewer_config, Path(viewer.__file__).with_name("viewer-config.json5")
        )
        self.assertEqual(
            args.zenoh_config,
            Path(viewer.__file__).with_name("viewer-zenoh-config.json5"),
        )

    def test_config_paths_are_selected_by_options(self) -> None:
        args = viewer.build_parser().parse_args(
            ["--viewer-config", "rear.json5", "--zenoh-config", "zenoh.json5"]
        )

        self.assertEqual(args.viewer_config, Path("rear.json5"))
        self.assertEqual(args.zenoh_config, Path("zenoh.json5"))

    def test_positional_key_is_rejected(self) -> None:
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as raised:
            viewer.build_parser().parse_args(["camera/front"])

        self.assertEqual(raised.exception.code, 2)


class DecodeJpegTests(unittest.TestCase):
    def test_valid_jpeg_is_decoded(self) -> None:
        image = np.zeros((2, 3, 3), dtype=np.uint8)
        success, encoded = cv2.imencode(".jpg", image)
        self.assertTrue(success)

        decoded = viewer.decode_jpeg(encoded.tobytes())

        self.assertIsNotNone(decoded)
        assert decoded is not None
        self.assertEqual(decoded.shape, image.shape)

    def test_empty_and_invalid_payloads_are_rejected(self) -> None:
        self.assertIsNone(viewer.decode_jpeg(b""))
        self.assertIsNone(viewer.decode_jpeg(b"not a jpeg"))


class DisplayFramesTests(unittest.TestCase):
    def test_valid_frame_is_displayed_and_q_closes_the_window(self) -> None:
        latest_frame = viewer.LatestFrame()
        latest_frame.update(b"jpeg")
        frame = np.zeros((2, 3, 3), dtype=np.uint8)
        initial_screen = MagicMock(spec=pygame.Surface)
        initial_screen.get_size.return_value = viewer.DEFAULT_WINDOW_SIZE
        frame_screen = MagicMock(spec=pygame.Surface)
        frame_surface = MagicMock(spec=pygame.Surface)
        quit_event = MagicMock(type=pygame.KEYDOWN, key=pygame.K_q)

        with (
            patch("examples.viewer.decode_jpeg", return_value=frame),
            patch.object(pygame.display, "init") as display_init,
            patch.object(pygame.display, "set_caption") as set_caption,
            patch.object(
                pygame.display,
                "set_mode",
                side_effect=[initial_screen, frame_screen],
            ) as set_mode,
            patch.object(
                pygame.image, "frombuffer", return_value=frame_surface
            ) as frombuffer,
            patch.object(pygame.display, "flip") as flip,
            patch.object(pygame.event, "get", return_value=[quit_event]),
            patch.object(pygame.time, "wait") as wait,
            patch.object(pygame.display, "quit") as display_quit,
        ):
            viewer.display_frames(latest_frame)

        display_init.assert_called_once_with()
        set_caption.assert_called_once_with(viewer.WINDOW_NAME)
        self.assertEqual(
            set_mode.call_args_list,
            [call(viewer.DEFAULT_WINDOW_SIZE), call((3, 2))],
        )
        buffer, size, pixel_format = frombuffer.call_args.args
        self.assertIs(buffer.obj, frame)
        self.assertEqual(size, (3, 2))
        self.assertEqual(pixel_format, "BGR")
        frame_screen.blit.assert_called_once_with(frame_surface, (0, 0))
        flip.assert_called_once_with()
        wait.assert_not_called()
        display_quit.assert_called_once_with()

    def test_invalid_frame_warnings_are_throttled_until_window_closes(self) -> None:
        latest_frame = MagicMock(spec=viewer.LatestFrame)
        latest_frame.snapshot.side_effect = [
            (f"invalid-{sequence}".encode(), sequence) for sequence in range(1, 11)
        ]
        stderr = StringIO()
        screen = MagicMock(spec=pygame.Surface)
        close_event = MagicMock(type=pygame.QUIT)

        with (
            redirect_stderr(stderr),
            patch("examples.viewer.decode_jpeg", return_value=None),
            patch.object(pygame.display, "init"),
            patch.object(pygame.display, "set_caption"),
            patch.object(pygame.display, "set_mode", return_value=screen),
            patch.object(pygame.image, "frombuffer") as frombuffer,
            patch.object(pygame.display, "flip") as flip,
            patch.object(pygame.event, "get", side_effect=[[]] * 9 + [[close_event]]),
            patch.object(pygame.time, "wait") as wait,
            patch.object(pygame.display, "quit") as display_quit,
        ):
            viewer.display_frames(latest_frame)

        self.assertEqual(stderr.getvalue().count("[WARNING]"), 2)
        self.assertIn("(1 total)", stderr.getvalue())
        self.assertIn("(10 total)", stderr.getvalue())
        frombuffer.assert_not_called()
        flip.assert_not_called()
        self.assertEqual(wait.call_count, 9)
        display_quit.assert_called_once_with()

    def test_display_errors_still_close_the_backend(self) -> None:
        latest_frame = MagicMock(spec=viewer.LatestFrame)
        latest_frame.snapshot.return_value = (None, 0)

        with (
            patch.object(pygame.display, "init"),
            patch.object(pygame.display, "set_caption"),
            patch.object(
                pygame.display, "set_mode", side_effect=pygame.error("display failed")
            ),
            patch.object(pygame.display, "quit") as display_quit,
            self.assertRaisesRegex(pygame.error, "display failed"),
        ):
            viewer.display_frames(latest_frame)

        display_quit.assert_called_once_with()


class ViewerRuntimeTests(unittest.TestCase):
    def _make_zenoh_contexts(
        self,
    ) -> tuple[MagicMock, MagicMock, MagicMock]:
        session_context = MagicMock()
        session = MagicMock()
        subscriber_context = MagicMock()
        session_context.__enter__.return_value = session
        session.declare_subscriber.return_value = subscriber_context
        return session_context, session, subscriber_context

    def test_run_forwards_samples_and_closes_contexts(self) -> None:
        session_context, session, subscriber_context = self._make_zenoh_contexts()
        sample = MagicMock()
        sample.payload.to_bytes.return_value = b"jpeg"

        def inspect_latest_frame(latest_frame: viewer.LatestFrame) -> None:
            callback = session.declare_subscriber.call_args.args[1]
            callback(sample)
            self.assertEqual(latest_frame.snapshot(), (b"jpeg", 1))

        with (
            redirect_stdout(StringIO()),
            patch("examples.viewer.zenoh.init_log_from_env_or") as init_log,
            patch("examples.viewer.zenoh.open", return_value=session_context),
            patch("examples.viewer.display_frames", side_effect=inspect_latest_frame),
        ):
            viewer.run(MagicMock(), "camera/front")

        init_log.assert_called_once_with("error")
        session.declare_subscriber.assert_called_once_with("camera/front", ANY)
        subscriber_context.__exit__.assert_called_once()
        session_context.__exit__.assert_called_once()

    def test_run_closes_contexts_when_display_fails(self) -> None:
        session_context, _, subscriber_context = self._make_zenoh_contexts()

        with (
            redirect_stdout(StringIO()),
            patch("examples.viewer.zenoh.init_log_from_env_or"),
            patch("examples.viewer.zenoh.open", return_value=session_context),
            patch(
                "examples.viewer.display_frames",
                side_effect=RuntimeError("display failed"),
            ),
            self.assertRaisesRegex(RuntimeError, "display failed"),
        ):
            viewer.run(MagicMock(), "camera/front")

        subscriber_context.__exit__.assert_called_once()
        session_context.__exit__.assert_called_once()


class ViewerMainTests(unittest.TestCase):
    def _run_main(self, viewer_config: Path) -> tuple[int, MagicMock, MagicMock, str]:
        open_session = MagicMock()
        display_frames = MagicMock()
        stderr = StringIO()

        with (
            redirect_stdout(StringIO()),
            redirect_stderr(stderr),
            patch("examples.viewer.zenoh.init_log_from_env_or"),
            patch("examples.viewer.zenoh.open", open_session),
            patch("examples.viewer.display_frames", display_frames),
        ):
            status = viewer.main(
                [
                    "--viewer-config",
                    str(viewer_config),
                    "--zenoh-config",
                    str(EXAMPLE_ZENOH_CONFIG_PATH),
                ]
            )

        return status, open_session, display_frames, stderr.getvalue()

    def test_subscriber_is_declared_on_the_configured_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            rear_config = Path(directory) / "viewer-config.json5"
            rear_config.write_text(
                "{zenoh_key_prefix: 'site/camera', device_key: 'rear'}",
                encoding="utf-8",
            )

            for viewer_config, key in (
                (EXAMPLE_VIEWER_CONFIG_PATH, "camera/front"),
                (rear_config, "site/camera/rear"),
            ):
                with self.subTest(key=key):
                    status, open_session, display_frames, _ = self._run_main(
                        viewer_config
                    )

                    self.assertEqual(status, 0)
                    session = open_session.return_value.__enter__.return_value
                    session.declare_subscriber.assert_called_once_with(key, ANY)
                    display_frames.assert_called_once()

    def test_absent_or_invalid_viewer_config_stops_before_the_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            absent_config = Path(directory) / "absent.json5"
            invalid_config = Path(directory) / "invalid.json5"
            invalid_config.write_text(
                "{zenoh_key_prefix: 'camera', device_key: '*'}", encoding="utf-8"
            )

            for viewer_config in (absent_config, invalid_config):
                with self.subTest(viewer_config=viewer_config.name):
                    status, open_session, display_frames, stderr = self._run_main(
                        viewer_config
                    )

                    self.assertEqual(status, 1)
                    self.assertIn("[ERROR]", stderr)
                    self.assertIn(str(viewer_config), stderr)
                    open_session.assert_not_called()
                    display_frames.assert_not_called()


class ViewerConfigTests(unittest.TestCase):
    def _load(self, text: str) -> viewer.ViewerConfig:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "viewer-config.json5"
            path.write_text(text, encoding="utf-8")
            return viewer.load_viewer_config(path)

    def test_example_viewer_config_is_valid(self) -> None:
        config = viewer.load_viewer_config(EXAMPLE_VIEWER_CONFIG_PATH)

        self.assertEqual(config.zenoh_key_prefix, "camera")
        self.assertEqual(config.device_key, "front")
        self.assertEqual(config.camera_key, "camera/front")

    def test_example_zenoh_config_is_valid(self) -> None:
        config = viewer.zenoh.Config.from_file(EXAMPLE_ZENOH_CONFIG_PATH)

        self.assertEqual(json.loads(config.get_json("mode")), "peer")
        self.assertEqual(
            json.loads(config.get_json("listen/endpoints")), ["tcp/0.0.0.0:0"]
        )
        self.assertEqual(json.loads(config.get_json("connect/endpoints")), [])
        self.assertTrue(json.loads(config.get_json("scouting/multicast/enabled")))

    def test_viewer_config_accepts_a_multi_segment_prefix(self) -> None:
        config = self._load("{zenoh_key_prefix: 'site/camera', device_key: 'rear'}")

        self.assertEqual(config.camera_key, "site/camera/rear")

    def test_viewer_config_rejects_invalid_fields(self) -> None:
        cases = (
            ("{zenoh_key_prefix: 'camera'}", "missing"),
            ("{device_key: 'front'}", "missing"),
            (
                "{zenoh_key_prefix: 'camera', device_key: 'front', quit: 'q'}",
                "extra_forbidden",
            ),
            (
                "{zenoh_key_prefix: 'a', zenoh_key_prefix: 'b', device_key: 'front'}",
                "(?i)duplicate key",
            ),
            ("{zenoh_key_prefix: 1, device_key: 'front'}", "string_type"),
            ("{zenoh_key_prefix: 'camera', device_key: ['front']}", "string_type"),
            ("['camera', 'front']", "model_type"),
            ("{zenoh_key_prefix: 'camera', device_key: 'front'", "invalid JSON5"),
        )
        for text, message in cases:
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, message):
                self._load(text)

    def test_viewer_config_rejects_invalid_keys(self) -> None:
        cases = (
            ("zenoh_key_prefix", " ", "non-empty string"),
            ("zenoh_key_prefix", "camera/*", "concrete Zenoh key"),
            ("zenoh_key_prefix", "camera//node", "valid Zenoh key expression"),
            ("device_key", "", "non-empty string"),
            ("device_key", "*", "concrete Zenoh key"),
            ("device_key", "front/left", "single key segment"),
        )
        for field, value, message in cases:
            document = {"zenoh_key_prefix": "camera", "device_key": "front"}
            document[field] = value
            with (
                self.subTest(field=field, value=value),
                self.assertRaisesRegex(ValueError, message),
            ):
                self._load(json.dumps(document))

    def test_viewer_config_errors_name_the_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "viewer-config.json5"
            for text in ("{zenoh_key_prefix: 'camera'", "{zenoh_key_prefix: 'camera'}"):
                path.write_text(text, encoding="utf-8")
                with self.subTest(text=text), self.assertRaises(ValueError) as raised:
                    viewer.load_viewer_config(path)

                self.assertTrue(str(raised.exception).startswith(f"{path}: "))


if __name__ == "__main__":
    unittest.main()
