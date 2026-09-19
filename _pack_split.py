# -*- coding: utf-8 -*-
"""
_pack_split.py —— 分别打「上位机包」和「下位机包」，按时间命名，输出到桌面
================================================================================
用法(本机 Windows, Anaconda python):
    D:\\programs\\anaconda\\python.exe _pack_split.py            # 输出目录默认 E:\\desktop
    D:\\programs\\anaconda\\python.exe _pack_split.py D:\\out

产物(时间戳 = 打包那一刻的 YYYYMMDD_HHMM):
    上位机_<时间>.zip      —— 上位机全部：主包(deploy_pi) + 校赛主程序 + 视觉依赖 + 文档
    下位机_<时间>.zip      —— 下位机 Keil 工程(26090901TEST 全量) + 校赛固件扩展

两个包各自带一份"包说明.md"，解压即用。
"""
import os
import sys
import time
import zipfile

ROOT = r"E:\工程文档\27工创赛"
PROG = os.path.join(ROOT, "整机程序设计")
EXTRACT = os.path.join(ROOT, "_pdf_extract")
FIRMWARE = os.path.join(PROG, "26090901TEST")

# 上位机侧：排除运行时产物
EXCLUDE_UP = {"__pycache__", "node_modules", ".git", "ring_ui_out", "color_demo_out",
              "aim_ui_out", ".vscode", ".idea"}
# 下位机侧：排除 Keil 中间产物（保留 .hex，烧录要用）
EXCLUDE_FW = {"__pycache__", ".git", "Listings", "DebugConfig", "RTE", "Objects"}
EXCLUDE_FW_EXT = {".o", ".d", ".crf", ".dep", ".lnp", ".iex", ".sct", ".htm",
                  ".map", ".lst", ".bak", ".axf", ".build_log.htm", ".scvd"}
EXCLUDE_EXT = {".pyc", ".pyo", ".zip", ".7z", ".rar", ".bak"}

UP_NOTE = """# 上位机包（VisualBot 上位机）—— 打包于 {stamp}

## 里面有什么
| 目录 | 说明 |
|---|---|
| `可视化总览_README.md` | ★接手第一份读的：赛项解读/接线/协议/固件/上位机/40+ 条坑档案 |
| `校赛开发.md` | ★校赛（魔改规则）开发指南：规则完善 + 上位机主导架构 + 六次瞄准 + 协议扩展 |
| `vision_robot/` | 上位机主包（= 部署到 Pi `~/vision_robot` / Jetson `~/VisualBot` / RDK `/root/VisualBot` 的那份）：瞄准放料、圆环调参台、颜色调参台、三套部署脚本、平台探测与性能调优工具 |
| `school/` | ★校赛主程序：状态机 + 六次瞄准 + 车载账本 + 参数/动作组下发 |
| `vision_deps/` | 圆环+数字识别核心 `ring_center_detect_v2.py`、二维码推流、三套回归（部署脚本会自动带上） |
| `vision_deps/images/` | 真实 A4 靶标图等测试素材 |

## 怎么用（RDK X5 为当前上位机）
```powershell
# 本机 Windows
powershell -ExecutionPolicy Bypass -File vision_robot\\deploy_to_rdk.ps1   # → rdk:/root/VisualBot
```
```bash
# RDK 上
cd /root/VisualBot
bash setup_platform.sh && python3 -u probe_platform.py --seconds 2
python3 ring_stream_ui.py    --perf rdk --cam 1 --port 8091     # 视觉标定
python3 color_blob_stream.py --perf rdk --cam 1 --port 8090
cd 校赛 && python3 test_school_local.py && python3 -u school_flow.py --dry-run
```

## 三个必须知道的点
1. `.ps1` 必须保持 **UTF-8 with BOM**（否则 PowerShell 5.1 按 GBK 解码中文会报莫名语法错）；
2. **在用的那台机器必须最新**（三台同名文件容易串）；改完 `ring_center_detect_v2.py` 要三台一起 scp；
3. 跑之前先 `probe_platform.py` 看平台与推荐性能档（RDK 用 `--perf rdk`）。
"""

FW_NOTE = """# 下位机包（STM32F103ZET6 Keil 工程）—— 打包于 {stamp}

## 里面有什么
| 目录 | 说明 |
|---|---|
| `26090901TEST/` | ★当前固件工程（CLEAN 重构 + TTL 四路闭环）：`USER/*.uvprojx` 用 Keil5 打开 |
| `26090901TEST/CLEAN/` | 全部业务源码（chassis / TTLctrl / ramp / protocol_and_main …）|
| `26090901TEST/USER/` | Keil 工程文件与启动文件（`USER\\main.c` 是历史文件，**没挂进工程**）|
| `校赛扩展/` | 校赛新增：`params.c/h`（参数表）、`motion.c/h`（动作组解释器）、`README_校赛扩展.md`（接入步骤 + 帧布局表）|
| `OBJ/*.hex` | 最近一次编译产物（**烧录前请自己 Rebuild 确认时间戳**）|

## 怎么用
1. Keil5 打开 `26090901TEST\\USER\\26090901TEST.uvprojx`；
2. 若要校赛固件：把 `校赛扩展\\params.c`、`motion.c` 加进 CLEAN 组，
   并照 `校赛扩展\\README_校赛扩展.md` 在 `protocol_and_main.c` 里加 5 个 case（0x20~0x24）；
3. **Rebuild**（不是 Build）→ 看 0 Error → 确认 `OBJ\\*.hex` 时间戳刷新 → 烧录；
4. 架空车轮先验单方向/单动作组，再上车。

## 两个历史坑（血泪）
- **烧旧 hex**：源码改了没 Rebuild，行为与代码不符 → 白排查几小时；
- **`USER\\main.c` 没挂进工程**：改错文件＝白改，真正的 main 在 `CLEAN\\protocol_and_main.c`。
"""


