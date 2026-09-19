# -*- coding: utf-8 -*-
"""
_make_release.py —— 「整理工程文件夹」（不是压缩包）
================================================================================
把散在工作区里的东西整理成两个**自洽的工程文件夹**，按时间命名：

    发布\\上位机_<日期>\\      ← 上位机全部：主包 + 校赛 + 视觉依赖 + 文档
    发布\\下位机_<日期>\\      ← 最新下位机 Keil 工程(自动挑日期最大的 *01TEST) + 校赛说明

用法(本机 Windows, Anaconda python):
    D:\\programs\\anaconda\\python.exe _make_release.py
    D:\\programs\\anaconda\\python.exe _make_release.py 20260916     # 指定日期
    D:\\programs\\anaconda\\python.exe _make_release.py --zip        # 顺手也打 zip(默认不打)
    D:\\programs\\anaconda\\python.exe _make_release.py --force      # 同名目录已存在时直接覆盖

说明：
  · 只**复制**，不动原工程（deploy_pi / 校赛 / 2*01TEST 保持原样）；
  · 下位机自动取**名字最大**（= 日期最新）的 `YYMMDD01TEST`，若该工程已内置校赛扩展
    （CLEAN 里有 params.c），就不再单列"校赛扩展"目录；
  · 每个文件夹里生成 `00_说明.md`，拿到就能用。
"""
import os
import re
import shutil
import sys
import time

ROOT = r"E:\工程文档\27工创赛"
PROG = os.path.join(ROOT, "整机程序设计")
EXTRACT = os.path.join(ROOT, "_pdf_extract")
REL = os.path.join(ROOT, "发布")

EXCLUDE_UP = {"__pycache__", "node_modules", ".git", "ring_ui_out", "color_demo_out",
              "aim_ui_out", ".vscode", ".idea", "firmware"}
EXCLUDE_FW = {"__pycache__", ".git", "Listings", "DebugConfig", "RTE", "Objects"}
EXCLUDE_FW_EXT = {".o", ".d", ".crf", ".dep", ".lnp", ".iex", ".sct", ".htm",
                  ".map", ".lst", ".bak", ".axf", ".build_log.htm", ".scvd", ".uvoptx",
                  ".uvguix"}
EXCLUDE_EXT = {".pyc", ".pyo", ".zip", ".7z", ".rar", ".bak"}

UP_NOTE = """# 上位机工程（VisualBot 上位机）—— 整理于 {stamp}

## 目录结构
| 目录/文件 | 说明 |
|---|---|
| `工程总览_README.md` | ★接手第一份读的：赛项解读/接线/协议/固件/上位机/40+ 条坑档案 |
| `校赛开发.md` | ★校赛（魔改规则）开发指南：规则完善 + 上位机主导架构 + 六次瞄准 + 协议扩展 + 主程序走读 |
| `vision_robot/` | 上位机主包（= 部署到 Pi `~/vision_robot` / Jetson `~/VisualBot` / RDK `/root/VisualBot`）：瞄准放料、圆环调参台、颜色调参台、三套部署脚本、平台探测与调优三件套 |
| `school/` | 校赛主程序：状态机 M0~M10 + 六次瞄准 + 车载账本 + 参数/动作组下发（`lower_api.py` 已适配新固件的 ACK 与 0x22 读回） |
| `vision_deps/` | 圆环+数字识别核心 `ring_center_detect_v2.py`、二维码推流、三套回归、真实 A4 靶标图 |

## 部署到机器（RDK X5 是当前上位机）
```powershell
cd vision_robot
powershell -ExecutionPolicy Bypass -File .\\deploy_to_rdk.ps1 -Stamped   # → rdk:/root/VisualBot_<时间>
powershell -ExecutionPolicy Bypass -File .\\deploy_to_jetson.ps1 -Stamped
powershell -ExecutionPolicy Bypass -File .\\deploy_to_pi.ps1 -Stamped
```
`-Stamped` 按时间命名远程工程文件夹，并维护 `<原名>_current` 软链；也可 `-Stamp 20260916_1224` 覆盖式重部署。
`deploy_to_pi.ps1` 默认不覆盖 Pi 上原有的圆环库/回归（要覆盖加 `-WithExtras`）。

## 机器上跑什么
```bash
cd /root/VisualBot_current
python3 -u probe_platform.py --seconds 2                       # 平台/相机/推荐性能档
python3 ring_stream_ui.py    --perf rdk --cam 1 --port 8091    # 圆环标定
python3 color_blob_stream.py --perf rdk --cam 1 --port 8090    # 颜色标定
cd 校赛 && python3 test_school_local.py && python3 -u school_flow.py --dry-run
```

## 三个必知点
1. `.ps1` 必须保持 **UTF-8 with BOM**（否则 PowerShell 5.1 按 GBK 解码中文会报莫名语法错）；
2. **在用的那台机器必须最新**（三台同名文件容易串）；改 `ring_center_detect_v2.py` 要三台一起 scp；
3. RDK 用 `--perf rdk`（512 宽档，实测 15.8fps）；Jetson `--perf auto`→high；Pi4 → mid。
"""

