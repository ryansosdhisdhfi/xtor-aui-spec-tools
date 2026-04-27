# pcie61_ch2_part003 — Ch2 分片（part_003）独立运行包

本目录在**完整仓库**中的位置：`ai-doctool2-master/user-run/pcie61_ch2_part003/`。  
`py/` 下为与仓库根**同版本**的脚本副本（含 `aidoc_convert_assets`、`strip`、图链 B 段、`batch_describe`、`inject` 等），便于**只拷这一目录**仍能在「仓库根」环境下跑通；**不包含**虚拟环境或模型权重。

## 文档索引（按阅读顺序）

| 文档 | 内容 |
|------|------|
| [RUN_REPRO.md](RUN_REPRO.md) | 从零复现：venv、WSL、`make`、**可选** `rag_ingest` + `rag_full`、示例配置 |
| [REPO_SYNC.md](REPO_SYNC.md) | `py/` 与仓库根主脚本的**同步策略**与何时更新副本 |
| [rag_config_pcie61_ch2.example.json](rag_config_pcie61_ch2.example.json) | 复制为本地 `rag_config_pcie61_ch2.json` 后，在仓库根跑向量导入/完整版 RAG |
| [../../docs/GIT_AND_GITHUB.md](../../docs/GIT_AND_GITHUB.md) | 整仓上 GitHub vs 子目录子仓、`.gitignore`、LFS/大文件 |
| [../../docs/RAG_ISSUE_TAXONOMY.md](../../docs/RAG_ISSUE_TAXONOMY.md) | RAG 六类问题 + `rag_full -v` 调试说明 |
| [../../docs/STAKEHOLDER_QUESTIONS.md](../../docs/STAKEHOLDER_QUESTIONS.md) | 与产品/架构需对齐的短问题表 |
| [../../CONTRIBUTING.md](../../CONTRIBUTING.md) | 贡献与 Cursor 规则入口 |

## 与「Git / GitHub」的关系

- 若你只在**本机**执行过 `git init` + `commit`，代码**仍在你的电脑里**，没有自动传到 GitHub。
- 传到 GitHub 需要：在 GitHub 上新建仓库 → `git remote add origin ...` → `git push`。
- 本目录自带 `Makefile` + `requirements.txt` + `README`，与是否使用 GitHub **无关**。

**只想版本管理这一包（不拖整个 `ai-doctool2-master`）**：在**本目录**单独 `git init`，依赖根目录的 `.gitignore` 时可用本目录的 `.gitignore`（已忽略 `output/`、`logs/`、`secrets.sh`）。仍建议在说明里写清：运行时要有一个「含 venv 的父仓库或自备 Python 环境」。

### 推荐：小仓库与乱的大仓库分开

1. 在**新目录**建干净 Git（**不要**在已有 `ai-doctool2-master/.git` 里再嵌套一层，除非大仓库已用 `.gitignore` 忽略本路径，否则很绕）。做法示例：把本文件夹**整份复制**到例如 `C:\work\pcie61_ch2_part003`，在**该副本**里 `git init` → `add` / `commit` → GitHub 建空库 → `git remote add` → `git push`。
2. 运行管线**仍需要**本机一份「主仓库根」`ai-doctool2-master`（或同名）：里面有根目录 `aidoc_*.py`、你装的 `.venv` 等。`Makefile` 里默认认为本包在 `主仓库根/user-run/pcie61_ch2_part003`，即 `REPO=主仓库根`。
3. 若小仓库 clone 在**其他路径**（不是 `.../user-run/pcie61_ch2_part003`），在跑 `make` 前**指定**主仓库根，例如：  
   `REPO=/path/to/ai-doctool2-master make check`（WSL 下用 `/mnt/c/...` 这种路径）。  
4. 大仓库若不再维护：可保留只作**本地运行依赖**，你日常 `git` 只操作小仓即可。

## 目录结构

