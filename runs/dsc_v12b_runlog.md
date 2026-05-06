# DSC v1.2b 全量跑通 — 操作与日志索引

- **源文件（本机）**: `C:\Users\0\Downloads\DSC v1.2b.pdf`
- **STEM（Makefile）**: `dsc_v12b` → 输入为 `input/dsc_v12b.pdf`
- **仓库根**: `xtor-aui-spec-tools`

## 第 1 步：准备输入与 STEM

已在仓库中设置 `STEM := dsc_v12b`。将源 PDF 复制为无空格的文件名，避免 shell/make 问题。

**WSL 中（推荐）**：

```bash
cp "/mnt/c/Users/0/Downloads/DSC v1.2b.pdf" /mnt/c/Users/0/Desktop/AI/xtor-aui-spec-tools/input/dsc_v12b.pdf
ls -la /mnt/c/Users/0/Desktop/AI/xtor-aui-spec-tools/input/dsc_v12b.pdf
```

**或 PowerShell**：

```powershell
Copy-Item -LiteralPath "C:\Users\0\Downloads\DSC v1.2b.pdf" -Destination "C:\Users\0\Desktop\AI\xtor-aui-spec-tools\input\dsc_v12b.pdf"
```

**已完成（自动复制）**：`input/dsc_v12b.pdf`，约 **1.41 MB**（1407717 字节）。


## 第 2 步：环境与检查

```bash
cd /mnt/c/Users/0/Desktop/AI/xtor-aui-spec-tools
source .venv/bin/activate
export REPO="$(pwd)"
make check
```

**预期**: 通过（存在 `input/dsc_v12b.pdf`、`secrets.sh`；若无 `.venv` 会仅**提示**，不失败）。

**若从未建过 venv**，先执行（仍在 WSL、本仓根）：

```bash
cd /mnt/c/Users/0/Desktop/AI/xtor-aui-spec-tools
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export REPO="$(pwd)"
make check
```

说明：`make` 使用 `bash -eu`；若 `check` 曾报 `VIRTUAL_ENV: unbound variable`，请拉取含 **Makefile 修复** 的版本（对未激活 venv 使用 `${VIRTUAL_ENV:-}` 判断）。


## 第 3 步起：按阶段 make

`scripts/run_with_log.sh` 会把每步完整输出写到 `logs/run_<时间戳>_<pid>.log`，并追加到 `logs/pipeline.log`；`logs/00_timings.txt`、`00_commands.txt` 有命令与耗时。

| 阶段 | 命令 | 说明 |
|------|------|------|
| 块一 | `make a1-convert` | PDF → MD，**最慢**，需 Docling/资源 |
| 基线 | `make a2-strip` | 需 LLM API |
| | `make a3-hierarchy` | 需 API |
| | `make a4-codeblocks` | 需 API |
| 图链 | `make b-all` 或 b2…b7 分步 | 需 Tesseract、VLM 等，见 `make help` |
| **一键** | `make all` | 等同于 `a-all` + `b-all` |


## 本记录中追加的原始命令 / 结果摘要

（每步把终端里关键几行、或 `tail -20 logs/00_timings.txt` 贴在此）

