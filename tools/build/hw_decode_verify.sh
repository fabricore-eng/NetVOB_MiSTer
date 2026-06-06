#!/usr/bin/env bash
# hw_decode_verify.sh — OBJECTIVE decode-verdict for NetVOB_MiSTer MPEG-2 HW decoder.
#
# Compares a raw HW framestore DDR dump against the mpeg2fpga SIM reference set
# and emits VERDICT=PASS or VERDICT=FAIL. Exit 0 = PASS, exit 1 = FAIL.
#
# This is the MILESTONE HARD GATE: no claim on a clean decode without a PASS here.
#
# Usage:
#   hw_decode_verify.sh <HW_FRAMESTORE.bin> [options]
#
# Options:
#   --out-dir DIR      scratch dir for rendered PNGs (default: /tmp/hw_verify_<pid>)
#   --keep             keep the scratch dir after exit (default: delete on success)
#   --json             also emit a machine-readable JSON summary to stdout
#   --log-cmd CMD      if PASS, print a ready-to-run dell_coord.sh testlog line
#   --ssim-pass N      full-frame SSIM threshold for PASS (default: 0.95)
#   --diff-pass N      %pixels-differing threshold for PASS (default: 2.0)
#   --bars-ssim N      bars cross-check SSIM warn threshold (default: 0.85)
#   --quiet            suppress per-reference-frame detail lines
#   --help             show this message
#
# Pipeline:
#   1. render_framestore.py  -> FRAME_0..3 PNGs (flip+bias defaults: CONFIRMED correct)
#   2. auto-pick decoded slot (highest stddev among plausible frames: 5<mean<250, stddev>8)
#   3. frame_diff.py HW-frame vs EACH ref in decode_ref_set/ref_frame_NN.png
#      -> take BEST (highest) SSIM across all refs (handles testsrc2 animation phase)
#   4. bars cross-check: crop HW-frame to 120,0,720,480 vs sim_ref_full_frame.png bars crop
#      -> informational SSIM (should be >=0.85 for any valid full frame)
#   5. VERDICT=PASS iff best-SSIM>=ssim_pass AND %diff<=diff_pass
#
# Reference set:
#   core/sim/artifacts/decode_ref_set/ref_frame_01..06.png
#   (6 distinct testsrc2 animation phases, all 720x480 grayscale Y-plane, mpeg2fpga sim)
#   ref_frame_01.png Y-md5 868c34a8 = sim_ref_full_frame.png (the committed golden ref)
#
# Known-good validation:
#   /tmp/fs_breakthrough.bin (partial frame: only top ~120 rows decoded)
#   -> full-frame best-SSIM ~0.25  -> VERDICT=FAIL  (correct: partial frame must FAIL)
#   -> bars top-rows SSIM    ~1.00 (decoded rows are pixel-perfect vs sim)
#
# Dependencies (all present in this repo's dev environment):
#   python3, PIL/Pillow, numpy
#   tools/build/render_framestore.py   (this repo)
#   mister-dev-hub/tools/frame_diff.py (hub tool)
#
# Authors: NetVOB_MiSTer project (noreply@fabricore.ai)
# =============================================================================
set -euo pipefail

# --------------------------------------------------------------------------- #
# Locate repo root and hub root (tolerates running from any directory).
# --------------------------------------------------------------------------- #
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
HUB_ROOT="${HUB_ROOT:-${HOME}/Dev/mister-dev-hub}"

RENDER_PY="${REPO_ROOT}/tools/build/render_framestore.py"
FRAME_DIFF_PY="${HUB_ROOT}/tools/frame_diff.py"
REF_DIR="${REPO_ROOT}/core/sim/artifacts/decode_ref_set"
FULL_REF="${REPO_ROOT}/core/sim/artifacts/sim_ref_full_frame.png"

