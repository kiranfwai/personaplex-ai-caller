#!/bin/bash
# =============================================================
# Generate voice samples for all PersonaPlex voices
# Run on RunPod: bash /workspace/caller/generate_voice_samples.sh
# =============================================================

OUTPUT_DIR="/workspace/voice_samples"
mkdir -p "$OUTPUT_DIR"

TEXT_PROMPT="Hello, I am Rahul from Freedom With AI. I am calling you regarding the masterclass you attended recently. How are you doing today?"

# Generate 5 seconds of silence as input (simulates user being quiet)
python -c "
import numpy as np, wave
sr = 24000
duration = 5
samples = np.zeros(int(sr * duration), dtype=np.int16)
with wave.open('/tmp/silence.wav', 'w') as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(sr)
    w.writeframes(samples.tobytes())
print('Created silence.wav')
"

echo "=========================================="
echo " Generating voice samples for all voices"
echo "=========================================="

# Variety voices (second list on research page)
VARIETY_VOICES="VARF0 VARF1 VARF2 VARF3 VARF4 VARM0 VARM1 VARM2 VARM3"

# Natural voices (first list)
NATURAL_VOICES="NATF0 NATF1 NATF2 NATF3 NATM0 NATM1 NATM2 NATM3"

ALL_VOICES="$VARIETY_VOICES $NATURAL_VOICES"

echo ""
echo "--- Variety Voices (VAR) ---"
for voice in $VARIETY_VOICES; do
    echo ""
    echo "Generating: $voice ..."
    python -m moshi.offline \
        --voice-prompt "${voice}.pt" \
        --text-prompt "$TEXT_PROMPT" \
        --input-wav "/tmp/silence.wav" \
        --output-wav "$OUTPUT_DIR/${voice}.wav" \
        --output-text "$OUTPUT_DIR/${voice}.json" \
        2>&1 | tail -3

    if [ -f "$OUTPUT_DIR/${voice}.wav" ]; then
        echo "  Done: $OUTPUT_DIR/${voice}.wav"
    else
        echo "  FAILED: $voice"
    fi
done

echo ""
echo "--- Natural Voices (NAT) ---"
for voice in $NATURAL_VOICES; do
    echo ""
    echo "Generating: $voice ..."
    python -m moshi.offline \
        --voice-prompt "${voice}.pt" \
        --text-prompt "$TEXT_PROMPT" \
        --input-wav "/tmp/silence.wav" \
        --output-wav "$OUTPUT_DIR/${voice}.wav" \
        --output-text "$OUTPUT_DIR/${voice}.json" \
        2>&1 | tail -3

    if [ -f "$OUTPUT_DIR/${voice}.wav" ]; then
        echo "  Done: $OUTPUT_DIR/${voice}.wav"
    else
        echo "  FAILED: $voice"
    fi
done

echo ""
echo "=========================================="
echo " All samples generated in: $OUTPUT_DIR"
echo "=========================================="
ls -la "$OUTPUT_DIR"/*.wav 2>/dev/null
echo ""
echo " To listen, copy to local:"
echo "   scp -P <PORT> -i ~/.ssh/id_ed25519 root@<IP>:$OUTPUT_DIR/*.wav ."
echo ""
echo " Or download via RunPod proxy:"
echo "   Add a /download endpoint or use 'python -m http.server 8080' in $OUTPUT_DIR"
