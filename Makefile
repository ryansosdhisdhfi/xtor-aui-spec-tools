# XTOR 规范文档管线：在本目录放输入，在 output/ 落产物。
# 使用前先 export REPO=…（工作目录+查找 .venv）；推荐: export REPO="$(pwd)"（本仓为根），见 README。
#   cd /path/to/xtor-aui-spec-tools
#   cp secrets.sh.example secrets.sh
#   make check
#   make all
#
# 建议 WSL2 + bash；依赖见本目录 README 与 requirements.txt。
# 每步通过 scripts/run_with_log.sh 写入 logs/（含时间戳、完整命令、退出码、耗时）。

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c

PART   := $(abspath .)
# 未设置 REPO 时：若本仓已有 .venv，则 REPO=本目录；否则默认上溯两级（旧布局：本仓与 Aidoc 根为兄弟）
# 仍可直接：REPO=/path/to/aidoc-toolchain make a1-batch
ifeq ($(origin REPO), undefined)
  REPO := $(shell test -d "$(PART)/.venv" && echo "$(PART)" || echo "$(abspath $(PART)/../..)")
else
  REPO := $(abspath $(REPO))
endif
PY     := $(PART)/py
OUT    := $(PART)/output
IN     := $(PART)/input
LOG    := $(PART)/logs
# 与 input/<STEM>.pdf 同名（无空格）；改 STEM 时同步重命名 input 下 PDF
# 用 ?= 以便 export STEM=… 或 env STEM=… 覆盖默认（:= 会无视环境变量导致仍跑 dsc_v12b）
STEM   ?= dsc_v12b
RUN    := bash $(PART)/scripts/run_with_log.sh $(LOG)

# 可选环境（可 export 或写在 secrets.sh）
DEVICE         ?= cuda
PICTURE_DESC   ?= 0
INDEX_DEPTH    ?= 4
OCR_LANG       ?= eng+chi_sim
IMAGE_MODE     ?= referenced
# a1 心跳行间隔（秒），可用 PROGRESS_INTERVAL=5 make a1-convert 覆盖
PROGRESS_INTERVAL ?= 10
# 表格模式：默认 accurate；与 FAST_A1=1 连用时自动改为 fast
TABLE_MODE     ?= accurate
# Docling 页面/图片渲染缩放，默认 4.0；大 PDF 易顶满 32G 主机内存时可改为 2～2.5 再跑 a1（仍 accurate 等全开）
# 用法: IMAGES_SCALE=2 STEM=… make a1-convert
IMAGES_SCALE   ?= 4.0
# 省时间：少加载/少跑子模型（代码/公式/图分类）+ 表格走 fast。日志里多段 “Loading weights” 会略少，整份 PDF 推理仍占大头。
# 用法: FAST_A1=1 make a1-convert
FAST_A1        ?= 0
# pdf-split：无书签时按固定页块拆；有书签时默认「章优先」+ 超长章内子书签（见 py/split_pdf_for_a1.py）
SPLIT_MAX_PAGES ?= 200
# qpdf 导出单段超过此 KB 再二分（图形多的章可能页少但体积大）；0=关闭
SPLIT_MAX_PART_KB ?= 0
# 传给 split_pdf_for_a1.py 的额外参数（make 不认识 --foo，须写在这里）。例: SPLIT_PDF_EXTRA=--top-level-only
SPLIT_PDF_EXTRA :=
# a1-batch-chunked：仅当 N>0 时每 N 个分段 PDF 后重建 Docling（省显存）；默认 0 = 与 a1-batch 相同单次加载
A1_BATCH_CHUNK_SIZE ?= 0
# a1-batch 从 manifest 第几段开始（1-based，含该段）；中断后续跑例：A1_START_INDEX=17 make a1-batch STEM=...
A1_START_INDEX ?= 1
# 设为 1 时传入 --skip-existing，已生成的 .md 跳过
A1_SKIP_EXISTING ?= 0
EXTRA_A1       :=
ifeq ($(FAST_A1),1)
  TABLE_MODE := fast
  EXTRA_A1 := --no-code-enrichment --no-formula-enrichment --no-picture-classification
endif

# 有 secrets 时执行的包装（子 shell 里 source，避免把密钥打进 Makefile）
define WITH_SECRETS
set -a && source "$(PART)/secrets.sh" && set +a
endef