# --------------------------------------------------------------------------- #
# Defaults
# --------------------------------------------------------------------------- #
HW_BIN=""
OUT_DIR=""
KEEP_OUT=0
EMIT_JSON=0
LOG_CMD=0
SSIM_PASS=0.95
DIFF_PASS=2.0
BARS_SSIM_WARN=0.85
QUIET=0

# --------------------------------------------------------------------------- #
# Parse args
# --------------------------------------------------------------------------- #
while [[ $# -gt 0 ]]; do
    case "$1" in
        --out-dir)  OUT_DIR="$2"; shift 2 ;;
        --keep)     KEEP_OUT=1; shift ;;
        --json)     EMIT_JSON=1; shift ;;
        --log-cmd)  LOG_CMD=1; shift ;;
        --ssim-pass) SSIM_PASS="$2"; shift 2 ;;
        --diff-pass) DIFF_PASS="$2"; shift 2 ;;
        --bars-ssim) BARS_SSIM_WARN="$2"; shift 2 ;;
        --quiet)    QUIET=1; shift ;;
        --help|-h)
            sed -n '2,50p' "${BASH_SOURCE[0]}" | grep '^#' | sed 's/^# \?//'
            exit 0 ;;
        -*)
            echo "hw_decode_verify: unknown option: $1" >&2; exit 1 ;;
        *)
            if [[ -z "${HW_BIN}" ]]; then HW_BIN="$1"
            else echo "hw_decode_verify: unexpected argument: $1" >&2; exit 1; fi
            shift ;;
    esac
done

if [[ -z "${HW_BIN}" ]]; then
    echo "Usage: hw_decode_verify.sh <HW_FRAMESTORE.bin> [options]" >&2
    echo "       Run with --help for full usage." >&2
    exit 1
fi

# --------------------------------------------------------------------------- #
# Validate prerequisites
# --------------------------------------------------------------------------- #
err=0
[[ -f "${HW_BIN}" ]]         || { echo "ERROR: not found: ${HW_BIN}" >&2; err=1; }
[[ -f "${RENDER_PY}" ]]      || { echo "ERROR: not found: ${RENDER_PY}" >&2; err=1; }
[[ -f "${FRAME_DIFF_PY}" ]]  || { echo "ERROR: not found: ${FRAME_DIFF_PY}" >&2; err=1; }
[[ -d "${REF_DIR}" ]]        || { echo "ERROR: ref set missing: ${REF_DIR}" >&2; err=1; }
[[ -f "${FULL_REF}" ]]       || { echo "ERROR: not found: ${FULL_REF}" >&2; err=1; }
n_refs=$(ls "${REF_DIR}"/ref_frame_*.png 2>/dev/null | wc -l | tr -d ' ')
[[ "${n_refs}" -gt 0 ]]      || { echo "ERROR: no ref_frame_*.png in ${REF_DIR}" >&2; err=1; }
python3 -c "import PIL, numpy" 2>/dev/null || { echo "ERROR: python3 PIL/numpy not found" >&2; err=1; }
[[ "${err}" -eq 0 ]] || exit 1

# --------------------------------------------------------------------------- #
# Set up scratch dir
# --------------------------------------------------------------------------- #
if [[ -z "${OUT_DIR}" ]]; then
    OUT_DIR="/tmp/hw_verify_$$"
    KEEP_OUT=0
fi
mkdir -p "${OUT_DIR}"

cleanup() {
    if [[ "${KEEP_OUT}" -eq 0 ]]; then
        rm -rf "${OUT_DIR}"
    fi
}
trap cleanup EXIT

# --------------------------------------------------------------------------- #
# Step 1: Render the HW framestore dump -> FRAME_0..3 PNGs
# --------------------------------------------------------------------------- #
echo "=== hw_decode_verify.sh ==="
echo "HW bin : ${HW_BIN}  ($(wc -c <"${HW_BIN}" | tr -d ' ') bytes)"
echo "Refs   : ${n_refs} frames in ${REF_DIR}"
echo ""
echo "--- Step 1: render framestore ---"

