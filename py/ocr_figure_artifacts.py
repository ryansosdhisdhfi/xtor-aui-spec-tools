#!/usr/bin/env python3
"""
对 filter_images 产出的「保留图」做本地 OCR，输出 sidecar JSON，供 describe_image_wsl / batch_describe
的 --ocr-text 或拼进 prompt 使用。

依赖:
  - 系统已安装 tesseract，且在 PATH 中可执行 `tesseract`（WSL: sudo apt install tesseract-ocr
    英文: tesseract-ocr-eng，中文可再加 tesseract-ocr-chi-sim 等）
  - 无额外 Python 包强制要求；若已安装 pytesseract + Pillow 则走 PIL 转 RGB 再识别（对少数格式更稳）

用法:
  python ocr_figure_artifacts.py \\
    --filtered-json user-run/e2e_10p_20260422/e2e_10p.images.filtered.json \\
    -o user-run/e2e_10p_20260422/e2e_10p.ocr_by_figure.json
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def _tesseract_on_path() -> str | None:
    return shutil.which("tesseract") or shutil.which("tesseract.exe")


def ocr_file_tesseract_cli(path: Path, lang: str) -> str:
    exe = _tesseract_on_path()
    if not exe:
        raise RuntimeError(
            "未在 PATH 中找到 tesseract。WSL: sudo apt install tesseract-ocr tesseract-ocr-eng；"
            "Windows: 安装后把 tesseract 所在目录加入 PATH。"
        )
    r = subprocess.run(
        [exe, str(path), "stdout", "-l", lang],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        raise RuntimeError(f"tesseract 失败: {err}")
    return r.stdout.strip()


def ocr_file_pil_then_tesseract(path: Path, lang: str) -> str:
    import pytesseract  # type: ignore
    from PIL import Image  # type: ignore

    with Image.open(path) as im:
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        return pytesseract.image_to_string(im, lang=lang).strip()  # type: ignore[attr-defined]


def ocr_file(path: Path, lang: str, prefer_pil: bool) -> tuple[str, str]:
    """
    返回 (ocr_text, engine) engine 为 'pytesseract' 或 'tesseract_cli'。
    """
    if prefer_pil:
        try:
            return ocr_file_pil_then_tesseract(path, lang), "pytesseract"
        except ImportError:
            pass
    return ocr_file_tesseract_cli(path, lang), "tesseract_cli"


def normalize_ws(s: str) -> str:
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def main() -> int:
    ap = argparse.ArgumentParser(
        description="对 filtered.json 中 kept 的 artifact 做本地 Tesseract OCR"
    )
    ap.add_argument(
        "--filtered-json",
        required=True,
        help="filter_images 输出的 *.images.filtered.json",
    )
    ap.add_argument("-o", "--output", required=True, help="输出 JSON 路径")
    ap.add_argument(
        "--lang",
        default="eng",
        help="Tesseract 语言，如 eng 或 eng+chi_sim（需已安装对应 traineddata）",
    )
    ap.add_argument(
        "--prefer-pil",
        action="store_true",
        help="若已安装 pytesseract+pillow 则优选用 PIL 读图",
    )
    ap.add_argument(
        "--max-chars",
        type=int,
        default=0,
        help="若 >0 则截断 ocr 文本到该长度（从开头保留，避免 prompt 过长）",
    )
    ap.add_argument(
        "--no-normalize",
        action="store_true",
        help="不做空白折叠（默认会轻度 normalize_ws）",
    )
    args = ap.parse_args()

    fpath = Path(args.filtered_json).resolve()
    if not fpath.is_file():
        print(f"错误: 无文件 {fpath}", file=sys.stderr)
        return 1
    data = json.loads(fpath.read_text(encoding="utf-8"))
    kept = data.get("kept")
    if not isinstance(kept, list):
        print("错误: filtered JSON 中缺少 kept 数组", file=sys.stderr)
        return 1

    if not _tesseract_on_path():
        try:
            import pytesseract  # noqa: F401
        except ImportError:
            print(
                "错误: 未找到 tesseract 可执行文件，且未安装 pytesseract。\n"
                "请安装 Tesseract 并保证在 PATH 中，或: pip install pytesseract pillow",
                file=sys.stderr,
            )
            return 1

    items: list[dict[str, Any]] = []
    for rec in kept:
        if not isinstance(rec, dict):
            continue
        p = rec.get("_artifact_path") or rec.get("image_path")
        if not p:
            continue
        apath = Path(str(p))
        if not apath.is_file():
            items.append(
                {
                    "image_id": rec.get("image_id"),
                    "image_basename": apath.name,
                    "artifact_path": str(apath),
                    "ocr_error": f"file_not_found: {apath}",
                    "ocr_text": "",
                }
            )
            continue
        try:
            text, eng = ocr_file(apath, args.lang, args.prefer_pil)
        except Exception as e:  # noqa: BLE001
            items.append(
                {
                    "image_id": rec.get("image_id"),
                    "image_basename": apath.name,
                    "artifact_path": str(apath),
                    "ocr_error": str(e),
                    "ocr_text": "",
                }
            )
            continue
        if not args.no_normalize:
            text = normalize_ws(text)
        if args.max_chars > 0 and len(text) > args.max_chars:
            text = text[: args.max_chars] + "\n... [truncated]"
        items.append(
            {
                "image_id": rec.get("image_id"),
                "image_basename": apath.name,
                "artifact_path": str(apath),
                "ocr_engine": eng,
                "ocr_text": text,
            }
        )

    by_base: dict[str, Any] = {}
    for x in items:
        b = x.get("image_basename")
        if isinstance(b, str) and b:
            by_base[b] = x
    out: dict[str, Any] = {
        "source_filtered": str(fpath),
        "tesseract_lang": args.lang,
        "items": items,
        "by_basename": by_base,
    }

    out_path = Path(args.output).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ok = sum(1 for x in items if "ocr_error" not in x)
    err_n = len(items) - ok
    print(f"已写入 {out_path}  成功 {ok}/{len(items)}")
    if err_n:
        print(f"提示: {err_n} 条失败，见各条 ocr_error", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
