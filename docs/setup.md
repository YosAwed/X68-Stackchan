# セットアップ手順

> **NVIDIA GPU が無い母艦で動かしたい場合は [setup-macmini.md](setup-macmini.md) (VOICEVOX 経路) を参照**。こちらは Windows + WSL2 + CUDA 経路 (Irodori-TTS-Lite) の手順。
>
> 母艦 PC 側だけを先に準備する場合は [pc-setup.md](pc-setup.md) に要点をまとめている。

## 0. 用意するもの

- M5Stack CoreS3 SE 本体 + USB-C ケーブル
- スタックちゃん Takao Base (組立済 or キット)
- Feetech SCS0009 シリアルサーボ ×2 (Yaw=ID1 / Pitch=ID2)
  - StackChan 基板経由で UART (GPIO6/7, 1 Mbps) に接続。SG90 PWM サーボ用ではない
  - サーボを使わない場合は `config.h` の `SERVO_ENABLED` を `0` にする
- Windows 11 + WSL2 (Ubuntu 22.04 以降) + NVIDIA GPU (Ampere 以降, VRAM 12GB 以上推奨。8GB は whisper-small + Irodori + Ollama qwen2.5:7b で結構ギリ)
- 同一 Wi-Fi LAN (CoreS3 から母艦に IP で届くこと)