PDESC :=
ifeq ($(PICTURE_DESC),1)
  PDESC := --picture-description
endif

# Step1: Docling 转换（本目录 py/ 为仓库主脚本的副本文本，行为一致）
a1-convert: LOG_STEP := a1-convert
a1-convert: dirs
	@cd "$(REPO)" && LOG_STEP=$(LOG_STEP) $(RUN) \
	  python "$(PY)/aidoc_convert_assets.py" \
	    "$(IN)/$(STEM).pdf" \
	    -o "$(OUT)/$(STEM).md" \
	    --device "$(DEVICE)" --table-mode "$(TABLE_MODE)" \
	    --image-mode "$(IMAGE_MODE)" \
	    --images-scale "$(IMAGES_SCALE)" \
	    --progress-interval "$(PROGRESS_INTERVAL)" \
	    $(EXTRA_A1) \
	    $(PDESC) \
	    -v --stats

# 整本 PDF 一次 a1，nohup 脱离当前终端（不拆文件、不拆 output/）；防 IDE 关窗仍见 logs/
a1-convert-detached: dirs
	@STEM="$(STEM)" REPO="$(REPO)" DEVICE="$(DEVICE)" FAST_A1="$(FAST_A1)" \
	  IMAGES_SCALE="$(IMAGES_SCALE)" \
	  PROGRESS_INTERVAL="$(PROGRESS_INTERVAL)" TABLE_MODE="$(TABLE_MODE)" \
	  PICTURE_DESC="$(PICTURE_DESC)" IMAGE_MODE="$(IMAGE_MODE)" \
	  bash "$(PART)/scripts/run_a1_detached.sh"

# 32G 物理内存主机：整本 a1 推荐（IMAGES_SCALE=2.5 + FAST_A1=1，配合 WSL 大 swap、工程在 ~/ext4）
a1-convert-32g: dirs
	@$(MAKE) a1-convert FAST_A1=1 IMAGES_SCALE=2.5

# 整本 a1 + 资源/dmesg 记录（logs/diag_<STEM>_时间/）；复现退出时查 oom / 内存尖峰
a1-convert-diag: dirs
	@STEM="$(STEM)" REPO="$(REPO)" DEVICE="$(DEVICE)" FAST_A1="$(FAST_A1)" \
	  IMAGES_SCALE="$(IMAGES_SCALE)" \
	  PROGRESS_INTERVAL="$(PROGRESS_INTERVAL)" TABLE_MODE="$(TABLE_MODE)" \
	  PICTURE_DESC="$(PICTURE_DESC)" IMAGE_MODE="$(IMAGE_MODE)" \
	  bash "$(PART)/scripts/run_a1_with_diags.sh"

# 将整本 input/$(STEM).pdf 拆成 input/$(STEM)_pt001.pdf …（需系统 qpdf；WSL: apt install qpdf）
pdf-split: dirs
	@test -f "$(IN)/$(STEM).pdf" || ( echo "缺少 $(IN)/$(STEM).pdf" >&2; exit 1 )
	@python3 "$(PY)/split_pdf_for_a1.py" "$(IN)/$(STEM).pdf" \
	  --base-stem "$(STEM)" --input-dir "$(IN)" --max-pages "$(SPLIT_MAX_PAGES)" \
	  --max-part-kb "$(SPLIT_MAX_PART_KB)" $(SPLIT_PDF_EXTRA)

# 分段 a1：单次加载 Docling，按 manifest 连转（须先 pdf-split；比多次 make a1-convert 少重复加载）
a1-batch: LOG_STEP := a1-batch
a1-batch: dirs
	@test -f "$(IN)/$(STEM)_parts_manifest.json" || ( echo "缺少 $(IN)/$(STEM)_parts_manifest.json，先 make pdf-split" >&2; exit 1 )
	@cd "$(REPO)" && LOG_STEP=$(LOG_STEP) $(RUN) \
	  python "$(PY)/aidoc_convert_assets_batch.py" \
	    --manifest "$(PART)/input/$(STEM)_parts_manifest.json" \
	    --output-dir "$(OUT)" \
	    --start-index "$(A1_START_INDEX)" \
	    $(if $(filter 1,$(A1_SKIP_EXISTING)),--skip-existing,) \
	    --device "$(DEVICE)" --table-mode "$(TABLE_MODE)" \
	    --image-mode "$(IMAGE_MODE)" \
	    --images-scale "$(IMAGES_SCALE)" \
	    --progress-interval "$(PROGRESS_INTERVAL)" \
	    $(EXTRA_A1) \
	    $(PDESC) \
	    -v --stats

