# Stream viewer example

This example subscribes to JPEG frames over Zenoh, decodes the newest frame with OpenCV, and displays it with pygame. Keeping only the latest received JPEG prevents a slow display from building an unbounded frame queue. Pygame uses SDL and provides the same window and event API on Windows, macOS, and Linux without Qt font configuration.

Pygame is isolated in the `example` dependency group. The commands below include that group without adding pygame to a publisher-only installation.

The checked-in `viewer-zenoh-config.json5` connects to `tcp/127.0.0.1:7447`. Run the publisher and viewer in separate terminals from the repository root:

```console
$ uv run node.py
```

```console
$ uv run --group example examples/viewer.py camera/front
```

Press `q`, `Esc`, or close the viewer window to stop it.

Use one viewer process per camera key. For example, a second configured camera can be displayed with:

```console
$ uv run --group example examples/viewer.py camera/rear
```

The key must be concrete; wildcard subscriptions are rejected because frames from multiple cameras would otherwise compete for one window. To use another endpoint, provide a Zenoh configuration file:

```console
$ uv run --group example examples/viewer.py camera/front \
    --zenoh-config path/to/viewer-zenoh-config.json5
```

[日本語](README_ja.md)
