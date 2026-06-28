#!/usr/bin/env bash
set -eu -o pipefail
PART="$(cd "$(dirname "$0")/.." && pwd)"
STEM="HDMI_2_2_Standard"
STAMP="20260520"
DEST="${PART}/archive/${STEM}_delivery_${STAMP}"

mkdir -p "${DEST}/json" "${DEST}/logs"

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

copy_if_exists "${PART}/output/figure_schemas" "${DEST}/"
copy_if_exists "${PART}/output/${STEM}_merged_images" "${DEST}/"
copy_if_exists "${PART}/logs/00_timings.txt" "${DEST}/logs/"

cat > "${DEST}/README.txt" <<EOF
HDMI 2.2 Standard 流水线交付备份
生成时间: $(date -Iseconds)

包含内容:
- json/: enriched.md、clean.md、索引与图片元数据
- figure_schemas/: 图片 VLM 描述 schema
- ${STEM}_merged_images/: 合并后的图片资源
- logs/00_timings.txt: 各步骤耗时

最终状态 (来自 logs/00_timings.txt):
- A 段: a1-batch ~ a4-codeblocks 全部 exit=0
- B 段: b1~b7 最终 exit=0 (b5 曾失败重跑，b6/b7 已成功)
EOF

echo "备份完成: ${DEST}"
du -sh "${DEST}" "${DEST}"/* 2>/dev/null || true
ls -lh "${DEST}/json/"
