# =============================================================
# Configuration — Update these values before deploying
# =============================================================

# Plivo credentials (from https://console.plivo.com/dashboard/)
PLIVO_AUTH_ID = "YOUR_PLIVO_AUTH_ID"
PLIVO_AUTH_TOKEN = "YOUR_PLIVO_AUTH_TOKEN"

# Your Plivo Indian phone number (format: +91XXXXXXXXXX)
PLIVO_FROM_NUMBER = "+91XXXXXXXXXX"

# RunPod public IP (shown in RunPod dashboard under "Connect")
# Update this after pod starts
RUNPOD_PUBLIC_IP = "YOUR_RUNPOD_IP"

# Ports
BRIDGE_PORT = 8080
ORCHESTRATOR_PORT = 3000
PERSONAPLEX_PORT = 8998

# PersonaPlex persona configuration
PERSONA_TEXT_PROMPT = """You are a friendly and professional sales representative.
You are calling potential customers to introduce our product.
Be concise, warm, and helpful. Listen carefully to the customer.
If they are not interested, thank them politely and say goodbye.
Keep responses short — 1-2 sentences max per turn."""

# Choose a voice (check PersonaPlex docs for available voices)
PERSONA_VOICE = None  # None = default voice

# Audio settings (don't change unless you know what you're doing)
PLIVO_SAMPLE_RATE = 8000
PLIVO_ENCODING = "mulaw"
PERSONAPLEX_SAMPLE_RATE = 24000
