#!/usr/bin/env bash
# 执行一条命令：终端照常输出，同时追加写入 logs/。
# 用法: ./scripts/run_with_log.sh <日志目录> <命令> [参数...]
# 可选环境变量 LOG_STEP：make 目标名（如 a1-convert），会写入 run_*.log 文件名与 00_*.txt 每行，便于区分步骤。
set -euo pipefail
# 经管道写入 tee 时子进程 stdout 常非 TTY，Python 会块缓冲，终端与日志里长时间无新行
export PYTHONUNBUFFERED=1
LOGDIR="${1:?need log directory}"
shift
mkdir -p "$LOGDIR"
STAMP=$(date +%Y%m%d_%H%M%S)
RID="${STAMP}_$$"
STEP_TAG="${LOG_STEP:-}"
if [[ -n "$STEP_TAG" ]]; then
  LOGF="$LOGDIR/run_${RID}_${STEP_TAG}.log"
else
  LOGF="$LOGDIR/run_${RID}.log"
fi
TS() { date -Iseconds 2>/dev/null || date; }
{
  echo "======== $(TS) ========"
  [[ -n "$STEP_TAG" ]] && echo "step: $STEP_TAG"
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
  [[ -n "$STEP_TAG" ]] && echo "step: $STEP_TAG"
  echo "exit=$EX  duration_s=$DUR"
} | tee -a "$LOGF" "$LOGDIR/pipeline.log"
{ printf "%s  step=%s  exit=%s  duration_s=%s  " "$(TS)" "${STEP_TAG:--}" "$EX" "$DUR"; printf "%q " "$@"; echo; } >> "$LOGDIR/00_timings.txt"
{
  echo ""
  echo "# $(TS)  step=${STEP_TAG:--}"
  printf "%q " "$@"
  echo
} >> "$LOGDIR/00_commands.txt"
exit "$EX"
