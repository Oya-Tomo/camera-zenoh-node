# camera-zenoh-node

[日本語](README_ja.md)

A configuration-driven Python node that captures one or more cameras with OpenCV, encodes their frames as JPEG, and publishes them over one shared Zenoh session.

## Quick start

```console
$ uv sync
$ cp config/zenoh-config.example.json5 config/zenoh-config.json5
$ cp config/node-config.example.json config/node-config.json5
```

Edit `config/node-config.json5` for the cameras on this host, then run:

```console
$ uv run node.py
```

The example contains placeholder sources for two cameras and must be adjusted before the first run. See [camera setup](docs/en/setup.md#checking-the-camera). The node reads Zenoh settings from `config/zenoh-config.json5` and camera publisher settings from `config/node-config.json5`. These defaults are relative to the current working directory, so run the command from the repository root. Runtime settings cannot be overridden individually from the CLI.

## Documentation

- [Setup](docs/en/setup.md)
- [Usage and data contract](docs/en/usage.md)
- [Stream viewer example](examples/README.md)
- [日本語ドキュメント](README_ja.md)
