# examples/

母艦サーバの外部 API (`/enqueue` / `/scheduler/status`) を実際に叩くサンプル。

| ファイル | 用途 |
|---|---|
| `discord_bot.py` | Discord スラッシュコマンドから母艦にテキストを push して CoreS3 に喋らせる |
| `.env.discord.example` | bot 用環境変数のテンプレート |

## discord_bot.py

スラッシュコマンド 3 つ:

| コマンド | 動作 |
|---|---|
| `/say <text>` | text をそのまま TTS してキューに積む |
| `/say-llm <prompt>` | prompt を LLM に通して応答を喋らせる |
| `/stackchan-status` | スケジューラの状態 (cron トリガと発火履歴) を表示 |

### セットアップ

```bash
pip install discord.py httpx python-dotenv
cp examples/.env.discord.example examples/.env.discord
# DISCORD_TOKEN と STACKCHAN_URL を埋める
python examples/discord_bot.py
```

母艦の uvicorn が `0.0.0.0:8000` で起きていれば、bot は LAN 越しに叩ける (Discord 側は外部経由なので bot ホストの位置は不問)。
