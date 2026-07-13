# camera-zenoh-node

[日本語](README_ja.md)

A Python node that captures camera frames with OpenCV, encodes them as JPEG, and publishes them over Zenoh.

The CLI, defaults, and JPEG payload format follow Eclipse Zenoh's
[`zcam-python`](https://github.com/eclipse-zenoh/zenoh-demos/tree/main/computer-vision/zcam/zcam-python).
Resource management and QoS use the Zenoh Python 1.9 API.

## Quick start

```console
$ uv sync
$ uv run node.py
```

By default, the node publishes camera device `0` on `demo/zcam` at a width of 500 pixels and JPEG quality 95.

## Documentation

- [Setup](docs/en/setup.md)
- [Usage and data contract](docs/en/usage.md)
- [日本語ドキュメント](README_ja.md)
