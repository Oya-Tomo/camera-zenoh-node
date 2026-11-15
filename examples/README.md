# Stream viewer example

This example subscribes to JPEG frames over Zenoh, decodes the newest frame with OpenCV, and displays it with pygame. Keeping only the latest received JPEG prevents a slow display from building an unbounded frame queue. Pygame uses SDL and provides the same window and event API on Windows, macOS, and Linux without Qt font configuration.

Pygame is isolated in the `example` dependency group. The commands below include that group without adding pygame to a publisher-only installation.

## Prepare the configuration files

Copy the version-controlled examples to the default runtime paths from the repository root:

```console
$ cp examples/viewer-config.example.json5 examples/viewer-config.json5
$ cp examples/viewer-zenoh-config.example.json5 examples/viewer-zenoh-config.json5
```

The runtime files are ignored by Git so that the camera selection and network endpoints of one machine are not committed accidentally.

| File | Purpose |
| --- | --- |
| `examples/viewer-config.json5` | Camera to display |
| `examples/viewer-zenoh-config.json5` | Zenoh configuration of the viewer session |

### Viewer configuration

```json5
{
  zenoh_key_prefix: "camera",
  device_key: "front",
}
```

| Key | Description |
| --- | --- |
| `zenoh_key_prefix` | Concrete Zenoh key under which the node publishes; must match the node configuration |
| `device_key` | Single key segment naming the camera to display |

The viewer subscribes to `{zenoh_key_prefix}/{device_key}`, which is `camera/front` in the example. Both keys follow the same rules as in the [node configuration](../docs/en/usage.md#node-configuration): `zenoh_key_prefix` is a concrete Zenoh key and `device_key` is a single key segment. Wildcards are rejected because frames from multiple cameras would otherwise compete for one window.

Unknown, missing, duplicate, and incorrectly typed fields are rejected at startup with an error that names the file.

### Zenoh connection

The supplied example runs as a peer, listens on a port assigned by the operating system, and enables multicast scouting:

```json5
{
  mode: "peer",
  listen: {
    endpoints: ["tcp/0.0.0.0:0"],
  },
  connect: {
    endpoints: [],
  },
  scouting: {
    multicast: {
      enabled: true,
    },
  },
}
```

The viewer finds the node by multicast scouting, so the example runs unchanged on the node's machine and on another machine in the same network. When multicast scouting cannot reach the node, add an address at which the node is reachable to `connect.endpoints` in the runtime file, for example `tcp/192.168.1.20:7447`. See [Connecting to Zenoh](../docs/en/setup.md#connecting-to-zenoh) for the node side.

## Run the viewer

Run the publisher and viewer in separate terminals from the repository root:

```console
$ uv run node.py
```

```console
$ uv run --group example examples/viewer.py
```

Press `q`, `Esc`, or close the viewer window to stop it.

The CLI selects configuration files only. The camera is selected by the viewer configuration alone:

| Option | Default | Description |
| --- | --- | --- |
| `--viewer-config FILE` | `examples/viewer-config.json5` | Viewer JSON5 configuration file |
| `--zenoh-config FILE` | `examples/viewer-zenoh-config.json5` | Zenoh JSON5 configuration file |

The default paths are resolved next to `viewer.py`, independently of the current working directory.

## Display a second camera

One viewer process displays one camera. To display a second configured camera at the same time, prepare another viewer configuration whose `device_key` names that camera and start a second viewer process with it:

```json5
{
  zenoh_key_prefix: "camera",
  device_key: "rear",
}
```

```console
$ uv run --group example examples/viewer.py \
    --viewer-config path/to/rear-viewer-config.json5
```

[日本語](README_ja.md)
