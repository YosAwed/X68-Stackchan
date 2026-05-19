# セットアップ手順

## 0. 用意するもの

- M5Stack CoreS3 SE 本体 + USB-C ケーブル
- スタックちゃん Takao Base (組立済 or キット)
- サーボ SG90 ×2
- Windows 11 + WSL2 (Ubuntu 22.04 以降) + NVIDIA GPU (Ampere 以降, VRAM 8GB 以上推奨)
- 同一 Wi-Fi LAN (CoreS3 から母艦に IP で届くこと)

## 1. 母艦 (Windows + WSL2) 側のセットアップ

### 1-0. WSL2 + CUDA の前提

Windows ホスト側に最新の NVIDIA ドライバを入れ、WSL2 (Ubuntu) を起動して以下が通ることを確認する。

```bash
nvidia-smi              # ホストの GPU が見える
```

WSL2 内で必要なシステムパッケージ:

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv build-essential ffmpeg
```

### 1-1. Irodori-TTS-Lite の準備

[Irodori-TTS-Lite](https://github.com/kizuna-intelligence/Irodori-TTS-Lite) は CUDA + Triton 前提のため WSL2 (Linux) で動かす。改造済みの fork を `pip install` する形にしてある (`server/tts.py` は in-process で import する)。

```bash
# まず CUDA 対応 torch を先に入れる (cu121 の例。GPU 世代に合わせて調整)
pip install --index-url https://download.pytorch.org/whl/cu121 torch torchaudio

# 改造済み Irodori-TTS-Lite を入れる (自分の fork URL に置換)
pip install git+https://github.com/<YOU>/Irodori-TTS-Lite.git@main

# 親パッケージ (推論ランタイム本体): fork の依存に入っていなければ手動で。
# パッケージ名は upstream の README / pyproject.toml を確認すること。
# pip install irodori-tts        # 例: PyPI にある場合
# pip install pyopenjtalk         # phoneme 長から秒数推定に使う
```

初回 import 時に Hugging Face (`kizuna-intelligence/Irodori-TTS-Lite-int4`) から weights が落ちてくる。事前に試したい場合は upstream の `example/run_tts.py --no-ref --text "テスト" --output-wav /tmp/t.wav` で動作確認しておくと吉。

> **性能上の注意**: 現在の [server/tts.py](../server/tts.py) は upstream の CLI shape (`infer.main()` を sys.argv 経由で叩く) をそのまま in-process で再現しているので、`/chat` のたびに `InferenceRuntime` が再構築されるとモデルロードが入って秒オーダーで遅くなる可能性がある。fork 側で `InferenceRuntime` をシングルトン化して `synthesize(text) -> waveform` を露出させた上で、`server/tts.py` の該当部分を直接呼び出しに差し替えるのが本筋。

### 1-2. Ollama を起動 (WSL2 内)

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama serve            # 11434 で待ち受け (バックグラウンドで起動)
ollama pull qwen2.5:7b  # 日本語の出が素直なものを推奨
```

### 1-3. このリポジトリの server/ を立ち上げる

```bash
cd server
python3.11 -m venv .venv
source .venv/bin/activate

# torch / Irodori-TTS-Lite は 1-1 で先に入れてあるのでここは追加分だけ
pip install -r requirements.txt

cp .env.example .env
# .env を編集 (OLLAMA_MODEL / WHISPER_MODEL / IRODORI_REF_WAV など)

uvicorn main:app --host 0.0.0.0 --port 8000
```

初回起動で Irodori-TTS-Lite の weights ロードに数秒〜十秒程度かかる。`Irodori-TTS-Lite ready` のログが出れば準備完了。

別端末から疎通確認:

```bash
# 適当な短い WAV (16k mono) を投げる
curl -X POST -F "audio=@hello.wav" http://localhost:8000/chat --output reply.wav
```

`reply.wav` がぺけ子ちゃんの声になっていれば成功。

### 1-4. 母艦の IP を控える

CoreS3 からは Windows ホストの LAN IP に届けばよい (WSL2 への port forward は WSL2 が透過的に面倒を見る)。

```powershell
# Windows 側 PowerShell
ipconfig | findstr IPv4    # 例: 192.168.1.42
```

## 2. CoreS3 側のセットアップ

### 2-1. PlatformIO 環境

VSCode + PlatformIO 拡張を入れた状態で:

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

## 3. 動作確認

1. 電源投入 → Human68k 風スプラッシュ + 起動チャイム → ぺけ子ちゃんが手を振る (face_36)
2. 通常待機 (face_01 中立) に戻る
3. 画面下中央 (BtnA 領域) をタッチで押し続けると「？」顔 (face_15) に変わって録音開始
4. 離すと「考え中」顔 (face_21, 手を顎) → 数秒後 "ピッ" の後にぺけ子ちゃんが返事を喋る
5. 喋っている間 face_02 (口閉) / face_29 (口開) を PCM 振幅で切り替えて口パク
6. エラー時はあわてた表情 + 下降ビープ

## 4. トラブルシュート

| 症状 | 切り分け |
|------|----------|
| `WiFi connected` が出ない | `config.h` の SSID/PASS、2.4GHz 帯か |
| 録音できているが応答が無音 | 母艦側の uvicorn ログを確認。Whisper でテキスト化されているか |
| 応答テキストは出るのに音が出ない | `Irodori-TTS-Lite ready` ログが出ているか / `nvidia-smi` で VRAM が足りているか |
| `CUDA out of memory` | `WHISPER_MODEL` を下げる、`IRODORI_FORCE_FP16=1` を維持、他の GPU プロセスを落とす |
| `Triton` 関連エラー | WSL2 / Linux で実行しているか確認。Windows ネイティブだと Triton が動かない |
| 早口/雑音で誤認識 | `WHISPER_MODEL` を `large-v3` に上げる、もしくは録音ゲインを下げる |
| `LittleFS init failed` と画面に出る | `pio run -t uploadfs` が済んでいない、もしくはパーティションが古い |
| 口パクのテンポが合わない | `main.cpp` の `RMS_THRESH` (既定 2200) を上下する |
