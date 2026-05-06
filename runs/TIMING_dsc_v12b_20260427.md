# 全链路耗时记录 — dsc_v12b（2026-04-27）

来源：`logs/00_timings.txt` 中各步 `duration_s` 之和（`make all` 等价于 a1→b7 连续成功一次）。

| 步骤 | 耗时 (s) | 约 |
|------|-----------|-----|
| a1-convert | 223 | 3.7 min |
| a2-strip | 2 | |
| a3-hierarchy | 117 | 2.0 min |
| a4-codeblocks | 0 | |
| b1-rewrite | 1 | |
| b2-filter | 0 | |
| b3-context | 1 | |
| b4-ocr | 7 | |
| b5-describe | 2415 | 40.3 min |
| b6-inject | 0 | |
| b7-index | 569 | 9.5 min |

- **各步合计**：**3335 s** ≈ **55 min 35 s**  
- **主要占比**：b5 VLM 批量描述 + b7 索引建摘要（LLM 调用多）

## 与产物一起备份

若要把**本跑完整 `output/` + `logs/`** 留档（不仅本文），在仓库根执行：

```bash
bash scripts/backup_output_logs.sh
```

会在 `archive/` 下出现带时间戳的 `output_*`、`logs_*`；**不要删 `archive/`**。

大 PDF 另跑前：改 Makefile 的 `STEM`、将 PDF 放 `input/<STEM>.pdf`，再 `export REPO="$(pwd)" && make check && make all`。
