# camera-zenoh-node

[English](README.md)

OpenCVで取得したカメラ映像をJPEGに変換し、Zenohへpublishする設定ファイル駆動のPythonノードです。

## クイックスタート

```console
$ uv sync
$ cp config/zenoh-config.example.json5 config/zenoh-config.json5
$ cp config/node-config.example.json config/node-config.json5
$ uv run node.py
```

Zenohの設定は`config/zenoh-config.json5`、カメラpublisherの設定は`config/node-config.json5`から読み込みます。既定パスはcurrent working directoryからの相対パスなので、リポジトリrootで実行してください。個別の実行時設定をCLIから上書きすることはできません。

## ドキュメント

- [セットアップ](docs/ja/setup.md)
- [使い方と配信データ仕様](docs/ja/usage.md)
- [English documentation](README.md)
