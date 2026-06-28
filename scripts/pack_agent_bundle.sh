#!/usr/bin/env bash
# 打包单本书的 Agent 交付包 → ../../deliveries/<STEM>_bundle_<日期>/
# 用法: bash scripts/pack_agent_bundle.sh <STEM> [规范标题]
set -euo pipefail

PART="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AI_ROOT="$(cd "${PART}/.." && pwd)"
STEM="${1:?用法: pack_agent_bundle.sh <STEM> [标题]}"
TITLE="${2:-$STEM}"
DATE_STAMP="$(date +%Y%m%d)"
OUT_SRC="${PART}/output"
DEST="${AI_ROOT}/deliveries/${STEM}_bundle_${DATE_STAMP}"
ZIP="${AI_ROOT}/deliveries/${STEM}_bundle_${DATE_STAMP}.zip"
FIG_DIR="${OUT_SRC}/figure_schemas"

mkdir -p "${DEST}/json" "${DEST}/figure_schemas"

for f in \
  "${STEM}_enriched.md" \
  "${STEM}_enriched.index.h4.json" \
  "${STEM}_enriched.index.h2.json"
do
  src="${OUT_SRC}/${f}"
  if [[ ! -f "$src" ]]; then
    echo "错误: 缺少 ${src}" >&2
    exit 1
  fi
  cp -a "$src" "${DEST}/json/"
done

fig_count=0
shopt -s nullglob
if [[ "$STEM" == "JESD223E" ]]; then
  for j in "${FIG_DIR}"/fig_*.json; do
    if grep -q "\"doc_id\": \"${STEM}\"" "$j" 2>/dev/null; then
      cp -a "$j" "${DEST}/figure_schemas/"
      fig_count=$((fig_count + 1))
    fi
  done
else
  for j in "${FIG_DIR}/${STEM}"*.json; do
    cp -a "$j" "${DEST}/figure_schemas/"
    fig_count=$((fig_count + 1))
  done
fi
shopt -u nullglob

cat > "${DEST}/README.txt" <<EOF
${TITLE} — Agent / 用户使用手册
===================================
来源: PDF 规范经 A+B 流水线处理后的直接交付物（无后续 C 段、无预置规则库、无 PDF）

本包仅含四类文件：

  README.txt
  json/${STEM}_enriched.md
  json/${STEM}_enriched.index.h4.json
  json/${STEM}_enriched.index.h2.json
  figure_schemas/*.json   (${fig_count} 个)


一、各文件是什么
----------------

enriched.md
  规范正文 Markdown。已做页眉页脚剥离、标题层级整理、代码块整理，
  并在图片位置注入 figure-enrich 短摘要块。
  → 读规范、写理解、抽要求时的主文本。

index.h4.json
  细粒度目录（H1–H4）。每节含标题、层级、在 enriched.md 中的起止行号，
  部分节带 LLM 生成的 summary / keywords（英文）。
  → 精确定位某一节、按节检索与阅读。

index.h2.json
  粗粒度目录（H1–H2）。用于快速了解全书结构。
  → 先浏览大章，再借助 h4 或 enriched 下钻。

figure_schemas/*.json
  每张插图的 VLM 结构化描述：image_type、summary、entities、
  diagram_semantics、ocr_text、page 等。
  → 理解表格/时序/状态机/寄存器位域等图。


二、推荐使用方式
----------------

1. 读 index.h2.json → 建立全书结构
2. 在 index.h4.json 中找目标 section 及行号
3. 打开 enriched.md 对应段落读正文
4. 若该节有图 → 打开 figure_schemas/ 中对应 JSON 读完整图语义
5. 需要写规则/设计/验证点时 → 引用 section 标题 + enriched 行号 +（如有）figure 文件名


三、使用原则
------------

  · enriched.md 是正文权威来源
  · index 用于导航与定位，summary 是辅助摘要，不能替代正文
  · 图相关结论应同时看 enriched 内 figure-enrich 块与 figure_schemas 全量 JSON
  · 本包不含 PDF；正文与图 JSON 仍不够时再向用户索取原 PDF


四、figure-enrich 与 figure_schema 的关系
-----------------------------------------

enriched.md 里类似：

  <!-- figure-enrich: source=vlm-v2 image_id=fig_0012 -->
  （短摘要）
  <!-- /figure-enrich -->

完整语义在 figure_schemas/ 对应 JSON 文件中。
Agent 做图相关任务时以 figure_schema 为准。


五、文档信息
------------

  规范:     ${TITLE}
  STEM:     ${STEM}
  打包日期: ${DATE_STAMP:0:4}-${DATE_STAMP:4:2}-${DATE_STAMP:6:2}
  图 JSON:  ${fig_count} 个

以上即为面向 Agent / 终端用户的全部背景资料。
EOF

rm -f "$ZIP"
python3 - <<PY
import zipfile, os
dest = "${DEST}"
zip_path = "${ZIP}"
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
    for root, _, files in os.walk(dest):
        for fn in files:
            fp = os.path.join(root, fn)
            arc = os.path.relpath(fp, os.path.dirname(dest))
            zf.write(fp, arc)
print(f"zip: {zip_path}")
PY

echo "✓ ${DEST}"
echo "  json: 3 文件"
echo "  figure_schemas: ${fig_count} 个"
du -sh "${DEST}" "$ZIP" | awk '{print "  " $0}'
