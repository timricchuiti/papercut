#!/usr/bin/env bash
# One-time setup for PaperCut's MLX transcription engine (the default).
#
# Builds `.venv-mlx` and converts nyrahealth/CrisperWhisper to MLX float16 at
# models/crisper-mlx-fp16/. MLX runs CrisperWhisper's weights ~15-30x faster than
# the transformers/MPS path; word timestamps use CrisperWhisper's own alignment
# heads (set at load time in mlx_transcribe.py), so they stay accurate.
#
# mlx_whisper is pinned to a commit whose loader reads model.safetensors (the
# format convert.py emits); the published pip release (0.4.3) does NOT, so do not
# `pip install mlx-whisper`. torch/transformers are only needed for the one-time
# conversion.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PIN="796f5b53cab69a3d48a44233ce21aae889e94a08"   # ml-explore/mlx-examples (safetensors-aware mlx_whisper)
MODEL_DIR="$HERE/models/crisper-mlx-fp16"
PY="$HERE/.venv-mlx/bin/python"

echo ">> venv + deps"
[ -d "$HERE/.venv-mlx" ] || /opt/homebrew/bin/python3.12 -m venv "$HERE/.venv-mlx"
"$PY" -m pip install -q --upgrade pip
"$PY" -m pip install -q "git+https://github.com/ml-explore/mlx-examples.git@${PIN}#subdirectory=whisper"
"$PY" -m pip install -q torch transformers huggingface_hub safetensors numba scipy tiktoken more-itertools

if [ -f "$MODEL_DIR/model.safetensors" ]; then
  echo ">> model already present: $MODEL_DIR"
else
  echo ">> converting CrisperWhisper -> MLX fp16 (downloads ~3GB once)"
  CLONE="$(mktemp -d)/mlx-examples"
  git clone --depth 1 -q https://github.com/ml-explore/mlx-examples.git "$CLONE"
  git -C "$CLONE" fetch -q --depth 1 origin "$PIN" 2>/dev/null && git -C "$CLONE" checkout -q FETCH_HEAD 2>/dev/null || true
  PYTHONPATH="$CLONE/whisper" "$PY" "$CLONE/whisper/convert.py" \
    --torch-name-or-path nyrahealth/CrisperWhisper \
    --mlx-path "$MODEL_DIR" --dtype float16
fi

echo ">> MLX engine ready: $MODEL_DIR"
