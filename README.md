# X68-Stackchan — ぺけ子ちゃん版 スタックちゃんカスタムファーム

M5Stack 公式スタックちゃん (CoreS3 SE) を、X68000 擬人化キャラ **ぺけ子ちゃん** にしてしまうカスタムファーム。
推論・音声認識・音声合成はすべて母艦の **Mac mini** 上で走らせる、**完全ローカルの AI 会話エージェント**。クラウド API は使わない。

起動時に Human68k 風のスプラッシュが流れて、ぺけ子ちゃんの Avatar が立ち上がる、というのが完成形のイメージ。
ぺけ子ちゃんのアートワーク自体は同人由来なのでリポには同梱せず、`firmware/include/pekeko_theme.h` のカラーパレットと、好みの顔絵差し替えポイントだけを公開している。

## 構成

```
┌─────────────────────────────┐         Wi-Fi (HTTP)          ┌────────────────────────────┐
│  M5Stack CoreS3 SE          │  ──── multipart/form-data ─► │  Mac mini (母艦)            │
│  ─ M5Unified + Avatar       │     録音 WAV (16k mono)       │  FastAPI /chat              │
│  ─ 内蔵 PDM マイクで録音    │                               │   1. faster-whisper (STT)   │
│  ─ ボタンで push-to-talk    │                               │   2. Ollama (ローカル LLM)  │
│  ─ 内蔵スピーカで再生       │  ◄──── audio/wav (応答) ─────│   3. VOICEVOX (TTS)         │
│  ─ 口パク同期で Avatar 表情 │                               │                            │
└─────────────────────────────┘                               └────────────────────────────┘
```

## ディレクトリ

```
.
├── README.md                  # このファイル
├── docs/
│   ├── architecture.md        # 詳細アーキテクチャ
│   └── setup.md               # セットアップ手順
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
│   │   └── chime.h            # 起動チャイム / ack beep / error beep
│   ├── tools/slice.py         # 4 枚のスプライトシートを 36 個に分割
│   └── data/                  # LittleFS イメージ (pio run -t uploadfs で焼く)
│       ├── face_01.jpg .. face_36.jpg
│       └── raw/               # 元シート画像 (LittleFS には不要なら別フォルダへ)
└── server/                    # Mac mini 側 (Python / FastAPI)
    ├── requirements.txt
    ├── main.py                # /chat エンドポイント
    ├── stt.py                 # faster-whisper ラッパ
    ├── llm.py                 # Ollama クライアント
    ├── tts.py                 # VOICEVOX クライアント
    ├── persona.py             # スタックちゃんのキャラ付け system prompt
    └── .env.example           # ホスト・ポート・モデル名・話者IDなど
```

## 今のステータス

- ハードウェア (CoreS3 SE / サーボ等) を発注済み・到着待ち
- このリポジトリには実機到着前に書ける範囲 (両端のスケルトンと配線/ビルド手順) が入っている
- 実機が来たら `docs/setup.md` の手順 1 → 2 → 3 で順に通すだけで火を入れられる状態を目標にする

## TODO (実機到着後)

- [ ] PDM マイクの WAV ヘッダとサンプリングが Whisper と整合するか確認
- [ ] サーボ (首振り) のピン番号確定と Avatar との同期
- [ ] 口パクと再生 PCM のエンベロープ同期
- [ ] ウェイクワード化 (現状は push-to-talk)
- [x] ぺけ子ちゃん 36 表情を LittleFS に焼く方式に切替 (`pekeko_face.h` + `face_map.h`)
- [x] PCM RMS による 2 フレーム口パク
- [x] X68 風起動チャイム + 応答前 ack beep (`chime.h`)
- [ ] チャイムを本格的に FM 風にする (M5Unified の波形カスタマイズ or 短いPCMサンプル)
- [ ] ウェイクワード化 (現状は push-to-talk)
- [ ] サーボ (首振り) のピン番号確定と表情と同期
