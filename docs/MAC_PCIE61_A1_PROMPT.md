# Mac PCIe 6.1 A+B Runbook

把下面提示词交给 Mac 端的 AI/终端助手使用。目标是生成新版 PCIe 6.1 A+B 产物，作为后续 C 段权威输入。

```text
你在一台 Apple Silicon Mac 上，当前目录是 xtor-aui-spec-tools 仓库根目录。

目标：
- 生成 PCIe 6.1 的新版 A+B 产物。
- 本地旧 PCIe 产物没有 pdf-page 标记，B5 schema 也没有 digital_ic_semantics，因此不要复用旧产物作为 C 段权威输入。
- 如果整本 A1 跑不完，就使用分段 A1；分段合并后继续 A2/A3/A4/B-all。

0. 拉最新代码：

   git pull --ff-only
   git log --oneline -5

1. 安装/确认系统依赖：

   brew install qpdf tesseract tesseract-lang poppler

2. 建 Python 环境：

   python3 -m venv .venv
   source .venv/bin/activate
   python -m pip install --upgrade pip setuptools wheel
   pip install -r requirements.txt -v --default-timeout=1000

3. 确认输入 PDF：

   仓库已提供示例 PDF：

   input/NCB-PCI_Express_Base_6.1.pdf

   先确认文件存在：

   ls -lh input/NCB-PCI_Express_Base_6.1.pdf

4. 设置通用环境：

   export REPO="$(pwd)"
   export STEM=NCB-PCI_Express_Base_6.1
   make check STEM="$STEM"

5. 先试整本 A1：

   DEVICE=mps IMAGES_SCALE=4.0 PROGRESS_INTERVAL=10 make a1-convert STEM="$STEM"

   如果 mps 失败，再试 CPU：

   DEVICE=cpu IMAGES_SCALE=4.0 PROGRESS_INTERVAL=10 make a1-convert STEM="$STEM"

6. 如果整本 A1 仍然失败，改走分段 A1：

   make pdf-split STEM="$STEM"
   DEVICE=mps IMAGES_SCALE=4.0 PROGRESS_INTERVAL=10 make a1-batch STEM="$STEM"
   make merge-parts-full STEM="$STEM"

   合并后把 _merged 对齐成后续 A2/B 的主输入：

   cp -f "output/${STEM}_merged.md" "output/${STEM}.md"
   cp -f "output/${STEM}_merged.images.json" "output/${STEM}.images.json"

   分段 A1 中断后续跑：

   A1_START_INDEX=<N> A1_SKIP_EXISTING=1 DEVICE=mps IMAGES_SCALE=4.0 PROGRESS_INTERVAL=10 make a1-batch STEM="$STEM"
   make merge-parts-full STEM="$STEM"
   cp -f "output/${STEM}_merged.md" "output/${STEM}.md"
   cp -f "output/${STEM}_merged.images.json" "output/${STEM}.images.json"

7. A1 成功标准：

   确认以下产物存在：

   output/${STEM}.md
   output/${STEM}.images.json
   output/${STEM}_images/ 或 output/${STEM}_merged_images/

   检查页码标记：

   grep -c '<!-- pdf-page:' "output/${STEM}.md"

   如果输出为 0，停止并汇报，不要继续 A2/B。

8. A1 成功后继续 A2/A3/A4 和 B-all：

   确认 secrets.sh 已配置 API_URL / API_KEY / MODEL。

   make a2-strip a3-hierarchy a4-codeblocks STEM="$STEM"
   make b-all STEM="$STEM"

9. 新版 A+B 成功标准：

   必须存在：

   output/${STEM}_clean.md
   output/${STEM}_enriched.md
   output/${STEM}_enriched.index.h4.json
   output/${STEM}_enriched.index.h2.json
   output/figure_schemas/

   检查新版页码：

   grep -c '<!-- pdf-page:' "output/${STEM}_enriched.md"

   检查新版 B5 schema：

   python3 - <<'PY'
import json
from pathlib import Path
figs = sorted(Path("output/figure_schemas").glob("NCB-PCI_Express_Base_6.1*.json"))
digital = 0
figure_kind = 0
for p in figs:
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        continue
    digital += int("digital_ic_semantics" in data)
    figure_kind += int("figure_kind" in data)
print("figure_schemas:", len(figs))
print("digital_ic_semantics:", digital)
print("figure_kind:", figure_kind)
if not figs or digital == 0:
    raise SystemExit("B5 schema does not look like the new v2 output")
PY

10. 最后汇报：

   - Mac 型号、内存、macOS 版本
   - Python 版本
   - 当前 git commit
   - A1 是整本成功还是分段成功；用 mps 还是 cpu
   - A1/A2-A4/B-all 各阶段是否 exit=0
   - pdf-page 标记数量
   - figure_schemas 总数
   - digital_ic_semantics 数量
   - 关键产物大小
   - 如果失败，贴对应 logs/run_*.log 最后 80 行

注意：
- 不要把旧本地 PCIe 输出作为 C 段权威输入。
- 新版 C 段必须等待这批 Mac 生成的 A+B 产物完成后再开始。
```
