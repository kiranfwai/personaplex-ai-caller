"""
Bridge Service — Connects Plivo audio stream to PersonaPlex
Handles audio format conversion: mulaw 8kHz <-> PCM 24kHz
"""
import asyncio
import json
import base64
import struct
import logging
import numpy as np
from scipy.signal import resample_poly
import websockets

from config import (
    PERSONAPLEX_PORT,
    PLIVO_SAMPLE_RATE,
    PERSONAPLEX_SAMPLE_RATE,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [BRIDGE] %(message)s")
log = logging.getLogger("bridge")

# ----- Audio conversion tables (mulaw) -----

# mulaw decompression table
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


def mulaw_decode(mulaw_bytes: bytes) -> np.ndarray:
    """Decode mulaw bytes to int16 PCM samples."""
    indices = np.frombuffer(mulaw_bytes, dtype=np.uint8)
    return MULAW_DECODE_TABLE[indices]


def mulaw_encode(pcm_samples: np.ndarray) -> bytes:
    """Encode int16 PCM samples to mulaw bytes."""
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


def resample_8k_to_24k(audio_8k: np.ndarray) -> np.ndarray:
    """Resample from 8kHz to 24kHz (factor of 3)."""
    return resample_poly(audio_8k, up=3, down=1).astype(np.int16)


def resample_24k_to_8k(audio_24k: np.ndarray) -> np.ndarray:
    """Resample from 24kHz to 8kHz (factor of 3)."""
    return resample_poly(audio_24k, up=1, down=3).astype(np.int16)


def plivo_to_personaplex(mulaw_payload: str) -> bytes:
    """Convert Plivo mulaw 8kHz base64 -> PCM 24kHz bytes for PersonaPlex."""
    mulaw_bytes = base64.b64decode(mulaw_payload)
    pcm_8k = mulaw_decode(mulaw_bytes)
    pcm_24k = resample_8k_to_24k(pcm_8k)
    return pcm_24k.tobytes()


def personaplex_to_plivo(pcm_24k_bytes: bytes) -> str:
    """Convert PersonaPlex PCM 24kHz bytes -> Plivo mulaw 8kHz base64."""
    pcm_24k = np.frombuffer(pcm_24k_bytes, dtype=np.int16)
    pcm_8k = resample_24k_to_8k(pcm_24k)
    mulaw_bytes = mulaw_encode(pcm_8k)
    return base64.b64encode(mulaw_bytes).decode("ascii")


class CallBridge:
    """Bridges a single Plivo call to a PersonaPlex session."""

    def __init__(self, call_id: str):
        self.call_id = call_id
        self.persona_ws = None
        self.running = False

    async def handle(self, plivo_ws):
        """Main handler — called when Plivo opens a WebSocket."""
        log.info(f"[{self.call_id}] Plivo WebSocket connected")
        self.running = True

        try:
            # Connect to PersonaPlex on localhost
            persona_url = f"wss://localhost:{PERSONAPLEX_PORT}/ws"
            self.persona_ws = await websockets.connect(
                persona_url,
                ssl=True,
                additional_headers={},
            )
            log.info(f"[{self.call_id}] Connected to PersonaPlex")

            # Run both directions concurrently
            await asyncio.gather(
                self._plivo_to_persona(plivo_ws),
                self._persona_to_plivo(plivo_ws),
            )
        except websockets.exceptions.ConnectionClosed as e:
            log.info(f"[{self.call_id}] Connection closed: {e}")
        except Exception as e:
            log.error(f"[{self.call_id}] Error: {e}", exc_info=True)
        finally:
            self.running = False
            if self.persona_ws:
                await self.persona_ws.close()
            log.info(f"[{self.call_id}] Bridge closed")

    async def _plivo_to_persona(self, plivo_ws):
        """Forward audio: Plivo -> PersonaPlex."""
        async for message in plivo_ws:
            if not self.running:
                break
            try:
                data = json.loads(message)
                event = data.get("event")

                if event == "media":
                    payload = data["media"]["payload"]
                    pcm_24k = plivo_to_personaplex(payload)
                    if self.persona_ws and self.persona_ws.open:
                        await self.persona_ws.send(pcm_24k)

                elif event == "start":
                    log.info(f"[{self.call_id}] Stream started: {data.get('start', {})}")

                elif event == "stop":
                    log.info(f"[{self.call_id}] Stream stopped")
                    self.running = False
                    break

            except Exception as e:
                log.error(f"[{self.call_id}] Plivo->Persona error: {e}")

    async def _persona_to_plivo(self, plivo_ws):
        """Forward audio: PersonaPlex -> Plivo."""
        try:
            async for message in self.persona_ws:
                if not self.running:
                    break
                if isinstance(message, bytes) and len(message) > 0:
                    mulaw_b64 = personaplex_to_plivo(message)
                    await plivo_ws.send(json.dumps({
                        "event": "playAudio",
                        "media": {
                            "contentType": "audio/x-mulaw",
                            "sampleRate": "8000",
                            "payload": mulaw_b64,
                        }
                    }))
        except websockets.exceptions.ConnectionClosed:
            log.info(f"[{self.call_id}] PersonaPlex connection closed")
        except Exception as e:
            log.error(f"[{self.call_id}] Persona->Plivo error: {e}")


# Track active calls
active_calls: dict[str, CallBridge] = {}


async def bridge_handler(websocket, path):
    """WebSocket handler for incoming Plivo streams."""
    # Wait for the first message to get call ID
    first_msg = await websocket.recv()
    data = json.loads(first_msg)

    call_id = data.get("start", {}).get("callId", "unknown")
    log.info(f"New call bridge: {call_id}")

    bridge = CallBridge(call_id)
    active_calls[call_id] = bridge

    try:
        await bridge.handle(websocket)
    finally:
        active_calls.pop(call_id, None)


async def start_bridge(host="0.0.0.0", port=8080):
    """Start the bridge WebSocket server."""
    server = await websockets.serve(bridge_handler, host, port)
    log.info(f"Bridge server running on ws://{host}:{port}")
    await server.wait_closed()


if __name__ == "__main__":
    asyncio.run(start_bridge())