RENDER_OUT="${OUT_DIR}/rendered"
mkdir -p "${RENDER_OUT}"
python3 "${RENDER_PY}" "${HW_BIN}" "${RENDER_OUT}" 2>&1

# --------------------------------------------------------------------------- #
# Step 2: Auto-pick decoded frame slot (highest stddev, plausible range)
# --------------------------------------------------------------------------- #
echo ""
echo "--- Step 2: auto-pick decoded slot ---"

PICK_RESULT=$(python3 - <<'PYEOF'
import os, sys, struct, zlib
from PIL import Image
import numpy as np

rendered_dir = os.environ.get("RENDER_OUT", "/tmp/hw_verify_rendered")
best_slot = None
best_sd = -1.0
best_mean = 0.0

for n in range(4):
    p = os.path.join(rendered_dir, f"frame{n}_Y.png")
    if not os.path.isfile(p):
        continue
    data = np.array(Image.open(p).convert("L"), dtype=float)
    mean = data.mean()
    sd   = data.std()
    print(f"  FRAME_{n}: mean={mean:.1f} stddev={sd:.2f}")
    if sd > 8 and 5 < mean < 250:
        if sd > best_sd:
            best_sd   = sd
            best_slot = n
            best_mean = mean

if best_slot is None:
    print("PICK_SLOT=none", flush=True)
    print("PICK_MEAN=0.0", flush=True)
    print("PICK_SD=0.0", flush=True)
else:
    print(f"PICK_SLOT={best_slot}", flush=True)
    print(f"PICK_MEAN={best_mean:.2f}", flush=True)
    print(f"PICK_SD={best_sd:.2f}", flush=True)
PYEOF
)
export RENDER_OUT
PICK_RESULT=$(RENDER_OUT="${RENDER_OUT}" python3 - <<'PYEOF'
import os
from PIL import Image
import numpy as np

rendered_dir = os.environ["RENDER_OUT"]
best_slot = None
best_sd = -1.0
best_mean = 0.0

for n in range(4):
    p = os.path.join(rendered_dir, f"frame{n}_Y.png")
    if not os.path.isfile(p):
        continue
    data = np.array(Image.open(p).convert("L"), dtype=float)
    mean = data.mean()
    sd   = data.std()
    print(f"  FRAME_{n}: mean={mean:.1f} stddev={sd:.2f}")
    if sd > 8 and 5 < mean < 250:
        if sd > best_sd:
            best_sd   = sd
            best_slot = n
            best_mean = mean

if best_slot is None:
    print("PICK_SLOT=none")
    print("PICK_MEAN=0.0")
    print("PICK_SD=0.0")
else:
    print(f"PICK_SLOT={best_slot}")
    print(f"PICK_MEAN={best_mean:.2f}")
    print(f"PICK_SD={best_sd:.2f}")
PYEOF
)

echo "${PICK_RESULT}"

PICK_SLOT=$(echo "${PICK_RESULT}" | grep '^PICK_SLOT=' | cut -d= -f2)
PICK_MEAN=$(echo "${PICK_RESULT}" | grep '^PICK_MEAN=' | cut -d= -f2)
PICK_SD=$(echo  "${PICK_RESULT}" | grep '^PICK_SD='   | cut -d= -f2)

if [[ "${PICK_SLOT}" == "none" ]]; then
    echo ""
    echo "ERROR: no plausible decoded frame found in FRAME_0..3 (all flat or out of range)."
    echo "       This bin may be uninitialized or not contain a decoded frame."
    echo "VERDICT=FAIL  (no decodable frame slot found)"
    exit 1
fi

HW_FRAME_PNG="${RENDER_OUT}/frame${PICK_SLOT}_Y.png"
echo ""
echo "Selected FRAME_${PICK_SLOT} (mean=${PICK_MEAN} stddev=${PICK_SD})"