# 同 a1-batch；若设置 A1_BATCH_CHUNK_SIZE>0 则每 N 段重建管线（显存顶满时用，默认 0 不重建）
a1-batch-chunked: LOG_STEP := a1-batch-chunked
a1-batch-chunked: dirs
	@test -f "$(IN)/$(STEM)_parts_manifest.json" || ( echo "缺少 $(IN)/$(STEM)_parts_manifest.json，先 make pdf-split" >&2; exit 1 )
	@cd "$(REPO)" && LOG_STEP=$(LOG_STEP) $(RUN) \
	  python "$(PY)/aidoc_convert_assets_batch.py" \
	    --manifest "$(PART)/input/$(STEM)_parts_manifest.json" \
	    --output-dir "$(OUT)" \
	    --start-index "$(A1_START_INDEX)" \
	    $(if $(filter 1,$(A1_SKIP_EXISTING)),--skip-existing,) \
	    --reload-every "$(A1_BATCH_CHUNK_SIZE)" \
	    --device "$(DEVICE)" --table-mode "$(TABLE_MODE)" \
	    --image-mode "$(IMAGE_MODE)" \
	    --images-scale "$(IMAGES_SCALE)" \
	    --progress-interval "$(PROGRESS_INTERVAL)" \
	    $(EXTRA_A1) \
	    $(PDESC) \
	    -v --stats

# 清除某 STEM 的 pdf-split / 分段 a1 / merge 中间产物（不删 input/$(STEM).pdf）
clean-split-parts: dirs
	@test -n "$(STEM)" || ( echo "用法: make clean-split-parts STEM=主书名" >&2; exit 1 )
	@rm -rf "$(IN)/.split_size_probe"
	@rm -f "$(IN)/$(STEM)"_pt*.pdf "$(IN)/$(STEM)_parts_manifest.json"
	@rm -f "$(OUT)/$(STEM)"_pt*.md "$(OUT)/$(STEM)"_pt*.images.json "$(OUT)/$(STEM)_merged.md"
	@rm -rf "$(OUT)/$(STEM)"_pt*_images "$(OUT)/$(STEM)_merged_images"
	@rm -f "$(OUT)/$(STEM)_merged.images.json"
	@echo "已清理 $(STEM) 的分段中间产物（保留 $(IN)/$(STEM).pdf）"

# 合并各段 output/$(STEM)_ptNNN.md -> output/$(STEM)_merged.md（图链仍指向各 *_ptNNN_images/；要单目录图见 merge-assets）
merge-parts: dirs
	@test -f "$(IN)/$(STEM)_parts_manifest.json" || ( echo "缺少 $(IN)/$(STEM)_parts_manifest.json，先 make pdf-split" >&2; exit 1 )
	@python3 "$(PY)/merge_split_md.py" "$(IN)/$(STEM)_parts_manifest.json" -o "$(OUT)/$(STEM)_merged.md"

# 合并分段图片与 *.images.json 到 output/$(STEM)_merged_images/ 与 $(STEM)_merged.images.json，并重写 _merged.md 内图路径（须先有 merge-parts）
merge-assets: LOG_STEP := merge-assets
merge-assets: dirs
	@test -f "$(IN)/$(STEM)_parts_manifest.json" || ( echo "缺少 $(IN)/$(STEM)_parts_manifest.json，先 make pdf-split" >&2; exit 1 )
	@test -f "$(OUT)/$(STEM)_merged.md" || ( echo "缺少 $(OUT)/$(STEM)_merged.md，先 make merge-parts" >&2; exit 1 )
	@LOG_STEP=$(LOG_STEP) $(RUN) \
	  python3 "$(PY)/merge_split_assets.py" "$(IN)/$(STEM)_parts_manifest.json" --output-dir "$(OUT)" --clean

# merge-parts 后立即合并资源（单目录图 + 统一 JSON）
merge-parts-full: merge-parts merge-assets

