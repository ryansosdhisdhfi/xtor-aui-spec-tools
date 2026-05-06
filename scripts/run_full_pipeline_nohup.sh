#!/usr/bin/env bash
# 在仓库根执行全量 make all，日志写入 logs/full_rebuild_<时间>.log
set -eu -o pipefail
PART="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PART"
export REPO="$PART"
set -a
# shellcheck source=/dev/null
source "${PART}/secrets.sh"
set +a
make check
LOG="${PART}/logs/full_rebuild_$(date +%Y%m%d_%H%M%S).log"
echo "日志: $LOG"
nohup make all >"$LOG" 2>&1 &
echo "已后台启动 make all，PID=$!"
