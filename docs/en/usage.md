# Usage

## Basic usage

Publish camera device `0` on `demo/zcam`:

```console
$ uv run node.py
```

The following example publishes camera device `1` at a width of 1280 pixels and JPEG quality 80:

```console
$ uv run node.py \
    --device 1 \
    --key demo/zcam/front \
    --width 1280 \
    --quality 80 \
    --delay 0.03
```

## Options

| Option | Default | Description |
| --- | --- | --- |
| `-m`, `--mode` | Zenoh default | Session mode: `peer` or `client` |
| `-e`, `--connect` | Zenoh default | Endpoint to connect to; may be repeated |
| `-l`, `--listen` | Zenoh default | Endpoint to listen on; may be repeated |
| `-c`, `--config` | None | Zenoh JSON5 configuration file |
| `--no-multicast-scouting` | false | Disable multicast scouting |
| `--cfg KEY:VALUE` | None | Override a Zenoh setting; may be repeated |
| `--device` | `0` | OpenCV camera device index |
| `-w`, `--width` | `500` | Published image width; aspect ratio is preserved |
| `-q`, `--quality` | `95` | JPEG quality from 0 to 100 |
| `-d`, `--delay` | `0.05` | Additional delay after each published frame |
| `-k`, `--key` | `demo/zcam` | Destination Zenoh key expression |

Display the complete CLI help with:

```console
$ uv run node.py --help
```

## Published data contract

Each Zenoh sample contains one frame.

| Field | Value |
| --- | --- |
| Payload | JPEG binary data |
| Encoding | `image/jpeg` |
| Congestion control | `DROP` |
| Reliability | `BEST_EFFORT` |

For live video, freshness is preferred over delivering stale frames. Samples may therefore be dropped when the transmission queue is congested.

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
    with session.declare_subscriber("demo/zcam", on_frame):
        input("Press Enter to stop\n")
```

When the publisher and subscriber run on different hosts, ensure that both join the same Zenoh network. See [Setup](setup.md#connecting-to-zenoh).
