# Stream viewer example

ZenohからJPEG frameをsubscribeし、最新frameをOpenCVでdecodeしてpygameで表示するexampleです。受信した最新JPEGだけを保持するため、表示が遅れてもframe queueが際限なく蓄積しません。pygameはSDLを利用するため、Qtのfont設定なしでWindows、macOS、Linuxから同じwindow・event APIを利用できます。

pygameは`example` dependency groupだけに分離しています。以下のcommandはpublisherのみの環境へpygameを追加せず、viewer実行時にこのgroupを指定します。

exampleに含めた`viewer-zenoh-config.json5`は`tcp/127.0.0.1:7447`へ接続します。repository rootからpublisherとviewerを別々のterminalで起動してください。

```console
$ uv run node.py
```

```console
$ uv run --group example examples/viewer.py camera/front
```

終了するときは`q`、`Esc`を押すか、viewer windowを閉じます。

viewer processはカメラkeyごとに1つ起動します。たとえば、設定した2台目のカメラは次のように表示できます。

```console
$ uv run --group example examples/viewer.py camera/rear
```

複数カメラのframeが1つのwindowを取り合わないよう、wildcard subscriptionは拒否し、concreteなkeyだけを受け付けます。別のendpointを使う場合は、対応するZenoh configを渡します。

```console
$ uv run --group example examples/viewer.py camera/front \
    --zenoh-config path/to/viewer-zenoh-config.json5
```

[English](README.md)
