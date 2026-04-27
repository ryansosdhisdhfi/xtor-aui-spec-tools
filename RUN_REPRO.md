# 复现说明（xtor-aui-spec-tools）

从零拉本仓到跑通**正文 + 图链 + 可选 RAG**。环境以 **WSL + bash** 为例。

## 1. 克隆与 Aidoc 工具链根（`REPO`）

- 克隆本仓库到本机任意路径，例如：  
  `git clone https://github.com/<你的组织或用户名>/xtor-aui-spec-tools.git`
- 另准备 **Aidoc 工具链根目录**（含 `aidoc_*.py`、`rag_ingest.py`、`.venv` 等），记为 **`REPO`**。

## 2. Python 依赖

在 **`REPO`** 下建 venv 并安装本仓列出的依赖：

```bash
export REPO="/path/to/aidoc-toolchain"
cd "$REPO"
python3 -m venv .venv
source .venv/bin/activate
pip install -r /path/to/xtor-aui-spec-tools/requirements.txt
```

## 3. 密钥与输入

- `cp secrets.sh.example secrets.sh`（在**本仓根目录**），填写 `API_URL`、`API_KEY`、`MODEL`。
- 将 PDF 放入 `input/`，见 [input/README.txt](input/README.txt)（默认文件名为与 `Makefile` 中 `STEM` 一致，见该文件）。

## 4. 检查与跑管线

```bash
export REPO="/path/to/aidoc-toolchain"
cd /path/to/xtor-aui-spec-tools
make check
make all
```

未设置 `REPO` 时，会尝试用「本仓目录的上级路径」推断，**建议显式 `export REPO`**.

## 5. 可选：完整版 RAG（`rag_ingest` + `rag_full`）

在 **`REPO`** 下、已 `activate` venv，且 `data.index_path` / `data.doc_path` 指向**本仓** `output/` 中成对文件（与 [rag_config_pcie61_ch2.example.json](rag_config_pcie61_ch2.example.json) 中说明一致）：

```bash
cd "$REPO"
source .venv/bin/activate
python rag_ingest.py --config /path/to/your_rag_config.json
python rag_full.py --config /path/to/your_rag_config.json -q "你的问题" -v
```

`rag_ask`（无向量）可仅在需验证时在 `REPO` 下对**本仓** `output/` 的 index + md 调用，路径用绝对或相对 `REPO` 的路径，见 `rag_ask.py --help`。

## 6. 产物

`output/`、`logs/` 默认不提交；大文件与密钥勿入库。
