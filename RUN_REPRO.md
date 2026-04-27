# 复现说明（xtor-aui-spec-tools）

从零 clone 到跑通**正文 + 图链**；**RAG 向量化**为可选。环境以 **WSL + bash** 为例。

## 1. 克隆

```bash
git clone https://github.com/<你的用户名>/xtor-aui-spec-tools.git
cd xtor-aui-spec-tools
```

## 2. Python 依赖（推荐：venv 就建在本仓根）

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 3. 密钥与输入

- `cp secrets.sh.example secrets.sh`，填写 `API_URL`（须含 `/v1`）、`API_KEY`、`MODEL`。
- 将 PDF 放入 `input/`，见 [input/README.txt](input/README.txt)。

## 4. 跑管线（`REPO` = 本仓根）

在**本仓库根目录**执行（与 [README](README.md) 一致）：

```bash
cd /path/to/xtor-aui-spec-tools
source .venv/bin/activate
export REPO="$(pwd)"
make check
make all
```

**不要**依赖「未设置 `REPO` 时默认上溯两级」——单独 clone 在桌面时该默认常错。务必 `export REPO="$(pwd)"`。

## 5. 可选：完整版 RAG（`rag_ingest` + `rag_full`）

本仓**主流程不自带** `rag_ingest.py` / `rag_full.py` 时，可任选其一：

- 将上述文件放到 **`REPO` 根**（与单仓方案一致时，即本仓根），再执行；或  
- 在**已含有这两脚本的另一工具根** 下 `cd` 与 `python`，`--config` 中路径指向**本仓** `output/`（见 [rag_config_pcie61_ch2.example.json](rag_config_pcie61_ch2.example.json)）。

`rag_ask`（无向量）可在任意有该脚本的 Python 环境下，对**本仓** `output/` 的 index + md 调用，见 `rag_ask.py --help`。

## 6. 产物

`output/`、`logs/` 默认不提交；大文件与密钥勿入库。
