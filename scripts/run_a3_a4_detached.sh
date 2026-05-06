#!/usr/bin/env bash
# 后台连续跑 a3-hierarchy → a4-codeblocks（自动激活本仓库 .venv + secrets.sh，无需盯终端）。
# 用法（在仓库根或任意目录）:
#   bash scripts/run_a3_a4_detached.sh
#   STEM=MyBook bash scripts/run_a3_a4_detached.sh
# 日志: logs/a34_detached_<STEM>_<时间>.log ，另有 logs/run_*_a3-hierarchy.log 等与 make 一致。
set -euo pipefail
PART="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PART"

STEM="${STEM:-NCB-PCI_Express_Base_6.1}"
export REPO="${REPO:-$PART}"
export PYTHONUNBUFFERED=1
export AIDOC_LLM_MAX_RETRIES="${AIDOC_LLM_MAX_RETRIES:-16}"

if [[ ! -f "$PART/secrets.sh" ]]; then
  echo "缺少 $PART/secrets.sh，请从 secrets.sh.example 复制并填写" >&2
  exit 1
fi

if [[ ! -f "$PART/.venv/bin/activate" ]]; then
  echo "缺少 $PART/.venv：请在该目录执行: python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt" >&2
  exit 1
fi

if [[ ! -f "$PART/output/${STEM}_clean.md" ]]; then
  echo "缺少 output/${STEM}_clean.md，请先完成 merge 与 a2-strip" >&2
  exit 1
fi

mkdir -p "$PART/logs"
STAMP=$(date +%Y%m%d_%H%M%S)
LOGF="$PART/logs/a34_detached_${STEM}_${STAMP}.log"
PIDF="$PART/logs/a34_detached_last.pid"

# nohup 子 shell 需自带 venv，故调用 worker（内含 activate + secrets + make）
nohup bash "$PART/scripts/run_a3_a4_worker.sh" "$STEM" >>"$LOGF" 2>&1 &
echo $! | tee "$PIDF" >/dev/null

echo "已后台启动 a3→a4（venv 已在 worker 内激活）。PID=$(cat "$PIDF")"
echo "聚合日志: $LOGF"
echo "Makefile 分步日志: ls -t $PART/logs/run_*_a3-hierarchy.log $PART/logs/run_*_a4-codeblocks.log 2>/dev/null | head -5"
echo "总览: tail -f $PART/logs/pipeline.log"
echo "中止: kill \$(cat $PIDF)"
