#!/usr/bin/env bash
# 执行一条命令：终端照常输出，同时追加写入 logs/。
# 用法: ./scripts/run_with_log.sh <日志目录> <命令> [参数...]
set -euo pipefail
LOGDIR="${1:?need log directory}"
shift
mkdir -p "$LOGDIR"
STAMP=$(date +%Y%m%d_%H%M%S)
RID="${STAMP}_$$"
LOGF="$LOGDIR/run_${RID}.log"
TS() { date -Iseconds 2>/dev/null || date; }
{
  echo "======== $(TS) ========"
  echo "host: $(hostname 2>/dev/null || echo unknown)"
  echo "pwd:  $(pwd)"
  printf "cmd:  "
  printf "%q " "$@"
  echo
  echo
} | tee -a "$LOGF" "$LOGDIR/pipeline.log"
T0=$(date +%s)
set +e
"$@" 2>&1 | tee -a "$LOGF"
EX=$?
set -e
T1=$(date +%s)
DUR=$((T1 - T0))
{
  echo ""
  echo "---- end $(TS) ----"
  echo "exit=$EX  duration_s=$DUR"
} | tee -a "$LOGF" "$LOGDIR/pipeline.log"
{ printf "%s  exit=%s  duration_s=%s  " "$(TS)" "$EX" "$DUR"; printf "%q " "$@"; echo; } >> "$LOGDIR/00_timings.txt"
{
  echo ""
  echo "# $(TS)"
  printf "%q " "$@"
  echo
} >> "$LOGDIR/00_commands.txt"
exit "$EX"
