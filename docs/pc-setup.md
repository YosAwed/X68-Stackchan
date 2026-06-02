# 母艦 PC セットアップ

このリポジトリでは、CoreS3 側は録音・表示・再生だけを担当し、音声認識、LLM、音声合成は母艦 PC 上の FastAPI サーバで動かす。

CoreS3 から見ると必要なのは `http://<母艦PCのLAN IP>:8000/chat` だけ。PC 側では `/chat` の中で次の順に処理する。

1. `faster-whisper` で録音 WAV を文字起こしする
2. `Ollama` にテキストを投げて返答文を作る
3. `TTS_BACKEND` で選んだ音声合成バックエンドから WAV を返す

## 構成を選ぶ

表中のファイルパスはリポジトリルートから見たパス。

| 母艦 PC | TTS | 使う手順 | requirements | `.env` |
|---------|-----|----------|--------------|--------|
| Windows 11 + WSL2 + NVIDIA GPU | Irodori-TTS-Lite | [setup.md](setup.md) | `server/requirements-cuda.txt` | `TTS_BACKEND=irodori` |
| Mac mini / CUDA なし | VOICEVOX | [setup-macmini.md](setup-macmini.md) | `server/requirements-macmini.txt` | `TTS_BACKEND=voicevox` |

通常は Windows + WSL2 + CUDA 構成を使う。Irodori-TTS-Lite は CUDA + Triton 前提なので、Windows ネイティブや Mac mini ではなく WSL2/Linux 側で動かす。

NVIDIA GPU がない環境では VOICEVOX 経路に切り替える。CoreS3 のファームウェアは同じでよく、PC 側の `.env` と依存だけが変わる。

## 共通の起動順

どちらの構成でも、母艦 PC 側は次の順に起動する。

1. Ollama を起動し、使うモデルを pull する
2. 必要なら TTS エンジンまたは TTS ランタイムを準備する
3. `server/.env.example` を `server/.env` にコピーして編集する
4. FastAPI サーバを `0.0.0.0:8000` で起動する
5. CoreS3 の `firmware/include/config.h` の `SERVER_HOST` を母艦 PC の LAN IP に向ける

## Windows + WSL2 + CUDA

WSL2 の Ubuntu 内で作業する。先に Windows ホスト側の NVIDIA ドライバを入れ、WSL2 から GPU が見えることを確認する。

```bash
nvidia-smi
```

システムパッケージ:

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv build-essential ffmpeg git
```

Ollama:

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama serve &          # WSL2 は systemd 無効が多いので & でバックグラウンド起動
ollama pull qwen2.5:7b
```

> WSL2 で `systemd` が無効の場合、インストール時に警告が出るが動作に支障はない。
> systemd を有効にするには `/etc/wsl.conf` に `[boot]\nsystemd=true` を追記して `wsl --shutdown` で再起動する。

別ターミナルでサーバ環境を作る。

```bash
cd server
python3.11 -m venv .venv
source .venv/bin/activate

pip install --index-url https://download.pytorch.org/whl/cu121 torch torchaudio
pip install git+https://github.com/YosAwed/Irodori-TTS-Lite.git@main
pip install -r requirements-cuda.txt

cp .env.example .env
```

`.env` の最低限の確認:

```env
TTS_BACKEND=irodori
WHISPER_DEVICE=cuda
OLLAMA_MODEL=qwen2.5:7b
OLLAMA_TIMEOUT_S=60
OLLAMA_TEMPERATURE=0.7
OLLAMA_NUM_PREDICT=200
HISTORY_TURNS=6
MAX_SESSIONS=16
MAX_AUDIO_BYTES=2097152
IRODORI_DEVICE=cuda
IRODORI_REF_WAV=
IRODORI_CHECKPOINT=
```

起動:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

起動ログに `TTS backend = irodori` と `Irodori-TTS-Lite ready` が出れば PC 側は概ね準備完了。

## Mac mini / VOICEVOX

Mac mini など CUDA がない環境では VOICEVOX 経路を使う。

VOICEVOX engine:

```bash
docker run --rm -p '50021:50021' voicevox/voicevox_engine:cpu-latest
curl http://localhost:50021/version
```

Ollama:

```bash
brew install ollama
ollama serve
ollama pull qwen2.5:7b
```

サーバ環境:

```bash
cd server
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-macmini.txt

cp .env.example .env
```

`.env` の最低限の確認:

```env
TTS_BACKEND=voicevox
WHISPER_DEVICE=auto
OLLAMA_MODEL=qwen2.5:7b
OLLAMA_TIMEOUT_S=60
OLLAMA_TEMPERATURE=0.7
OLLAMA_NUM_PREDICT=200
HISTORY_TURNS=6
MAX_SESSIONS=16
MAX_AUDIO_BYTES=2097152
VOICEVOX_HOST=http://127.0.0.1:50021
VOICEVOX_SPEAKER=3
```

