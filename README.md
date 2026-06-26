# X68-Stackchan — ぺけ子ちゃん版 スタックちゃんカスタムファーム

M5Stack 公式スタックちゃん (CoreS3 SE) を、X68000 擬人化キャラ **ぺけ子ちゃん** にしてしまうカスタムファーム。
推論・音声認識・音声合成はすべて母艦 PC/Mac 上で走らせる、**完全ローカルの AI 会話エージェント**。クラウド API は使わない。

> **CoreS3 単体では完結しません。** 録音・再生・表示・サーボ・頭頂タッチは CoreS3、STT (faster-whisper) / LLM (Ollama) / TTS (Irodori-TTS-Lite または VOICEVOX) は母艦 PC/Mac 上の FastAPI サーバが担当する分担構成です。Wi-Fi LAN 越しに `multipart/form-data` で繋がります。母艦が無くても **`OFFLINE_MODE=1` を入れれば** 起動チャイム・スプラッシュ・表情アニメ・頭撫で反応・LED 演出までは単体で動作確認できます。

### どの手順を読むか

| やりたいこと                              | 読むセットアップ                                  |
|-----------------------------------------|--------------------------------------------------|
| まず CoreS3 にファームを焼きたい (本書下部) | [§ インストール / 書き込み](#インストール--書き込み) |
| 母艦 PC/Mac の AI サーバを立てたい       | [docs/setup.md](docs/setup.md) (Win+WSL+CUDA) または [docs/setup-macmini.md](docs/setup-macmini.md) (Mac/VOICEVOX) |
| 母艦側だけ準備したい                    | [docs/pc-setup.md](docs/pc-setup.md)             |
| 内部設計・API 仕様                      | [docs/architecture.md](docs/architecture.md)     |

母艦の構成は 2 通り用意してあり、`.env` の `TTS_BACKEND` 切替で同じ CoreS3 ファームから使い分けられる。

| 自分の母艦                       | TTS バックエンド     | 読むセットアップ                                  |
|---------------------------------|---------------------|--------------------------------------------------|
| Windows 11 + WSL2 + NVIDIA GPU  | Irodori-TTS-Lite    | [docs/setup.md](docs/setup.md)                   |
| Mac mini / CUDA なしの PC       | VOICEVOX (HTTP)     | [docs/setup-macmini.md](docs/setup-macmini.md)   |

> TTS に [Irodori-TTS-Lite](https://github.com/YosAwed/Irodori-TTS-Lite) (upstream: [kizuna-intelligence/Irodori-TTS-Lite](https://github.com/kizuna-intelligence/Irodori-TTS-Lite) の pip 対応フォーク) を採用している関係で、Irodori 経路は CUDA 必須 (Ampere 以降推奨)。NVIDIA GPU が無い場合は VOICEVOX 経路に切替。

起動時に Human68k 風のスプラッシュが流れて、ぺけ子ちゃんの Avatar が立ち上がる、というのが完成形のイメージ。
分割済みの 36 表情 (`firmware/data/face_01.jpg` 〜 `face_36.jpg`) はリポに同梱しており、`pio run -t uploadfs` でそのまま LittleFS に焼ける。元になったスプライトシート画像は同人由来のため `firmware/assets/raw/` には commit せず、`.gitignore` で除外している (再生成したい場合のみ自分で配置して `firmware/tools/slice.py` を回す)。スプラッシュ画面の色は `firmware/include/pekeko_theme.h`、シーンごとの表情割当は `firmware/include/face_map.h` で差し替えられる。

## ぺけ子ちゃんの仕草 (kawaii ふるまい一覧)

`OFFLINE_MODE` でも全部動くので、Wi-Fi 接続前にここだけ動作確認できる。

| シーン                          | 表情                                              | 音 / LED                             |
|--------------------------------|--------------------------------------------------|--------------------------------------|
| 起動完了                       | F_WAVE (手を振る) → F_NEUTRAL                      | A4→C#5→E5→A5 「ピロリロロ〜ン」      |
| アイドル中・まばたき (4〜8秒) | F_LAUGH_EYES_CLOSED を 150ms                      | —                                    |
| アイドル中・マイクロ表情 (8〜15秒) | 5択 (F_SOFT_SMILE / F_SPARKLE_EYES / F_BASHFUL / F_BORED / F_YAWN_SMALL) を 800ms | —                          |
| LCD タッチした瞬間             | F_SURPRISED「ハッ」を 150ms                       | —                                    |
| 録音中                         | F_QUESTION (はてな)                              | —                                    |
| 応答音声直前                   | —                                                | D6→G6 「ピロン♪」 (ack beep)        |
| 応答音声再生中                 | 口パク 3 段階 (RMS 高さで closed / open / wide)、emote 別ペア | —                            |
| **頭頂を撫でた直後**           | **F_BASHFUL (はにかみ)**                          | **F6→A6→C7 「テロリーン♪」 + 薄ピンク LED** |
| **頭頂を撫で続けて 1.5 秒〜**  | **F_LAUGH_EYES_CLOSED (とろけ笑い)**             | **暖オレンジ LED**                    |
| **頭頂を撫で続けて 3 秒〜**    | **F_SLEEPING (Zzz 夢見心地)**                     | **紫マゼンタ LED**                    |
| 頭から手を離した               | F_SOFT_SMILE 600ms → Idle                         | LED 消灯                              |
| Wi-Fi 失敗 / サーバ 5xx 等     | F_CONCERNED / F_DIZZY / F_BORED 等                 | 失敗系ビープ (種類別)                |

LED の 12 個は全て `M5StackChan.showRgbColor()` で同色制御、撫で中は 2.5Hz の sin で脈動する。

### 最小確認手順 (段階的に切り分け)

初めて触るときは下の順に確かめると詰まりにくい (詳細は [docs/setup.md §3](docs/setup.md#3-動作確認-段階的に切り分ける) に同じ内容で展開)。

1. **(母艦無しでも OK)** `OFFLINE_MODE=1` で焼く → 起動チャイム・スプラッシュ・顔・まばたき・マイクロ表情・頭撫で反応まで確認
2. **母艦サーバへの到達確認** — `OFFLINE_MODE=0` に戻して uvicorn を起こす。シリアルログに `WiFi connected` と `/pull` の応答が出ることを見る
3. **会話 (push-to-talk)** — 画面下をタッチ長押し → 録音 → STT → LLM → TTS が往復する

Windows + WSL2 で Irodori サーバを CoreS3 から使う場合は、WSL の localhost 転送を LAN に出すプロキシも必要。下のスクリプトで uvicorn と LAN プロキシをまとめて起動できる。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-irodori-server.ps1 -ListenAddress 192.168.0.5 -StopExisting
```

## 構成

```
┌─────────────────────────────┐         Wi-Fi (HTTP)          ┌────────────────────────────┐
│  M5Stack CoreS3 SE          │  ──── multipart/form-data ─► │  母艦 PC / Mac              │
│  ─ M5Unified + Avatar       │     録音 WAV (16k mono)       │  FastAPI                    │
│  ─ 内蔵 PDM マイクで録音    │                               │   /chat        (会話)       │
│  ─ LCD タッチで push-to-talk │                              │   /pull        (定期発話)   │
│  ─ 頭頂 Si12T で head-pat   │                               │   /enqueue     (外部 push)  │
│  ─ 内蔵スピーカで再生       │  ◄──── audio/wav (応答) ─────│                             │
│  ─ 口パク同期で Avatar 表情 │                               │   1. faster-whisper (STT)   │
│  ─ サーボ (Yaw/Pitch) で首振り │                            │   2. Ollama (LLM)           │
│  ─ RGB LED 12 個 (頭撫で連動) │ ──── GET /pull?wait=0 ────► │   3. Irodori or VOICEVOX    │
│                             │  ◄──── audio/wav (発話) ──── │   4. scheduler (任意)       │
└─────────────────────────────┘                               │      croniter で定時発話     │
                                                              └────────────────────────────┘
```

- 通常会話は CoreS3 → 母艦の **要求-応答** (`POST /chat`)
- 定期発話 / 外部 push は母艦 → CoreS3 を **long-poll** で実現 (`GET /pull`、CoreS3 が Idle 中に短ポーリング)
- 外部システム (Discord bot / curl など) からは `X-Stackchan-Token` 付きの `POST /enqueue` で同じキューに発話を積める
- 頭頂タッチセンサー (Si12T、I2C 接続) は **M5StackChan-BSP** ライブラリ経由で読む

## ディレクトリ

```
.
├── README.md                  # このファイル
├── docs/
│   ├── architecture.md        # 詳細アーキテクチャ
│   ├── pc-setup.md            # 母艦 PC 側だけのセットアップ
│   ├── setup.md               # セットアップ手順 (CUDA / WSL2 / Irodori)
│   └── setup-macmini.md       # 代替セットアップ (Mac mini / VOICEVOX)
├── firmware/                  # CoreS3 側 (PlatformIO + Arduino)
│   ├── platformio.ini         # platform は pioarduino フォーク 55.03.38-1
│   ├── partitions_pekeko.csv  # OTA無し / app 6MB / LittleFS 10MB
│   ├── src/main.cpp
│   ├── include/
│   │   ├── config.h.example   # Wi-Fi / 母艦 LAN IP / OFFLINE_MODE / サーボ / タイムアウト
│   │   ├── audio_recorder.h   # 内蔵 PDM マイク録音 + overflow 自動停止
│   │   ├── http_client.h      # multipart で /chat に POST、GET /pull で long-poll
│   │   ├── servo_controller.h # Feetech SCS0009 シリアルサーボ ×2 (Yaw/Pitch)
│   │   ├── avatar_state.h     # 状態 enum (Boot / Idle / Listening / Thinking / Speaking / Headpat / Error)
│   │   ├── pekeko_theme.h     # X68000 風スプラッシュ
│   │   ├── pekeko_face.h      # LittleFS から face_NN.jpg を描く (drawJpgFile)
│   │   ├── face_map.h         # シーン→表情番号 (3 段階口パク + emote ペア)
│   │   ├── chime.h            # 起動 / ack / headpat / overflow / 各種エラービープ
│   │   └── power.h            # 低電池監視 / idle deep sleep
│   ├── tools/
│   │   ├── slice.py                # 4 枚のスプライトシートを 36 個に分割 (初回)
│   │   └── align_face_bottoms.py   # 36 顔の bbox を 2D 中心揃え (位置ズレ修正)
│   ├── assets/raw/            # 元シート画像置き場 (gitignore、LittleFS には焼かない)
│   └── data/                  # LittleFS イメージ (pio run -t uploadfs で焼く)
│       └── face_01.jpg .. face_36.jpg  # 2D 中心揃え済み 36 表情
└── server/                    # 母艦側 (Python / FastAPI)
    ├── requirements-cuda.txt    # WSL2 + CUDA 版 (Irodori-TTS-Lite)
    ├── requirements-macmini.txt # Mac mini 版 (VOICEVOX)
    ├── main.py                # /chat /chat_text /speak /ready /pull /enqueue /scheduler/status
    ├── stt.py                 # faster-whisper ラッパ
    ├── llm.py                 # Ollama クライアント
    ├── tts.py                 # backend dispatcher (TTS_BACKEND env で切替)
    ├── tts_irodori.py         # Irodori-TTS-Lite (in-process / CUDA)
    ├── tts_voicevox.py        # VOICEVOX HTTP (Mac mini など)
    ├── scheduler.py           # croniter 駆動の定期発話タスク (SCHEDULE_ENABLED=1 で起動)
    ├── utterance_queue.py     # 定期発話 / 外部 push を貯める asyncio.Queue
    ├── schedule.json.example  # cron トリガ定義のサンプル
    ├── persona.py             # スタックちゃんのキャラ付け system prompt
    └── .env.example           # ホスト・ポート・モデル名・参照音声などの設定
```

## インストール / 書き込み

CoreS3 SE 側のファームウェアと表情画像 (LittleFS) を焼くまでの手順。母艦サーバ (FastAPI) のセットアップは [docs/setup.md](docs/setup.md) または [docs/setup-macmini.md](docs/setup-macmini.md) を参照。

### 0. 用意するもの

- **M5Stack CoreS3 SE** + **M5StackChan キット (公式)**
  - 公式キットには頭頂タッチセンサー (Si12T、I2C)、I/O expander、RGB LED 12 個、サーボ電源回路が含まれる
  - Feetech SCS0009 シリアルサーボ ×2 (Yaw=ID1 / Pitch=ID2) を Stack-chan 基板経由で接続
- **USB-C ケーブル** (CoreS3 を母艦 PC に繋ぐ)
- **2.4GHz Wi-Fi LAN** (母艦と同セグメント)
- **PlatformIO** (`pip install platformio`) — 後述のとおり WSL 上に立てるのが安定
- **esptool** (PlatformIO に同梱、`~/.platformio/penv/Scripts/esptool.exe` などにある)

### 1. 工場 flash のバックアップ (強く推奨)

何かおかしくなった時に **工場出荷状態の M5Launcher に戻せる** よう、フル flash を吸い出しておく。Windows 側のコマンド例:

```powershell
# COM ポートはデバイスマネージャで確認 (CoreS3 SE は VID 0x303A / PID 0x1001)
$ESPTOOL = "$env:USERPROFILE\.platformio\packages\tool-esptoolpy\esptool.py"
$BACKUP_DIR = "$env:USERPROFILE\Backups\StackChan"
mkdir -Force $BACKUP_DIR | Out-Null

# 16MB 全部を dump (~30 秒)
python $ESPTOOL --chip esp32s3 --port COM3 read_flash 0 0x1000000 `
    "$BACKUP_DIR\cores3_se_factory.bin"

# 検証用 SHA256 を併設
Get-FileHash "$BACKUP_DIR\cores3_se_factory.bin" -Algorithm SHA256 |
    Set-Content "$BACKUP_DIR\cores3_se_factory.bin.sha256"
```

このファイル 1 個 (16,777,216 バイト ちょうど) があれば、後から `esptool write_flash 0x0 cores3_se_factory.bin` で完全復元できる。MAC や NVS のキャリブレーションも含まれるため、**他の個体には絶対に書かないこと**。

### 2. ファームのビルド

Windows 側で PlatformIO を回そうとすると、現在の `framework-arduinoespressif32 3.x` 同梱の SDK 周りで `sdkconfig.h` 不在問題に当たることがある (M5StackChan の長い日本語パスでも同様の不安定さが出る)。**WSL2 上に PlatformIO 環境を作って WSL ネイティブ FS でビルドする**のが一番素直。手順:

```bash
# WSL Ubuntu 24.04 などの中で
python3 -m venv ~/pio-venv
~/pio-venv/bin/pip install --upgrade pip wheel
~/pio-venv/bin/pip install platformio

# プロジェクトを WSL ネイティブ FS にコピー (OneDrive 同期下を直接ビルドすると遅い)
mkdir -p ~/X68-firmware
cp -r "/mnt/c/path/to/X68-Stackchan/firmware/." ~/X68-firmware/

# 初回ビルド (pioarduino フォーク + 各種ライブラリのダウンロード含めて ~20 分)
cd ~/X68-firmware
~/pio-venv/bin/pio run -e m5stack-cores3
```

PlatformIO ビルドが成功すると、4 つの `.bin` が `~/X68-firmware/.pio/build/m5stack-cores3/` 配下に揃う:

| ファイル                | オフセット   | 中身                                  |
|------------------------|------------|--------------------------------------|
| `bootloader.bin`       | `0x0000`   | 2nd stage bootloader                 |
| `partitions.bin`       | `0x8000`   | `partitions_pekeko.csv` のテーブル    |
| `firmware.bin`         | `0x10000`  | アプリ本体                            |
| `firmware.factory.bin` | `0x0000`   | 上記をまとめた **書き込み 1 発分**     |

`firmware.factory.bin` 1 個を 0x0 に書けば bootloader + partitions + app をまとめて入れ替えできるので楽。**LittleFS (face JPG) は別途必要** (次節)。

### 3. config.h を作る

`firmware/include/config.h` は **gitignore 対象**。初回は `config.h.example` をコピーして自分の値で埋める。

```bash
cd ~/X68-firmware
cp include/config.h.example include/config.h
# 編集 (Wi-Fi SSID/PSK、母艦の LAN IP、OFFLINE_MODE) ...
~/pio-venv/bin/pio run -e m5stack-cores3   # 再ビルド
```

設定したい主な値:
- `WIFI_SSID` / `WIFI_PASS` — 自宅の 2.4GHz Wi-Fi
- `SERVER_HOST` — 母艦の **LAN IP** (`ipconfig | findstr IPv4` / `ipconfig getifaddr en0` で確認、`localhost` は不可)
- `OFFLINE_MODE` — **`1` にすると Wi-Fi/サーバ無しで起動 → 表情アニメや頭撫でだけ確認できる**。本番運用は `0`
- `SERVO_ENABLED` — サーボ未接続なら `0` でビルド可

### 4. CoreS3 SE に焼く

Windows 側 (USB が直接見える) から esptool で焼くのが速い。WSL の `~/X68-firmware/.pio/build/m5stack-cores3/` から Windows パスへコピーしてから:

```powershell
$ESPTOOL = "$env:USERPROFILE\.platformio\penv\Scripts\esptool.exe"   # pioarduino 版 esptool v5.x
$BUILD = "C:\path\to\bin\output"     # firmware.factory.bin / littlefs.bin を置いた場所

# (a) ファーム本体 (bootloader + partitions + app を 1 発で焼く)
& $ESPTOOL --chip esp32s3 --port COM3 --baud 1500000 `
    write-flash --flash-mode dio --flash-size 16MB `
    0x0 "$BUILD\firmware.factory.bin"

# (b) LittleFS イメージ (顔 JPG 36 枚) を焼く
#     WSL 側で先に: pio run -e m5stack-cores3 -t buildfs して littlefs.bin を生成
& $ESPTOOL --chip esp32s3 --port COM3 --baud 1500000 `
    write-flash --flash-mode dio --flash-size 16MB `
    0x610000 "$BUILD\littlefs.bin"
```

オフセット `0x610000` は `partitions_pekeko.csv` の `spiffs` パーティション (10MB) の開始位置。書き込み後は esptool が自動でハードリセットして起動する。

WSL からそのまま焼きたい場合は `usbipd attach --wsl --busid <COM3 のバス ID>` で USB を WSL に渡せる。だいたい Windows 側 esptool の方が早い。

### 5. 動作確認

シリアルモニタで起動ログを見る:

```powershell
# Windows 側
python -m serial.tools.miniterm COM3 115200
```

期待される起動ログ抜粋:

```
[OFFLINE] skipping WiFi & server ready check (kawaii test mode)   # OFFLINE_MODE=1 時
[M5StackChan] Servo ID: 1 get zero pos: ... from settings
[M5StackChan] Servo ID: 2 get zero pos: ... from settings
[READY] server ok: ...                                            # OFFLINE_MODE=0 時のみ
```

実機側の体感確認:
- 起動チャイム「ピロリロロ〜ン」が鳴って、Human68k 風スプラッシュ → 手振り顔 → 中立顔へ
- 4〜8 秒ごとにまばたき / 8〜15 秒ごとにマイクロ表情がチラッと
- **頭頂を触る**と「テロリーン♪」とはにかみ顔 + ピンク LED、撫で続けると 1.5s でとろけ笑い、3s で Zzz 寝顔へ
- 画面の下をタッチで録音開始 (OFFLINE_MODE 時はサーバに送らず擬似応答)

### 6. (本番運用) 母艦サーバを立てる

母艦 (Windows + WSL + CUDA / Mac mini + VOICEVOX) で FastAPI サーバを動かす。詳細は別ドキュメント:

- Windows + WSL + NVIDIA GPU: [docs/setup.md](docs/setup.md)
- Mac mini / CUDA 無し: [docs/setup-macmini.md](docs/setup-macmini.md)

サーバが上がったら `OFFLINE_MODE=0` で焼き直して接続テスト。

### トラブルシュート

| 症状                                       | 対処                                                                                       |
|------------------------------------------|------------------------------------------------------------------------------------------|
| ビルド時に `sdkconfig.h: No such file`     | `platformio.ini` は pioarduino フォーク必須 (公式 platformio/espressif32 6.x は SDK 同梱が不完全)。本リポは既に `pioarduino/platform-espressif32` 55.03.38-1 に切替済み |
| 画面下を触っても録音にならない              | M5Unified の virtual BtnA はデフォルト無効。本リポは `M5.Touch.getDetail(0).isPressed()` を直接読む実装に置換済み |
| 起動直後に `LittleFS init failed` が画面に残る | LittleFS が空 (face JPG 未書き込み)。`pio run -t buildfs` → 0x610000 に焼き直す                |
| 30 秒おきに勝手にリセット                  | `OFFLINE_MODE=1` で焼いてるのに何か入力したか、ロングポール `/pull` が WiFi 未初期化で crash 中。`OFFLINE_MODE` が main.cpp 経路を塞ぐので最新コードで再ビルド |
| 表情遷移時に顔位置がズレる / 下端に白い線  | `firmware/tools/align_face_bottoms.py` を回して `firmware/data/` を再生成 → `buildfs` で焼き直し |

## 今のステータス

- 母艦 (Windows + WSL2 + NVIDIA GPU) 側のセットアップ完了・動作確認済み (STT / LLM / TTS パイプライン疎通)
- CoreS3 SE ファームウェア: 録音 / 表情 / サーボ / 会話 / 定期発話受信 (`/pull`) / 頭撫で連動 まで実装済
- Feetech SCS0009 シリアルサーボ ×2 (Yaw=ID1 / Pitch=ID2、UART GPIO6/7 で 1 Mbps) で首振りを実装。PWM SG90 から差し替え済
- Si12T (頭頂 I2C タッチ) を M5StackChan-BSP 経由で読み、撫で時間に応じて F_BASHFUL → F_LAUGH_EYES_CLOSED → F_SLEEPING の段階遷移 + RGB LED 12 個の脈動演出
- 顔 JPG (`firmware/data/face_*.jpg`) は `align_face_bottoms.py` で 2D 中心揃え済 (表情遷移時の位置ズレ・白ライン解消)
- 母艦は当初の Mac mini (VOICEVOX) から Windows + WSL2 + NVIDIA GPU (Irodori-TTS-Lite) に構成変更済 (VOICEVOX 経路も維持)
- Irodori-TTS-Lite は pip でインストール可能な [YosAwed/Irodori-TTS-Lite](https://github.com/YosAwed/Irodori-TTS-Lite) フォークを使用
- 定期発話 (cron) / 外部 push (Discord bot 連携など) の経路を server 側で実装済 (`SCHEDULE_ENABLED=1` で有効化)

## 既知の TODO / 注意点

### TTS (Irodori-TTS-Lite) 周り
- [x] **TTS/LLM/STT の処理時間計測**: `/chat` / `/chat_text` / `/speak` は `X-Stackchan-Timing` を返す。Irodori 経路は `/ready` の `tts.last_*` とログで推論時間・変換時間・推定秒数を確認できる
- [x] **実機ログでの切り分け補助**: CoreS3 のシリアルログに `[TIME]` / `[TTS ]` を出し、サーバ側は LLM 設定・履歴上限・音声サイズ上限を `.env` で調整可能
- [ ] **fork 側に `synthesize(text) -> waveform` を露出**: 上記が遅ければ、`irodori_tts.inference_runtime.InferenceRuntime` をシングルトン化してクリーンな関数として export。[server/tts_irodori.py](server/tts_irodori.py) の tempfile + sys.argv ブロックを直接呼び出しに置換できる
- [ ] **`IRODORI_REF_WAV` の確定**: ぺけ子ちゃん声の参照音声 WAV を用意するか、`--no-ref` (voice-design checkpoint) のまま行くか決める
- [x] **`infer` モジュール = 親パッケージ `irodori_tts` の import 確認**: [YosAwed/Irodori-TTS-Lite](https://github.com/YosAwed/Irodori-TTS-Lite) フォークが `irodori-tts` と `infer` を pip 依存として同梱済み

### 実機側
- [x] PDM マイクの WAV ヘッダとサンプリングが Whisper と整合するか確認
- [x] サーボ (首振り) のピン番号確定と Avatar との同期 (SCS0009 / UART GPIO6,7 で実装)
- [x] 口パクと再生 PCM のエンベロープ同期 (RMS + サーボ重み連動)、3 段階化済 (closed / open / wide=F_JOY)
- [x] ぺけ子ちゃん 36 表情を LittleFS に焼く方式に切替 (`pekeko_face.h` + `face_map.h`)
- [x] X68 風起動チャイム + 応答前 ack beep (2 音「ピロン♪」) + 頭撫でチャイム (`chime.h`)
- [x] HTTP エラーの粒度向上 (`413` / `5xx` / タイムアウト で表情とビープを出し分け)
- [x] 録音バッファ上限到達時の自動停止と通知 (`audio_recorder::isFull()` + overflow beep)
- [x] 定期発話 / 外部 push の受信経路 (`/pull` ロングポール)
- [x] アイドル中のまばたき (4〜8 秒間隔) + マイクロ表情 (5 種ローテーション、8〜15 秒間隔)
- [x] LCD タッチ ↔ M5.BtnA 不整合を回避 (M5.Touch を直接読む)
- [x] 頭頂 Si12T による headpat 反応 + RGB LED 12 個の連動演出 (段階的とろけ顔 + 脈動)
- [x] 顔 JPG の 2D 中心揃え (`align_face_bottoms.py`) で表情遷移時の位置ズレを解消
- [x] OFFLINE_MODE: Wi-Fi/サーバ無しで起動して表情アニメ・頭撫で・LED 演出を確認する開発用フラグ
- [x] 電源管理: 低電池監視と Idle 長時間継続時の deep sleep (`power.h`)
- [ ] ウェイクワード化 (現状は LCD タッチで push-to-talk)
- [ ] チャイムを本格的に FM 風にする (M5Unified の波形カスタマイズ or 短い PCM サンプル)
- [ ] Si12T のスワイプ方向検出 (`wasSwipedForward` / `wasSwipedBackward`) を使った「逆撫で → 困り顔」演出
- [ ] 過去の WiFi 接続中 `xQueueSemaphoreTake assert` クラッシュの根本対応 (現在は OFFLINE_MODE で迂回)
