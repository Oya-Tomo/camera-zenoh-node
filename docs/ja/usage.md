# 使い方

## 基本的な実行

既定パスへ2つの設定ファイルを用意してから起動します。

```console
$ uv run node.py
```

終了するには`Ctrl-C`を押します。設定されたすべてのカメラは1つのprocessで動作し、`config/zenoh-config.json5`から読み込んだ1つのZenoh sessionを共有します。

## ノード設定

`config/node-config.json5`には、ノードのkey階層とカメラごとの設定を記述します。strict JSONの完全なexampleは[`config/node-config.example.json`](../../config/node-config.example.json)にあり、実行時ファイルはJSON5として読み込まれます。

```json5
{
  base_key: "camera",
  cameras: [
    {
      device_key: "front",
      source: {
        path: "/dev/v4l/by-id/usb-Example_Front_Camera-video-index0",
      },
      size: [1280, 720],
      jpeg_quality: 90,
      publisher: {
        publish_frequency_hz: 30.0,
        congestion_control: "drop",
        reliability: "best_effort",
      },
    },
    {
      device_key: "rear",
      source: {index: 1},
      size: [640, 480],
      jpeg_quality: 85,
      publisher: {
        publish_frequency_hz: 20.0,
        congestion_control: "drop",
        reliability: "best_effort",
      },
    },
  ],
}
```

未知、欠落、重複、型が不正なfieldは起動時に拒否されます。`cameras`には1件以上が必要です。`device_key`とsource selectorはノード内で一意でなければなりません。

### ノード設定

| キー | 型 | 説明 |
| --- | --- | --- |
| `base_key` | concreteなZenoh key | このノードがpublishする全カメラの共通prefix。複数segmentを指定可能 |
| `cameras` | 空でないarray | カメラごとの設定 |

publisher keyは常に`{base_key}/{device_key}`から生成します。上のexampleでは`camera/front`と`camera/rear`へpublishします。任意のfull keyやカメラごとのZenoh session・endpointは意図的に対応せず、全streamで1つのノードidentityと接続設定を維持します。

### カメラ設定

| キー | 型 | 説明 |
| --- | --- | --- |
| `device_key` | 一意なconcrete key segment | Zenoh key、worker名、ログ、エラーに使うカメラidentity。`/`とwildcardは指定不可 |
| `source.index` | 0以上の整数 | OpenCVのカメラindex |
| `source.path` | 空でない文字列 | OpenCVのカメラdevice path |
| `size` | 正の整数2個 | 正確な出力`[width, height]` pixel数 |
| `jpeg_quality` | 0〜100の整数 | JPEG encoderの品質 |
| `publisher` | object | カメラごとの出力frequencyとZenoh QoS |

`source.index`と`source.path`のどちらか一方だけを指定します。Linuxの`/dev/videoX`番号はdriverのprobe順で変わる可能性があります。利用できる場合は永続的な`/dev/v4l/by-id/...`を推奨します。`/dev/v4l/by-path/...`は物理的な接続位置を識別するpathとして利用できます。単純な構成やLinux以外では整数indexも利用できます。

frameは設定した`size`へ正確にresizeします。取得frameとaspect ratioが異なる場合は画像が変形するため、変形を避けたい場合は意図するaspect ratioの寸法を指定してください。

### Publisher設定

| キー | 型 | 説明 |
| --- | --- | --- |
| `publish_frequency_hz` | 0より大きい有限数 | publishするframeの目標frequency（Hz） |
| `congestion_control` | `drop`、`block`、`block_first` | Zenoh送信queue混雑時の動作 |
| `reliability` | `best_effort`または`reliable` | Zenoh publisherのreliability |

各camera workerはmonotonic clockの絶対deadlineを基準にscheduleします。OpenCVの`grab()`を継続的に呼んで入力frameをdrainし、publish対象となる最新frameだけを`retrieve()`します。capture、resize、JPEG encode、publishにかかる時間が各周期へ累積して遅れていくことはありません。処理が遅れて出力slotに間に合わない場合、そのslotをskipして絶対scheduleへ復帰し、追いつくためのburst publishは行いません。

