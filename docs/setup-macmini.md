# セットアップ手順 (Mac mini + VOICEVOX 代替構成)

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

### 1-1. VOICEVOX engine を起動

GUI 版 VOICEVOX をインストールするか、`voicevox_engine` Docker イメージを起動:

```bash
docker run --rm -p '50021:50021' voicevox/voicevox_engine:cpu-latest
```

`curl http://localhost:50021/version` が JSON を返せば OK。

### 1-2. Ollama を起動

```bash
brew install ollama
ollama serve            # 11434 で待ち受け
ollama pull qwen2.5:7b  # 日本語の素直さで qwen2.5 か gemma2 系がおすすめ
```

### 1-3. このリポジトリの server/ を立ち上げる

VOICEVOX 経路では torch / Irodori 系の依存は不要。`requirements-macmini.txt` を使う。

```bash
cd server
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-macmini.txt

cp .env.example .env
# .env を編集:
#   TTS_BACKEND=voicevox       # ★必須: 既定は irodori なので明示的に切替
#   WHISPER_DEVICE=auto        # Apple Silicon は auto / cpu
#   VOICEVOX_SPEAKER=3         # 好みの話者
#   OLLAMA_MODEL=qwen2.5:7b
#   OLLAMA_TEMPERATURE=0.7
#   OLLAMA_NUM_PREDICT=200
#   MAX_SESSIONS=16
#   MAX_AUDIO_BYTES=2097152

uvicorn main:app --host 0.0.0.0 --port 8000
```

起動ログに `TTS backend = voicevox` と `VOICEVOX backend ready (...)` が並べば成功。

別端末から疎通確認:

```bash
curl http://localhost:8000/ready
curl -D - -X POST -F "text=テストです" http://localhost:8000/speak --output speak.wav
curl -D - -X POST -F "text=こんにちは" http://localhost:8000/chat_text --output chat_text.wav
curl -D - -X POST -F "audio=@hello.wav" http://localhost:8000/chat --output reply.wav
```

`reply.wav` がスタックちゃんの声になっていれば OK。`X-Stackchan-Timing` で LLM / TTS / total の処理時間を確認できる。

### 1-4. Mac mini の IP を控える

```bash
ipconfig getifaddr en0   # 例: 192.168.1.42
```

## 2. CoreS3 側のセットアップ

[setup.md のセクション 2](setup.md#2-cores3-側のセットアップ) と完全に同じ手順。`SERVER_HOST` を Mac mini の IP に向けるだけ。

## 3. 動作確認

[setup.md のセクション 3](setup.md#3-動作確認-段階的に切り分ける) と同じ。CoreS3 単体 → サーバ疎通 → 会話、の順に切り分けるとよい。

## 4. トラブルシュート

| 症状 | 切り分け |
|------|----------|
| `WiFi connected` が出ない | `config.h` の SSID/PASS、2.4GHz 帯か |
| 録音できているが応答が無音 | Mac mini 側の uvicorn ログを確認。Whisper でテキスト化されているか |
| `/chat` が `413` になる | 録音 WAV が `MAX_AUDIO_BYTES` を超えている。`MAX_REC_SECONDS` を下げるか `MAX_AUDIO_BYTES` を上げる |
| 起動ログが `TTS backend = irodori` になる | `.env` の `TTS_BACKEND=voicevox` が読まれていない (`.env` の位置 / 改行コード確認) |
| `tts_irodori` の import エラーが出る | `TTS_BACKEND=irodori` のまま起動している。`.env` を見直す |
| 応答テキストは出るのに音が出ない | VOICEVOX engine の port 50021 が開いているか / `VOICEVOX_HOST` が正しいか |
| 早口/雑音で誤認識 | `WHISPER_MODEL` を `medium` か `large-v3` に上げる、もしくは録音ゲインを下げる |
| `LittleFS init failed` と画面に出る | `pio run -t uploadfs` が済んでいない、もしくはパーティションが古い |
| 口パクのテンポが合わない | `main.cpp` の `RMS_THRESH` (既定 2200) を上下する |
