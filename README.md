# X68-Stackchan — ぺけ子ちゃん版 スタックちゃんカスタムファーム

M5Stack 公式スタックちゃん (CoreS3 SE) を、X68000 擬人化キャラ **ぺけ子ちゃん** にしてしまうカスタムファーム。
推論・音声認識・音声合成はすべて母艦の **Windows + WSL2 (NVIDIA CUDA)** 上で走らせる、**完全ローカルの AI 会話エージェント**。クラウド API は使わない。

> TTS に [Irodori-TTS-Lite](https://github.com/kizuna-intelligence/Irodori-TTS-Lite) を採用している関係で、母艦は CUDA 必須 (Ampere 以降推奨)。当初想定の Mac mini からは構成変更している。
>
> Mac mini など NVIDIA GPU が無い母艦で動かしたい場合は、VOICEVOX 経路の代替セットアップ [docs/setup-macmini.md](docs/setup-macmini.md) を用意してある。`.env` で `TTS_BACKEND=voicevox` に切り替えるだけ。

起動時に Human68k 風のスプラッシュが流れて、ぺけ子ちゃんの Avatar が立ち上がる、というのが完成形のイメージ。
ぺけ子ちゃんのアートワーク自体は同人由来なのでリポには同梱せず、`firmware/include/pekeko_theme.h` のカラーパレットと、好みの顔絵差し替えポイントだけを公開している。

## 構成

```
┌─────────────────────────────┐         Wi-Fi (HTTP)          ┌────────────────────────────┐
│  M5Stack CoreS3 SE          │  ──── multipart/form-data ─► │  Win + WSL2 (母艦/CUDA)     │
│  ─ M5Unified + Avatar       │     録音 WAV (16k mono)       │  FastAPI /chat              │
│  ─ 内蔵 PDM マイクで録音    │                               │   1. faster-whisper (STT)   │
│  ─ ボタンで push-to-talk    │                               │   2. Ollama (ローカル LLM)  │
│  ─ 内蔵スピーカで再生       │  ◄──── audio/wav (応答) ─────│   3. Irodori-TTS-Lite (TTS) │
│  ─ 口パク同期で Avatar 表情 │                               │      ※ in-process (CUDA)   │
└─────────────────────────────┘                               └────────────────────────────┘
```

## ディレクトリ

```
.
├── README.md                  # このファイル
├── docs/
│   ├── architecture.md        # 詳細アーキテクチャ
│   ├── setup.md               # セットアップ手順 (CUDA / WSL2 / Irodori)
│   └── setup-macmini.md       # 代替セットアップ (Mac mini / VOICEVOX)
├── firmware/                  # CoreS3 側 (PlatformIO + Arduino)
│   ├── platformio.ini
│   ├── partitions_pekeko.csv  # OTA無し / LittleFS 10MB
│   ├── src/main.cpp
│   ├── include/
│   │   ├── config.h.example   # Wi-Fi / サーバ IP / ピン定義など
│   │   ├── audio_recorder.h   # 内蔵 PDM マイクの WAV 録音
│   │   ├── http_client.h      # multipart で /chat に POST
│   │   ├── avatar_state.h     # 状態 enum
│   │   ├── pekeko_theme.h     # X68000 風スプラッシュ
│   │   ├── pekeko_face.h      # LittleFS から face_NN.jpg を描く
│   │   ├── face_map.h         # シーン→表情番号 (ここを書き換えて配役変更)
│   │   ├── chime.h            # 起動チャイム / ack beep / error beep
│   │   └── servo_motion.h     # 首振りサーボ (SG90 ×2) を State と同期
│   ├── tools/slice.py         # 4 枚のスプライトシートを 36 個に分割
│   ├── assets/raw/            # 元シート画像置き場 (LittleFS には焼かない)
│   └── data/                  # LittleFS イメージ (pio run -t uploadfs で焼く)
│       └── face_01.jpg .. face_36.jpg
└── server/                    # 母艦側 (Python / FastAPI)
    ├── requirements-cuda.txt    # WSL2 + CUDA 版 (Irodori-TTS-Lite)
    ├── requirements-macmini.txt # Mac mini 版 (VOICEVOX)
    ├── main.py                # /chat エンドポイント
    ├── stt.py                 # faster-whisper ラッパ
    ├── llm.py                 # Ollama クライアント
    ├── tts.py                 # backend dispatcher (TTS_BACKEND env で切替)
    ├── tts_irodori.py         # Irodori-TTS-Lite (in-process / CUDA)
    ├── tts_voicevox.py        # VOICEVOX HTTP (Mac mini など)
    ├── persona.py             # スタックちゃんのキャラ付け system prompt
    └── .env.example           # ホスト・ポート・モデル名・参照音声などの設定
```

## 今のステータス

- ハードウェア (CoreS3 SE / サーボ等) を発注済み・到着待ち
- このリポジトリには実機到着前に書ける範囲 (両端のスケルトンと配線/ビルド手順) が入っている
- 母艦は当初の Mac mini (VOICEVOX) から Windows + WSL2 + NVIDIA GPU (Irodori-TTS-Lite) に構成変更済み
- 実機が来たら `docs/setup.md` の手順 1 → 2 → 3 で順に通すだけで火を入れられる状態を目標にする

## 既知の TODO / 注意点

### TTS (Irodori-TTS-Lite) 周り
- [ ] **モデルロード回数の確認**: 現在の [server/tts.py](server/tts.py) は upstream の `example/run_tts.py` を in-process 再現 (sys.argv を組んで `infer.main()` を呼ぶ) しているので、`/chat` のたびに `InferenceRuntime` が再構築されると秒オーダーで遅くなる。最初の起動と 2 回目の `/chat` で所要時間を計測して切り分け
- [ ] **fork 側に `synthesize(text) -> waveform` を露出**: 上記が遅ければ、`irodori_tts.inference_runtime.InferenceRuntime` をシングルトン化してクリーンな関数として export。[server/tts.py](server/tts.py) の tempfile + sys.argv ブロック (約 20 行) を直接呼び出しに置換できる
- [ ] **`IRODORI_REF_WAV` の確定**: ぺけ子ちゃん声の参照音声 WAV を用意するか、`--no-ref` (voice-design checkpoint) のまま行くか決める
- [ ] **`infer` モジュール = 親パッケージ `irodori_tts` の import 確認**: fork が transitively pull してくれるか、別途 `pip install` が必要か

### 実機到着後
- [ ] PDM マイクの WAV ヘッダとサンプリングが Whisper と整合するか確認
- [x] サーボ (首振り) を State 遷移に同期 (`servo_motion.h`)。ピン番号 (`SERVO_YAW_PIN` / `SERVO_PITCH_PIN`) は実機の配線で確定する
- [ ] 口パクと再生 PCM のエンベロープ同期
- [ ] ウェイクワード化 (現状は push-to-talk)
- [x] ぺけ子ちゃん 36 表情を LittleFS に焼く方式に切替 (`pekeko_face.h` + `face_map.h`)
- [x] PCM RMS による 2 フレーム口パク
- [x] X68 風起動チャイム + 応答前 ack beep (`chime.h`)
- [ ] チャイムを本格的に FM 風にする (M5Unified の波形カスタマイズ or 短いPCMサンプル)
