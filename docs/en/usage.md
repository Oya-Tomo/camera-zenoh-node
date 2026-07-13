# Usage

## Basic usage

After preparing the two default configuration files, start the node with:

```console
$ uv run node.py
```

Stop it with `Ctrl-C`.

## Node configuration

`config/node-config.json5` contains all camera and publisher settings. A complete example is available at [`config/node-config.example.json`](../../config/node-config.example.json).

```json5
{
  camera: {
    device: 0,
    width: 500,
    jpeg_quality: 95,
  },
  publisher: {
    key_expression: "camera/frame",
    frame_delay_seconds: 0.05,
    congestion_control: "drop",
    reliability: "best_effort",
  },
}
```

### Camera settings

| Key | Type | Description |
| --- | --- | --- |
| `device` | integer, 0 or greater | OpenCV camera device index |
| `width` | integer, 1 or greater | Published image width; aspect ratio is preserved |
| `jpeg_quality` | integer, 0–100 | JPEG encoder quality |

### Publisher settings

| Key | Type | Description |
| --- | --- | --- |
| `key_expression` | non-empty string | Destination Zenoh key expression |
| `frame_delay_seconds` | finite number, 0 or greater | Additional delay after each published frame |
| `congestion_control` | `drop`, `block`, or `block_first` | Behavior when the transmission queue is congested |
| `reliability` | `best_effort` or `reliable` | Zenoh publisher reliability |

`frame_delay_seconds` is an additional delay after capture, resize, JPEG encoding, and publication. It does not guarantee an exact frame rate.

For live video, `drop` with `best_effort` normally favors fresh frames over delayed delivery. For delivery-oriented behavior, evaluate congestion control and reliability together: `reliable` does not prevent `drop` from discarding samples when the transmission queue is congested.

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
| Payload | JPEG binary data |
| Encoding | `image/jpeg` |
| Key expression | `publisher.key_expression` from the node configuration |
| Congestion control | `publisher.congestion_control` from the node configuration |
| Reliability | `publisher.reliability` from the node configuration |

## Decoding in a subscriber

Minimal receiving example using Zenoh Python and OpenCV:

```python
import cv2
import numpy as np
import zenoh


def on_frame(sample: zenoh.Sample) -> None:
    encoded = np.frombuffer(sample.payload.to_bytes(), dtype=np.uint8)
    frame = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    if frame is not None:
        print(f"received: {frame.shape[1]}x{frame.shape[0]}")


with zenoh.open(zenoh.Config()) as session:
    with session.declare_subscriber("camera/frame", on_frame):
        input("Press Enter to stop\n")
```

Use the same key expression as the publisher. When the publisher and subscriber run on different hosts, configure both to join the same Zenoh network. See [Setup](setup.md#connecting-to-zenoh).
