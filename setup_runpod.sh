#!/bin/bash
# =============================================================
# RunPod Setup Script — Run this ONCE after creating your pod
# =============================================================
set -e

echo "=========================================="
echo " PersonaPlex Caller — RunPod Setup"
echo "=========================================="

# 1. Install system dependencies
echo "[1/5] Installing system dependencies..."
apt-get update && apt-get install -y libopus-dev ffmpeg

# 2. Clone PersonaPlex
echo "[2/5] Cloning PersonaPlex..."
cd /workspace
if [ ! -d "personaplex" ]; then
    git clone https://github.com/NVIDIA/personaplex.git
fi
cd personaplex

# 3. Install PersonaPlex (Moshi)
echo "[3/5] Installing PersonaPlex..."
pip install moshi/.

# 4. Install bridge dependencies
echo "[4/5] Installing bridge service dependencies..."
pip install fastapi uvicorn[standard] websockets plivo numpy scipy audioop-lts python-multipart aiofiles

# 5. Copy project files to workspace
echo "[5/5] Setting up project files..."
mkdir -p /workspace/caller
echo ""
echo "=========================================="
echo " Setup complete!"
echo " "
echo " Next: copy your bridge.py, orchestrator.py,"
echo " and config.py to /workspace/caller/"
echo "=========================================="
