# `pcie61_ch2_part003` 复现单

从零 clone 到跑通**正文+图链+可选 RAG**的最低步骤。环境以 **WSL2 + 仓库根 venv** 为主（与 [README](README.md) 一致）。

## 1. 准备仓库与 Python

```bash
cd /path/to/ai-doctool2-master
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# 与上面等价亦可:
# pip install -r user-run/pcie61_ch2_part003/requirements.txt
```

Windows 上若无 `make`/`bash`：可装 WSL2，或用 Git Bash，或按 README 用 PowerShell 逐条调 `py/*.py`（命令行与 [Makefile](Makefile) 中一致）。

## 2. 输入与密钥

- **PDF**：`input/pcie61_ch2.pdf`  
  来源见 [input/README.txt](input/README.txt)（如从 `user-run/pcie6.1-full/parts/part_003.pdf` 复制重命名）。
- **API**：`cp secrets.sh.example secrets.sh`，填写 `API_URL`（须含 `/v1`）、`API_KEY`、`MODEL`。

## 3. 检查

```bash
cd user-run/pcie61_ch2_part003
make check
```

## 4. 跑管线

```bash
make all
```

含义：`a-all`（a1–a4：convert → strip → hierarchy → codeblocks）+ `b-all`（b2–b7：图链与终稿 `aidoc_index` 于 `*_enriched.md`）。  
**不**含默认目标外的可选步骤：`a5-index`、`b1-rewrite`；需要时单独 `make a5-index` 或 `make b1-rewrite`。

### 重要环境变量（可选，见 [Makefile](Makefile) 头部）

| 变量 | 说明 |
|------|------|
| `DEVICE` | Docling 设备，如 `cuda` 或 `cpu` |
| `INDEX_DEPTH` | `aidoc_index` 深度，默认 `4` |
| `PICTURE_DESC` | 设为 `1` 时块一开启图描，**极慢** |
| `OCR_LANG` | Tesseract 语言，如 `eng+chi_sim` |

## 5. 产物位置（本机，默认不入 Git）

- `output/pcie61_ch2*.md`、`*_enriched.md`、`*.index.json` 等。  
- `logs/run_*.log`、`pipeline.log`、`00_timings.txt`

## 6. 可选：完整版 RAG（向量化 + 混合检索）

在**仓库根**、已 `activate` venv 的前提下：

1. 复制 [rag_config_pcie61_ch2.example.json](rag_config_pcie61_ch2.example.json) 为如 `user-run/pcie61_ch2_part003/rag_config_pcie61_ch2.json`（该文件若含路径请**勿提交**若你本地改过密钥路径；本仓库可只提交 `*.example.json`）。  
2. 按你实际选用的 **md + index 成对** 修改 `data.index_path` / `data.doc_path`：  
   - 仅 A 段 + `make a5-index`：指向 `pcie61_ch2.index.json` 与 `*_clean.md`；  
   - 跑满 `make all`：通常用 `pcie61_ch2_enriched.index.json` 与 `*_enriched.md`。  
3. 导入向量库并问答：

```bash
cd /path/to/ai-doctool2-master
source .venv/bin/activate
python rag_ingest.py --config user-run/pcie61_ch2_part003/rag_config_pcie61_ch2.json
# 重建集合时:
# python rag_ingest.py --config ... --force

python rag_full.py --config user-run/pcie61_ch2_part003/rag_config_pcie61_ch2.json -q "你的问题" -v
```

`-v` 会打印 `vector_hits` / `keyword_hits` 等 debug，便于对照 [RAG 问题分类表](../../docs/RAG_ISSUE_TAXONOMY.md)。

### 不建向量库的简版

```bash
python rag_ask.py --index output/pcie61_ch2_enriched.index.json --doc output/pcie61_ch2_enriched.md
```

（路径相对**仓库根**；若在子目录，写绝对或 `../../...`。）

## 7. 对照样例（已有数据）

用 `user-run/replay-ch2/output/` 与根目录 `rag_config_ch2.json` 可对照「chunk 数、index、混合检索」行为，与本文档 [docs/RAG_ISSUE_TAXONOMY.md](../../docs/RAG_ISSUE_TAXONOMY.md) 中说明一致。

## 8. 与 Git 的关系

见 [docs/GIT_AND_GITHUB.md](../../docs/GIT_AND_GITHUB.md)。
