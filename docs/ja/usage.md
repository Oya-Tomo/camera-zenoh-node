# 使い方

## 基本的な実行

既定パスへ2つの設定ファイルを用意してから起動します。

```console
$ uv run node.py
```

終了するには`Ctrl-C`を押します。

## ノード設定

カメラとpublisherの全設定は`config/node-config.json5`に記述します。完全なexampleは[`config/node-config.example.json`](../../config/node-config.example.json)にあります。

```json5
{
  camera: {
    device: 0,
    width: 500,
    jpeg_quality: 95,
  },
  publisher: {
    key_expression: "camera/frame",
    frame_delay_seconds: 0.05,
    congestion_control: "drop",
    reliability: "best_effort",
  },
}
```

### カメラ設定

| キー | 型 | 説明 |
| --- | --- | --- |
| `device` | 0以上の整数 | OpenCVのカメラデバイス番号 |
| `width` | 1以上の整数 | 配信画像の幅。縦横比は維持される |
| `jpeg_quality` | 0〜100の整数 | JPEG encoderの品質 |

### Publisher設定

| キー | 型 | 説明 |
| --- | --- | --- |
| `key_expression` | 空でない文字列 | publish先のZenoh key expression |
| `frame_delay_seconds` | 0以上の有限数 | 各フレームのpublish後に追加する待機秒数 |
| `congestion_control` | `drop`、`block`、`block_first` | 送信キュー混雑時の動作 |
| `reliability` | `best_effort`または`reliable` | Zenoh publisherのreliability |

`frame_delay_seconds`は、capture、resize、JPEG encode、publishにかかる時間とは別に追加される待機時間です。正確なframe rateを保証する設定ではありません。

ライブ映像では、通常は`drop`と`best_effort`の組み合わせが、遅れて届く古いフレームより新しいフレームを優先します。deliveryを重視する場合はcongestion controlとreliabilityを一緒に検討してください。`reliable`を指定しても、送信キュー混雑時に`drop`がsampleを破棄する動作は防げません。

## CLIオプション

CLIは設定ファイルを選択するためだけに使います。

| オプション | デフォルト | 説明 |
| --- | --- | --- |
| `--zenoh-config FILE` | `config/zenoh-config.json5` | Zenoh JSON5設定ファイル |
| `--node-config FILE` | `config/node-config.json5` | カメラpublisher JSON5設定ファイル |

完全なhelpは次のコマンドで確認できます。

```console
$ uv run node.py --help
```

## 配信データ仕様

各Zenoh sampleが1フレームに対応します。

| 項目 | 値 |
| --- | --- |
| payload | JPEGバイナリ |
| encoding | `image/jpeg` |
| key expression | ノード設定の`publisher.key_expression` |
| congestion control | ノード設定の`publisher.congestion_control` |
| reliability | ノード設定の`publisher.reliability` |

## Subscriberでのデコード

Zenoh PythonとOpenCVを使う最小の受信例です。

```python
import cv2
import numpy as np
import zenoh


def on_frame(sample: zenoh.Sample) -> None:
    encoded = np.frombuffer(sample.payload.to_bytes(), dtype=np.uint8)
    frame = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    if frame is not None:
        print(f"received: {frame.shape[1]}x{frame.shape[0]}")


with zenoh.open(zenoh.Config()) as session:
    with session.declare_subscriber("camera/frame", on_frame):
        input("Press Enter to stop\n")
```

subscriberにはpublisherと同じkey expressionを指定します。publisherとsubscriberを別ホストで動かす場合は、両方が同じZenoh networkに参加できるよう設定してください。接続方法は[セットアップ](setup.md#zenohの接続)を参照してください。
