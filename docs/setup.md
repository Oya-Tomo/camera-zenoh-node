# セットアップ

## 必要なもの

- Python 3.13
- [uv](https://docs.astral.sh/uv/)
- OpenCV から認識できるカメラ

依存パッケージをインストールします。

```console
$ uv sync
```

次のコマンドがヘルプを表示すれば、Python 環境の準備は完了です。

```console
$ uv run node.py --help
```

## カメラの確認

Linux では、接続された Video4Linux デバイスを確認します。

```console
$ ls /dev/video*
```

`could not open camera device` と表示される場合は、次を確認してください。

- `--device` に指定した番号の `/dev/video*` が存在する
- 実行ユーザーにデバイスの読み書き権限がある（Linux では `video` group も確認する）
- 他のプロセスがカメラを占有していない

Docker などのコンテナ内で実行する場合は、カメラデバイスをコンテナへ渡す必要があります。

## Zenoh の接続

設定を指定しない場合は Zenoh のデフォルト設定を使います。同一ネットワーク上の peer は multicast scouting により自動検出されます。

Zenoh router へ client として接続する場合は、CLI から指定できます。

```console
$ uv run node.py --mode client --connect tcp/192.168.1.10:7447
```

複数の endpoint は `--connect` を繰り返して指定します。

```console
$ uv run node.py \
    --mode client \
    --connect tcp/router-a:7447 \
    --connect tcp/router-b:7447
```

JSON5 設定ファイルも利用できます。

```console
$ uv run node.py --config zenoh.json5
```

設定の優先順位は、低い方から「Zenoh の既定値」「`--config` の設定ファイル」「`--mode`、`--connect`、`--listen` などの専用オプション」「`--cfg`」です。

multicast scouting を無効にする場合は `--no-multicast-scouting` を指定します。その他の設定は、現行の Zenoh Python examples と同様に `--cfg KEY:VALUE` で上書きできます。

```console
$ uv run node.py \
    --no-multicast-scouting \
    --cfg 'transport/unicast/max_links:2'
```

`VALUE` は Zenoh が対象項目に期待する JSON5 値で指定します。

## 開発時の検証

変更後は formatter、lint、型検査、単体テストを実行します。

```console
$ uv run ruff format --check .
$ uv run ruff check .
$ uv run pyright
$ uv run python -m unittest discover -v
```