# Step2-4: 需 LLM
a2-strip: LOG_STEP := a2-strip
a2-strip: dirs
	@cd "$(REPO)" && \
	$(WITH_SECRETS) && LOG_STEP=$(LOG_STEP) $(RUN) \
	  python "$(PY)/aidoc_strip.py" "$(OUT)/$(STEM).md" \
	    --api openai --api-url "$$API_URL" --api-key "$$API_KEY" --model "$$MODEL" -v

a3-hierarchy: LOG_STEP := a3-hierarchy
a3-hierarchy: dirs
	@cd "$(REPO)" && \
	$(WITH_SECRETS) && LOG_STEP=$(LOG_STEP) $(RUN) \
	  python "$(PY)/aidoc_fix_hierarchy.py" "$(OUT)/$(STEM)_clean.md" \
	    --api openai --api-url "$$API_URL" --api-key "$$API_KEY" --model "$$MODEL" -v

a4-codeblocks: LOG_STEP := a4-codeblocks
a4-codeblocks: dirs
	@cd "$(REPO)" && \
	$(WITH_SECRETS) && LOG_STEP=$(LOG_STEP) $(RUN) \
	  python "$(PY)/aidoc_fix_codeblocks.py" "$(OUT)/$(STEM)_clean.md" \
	    -o "$(OUT)/$(STEM)_clean.md" \
	    --api openai --api-url "$$API_URL" --api-key "$$API_KEY" --model "$$MODEL" -v

# a3→a4 断点续跑：激活 .venv + nohup 后台（无需盯终端），见 scripts/run_a3_a4_detached.sh
a34-detached: dirs
	@bash "$(PART)/scripts/run_a3_a4_detached.sh"

# 第一次 index（可选；可与 B7 二选一或都跑）
a5-index: LOG_STEP := a5-index
a5-index: dirs
	@cd "$(REPO)" && \
	$(WITH_SECRETS) && LOG_STEP=$(LOG_STEP) $(RUN) \
	  python "$(PY)/aidoc_index.py" "$(OUT)/$(STEM)_clean.md" \
	    --depth "$(INDEX_DEPTH)" \
	    -o "$(OUT)/$(STEM).index.json" \
	    --api openai --api-url "$$API_URL" --api-key "$$API_KEY" --model "$$MODEL" -v

# 仅按 H1 或 H2 切块索引（正文为 _clean.md）
a5-index-h1: LOG_STEP := a5-index-h1
a5-index-h1: dirs
	@cd "$(REPO)" && \
	$(WITH_SECRETS) && LOG_STEP=$(LOG_STEP) $(RUN) \
	  python "$(PY)/aidoc_index.py" "$(OUT)/$(STEM)_clean.md" \
	    --depth 1 \
	    -o "$(OUT)/$(STEM).index.h1.json" \
	    --api openai --api-url "$$API_URL" --api-key "$$API_KEY" --model "$$MODEL" -v

a5-index-h2: LOG_STEP := a5-index-h2
a5-index-h2: dirs
	@cd "$(REPO)" && \
	$(WITH_SECRETS) && LOG_STEP=$(LOG_STEP) $(RUN) \
	  python "$(PY)/aidoc_index.py" "$(OUT)/$(STEM)_clean.md" \
	    --depth 2 \
	    -o "$(OUT)/$(STEM).index.h2.json" \
	    --api openai --api-url "$$API_URL" --api-key "$$API_KEY" --model "$$MODEL" -v

# B1: 图链相对化（b-all 首步，先于 b2 筛图）
b1-rewrite: LOG_STEP := b1-rewrite
b1-rewrite: dirs
	@cd "$(REPO)" && LOG_STEP=$(LOG_STEP) $(RUN) \
	  python "$(PY)/rewrite_md_image_paths.py" \
	    -i "$(OUT)/$(STEM)_clean.md" --in-place \
	    --images-json "$(OUT)/$(STEM).images.json"

b2-filter: LOG_STEP := b2-filter
b2-filter: dirs
	@cd "$(REPO)" && LOG_STEP=$(LOG_STEP) $(RUN) \
	  python "$(PY)/filter_images.py" \
	    --md "$(OUT)/$(STEM)_clean.md" \
	    --images-json "$(OUT)/$(STEM).images.json" \
	    -o "$(OUT)/$(STEM).images.filtered.json"

