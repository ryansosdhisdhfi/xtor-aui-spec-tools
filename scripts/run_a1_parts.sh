#!/usr/bin/env bash
# 在 make pdf-split 之后：按 manifest 逐段 a1-convert（降低单次内存峰值）。
# 用法（仓库根）: export REPO="$(pwd)"; STEM=NCB-PCI_Express_Base_6.1 bash scripts/run_a1_parts.sh
set -euo pipefail
PART="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PART"
export REPO="${REPO:-$PART}"
export PYTHONUNBUFFERED=1
STEM="${STEM:-}"
if [[ -z "$STEM" ]]; then
  echo "用法: STEM=<与整本 input 同名、无 .pdf 后缀> $0" >&2
  echo "须已执行: make pdf-split STEM=...（或 SPLIT_MAX_PAGES=120）" >&2
  exit 1
fi
MAN="input/${STEM}_parts_manifest.json"
if [[ ! -f "$MAN" ]]; then
  echo "缺少 $MAN，请先: make pdf-split STEM=$STEM" >&2
  exit 1
fi
if [[ -f .venv/bin/activate ]]; then
  # shellcheck source=/dev/null
  source .venv/bin/activate
fi
n=$(python3 -c "import json; print(len(json.load(open('$MAN'))['parts']))")
i=0
while read -r pstem; do
  [[ -z "$pstem" ]] && continue
  i=$((i + 1))
  echo "========== a1 分段 $i/$n STEM=$pstem =========="
  make a1-convert STEM="$pstem"
done < <(python3 -c "import json; print('\\n'.join(p['stem'] for p in json.load(open('$MAN'))['parts']))")

echo "全部分段 a1 完成。合并: make merge-parts STEM=$STEM"
echo "提示: 若需「只加载一次模型」连转各段，改用: make a1-batch STEM=$STEM（须先 pdf-split）"
