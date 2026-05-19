"""Stack-chan 母艦サーバ (FastAPI)

エンドポイント:
    POST /chat
        multipart/form-data:
            audio: WAV (16k mono PCM16)
            sid:   セッションID (任意, デフォルト "default")
        → audio/wav を返す。
        → X-Stackchan-User-Text / X-Stackchan-Bot-Text ヘッダにテキストを載せる。

起動:
    uvicorn main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import logging
import os
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
tts = TTS(
    ref_wav=os.getenv("IRODORI_REF_WAV") or None,
    device=os.getenv("IRODORI_DEVICE", "cuda"),
    force_fp16=os.getenv("IRODORI_FORCE_FP16", "1") == "1",
    checkpoint=os.getenv("IRODORI_CHECKPOINT") or None,
)

app = FastAPI(title="Stack-chan server", version="0.1.0")


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/chat")
async def chat(
    audio: UploadFile = File(...),
    sid: str = Form("default"),
):
    if audio.content_type not in ("audio/wav", "audio/x-wav", "application/octet-stream"):
        log.warning("Unexpected content_type=%s, accepting anyway", audio.content_type)

    wav_in = await audio.read()
    if len(wav_in) < 44:
        raise HTTPException(status_code=400, detail="audio too short")

    try:
        user_text = stt.transcribe(wav_in)
    except Exception as e:
        log.exception("STT failed")
        return JSONResponse(status_code=500, content={"error": f"stt: {e}"})

    if not user_text:
        # 無音だった場合は短い相槌を返す
        user_text = "(no speech)"
        bot_text = "ん? 聞こえなかった、もう一回いってくれる?"
    else:
        try:
            bot_text = llm.chat(sid, user_text)
        except Exception as e:
            log.exception("LLM failed")
            return JSONResponse(status_code=500, content={"error": f"llm: {e}"})

    try:
        wav_out = tts.synthesize(bot_text)
    except Exception as e:
        log.exception("TTS failed")
        return JSONResponse(status_code=500, content={"error": f"tts: {e}"})

    return Response(
        content=wav_out,
        media_type="audio/wav",
        headers={
            # HTTP ヘッダに非 ASCII を載せられないので URL エンコード
            "X-Stackchan-User-Text": quote(user_text),
            "X-Stackchan-Bot-Text":  quote(bot_text),
        },
    )


@app.post("/reset")
def reset(sid: str = Form("default")):
    llm.reset(sid)
    return {"ok": True}
