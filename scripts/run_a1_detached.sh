#!/usr/bin/env bash
# 整本 PDF 一次 a1（不拆文件、不拆 output/，无需事后合并图片目录）。
# 用 nohup 脱离当前终端/IDE 进程组，减少「关标签 / 停止」带来的 SIGTERM；仍写出 logs/run_*_a1-convert.log（与 make a1-convert 相同）。
# 防不了：Windows 睡眠、wsl --shutdown、WSL 内存 OOM；请配 %USERPROFILE%\.wslconfig 并避免睡眠（见 ../wslconfig.example）。
set -euo pipefail
PART="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PART"
export REPO="${REPO:-$PART}"
export PYTHONUNBUFFERED=1
STEM="${STEM:-}"
if [[ -z "$STEM" ]]; then
  echo "用法: STEM=<无.pdf后缀的输入名> $0" >&2
  echo "示例: STEM=NCB-PCI_Express_Base_6.1 $0" >&2
  exit 1
fi
if [[ ! -f "input/${STEM}.pdf" ]]; then
  echo "缺少 input/${STEM}.pdf" >&2
  exit 1
fi
if [[ -f .venv/bin/activate ]]; then
  # shellcheck source=/dev/null
  source .venv/bin/activate
fi
mkdir -p logs
STAMP=$(date +%Y%m%d_%H%M%S)
# 聚合 tail 用（run_with_log 仍会写 logs/run_*_a1-convert.log）
LOGF="logs/a1_detached_${STEM}_${STAMP}.log"
PIDF="logs/a1_detached_last.pid"
export DEVICE="${DEVICE:-cuda}"
export IMAGES_SCALE="${IMAGES_SCALE:-4.0}"
export FAST_A1="${FAST_A1:-0}"
export PROGRESS_INTERVAL="${PROGRESS_INTERVAL:-10}"
export TABLE_MODE="${TABLE_MODE:-accurate}"
export PICTURE_DESC="${PICTURE_DESC:-0}"
export IMAGE_MODE="${IMAGE_MODE:-referenced}"
# 与 make 的 EXTRA_A1 / PDESC 一致需通过 make 解析；只传环境变量，recipe 在 Makefile
nohup make a1-convert STEM="$STEM" >>"$LOGF" 2>&1 &
echo $! | tee "$PIDF" >/dev/null
echo "已后台启动 a1（整本、单套 output/）。PID=$(cat "$PIDF")"
echo "聚合日志: $LOGF"
echo "同一次 run_with_log 日志: ls -t logs/run_*_a1-convert.log 2>/dev/null | head -1"
echo "查看: tail -f $LOGF"
echo "结束: kill \$(cat $PIDF)   # 仅当你要中止时"
