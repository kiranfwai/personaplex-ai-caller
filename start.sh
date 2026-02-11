#!/bin/bash
# =============================================================
# Start all services — Run this to launch the full system
# =============================================================
set -e

WORKSPACE="/workspace"
CALLER_DIR="$WORKSPACE/caller"
PERSONA_DIR="$WORKSPACE/personaplex"
LOG_DIR="$WORKSPACE/logs"

mkdir -p "$LOG_DIR"

echo "=========================================="
echo " Starting PersonaPlex Caller System"
echo "=========================================="

# 1. Start PersonaPlex server
echo "[1/3] Starting PersonaPlex server..."
cd "$PERSONA_DIR"
SSL_DIR=$(mktemp -d)
python -m moshi.server --ssl "$SSL_DIR" \
    > "$LOG_DIR/personaplex.log" 2>&1 &
PERSONA_PID=$!
echo "  PersonaPlex PID: $PERSONA_PID (port 8998)"

# Wait for PersonaPlex to be ready
echo "  Waiting for PersonaPlex to load model..."
sleep 30  # Model loading takes time on first run

# 2. Start Bridge service
echo "[2/3] Starting Bridge service..."
cd "$CALLER_DIR"
python bridge.py > "$LOG_DIR/bridge.log" 2>&1 &
BRIDGE_PID=$!
echo "  Bridge PID: $BRIDGE_PID (port 8080)"

sleep 2

# 3. Start Orchestrator
echo "[3/3] Starting Orchestrator..."
python orchestrator.py > "$LOG_DIR/orchestrator.log" 2>&1 &
ORCH_PID=$!
echo "  Orchestrator PID: $ORCH_PID (port 3000)"

echo ""
echo "=========================================="
echo " All services running!"
echo ""
echo " Orchestrator : http://YOUR_IP:3000"
echo " Bridge WS    : ws://YOUR_IP:8080"
echo " PersonaPlex  : wss://localhost:8998"
echo ""
echo " Logs:"
echo "   tail -f $LOG_DIR/personaplex.log"
echo "   tail -f $LOG_DIR/bridge.log"
echo "   tail -f $LOG_DIR/orchestrator.log"
echo ""
echo " To make a call:"
echo '   curl -X POST http://YOUR_IP:3000/call \'
echo '     -H "Content-Type: application/json" \'
echo '     -d '"'"'{"phone": "+91XXXXXXXXXX"}'"'"
echo "=========================================="

# Keep script running
wait