b3-context: LOG_STEP := b3-context
b3-context: dirs
	@cd "$(REPO)" && LOG_STEP=$(LOG_STEP) $(RUN) \
	  python "$(PY)/extract_figure_context.py" \
	    --md "$(OUT)/$(STEM)_clean.md" \
	    -o "$(OUT)/$(STEM).figure_context.json" \
	    --by-basename

b4-ocr: LOG_STEP := b4-ocr
b4-ocr: dirs
	@cd "$(REPO)" && LOG_STEP=$(LOG_STEP) $(RUN) \
	  python "$(PY)/ocr_figure_artifacts.py" \
	    --filtered-json "$(OUT)/$(STEM).images.filtered.json" \
	    -o "$(OUT)/$(STEM).ocr.json" \
	    --lang "$(OCR_LANG)"

# B5: VLM 批量描述（贵、慢）
b5-describe: LOG_STEP := b5-describe
b5-describe: dirs
	@cd "$(REPO)" && \
	$(WITH_SECRETS) && \
	BAPI="$${BATCH_BASE_URL:-$${API_URL}}"; \
	LOG_STEP=$(LOG_STEP) $(RUN) \
	  python "$(PY)/batch_describe.py" \
	    --filtered-json "$(OUT)/$(STEM).images.filtered.json" \
	    --figure-context "$(OUT)/$(STEM).figure_context.json" \
	    --ocr-json "$(OUT)/$(STEM).ocr.json" \
	    --out-dir "$(OUT)/figure_schemas" \
	    --doc-id "$(STEM)" \
	    --api-key "$$API_KEY" --model "$$MODEL" \
	    --base-url "$$BAPI" \
	    --merge-out "$(OUT)/$(STEM).descriptions_merged.json" -v

b6-inject: LOG_STEP := b6-inject
b6-inject: dirs
	@cd "$(REPO)" && LOG_STEP=$(LOG_STEP) $(RUN) \
	  python "$(PY)/inject_figure_enrichment.py" \
	    --md "$(OUT)/$(STEM)_clean.md" \
	    --merged-json "$(OUT)/$(STEM).descriptions_merged.json" \
	    -o "$(OUT)/$(STEM)_enriched.md" --source-tag vlm-v1

b7-index: LOG_STEP := b7-index
b7-index: dirs
	@cd "$(REPO)" && \
	$(WITH_SECRETS) && LOG_STEP=$(LOG_STEP) $(RUN) \
	  python "$(PY)/aidoc_index.py" "$(OUT)/$(STEM)_enriched.md" \
	    --depth "$(INDEX_DEPTH)" \
	    -o "$(OUT)/$(STEM)_enriched.index.json" \
	    --api openai --api-url "$$API_URL" --api-key "$$API_KEY" --model "$$MODEL" -v

# 同一份 enriched.md 生成两份索引（细/粗各一；各跑一遍 LLM，耗时与费用约两倍）
b7-index-dual: LOG_STEP := b7-index-dual
b7-index-dual: dirs
	@cd "$(REPO)" && \
	$(WITH_SECRETS) && LOG_STEP=$(LOG_STEP) $(RUN) \
	  python "$(PY)/aidoc_index.py" "$(OUT)/$(STEM)_enriched.md" \
	    --depth 4 \
	    -o "$(OUT)/$(STEM)_enriched.index.h4.json" \
	    --api openai --api-url "$$API_URL" --api-key "$$API_KEY" --model "$$MODEL" -v
	@cd "$(REPO)" && \
	$(WITH_SECRETS) && LOG_STEP=$(LOG_STEP) $(RUN) \
	  python "$(PY)/aidoc_index.py" "$(OUT)/$(STEM)_enriched.md" \
	    --depth 2 \
	    -o "$(OUT)/$(STEM)_enriched.index.h2.json" \
	    --api openai --api-url "$$API_URL" --api-key "$$API_KEY" --model "$$MODEL" -v

# 仅按 H1 或 H2 切块索引（正文为 _enriched.md）
b7-index-h1: LOG_STEP := b7-index-h1
b7-index-h1: dirs
	@cd "$(REPO)" && \
	$(WITH_SECRETS) && LOG_STEP=$(LOG_STEP) $(RUN) \
	  python "$(PY)/aidoc_index.py" "$(OUT)/$(STEM)_enriched.md" \
	    --depth 1 \
	    -o "$(OUT)/$(STEM)_enriched.index.h1.json" \
	    --api openai --api-url "$$API_URL" --api-key "$$API_KEY" --model "$$MODEL" -v

