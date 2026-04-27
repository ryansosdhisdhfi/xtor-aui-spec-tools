#!/usr/bin/env bash
# 在 WSL 中先安装 **CPU 版** PyTorch，再安装 requirements.txt，避免拉取多枚 nvidia-* 超大 wheel 导致常卡死/断线。
# 用法（在本仓库根、已建 .venv 且已 activate）:
#   bash scripts/install_deps_cpu_wsl.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [[ -z "${VIRTUAL_ENV:-}" ]]; then
  echo "请先: source .venv/bin/activate" >&2
  exit 1
fi
# PyTorch 官方 CPU 轮子（体积远小于带 CUDA 的依赖链）
# -v：安装过程持续有输出，便于判断未卡死（需要更细可改为 -vv）
pip install --upgrade pip -v
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu -v --default-timeout=1000
pip install -r requirements.txt -v --default-timeout=1000
echo "Done. 若需 GPU/ CUDA，请改用手动安装 PyTorch（CUDA 版）后再 pip install -r。"
