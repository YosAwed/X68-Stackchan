# セットアップ手順 (Mac mini + macsay / VOICEVOX / Kokoro 代替構成)

> こちらは **代替構成**。メインは [setup.md](setup.md) (Windows + WSL2 + CUDA / Irodori-TTS-Lite)。
> Mac mini など NVIDIA GPU が無い母艦で動かしたい場合のみこのファイルに従う。
>
> 違いは TTS だけで、firmware 側は無改造で同じものが動く (`docs/setup.md` のセクション 2 以降をそのまま使う)。
> 母艦 PC 側だけの要点は [pc-setup.md](pc-setup.md) にもまとめている。

## 0. 用意するもの

- M5Stack CoreS3 SE 本体 + USB-C ケーブル
- スタックちゃん Takao Base (組立済 or キット)
- Feetech SCS0009 シリアルサーボ ×2 (Yaw=ID1 / Pitch=ID2)
  - StackChan 基板経由で UART (GPIO6/7, 1 Mbps) に接続。SG90 PWM サーボ用ではない
  - サーボを使わない場合は `config.h` の `SERVO_ENABLED` を `0` にする
- Mac mini (Apple Silicon 推奨)
- 同一 Wi-Fi LAN (CoreS3 から Mac mini に IP で届くこと)

## 1. Mac mini 側のセットアップ

### 1-1. TTS backend を選ぶ

Mac だけでまず実機疎通を確認するなら、追加サービス不要の `macsay` を使う。これは macOS の `say` と `afconvert` で 16 kHz / mono / PCM16 の WAV を返すテスト用 backend。

キャラクター声にしたい場合は `voicevox` を使う。

軽量なローカル ONNX 音声も比較したい場合は `kokoro` を使える。通常運用の
切替前に、後述の A/B 比較ツールで日本語の読みと声質を確認することを推奨。

### 1-2. VOICEVOX engine を起動する場合

GUI 版 VOICEVOX をインストールするか、`voicevox_engine` Docker イメージを起動:

```bash
docker run --rm -p '50021:50021' voicevox/voicevox_engine:cpu-latest
```

`curl http://localhost:50021/version` が JSON を返せば OK。

`TTS_BACKEND=macsay` で使う場合、この手順は不要。

### 1-3. Ollama を起動

```bash
brew install ollama
ollama serve            # 11434 で待ち受け
ollama pull qwen2.5:7b  # 日本語の素直さで qwen2.5 か gemma2 系がおすすめ
```

### 1-4. このリポジトリの server/ を立ち上げる

Mac mini 経路では torch / Irodori 系の依存は不要。`requirements-macmini.txt` を使う。

```bash
cd server
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-macmini.txt

cp .env.example .env
# .env を編集:
#   TTS_BACKEND=macsay         # まず実機疎通を見るならこちら
#   MACSAY_VOICE=Kyoko
#   MACSAY_RATE=185
#   # TTS_BACKEND=voicevox     # VOICEVOX を使う場合はこちら
#   WHISPER_DEVICE=auto        # Apple Silicon は auto / cpu
#   VOICEVOX_SPEAKER=3         # voicevox の場合だけ。好みの話者
#   OLLAMA_MODEL=qwen2.5:7b
#   OLLAMA_TEMPERATURE=0.7
#   OLLAMA_NUM_PREDICT=200
#   MAX_SESSIONS=16
#   MAX_AUDIO_BYTES=2097152

uvicorn main:app --host 0.0.0.0 --port 8000
```

起動ログに `TTS backend = voicevox` と `VOICEVOX backend ready (...)` が並べば成功。
`macsay` の場合は `TTS backend = macsay` と `macsay backend ready (...)` が並べば成功。

別端末から疎通確認:

```bash
curl http://localhost:8000/ready
curl -D - -X POST -F "text=テストです" http://localhost:8000/speak --output speak.wav
curl -D - -X POST -F "text=こんにちは" http://localhost:8000/chat_text --output chat_text.wav
curl -D - -X POST -F "audio=@hello.wav" http://localhost:8000/chat --output reply.wav
```

`reply.wav` がスタックちゃんの声になっていれば OK。`X-Stackchan-Timing` で LLM / TTS / total の処理時間を確認できる。

### 1-4a. Kokoro をVOICEVOXとA/B比較する

`requirements-macmini.txt` の導入後、日本語G2P用辞書とKokoroモデルを一度だけ取得する。

```bash
cd server
.venv/bin/python -m unidic download
mkdir -p models/kokoro
curl -L -o models/kokoro/kokoro-v1.0.onnx \
  https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx
curl -L -o models/kokoro/voices-v1.0.bin \
  https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin
```

VOICEVOX Engineを起動した状態で比較ツールを実行する。

```bash
.venv/bin/python compare_tts.py
```

`data/tts_ab/<日時>/` に、同じ番号の `NN_voicevox.wav` と `NN_kokoro.wav`、
および `report.csv` / `report.json` が生成される。任意の文だけ比較する場合:

