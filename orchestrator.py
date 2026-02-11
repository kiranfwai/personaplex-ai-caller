"""
Call Orchestrator — Triggers outbound calls via Plivo,
serves Plivo answer XML, and manages the call queue.
"""
import asyncio
import csv
import io
import logging
from datetime import datetime
from typing import Optional

import plivo
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from config import (
    PLIVO_AUTH_ID,
    PLIVO_AUTH_TOKEN,
    PLIVO_FROM_NUMBER,
    RUNPOD_PUBLIC_IP,
    BRIDGE_PORT,
    ORCHESTRATOR_PORT,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [ORCH] %(message)s")
log = logging.getLogger("orchestrator")

app = FastAPI(title="PersonaPlex Call Orchestrator")

# Plivo client
plivo_client = plivo.RestClient(PLIVO_AUTH_ID, PLIVO_AUTH_TOKEN)

# Call tracking
call_log: list[dict] = []


# ----- Models -----

class CallRequest(BaseModel):
    phone: str  # Format: +91XXXXXXXXXX
    persona: Optional[str] = "sales_agent"


class BatchCallRequest(BaseModel):
    phones: list[str]
    delay_seconds: float = 3.0
    persona: Optional[str] = "sales_agent"


# ----- Plivo Answer Endpoint -----

@app.post("/plivo-answer")
@app.get("/plivo-answer")
async def plivo_answer():
    """
    Plivo hits this URL when the lead picks up.
    Returns XML that starts bidirectional audio streaming.
    """
    xml_response = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Stream bidirectional="true" keepCallAlive="true"
           contentType="audio/x-mulaw;rate=8000"
           streamTimeout="600">
        wss://{RUNPOD_PUBLIC_IP}:{BRIDGE_PORT}
    </Stream>
</Response>"""

    return Response(content=xml_response, media_type="application/xml")


# ----- Call Endpoints -----

@app.post("/call")
async def make_call(req: CallRequest):
    """Make a single outbound call to a lead."""
    try:
        answer_url = f"https://{RUNPOD_PUBLIC_IP}:{ORCHESTRATOR_PORT}/plivo-answer"

        response = plivo_client.calls.create(
            from_=PLIVO_FROM_NUMBER,
            to_=req.phone,
            answer_url=answer_url,
            answer_method="POST",
        )

        call_record = {
            "phone": req.phone,
            "call_uuid": response.request_uuid,
            "status": "initiated",
            "persona": req.persona,
            "timestamp": datetime.now().isoformat(),
        }
        call_log.append(call_record)

        log.info(f"Call initiated to {req.phone} | UUID: {response.request_uuid}")
        return call_record

    except plivo.exceptions.PlivoRestError as e:
        log.error(f"Plivo error calling {req.phone}: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/batch-call")
async def batch_call(req: BatchCallRequest):
    """Call multiple leads sequentially with a delay between calls."""
    results = []
    for phone in req.phones:
        try:
            result = await make_call(CallRequest(phone=phone, persona=req.persona))
            results.append(result)
        except HTTPException as e:
            results.append({"phone": phone, "status": "failed", "error": str(e.detail)})
        await asyncio.sleep(req.delay_seconds)

    return {"total": len(results), "results": results}


@app.post("/upload-leads")
async def upload_leads(file: UploadFile = File(...), delay_seconds: float = 3.0):
    """
    Upload a CSV file with a 'phone' column.
    Triggers calls to each number sequentially.
    """
    content = await file.read()
    reader = csv.DictReader(io.StringIO(content.decode("utf-8")))

    phones = []
    for row in reader:
        phone = row.get("phone", "").strip()
        if phone:
            phones.append(phone)

    if not phones:
        raise HTTPException(status_code=400, detail="No phone numbers found in CSV")

    log.info(f"Uploaded {len(phones)} leads, starting calls...")

    # Start calling in background
    asyncio.create_task(_batch_call_background(phones, delay_seconds))

    return {"message": f"Started calling {len(phones)} leads", "phones": phones}


async def _batch_call_background(phones: list[str], delay: float):
    for phone in phones:
        try:
            await make_call(CallRequest(phone=phone))
        except Exception as e:
            log.error(f"Failed to call {phone}: {e}")
        await asyncio.sleep(delay)


# ----- Status Endpoints -----

@app.get("/calls")
async def list_calls():
    """Get all call logs."""
    return {"total": len(call_log), "calls": call_log}


@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=ORCHESTRATOR_PORT)
