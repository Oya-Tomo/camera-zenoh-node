# camera-zenoh-node

[English](README.md)

OpenCVで取得したカメラ映像をJPEGに変換し、ZenohへpublishするPythonノードです。

CLI、既定値、JPEG payloadはEclipse Zenohの
[`zcam-python`](https://github.com/eclipse-zenoh/zenoh-demos/tree/main/computer-vision/zcam/zcam-python)
を基準にしています。リソース管理とQoSはZenoh Python 1.9のAPIに合わせています。

## クイックスタート

```console
$ uv sync
$ uv run node.py
```

デフォルトではカメラデバイス`0`の映像を幅500 px、JPEG品質95で`demo/zcam`にpublishします。

## ドキュメント

- [セットアップ](docs/ja/setup.md)
- [使い方と配信データ仕様](docs/ja/usage.md)
- [English documentation](README.md)
