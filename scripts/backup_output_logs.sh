#!/usr/bin/env bash
# 将 output/ 与 logs/ 移到 archive/ 下带时间戳的目录，并重建空目录供全量重跑。
#
# archive/ 为本地历史备份，勿随「清理、重跑」一起删除；仅当你明确要腾出磁盘时再手动处理。
# 全量重跑前清理：只动 output/ 与 logs/，或先跑本脚本再 make。
#
# 用法: 在 xtor-aui-spec-tools 根目录: bash scripts/backup_output_logs.sh
set -eu -o pipefail
PART="$(cd "$(dirname "$0")/.." && pwd)"
STAMP="$(date +%Y%m%d_%H%M%S)"
ARCH="${PART}/archive"
mkdir -p "$ARCH"
cd "$PART"
if [[ -d output ]]; then
  mv output "${ARCH}/output_${STAMP}"
  echo "已备份: ${ARCH}/output_${STAMP}"
fi
if [[ -d logs ]]; then
  mv logs "${ARCH}/logs_${STAMP}"
  echo "已备份: ${ARCH}/logs_${STAMP}"
fi
mkdir -p output logs
touch "${PART}/logs/00_timings.txt" "${PART}/logs/00_commands.txt" 2>/dev/null || true
echo "已新建空 output/ 与 logs/。可 export REPO=\"\$(pwd)\" && make check && make all"
