# camera-zenoh-node

OpenCV で取得したカメラ映像を JPEG に変換し、Zenoh へ publish する Python ノードです。

CLI と既定値は Eclipse Zenoh の
[`zcam-python`](https://github.com/eclipse-zenoh/zenoh-demos/tree/main/computer-vision/zcam/zcam-python)
の CLI、既定値、JPEG payload を基準にしています。リソース管理と QoS は Zenoh Python 1.9 の API に合わせています。

## Quick start

```console
$ uv sync
$ uv run node.py
```

デフォルトではカメラデバイス `0` の映像を幅 500 px、JPEG 品質 95 で `demo/zcam` に publish します。

## Documentation

- [セットアップ](docs/setup.md)
- [使い方と配信データ仕様](docs/usage.md)
