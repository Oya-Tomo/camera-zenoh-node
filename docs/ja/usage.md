# 使い方

## 基本的な実行

カメラデバイス `0` の映像を `demo/zcam` に publish します。

```console
$ uv run node.py
```

別のカメラから幅 1280 px、JPEG 品質 80 で配信する例です。

```console
$ uv run node.py \
    --device 1 \
    --key demo/zcam/front \
    --width 1280 \
    --quality 80 \
    --delay 0.03
```

## オプション

| オプション | デフォルト | 説明 |
| --- | --- | --- |
| `-m`, `--mode` | Zenoh 既定値 | session mode（`peer` または `client`） |
| `-e`, `--connect` | Zenoh 既定値 | 接続する endpoint。複数回指定可能 |
| `-l`, `--listen` | Zenoh 既定値 | listen する endpoint。複数回指定可能 |
| `-c`, `--config` | なし | Zenoh JSON5 設定ファイル |
| `--no-multicast-scouting` | false | multicast scouting を無効化 |
| `--cfg KEY:VALUE` | なし | 任意の Zenoh 設定を上書き。複数回指定可能 |
| `--device` | `0` | OpenCV のカメラデバイス番号 |
| `-w`, `--width` | `500` | 配信画像の幅。縦横比は維持される |
| `-q`, `--quality` | `95` | JPEG 品質（0〜100） |
| `-d`, `--delay` | `0.05` | フレーム送信後の待機秒数 |
| `-k`, `--key` | `demo/zcam` | publish 先の Zenoh key expression |

完全なヘルプは CLI から確認できます。

```console
$ uv run node.py --help
```

## 配信データ仕様

各 Zenoh sample が 1 フレームに対応します。

| 項目 | 値 |
| --- | --- |
| payload | JPEG バイナリ |
| encoding | `image/jpeg` |
| congestion control | `DROP` |
| reliability | `BEST_EFFORT` |

映像では古いフレームを後から届けるより遅延を抑えることを優先するため、送信キューが混雑した場合は sample を drop します。

## subscriber でのデコード

Zenoh Python と OpenCV を使う最小の受信例です。

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
    with session.declare_subscriber("demo/zcam", on_frame):
        input("Press Enter to stop\n")
```

publisher と subscriber を別ホストで動かす場合は、両方が同じ Zenoh network に参加できるよう設定してください。接続方法は[セットアップ](setup.md#zenoh-の接続)を参照してください。
