# Usage

## Basic usage

After preparing the two default configuration files, start the node with:

```console
$ uv run node.py
```

Stop it with `Ctrl-C`. All configured cameras run in one process and share the single Zenoh session loaded from `config/zenoh-config.json5`.

## Node configuration

`config/node-config.json5` contains the node key hierarchy and per-camera settings. A complete strict-JSON example is available at [`config/node-config.example.json`](../../config/node-config.example.json); the runtime file is parsed as JSON5.

```json5
{
  base_key: "camera",
  cameras: [
    {
      device_key: "front",
      source: {
        path: "/dev/v4l/by-id/usb-Example_Front_Camera-video-index0",
      },
      size: [1280, 720],
      jpeg_quality: 90,
      publisher: {
        publish_frequency_hz: 30.0,
        congestion_control: "drop",
        reliability: "best_effort",
      },
    },
    {
      device_key: "rear",
      source: {index: 1},
      size: [640, 480],
      jpeg_quality: 85,
      publisher: {
        publish_frequency_hz: 20.0,
        congestion_control: "drop",
        reliability: "best_effort",
      },
    },
  ],
}
```

Unknown, missing, duplicate, and incorrectly typed fields are rejected at startup. `cameras` must contain at least one entry. Every `device_key` and source selector must be unique within the node.

### Node settings

| Key | Type | Description |
| --- | --- | --- |
| `base_key` | concrete Zenoh key | Common prefix for every camera published by this node; multiple segments are allowed |
| `cameras` | non-empty array | Per-camera configurations |

Each publisher key is derived as `{base_key}/{device_key}`. In the example, frames are published on `camera/front` and `camera/rear`. Arbitrary full keys and per-camera Zenoh sessions or endpoints are intentionally unsupported, so all streams retain one node identity and one connection configuration.

### Camera settings

| Key | Type | Description |
| --- | --- | --- |
| `device_key` | unique concrete key segment | Camera identity used in the Zenoh key, worker name, logs, and errors; `/` and wildcards are not allowed |
| `source.index` | integer, 0 or greater | OpenCV camera index |
| `source.path` | non-empty string | OpenCV camera device path |
| `size` | two positive integers | Exact output `[width, height]` in pixels |
| `jpeg_quality` | integer, 0–100 | JPEG encoder quality |
| `publisher` | object | Per-camera output frequency and Zenoh QoS |

Specify exactly one of `source.index` and `source.path`. On Linux, `/dev/videoX` numbering may change with driver probe order. Prefer a persistent `/dev/v4l/by-id/...` path when available; `/dev/v4l/by-path/...` identifies a physical connection location and is also supported. Integer indexes remain useful for simple or non-Linux setups.

Frames are resized to the exact configured `size`. If its aspect ratio differs from the captured frame, the image is stretched; choose dimensions with the intended aspect ratio when distortion is undesirable.

### Publisher settings

| Key | Type | Description |
| --- | --- | --- |
| `publish_frequency_hz` | finite number greater than 0 | Target published-frame frequency in hertz |
| `congestion_control` | `drop`, `block`, or `block_first` | Behavior when the Zenoh transmission queue is congested |
| `reliability` | `best_effort` or `reliable` | Zenoh publisher reliability |

Each camera worker schedules against absolute deadlines from a monotonic clock. It continuously calls OpenCV `grab()` to drain incoming frames and calls `retrieve()` only for the newest frame due for publication. Capture, resize, JPEG encoding, and publication time therefore do not accumulate as a delay on every cycle. If processing finishes too late for one or more output slots, those slots are skipped and the worker resumes on the absolute schedule; it does not emit a catch-up burst.

The missed-slot count is independent for each `device_key`. A warning is logged on the first miss and then when the cumulative count crosses 10, 20, 30, and so on. One scheduling iteration emits at most one summary even if it crosses several thresholds. Frames normally omitted because the camera captures faster than `publish_frequency_hz` are rate downsampling, not deadline misses, and do not produce warnings.

