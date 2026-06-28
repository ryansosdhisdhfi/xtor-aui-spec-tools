#!/usr/bin/env python3
"""Prepare single-image eval inputs for fig_0230 (V1 + V2)."""
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STEM = "PHY_Interface_PCIe_SATA_USB32_DP_USB4_Arch_Rev6p1p1"
IMAGE_ID = "fig_0230"


def main() -> None:
    src_filtered = json.loads(
        (ROOT / "output" / f"{STEM}.images.filtered.json").read_text(encoding="utf-8")
    )
    src_context = json.loads(
        (ROOT / "output" / f"{STEM}.figure_context.json").read_text(encoding="utf-8")
    )
    src_ocr = json.loads(
        (ROOT / "output" / f"{STEM}.ocr.json").read_text(encoding="utf-8")
    )

    rec = next(x for x in src_filtered["kept"] if x["image_id"] == IMAGE_ID)
    src_img = Path(rec["_artifact_path"])
    basename = src_img.name

    ctx = next(x for x in src_context["figures"] if x["image_basename"] == basename)
    ocr = next(x for x in src_ocr["items"] if x["image_id"] == IMAGE_ID)

    for ver in ("output_v1", "output_v2"):
        base = ROOT / ver / "eval_fig0230"
        img_dir = base / "images"
        img_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_img, img_dir / basename)

        rec2 = dict(rec)
        rec2["_source_artifact_path"] = str(src_img)
        rec2["_artifact_href"] = f"images/{basename}"
        rec2["_artifact_path"] = str((img_dir / basename).resolve())
        rec2["_artifact_path_rel"] = f"images/{basename}"

        ocr2 = dict(ocr)
        ocr2["artifact_path"] = str((img_dir / basename).resolve())

        filtered = dict(src_filtered)
        filtered["kept"] = [rec2]
        filtered["dropped"] = []
        filtered["summary"] = {
            "kept_count": 1,
            "dropped_count": 0,
            "sample_ids": [IMAGE_ID],
        }

        context = {
            "source_md": src_context.get("source_md"),
            "options": src_context.get("options", {}),
            "figures": [ctx],
            "by_basename": {basename: ctx},
        }

        ocrj = {
            "source_filtered": src_ocr.get("source_filtered"),
            "tesseract_lang": src_ocr.get("tesseract_lang"),
            "items": [ocr2],
            "by_basename": {basename: ocr2},
        }

        (base / f"{STEM}.images.filtered.fig0230.json").write_text(
            json.dumps(filtered, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (base / f"{STEM}.figure_context.fig0230.json").write_text(
            json.dumps(context, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (base / f"{STEM}.ocr.fig0230.json").write_text(
            json.dumps(ocrj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    print("prepared fig_0230 single-image inputs")


if __name__ == "__main__":
    main()
