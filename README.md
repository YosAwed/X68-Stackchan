# X68-Stackchan — ぺけ子ちゃん版 スタックちゃんカスタムファーム

M5Stack 公式スタックちゃん (CoreS3 SE) を、X68000 擬人化キャラ **ぺけ子ちゃん** にしてしまうカスタムファーム。
推論・音声認識・音声合成はすべて母艦 PC/Mac 上で走らせる、**完全ローカルの AI 会話エージェント**。クラウド API は使わない。

> **CoreS3 単体では完結しません。** 録音・再生・表示・サーボは CoreS3、STT (faster-whisper) / LLM (Ollama) / TTS (Irodori-TTS-Lite または VOICEVOX) は母艦 PC/Mac 上の FastAPI サーバが担当する分担構成です。Wi-Fi LAN 越しに `multipart/form-data` で繋がります。母艦が無いと CoreS3 はスプラッシュと顔画像だけが表示される状態になります。

### どの手順を読むか

母艦の構成は 2 通り用意してあり、`.env` の `TTS_BACKEND` 切替で同じ CoreS3 ファームから使い分けられる。

| 自分の母艦                       | TTS バックエンド     | 読むセットアップ                                  |
|---------------------------------|---------------------|--------------------------------------------------|
| Windows 11 + WSL2 + NVIDIA GPU  | Irodori-TTS-Lite    | [docs/setup.md](docs/setup.md)                   |
| Mac mini / CUDA なしの PC       | VOICEVOX (HTTP)     | [docs/setup-macmini.md](docs/setup-macmini.md)   |
| まず母艦側だけ準備したい        | (どちらでも)         | [docs/pc-setup.md](docs/pc-setup.md)             |
| 内部設計・API 仕様              | —                   | [docs/architecture.md](docs/architecture.md)     |