The FPS reported by a camera is a capability value, not a guarantee for simultaneous capture. USB bandwidth, capture format, resizing, JPEG encoding, and other cameras can lower effective throughput. Start below the measured simultaneous-capture rate. If missed-slot warnings continue to increase, lower `publish_frequency_hz`, output `size`, or `jpeg_quality` as appropriate.

For live video, `drop` with `best_effort` normally favors fresh frames over delayed delivery. For delivery-oriented behavior, evaluate congestion control and reliability together: `reliable` does not prevent `drop` from discarding samples when the transmission queue is congested.

## Failure behavior

Startup is atomic at the node level: every configured camera and publisher is opened before any worker starts. If any resource cannot be initialized, all resources opened so far are released and the process exits with a non-zero status.

At runtime, a fatal capture, encode, or publish error in any camera stops every worker, releases all cameras and publishers, closes the shared session, and exits non-zero. Shutdown first gives workers a short cooperative stop period. It then closes camera and publisher resources to interrupt remaining native operations and bounds the subsequent daemon-worker join. The resource-close calls themselves follow OpenCV and Zenoh backend behavior and cannot be given a hard timeout by this process. Automatic camera reconnection and hot-plug recovery are not provided; use a process supervisor when automatic restart is required.

## CLI options

The CLI selects configuration files only:

| Option | Default | Description |
| --- | --- | --- |
| `--zenoh-config FILE` | `config/zenoh-config.json5` | Zenoh JSON5 configuration file |
| `--node-config FILE` | `config/node-config.json5` | Camera publisher JSON5 configuration file |

Display the complete help with:

```console
$ uv run node.py --help
```

## Published data contract

Each Zenoh sample contains one frame.

| Field | Value |
| --- | --- |
| Payload | JPEG binary data resized to the camera's configured `size` |
| Encoding | `image/jpeg` |
| Key expression | `{base_key}/{device_key}` |
| Congestion control | The camera's `publisher.congestion_control` |
| Reliability | The camera's `publisher.reliability` |

## Decoding in a subscriber

The ready-to-run viewer uses OpenCV for JPEG decoding and pygame for cross-platform display. It subscribes to one concrete camera key and keeps only the newest received JPEG, so a slow display does not build an unbounded queue of stale frames:

```console
$ uv run --group example examples/viewer.py camera/front
```

Its default Zenoh configuration connects to `tcp/127.0.0.1:7447`. Press `q`, `Esc`, or close the window to stop it. See the [viewer example](../../examples/README.md) for a different endpoint or a second camera.

The equivalent minimal receiving callback is:

```python
import cv2
import numpy as np
import zenoh


def on_frame(sample: zenoh.Sample) -> None:
    encoded = np.frombuffer(sample.payload.to_bytes(), dtype=np.uint8)
    frame = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    if frame is not None:
        print(f"{sample.key_expr}: {frame.shape[1]}x{frame.shape[0]}")


with zenoh.open(zenoh.Config()) as session:
    with session.declare_subscriber("camera/*", on_frame):
        input("Press Enter to stop\n")
```

Use a concrete camera key such as `camera/front`, or a matching expression such as `camera/*`. When publisher and subscriber run on different hosts, configure both to join the same Zenoh network. See [Setup](setup.md#connecting-to-zenoh).

## Manual multi-camera smoke test

When two physical cameras are available:

1. Configure distinct `source` and `device_key` values for both cameras.
2. Start the node and one `examples/viewer.py` process for each concrete `{base_key}/{device_key}`.
3. Confirm that both windows receive valid frames at their configured dimensions and that sustained missed-slot warnings do not appear at the chosen frequencies.
4. Disconnect or otherwise fail one camera and confirm that the whole node exits non-zero after releasing the other camera.

This hardware-dependent test is not part of the automated test suite.
