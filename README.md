# xtor-aui-spec-tools

**XTOR** 项目：面向规范文档的 **PDF → Markdown → 清洗 → 图语义链 → 索引** 管线。本仓库是**独立维护**的应用包：编排、脚本与配置都在这里，不依赖其它历史仓库路径。

## 本仓库提供什么

| 内容 | 说明 |
|------|------|
| `Makefile` | 串联 a1–a4（正文基线）与 b2–b7（图链 + 终稿索引） |
| `py/` | 管线 Python 脚本（与上游 **Aidoc 工具链**同族，可随升级替换） |
| `input/` | 放置待处理 PDF（见 `input/README.txt`） |
| `output/`、`logs/` | 运行产物与日志（默认不入库） |

## 运行依赖（必须）

执行 `make` 时，需要一个本机上的 **Aidoc 工具链根目录**（环境变量 **`REPO`**）：

- 该目录下应有：根级 `aidoc_*.py`、`rag_ingest.py`、`rag_full.py` 等，以及你在该目录创建的 **Python 虚拟环境**（推荐 `.venv`）。
- `make` 会在 `REPO` 下作为工作目录调用解释器，脚本入口使用**本仓库**的 `py/`。

**未设置 `REPO` 时**，`Makefile` 默认将 `REPO` 设为本目录的**上两级目录**（兼容「本仓与工具链根为兄弟目录」等布局）。**推荐**始终显式设置，避免歧义：

```bash
export REPO="/path/to/your/aidoc-toolchain-root"
cd /path/to/xtor-aui-spec-tools
make check
```

WSL 下路径示例：`export REPO="/mnt/c/Users/you/work/aidoc-toolchain"`。

## 环境与快速开始（建议 WSL）

1. 在 **Aidoc 工具链根** 创建 venv 并安装依赖（与本仓 `requirements.txt` 对齐即可）：
   ```bash
   cd "$REPO"
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r /path/to/xtor-aui-spec-tools/requirements.txt
   ```
2. 在本仓根目录：`cp secrets.sh.example secrets.sh`，填写 `API_URL`（须含 `/v1`）、`API_KEY`、`MODEL`。
3. 将规范 PDF 放入 `input/`，文件名与 `Makefile` 中 `STEM` 一致（默认 `pcie61_ch2.pdf`）。
4. `make check` → `make all`（或分步 `make a1-convert` 等）。

GPU、OCR、可选环境变量见 `Makefile` 顶部注释。

## 文档索引

| 文档 | 内容 |
|------|------|
| [RUN_REPRO.md](RUN_REPRO.md) | 复现步骤、可选 RAG |
| [REPO_SYNC.md](REPO_SYNC.md) | `py/` 与上游工具链脚本的维护说明 |

## Makefile 目标摘要

| 目标 | 含义 |
|------|------|
| `a1-convert` … `a4-codeblocks` | PDF → 清洗与结构修复 |
| `a5-index` | 首次索引（可选） |
| `b2` … `b7` | 图链与 enriched 终稿索引 |
| `all` | `a-all` + `b-all` |

## 无 `make` 时

参考 `Makefile` 中的命令行，或阅读 `scripts/run_with_log.sh`，在 `REPO` 下用相同参数直接调用 `py/` 内脚本。

---

**说明**：本 README 仅描述 **本 Git 仓库**。Aidoc 工具链根目录若由团队内部发布，请以你们实际路径与版本为准。
