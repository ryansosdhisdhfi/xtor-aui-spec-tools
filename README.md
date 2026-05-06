# xtor-aui-spec-tools

面向 **工业规格书（SPEC）等大 PDF** 的 **离线批处理工具链**：在本地/WSL 将 PDF 转成可维护的 Markdown、规整图片与语义描述，并生成 **RAG/检索可用的索引 JSON**。本仓库是**独立**应用包：**`Makefile`** 编排、**`py/`** 内含全部入口脚本，可按 README 一键复现主干流程，无需再克隆另一棵「历史大仓」。

---

## 能力与产出（一句话版图）

```
PDF ──► Docling(a1) ──► Markdown + 配图
           │
           ├─► A 段：页眉剥离、标题层级修复、代码块整理（LLM）
           └─► B 段：图链相对化 → 筛图/OCR/VLM → 注入 enriched MD → H4+H2 双份索引 JSON
```

| 产出位置 | 说明 |
|----------|------|
| `output/`（默认不进 Git） | `*_clean.md`、`*_enriched.md`、合并图目录、`figure_schemas/*.json`、`*_enriched.index.h4.json` / `.h2.json` 等 |
| `logs/` | `pipeline.log`、`run_*_<步骤>.log`、时间与命令摘要 |

**`make b-all`** 结尾为 **`b7-index-dual`**：对同一份 **`$(STEM)_enriched.md`** 依次生成 **细粒度（H4）** 与 **粗粒度（H2）** 两套索引。

---

## 目录结构

| 路径 | 作用 |
|------|------|
| `Makefile` | `a*` / `b*` 管线入口；`pdf-split`、`a1-batch`、`merge-*` 等大 PDF 分段与合并 |
| `py/` | `aidoc_index.py`、`batch_describe.py`、`merge_split_md.py`、`split_pdf_for_a1.py` 等脚本 |
| `scripts/` | `run_with_log.sh`、`backup_output_logs.sh`、`run_a*_detached.sh`、`*.ps1` 辅助 |
| `input/` | 放置 **`$(STEM).pdf`**（大文件见 `.gitignore`）；`README.txt` 命名约定 |
| `archive/` | 本地快照： **`scripts/backup_output_logs.sh`** 将整棵 `output/`、`logs/` 按时间戳移入（勿当垃圾删） |

---

## 环境与快速开始（WSL / Bash）

```bash
git clone <本仓库 HTTPS 或 SSH>
cd xtor-aui-spec-tools

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -v --default-timeout=1000

cp secrets.sh.example secrets.sh
# 填写 API_URL（须含 /v1）、API_KEY、MODEL（及 B 段可用的 BATCH_BASE_URL 等按需）

export REPO="$(pwd)"
export STEM=你的书名前缀无空格   # 与 input/<STEM>.pdf 同名
make check STEM="$STEM"
```

- **单独放本仓库在桌面等路径时，务必 **`export REPO="$(pwd)"`**；未设置时 Makefile 可能对旧目录结构的兼容默认值不符合你的布局。**

### 大 PDF（推荐拆分）

依赖系统 **`qpdf`**（如 `sudo apt install qpdf`）。

```bash
make pdf-split STEM="$STEM"
make a1-batch STEM="$STEM"          # 可 A1_START_INDEX / A1_SKIP_EXISTING 续跑
make merge-parts-full STEM="$STEM"

# Makefile 约定：a2 入参为 $(STEM).md——合并后请先对齐主文件名与 images.json
cp "output/${STEM}_merged.md" "output/${STEM}.md"
cp "output/${STEM}_merged.images.json" "output/${STEM}.images.json"

make a2-strip a3-hierarchy a4-codeblocks STEM="$STEM"
make b-all STEM="$STEM"
```

### 小 PDF（整本一次 a1）

```bash
make a1-convert STEM="$STEM"
make a-all b-all STEM="$STEM"       # 或分步调用，见下文
```

---

## 常用 `make` 目标

| 目标 | 含义 |
|------|------|
| `check` | 校验 `secrets.sh`、`input/$(STEM).pdf` 等 |
| `a-all` | `a1-convert`（整本路径）→ `a2`～`a4`；拆分场景下请按需用手动 `cp` + `make a2-strip`… |
| `b-all` | `b1`～`b6` → **`b7-index-dual`（H4 + H2 索引）** |
| `b7-index`、`b7-index-h1`、`b7-index-h2` | 单份索引可调深度 |
| `a34-detached`、`run_a*_detached.sh` | 长任务后台/nohup 示例 |

更多变量（`FAST_A1`、`IMAGES_SCALE`、`AIDOC_LLM_MAX_RETRIES`、`AIDOC_INDEX_MAX_CHUNK_CHARS`、`INDEX_DEPTH` 等）见 **`Makefile` 顶部注释**。

---

## Shell 与本机路径约定

- **在 WSL 中执行 **`*.sh`** 须为 LF 换行**；若有 `set: pipefail` 一类乱码报错，在项目根运行 **`python3 scripts/_fix_sh_lf.py`**。
- **`scripts/backup_output_logs.sh`**：整包备份 **`output`** + **`logs`** 至 **`archive/`**，并新建空目录，便于新书 **`STEM`** 下一轮全流程。

---

## B5 图描述 JSON 路径

**`figure_schemas/*.json`** 中的 **`image_path`** 新版本写为 **相对 `output/` 根目录的路径**（与 `*_merged_images/` 一致），便于换机或与 `archive` 一并搬迁；历史存档可用 **`python3 py/fix_figure_schemas_image_path.py`** 批量规范化。

---

## 文档索引

| 文档 | 内容 |
|------|------|
| [RUN_REPRO.md](RUN_REPRO.md) | 复现说明、可选 RAG |
| [REPO_SYNC.md](REPO_SYNC.md) | 与上游 `py/` 脚本的同步说明 |
| [runs/BACKLOG.md](runs/BACKLOG.md) | 规划与待增强 |
| [runs/IMAGE_NAMING.md](runs/IMAGE_NAMING.md) | 图链、`fig_`、`images.json` 对应关系 |

---

## License / 致谢

本项目所用 PDF 示例与第三方模型/API 由各使用者自行合规取得与配置。