b7-index-h2: LOG_STEP := b7-index-h2
b7-index-h2: dirs
	@cd "$(REPO)" && \
	$(WITH_SECRETS) && LOG_STEP=$(LOG_STEP) $(RUN) \
	  python "$(PY)/aidoc_index.py" "$(OUT)/$(STEM)_enriched.md" \
	    --depth 2 \
	    -o "$(OUT)/$(STEM)_enriched.index.h2.json" \
	    --api openai --api-url "$$API_URL" --api-key "$$API_KEY" --model "$$MODEL" -v

# 聚合
a-all: a1-convert a2-strip a3-hierarchy a4-codeblocks
# B 段：末步为 b7-index-dual（同一份 enriched.md 输出 H4 + H2 两份 JSON，耗时/费用约为原 b7-index 两倍）
b-all: b1-rewrite b2-filter b3-context b4-ocr b5-describe b6-inject b7-index-dual
# 不默认包含 a5-index（可与 b7 二选一；可单独 make）
all: a-all b-all

dirs:
	@mkdir -p "$(OUT)" "$(LOG)"
	@touch "$(LOG)/00_timings.txt" "$(LOG)/00_commands.txt" 2>/dev/null || true

check:
	@test -f "$(IN)/$(STEM).pdf" || ( echo "缺少 $(IN)/$(STEM).pdf — 见 input/README.txt" >&2; exit 1 )
	@test -f "$(PART)/secrets.sh" || ( echo "缺少 $(PART)/secrets.sh — 从 secrets.sh.example 复制并填写" >&2; exit 1 )
	@test -d "$(REPO)/.venv" -o -n "$${VIRTUAL_ENV:-}" || echo "提示: 建议在仓库根 REPO 创建 .venv 并 pip install -r $(PART)/requirements.txt" >&2
	@echo "REPO=$(REPO)  PART=$(PART)  检查通过。"

help:
	@echo "目标: a1-convert  a1-convert-32g(32G 机器整本推荐)  a1-convert-detached  a1-convert-diag  a2-strip …"
	@echo "     a34-detached(a3→a4 后台续跑，激活 .venv)"
	@echo "     pdf-split  a1-batch  a1-batch-chunked(每 N 段重建管线)  merge-parts  merge-assets  merge-parts-full  clean-split-parts"
	@echo "     a5-index  a5-index-h1/H2(对 _clean.md 仅 H1 或 H2 切块)"
	@echo "     b1-rewrite b2-filter b3-context b4-ocr b5-describe b6-inject b-all(末步 b7-index-dual: H4+H2 两份索引)"
	@echo "     a-all  b-all  all(=a-all+b-all)  check  help"
	@echo "环境: secrets.sh 需 API_URL / API_KEY / MODEL；可选 BATCH_BASE_URL（覆盖 B5 的 OpenAI base，须含 /v1）"
	@echo "a1: 可选 FAST_A1=1、IMAGES_SCALE=2（省内存）或 TABLE_MODE=fast；见 py/aidoc_convert_assets.py"
	@echo "a1-batch 续跑: A1_START_INDEX=N（manifest 第 N 段起）、A1_SKIP_EXISTING=1（跳过已有 .md）"
	@echo "pdf-split: 可选 SPLIT_MAX_PAGES=、SPLIT_MAX_PART_KB=（单段 qpdf 体积上限 KB，0=关）、SPLIT_PDF_EXTRA="
	@echo "日志: $(LOG)/run_*_<步骤>.log（如 a1-convert）、pipeline.log、00_timings.txt（含 step=）、00_commands.txt"

.PHONY: a1-convert a1-convert-32g a1-convert-detached a1-convert-diag a1-batch a1-batch-chunked a2-strip a3-hierarchy a4-codeblocks a34-detached a5-index a5-index-h1 a5-index-h2 \
	pdf-split merge-parts merge-assets merge-parts-full clean-split-parts \
	b1-rewrite b2-filter b3-context b4-ocr b5-describe b6-inject b7-index b7-index-h1 b7-index-h2 b7-index-dual \
	a-all b-all all dirs check help
