#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dump a .pyc header (source mtime/size) and module-level NAME=CONST assignments.

Used to recover the parameter values of the *final* on-robot school_main.py,
whose .py source was overwritten by a deploy.
"""
import datetime
import dis
import marshal
import struct
import sys

TARGETS = {
    "CHASSIS_VEL", "CHASSIS_ACC", "CHASSIS_DEC", "BASE_90",
    "LIFT_UP", "LIFT_DOWN", "TELESCOPE", "RIGHT_OUT",
    "TO_SCAN", "TO_MATERIAL", "TO_PROCESS", "SPIN_DEG",
    "ENTER_PROCESS", "TO_FINISH", "PITCH", "CAM_INDEX",
}


def main() -> int:
    for path in sys.argv[1:]:
        print("== %s ==" % path)
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError as exc:
            print("   read failed: %s" % exc)
            continue

        magic = data[:4]
        src_mtime, src_size = struct.unpack("<II", data[8:16])
        print("   magic=%s  header src_mtime=%d (%s)  header src_size=%d"
              % (magic.hex(), src_mtime,
                 datetime.datetime.fromtimestamp(src_mtime), src_size))

        try:
            code = marshal.loads(data[16:])
        except Exception as exc:  # noqa: BLE001
            print("   marshal failed (python version mismatch?): %s" % exc)
            continue

        ins = list(dis.get_instructions(code))
        found = 0
        for i, item in enumerate(ins):
            if item.opname == "STORE_NAME" and item.argval in TARGETS:
                prev = ins[i - 1] if i else None
                val = prev.argval if prev is not None else "?"
                print("   %-14s = %s" % (item.argval, val))
                found += 1
        if not found:
            print("   (no target names in module level code)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