FW_NOTE = """# 下位机工程（STM32F103ZET6 Keil 工程）—— 整理于 {stamp}

## 用的是哪个工程
**`{proj}/`** —— 工作区里日期最新的固件工程（`USER\\{proj}.uvprojx` 用 Keil5 打开）。
{school_line}

| 目录 | 说明 |
|---|---|
| `{proj}/USER/` | Keil 工程文件与启动文件（`USER/main.c` 是历史文件，**没挂进工程**） |
| `{proj}/CLEAN/` | 全部业务源码（chassis / TTLctrl / ramp / protocol_and_main / params / motion …） |
| `{proj}/OBJ/` | 编译产物目录（**本整理包特意不带 hex**：一定要自己 Rebuild 后再烧） |
{school_row}

## 编译与烧录
1. Keil5 打开 `{proj}\\USER\\{proj}.uvprojx`（校赛版工程里 params.c / motion.c **已经加进 CLEAN 组**）；
2. **Project → Rebuild all target files**（不是 Build）→ 看 0 Error；
3. 确认 `OBJ\\{proj}.hex` 时间戳 = 刚刚（这一步别省，历史上有过烧旧 hex 白查几小时）；
4. 烧录 → **架空车轮**先试 `l.run_group("HOME")` → 再接视觉跑 `校赛/school_flow.py`。

## 校赛扩展怎么接（若是原版工程才需要）
按 `校赛扩展/README_校赛扩展.md`：把 `params.c/h`、`motion.c/h` 放进 `CLEAN\\`（IncludePath 已含 `..\\CLEAN`），
Add 进 **CLEAN 组**，再在 `car_config.h` 加 0x20~0x24 命令字、在 `protocol_and_main.c` 加
`SendFrame10()` + 5 个 case + `Params_Load()`/`Motion_Init()`。
不想手动做就跑 `_make_fw_project.py`，它会按今天日期生成一个**已接好校赛**的完整工程。

## 两个历史坑
- **烧旧 hex**：源码改了没 Rebuild，行为与代码不符 → 白排查几小时；
- **`USER\\main.c` 没挂进工程**：改错文件＝白改，真正的 main 在 `CLEAN\\protocol_and_main.c`。
"""


def latest_fw():
    """取工作区里日期最新的 YYMMDD01TEST 工程。"""
    cands = [d for d in os.listdir(PROG)
             if re.match(r"^\d{6}01TEST$", d) and os.path.isdir(os.path.join(PROG, d))]
    cands.sort()
    return cands[-1] if cands else "26090901TEST"


def _copy_tree(src, dst, exclude_dirs, exclude_ext=None, exts=None):
    n = 0
    ex_dirs = exclude_dirs or set()
    ex_ext = (exclude_ext or set()) | EXCLUDE_EXT
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = [d for d in dirnames if d not in ex_dirs]
        rel = os.path.relpath(dirpath, src)
        out_dir = os.path.join(dst, rel) if rel != "." else dst
        os.makedirs(out_dir, exist_ok=True)
        for fn in filenames:
            ext = os.path.splitext(fn)[1].lower()
            if ext in ex_ext:
                continue
            if exts is not None and ext not in exts:
                continue
            shutil.copy2(os.path.join(dirpath, fn), os.path.join(out_dir, fn))
            try:
                os.chmod(os.path.join(out_dir, fn), 0o666)   # 整理包里的文件不该是只读的
            except Exception:
                pass
            n += 1
    return n


def _copy_file(src, dst_dir, dst_name=None):
    if not os.path.exists(src):
        print("   [缺] %s" % src)
        return 0
    os.makedirs(dst_dir, exist_ok=True)
    shutil.copy2(src, os.path.join(dst_dir, dst_name or os.path.basename(src)))
    try:
        os.chmod(os.path.join(dst_dir, dst_name or os.path.basename(src)), 0o666)
    except Exception:
        pass
    return 1


