"""Discord bot から母艦の /enqueue を叩いて CoreS3 に喋らせる最小例。

サーバ側で SCHEDULE_ENABLED の値に関係なく /enqueue は使えるので、
スケジューラを止めたままでも Discord からの push 専用に使える。

セットアップ:
    pip install discord.py httpx python-dotenv

    cp examples/.env.discord.example examples/.env.discord
    # examples/.env.discord に下記を埋める:
    #   DISCORD_TOKEN      ... bot トークン
    #   DISCORD_GUILD_ID   ... 反映を即時化したい guild の ID (省略可)
    #   STACKCHAN_URL      ... http://192.168.1.42:8000 など、母艦の URL
    #   STACKCHAN_ENQUEUE_TOKEN ... 母艦 server/.env の ENQUEUE_TOKEN と同じ値

実行:
    python examples/discord_bot.py

使い方:
    Discord 上で /say テキスト を実行すると、母艦経由で CoreS3 が喋る。
    /say-llm テキスト で LLM 経由 (応答を生成して喋る) になる。

注意:
    - 母艦と Discord bot が同じ LAN にいる必要は無い (HTTP で叩くだけ)。
    - 母艦の uvicorn を 0.0.0.0:8000 で起こしているなら、bot ホストは
      LAN 越しに接続できる。外部公開する場合は逆プロキシ + 認証を入れる。
    - 文字列は 1 メッセージあたり 200 字以内くらいに収めるとよい
      (CoreS3 側の発話再生時間と合わせて UX 良い)。
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

import httpx

# discord.py は重いので、未インストールでもファイル自体が読めるように
try:
    import discord
    from discord import app_commands
except ImportError:
    sys.stderr.write(
        "discord.py がインストールされていません。\n"
        "  pip install discord.py httpx python-dotenv\n"
    )
    raise

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*a, **kw):  # type: ignore[no-redef]
        return False


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("stackchan-bot")

ENV_PATH = Path(__file__).parent / ".env.discord"
load_dotenv(ENV_PATH)

DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
STACKCHAN_URL = os.getenv("STACKCHAN_URL", "http://192.168.1.42:8000").rstrip("/")
STACKCHAN_ENQUEUE_TOKEN = os.getenv("STACKCHAN_ENQUEUE_TOKEN", "")
GUILD_ID = os.getenv("DISCORD_GUILD_ID")  # 省略可。あればコマンド即時反映
HTTP_TIMEOUT_S = float(os.getenv("STACKCHAN_TIMEOUT_S", "60"))


intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


async def _enqueue(text: str, via_llm: bool, sid: str) -> dict:
    """母艦の /enqueue を叩いて結果を返す。"""
    if not STACKCHAN_ENQUEUE_TOKEN:
        raise RuntimeError("STACKCHAN_ENQUEUE_TOKEN is required")
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S) as cli:
        r = await cli.post(
            f"{STACKCHAN_URL}/enqueue",
            data={"text": text, "via_llm": str(via_llm).lower(), "sid": sid},
            headers={"X-Stackchan-Token": STACKCHAN_ENQUEUE_TOKEN},
        )
        r.raise_for_status()
        return r.json()


@tree.command(name="say", description="ぺけ子ちゃんにそのまま喋らせる")
@app_commands.describe(text="読み上げるテキスト")
async def say(interaction: discord.Interaction, text: str):
    await interaction.response.defer(thinking=True, ephemeral=True)
    try:
        result = await _enqueue(text, via_llm=False, sid=f"discord:{interaction.user.id}")
    except Exception as e:
        log.exception("/enqueue failed")
        await interaction.followup.send(f"❌ 母艦への push に失敗: {e}", ephemeral=True)
        return
    await interaction.followup.send(
        f"✅ キューに積みました (size={result.get('queue_size', '?')}): {result.get('bot_text', text)}",
        ephemeral=True,
    )


@tree.command(name="say-llm", description="プロンプトを LLM に通してから喋らせる")
@app_commands.describe(prompt="LLM へのプロンプト (応答が読み上げられる)")
async def say_llm(interaction: discord.Interaction, prompt: str):
    await interaction.response.defer(thinking=True, ephemeral=True)
    try:
        result = await _enqueue(prompt, via_llm=True, sid=f"discord:{interaction.user.id}")
    except Exception as e:
        log.exception("/enqueue (llm) failed")
        await interaction.followup.send(f"❌ 母艦への push に失敗: {e}", ephemeral=True)
        return
    await interaction.followup.send(
        f"✅ LLM 応答をキューに積みました: {result.get('bot_text', '(empty)')}",
        ephemeral=True,
    )


@tree.command(name="stackchan-status", description="スケジューラの状態を確認")
async def status(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        async with httpx.AsyncClient(timeout=10) as cli:
            r = await cli.get(f"{STACKCHAN_URL}/scheduler/status")
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        await interaction.followup.send(f"❌ status 取得失敗: {e}", ephemeral=True)
        return
    if not data.get("enabled"):
        await interaction.followup.send("スケジューラは無効 (SCHEDULE_ENABLED=0)", ephemeral=True)
        return
    lines = [f"running={data.get('running')}  queue_size={data.get('queue_size')}"]
    for t in data.get("triggers", []):
        last = t.get("last_fire") or "(no fire yet)"
        err = f"  ⚠️ {t['last_error']}" if t.get("last_error") else ""
        lines.append(
            f"• {t['name']}  cron=`{t['cron']}`  next={t['next']}  fired={t['fire_count']}  last={last}{err}"
        )
    await interaction.followup.send("\n".join(lines), ephemeral=True)


@client.event
async def on_ready():
    if GUILD_ID:
        guild = discord.Object(id=int(GUILD_ID))
        await tree.sync(guild=guild)
        log.info("Synced commands to guild %s", GUILD_ID)
    else:
        await tree.sync()
        log.info("Synced commands globally (反映は最大 1 時間かかる)")
    log.info("Logged in as %s (id=%s)", client.user, getattr(client.user, "id", None))


def main():
    client.run(DISCORD_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
