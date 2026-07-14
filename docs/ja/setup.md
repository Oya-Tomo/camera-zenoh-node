# セットアップ

## 必要なもの

- Python 3.13
- [uv](https://docs.astral.sh/uv/)
- OpenCVから認識できるカメラ
- stream viewer exampleを使う場合はdesktop display

依存パッケージをインストールします。

```console
$ uv sync
```

## 設定ファイルの準備

リポジトリで管理されているexampleを、既定の実行時パスへコピーします。

```console
$ cp config/zenoh-config.example.json5 config/zenoh-config.json5
$ cp config/node-config.example.json5 config/node-config.json5
```

実行時設定はGitの管理対象外です。マシン固有のカメラデバイスやネットワークendpointを誤ってコミットすることを防ぎます。

ノードは起動時に次の2ファイルを読み込みます。

| ファイル | 用途 |
| --- | --- |
| `config/zenoh-config.json5` | Zenohのmode、endpoint、scouting、transport設定 |
| `config/node-config.json5` | カメラsource、出力、key階層、frequency、publisher QoS設定 |

どちらもJSON5を使用します。ノード設定では、設定ミスを起動時に検出するため、必須キーの欠落、未知のキー、重複キーをエラーにします。

既定パスはcurrent working directoryから解決されます。既定パスを使う場合はリポジトリrootから起動し、別の場所から起動する場合は後述の2つの明示パスを指定してください。

## カメラの確認

Linuxでは、Video4Linuxデバイスと永続的なudev linkを確認します。

```console
$ ls /dev/video*
$ ls -l /dev/v4l/by-id/ /dev/v4l/by-path/
```

安定したidentityが必要な場合は、`/dev/v4l/by-id/...` linkを設定します。

```json5
source: {path: "/dev/v4l/by-id/usb-Example_Camera-video-index0"}
```

物理USB接続位置をidentityにしたい場合は`/dev/v4l/by-path/...`も利用できます。OpenCVの整数indexを渡すには`source: {index: 0}`を使います。`/dev/videoX`番号とOpenCV indexはdeviceの検出順によって変わる可能性があるため、複数カメラ構成では安定したpathの方が適しています。

1台の物理カメラが複数のV4L2 nodeを公開する場合があります。一般には`video-index0` linkがcapture nodeですが、すべての`/dev/videoX`を別カメラと見なさず、実際にopenしてframeを読めることを確認してください。

各カメラでは`source.path`と`source.index`のどちらか一方だけを指定します。カメラをopenできないエラーが表示された場合は、次を確認してください。

- 設定したdeviceまたはsymlinkが存在し、意図したカメラを参照している
- 実行ユーザーにデバイスの読み書き権限がある（Linuxでは`video` groupも確認する）
- 他のプロセスがカメラを占有していない

コンテナ内で実行する場合は、設定したすべてのカメラdeviceをコンテナへ渡す必要があります。ノードはpublish開始前に全カメラをopenするため、1台でも利用できない場合は起動に失敗し、全resourceをreleaseします。

## Zenohの接続

Zenoh Pythonは`zenoh.Config.from_file()`で接続設定を直接読み込みます。完全なschemaは公式の[Zenoh deployment guide](https://zenoh.io/docs/getting-started/deployment/)と[`DEFAULT_CONFIG.json5`](https://github.com/eclipse-zenoh/zenoh/blob/main/DEFAULT_CONFIG.json5)を参照してください。

同梱のexampleはpeerとして動作し、TCP port 7447でlistenしながらmulticast scoutingも有効にします。

```json5
{
  mode: "peer",
  listen: {
    endpoints: ["tcp/0.0.0.0:7447"],
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

`0.0.0.0`はローカルのwildcard bind addressです。すべてのローカルIPv4 interfaceで接続を受け付ける指定であり、相手側の`connect.endpoints`に書くIPではありません。相手側には、このホストへ到達可能なIPを使い、たとえば`tcp/192.168.1.20:7447`と指定します。

publisher側でport 7447が未使用である必要があります。また、`0.0.0.0`でlistenすると到達可能なすべてのIPv4 interfaceへZenoh endpointを公開します。信頼できないnetworkではbind addressやfirewall ruleを制限してください。

既存のrouterへ接続する場合はclient modeにし、routerへ到達可能なアドレスを指定します。

```json5
{
  mode: "client",
  connect: {
    endpoints: ["tcp/192.168.1.10:7447"],
  },
  scouting: {
    multicast: {
      enabled: false,
    },
  },
}
```

すでにlistenしているpeerへ直接接続する場合は、次のように指定します。

```json5
{
  mode: "peer",
  connect: {
    endpoints: ["tcp/192.168.1.20:7447"],
  },
  scouting: {
    multicast: {
      enabled: false,
    },
  },
}
```

1本の直接TCP接続について、接続開始側は片方だけで十分です。一方が固定portでlistenし、もう一方がそのhostとportへconnectします。multicast scoutingが利用できる環境では、明示的なendpoint自体が不要な場合もあります。

## 別の場所にある設定ファイルを使う

CLIに残るのは、2つの設定ファイルを選ぶオプションだけです。

```console
$ uv run node.py \
    --zenoh-config /etc/camera-node/zenoh.json5 \
    --node-config /etc/camera-node/node.json5
```

カメラ、publisher、Zenohの個別設定をCLIから上書きすることはできません。

## 開発時の検証

変更後はformatter、lint、型検査、単体テストを実行します。

```console
$ uv run --group example ruff format --check .
$ uv run --group example ruff check .
$ uv run --group example pyright
$ uv run --group example python -m unittest discover -v
```
