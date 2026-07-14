import argparse
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import ANY, MagicMock, call, patch

import cv2
import numpy as np
import pygame

from examples import viewer


class LatestFrameTests(unittest.TestCase):
    def test_snapshot_returns_only_the_latest_frame(self) -> None:
        latest_frame = viewer.LatestFrame()

        self.assertEqual(latest_frame.snapshot(), (None, 0))
        latest_frame.update(b"first")
        latest_frame.update(b"second")

        self.assertEqual(latest_frame.snapshot(), (b"second", 2))


class ViewerArgumentTests(unittest.TestCase):
    def test_concrete_camera_key_is_accepted(self) -> None:
        self.assertEqual(viewer.parse_concrete_key("camera/front"), "camera/front")

    def test_wildcard_and_invalid_keys_are_rejected(self) -> None:
        for key in ("", " ", "camera/*", "camera//front"):
            with self.subTest(key=key), self.assertRaises(argparse.ArgumentTypeError):
                viewer.parse_concrete_key(key)


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


class ViewerConfigTests(unittest.TestCase):
    def test_example_zenoh_config_is_valid(self) -> None:
        config = viewer.zenoh.Config.from_file(viewer.DEFAULT_ZENOH_CONFIG_PATH)

        self.assertEqual(json.loads(config.get_json("mode")), "client")
        self.assertEqual(
            json.loads(config.get_json("connect/endpoints")),
            ["tcp/127.0.0.1:7447"],
        )


if __name__ == "__main__":
    unittest.main()