起動:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

起動ログに `TTS backend = voicevox` と `VOICEVOX backend ready` が出れば OK。

## 疎通確認

まず PC 内で FastAPI が起きているか確認する。

```bash
curl http://localhost:8000/health
curl http://localhost:8000/ready
```

`/ready` の `ok` が `false` の場合は `components.llm.error` や `components.tts.error` を見る。Ollama のモデル未 pull、VOICEVOX engine 未起動などをここで切り分けられる。

次に TTS 単体、LLM+TTS、STT+LLM+TTS の順に確認する。

```bash
curl -D - -X POST -F "text=テストです" http://localhost:8000/speak --output speak.wav
curl -D - -X POST -F "text=こんにちは" http://localhost:8000/chat_text --output chat_text.wav
curl -D - -X POST -F "audio=@hello.wav" http://localhost:8000/chat --output reply.wav
```

`X-Stackchan-Timing` ヘッダに `stt` / `llm` / `tts` / `total` の処理時間が出る。Irodori 経路では `/ready` の `components.tts.last_infer_ms` でも直近の推論時間を確認できる。
CoreS3 のシリアルログにも `[TIME]` と `[TTS ]` が出る。

CoreS3 から接続するには、PC の LAN IP を確認して `firmware/include/config.h` に入れる。

> ⚠️ `server/.env` 側の `SERVER_HOST` は **uvicorn の bind アドレス** (既定 `0.0.0.0`) で、LAN 内の他マシンから到達できるようにするためのもの。一方 `firmware/include/config.h` の `SERVER_HOST` は **CoreS3 が接続する母艦の LAN IP**。同名だが別物なので混同しないこと。母艦自身からの `curl localhost:8000/...` は通っても、CoreS3 側で `localhost` を指定すると自分自身を見にいってしまうので必ず LAN IP を入れる。

Windows:

```powershell
ipconfig | findstr IPv4
```

macOS:

```bash
ipconfig getifaddr en0
```

`config.h`:

```cpp
static constexpr const char* SERVER_HOST = "192.168.1.42";
static constexpr uint16_t    SERVER_PORT = 8000;
static constexpr const char* SERVER_PATH = "/chat";
```

CoreS3 と母艦 PC は同じ LAN 上に置く。Windows ファイアウォールや macOS のファイアウォールで 8000 番が塞がれていると、CoreS3 側は HTTP エラーになる。

## 定期発話 / 外部 push (任意機能)

`/chat` の往復とは別に、サーバ起点で CoreS3 に喋らせる経路がある。`.env` に下記を追加すると有効化される。

```env
SCHEDULE_ENABLED=1
SCHEDULE_FILE=schedule.json
QUEUE_MAX_SIZE=16
ENQUEUE_TOKEN=<random-long-token>
```

`server/schedule.json.example` をコピーして `server/schedule.json` を編集すると、cron 式 + LLM プロンプト or 固定文で定期発話を仕込める。外部 (Discord bot / curl) からは `X-Stackchan-Token` 付きの `POST /enqueue` で同じキューに発話を積める。詳細とエンドポイント仕様は [architecture.md §定期発話 / 外部 push](architecture.md#定期発話--外部-push) を参照。

> 定期発話を使わない場合でも `SCHEDULE_ENABLED=0` のまま `/pull` と `/enqueue` は使える (キュー空時は 204 が返る)。

## よくある確認ポイント

| 症状 | 見るところ |
|------|------------|
| CoreS3 から HTTP エラー | `SERVER_HOST` が PC の LAN IP か、8000 番が開いているか |
| `/health` は通るが `/chat` が失敗 | `/ready` の `components` と uvicorn ログの `STT failed` / `LLM failed` / `TTS failed` を見る |
| `/chat` が `413` になる | WAV が `MAX_AUDIO_BYTES` を超えている。`MAX_REC_SECONDS` を下げるか `MAX_AUDIO_BYTES` を上げる |
| `TTS backend = irodori` で import エラー | CUDA 構成の依存が足りない。Mac mini なら `TTS_BACKEND=voicevox` にする |
| `CUDA out of memory` | `WHISPER_MODEL` を下げる、Ollama モデルを軽くする、他の GPU プロセスを止める |
| VOICEVOX で音が出ない | `curl http://localhost:50021/version` と `VOICEVOX_HOST` を確認する |
| Ollama につながらない | `ollama serve` が起動中か、`OLLAMA_MODEL` が pull 済みか確認する |
