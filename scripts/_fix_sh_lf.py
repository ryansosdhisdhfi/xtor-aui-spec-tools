#!/usr/bin/env python3
"""Force LF in scripts/*.sh under repo root (fixes WSL: set -o pipefail CRLF errors). Run: python3 scripts/_fix_sh_lf.py"""
from pathlib import Path

here = Path(__file__).resolve().parent
root = here.parent
for p in (here.glob("*.sh")):
    b = p.read_bytes()
    n = b.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    if b != n:
        p.write_bytes(n)
        print("LF:", p.relative_to(root))
