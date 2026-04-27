# xtor-aui-spec-tools

**XTOR** 项目：面向规范文档的 **PDF → Markdown → 清洗 → 图语义链 → 索引** 管线。本仓库是**独立**应用包：编排、`py/` 脚本与配置都在此；**不依赖**再下载另一份「历史大仓」才能跑主流程。

## 本仓库提供什么

| 内容 | 说明 |
|------|------|
| `Makefile` | 串联 a1–a4（正文基线）与 b2–b7（图链 + 终稿索引） |
| `py/` | 管线所需 Python 脚本；`make` 调用的就是这里的入口 |
| `input/` | 待处理 PDF（见 `input/README.txt`） |
| `output/`、`logs/` | 运行产物与日志（默认不入库） |

---

## 推荐：单仓闭环（clone → 装依赖 → 跑 `make`）

**`REPO` 是 `make` 的工作目录，且默认在此目录下找 `.venv`。** 你完全可以把 **`REPO` 设成本仓库根目录**，只维护**这一份 Git**。

在 **WSL** 或 **bash** 中：

```bash
git clone https://github.com/<你的用户名>/xtor-aui-spec-tools.git
cd xtor-aui-spec-tools

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp secrets.sh.example secrets.sh
# 编辑 secrets.sh：填写 API_URL（须含 /v1）、API_KEY、MODEL

# 将 PDF 放入 input/，文件名与 Makefile 中 STEM 一致（默认见 input/README.txt）

export REPO="$(pwd)"
make check
make all
```

WSL 下若写绝对路径，例如：

`export REPO="/mnt/c/Users/你/桌面/xtor-aui-spec-tools"`

**说明**：`make` 会 `cd` 到 `REPO`，再执行本仓 `py/` 中的脚本；**主流程不依赖** `REPO` 下是否还有**另一套** `aidoc_*.py` 副本——`py/` 里已经带了管线入口。

**未设置 `REPO` 时**，`Makefile` 会把 `REPO` 设成「本目录上溯两级」——这是给**旧式目录结构**的兼容。若本仓**单独**放在 `Desktop/xtor-aui-spec-tools` 这类路径，**默认往往不对**，请务必用上面的 **`export REPO="$(pwd)"`**（在**本仓根**执行时）。

---

## 可选：两种目录与另一套工具根（进阶）

若团队**刻意**把 venv 与「别处的工具根」放在**兄弟目录**（例如大仓与 `xtor-aui-spec-tools` 同级），可令 **`REPO` 指向那棵根**，但仍用本仓的 `py/` 路径。此时在说明里**显式写出 `REPO` 的绝对路径**即可。

常见场景：**RAG 向量化**要运行 `rag_ingest.py` / `rag_full.py` 时，若**本仓根下没有**这两份文件，可：

- 从上游 **Aidoc 工具链** 拷贝到 `REPO` 根，或  
- 在**另一已 clone 的仓库根** 设 `REPO` 并执行 `python rag_ingest.py`（`config` 里路径仍指**本仓** `output/`，见 [rag_config_pcie61_ch2.example.json](rag_config_pcie61_ch2.example.json)）。

**仅跑 `make` 管线**（a1–b7）**不需要**上述两步。

---

## 文档索引

| 文档 | 内容 |
|------|------|
| [RUN_REPRO.md](RUN_REPRO.md) | 与上文一致的复现、可选 RAG |
| [REPO_SYNC.md](REPO_SYNC.md) | `py/` 与上游脚本的维护说明 |

## Makefile 目标摘要

| 目标 | 含义 |
|------|------|
| `a1-convert` … `a4-codeblocks` | PDF → 清洗与结构修复 |
| `a5-index` | 首次索引（可选） |
| `b2` … `b7` | 图链与 enriched 终稿索引 |
| `all` | `a-all` + `b-all` |

## 无 `make` 时

参考 `Makefile` 中的命令行，或 `scripts/run_with_log.sh`，在**已 `export REPO` 且已 `activate` venv** 的前提下，以相同参数直接调用 `py/` 内脚本。

GPU、OCR、可选环境变量见 `Makefile` 顶部注释。
