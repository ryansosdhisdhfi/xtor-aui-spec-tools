# `py/` 与仓库根脚本的同步约定

## 主源

- **唯一主源（canonical）**：仓库根目录下的 `aidoc_*.py`、`filter_images.py`、`extract_figure_context.py`、`ocr_figure_artifacts.py`、`batch_describe.py`、`inject_figure_enrichment.py`、`rewrite_md_image_paths.py`、`rag_*.py` 等。
- **本包 `user-run/pcie61_ch2_part003/py/`**：与主源**同一份逻辑**的**副本**，便于只拷贝本场景目录、仍在「仓库根 + venv」下执行时路径一致（Makefile 在 `REPO` 下 `cd` 后调 `$(PY)/...`）。

## 何时需要同步

在以下任一类变更后，应将**相应文件**从仓库根**复制到**本目录 `py/`，并做一次 smoke（例如 `make check` 后单步 `a1-convert` 或你正在改的脚本）：

- 根目录 `aidoc_*` 或图链、RAG 相关脚本**有功能修复或行为变更**；
- 本包 README 中承诺的能力与主仓库**文档或测试**已不一致时。

**不必**在每次无关提交后机械同步；以「行为分叉风险」为信号。

## 不要删除 `py/`

- Makefile 与文档依赖 `$(PY)/脚本名`；**删除** `py/` 会使本包无法独立工作，除非改为全部调用 `$(REPO)/aidoc_*.py`（可维护性由团队决定）。
- 若希望**减少重复**，长期方案是：Make 中 `PY := $(REPO)` 只使用根目录脚本，并在此文档写清**不再维护副本**—属架构变更，需全团队同意。

## 与「inline」副本

`../pcie61_ch2_part003_inline/` 下若再嵌套一份 `pcie61_ch2_part003`，**不要**让三处 `py/` 长期分叉；以本路径或根目录**其一**为同步目标即可。