```bash
.venv/bin/python compare_tts.py \
  --text "ぺけ子ちゃん、おはよう。" \
  --text "X68000を起動します。"
```

試聴後にKokoroへ切り替える場合は `.env` を次のようにしてFastAPIを再起動する。

```dotenv
TTS_BACKEND=kokoro
KOKORO_VOICE=jf_alpha
KOKORO_SPEED=1.00
```

### 1-5. Mac mini の IP を控える

```bash
ipconfig getifaddr en0   # 例: 192.168.1.42
```

### 1-6. 必要な時だけ手動で起動する

VOICEVOX Engine と母艦 FastAPI サーバは、管理スクリプトでまとめて起動・停止できる。
母艦は VOICEVOX の HTTP 応答を待ってから起動する。

```bash
# 起動
scripts/mac_stackchan_services.sh start
# このターミナルを開いたままにする。Ctrl-C で両方停止。

# プロセスと HTTP readiness を確認
scripts/mac_stackchan_services.sh status

# ログを追う
scripts/mac_stackchan_services.sh logs

# 停止
# 別ターミナルから止めたい場合に使用
scripts/mac_stackchan_services.sh stop
```

自動起動の登録は行わない。ログは `~/Library/Logs/X68-Stackchan/` に保存される。

外出先でデモする場合は、次の順で準備する。

1. Mac を起動する
2. iPhone のインターネット共有を有効にする
3. Mac と Stackchan を同じインターネット共有へ接続する
4. Finder で `scripts/Start-Stackchan.command` をダブルクリックする
5. `Demo network`、`Ollama`、`VOICEVOX`、`Stackchan server` がすべて
   `ready` になったらデモを開始する

終了時は、開いたターミナルで `Ctrl-C` を押す。スクリプトはFWの `SERVER_HOST`
とMacのIPが一致しない場合、母艦を起動せず接続手順を表示する。

## 2. CoreS3 側のセットアップ

[setup.md のセクション 2](setup.md#2-cores3-側のセットアップ) と完全に同じ手順。`SERVER_HOST` を Mac mini の IP に向けるだけ。

### 2-1. Mac から USB 直結で焼く場合

このリポジトリを Mac 上で開いている場合は、PlatformIO の手順をまとめた補助スクリプトが使える。

```bash
# まずビルドだけ確認
scripts/mac_stackchan_test.sh --build-only

# CoreS3 を USB-C で接続して、LittleFS + ファームを書き込み、そのままシリアル監視
scripts/mac_stackchan_test.sh --flash-all

# 書き込み済みでログだけ見たい場合
scripts/mac_stackchan_test.sh --monitor-only
```

自動検出できない場合は `/dev/cu.usbmodem...` などのポートを明示する。

```bash
scripts/mac_stackchan_test.sh --flash-all --port /dev/cu.usbmodemXXXX
```

`No USB serial device found` が出る場合は、充電専用ではない USB-C ケーブルに替える、CoreS3 を再接続する、または CoreS3 の BOOT を押しながら RESET を押してダウンロードモードに入れてから再実行する。

## 3. 動作確認

[setup.md のセクション 3](setup.md#3-動作確認-段階的に切り分ける) と同じ。CoreS3 単体 → サーバ疎通 → 会話、の順に切り分けるとよい。

## 4. トラブルシュート

| 症状 | 切り分け |
|------|----------|
| `WiFi connected` が出ない | `config.h` の SSID/PASS、2.4GHz 帯か |
| 録音できているが応答が無音 | Mac mini 側の uvicorn ログを確認。Whisper でテキスト化されているか |
| `/chat` が `413` になる | 録音 WAV が `MAX_AUDIO_BYTES` を超えている。`MAX_REC_SECONDS` を下げるか `MAX_AUDIO_BYTES` を上げる |
| 起動ログが `TTS backend = irodori` になる | `.env` の `TTS_BACKEND=macsay` または `TTS_BACKEND=voicevox` が読まれていない (`.env` の位置 / 改行コード確認) |
| `tts_irodori` の import エラーが出る | `TTS_BACKEND=irodori` のまま起動している。`.env` を見直す |
| 応答テキストは出るのに音が出ない | VOICEVOX engine の port 50021 が開いているか / `VOICEVOX_HOST` が正しいか |
| `macsay` で日本語音声にならない | `say -v '?' | grep Japanese` で日本語 voice を確認し、`MACSAY_VOICE` を変更する |
| 早口/雑音で誤認識 | `WHISPER_MODEL` を `medium` か `large-v3` に上げる、もしくは録音ゲインを下げる |
| `LittleFS init failed` と画面に出る | `pio run -t uploadfs` が済んでいない、もしくはパーティションが古い |
| 口パクのテンポが合わない | `main.cpp` の `RMS_THRESH` (既定 2200) を上下する |
