#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Print the 现场参数区 of school_main.py as a single ordered table.

Usage: python3 dump_params.py [school_main.py]
"""
from __future__ import annotations

import ast
import re
import sys

KEYS = [
    "CHASSIS_VEL", "CHASSIS_ACC", "CHASSIS_DEC",
    "GRIP_CLOSE_ANGLE", "GRIP_OPEN_ANGLE",
    "BASE_90", "LIFT_UP", "LIFT_DOWN", "TELESCOPE",
    "RIGHT_OUT", "TO_SCAN", "TO_MATERIAL", "TO_PROCESS",
    "SPIN_DEG", "ENTER_PROCESS", "TO_FINISH", "FINISH_RIGHT", "PITCH",
    "CAM_INDEX", "PERF", "SCAN_TIMEOUT",
]

PAT = re.compile(r"^(%s)\s*=\s*(.+?)\s*(?:#.*)?$" % "|".join(KEYS))


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "school_main.py"
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    try:
        ast.parse(src)
        print("[syntax] OK")
    except SyntaxError as exc:
        print("[syntax] FAIL %s" % exc)
        return 2

    seen = {}
    for line in src.splitlines():
        m = PAT.match(line)
        if m:
            seen[m.group(1)] = m.group(2)
    for key in KEYS:
        if key in seen:
            print("   %-16s = %s" % (key, seen[key]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
