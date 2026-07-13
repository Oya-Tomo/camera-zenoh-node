# Setup

## Requirements

- Python 3.13
- [uv](https://docs.astral.sh/uv/)
- A camera recognized by OpenCV

Install the project dependencies:

```console
$ uv sync
```

If the following command displays the CLI help, the Python environment is ready:

```console
$ uv run node.py --help
```

## Checking the camera

On Linux, list the available Video4Linux devices:

```console
$ ls /dev/video*
```

If the node reports `could not open camera device`, check that:

- The `/dev/video*` device selected by `--device` exists.
- The current user has read/write access to the device. On Linux, also check membership in the `video` group.
- Another process is not already using the camera.

When running inside a container, pass the camera device through to the container.

## Connecting to Zenoh

Without explicit connection settings, the node uses the Zenoh defaults. Peers on the same network can discover each other through multicast scouting.

To connect to a Zenoh router as a client:

```console
$ uv run node.py --mode client --connect tcp/192.168.1.10:7447
```

Repeat `--connect` to provide multiple candidate endpoints:

```console
$ uv run node.py \
    --mode client \
    --connect tcp/router-a:7447 \
    --connect tcp/router-b:7447
```

You can also use a JSON5 configuration file:

```console
$ uv run node.py --config zenoh.json5
```

Configuration precedence, from lowest to highest, is: Zenoh defaults, the file passed to `--config`, dedicated options such as `--mode`, `--connect`, and `--listen`, and finally `--cfg`.

Disable multicast scouting with `--no-multicast-scouting`. Other Zenoh settings can be overridden with `--cfg KEY:VALUE`, following the current Zenoh Python examples:

```console
$ uv run node.py \
    --no-multicast-scouting \
    --cfg 'transport/unicast/max_links:2'
```

`VALUE` must be valid JSON5 for the selected Zenoh configuration field.

## Development checks

Run the formatter, linter, type checker, and unit tests after making changes:

```console
$ uv run ruff format --check .
$ uv run ruff check .
$ uv run pyright
$ uv run python -m unittest discover -v
```