def add_file(zf, src, arc, cnt):
    if not os.path.exists(src):
        print("  [缺] %s" % src)
        return
    zf.write(src, arc.replace("\\", "/"))
    cnt[0] += 1


def add_tree(zf, src, arc, cnt, exclude_dirs=None, exclude_ext=None, exts=None):
    if not os.path.isdir(src):
        print("  [缺目录] %s" % src)
        return
    ex_dirs = exclude_dirs or set()
    ex_ext = exclude_ext or set()
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = sorted(d for d in dirnames if d not in ex_dirs)
        for fn in sorted(filenames):
            ext = os.path.splitext(fn)[1].lower()
            if ext in EXCLUDE_EXT or ext in ex_ext:
                continue
            if exts is not None and ext not in exts:
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, src)
            zf.write(full, os.path.join(arc, rel).replace("\\", "/"))
            cnt[0] += 1


def pack_upper(out, stamp):
    cnt = [0]
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        zf.writestr("上位机/包说明.md", UP_NOTE.format(stamp=stamp))
        cnt[0] += 1
        add_file(zf, os.path.join(ROOT, "README.md"), "上位机/可视化总览_README.md", cnt)
        add_file(zf, os.path.join(ROOT, "校赛开发.md"), "上位机/校赛开发.md", cnt)
        add_tree(zf, os.path.join(PROG, "deploy_pi"), "上位机/vision_robot", cnt,
                 exclude_dirs=EXCLUDE_UP)
        for f in ("aim_and_place_glare_filter.py", "aim_and_place_pi_lite.py"):
            add_file(zf, os.path.join(PROG, f), "上位机/vision_robot/%s" % f, cnt)
        # 校赛上位机代码（firmware/ 子目录是下位机的东西，只进下位机包）
        add_tree(zf, os.path.join(PROG, "校赛"), "上位机/school", cnt,
                 exclude_dirs=EXCLUDE_UP | {"firmware"})
        add_tree(zf, EXTRACT, "上位机/vision_deps", cnt,
                 exclude_dirs=EXCLUDE_UP, exts={".py", ".sh"})
        for f in ("工创赛标靶A4(12).png", "工创赛标靶A4(13).png"):
            add_file(zf, os.path.join(ROOT, f), "上位机/vision_deps/images/%s" % f, cnt)
        add_file(zf, os.path.join(ROOT, "工创赛标靶A4(12).png"),
                 "上位机/vision_robot/a4_12.png", cnt)
    return cnt[0]


def pack_lower(out, stamp):
    cnt = [0]
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        zf.writestr("下位机/包说明.md", FW_NOTE.format(stamp=stamp))
        cnt[0] += 1
        add_tree(zf, FIRMWARE, "下位机/26090901TEST", cnt,
                 exclude_dirs=EXCLUDE_FW, exclude_ext=EXCLUDE_FW_EXT)
        # 校赛固件扩展：优先取"独立目录"，没有就从 CLEAN 里拿（兼容两种放置方式）
        ext_dir = os.path.join(PROG, "校赛", "firmware")
        names = ("params.h", "params.c", "motion.h", "motion.c", "README_校赛扩展.md")
        for f in names:
            src = os.path.join(ext_dir, f)
            if not os.path.exists(src):
                src = os.path.join(FIRMWARE, "CLEAN", f)
            add_file(zf, src, "下位机/校赛扩展/%s" % f, cnt)
    return cnt[0]


def main():
    desktop = sys.argv[1] if len(sys.argv) > 1 else r"E:\desktop"
    if not os.path.isdir(desktop):
        print("!! 输出目录不存在: %s" % desktop)
        return 2
    stamp = time.strftime("%Y%m%d_%H%M")
    up = os.path.join(desktop, "上位机_%s.zip" % stamp)
    low = os.path.join(desktop, "下位机_%s.zip" % stamp)

    t0 = time.time()
    n1 = pack_upper(up, stamp)
    n2 = pack_lower(low, stamp)
    for path, n in ((up, n1), (low, n2)):
        size = os.path.getsize(path) / 1048576.0
        with zipfile.ZipFile(path) as zf:
            bad = zf.testzip()
        print("%-46s %3d 文件  %5.2f MB  %s"
              % (os.path.basename(path), n, size, "完好" if not bad else ("损坏:" + str(bad))))
    print("用时 %.1fs" % (time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
