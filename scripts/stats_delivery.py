#!/usr/bin/env python3
import json
import re
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1] / "archive"
DOCS = [
    ("HDMI 2.2 Standard", "HDMI_2_2_Standard", REPO / "HDMI_2_2_Standard_delivery_20260520", 666, 29),
    ("HDMI 1.4 Standard", "HDMI1_4_Standard", REPO / "HDMI1_4_Standard_delivery_20260521", 425, None),
]


def parse_timings(path: Path):
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"(\S+)\s+step=(\S+)\s+exit=(\d+)\s+duration_s=(\d+)", line)
        if m:
            rows.append(
                {
                    "ts": m.group(1),
                    "step": m.group(2),
                    "exit": int(m.group(3)),
                    "dur": int(m.group(4)),
                }
            )
    return rows


def fmt_sec(s: int) -> str:
    h, rem = divmod(int(s), 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {sec}s"
    if m:
        return f"{m}m {sec}s"
    return f"{sec}s"


def human(n: float) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return f"{n:.1f}{u}" if u != "B" else f"{int(n)}B"
        n /= 1024
    return f"{n:.1f}GB"


def dir_size(p: Path) -> int:
    if not p.exists():
        return 0
    if p.is_file():
        return p.stat().st_size
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


ORDER = [
    "a1-batch",
    "merge-assets",
    "a2-strip",
    "a3-hierarchy",
    "a4-codeblocks",
    "b1-rewrite",
    "b2-filter",
    "b3-context",
    "b4-ocr",
    "b5-describe",
    "b6-inject",
]

grand_wall = grand_ok = grand_fail = 0

for label, stem, pkg, pages, parts in DOCS:
    log = pkg / "logs_full" / "00_timings.txt"
    if not log.exists():
        log = pkg / "logs" / "00_timings.txt"
    rows = parse_timings(log)

    t0 = datetime.fromisoformat(rows[0]["ts"]) if rows else None
    t1 = datetime.fromisoformat(rows[-1]["ts"]) if rows else None
    wall = int((t1 - t0).total_seconds()) if t0 and t1 else 0

    ok_dur = sum(r["dur"] for r in rows if r["exit"] == 0)
    fail_dur = sum(r["dur"] for r in rows if r["exit"] != 0)

    logical: dict = {}
    b7_calls: list[int] = []
    for r in rows:
        if r["exit"] != 0:
            continue
        if r["step"] == "b7-index-dual":
            b7_calls.append(r["dur"])
        elif r["step"].startswith("b7-fill"):
            continue
        else:
            logical[r["step"]] = r["dur"]

    # 精简：每步最后一次成功；b7 取最终一轮 index-dual（最后两次）
    if len(b7_calls) >= 2:
        logical["b7-index-dual"] = b7_calls[-2] + b7_calls[-1]
    elif b7_calls:
        logical["b7-index-dual"] = sum(b7_calls)

    clean = sum(v for v in logical.values() if isinstance(v, int))
    a_sum = sum(logical.get(s, 0) for s in ORDER if s.startswith("a") or s == "merge-assets")
    b_sum = sum(logical.get(s, 0) for s in ORDER if s.startswith("b")) + logical.get("b7-index-dual", 0)

    json_dir = pkg / "json"
    img_dir = pkg / f"{stem}_merged_images"
    fig_dir = pkg / "figure_schemas"
    filt = json_dir / f"{stem}.images.filtered.json"
    n_kept = None
    if filt.exists():
        data = json.loads(filt.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "images" in data:
            n_kept = len(data["images"])
        elif isinstance(data, list):
            n_kept = len(data)

    print(f"\n{'='*70}")
    print(f"{label}  ({stem})")
    print(f"{'='*70}")
    print(f"PDF: {pages} 页" + (f"，拆 {parts} 段" if parts else ""))
    print(f"时间窗口: {rows[0]['ts'] if rows else '?'} → {rows[-1]['ts'] if rows else '?'}")
    print(f"墙钟时间: {fmt_sec(wall)} ({wall}s)")
    print(f"步骤耗时 (全部 exit=0 累加): {fmt_sec(ok_dur)} ({ok_dur}s)")
    print(f"失败重试浪费: {fmt_sec(fail_dur)} ({fail_dur}s)")
    print(f"精简估算 (不含 fill-empty / 重复 b7): {fmt_sec(clean)} ({clean}s)")
    print(f"  A 段: {fmt_sec(a_sum)} | B 段: {fmt_sec(b_sum)}")

    print("\n分步 (最后一次成功):")
    for s in ORDER:
        if s in logical:
            print(f"  {s:18s} {fmt_sec(logical[s]):>10s}")
    if "b7-index-dual" in logical:
        print(f"  {'b7-index-dual':18s} {fmt_sec(logical['b7-index-dual']):>10s}")

    b5_ok = any(r["step"] == "b5-describe" and r["exit"] == 0 for r in rows)
    if not b5_ok:
        print("  注: 日志中 b5-describe 无 exit=0，但交付包已含 descriptions/enriched（有未记入日志的续跑）")

    enriched = json_dir / f"{stem}_enriched.md"
    clean_md = json_dir / f"{stem}_clean.md"
    print("\n产物:")
    print(f"  交付包总大小: {human(dir_size(pkg))}")
    print(f"  enriched.md:  {human(enriched.stat().st_size) if enriched.exists() else 'N/A'}")
    print(f"  clean.md:     {human(clean_md.stat().st_size) if clean_md.exists() else 'N/A'}")
    print(f"  图片文件:     {len(list(img_dir.glob('*'))) if img_dir.exists() else 0} 张")
    print(f"  VLM JSON:     {len(list(fig_dir.glob(f'{stem}*.json'))) if fig_dir.exists() else 0} 个")
    if n_kept is not None:
        print(f"  筛选 kept:    {n_kept} 张")

    grand_wall += wall
    grand_ok += ok_dur
    grand_fail += fail_dur

print(f"\n{'='*70}")
print("两本合计")
print(f"{'='*70}")
print(f"墙钟合计 (两本串行，含中间清理/等待): {fmt_sec(grand_wall)}")
print(f"步骤耗时合计 (exit=0): {fmt_sec(grand_ok)}")
print(f"失败重试浪费合计: {fmt_sec(grand_fail)}")
print(f"交付包磁盘占用: {human(dir_size(REPO / 'HDMI_2_2_Standard_delivery_20260520') + dir_size(REPO / 'HDMI1_4_Standard_delivery_20260521'))}")