| 路径 | 说明 |
|------|------|
| `input/` | 见 [input/README.txt](input/README.txt)：`pcie61_ch2.pdf`（多由 `pcie6.1-full/parts/part_003.pdf` 复制重命名；**不随 Git 提交 PDF**，由本机/网盘提供） |
| `py/` | Python 脚本副本（与主仓库同步维护） |
| `output/` | 运行产物（默认被 `.gitignore` 忽略，勿提交大文件） |
| `logs/` | 每次 `make` 目标通过 `scripts/run_with_log.sh` 记录**时间、完整命令、退出码、耗时** |
| `secrets.sh` | 从 `secrets.sh.example` 复制后填写 API（勿提交） |

## 环境（建议 WSL2）

1. 在**仓库根** `ai-doctool2-master` 创建 venv 并安装依赖：  
   `pip install -r user-run/pcie61_ch2_part003/requirements.txt`  
   （与根目录 `requirements.txt` 一致亦可。）
2. Docling / 块一转换：需要 **GPU 时** 安装对应 `torch`（见主项目习惯）；仅 CPU 也可跑，较慢。
3. B4 OCR：本机需安装 [Tesseract](https://github.com/tesseract-ocr/tesseract) 及中英语言包；Python 侧见 `requirements.txt` 中 `pytesseract`、`Pillow`。
4. 在**本目录**执行：  
   `cp secrets.sh.example secrets.sh` 并填写 `API_URL`（须含 `/v1`）、`API_KEY`、`MODEL`。

## 使用 Makefile（推荐）

在 **WSL** 或已安装 `make` + `bash` 的环境：

```bash
cd /path/to/ai-doctool2-master/user-run/pcie61_ch2_part003
# 若使用仓库根 venv:
source ../../.venv/bin/activate

make check     # 检查 input 下 PDF 与 secrets.sh
make help
make all       # = A 段 a1–a4 + B 段 b2–b7（不含可选的 a5、b1）
```

| 目标 | 含义 |
|------|------|
| `a1-convert` | PDF → MD + `*.images.json` + `*_artifacts/` |
| `a2-strip` `a3-hierarchy` `a4-codeblocks` | 正文基线（需 LLM） |
| `a5-index` | 第一次 index（**可选**） |
| `b1-rewrite` | 图链改相对路径（**可选**） |
| `b2-filter` … `b7-index` | 图语义链 + 终稿二次 index |
| `a-all` / `b-all` / `all` | 组合目标 |

环境变量（可选，见 `Makefile` 顶注释）：`DEVICE`（如 `cuda`）、`PICTURE_DESC=1`（块一开 Docling 内置图描，**很慢**）、`OCR_LANG`（如 `eng+chi_sim`）、`INDEX_DEPTH` 等。

### 运行记录

每步在 `logs/` 下生成：

- `run_<时间戳>_<pid>.log`：该次完整标准输出
- `pipeline.log`：追加的总流水
- `00_timings.txt`：每行**时间、退出码、耗时、命令**
- `00_commands.txt`：仅命令行，便于复盘

**没有 `make` 时**：可阅读 `scripts/run_with_log.sh`，用同一方式手动包一层，或直接执行 `py/` 中脚本（命令行与主仓库文档一致，见 `../pcie6.1_full_e2e/RUNBOOK_块一与步骤2-5.md`）。

## 与「全仓库只维护一处」的约定

- **主源**：以仓库根 `aidoc_*.py`、`filter_images.py` 等为准；本目录 `py/` 在重大改动后应**从根目录再拷一份**或手动合并，避免长期分叉。
- 本包已含 `py/rewrite_md_image_paths.py`（与根目录同文件），便于不依赖根目录多一个文件。

## 更轻量的代替方案（不装 make）

- **PowerShell** 可写小 `.ps1` 顺序调用；**npm `just`** 或 **Python `invoke`/`nox`** 也可替代 Make。选用 Make 是因为零依赖、只依赖 bash、与现有 `run_sequential_w_stats.sh` 风格接近。

---

有问题对照主项目 `user-run/项目上下文_给AI.md` 与 `RUNBOOK_块一与步骤2-5.md`。