> TTS に [Irodori-TTS-Lite](https://github.com/YosAwed/Irodori-TTS-Lite) (upstream: [kizuna-intelligence/Irodori-TTS-Lite](https://github.com/kizuna-intelligence/Irodori-TTS-Lite) の pip 対応フォーク) を採用している関係で、Irodori 経路は CUDA 必須 (Ampere 以降推奨)。NVIDIA GPU が無い場合は VOICEVOX 経路に切替。

起動時に Human68k 風のスプラッシュが流れて、ぺけ子ちゃんの Avatar が立ち上がる、というのが完成形のイメージ。
分割済みの 36 表情 (`firmware/data/face_01.jpg` 〜 `face_36.jpg`) はリポに同梱しており、`pio run -t uploadfs` でそのまま LittleFS に焼ける。元になったスプライトシート画像は同人由来のため `firmware/assets/raw/` には commit せず、`.gitignore` で除外している (再生成したい場合のみ自分で配置して `firmware/tools/slice.py` を回す)。スプラッシュ画面の色は `firmware/include/pekeko_theme.h`、シーンごとの表情割当は `firmware/include/face_map.h` で差し替えられる。

### 最小確認手順 (段階的に切り分け)

初めて触るときは下の順に確かめると詰まりにくい (詳細は [docs/setup.md §3](docs/setup.md#3-動作確認-段階的に切り分ける) に同じ内容で展開)。

1. **顔画像表示の確認** — 母艦サーバ無しで CoreS3 だけ電源 ON。スプラッシュと face_01 が出れば、ファーム本体 + LittleFS の表情画像 + サーボは OK
2. **母艦サーバへの到達確認** — uvicorn を起こした状態でシリアルログに `WiFi connected` と `/pull` の応答が出ることを見る。母艦から `X-Stackchan-Token` 付きで `POST /enqueue` を投げれば CoreS3 が喋る
3. **会話 (push-to-talk)** — ボタン長押し → 録音 → STT → LLM → TTS が往復する

## 構成

```
┌─────────────────────────────┐         Wi-Fi (HTTP)          ┌────────────────────────────┐
│  M5Stack CoreS3 SE          │  ──── multipart/form-data ─► │  母艦 PC / Mac              │
│  ─ M5Unified + Avatar       │     録音 WAV (16k mono)       │  FastAPI                    │
│  ─ 内蔵 PDM マイクで録音    │                               │   /chat        (会話)       │
│  ─ ボタンで push-to-talk    │                               │   /pull        (定期発話)   │
│  ─ 内蔵スピーカで再生       │  ◄──── audio/wav (応答) ─────│   /enqueue     (外部 push)  │
│  ─ 口パク同期で Avatar 表情 │                               │                             │
│  ─ サーボ (Yaw/Pitch) で首振り │                            │   1. faster-whisper (STT)   │
│                             │  ──── GET /pull?wait=0 ────► │   2. Ollama (LLM)           │
│  Idle 中に 30 秒おき long-poll │ ◄──── audio/wav (発話) ──── │   3. Irodori or VOICEVOX    │
└─────────────────────────────┘                               │   4. scheduler (任意)       │
                                                              │      croniter で定時発話     │
                                                              └────────────────────────────┘
```

- 通常会話は CoreS3 → 母艦の **要求-応答** (`POST /chat`)
- 定期発話 / 外部 push は母艦 → CoreS3 を **long-poll** で実現 (`GET /pull`、CoreS3 が Idle 中に短ポーリング)
- 外部システム (Discord bot / curl など) からは `X-Stackchan-Token` 付きの `POST /enqueue` で同じキューに発話を積める

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
│   ├── platformio.ini
│   ├── partitions_pekeko.csv  # OTA無し / LittleFS 10MB
│   ├── src/main.cpp
│   ├── include/
│   │   ├── config.h.example   # Wi-Fi / 母艦 LAN IP / サーボ / タイムアウト
│   │   ├── audio_recorder.h   # 内蔵 PDM マイク録音 + overflow 自動停止
│   │   ├── http_client.h      # multipart で /chat に POST、GET /pull で long-poll
│   │   ├── servo_controller.h # Feetech SCS0009 シリアルサーボ ×2 (Yaw/Pitch)
│   │   ├── avatar_state.h     # 状態 enum
│   │   ├── pekeko_theme.h     # X68000 風スプラッシュ
│   │   ├── pekeko_face.h      # LittleFS から face_NN.jpg を描く
│   │   ├── face_map.h         # シーン→表情番号 (ここを書き換えて配役変更)
│   │   └── chime.h            # 起動 / ack / overflow / 各種エラービープ
│   ├── tools/slice.py         # 4 枚のスプライトシートを 36 個に分割
│   ├── assets/raw/            # 元シート画像置き場 (LittleFS には焼かない)
│   └── data/                  # LittleFS イメージ (pio run -t uploadfs で焼く)
│       └── face_01.jpg .. face_36.jpg
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

## 今のステータス

- 母艦 (Windows + WSL2 + NVIDIA GPU) 側のセットアップ完了・動作確認済み (STT / LLM / TTS パイプライン疎通)
- CoreS3 SE ファームウェア: 録音 / 表情 / サーボ / 会話 / 定期発話受信 (`/pull`) まで実装済
- Feetech SCS0009 シリアルサーボ ×2 (Yaw=ID1 / Pitch=ID2、UART GPIO6/7 で 1 Mbps) で首振りを実装。PWM SG90 から差し替え済
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

### 実機到着後
- [x] PDM マイクの WAV ヘッダとサンプリングが Whisper と整合するか確認
- [x] サーボ (首振り) のピン番号確定と Avatar との同期 (SCS0009 / UART GPIO6,7 で実装)
- [x] 口パクと再生 PCM のエンベロープ同期 (RMS + サーボ重み連動)
- [ ] ウェイクワード化 (現状は push-to-talk)
- [x] ぺけ子ちゃん 36 表情を LittleFS に焼く方式に切替 (`pekeko_face.h` + `face_map.h`)
- [x] PCM RMS による 2 フレーム口パク
- [x] X68 風起動チャイム + 応答前 ack beep (`chime.h`)
- [x] HTTP エラーの粒度向上 (`413` / `5xx` / タイムアウト で表情とビープを出し分け)
- [x] 録音バッファ上限到達時の自動停止と通知 (`audio_recorder::isFull()` + overflow beep)
- [x] 定期発話 / 外部 push の受信経路 (`/pull` ロングポール)
- [ ] チャイムを本格的に FM 風にする (M5Unified の波形カスタマイズ or 短いPCMサンプル)
