# Setup

## Requirements

- Python 3.13
- [uv](https://docs.astral.sh/uv/)
- A camera recognized by OpenCV

Install the project dependencies:

```console
$ uv sync
```

## Prepare the configuration files

Copy the version-controlled examples to the default runtime paths:

```console
$ cp config/zenoh-config.example.json5 config/zenoh-config.json5
$ cp config/node-config.example.json config/node-config.json5
```

The runtime files are ignored by Git so that machine-specific camera devices and network endpoints are not committed accidentally.

The node reads both files at startup:

| File | Purpose |
| --- | --- |
| `config/zenoh-config.json5` | Zenoh mode, endpoints, scouting, and transport settings |
| `config/node-config.json5` | Camera sources, output settings, key hierarchy, frequency, and publisher QoS |

Both files accept JSON5. The node configuration rejects missing, unknown, and duplicate keys to make configuration mistakes fail at startup.

The node example deliberately uses strict JSON and a `.json` suffix. JSON is a subset of JSON5, so copying it to `node-config.json5` preserves the same content while allowing JSON5 features in the runtime file.

Default paths are resolved from the current working directory. Run the node from the repository root when using them, or pass both explicit paths as described below.

## Checking the camera

On Linux, inspect the available Video4Linux devices and persistent udev links:

```console
$ ls /dev/video*
$ ls -l /dev/v4l/by-id/ /dev/v4l/by-path/
```

For a stable identity, configure a camera with a `/dev/v4l/by-id/...` link:

```json5
source: {path: "/dev/v4l/by-id/usb-Example_Camera-video-index0"}
```

`/dev/v4l/by-path/...` can be used when the physical USB connection is the desired identity. Alternatively, use `source: {index: 0}` to pass an OpenCV integer index. `/dev/videoX` numbers and OpenCV indexes can change when device discovery order changes, so they are less suitable for multi-camera deployments.

Specify exactly one of `source.path` and `source.index` for every camera. If the node reports that it could not open a camera, check that:

- The configured device or symlink exists and resolves to the intended camera.
- The current user has read/write access to the device. On Linux, also check membership in the `video` group.
- Another process is not already using the camera.

When running inside a container, pass every configured camera device through to the container. The node opens all configured cameras before starting publication, so one unavailable camera causes startup to fail and all resources to be released.

## Connecting to Zenoh

Zenoh Python loads the connection configuration directly through `zenoh.Config.from_file()`. Refer to the official [Zenoh deployment guide](https://zenoh.io/docs/getting-started/deployment/) and [`DEFAULT_CONFIG.json5`](https://github.com/eclipse-zenoh/zenoh/blob/main/DEFAULT_CONFIG.json5) for the complete schema.

The supplied example runs as a peer, listens on TCP port 7447, and enables multicast scouting:

```json5
{
  mode: "peer",
  listen: {
    endpoints: ["tcp/0.0.0.0:7447"],
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

`0.0.0.0` is a local wildcard bind address. It makes the process accept connections on every local IPv4 interface; it is not the address that a remote peer puts in `connect.endpoints`. A remote peer connects to an address reachable on this host, for example `tcp/192.168.1.20:7447`.

Port 7447 must be unused on the publisher host. Listening on `0.0.0.0` also exposes the Zenoh endpoint on every reachable IPv4 interface, so restrict the bind address or firewall rules when the network is not trusted.

To connect this node to an existing router instead, use client mode and the router's reachable address:

```json5
{
  mode: "client",
  connect: {
    endpoints: ["tcp/192.168.1.10:7447"],
  },
  scouting: {
    multicast: {
      enabled: false,
    },
  },
}
```

To connect directly to a peer that is already listening:

```json5
{
  mode: "peer",
  connect: {
    endpoints: ["tcp/192.168.1.20:7447"],
  },
  scouting: {
    multicast: {
      enabled: false,
    },
  },
}
```

Only one side needs to initiate a given direct TCP connection: one side listens on a stable port and the other side connects to that host and port. If multicast scouting is available, explicit endpoints may be unnecessary.

## Using configuration files in another location

The only CLI options select the two configuration files:

```console
$ uv run node.py \
    --zenoh-config /etc/camera-node/zenoh.json5 \
    --node-config /etc/camera-node/node.json5
```

Camera, publisher, and Zenoh values cannot be overridden individually from the CLI.

## Development checks

Run the formatter, linter, type checker, and unit tests after making changes:

```console
$ uv run ruff format --check .
$ uv run ruff check .
$ uv run pyright
$ uv run python -m unittest discover -v
```
