# Mac PCIe 6.1 A1 Prompt

把下面提示词交给 Mac 端的 AI/终端助手使用。目标只跑 A1：PDF -> Markdown + image assets + images.json。

```text
你在一台 Apple Silicon Mac 上，目标是试跑 xtor-aui-spec-tools 的 A1，把 PCIe 6.1 PDF 整本转换成 Markdown 和图片资源。

请按以下步骤执行，不要先跑 A2/B 段。

1. 克隆仓库并进入目录：

   git clone git@github.com:ryansosdhisdhfi/xtor-aui-spec-tools.git
   cd xtor-aui-spec-tools

   如果没有 SSH key，就改用：
   git clone https://github.com/ryansosdhisdhfi/xtor-aui-spec-tools.git

2. 安装系统依赖：

   brew install qpdf tesseract tesseract-lang poppler

3. 建 Python 环境：

   python3 -m venv .venv
   source .venv/bin/activate
   python -m pip install --upgrade pip setuptools wheel
   pip install -r requirements.txt -v --default-timeout=1000

4. 准备输入 PDF：

   mkdir -p input
   把 PCIe 6.1 PDF 放到：
   input/NCB-PCI_Express_Base_6.1.pdf

   注意：PDF 不在 Git 里，需要从本机已有文件或合规来源放入。

5. A1 整本优先试跑：

   export REPO="$(pwd)"
   make check STEM=NCB-PCI_Express_Base_6.1
   DEVICE=mps IMAGES_SCALE=4.0 PROGRESS_INTERVAL=10 make a1-convert STEM=NCB-PCI_Express_Base_6.1

6. 成功标准：

   确认以下产物存在：
   output/NCB-PCI_Express_Base_6.1.md
   output/NCB-PCI_Express_Base_6.1.images.json
   output/NCB-PCI_Express_Base_6.1_images/

   并检查 logs/pipeline.log 或 logs/run_*_a1-convert.log 末尾 exit=0。

7. 如果 DEVICE=mps 失败，直接回退 CPU：

   DEVICE=cpu IMAGES_SCALE=4.0 PROGRESS_INTERVAL=10 make a1-convert STEM=NCB-PCI_Express_Base_6.1

8. 如果整本 A1 因内存、超时或 Docling 失败，再走分段回退：

   make pdf-split STEM=NCB-PCI_Express_Base_6.1
   DEVICE=mps IMAGES_SCALE=4.0 PROGRESS_INTERVAL=10 make a1-batch STEM=NCB-PCI_Express_Base_6.1
   make merge-parts-full STEM=NCB-PCI_Express_Base_6.1

   分段合并后主产物是：
   output/NCB-PCI_Express_Base_6.1_merged.md
   output/NCB-PCI_Express_Base_6.1_merged.images.json
   output/NCB-PCI_Express_Base_6.1_merged_images/

9. 请最后汇报：

   - Mac 型号、内存、macOS 版本
   - Python 版本
   - `DEVICE=mps` 是否成功
   - A1 用时
   - 产物大小
   - 如果失败，贴 logs/run_*_a1-convert.log 最后 80 行
```