> バッテリ駆動の挙動 (低電池サーボ抑止 / 5 分 idle で deep sleep / 電源ボタンで復帰) は [docs/architecture.md#電源--バッテリ駆動](architecture.md#電源--バッテリ駆動) 参照。

## 1. 母艦 (Windows + WSL2) 側のセットアップ

> [Irodori-TTS-Lite](https://github.com/kizuna-intelligence/Irodori-TTS-Lite) は CUDA + Triton 前提なので WSL2 (Linux) 内で動かす。Windows ネイティブだと Triton が走らない。

### 1-0. WSL2 + CUDA の前提

Windows ホスト側に最新の NVIDIA ドライバを入れ、WSL2 (Ubuntu) を起動して以下が通ることを確認する。

```bash
nvidia-smi              # ホストの GPU が見える (WSL2 が透過利用)
```

WSL2 内で必要なシステムパッケージ:

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv build-essential ffmpeg git
```

### 1-1. Ollama を起動 (WSL2 内)

Python 側に依存しないので先に済ませてしまう。

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama serve            # 11434 で待ち受け (別 tty かバックグラウンドで)
ollama pull qwen2.5:7b  # 日本語の出が素直なものを推奨
```

### 1-2. server/ の venv 作成と依存インストール

**順序が重要**: 先に venv を作って activate し、その中で torch (CUDA) → 改造済み Irodori fork → `requirements-cuda.txt` の順に入れる (venv の外で入れても見えない)。

```bash
cd server
python3.11 -m venv .venv
source .venv/bin/activate

# (1) CUDA 対応 torch を先に。cu121 の例。GPU 世代に合わせて cu124 等に変える
pip install --index-url https://download.pytorch.org/whl/cu121 torch torchaudio

# (2) 改造済み Irodori-TTS-Lite (irodori_tts 親パッケージ + infer モジュール同梱 fork)
pip install git+https://github.com/YosAwed/Irodori-TTS-Lite.git@main

# (3) 残り (FastAPI / faster-whisper / pyopenjtalk など)
pip install -r requirements-cuda.txt
```

事前に Irodori 単体で動作確認したい場合 (推奨):

```bash
python -c "
import pyopenjtalk, irodori_tts_lite, sys, infer
irodori_tts_lite.configure(use_fused=True, force_fp16=True)
irodori_tts_lite.patch()
ckpt = irodori_tts_lite.resolve_checkpoint(None)
phs = pyopenjtalk.g2p('テスト', kana=False).split()
infer.FIXED_SECONDS = max(2.0, len(phs) / 11.0 + 0.6)
sys.argv = ['infer', '--checkpoint', ckpt, '--text', 'テスト', '--output-wav', '/tmp/t.wav', '--no-ref']
infer.main()
"
# 初回は HuggingFace (kizuna-intelligence/Irodori-TTS-Lite-int4) から weights を auto-download
```

### 1-3. `.env` の編集と uvicorn 起動

```bash
cp .env.example .env
# .env を編集:
#   OLLAMA_MODEL     — pull したモデル名と一致させる
#   OLLAMA_TEMPERATURE / OLLAMA_NUM_PREDICT — 応答の揺れと長さ
#   MAX_SESSIONS     — メモリ上に保持する sid 数
#   MAX_AUDIO_BYTES  — /chat で受け付ける WAV の最大サイズ
#   WHISPER_MODEL    — VRAM に余裕があれば medium / large-v3
#   IRODORI_REF_WAV  — 参照音声 WAV のパス (空なら --no-ref / voice-design)
#   IRODORI_CHECKPOINT — 通常は空で OK (HF から auto-download)

uvicorn main:app --host 0.0.0.0 --port 8000
```

初回起動で `Irodori-TTS-Lite ready` のログが出るまで数秒〜十数秒。別端末から疎通確認:

```bash
curl http://localhost:8000/ready

# TTS 単体
curl -D - -X POST -F "text=テストです" http://localhost:8000/speak --output speak.wav

# LLM + TTS
curl -D - -X POST -F "text=こんにちは" http://localhost:8000/chat_text --output chat_text.wav

# 適当な短い WAV (16k mono) を投げて STT + LLM + TTS
curl -D - -X POST -F "audio=@hello.wav" http://localhost:8000/chat --output reply.wav
```

`reply.wav` がぺけ子ちゃんの声になっていれば成功。

`curl -D -` で表示される `X-Stackchan-Timing` に `stt` / `llm` / `tts` / `total` の処理時間が出る。Irodori 経路では `/ready` の `components.tts.last_infer_ms` でも直近の推論時間を確認できる。
CoreS3 のシリアルログにも `[TIME]` と `[TTS ]` が表示される。

> **性能上の注意**: 現在の [server/tts_irodori.py](../server/tts_irodori.py) は upstream の CLI shape (`sys.argv` を組んで `infer.main()` を叩き、tempfile WAV を読み戻す) をそのまま in-process で再現している。`infer.main()` が呼び出しごとに `InferenceRuntime` を作り直す実装なら、`/chat` の TTS フェーズが毎回秒オーダー。処理時間は `X-Stackchan-Timing` と `/ready` の `components.tts.last_*` で確認する。fork 側で `InferenceRuntime` をシングルトン化して `synthesize(text) -> waveform` をエクスポートし、`server/tts_irodori.py` の tempfile + sys.argv ブロックを直接呼び出しに差し替えるのが本筋。

### 1-4. 母艦の IP を控える

CoreS3 からは Windows ホストの LAN IP に届けばよい (WSL2 への port forward は WSL2 が透過的に面倒を見る)。

```powershell
# Windows 側 PowerShell
ipconfig | findstr IPv4    # 例: 192.168.1.42
```

## 2. CoreS3 側のセットアップ

CoreS3 側は **「① 表情画像 (LittleFS) を焼く」「② ファーム本体を焼く」「③ `config.h` で Wi-Fi と母艦 IP を設定」** の 3 つを別々に行う必要がある。①②③ をどの順で実施してもよいが、いずれか 1 つでも欠けると `LittleFS init failed` や `WiFi connect failed` で起動しない。

### 2-1. PlatformIO 環境

PlatformIO CLI を pip でインストールする (`apt` 版は古くて ESP32-S3 非対応のことが多い):

```bash
pip install platformio
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
pio --version   # 6.x 以上であること
```

Windows ネイティブの PlatformIO でビルドする場合、ESP32 toolchain が日本語や空白を含むパスで失敗することがある。その場合は `C:\pio-build` など ASCII だけの作業ディレクトリにリポジトリを置くか、WSL2 側でビルドする。

```bash
cd firmware
cp include/config.h.example include/config.h
# config.h を編集:
#   WIFI_SSID, WIFI_PASS, SERVER_HOST ("192.168.1.42"), SERVER_PORT (8000)
```

### 2-2. ぺけ子ちゃん表情の生成と LittleFS への書込

1. 36 表情のスプライトシート 4 枚を `firmware/assets/raw/` に置く
2. 240x240 JPG への分割スクリプトを実行 (要 Pillow)

```bash
pip install Pillow
python3 firmware/tools/slice.py
# → firmware/data/face_01.jpg ... face_36.jpg が生成される
```

3. LittleFS イメージをビルド & 書き込み

```bash
pio run -e m5stack-cores3 -t buildfs
pio run -e m5stack-cores3 -t uploadfs
```

`uploadfs` は数十秒〜1 分かかる。最終行に `Wrote ... bytes` と出れば成功。

> ※ 表情のシーン割当を変えたい時は `firmware/include/face_map.h` の
>   `FACE_xxx` 定数の右辺を書き換える。再ビルドだけで反映される。

### 2-3. プログラム本体の書込

```bash
pio run -e m5stack-cores3 -t upload
pio device monitor -e m5stack-cores3
```

シリアルに `WiFi connected: 192.168.x.x` と出れば疎通 OK。

## 3. 動作確認 (段階的に切り分ける)

依存が多いので、いきなり会話まで通すよりも **「顔表示 → サーバ接続 → 会話」** の順で 1 段ずつ確かめると詰まりにくい。

### ステップ 1: 顔画像が出るか (CoreS3 単体)

母艦サーバを止めた状態で電源を入れる。LittleFS と画面・サーボだけが対象。

- ✅ Human68k 風スプラッシュ + 起動チャイム
- ✅ ぺけ子ちゃんが手を振る (face_36) → 通常待機 (face_01)
- ❌ `LittleFS init failed` が出る場合は `pio run -t uploadfs` をやり直す
- ❌ サーボが暴れる場合は `config.h` の `SERVO_ENABLED=0` でいったん切る

ここが通れば、ファーム本体 + LittleFS イメージ + 表情マップは正常。

### ステップ 2: 母艦サーバに届くか (Wi-Fi + HTTP)

母艦の uvicorn を起動した状態で、CoreS3 のシリアルログを確認する。

- ✅ `WiFi connected: 192.168.x.x` が出る → Wi-Fi 設定 OK
- ✅ Idle 中に 30 秒おきに `[PULL] 204` (or `[PULL] ok`) が出る → `/pull` で母艦に到達できている
- ❌ `WiFi connect failed` → `config.h` の SSID/PASS、2.4GHz 帯か
- ❌ `[PULL] http err` → `config.h` の `SERVER_HOST` が母艦の LAN IP になっているか、母艦のファイアウォールで 8000 が開いているか

母艦側で `curl -X POST -H "X-Stackchan-Token: $ENQUEUE_TOKEN" -F "text=テスト" http://localhost:8000/enqueue` を実行すれば CoreS3 が次の `/pull` で発話する。`ENQUEUE_TOKEN` は `server/.env` に設定した値を使う。経路全体 (Wi-Fi → HTTP → TTS → CoreS3 で再生) の疎通確認に使える。

### ステップ 3: 会話 (push-to-talk)

ステップ 2 まで通っていれば、あとは録音 → STT → LLM → TTS の往復だけ。

1. 画面下中央 (BtnA 領域) をタッチで押し続けると「？」顔 (face_15) に変わって録音開始
2. 離すと「考え中」顔 (face_21, 手を顎) → 数秒後 "ピッ" の後にぺけ子ちゃんが返事を喋る
3. 喋っている間 face_02 (口閉) / face_29 (口開) を PCM 振幅で切り替えて口パク
4. エラー時はあわてた表情 + 下降ビープ (`config.h` の `HTTP_TIMEOUT_MS` を超えた場合など)

ここで止まる場合は、母艦側の uvicorn ログで STT / LLM / TTS のどこで落ちたかを見る (`X-Stackchan-Timing` ヘッダにも各段の所要時間が出る)。

## 4. トラブルシュート

| 症状 | 切り分け |
|------|----------|
| `WiFi connected` が出ない | `config.h` の SSID/PASS、2.4GHz 帯か |
| 録音できているが応答が無音 | 母艦側の uvicorn ログを確認。Whisper でテキスト化されているか |
| `/chat` が `413` になる | 録音 WAV が `MAX_AUDIO_BYTES` を超えている。`MAX_REC_SECONDS` を下げるか `MAX_AUDIO_BYTES` を上げる |
| 応答テキストは出るのに音が出ない | `Irodori-TTS-Lite ready` ログが出ているか / `nvidia-smi` で VRAM が足りているか |
| `CUDA out of memory` | `WHISPER_MODEL` を下げる、`IRODORI_FORCE_FP16=1` を維持、他の GPU プロセスを落とす |
| `Triton` 関連エラー | WSL2 / Linux で実行しているか確認。Windows ネイティブだと Triton が動かない |
| 早口/雑音で誤認識 | `WHISPER_MODEL` を `large-v3` に上げる、もしくは録音ゲインを下げる |
| `LittleFS init failed` と画面に出る | `pio run -t uploadfs` が済んでいない、もしくはパーティションが古い |
| 口パクのテンポが合わない | `main.cpp` の `RMS_THRESH` (既定 2200) を上下する |
