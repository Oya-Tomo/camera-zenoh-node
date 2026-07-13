# camera-zenoh-node

[English](README.md)

OpenCVで1台以上のカメラ映像を取得してJPEGに変換し、1つの共有Zenoh sessionからpublishする設定ファイル駆動のPythonノードです。

## クイックスタート

```console
$ uv sync
$ cp config/zenoh-config.example.json5 config/zenoh-config.json5
$ cp config/node-config.example.json config/node-config.json5
```

このホストのカメラに合わせて`config/node-config.json5`を編集してから起動します。

```console
$ uv run node.py
```

exampleには2台分のplaceholder sourceが含まれるため、初回起動前に環境へ合わせて変更してください。[カメラの設定](docs/ja/setup.md#カメラの確認)も参照してください。Zenohの設定は`config/zenoh-config.json5`、カメラpublisherの設定は`config/node-config.json5`から読み込みます。既定パスはcurrent working directoryからの相対パスなので、リポジトリrootで実行してください。個別の実行時設定をCLIから上書きすることはできません。

## ドキュメント

- [セットアップ](docs/ja/setup.md)
- [使い方と配信データ仕様](docs/ja/usage.md)
- [English documentation](README.md)
