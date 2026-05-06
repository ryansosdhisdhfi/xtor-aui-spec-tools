#!/usr/bin/env bash
# Run a1-convert with background diagnostics (OOM / memory / dmesg correlation).
# Usage (WSL):
#   export REPO="$(pwd)"; source .venv/bin/activate
#   STEM=NCB-PCI_Express_Base_6.1 bash scripts/run_a1_with_diags.sh
# Detached (IDE-safe):
#   nohup env STEM=... bash scripts/run_a1_with_diags.sh >logs/a1_diag_console.log 2>&1 &
#
# Artifacts under logs/diag_<STEM>_<timestamp>/:
#   00_baseline.txt  resource.log  dmesg_follow.log (if allowed)  dmesg_end.txt
#   make.stdout.log  monitors.pids
#
# Optional: in a second Windows PowerShell window run (host memory for WSL VM):
#   powershell -File scripts/win_vmmem_sample.ps1
set -euo pipefail
PART="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PART"
export REPO="${REPO:-$PART}"
export PYTHONUNBUFFERED=1
STEM="${STEM:-}"
if [[ -z "$STEM" ]]; then
  echo "Set STEM=<input basename without .pdf>" >&2
  exit 1
fi
if [[ ! -f "input/${STEM}.pdf" ]]; then
  echo "Missing input/${STEM}.pdf" >&2
  exit 1
fi
if [[ -f .venv/bin/activate ]]; then
  # shellcheck source=/dev/null
  source .venv/bin/activate
fi

STAMP=$(date +%Y%m%d_%H%M%S)
DIAG="logs/diag_${STEM}_${STAMP}"
mkdir -p "$DIAG" logs
echo "$DIAG" > logs/last_diag_dir.txt

MON_PIDS=()
stop_monitors() {
  for p in "${MON_PIDS[@]:-}"; do
    kill "$p" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}

capture_end() {
  {
    echo "---- $(date -Is) ----"
    free -h
    command -v nvidia-smi >/dev/null && nvidia-smi
    echo "---- meminfo tail ----"
    tail -20 /proc/meminfo 2>/dev/null || true
    echo "---- dmesg tail (oom / kill) ----"
    dmesg -T 2>/dev/null | tail -200 || true
  } >"$DIAG/dmesg_end.txt" 2>&1
}

{
  echo "date: $(date -Is)"
  uname -a
  command -v wsl.exe >/dev/null && wsl.exe --version 2>/dev/null || true
  ulimit -a
  free -h
  command -v nvidia-smi >/dev/null && nvidia-smi
  echo "---- dmesg tail baseline ----"
  dmesg -T 2>/dev/null | tail -80 || true
} >"$DIAG/00_baseline.txt" 2>&1

# Resource sampler every 25s
(
  while true; do
    echo "==== $(date -Is) ====" >>"$DIAG/resource.log"
    free -h >>"$DIAG/resource.log" 2>&1
    ps aux --sort=-%mem | head -18 >>"$DIAG/resource.log" 2>&1
    sleep 25
  done
) &
MON_PIDS+=($!)

# GPU sample (if any)
if command -v nvidia-smi >/dev/null; then
  (
    while true; do
      echo "$(date -Is)" >>"$DIAG/nvidia.csv"
      nvidia-smi --query-gpu=timestamp,memory.used,memory.total,utilization.gpu --format=csv,noheader >>"$DIAG/nvidia.csv" 2>&1
      sleep 30
    done
  ) &
  MON_PIDS+=($!)
fi

# Kernel messages (search dmesg_follow / dmesg_end for "killed process" "oom" "Out of memory")
( dmesg -w 2>&1 | tee -a "$DIAG/dmesg_follow.log" ) &
MON_PIDS+=($!)

printf '%s\n' "${MON_PIDS[@]}" >"$DIAG/monitors.pids"
trap 'stop_monitors; capture_end' EXIT INT TERM

export DEVICE="${DEVICE:-cuda}"
export IMAGES_SCALE="${IMAGES_SCALE:-4.0}"
export FAST_A1="${FAST_A1:-0}"
export PROGRESS_INTERVAL="${PROGRESS_INTERVAL:-10}"
export TABLE_MODE="${TABLE_MODE:-accurate}"
export PICTURE_DESC="${PICTURE_DESC:-0}"
export IMAGE_MODE="${IMAGE_MODE:-referenced}"

# Foreground: same as make a1; tee copy for this session
set +e
set -o pipefail
make a1-convert STEM="$STEM" 2>&1 | tee "$DIAG/make.stdout.log"
X=$?
set +o pipefail
set -e
stop_monitors
capture_end
echo "Diagnostics: $DIAG  exit=$X"
echo "If job died, check: dmesg_end.txt (oom-kill), resource.log (mem climb), nvidia.csv"
exit "$X"
