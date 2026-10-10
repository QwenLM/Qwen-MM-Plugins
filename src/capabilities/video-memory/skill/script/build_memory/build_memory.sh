#!/bin/bash
# Build graph memory for a single video or a directory of videos.
#
# Usage:
#   bash build_memory.sh /path/to/video.mp4
#   bash build_memory.sh /path/to/video.mp4 --output-dir /path/to/memory
#   bash build_memory.sh --video-dir /path/to/videos/
#   bash build_memory.sh --video-dir /path/to/videos/ --output-dir /path/to/memory
#
# Options:
#   --output-dir DIR   Output directory (default: <video_path>.memory/)
#   --model NAME       Model name (default: QWEN_MM_API_VL_MODEL)
#   --api-key KEY      DashScope API key (default: $DASHSCOPE_API_KEY)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Defaults ──────────────────────────────────────────────────────────────
MODEL=""
VIDEO_PATH=""
VIDEO_DIR=""
OUTPUT_DIR=""
API_KEY=""
P2_WORKERS=10
CHUNK_SEC=3600
ENABLE_ASR=1
ASR_MODEL="qwen3-asr-flash"

# ── Parse args ────────────────────────────────────────────────────────────
POSITIONAL=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --model)       MODEL="$2"; shift 2 ;;
        --video-dir)   VIDEO_DIR="$2"; shift 2 ;;
        --output-dir)  OUTPUT_DIR="$2"; shift 2 ;;
        --api-key)     API_KEY="$2"; shift 2 ;;
        --p2-workers)  P2_WORKERS="$2"; shift 2 ;;
        --chunk-sec)   CHUNK_SEC="$2"; shift 2 ;;
        --no-asr)          ENABLE_ASR=0; shift ;;
        --asr-model)       ASR_MODEL="$2"; shift 2 ;;
        -*)
            echo "Unknown option: $1"
            echo "Usage: bash build_memory.sh [VIDEO_PATH] [--video-dir DIR] [--output-dir DIR] [--model NAME] [--api-key KEY]"
            exit 1
            ;;
        *)  POSITIONAL+=("$1"); shift ;;
    esac
done

