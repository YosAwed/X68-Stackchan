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

import logging
import os
import time
from urllib.parse import quote

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response, JSONResponse

from stt import STT
from llm import LLM
from tts import TTS

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
)
tts = TTS()  # backend / env 解釈は tts.py + tts_<backend>.py に委譲

app = FastAPI(title="Stack-chan server", version="0.1.0")


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
