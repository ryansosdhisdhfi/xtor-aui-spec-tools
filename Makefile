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
# 未设置环境变量 REPO 时，默认上溯两级 = Aidoc 工具链根（与「本仓与 REPO 为兄弟目录」等布局一致）。
# 更稳妥：REPO=/path/to/aidoc-toolchain make all
ifeq ($(origin REPO), undefined)
  REPO := $(abspath $(PART)/../..)
else
  REPO := $(abspath $(REPO))
endif
PY     := $(PART)/py
OUT    := $(PART)/output
IN     := $(PART)/input
LOG    := $(PART)/logs
# 与 input/<STEM>.pdf 同名（无空格）；改 STEM 时同步重命名 input 下 PDF
STEM   := dsc_v12b
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
# 省时间：少加载/少跑子模型（代码/公式/图分类）+ 表格走 fast。日志里多段 “Loading weights” 会略少，整份 PDF 推理仍占大头。
# 用法: FAST_A1=1 make a1-convert
FAST_A1        ?= 0
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
	    --progress-interval "$(PROGRESS_INTERVAL)" \
	    $(EXTRA_A1) \
	    $(PDESC) \
	    -v --stats

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

# 第一次 index（可选；可与 B7 二选一或都跑）
a5-index: LOG_STEP := a5-index
a5-index: dirs
	@cd "$(REPO)" && \
	$(WITH_SECRETS) && LOG_STEP=$(LOG_STEP) $(RUN) \
	  python "$(PY)/aidoc_index.py" "$(OUT)/$(STEM)_clean.md" \
	    --depth "$(INDEX_DEPTH)" \
	    -o "$(OUT)/$(STEM).index.json" \
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

# 聚合
a-all: a1-convert a2-strip a3-hierarchy a4-codeblocks
b-all: b1-rewrite b2-filter b3-context b4-ocr b5-describe b6-inject b7-index
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
	@echo "目标: a1-convert a2-strip a3-hierarchy a4-codeblocks  a5-index(可选)"
	@echo "     b1-rewrite b2-filter b3-context b4-ocr b5-describe b6-inject b7-index"
	@echo "     a-all  b-all  all(=a-all+b-all)  check  help"
	@echo "环境: secrets.sh 需 API_URL / API_KEY / MODEL；可选 BATCH_BASE_URL（覆盖 B5 的 OpenAI base，须含 /v1）"
	@echo "a1: 可选 FAST_A1=1（更快、子模型关几项）或 TABLE_MODE=fast；纯文字 PDF 可加脚本参数 --no-ocr（见 py/aidoc_convert_assets.py）"
	@echo "日志: $(LOG)/run_*_<步骤>.log（如 a1-convert）、pipeline.log、00_timings.txt（含 step=）、00_commands.txt"

.PHONY: a1-convert a2-strip a3-hierarchy a4-codeblocks a5-index \
	b1-rewrite b2-filter b3-context b4-ocr b5-describe b6-inject b7-index \
	a-all b-all all dirs check help
