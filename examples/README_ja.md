# Stream viewer example

ZenohからJPEG frameをsubscribeし、最新frameをOpenCVでdecodeしてpygameで表示するexampleです。受信した最新JPEGだけを保持するため、表示が遅れてもframe queueが際限なく蓄積しません。pygameはSDLを利用するため、Qtのfont設定なしでWindows、macOS、Linuxから同じwindow・event APIを利用できます。

pygameは`example` dependency groupだけに分離しています。以下のcommandはpublisherのみの環境へpygameを追加せず、viewer実行時にこのgroupを指定します。

## 設定ファイルの準備

repository rootで、リポジトリで管理されているexampleを既定の実行時パスへコピーします。

```console
$ cp examples/viewer-config.example.json5 examples/viewer-config.json5
$ cp examples/viewer-zenoh-config.example.json5 examples/viewer-zenoh-config.json5
```

実行時設定はGitの管理対象外です。マシンごとに異なる表示カメラやネットワークendpointを誤ってコミットすることを防ぎます。

| ファイル | 用途 |
| --- | --- |
| `examples/viewer-config.json5` | 表示するカメラ |
| `examples/viewer-zenoh-config.json5` | viewer sessionのZenoh設定 |

### Viewer設定

```json5
{
  zenoh_key_prefix: "camera",
  device_key: "front",
}
```

| キー | 説明 |
| --- | --- |
| `zenoh_key_prefix` | ノードがpublishするconcreteなZenoh key。ノード設定と一致させる |
| `device_key` | 表示するカメラを指す1つのkey segment |

viewerは`{zenoh_key_prefix}/{device_key}`をsubscribeします。上のexampleでは`camera/front`です。どちらのkeyも[ノード設定](../docs/ja/usage.md#ノード設定)と同じ規則に従い、`zenoh_key_prefix`はconcreteなZenoh key、`device_key`は1つのkey segmentでなければなりません。複数カメラのframeが1つのwindowを取り合わないよう、wildcardは拒否します。

未知、欠落、重複、型が不正なfieldは起動時に拒否され、エラーには該当ファイルが示されます。

### Zenohの接続

同梱のexampleはpeerとして動作し、OSが割り当てるportでlistenしながらmulticast scoutingも有効にします。

```json5
{
  mode: "peer",
  listen: {
    endpoints: ["tcp/0.0.0.0:0"],
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

viewerはmulticast scoutingでノードを見つけるため、このexampleはノードと同じマシンでも、同じnetwork内の別のマシンでもそのまま動作します。multicast scoutingがノードへ届かない場合は、実行時ファイルの`connect.endpoints`へノードに到達可能なアドレスを追加します。たとえば`tcp/192.168.1.20:7447`と指定します。ノード側の設定は[Zenohの接続](../docs/ja/setup.md#zenohの接続)を参照してください。

## Viewerの実行

repository rootからpublisherとviewerを別々のterminalで起動してください。

```console
$ uv run node.py
```

```console
$ uv run --group example examples/viewer.py
```

終了するときは`q`、`Esc`を押すか、viewer windowを閉じます。

CLIは設定ファイルを選択するためだけに使います。表示するカメラはviewer設定だけで決まります。

| オプション | デフォルト | 説明 |
| --- | --- | --- |
| `--viewer-config FILE` | `examples/viewer-config.json5` | Viewer JSON5設定ファイル |
| `--zenoh-config FILE` | `examples/viewer-zenoh-config.json5` | Zenoh JSON5設定ファイル |

既定パスはcurrent working directoryに関係なく、`viewer.py`と同じdirectoryから解決されます。

## 2台目のカメラの表示

1つのviewer processが表示するカメラは1台です。設定した2台目のカメラを同時に表示するには、`device_key`がそのカメラを指す別のviewer設定を用意し、それを指定して2つ目のviewer processを起動します。

```json5
{
  zenoh_key_prefix: "camera",
  device_key: "rear",
}
```

```console
$ uv run --group example examples/viewer.py \
    --viewer-config path/to/rear-viewer-config.json5
```

[English](README.md)
