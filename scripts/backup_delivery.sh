#!/usr/bin/env bash
# 打包某 STEM 的 A+B 交付物到 archive/<STEM>_delivery_<日期>/
# 用法: bash scripts/backup_delivery.sh HDMI1_4_Standard
set -eu -o pipefail
PART="$(cd "$(dirname "$0")/.." && pwd)"
STEM="${1:-}"
if [[ -z "$STEM" ]]; then
  echo "用法: bash scripts/backup_delivery.sh <STEM>" >&2
  exit 1
fi
STAMP="$(date +%Y%m%d)"
DEST="${PART}/archive/${STEM}_delivery_${STAMP}"
LOGS_ARCHIVE="${PART}/archive/logs_${STEM}_${STAMP}"

mkdir -p "${DEST}/json" "${DEST}/logs" "${LOGS_ARCHIVE}"

copy_if_exists() {
  local src="$1"
  local dst="$2"
  if [[ -e "$src" ]]; then
    cp -a "$src" "$dst"
  fi
}

for f in \
  "${STEM}_enriched.md" \
  "${STEM}_enriched.index.h4.json" \
  "${STEM}_enriched.index.h2.json" \
  "${STEM}_clean.md" \
  "${STEM}.images.json" \
  "${STEM}_merged.images.json" \
  "${STEM}.images.filtered.json" \
  "${STEM}.figure_context.json" \
  "${STEM}.ocr.json" \
  "${STEM}.descriptions_merged.json"
do
  copy_if_exists "${PART}/output/${f}" "${DEST}/json/"
done

# 仅复制该 STEM 的 figure schema（figure_schemas 目录可能混有其他文档）
if [[ -d "${PART}/output/figure_schemas" ]]; then
  mkdir -p "${DEST}/figure_schemas"
  shopt -s nullglob
  for j in "${PART}/output/figure_schemas/${STEM}"*.json; do
    cp -a "$j" "${DEST}/figure_schemas/"
  done
  shopt -u nullglob
  copy_if_exists "${PART}/output/figure_schemas/batch_report.json" "${DEST}/figure_schemas/"
fi

copy_if_exists "${PART}/output/${STEM}_merged_images" "${DEST}/"

if [[ -d "${PART}/logs" ]]; then
  cp -a "${PART}/logs/." "${LOGS_ARCHIVE}/"
  cp -a "${PART}/logs/." "${DEST}/logs_full/"
fi

# 校验关键交付物
missing=0
for req in "${STEM}_enriched.md" "${STEM}_enriched.index.h4.json"; do
  if [[ ! -f "${DEST}/json/${req}" ]]; then
    echo "警告: 缺少关键文件 output/${req}" >&2
    missing=1
  fi
done

cat > "${DEST}/README.txt" <<EOF
${STEM} 流水线交付备份
生成时间: $(date -Iseconds)

目录说明:
- json/           enriched.md、clean.md、索引与图片元数据
- figure_schemas/ 该文档的 VLM 图片描述 JSON
- ${STEM}_merged_images/  合并后的图片（若存在）
- logs_full/      完整运行日志
- 独立 logs 备份: ${LOGS_ARCHIVE}

下游 spec-semantics 主要使用:
- json/${STEM}_enriched.md
- json/${STEM}_enriched.index.h4.json
EOF

echo "备份完成: ${DEST}"
du -sh "${DEST}" "${LOGS_ARCHIVE}" 2>/dev/null || true
ls -lh "${DEST}/json/" 2>/dev/null || true
exit "$missing"
