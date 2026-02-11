# =============================================================
# Configuration — Update these values before deploying
# =============================================================

# Plivo credentials (from https://console.plivo.com/dashboard/)
PLIVO_AUTH_ID = "YOUR_PLIVO_AUTH_ID"
PLIVO_AUTH_TOKEN = "YOUR_PLIVO_AUTH_TOKEN"

# Your Plivo Indian phone number (format: +91XXXXXXXXXX)
PLIVO_FROM_NUMBER = "+91XXXXXXXXXX"

# Public URL (ngrok or RunPod proxy — no port, no trailing slash)
RUNPOD_PUBLIC_IP = "abby-pseudolateral-nonmountainously.ngrok-free.dev"

# Ports
ORCHESTRATOR_PORT = 3000
PERSONAPLEX_PORT = 8998

# Audio settings
PLIVO_SAMPLE_RATE = 8000
PERSONAPLEX_SAMPLE_RATE = 24000
