# 图片：Markdown 路径、JSON、`fig_` 命名如何对应

本文说明 **a1（`aidoc_convert_assets.py`）** 产出中，**正文里的图链**、**磁盘上的文件名**、**`<STEM>.images.json`** 三者关系，避免误以为「JSON 里的名字 = MD 里写的文件名」。

## 结论（先看这个）

| 问题 | 答案 |
|------|------|
| JSON 里 `image_id`（如 `fig_0012`）和 MD 里 `![](...)` 的路径**是不是同一个字符串**？ | **通常不是**。MD 由 **Docling** 写出一套路径；JSON 由我们的 **`export_image_index`** 按 `fig_0000` 顺序另编一套 id。 |
| 那还能不能 **一一对应**？ | **能**，靠**顺序**：第 **1** 个图（MD 里从上到下第 1 个 `![...](...)`）↔ JSON 数组里下标 **0**（`fig_0000`），第 2 个图 ↔ 下标 1（`fig_0001`），以此类推。 |
| `image_id` 在 JSON 里是否唯一？ | **是**，同一文件里 `fig_0000…fig_NNNN` 不重复。 |
| 若某次 MD 图个数和 JSON 条数不一致？ | **`filter_images.py` 会警告**并按**较短长度**做 `zip`，需要人工检查导出是否完整。 |

## a1 实际写盘的两套资源（都针对「同一些图」）

1. **Docling 原生落盘（决定 MD 里 `![](...)` 写什么）**  
   见 `save_markdown_with_assets` → `doc.save_as_markdown(..., image_mode=referenced)`。  
   典型形态（随 Docling 版本可能略有差异）：

   - 目录：`<output_stem>_artifacts/`（例如 `dsc_v12b_artifacts/`）
   - 文件名：`image_<6 位序号>_<内容哈希>.png`（例：`image_000012_c636eb44....png`）

   这里的**序号**一般与文档中**图片出现顺序**一致，但**带哈希**，与下面第 2 套**不是同一套文件名**。

2. **本仓库 `export_image_index`（生成 `*.images.json` 与可选 `*_images/`）**  
   见 `py/aidoc_convert_assets.py` 中 `export_image_index`：

   - 对 `document` 里每个 `PICTURE` 按 **iterate_items 顺序**编号：`fig_0000`、`fig_0001`、…
   - 若成功从节点 `save` 出 PNG，则写入 `<stem>_images/fig_NNNN.png`，并把**相对 `output/` 的路径**写入该条目的 `image_path`。
   - 若当前 Docling/环境下 `image` 对象**不能 `save`**，则 `image_path` 可能为 **`null`**（你本地若见全为 `null`，即属此类）；**JSON 仍有 `image_id` 与 `page_no`/`bbox`**，下游仍可按**顺序**与 MD 对齐。

**因此**：**不要**用「`fig_0012` 是否等于某段 `image_000012_xxx` 字符串」来判断是否同一张图；应用 **「第几个图」** 对齐。

## 下游脚本如何对齐（设计约定）

- **`filter_images.py`**：从 MD 解析出图链列表，与 `images.json` 的 **list 按索引 zip**；若数量不等会打 **警告**。见其中 `len(links) != len(items)` 分支。  
- **`b1-rewrite` 等**：改的是**路径字符串**与 JSON 内路径键，不改变「第 i 个图」的语义。

## 清洗之后（`*_clean.md`）

`a2` 等会改写正文，图链可能变化；**b2 及以后**应以**当前** `*_clean.md` 与**当时**导出的 `*.images.json`（或 filtered 结果）为准，道理仍是：**顺序对位 + 条数一致**。

## 若要「文件名级」强绑定（未来）

需在代码里统一：例如 a1 只保留一套命名，或增加 `md_image_basename` / `docling_artifact_index` 等字段写入 JSON。当前实现**未**保证「MD 路径 basename = `fig_xxxx`」，**仅保证顺序一致**。
