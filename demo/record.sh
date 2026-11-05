#!/usr/bin/env bash
# Record demo/run_demo.sh as an asciinema cast, then render a 1080p MP4.
#   HERMES_SRC=/path/to/hermes-agent demo/record.sh
# Needs: asciinema (2.x), python with pyte + pillow, ffmpeg.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/out"; mkdir -p "$OUT"
ASCIINEMA="${ASCIINEMA:-asciinema}"
RENDER_PY="${RENDER_PY:-python3}"
"$ASCIINEMA" rec --overwrite --cols 118 --rows 33 -q -c "bash $HERE/run_demo.sh" "$OUT/trust-ladder-demo.cast"
"$RENDER_PY" "$HERE/render.py" "$OUT/trust-ladder-demo.cast" "$HERE/trust-ladder-demo.mp4" --idle 1.6
