#!/usr/bin/env bash
# 由 run_a3_a4_detached.sh 在已激活 venv、已加载 secrets 的环境下调用；也可单独调试。
set -euo pipefail
PART="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PART"
export REPO="${REPO:-$PART}"
export PYTHONUNBUFFERED=1

STEM="${1:?usage: $0 <STEM>}"
export AIDOC_LLM_MAX_RETRIES="${AIDOC_LLM_MAX_RETRIES:-16}"

if [[ -f "$PART/.venv/bin/activate" ]]; then
  # shellcheck source=/dev/null
  source "$PART/.venv/bin/activate"
fi

# secrets 由 Makefile 各目标的 WITH_SECRETS 注入；此处只保证 python 使用 .venv
make a3-hierarchy STEM="$STEM"
make a4-codeblocks STEM="$STEM"