if [[ ${#POSITIONAL[@]} -gt 0 ]]; then
    VIDEO_PATH="${POSITIONAL[0]}"
fi

# ── ASR CLI flags — computed once so EVERY build path honors --no-asr / --asr-model.
# ASR runs inside Phase 1 (parallel with segmentation), so these attach to the Phase-1 chunk
# command and to the directory-mode full build — NOT to Phase 3, which does no ASR.
if [[ $ENABLE_ASR -eq 1 ]]; then
    ASR_FLAGS="--asr --asr-model $ASR_MODEL"
else
    ASR_FLAGS="--no-asr"
fi

# ── Environment ───────────────────────────────────────────────────────────
if [[ -n "$API_KEY" ]]; then
    export DASHSCOPE_API_KEY="$API_KEY"
fi
# ── Install Python dependencies (if missing) ────────────────────────────
_MISSING_PKGS=()
python -c "import cv2" 2>/dev/null || _MISSING_PKGS+=(opencv-python-headless)
python -c "import numpy" 2>/dev/null || _MISSING_PKGS+=('numpy<2')
python -c "import dashscope" 2>/dev/null || _MISSING_PKGS+=(dashscope)
_PYDANTIC_CHECK='import pydantic; assert (2, 11) <= tuple(map(int, pydantic.__version__.split(".")[:2])) < (3, 0)'
python -c "$_PYDANTIC_CHECK" 2>/dev/null || _MISSING_PKGS+=('pydantic>=2.11,<3')

if [[ ${#_MISSING_PKGS[@]} -gt 0 ]]; then
    echo "Installing missing packages: ${_MISSING_PKGS[*]}"
    python -m pip install -q -i https://mirrors.aliyun.com/pypi/simple/ --timeout 60 "${_MISSING_PKGS[@]}" || {
        echo "WARNING: pip install failed for: ${_MISSING_PKGS[*]}"
    }
fi

if [[ $ENABLE_ASR -eq 1 ]]; then
    if ! python -c "import dashscope" 2>/dev/null; then
        echo "ERROR: ASR is enabled but 'dashscope' package is not installed and install failed."
        echo "  Either install it manually (pip install dashscope) or disable ASR with --no-asr"
        exit 1
    fi
fi

export PYTHONUNBUFFERED=1
# Run the build modules as flat top-level modules (no symlinks, no package name).
# All build files (schema.py, embeddings.py, build_graph.py, ...) live in this one
# directory, so putting it on PYTHONPATH lets `python -m build_graph` import its siblings.
export PYTHONPATH="${SCRIPT_DIR}:${PYTHONPATH:-}"

# The portable reader needs Pydantic before reading file-only settings. Keep credentials in
# the reader instead of exporting config-file contents into a stale environment snapshot.
if ! python -c "$_PYDANTIC_CHECK" 2>/dev/null; then
    echo 'ERROR: configuration requires pydantic>=2.11,<3 in the Python environment used for this build.'
    exit 1
fi
python - <<'PYCONFIG'
import sys
from env_config import get_env
if not get_env("DASHSCOPE_API_KEY"):
    sys.exit("ERROR: DASHSCOPE_API_KEY not set. Pass --api-key, export it, or set it in ~/.qwen-mm-plugins/config.")
PYCONFIG
if [[ -z "$MODEL" ]]; then
    MODEL=$(python -c 'from env_config import get_env; print(get_env("QWEN_MM_API_VL_MODEL"))')
fi

NODE=$(hostname)

# ══════════════════════════════════════════════════════════════════════════
# Single video — parallel chunk mode
# ══════════════════════════════════════════════════════════════════════════
if [[ -n "$VIDEO_PATH" ]]; then
    if [[ ! -f "$VIDEO_PATH" ]]; then
        echo "ERROR: Video file not found: $VIDEO_PATH"
        exit 1
    fi

    if [[ -z "$OUTPUT_DIR" ]]; then
        OUTPUT_DIR="${VIDEO_PATH}.memory"
    fi
    mkdir -p "$OUTPUT_DIR"

    # Raise fd limit to avoid "Too many open files"
    ulimit -n 65536 2>/dev/null || true

    if [[ -f "$OUTPUT_DIR/graph_memory.json" && -f "$OUTPUT_DIR/embeddings.npz" ]]; then
        echo "graph_memory.json and embeddings.npz already exist, skipping"
        exit 0
    fi
    if [[ -f "$OUTPUT_DIR/graph_memory.json" ]]; then
        echo "graph_memory.json exists but embeddings.npz is missing; rebuilding embeddings"
        python -u -m build_graph "$VIDEO_PATH" --output-dir "$OUTPUT_DIR" --embeddings-only
        exit 0
    fi

    # Get video duration
    DURATION=$(python -c "
import sys; sys.path.insert(0, '$SCRIPT_DIR')
from build_graph import get_video_duration
print(int(get_video_duration('$VIDEO_PATH')))
")

    # Calculate number of chunks
    N_CHUNKS=$(( (DURATION + CHUNK_SEC - 1) / CHUNK_SEC ))
    if [[ $N_CHUNKS -lt 1 ]]; then
        N_CHUNKS=1
    fi

    # Workers per chunk (at least 1)
    WORKERS_PER_CHUNK=$(( (P2_WORKERS + N_CHUNKS - 1) / N_CHUNKS ))
    if [[ $WORKERS_PER_CHUNK -lt 1 ]]; then
        WORKERS_PER_CHUNK=1
    fi

    echo "============================================================"
    echo "  Build Graph Memory — Parallel Chunk Mode"
    echo "============================================================"
    echo "  Video:      $VIDEO_PATH"
    echo "  Duration:   ${DURATION}s ($((DURATION/60))min)"
    echo "  Output:     $OUTPUT_DIR"
    echo "  Model:      $MODEL"
    echo "  Chunks:     $N_CHUNKS × ${CHUNK_SEC}s"
    echo "  P2 Workers: $P2_WORKERS total ($WORKERS_PER_CHUNK per chunk)"
    echo "  ASR:        $([ $ENABLE_ASR -eq 1 ] && echo 'ON ('$ASR_MODEL')' || echo 'OFF')"
    echo "  Node:       $NODE"
    echo "============================================================"

    # ── Launch parallel Phase 1 + Phase 2 per chunk ──────────────────────
    P1_PIDS=()
    P2_PIDS=()
    CHUNK_DIRS=()

    for (( i=0; i<N_CHUNKS; i++ )); do
        START_SEC=$(( i * CHUNK_SEC ))
        END_SEC=$(( (i + 1) * CHUNK_SEC ))
        if [[ $END_SEC -gt $DURATION ]]; then
            END_SEC=$DURATION
        fi

        CHUNK_DIR="$OUTPUT_DIR/chunk_$(printf '%02d' $i)"
        mkdir -p "$CHUNK_DIR"
        CHUNK_DIRS+=("$CHUNK_DIR")

        # Skip chunk if already done
        if [[ -f "$CHUNK_DIR/02_done" ]]; then
            echo "[chunk_$(printf '%02d' $i)] Already complete, skipping"
            P1_PIDS+=("0")
            P2_PIDS+=("0")
            continue
        fi

        echo ""
        echo "[chunk_$(printf '%02d' $i)] ${START_SEC}s - ${END_SEC}s"

        # Phase 1 for this chunk
        python -u -m build_graph \
            "$VIDEO_PATH" \
            --output-dir "$CHUNK_DIR" \
            --model "$MODEL" \
            --phase1-only \
            --start-sec "$START_SEC" \
            --end-sec "$END_SEC" \
            $ASR_FLAGS &
        _P1_PID=$!
        P1_PIDS+=("$_P1_PID")

        # Phase 2 worker for this chunk
        python -u -m pipeline_worker \
            "$VIDEO_PATH" \
            --output-dir "$CHUNK_DIR" \
            --model "$MODEL" \
            --num-workers "$WORKERS_PER_CHUNK" \
            --p1-pid "$_P1_PID" &
        P2_PIDS+=("$!")
    done

    echo ""
    echo "[PARALLEL] Launched $N_CHUNKS chunk pairs (Phase 1 + Phase 2)"

    # ── Wait for all Phase 1 processes ───────────────────────────────────
    ANY_FAIL=0
    for (( i=0; i<N_CHUNKS; i++ )); do
        pid=${P1_PIDS[$i]}
        [[ "$pid" == "0" ]] && continue
        if ! wait "$pid"; then
            echo "[chunk_$(printf '%02d' $i)] Phase 1 FAILED"
            ANY_FAIL=1
        fi
    done

    if [[ $ANY_FAIL -ne 0 ]]; then
        echo "ERROR: One or more Phase 1 chunks failed, killing Phase 2 workers..."
        for pid in "${P2_PIDS[@]}"; do
            [[ "$pid" == "0" ]] && continue
            kill "$pid" 2>/dev/null || true
        done
        wait 2>/dev/null
        exit 1
    fi
    echo "[PARALLEL] All Phase 1 chunks complete"

    # ── Wait for all Phase 2 processes ───────────────────────────────────
    for (( i=0; i<N_CHUNKS; i++ )); do
        pid=${P2_PIDS[$i]}
        [[ "$pid" == "0" ]] && continue
        if ! wait "$pid"; then
            echo "[chunk_$(printf '%02d' $i)] Phase 2 FAILED"
            ANY_FAIL=1
        fi
    done

    if [[ $ANY_FAIL -ne 0 ]]; then
        echo "ERROR: One or more Phase 2 chunks failed"
        exit 1
    fi
    echo "[PARALLEL] All Phase 2 chunks complete"

    # ── Merge chunks into main output dir ────────────────────────────────
    echo ""
    echo "[MERGE] Merging $N_CHUNKS chunks..."

    # Merge each chunk's macros + subgraphs into $OUTPUT_DIR (global re-sort + id remap);
    # logic lives in build_graph.merge_chunks so it's unit-tested, not a bash heredoc.
    python -u -m build_graph "$VIDEO_PATH" --output-dir "$OUTPUT_DIR" --merge-chunks "${CHUNK_DIRS[@]}"

    # ── Phase 3: Aggregation + Embeddings ────────────────────────────────
    echo ""
    echo "[PHASE 3] Starting aggregation + embeddings..."
    python -u -m build_graph \
        "$VIDEO_PATH" \
        --output-dir "$OUTPUT_DIR" \
        --model "$MODEL" \
        --phase3-only

    echo ""
    echo "============================================================"
    echo "  Done on $NODE at $(date '+%Y-%m-%d %H:%M:%S')"
    echo "============================================================"
    exit 0
fi

# ══════════════════════════════════════════════════════════════════════════
# Directory of videos (sequential, one by one)
# ══════════════════════════════════════════════════════════════════════════
if [[ -n "$VIDEO_DIR" ]]; then
    if [[ ! -d "$VIDEO_DIR" ]]; then
        echo "ERROR: Video directory not found: $VIDEO_DIR"
        exit 1
    fi

    VIDEOS=()
    while IFS= read -r -d '' f; do
        VIDEOS+=("$f")
    done < <(find "$VIDEO_DIR" -maxdepth 1 -type f \( -name '*.mp4' -o -name '*.mkv' -o -name '*.avi' -o -name '*.mov' -o -name '*.webm' \) -print0 | sort -z)

    if [[ ${#VIDEOS[@]} -eq 0 ]]; then
        echo "ERROR: No video files found in $VIDEO_DIR"
        exit 1
    fi

    TIMESTAMP=$(date '+%Y%m%d_%H%M%S')

    echo "============================================================"
    echo "  Build Graph Memory — Video Directory"
    echo "============================================================"
    echo "  Video dir:  $VIDEO_DIR"
    echo "  Videos:     ${#VIDEOS[@]}"
    echo "  Output:     $OUTPUT_DIR"
    echo "  Model:      $MODEL"
    echo "  Node:       $NODE"
    echo "============================================================"

    DONE=0
    FAILED=0
    for v in "${VIDEOS[@]}"; do
        VIDEO_STEM=$(basename "$v" | sed 's/\.[^.]*$//')
        if [[ -z "$OUTPUT_DIR" ]]; then
            VIDEO_OUT="${v}.memory"
        else
            VIDEO_OUT="$OUTPUT_DIR/$VIDEO_STEM"
        fi

        if [[ -f "$VIDEO_OUT/graph_memory.json" && -f "$VIDEO_OUT/embeddings.npz" ]]; then
            echo "[skip] $VIDEO_STEM — graph_memory.json and embeddings.npz already exist"
            DONE=$((DONE + 1))
            continue
        fi

        if [[ -f "$VIDEO_OUT/graph_memory.json" ]]; then
            echo "[recover] $VIDEO_STEM — rebuilding missing embeddings.npz"
            if python -u -m build_graph \
                "$v" \
                --output-dir "$VIDEO_OUT" \
                --embeddings-only \
                2>&1 | tee "$VIDEO_OUT/build_${TIMESTAMP}.log"; then
                DONE=$((DONE + 1))
                echo "[done] $VIDEO_STEM"
            else
                FAILED=$((FAILED + 1))
                echo "[FAIL] $VIDEO_STEM"
            fi
            continue
        fi

        echo ""
        echo "[build] $VIDEO_STEM ..."
        mkdir -p "$VIDEO_OUT"

        if python -u -m build_graph \
            "$v" \
            --output-dir "$VIDEO_OUT" \
            --model "$MODEL" \
            $ASR_FLAGS \
            2>&1 | tee "$VIDEO_OUT/build_${TIMESTAMP}.log"; then
            DONE=$((DONE + 1))
            echo "[done] $VIDEO_STEM"
        else
            FAILED=$((FAILED + 1))
            echo "[FAIL] $VIDEO_STEM"
        fi
    done

    echo ""
    echo "============================================================"
    echo "  Done: $DONE  Failed: $FAILED  Total: ${#VIDEOS[@]}"
    echo "  $NODE at $(date '+%Y-%m-%d %H:%M:%S')"
    echo "============================================================"
    [[ $FAILED -eq 0 ]] || exit 1
    exit 0
fi

echo "ERROR: Provide a video path or --video-dir."
echo "Usage: bash build_memory.sh [VIDEO_PATH] [--video-dir DIR] [--output-dir DIR] [--model NAME] [--api-key KEY]"
exit 1
