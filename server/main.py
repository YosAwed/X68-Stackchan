"""Stack-chan 母艦サーバ (FastAPI)

エンドポイント:
    GET /ready
        STT / LLM / TTS の準備状態とバックエンド情報を返す。
    POST /chat
        multipart/form-data:
            audio: WAV (16k mono PCM16)
            sid:   セッションID (任意, デフォルト "default")
        → audio/wav を返す。
        → X-Stackchan-* ヘッダにテキストと処理時間を載せる。
    POST /chat_text
        text を直接 LLM に渡して、応答 audio/wav を返す。
    POST /speak
        text を直接 TTS に渡して、audio/wav を返す。

起動:
    uvicorn main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response, JSONResponse

from stt import STT
from llm import LLM
from tts import TTS
from utterance_queue import UtteranceQueue, Utterance
from scheduler import Scheduler

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "info").upper(),
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
log = logging.getLogger("stackchan")

# ----- 初期化 -----
stt = STT(
    model_name=os.getenv("WHISPER_MODEL", "small"),
    device=os.getenv("WHISPER_DEVICE", "auto"),
    language=os.getenv("WHISPER_LANGUAGE", "ja"),
)
llm = LLM(
    host=os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434"),
    model=os.getenv("OLLAMA_MODEL", "qwen2.5:7b"),
    history_turns=int(os.getenv("HISTORY_TURNS", "6")),
    timeout_s=float(os.getenv("OLLAMA_TIMEOUT_S", "60")),
    temperature=float(os.getenv("OLLAMA_TEMPERATURE", "0.7")),
    num_predict=int(os.getenv("OLLAMA_NUM_PREDICT", "200")),
    max_sessions=int(os.getenv("MAX_SESSIONS", "16")),
)
tts = TTS()  # backend / env 解釈は tts.py + tts_<backend>.py に委譲

queue = UtteranceQueue(max_size=int(os.getenv("QUEUE_MAX_SIZE", "16")))
_scheduler: Scheduler | None = None


async def _prewarm_tts():
    """起動直後にダミー合成を 1 回走らせて初回ペナルティを潰す。

    Irodori 経路は infer.main() が最初に呼ばれた時点でモデルロードや
    Triton カーネルの JIT コンパイルが走るため、最初の /chat だけが
    数秒遅くなる。本番リクエストの前に 1 度合成しておくと体感が改善する。
    失敗しても起動は止めない (TTS_BACKEND の設定ミスなどはユーザーに任せる)。
    """
    try:
        text = os.getenv("TTS_PREWARM_TEXT", "あ")
        log.info("TTS pre-warm: synthesizing %r ...", text)
        t0 = time.perf_counter()
        await asyncio.to_thread(tts.synthesize, text)
        log.info("TTS pre-warm done in %.0f ms", (time.perf_counter() - t0) * 1000)
    except Exception:
        log.exception("TTS pre-warm failed (continuing without warm cache)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scheduler
    # 起動時に TTS を一度叩いて初回ペナルティを消す。
    # TTS_PREWARM=0 で無効化可能 (CI など TTS が動かない環境用)。
    if os.getenv("TTS_PREWARM", "1") == "1":
        # 別タスクで走らせて lifespan の yield を待たせない (起動を遅らせない)。
        asyncio.create_task(_prewarm_tts())
    if os.getenv("SCHEDULE_ENABLED", "0") == "1":
        path = Path(os.getenv("SCHEDULE_FILE", "schedule.json"))
        _scheduler = Scheduler.from_file(path, llm, tts, queue)
        await _scheduler.start()
    else:
        log.info("scheduler disabled (set SCHEDULE_ENABLED=1 to enable)")
    yield
    if _scheduler is not None:
        await _scheduler.stop()


app = FastAPI(title="Stack-chan server", version="0.1.0", lifespan=lifespan)
MAX_AUDIO_BYTES = int(os.getenv("MAX_AUDIO_BYTES", str(2 * 1024 * 1024)))


def _elapsed_ms(t0: float) -> float:
    return (time.perf_counter() - t0) * 1000


def _timing_header(timings: dict[str, float]) -> str:
    return ",".join(f"{k};dur={v:.1f}" for k, v in timings.items())


def _wav_response(
    wav: bytes,
    *,
    user_text: str | None,
    bot_text: str,
    timings: dict[str, float],
) -> Response:
    headers = {
        "X-Stackchan-Bot-Text": quote(bot_text),
        "X-Stackchan-Timing": _timing_header(timings),
        "X-Stackchan-TTS-Backend": os.getenv("TTS_BACKEND", "irodori"),
    }
    if user_text is not None:
        headers["X-Stackchan-User-Text"] = quote(user_text)
    log.info("timing %s", headers["X-Stackchan-Timing"])
    return Response(content=wav, media_type="audio/wav", headers=headers)


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/ready")
def ready():
    components = {
        "stt": stt.status(),
        "llm": llm.status(),
        "tts": tts.status() if hasattr(tts, "status") else {"ok": True},
    }
    return {
        "ok": all(c.get("ok", False) for c in components.values()),
        "components": components,
    }


@app.post("/chat")
async def chat(
    audio: UploadFile = File(...),
    sid: str = Form("default"),
):
    timings: dict[str, float] = {}
    total_t0 = time.perf_counter()
    if audio.content_type not in ("audio/wav", "audio/x-wav", "application/octet-stream"):
        log.warning("Unexpected content_type=%s, accepting anyway", audio.content_type)

    wav_in = await audio.read()
    if len(wav_in) < 44:
        raise HTTPException(status_code=400, detail="audio too short")
    if len(wav_in) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="audio too large")

    try:
        t0 = time.perf_counter()
        user_text = stt.transcribe(wav_in)
        timings["stt"] = _elapsed_ms(t0)
    except Exception as e:
        log.exception("STT failed")
        return JSONResponse(status_code=500, content={"error": f"stt: {e}"})

    if not user_text:
        # 無音だった場合は短い相槌を返す
        user_text = "(no speech)"
        bot_text = "ん? 聞こえなかった、もう一回いってくれる?"
    else:
        try:
            t0 = time.perf_counter()
            bot_text = llm.chat(sid, user_text)
            timings["llm"] = _elapsed_ms(t0)
        except Exception as e:
            log.exception("LLM failed")
            return JSONResponse(status_code=500, content={"error": f"llm: {e}"})

    try:
        t0 = time.perf_counter()
        wav_out = tts.synthesize(bot_text)
        timings["tts"] = _elapsed_ms(t0)
    except Exception as e:
        log.exception("TTS failed")
        return JSONResponse(status_code=500, content={"error": f"tts: {e}"})

    timings["total"] = _elapsed_ms(total_t0)
    return _wav_response(
        wav_out,
        user_text=user_text,
        bot_text=bot_text,
        timings=timings,
    )


@app.post("/speak")
def speak(text: str = Form(...)):
    timings: dict[str, float] = {}
    total_t0 = time.perf_counter()
    text = text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is empty")
    try:
        t0 = time.perf_counter()
        wav_out = tts.synthesize(text)
        timings["tts"] = _elapsed_ms(t0)
    except Exception as e:
        log.exception("TTS failed")
        return JSONResponse(status_code=500, content={"error": f"tts: {e}"})
    timings["total"] = _elapsed_ms(total_t0)
    return _wav_response(wav_out, user_text=None, bot_text=text, timings=timings)


@app.post("/chat_text")
def chat_text(text: str = Form(...), sid: str = Form("default")):
    timings: dict[str, float] = {}
    total_t0 = time.perf_counter()
    text = text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is empty")
    try:
        t0 = time.perf_counter()
        bot_text = llm.chat(sid, text)
        timings["llm"] = _elapsed_ms(t0)
    except Exception as e:
        log.exception("LLM failed")
        return JSONResponse(status_code=500, content={"error": f"llm: {e}"})
    try:
        t0 = time.perf_counter()
        wav_out = tts.synthesize(bot_text)
        timings["tts"] = _elapsed_ms(t0)
    except Exception as e:
        log.exception("TTS failed")
        return JSONResponse(status_code=500, content={"error": f"tts: {e}"})
    timings["total"] = _elapsed_ms(total_t0)
    return _wav_response(wav_out, user_text=text, bot_text=bot_text, timings=timings)


@app.post("/reset")
def reset(sid: str = Form("default")):
    llm.reset(sid)
    return {"ok": True}


# ---- 定期発話 / 外部 push --------------------------------------------------

@app.get("/pull")
async def pull(wait: float = 0.0):
    """CoreS3 から呼ぶ long-poll。`wait` 秒 (0..60) 待ってキューから 1 件返す。

    キュー空のままタイムアウトしたら 204 No Content。
    成功時は `X-Stackchan-Bot-Text` / `X-Stackchan-Source` ヘッダ付きの WAV。
    """
    wait = max(0.0, min(wait, 60.0))
    u = await queue.pull(wait)
    if u is None:
        return Response(status_code=204)
    return Response(
        content=u.wav,
        media_type="audio/wav",
        headers={
            "X-Stackchan-Bot-Text":     quote(u.bot_text),
            "X-Stackchan-Source":       u.source,
            "X-Stackchan-TTS-Backend":  os.getenv("TTS_BACKEND", "irodori"),
        },
    )


@app.post("/enqueue")
async def enqueue(
    text: str = Form(...),
    via_llm: bool = Form(False),
    sid: str = Form("external"),
):
    """外部 (Discord bot / curl など) から発話を積む。

    via_llm=true で `text` をプロンプトとして LLM を通してから TTS、
    false なら `text` をそのまま TTS する。
    """
    text = text.strip()
    if not text:
        raise HTTPException(400, "text is empty")
    if via_llm:
        bot_text = await asyncio.to_thread(llm.chat, sid, text)
    else:
        bot_text = text
    if not bot_text:
        raise HTTPException(500, "empty bot_text after LLM")
    wav = await asyncio.to_thread(tts.synthesize, bot_text)
    ok = queue.push_nowait(Utterance(wav=wav, bot_text=bot_text, source=f"ext:{sid}"))
    if not ok:
        raise HTTPException(503, "utterance queue full")
    return {"ok": True, "bot_text": bot_text, "queue_size": queue.size()}


@app.get("/scheduler/status")
def scheduler_status():
    if _scheduler is None:
        return {"enabled": False}
    return {"enabled": True, **_scheduler.status()}


# ---- 簡易管理画面 -----------------------------------------------------------
# /admin を開くと /ready /scheduler/status をブラウザから見れる。
# /enqueue を叩くフォームつき。認証なしのローカル運用専用なので、
# uvicorn を 0.0.0.0 で外に出している場合は逆プロキシ + Basic 認証推奨。
ADMIN_HTML = """<!doctype html>
<html lang="ja"><head>
<meta charset="utf-8"><title>Stack-chan admin</title>
<style>
 body{font-family:system-ui,-apple-system,sans-serif;max-width:780px;margin:24px auto;padding:0 16px;line-height:1.55}
 h1{font-size:1.3em;border-bottom:1px solid #ccc;padding-bottom:6px}
 h2{font-size:1.05em;margin-top:1.8em}
 code,pre{background:#f4f4f4;padding:2px 6px;border-radius:4px;font-family:'SFMono-Regular',Consolas,monospace}
 pre{padding:10px;overflow-x:auto}
 table{border-collapse:collapse;width:100%;margin:8px 0}
 th,td{border:1px solid #ddd;padding:6px 8px;font-size:0.9em;text-align:left}
 th{background:#f8f8f8}
 .err{color:#a00}
 .ok{color:#070}
 button{padding:6px 14px;cursor:pointer}
 input[type=text]{padding:6px 8px;width:60%}
 label{display:inline-block;margin-right:14px}
</style></head><body>
<h1>Stack-chan admin</h1>
<p>ローカル運用専用ページ。 <code>0.0.0.0</code> 公開時は逆プロキシで保護してください。</p>

<h2>サブシステム状態 (<code>/ready</code>)</h2>
<pre id="ready">loading...</pre>

<h2>スケジューラ (<code>/scheduler/status</code>)</h2>
<div id="sched">loading...</div>

<h2>テスト発話 push (<code>/enqueue</code>)</h2>
<form id="f">
  <input type="text" name="text" placeholder="読み上げるテキスト" required>
  <label><input type="checkbox" name="via_llm"> LLM 経由</label>
  <button type="submit">送信</button>
</form>
<pre id="enq"></pre>

<script>
async function loadReady(){
  try{
    const r = await fetch('/ready'); const j = await r.json();
    document.getElementById('ready').textContent = JSON.stringify(j, null, 2);
  }catch(e){
    document.getElementById('ready').innerHTML = '<span class="err">/ready 取得失敗: '+e+'</span>';
  }
}
async function loadSched(){
  try{
    const r = await fetch('/scheduler/status'); const j = await r.json();
    if(!j.enabled){ document.getElementById('sched').innerHTML =
       '<i>スケジューラ無効 (SCHEDULE_ENABLED=0)。 /enqueue は使えます。</i>'; return; }
    let html = '<p>running: <b>'+(j.running?'<span class="ok">yes</span>':'<span class="err">no</span>')+
               '</b> &nbsp; queue_size: <b>'+j.queue_size+'</b></p>';
    html += '<table><tr><th>name</th><th>cron</th><th>kind</th><th>next</th><th>fired</th><th>last</th><th>error</th></tr>';
    for(const t of j.triggers){
      html += '<tr><td>'+t.name+'</td><td><code>'+t.cron+'</code></td><td>'+t.kind+
              '</td><td>'+t.next+'</td><td>'+t.fire_count+'</td><td>'+(t.last_fire||'-')+
              '</td><td>'+(t.last_error?'<span class="err">'+t.last_error+'</span>':'-')+'</td></tr>';
    }
    html += '</table>';
    document.getElementById('sched').innerHTML = html;
  }catch(e){
    document.getElementById('sched').innerHTML = '<span class="err">取得失敗: '+e+'</span>';
  }
}
document.getElementById('f').addEventListener('submit', async ev => {
  ev.preventDefault();
  const fd = new FormData(ev.target);
  // checkbox は付いていないと送信されないので明示
  if(!fd.has('via_llm')) fd.set('via_llm','false'); else fd.set('via_llm','true');
  fd.set('sid','admin');
  const r = await fetch('/enqueue',{method:'POST',body:fd});
  const j = await r.json().catch(()=>({error:'parse failed', status:r.status}));
  document.getElementById('enq').textContent = JSON.stringify(j, null, 2);
  loadSched();
});
loadReady(); loadSched();
setInterval(()=>{loadReady(); loadSched();}, 5000);
</script>
</body></html>
"""


@app.get("/admin", response_class=Response)
def admin_page():
    return Response(content=ADMIN_HTML, media_type="text/html; charset=utf-8")