missしたslot数は`device_key`ごとに独立して数えます。1回目と、累積数が10、20、30回…を超えた時にwarning logを出します。1回のscheduling iterationで複数thresholdを超えてもsummaryは1件だけです。カメラの取得速度が`publish_frequency_hz`より速いため通常のrate downsamplingでpublishされないframeはdeadline missではなく、warningを出しません。

カメラが報告するFPSはcapability値であり、複数台同時capture時の保証値ではありません。USB bandwidth、capture format、resize、JPEG encode、ほかのカメラの影響で実効throughputは低下します。同時captureで実測したrateより低い値から設定してください。missed-slot warningが増え続ける場合は、用途に合わせて`publish_frequency_hz`、出力`size`、または`jpeg_quality`を下げます。

ライブ映像では、通常は`drop`と`best_effort`の組み合わせが、遅れて届く古いframeより新しいframeを優先します。deliveryを重視する場合はcongestion controlとreliabilityを一緒に検討してください。`reliable`を指定しても、送信queue混雑時に`drop`がsampleを破棄する動作は防げません。

## 障害時の動作

起動はノード単位でatomicです。workerを開始する前に、設定されたすべてのcameraとpublisherをopenします。1つでも初期化できない場合は、それまでにopenした全resourceをreleaseして非0 statusで終了します。

実行中にいずれかのカメラでcapture、encode、publishの致命的なエラーが発生した場合は、全workerを停止し、全cameraとpublisherをreleaseし、共有sessionをcloseして非0 statusで終了します。shutdownでは最初に短いcooperative stop時間を設けます。その後も残るnative処理を解除するためcameraとpublisher resourceをcloseし、それ以降のdaemon worker join時間を制限します。resourceのclose処理自体はOpenCVとZenoh backendの動作に従うため、このprocessからhard timeoutを設定することはできません。カメラの自動再接続やhot plug復旧には対応しません。自動restartが必要な場合はprocess supervisorを利用してください。

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

各Zenoh sampleが1frameに対応します。

| 項目 | 値 |
| --- | --- |
| payload | カメラの設定`size`へresizeしたJPEGバイナリ |
| encoding | `image/jpeg` |
| key expression | `{base_key}/{device_key}` |
| congestion control | カメラの`publisher.congestion_control` |
| reliability | カメラの`publisher.reliability` |

## Subscriberでのデコード

実行可能なviewerは、JPEGのdecodeにOpenCV、cross-platform表示にpygameを使います。1台のconcreteなcamera keyをsubscribeし、受信した最新JPEGだけを保持するため、表示が遅れても古いframeのqueueが際限なく蓄積しません。

```console
$ uv run --group example examples/viewer.py camera/front
```

defaultのZenoh設定は`tcp/127.0.0.1:7447`へ接続します。終了するときは`q`、`Esc`を押すかwindowを閉じます。別endpointや2台目の表示は[viewer example](../../examples/README_ja.md)を参照してください。

同等の最小受信callbackは次のとおりです。

```python
import cv2
import numpy as np
import zenoh


def on_frame(sample: zenoh.Sample) -> None:
    encoded = np.frombuffer(sample.payload.to_bytes(), dtype=np.uint8)
    frame = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    if frame is not None:
        print(f"{sample.key_expr}: {frame.shape[1]}x{frame.shape[0]}")


with zenoh.open(zenoh.Config()) as session:
    with session.declare_subscriber("camera/*", on_frame):
        input("Press Enter to stop\n")
```

`camera/front`のような個別key、または`camera/*`のようなmatching expressionを指定します。publisherとsubscriberを別hostで動かす場合は、両方が同じZenoh networkに参加できるよう設定してください。接続方法は[セットアップ](setup.md#zenohの接続)を参照してください。

## 複数カメラの手動smoke test

物理カメラを2台利用できる場合は、次を確認します。

1. 2台に異なる`source`と`device_key`を設定する。
2. ノードを起動し、各concreteな`{base_key}/{device_key}`に対して`examples/viewer.py`を1processずつ起動する。
3. 両方のwindowで設定寸法の有効なframeを受信でき、選択したfrequencyでmissed-slot warningが継続しないことを確認する。
4. 一方のカメラを切断するなどして障害を起こし、もう一方もreleaseしてノード全体が非0 statusで終了することを確認する。

このhardware依存testは自動test suiteには含まれません。