def make_upper(dst, stamp):
    n = 0
    os.makedirs(dst, exist_ok=True)
    with open(os.path.join(dst, "00_说明.md"), "w", encoding="utf-8") as f:
        f.write(UP_NOTE.format(stamp=stamp))
    n += 1
    n += _copy_file(os.path.join(ROOT, "README.md"), dst, "工程总览_README.md")
    n += _copy_file(os.path.join(ROOT, "校赛开发.md"), dst)
    n += _copy_file(os.path.join(ROOT, "现场标定手册.md"), dst)
    n += _copy_tree(os.path.join(PROG, "deploy_pi"), os.path.join(dst, "vision_robot"),
                    EXCLUDE_UP)
    for f in ("aim_and_place_glare_filter.py", "aim_and_place_pi_lite.py"):
        n += _copy_file(os.path.join(PROG, f), os.path.join(dst, "vision_robot"))
    n += _copy_tree(os.path.join(PROG, "校赛"), os.path.join(dst, "school"), EXCLUDE_UP)
    n += _copy_tree(EXTRACT, os.path.join(dst, "vision_deps"), EXCLUDE_UP, exts={".py", ".sh"})
    for f in ("工创赛标靶A4(12).png", "工创赛标靶A4(13).png"):
        n += _copy_file(os.path.join(ROOT, f), os.path.join(dst, "vision_deps", "images"))
    n += _copy_file(os.path.join(ROOT, "工创赛标靶A4(12).png"),
                    os.path.join(dst, "vision_robot"), "a4_12.png")
    return n


def make_lower(dst, stamp):
    fw_name = latest_fw()
    fw = os.path.join(PROG, fw_name)
    built_in = os.path.exists(os.path.join(fw, "CLEAN", "params.c"))
    n = 0
    os.makedirs(dst, exist_ok=True)
    with open(os.path.join(dst, "00_说明.md"), "w", encoding="utf-8") as f:
        f.write(FW_NOTE.format(
            stamp=stamp, proj=fw_name,
            school_line=("> 该工程**已内置校赛扩展**（CLEAN 里已有 `params.c/h`、`motion.c/h`，"
                         "Keil 的 CLEAN 组也已加入），打开即可 Rebuild。" if built_in else
                         "> 该工程是**原版固件**，校赛扩展在下方 `校赛扩展/` 里，需要按说明手动接。"),
            school_row=("" if built_in else
                        "| `校赛扩展/` | `params.c/h`、`motion.c/h`、`README_校赛扩展.md`(接入步骤 + 帧布局表) |")))
    n += 1
    n += _copy_tree(fw, os.path.join(dst, fw_name), EXCLUDE_FW, exclude_ext=EXCLUDE_FW_EXT)
    if not built_in:
        ext_dir = os.path.join(PROG, "校赛", "firmware")
        for f in ("params.h", "params.c", "motion.h", "motion.c", "README_校赛扩展.md"):
            src = os.path.join(ext_dir, f)
            if not os.path.exists(src):
                src = os.path.join(fw, "CLEAN", f)
            n += _copy_file(src, os.path.join(dst, "校赛扩展"))
    return n, fw_name


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    want_zip = "--zip" in sys.argv
    force = "--force" in sys.argv
    stamp_day = args[0] if args else time.strftime("%Y%m%d")
    if force:
        for p in (os.path.join(REL, "上位机_%s" % stamp_day),
                  os.path.join(REL, "下位机_%s" % stamp_day)):
            if os.path.isdir(p):
                for dp, _dn, fns in os.walk(p):        # 先清只读属性, 否则 WinError 5
                    for fn in fns:
                        try:
                            os.chmod(os.path.join(dp, fn), 0o666)
                        except Exception:
                            pass
                shutil.rmtree(p, ignore_errors=True)
    os.makedirs(REL, exist_ok=True)

    up_dir = os.path.join(REL, "上位机_%s" % stamp_day)
    low_dir = os.path.join(REL, "下位机_%s" % stamp_day)
    if os.path.exists(up_dir) or os.path.exists(low_dir):
        stamp_day = "%s_%s" % (stamp_day, time.strftime("%H%M"))
        up_dir = os.path.join(REL, "上位机_%s" % stamp_day)
        low_dir = os.path.join(REL, "下位机_%s" % stamp_day)

    t0 = time.time()
    n1 = make_upper(up_dir, stamp_day)
    n2, fw_name = make_lower(low_dir, stamp_day)
    print("上位机_%s  %3d 个文件  →  %s" % (stamp_day, n1, up_dir))
    print("下位机_%s  %3d 个文件  →  %s   (工程: %s)" % (stamp_day, n2, low_dir, fw_name))

    if want_zip:
        import zipfile
        for d in (up_dir, low_dir):
            zp = d + ".zip"
            with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
                base = os.path.basename(d)
                for dp, _dn, fns in os.walk(d):
                    for fn in fns:
                        full = os.path.join(dp, fn)
                        arc = os.path.join(base, os.path.relpath(full, d))
                        zf.write(full, arc.replace("\\", "/"))
            print("   + zip: %s (%.2f MB)" % (os.path.basename(zp), os.path.getsize(zp) / 1048576.0))
    print("用时 %.1fs" % (time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
