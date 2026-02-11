"""
Merged Orchestrator + Bridge — Single service on port 3000
Handles Plivo calls, answer XML, and WebSocket audio bridge to PersonaPlex.
"""
import asyncio
import csv
import io
import json
import base64
import logging
from datetime import datetime

import numpy as np
from scipy.signal import resample_poly
import websockets
import plivo
from fastapi import FastAPI, UploadFile, File, HTTPException, WebSocket
from fastapi.responses import Response
from pydantic import BaseModel

from config import (
    PLIVO_AUTH_ID, PLIVO_AUTH_TOKEN, PLIVO_FROM_NUMBER,
    RUNPOD_PUBLIC_IP, ORCHESTRATOR_PORT, PERSONAPLEX_PORT,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [APP] %(message)s")
log = logging.getLogger("app")

app = FastAPI(title="PersonaPlex AI Caller")
plivo_client = plivo.RestClient(PLIVO_AUTH_ID, PLIVO_AUTH_TOKEN)
call_log = []

# ---- Audio conversion ----
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

def plivo_to_personaplex(mulaw_payload):
    mulaw_bytes = base64.b64decode(mulaw_payload)
    pcm_8k = mulaw_decode(mulaw_bytes)
    pcm_24k = resample_poly(pcm_8k, up=3, down=1).astype(np.int16)
    return pcm_24k.tobytes()

def personaplex_to_plivo(pcm_24k_bytes):
    pcm_24k = np.frombuffer(pcm_24k_bytes, dtype=np.int16)
    pcm_8k = resample_poly(pcm_24k, up=1, down=3).astype(np.int16)
    mulaw_bytes = mulaw_encode(pcm_8k)
    return base64.b64encode(mulaw_bytes).decode("ascii")

# ---- Bridge WebSocket endpoint ----
@app.websocket("/bridge")
async def bridge_websocket(plivo_ws: WebSocket):
    await plivo_ws.accept()
    log.info("Plivo WebSocket connected to /bridge")
    persona_ws = None
    running = True
    try:
        first_msg = await plivo_ws.receive_text()
        data = json.loads(first_msg)
        call_id = data.get("start", {}).get("callId", "unknown")
        log.info(f"[{call_id}] Stream started")

        persona_url = f"wss://localhost:{PERSONAPLEX_PORT}/ws"
        persona_ws = await websockets.connect(persona_url, ssl=True)
        log.info(f"[{call_id}] Connected to PersonaPlex")

        async def plivo_to_persona():
            nonlocal running
            try:
                while running:
                    msg = await plivo_ws.receive_text()
                    d = json.loads(msg)
                    ev = d.get("event")
                    if ev == "media":
                        pcm = plivo_to_personaplex(d["media"]["payload"])
                        if persona_ws and persona_ws.open:
                            await persona_ws.send(pcm)
                    elif ev == "stop":
                        log.info(f"[{call_id}] Plivo stream stopped")
                        running = False
                        break
            except Exception as e:
                log.info(f"[{call_id}] Plivo recv ended: {e}")
                running = False

        async def persona_to_plivo():
            nonlocal running
            try:
                async for message in persona_ws:
                    if not running:
                        break
                    if isinstance(message, bytes) and len(message) > 0:
                        mulaw_b64 = personaplex_to_plivo(message)
                        await plivo_ws.send_text(json.dumps({
                            "event": "playAudio",
                            "media": {
                                "contentType": "audio/x-mulaw",
                                "sampleRate": "8000",
                                "payload": mulaw_b64,
                            }
                        }))
            except Exception as e:
                log.info(f"[{call_id}] Persona recv ended: {e}")
                running = False

        await asyncio.gather(plivo_to_persona(), persona_to_plivo())
    except Exception as e:
        log.error(f"Bridge error: {e}", exc_info=True)
    finally:
        if persona_ws:
            await persona_ws.close()
        log.info("Bridge closed")

# ---- Plivo endpoints ----
class CallRequest(BaseModel):
    phone: str

@app.post("/plivo-answer")
@app.get("/plivo-answer")
async def plivo_answer():
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
        response = plivo_client.calls.create(
            from_=PLIVO_FROM_NUMBER, to_=req.phone,
            answer_url=answer_url, answer_method="POST",
        )
        record = {
            "phone": req.phone, "call_uuid": response.request_uuid,
            "status": "initiated", "timestamp": datetime.now().isoformat(),
        }
        call_log.append(record)
        log.info(f"Call initiated to {req.phone} | UUID: {response.request_uuid}")
        return record
    except plivo.exceptions.PlivoRestError as e:
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
