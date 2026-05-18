# セットアップ手順

## 0. 用意するもの

- M5Stack CoreS3 SE 本体 + USB-C ケーブル
- スタックちゃん Takao Base (組立済 or キット)
- サーボ SG90 ×2
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

```bash
cd server
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# .env を編集 (OLLAMA_MODEL / VOICEVOX_SPEAKER / WHISPER_MODEL)

uvicorn main:app --host 0.0.0.0 --port 8000
```

別端末から疎通確認:

```bash
# 適当な短い WAV (16k mono) を投げる
curl -X POST -F "audio=@hello.wav" http://localhost:8000/chat --output reply.wav
```

`reply.wav` がスタックちゃんの声になっていれば成功。

### 1-4. Mac mini の IP を控える

```bash
ipconfig getifaddr en0   # 例: 192.168.1.42
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

1. 36 表情のスプライトシート 4 枚を `firmware/data/raw/` に置く
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
| 録音できているが応答が無音 | Mac mini 側の uvicorn ログを確認。Whisper でテキスト化されているか |
| 応答テキストは出るのに音が出ない | VOICEVOX engine の port 50021 が開いているか |
| 早口/雑音で誤認識 | `WHISPER_MODEL` を `large-v3` に上げる、もしくは録音ゲインを下げる |
| `LittleFS init failed` と画面に出る | `pio run -t uploadfs` が済んでいない、もしくはパーティションが古い |
| 口パクのテンポが合わない | `main.cpp` の `RMS_THRESH` (既定 2200) を上下する |