# --------------------------------------------------------------------------- #
# Step 3: Best-match SSIM across all reference frames
# --------------------------------------------------------------------------- #
echo ""
echo "--- Step 3: full-frame best-match SSIM (${n_refs} refs) ---"

BEST_SSIM="-1"
BEST_PCTDIFF="100"
BEST_REF=""
BEST_REF_MD5=""

for ref_png in "${REF_DIR}"/ref_frame_*.png; do
    ref_name=$(basename "${ref_png}")
    diff_result=$(python3 "${FRAME_DIFF_PY}" "${ref_png}" "${HW_FRAME_PNG}" --json 2>/dev/null) || true
    if [[ -z "${diff_result}" ]]; then
        [[ "${QUIET}" -eq 0 ]] && echo "  ${ref_name}: ERROR running frame_diff"
        continue
    fi
    ssim=$(echo "${diff_result}"      | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['ssim'])")
    pctdiff=$(echo "${diff_result}"   | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['pct_pixels_differing'])")
    verdict=$(echo "${diff_result}"   | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['verdict'])")
    [[ "${QUIET}" -eq 0 ]] && echo "  ${ref_name}: SSIM=${ssim}  %diff=${pctdiff}  ${verdict}"

    # Track best SSIM (Python float compare via awk)
    is_better=$(awk "BEGIN { print (${ssim} > ${BEST_SSIM}) ? \"1\" : \"0\" }")
    if [[ "${is_better}" == "1" ]]; then
        BEST_SSIM="${ssim}"
        BEST_PCTDIFF="${pctdiff}"
        BEST_REF="${ref_name}"
        # Compute Y-md5 of the best reference
        BEST_REF_MD5=$(python3 -c "
import hashlib; import numpy as np; from PIL import Image
data = np.array(Image.open('${ref_png}').convert('L'))
print(hashlib.md5(data.tobytes()).hexdigest())
")
    fi
done

echo ""
echo "  >>> Best match: ${BEST_REF}  SSIM=${BEST_SSIM}  %diff=${BEST_PCTDIFF}  ref-Y-md5=${BEST_REF_MD5}"

# --------------------------------------------------------------------------- #
# Step 4: Bars cross-check (cols 120-720, all rows) vs sim_ref_full_frame.png
# --------------------------------------------------------------------------- #
echo ""
echo "--- Step 4: bars cross-check (crop 120,0,720,480 vs sim_ref_full_frame.png) ---"

BARS_REF="${OUT_DIR}/sim_ref_bars_check.png"
python3 -c "
from PIL import Image
img = Image.open('${FULL_REF}').convert('L')
bars = img.crop((120, 0, 720, 480))
bars.save('${BARS_REF}')
"

BARS_RESULT=$(python3 "${FRAME_DIFF_PY}" "${BARS_REF}" "${HW_FRAME_PNG}" \
    --crop-test 120,0,720,480 --json 2>/dev/null) || true

BARS_SSIM="0"
if [[ -n "${BARS_RESULT}" ]]; then
    BARS_SSIM=$(echo "${BARS_RESULT}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['ssim'])")
    BARS_PCTDIFF=$(echo "${BARS_RESULT}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['pct_pixels_differing'])")
    echo "  bars SSIM=${BARS_SSIM}  %diff=${BARS_PCTDIFF}"
    bars_pass=$(awk "BEGIN { print (${BARS_SSIM} >= ${BARS_SSIM_WARN}) ? \"PASS\" : \"WARN\" }")
    echo "  bars cross-check: ${bars_pass}  (threshold: SSIM>=${BARS_SSIM_WARN})"
else
    echo "  bars cross-check: ERROR running frame_diff"
    bars_pass="WARN"
fi

# --------------------------------------------------------------------------- #
# Step 5: Compute VERDICT
# --------------------------------------------------------------------------- #
echo ""
echo "--- Step 5: VERDICT ---"

PASS_SSIM=$(awk "BEGIN { print (${BEST_SSIM} >= ${SSIM_PASS}) ? \"1\" : \"0\" }")
PASS_DIFF=$(awk "BEGIN { print (${BEST_PCTDIFF} <= ${DIFF_PASS}) ? \"1\" : \"0\" }")

if [[ "${PASS_SSIM}" == "1" && "${PASS_DIFF}" == "1" ]]; then
    VERDICT="PASS"
else
    VERDICT="FAIL"
fi

echo "  full-frame best SSIM : ${BEST_SSIM}  (need >= ${SSIM_PASS})"
echo "  full-frame %diff     : ${BEST_PCTDIFF}  (need <= ${DIFF_PASS})"
echo "  bars cross-check SSIM: ${BARS_SSIM}  (informational, warn < ${BARS_SSIM_WARN})"
echo "  matched reference    : ${BEST_REF}  (Y-md5 ${BEST_REF_MD5})"
echo ""
echo "  VERDICT=${VERDICT}"

if [[ "${VERDICT}" == "PASS" ]]; then
    echo ""
    echo "=== DECODE GATE: PASS ==="
    echo ""
    if [[ "${LOG_CMD}" -eq 1 ]]; then
        echo "Testlog command:"
        echo "  ~/Dev/mister-dev-hub/tools/dell_coord.sh testlog dvd mister \\"
        echo "    --data verify=PASS --data golden=md5:${BEST_REF_MD5} \\"
        echo "    \"clean full-frame decode\""
    fi
else
    echo ""
    echo "=== DECODE GATE: FAIL ==="
    echo ""
    echo "Interpretation:"
    if awk "BEGIN { exit !(${BEST_SSIM} < 0.5) }"; then
        echo "  SSIM=${BEST_SSIM} < 0.5 suggests severely partial or corrupt decode."
        echo "  Check: is the DDR dump from a stably-decoding frame? Is bias/flip correct?"
    elif awk "BEGIN { exit !(${BEST_SSIM} < ${SSIM_PASS}) }"; then
        echo "  SSIM=${BEST_SSIM} below threshold ${SSIM_PASS} — partial decode or RTL arithmetic error."
        echo "  Run with --keep to inspect rendered PNGs: ${RENDER_OUT}/frame${PICK_SLOT}_Y.png"
    fi
fi

# --------------------------------------------------------------------------- #
# Optional JSON output
# --------------------------------------------------------------------------- #
if [[ "${EMIT_JSON}" -eq 1 ]]; then
    echo ""
    echo "--- JSON summary ---"
    python3 - <<JEOF
import json, sys
d = {
    "hw_bin": "${HW_BIN}",
    "picked_frame_slot": ${PICK_SLOT},
    "picked_frame_mean": ${PICK_MEAN},
    "picked_frame_stddev": ${PICK_SD},
    "best_ref": "${BEST_REF}",
    "best_ref_y_md5": "${BEST_REF_MD5}",
    "best_ssim": ${BEST_SSIM},
    "best_pct_diff": ${BEST_PCTDIFF},
    "bars_ssim": ${BARS_SSIM},
    "ssim_pass_threshold": ${SSIM_PASS},
    "diff_pass_threshold": ${DIFF_PASS},
    "bars_warn_threshold": ${BARS_SSIM_WARN},
    "verdict": "${VERDICT}",
    "ref_set_dir": "${REF_DIR}",
    "n_refs": ${n_refs}
}
print(json.dumps(d, indent=2))
JEOF
fi

# --------------------------------------------------------------------------- #
# Exit code: 0 = PASS, 1 = FAIL
# --------------------------------------------------------------------------- #
if [[ "${VERDICT}" == "PASS" ]]; then
    [[ "${KEEP_OUT}" -eq 0 ]] && trap - EXIT  # let cleanup run
    exit 0
else
    [[ "${KEEP_OUT}" -eq 0 ]] && trap - EXIT  # let cleanup run
    exit 1
fi
