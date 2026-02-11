"""
Merged Orchestrator + Bridge — Single service on port 3000
Handles Plivo calls, answer XML, and WebSocket audio bridge to PersonaPlex.
Uses Opus encoding/decoding for PersonaPlex's Moshi protocol.
"""
import asyncio
import csv
import io
import json
import base64
import logging
import ssl
import traceback
from datetime import datetime
from urllib.parse import urlencode

import numpy as np
from scipy.signal import resample_poly
import sphn
import websockets
import plivo
from fastapi import FastAPI, UploadFile, File, HTTPException, WebSocket, Request
from fastapi.responses import Response
from pydantic import BaseModel

from config import (
    PLIVO_AUTH_ID, PLIVO_AUTH_TOKEN, PLIVO_FROM_NUMBER,
    RUNPOD_PUBLIC_IP, ORCHESTRATOR_PORT, PERSONAPLEX_PORT,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("app")

app = FastAPI(title="PersonaPlex AI Caller")
plivo_client = plivo.RestClient(PLIVO_AUTH_ID, PLIVO_AUTH_TOKEN)
call_log = []

SAMPLE_RATE = 24000  # PersonaPlex native sample rate

# ---- Audio conversion (mulaw) ----
MULAW_DECODE_TABLE = np.zeros(256, dtype=np.int16)
for i in range(256):
    val = ~i
    sign = val & 0x80
    exponent = (val >> 4) & 0x07
    mantissa = val & 0x0F
    sample = (mantissa << 3) + 0x84
    sample <<= exponent
    sample -= 0x84
    MULAW_DECODE_TABLE[i] = -sample if sign else sample

def mulaw_decode(mulaw_bytes):
    indices = np.frombuffer(mulaw_bytes, dtype=np.uint8)
    return MULAW_DECODE_TABLE[indices]

def mulaw_encode(pcm_samples):
    MULAW_MAX = 0x1FFF
    MULAW_BIAS = 0x84
    samples = pcm_samples.astype(np.int32)
    sign = np.where(samples < 0, 0x80, 0)
    samples = np.abs(samples)
    samples = np.minimum(samples, MULAW_MAX)
    samples = samples + MULAW_BIAS
    exponent = np.zeros(len(samples), dtype=np.int32)
    for exp in range(7, 0, -1):
        mask = 1 << (exp + 3)
        exponent = np.where((samples & mask) != 0, exp, exponent)
    mantissa = (samples >> (exponent + 3)) & 0x0F
    mulaw_byte = ~(sign | (exponent << 4) | mantissa) & 0xFF
    return mulaw_byte.astype(np.uint8).tobytes()


# ---- Bridge WebSocket endpoint ----
@app.websocket("/bridge")
async def bridge_websocket(plivo_ws: WebSocket):
    await plivo_ws.accept()
    log.info(">>> /bridge WebSocket ACCEPTED")
    persona_ws = None
    running = True
    call_id = "unknown"
    media_recv_count = 0
    media_send_count = 0

    # Opus encoder/decoder for PersonaPlex protocol
    opus_writer = sphn.OpusStreamWriter(SAMPLE_RATE)
    opus_reader = sphn.OpusStreamReader(SAMPLE_RATE)

    try:
        # Wait for Plivo's start event
        first_msg = await plivo_ws.receive_text()
        data = json.loads(first_msg)
        call_id = data.get("start", {}).get("callId", "unknown")
        log.info(f"[{call_id}] Plivo stream started")

        # Connect to PersonaPlex
        params = urlencode({
            "voice_prompt": "NATF0.pt",
            "text_prompt": "You are a friendly sales representative. Be concise and helpful.",
        })
        persona_url = f"wss://localhost:{PERSONAPLEX_PORT}/api/chat?{params}"
        log.info(f"[{call_id}] Connecting to PersonaPlex...")

        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        persona_ws = await websockets.connect(persona_url, ssl=ssl_ctx)
        log.info(f"[{call_id}] Connected to PersonaPlex")

        # Wait for handshake byte (b"\x00")
        handshake = await persona_ws.recv()
        if isinstance(handshake, bytes) and handshake == b"\x00":
            log.info(f"[{call_id}] Received PersonaPlex handshake OK")
        else:
            log.warning(f"[{call_id}] Unexpected handshake: {handshake!r}")

        async def plivo_to_persona():
            """Plivo mulaw 8kHz -> PCM 24kHz -> Opus encode -> PersonaPlex"""
            nonlocal running, media_recv_count
            try:
                while running:
                    msg = await plivo_ws.receive_text()
                    d = json.loads(msg)
                    ev = d.get("event")
                    if ev == "media":
                        media_recv_count += 1
                        payload = d["media"]["payload"]

                        # Decode mulaw to PCM 8kHz
                        mulaw_bytes = base64.b64decode(payload)
                        pcm_8k = mulaw_decode(mulaw_bytes)

                        # Resample 8kHz -> 24kHz
                        pcm_24k = resample_poly(pcm_8k, up=3, down=1).astype(np.int16)

                        # Normalize to float32 [-1, 1] for Opus encoder
                        pcm_float = pcm_24k.astype(np.float32) / 32768.0

                        # Write to Opus encoder
                        opus_writer.append_pcm(pcm_float)

                        # Read encoded Opus bytes and send to PersonaPlex
                        # Moshi protocol: b"\x01" prefix = audio data
                        opus_bytes = opus_writer.read_bytes()
                        if len(opus_bytes) > 0 and persona_ws and persona_ws.open:
                            await persona_ws.send(b"\x01" + opus_bytes)

                        if media_recv_count <= 3 or media_recv_count % 200 == 0:
                            log.info(f"[{call_id}] Plivo->Persona #{media_recv_count} mulaw={len(mulaw_bytes)}b opus={len(opus_bytes)}b")

                    elif ev == "stop":
                        log.info(f"[{call_id}] Plivo stream stopped")
                        running = False
                        break
            except Exception as e:
                log.error(f"[{call_id}] Plivo->Persona error: {type(e).__name__}: {e}")
                running = False

        async def persona_to_plivo():
            """PersonaPlex Opus -> PCM 24kHz -> mulaw 8kHz -> Plivo"""
            nonlocal running, media_send_count
            try:
                async for message in persona_ws:
                    if not running:
                        break
                    if isinstance(message, bytes) and len(message) > 0:
                        # PersonaPlex sends b"\x01" + opus_data for audio
                        prefix = message[0:1]
                        if prefix == b"\x01" and len(message) > 1:
                            opus_data = message[1:]

                            # Decode Opus to PCM float32
                            opus_reader.append_bytes(opus_data)
                            pcm_float = opus_reader.read_pcm()

                            if pcm_float is not None and len(pcm_float) > 0:
                                # Convert float32 -> int16
                                pcm_24k = (pcm_float * 32768.0).clip(-32768, 32767).astype(np.int16)

                                # Resample 24kHz -> 8kHz
                                pcm_8k = resample_poly(pcm_24k, up=1, down=3).astype(np.int16)

                                # Encode to mulaw
                                mulaw_bytes = mulaw_encode(pcm_8k)
                                mulaw_b64 = base64.b64encode(mulaw_bytes).decode("ascii")

                                # Send to Plivo
                                await plivo_ws.send_text(json.dumps({
                                    "event": "playAudio",
                                    "media": {
                                        "contentType": "audio/x-mulaw",
                                        "sampleRate": "8000",
                                        "payload": mulaw_b64,
                                    }
                                }))

                                media_send_count += 1
                                if media_send_count <= 3 or media_send_count % 200 == 0:
                                    log.info(f"[{call_id}] Persona->Plivo #{media_send_count} pcm={len(pcm_24k)} mulaw={len(mulaw_bytes)}b")

                        elif prefix == b"\x00":
                            log.info(f"[{call_id}] PersonaPlex control message")
                        else:
                            log.debug(f"[{call_id}] PersonaPlex other prefix: {prefix!r} len={len(message)}")

            except Exception as e:
                log.error(f"[{call_id}] Persona->Plivo error: {type(e).__name__}: {e}")
                running = False

        log.info(f"[{call_id}] Starting bidirectional audio bridge...")
        await asyncio.gather(plivo_to_persona(), persona_to_plivo())
        log.info(f"[{call_id}] Bridge ended. Recv={media_recv_count} Sent={media_send_count}")

    except Exception as e:
        log.error(f"[{call_id}] Bridge fatal error: {type(e).__name__}: {e}")
        log.error(traceback.format_exc())
    finally:
        if persona_ws:
            await persona_ws.close()
        log.info(f"[{call_id}] Bridge CLOSED. Total recv={media_recv_count} sent={media_send_count}")


# ---- Plivo endpoints ----
class CallRequest(BaseModel):
    phone: str

@app.post("/plivo-answer")
@app.get("/plivo-answer")
async def plivo_answer(request: Request):
    log.info(f">>> /plivo-answer hit from {request.client.host}")
    xml_response = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Stream bidirectional="true" keepCallAlive="true"
           contentType="audio/x-mulaw;rate=8000"
           streamTimeout="600">
        wss://{RUNPOD_PUBLIC_IP}/bridge
    </Stream>
</Response>"""
    return Response(content=xml_response, media_type="application/xml")

@app.post("/call")
async def make_call(req: CallRequest):
    try:
        answer_url = f"https://{RUNPOD_PUBLIC_IP}/plivo-answer"
        log.info(f">>> Calling {req.phone}")
        response = plivo_client.calls.create(
            from_=PLIVO_FROM_NUMBER, to_=req.phone,
            answer_url=answer_url, answer_method="POST",
        )
        record = {
            "phone": req.phone, "call_uuid": response.request_uuid,
            "status": "initiated", "timestamp": datetime.now().isoformat(),
        }
        call_log.append(record)
        log.info(f">>> Call UUID: {response.request_uuid}")
        return record
    except plivo.exceptions.PlivoRestError as e:
        log.error(f">>> Plivo error: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/upload-leads")
async def upload_leads(file: UploadFile = File(...)):
    content = await file.read()
    reader = csv.DictReader(io.StringIO(content.decode("utf-8")))
    phones = [r.get("phone","").strip() for r in reader if r.get("phone","").strip()]
    if not phones:
        raise HTTPException(400, "No phone numbers found")
    asyncio.create_task(_batch_bg(phones))
    return {"message": f"Calling {len(phones)} leads"}

async def _batch_bg(phones):
    for phone in phones:
        try:
            await make_call(CallRequest(phone=phone))
        except Exception as e:
            log.error(f"Failed {phone}: {e}")
        await asyncio.sleep(3)

@app.get("/calls")
async def list_calls():
    return {"total": len(call_log), "calls": call_log}

@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=ORCHESTRATOR_PORT)
