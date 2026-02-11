#!/bin/bash
# =============================================================
# PersonaPlex AI Caller — One-command setup & launch
# Usage: bash /workspace/caller/start.sh
# =============================================================
set -e

WORKSPACE="/workspace"
CALLER_DIR="$WORKSPACE/caller"
PERSONA_DIR="$WORKSPACE/personaplex"
LOG_DIR="$WORKSPACE/logs"

mkdir -p "$LOG_DIR"

echo "=========================================="
echo " PersonaPlex AI Caller — Setup & Launch"
echo "=========================================="

# ---- Step 0: Kill any old processes ----
echo "[0/4] Cleaning up old processes..."
pkill -f "moshi.server" 2>/dev/null || true
pkill -f "orchestrator.py" 2>/dev/null || true
sleep 1

# ---- Step 1: Install dependencies ----
echo "[1/4] Installing dependencies..."

# Install moshi (PersonaPlex) if not already installed
if ! python -c "import moshi" 2>/dev/null; then
    echo "  Installing moshi from $PERSONA_DIR/moshi/..."
    pip install "$PERSONA_DIR/moshi/" -q
fi

# Install caller dependencies
pip install -r "$CALLER_DIR/requirements.txt" -q
echo "  Dependencies ready."

# ---- Step 2: Set environment variables ----
echo "[2/4] Loading environment..."

# Load from .env file if it exists
if [ -f "$CALLER_DIR/.env" ]; then
    echo "  Loading .env file..."
    export $(grep -v '^#' "$CALLER_DIR/.env" | xargs)
fi

# Verify Plivo credentials are set
if [ -z "$PLIVO_AUTH_ID" ] || [ "$PLIVO_AUTH_ID" = "YOUR_PLIVO_AUTH_ID" ]; then
    echo ""
    echo "  ERROR: Plivo credentials not set!"
    echo "  Either export them or create $CALLER_DIR/.env with:"
    echo "    PLIVO_AUTH_ID=your_id"
    echo "    PLIVO_AUTH_TOKEN=your_token"
    echo "    PLIVO_FROM_NUMBER=+91XXXXXXXXXX"
    echo ""
    exit 1
fi
echo "  Plivo credentials loaded."

# ---- Step 3: Start PersonaPlex ----
echo "[3/4] Starting PersonaPlex server..."
SSL_DIR=$(mktemp -d)
python -m moshi.server --ssl "$SSL_DIR" > "$LOG_DIR/personaplex.log" 2>&1 &
PERSONA_PID=$!
echo "  PersonaPlex PID: $PERSONA_PID (port 8998)"

# Wait for PersonaPlex to be ready
echo "  Waiting for model to load (this takes ~30-60s on first run)..."
for i in $(seq 1 60); do
    if curl -sk https://localhost:8998/ >/dev/null 2>&1; then
        echo "  PersonaPlex is ready!"
        break
    fi
    if ! kill -0 $PERSONA_PID 2>/dev/null; then
        echo "  ERROR: PersonaPlex crashed! Check: tail -50 $LOG_DIR/personaplex.log"
        exit 1
    fi
    sleep 2
done

# ---- Step 4: Start Orchestrator ----
echo "[4/4] Starting Orchestrator..."
cd "$CALLER_DIR"
python orchestrator.py > "$LOG_DIR/orchestrator.log" 2>&1 &
ORCH_PID=$!
sleep 2

# Verify orchestrator is running
if curl -s http://localhost:3000/health >/dev/null 2>&1; then
    echo "  Orchestrator PID: $ORCH_PID (port 3000)"
else
    echo "  ERROR: Orchestrator failed! Check: tail -20 $LOG_DIR/orchestrator.log"
    exit 1
fi

echo ""
echo "=========================================="
echo " All services running!"
echo ""
echo " Orchestrator : http://localhost:3000"
echo " PersonaPlex  : wss://localhost:8998"
echo ""
echo " Logs:"
echo "   tail -f $LOG_DIR/personaplex.log"
echo "   tail -f $LOG_DIR/orchestrator.log"
echo ""
echo " Make a call:"
echo "   curl -X POST http://localhost:3000/call \\"
echo "     -H 'Content-Type: application/json' \\"
echo "     -d '{\"phone\": \"+91XXXXXXXXXX\"}'"
echo ""
echo " Stop everything:"
echo "   pkill -f 'moshi.server'; pkill -f orchestrator.py"
echo "=========================================="

# Keep script running so Ctrl+C stops everything
wait
