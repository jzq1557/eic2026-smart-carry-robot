# -*- coding: utf-8 -*-
"""
_pack_visualbot.py —— 把上位机(树莓派 4B / Jetson Orin Nano)的全部开发工程
                       打成一个 ZIP 放到桌面上(E:\\desktop\\VisualBot.zip)

用法(本机 Windows, Anaconda python):
    D:\\programs\\anaconda\\python.exe _pack_visualbot.py
    可选参数:  <输出目录>   默认 E:\\desktop

打包内容(见包内 00_打包说明.md):
    VisualBot/vision_robot/    ← 上位机主包(= 部署到 Pi ~/vision_robot 与 Jetson ~/VisualBot 的那一份)
    VisualBot/vision_deps/     ← 上位机视觉依赖与回归/诊断工具(ring_center_detect_v2.py 等)
    VisualBot/vision_deps/images/ ← 测试与现场素材图(A4 靶标等)
    VisualBot/logic_v2/        ← 全流程 v2 纯逻辑层(任务码/统计/编排/打包/串口)
    VisualBot/docs/            ← 赛项原文/研读笔记/设计文档/历史交接文档/二维码生成器
    VisualBot/工程总览_README.md ← 接手必读(坑档案在此)

排除: __pycache__ / *.pyc / node_modules / 各工具的运行时输出目录。
"""
import os
import sys
import time
import zipfile

ROOT = r"E:\工程文档\27工创赛"
PROG = os.path.join(ROOT, "整机程序设计")
EXTRACT = os.path.join(ROOT, "_pdf_extract")
TOP = "VisualBot"

EXCLUDE_DIRS = {"__pycache__", "node_modules", ".git", ".vscode", ".idea",
                "ring_ui_out", "color_demo_out", "digit_custom", "aim_ui_out"}
EXCLUDE_EXT = {".pyc", ".pyo", ".zip", ".7z", ".rar", ".bak"}

NOTE = """# VisualBot —— 上位机开发工程打包说明

> 打包时间: {stamp}
> 来源: `E:\\工程文档\\27工创赛`（本机唯一编辑源）
> 目标机器: 树莓派 4B(`~/vision_robot/`) / Jetson Orin Nano(`~/VisualBot/`) / RDK X5(`/root/VisualBot/`) —— **同一套代码**

## 一、包内结构

| 目录/文件 | 说明 |
|---|---|
| `工程总览_README.md` | ★**接手第一份要读的东西**：赛项解读、硬件接线、协议、固件、上位机、40+ 条坑档案 |
| `vision_robot/` | ★上位机主包（= 部署到三台机器上的那份）：瞄准/放料、圆环调参台、颜色调参台、二维码推流、三套部署脚本、平台探测/性能脚本、三份现场必读文档 |
| `vision_deps/` | 上位机视觉依赖与工具：`ring_center_detect_v2.py`(圆环+数字识别核心)、`qr_stream_demo.py`、三套回归、对焦/诊断/基准脚本 |
| `vision_deps/images/` | 测试与现场素材图：`工创赛标靶A4(12)/(13).png`（digit=2 / digit=3，离线验证用）等 |
| `logic_v2/` | 全流程 v2 纯逻辑层：任务码解析/统计账本/流程编排/串口命令打包/串口执行 + 5 套回归 + 本机演示 |
| `docs/` | 赛题原文(附件2-1/2-2)、研读笔记、全流程设计蓝图、上位机程序分析、任务码二维码生成器、历史交接文档 |

## 二、怎么用（三步）

1. **看总览**：先读 `工程总览_README.md`（尤其"已知坑与事故档案"一节），再读
   `vision_robot/README.md`（部署包说明）与 `vision_robot/SSH命令大全.md`（命令速查）。
2. **部署到机器**（需要同局域网、SSH 可达；三台各跑一条）：
   ```powershell
   cd vision_robot
   powershell -ExecutionPolicy Bypass -File .\\deploy_to_pi.ps1       # 树莓派 Pi4   10.48.81.145  ~/vision_robot
   powershell -ExecutionPolicy Bypass -File .\\deploy_to_jetson.ps1   # Jetson Orin  10.48.81.108  ~/VisualBot
   powershell -ExecutionPolicy Bypass -File .\\deploy_to_rdk.ps1      # RDK X5       10.48.81.249  /root/VisualBot
   ```
   ⚠️ 三个 `.ps1` **必须保持 UTF-8 with BOM**，否则 PowerShell 5.1 按 GBK 解码中文会报莫名的语法错误。
   首次连新机器需要授权公钥（脚本会打印那条命令）。
   ⚠️ `deploy_to_pi.ps1` 不会覆盖机器上已有的 `ring_center_detect_v2.py` / `qr_stream_demo.py` /
   `test_*.py`；新机器上要手动先放一份 `vision_deps/` 里的这些文件
   （`deploy_to_jetson.ps1` / `deploy_to_rdk.ps1` 会自动带上）。
3. **在机器上跑**（远端零窗口，全靠浏览器；先确保没有别的进程占着相机）：
   ```bash
   python3 -u probe_platform.py --seconds 2    # 平台/OpenCV/相机/推荐性能档(Jetson/RDK/Pi 通用)
   bash setup_platform.sh                      # 依赖检查(+需要串口时装 pyserial)
   python3 ring_stream_ui.py    --perf auto --cam 0 --port 8091   # 圆环调参台
   python3 color_blob_stream.py --perf auto --cam 0 --port 8090   # 颜色调参台
   python3 aim_and_place.py --port /dev/ttyUSB0 --stream 8092     # 瞄准/放料主线
   bash bench_perf.sh 12                       # 性能档对比(low/mid/high/rdk)
   ```
   浏览器打开 `http://<机器IP>:8091/`。
   - **树莓派 4B**：`--perf auto` → `mid`；UVC 相机，别加 `--gst`。
   - **Jetson Orin Nano**：`--perf auto` → `high`（真机 ring 34fps，相机上限 14.9fps）；CSI 才要 `--gst`。
   - **RDK X5**：`--perf auto` → `rdk`（640 处理，实测 7.5fps）；OpenCV 无 GStreamer，`--gst` 无用；
     打包时它还没接相机，先用 `--no-cam` / `--image` 调参。

## 三、必须先知道的三件事

1. **下位机固件要 Rebuild + 重新烧录**：`deploy_pi` 里的瞄准主线依赖固件 0x10~0x16 命令
   （尤其 0x16 现按 `buf[1]` 选物架 1/2/3 号位）。烧旧 hex 会白排查几小时。
2. **换机器要重新确认相机**：相机索引会漂移（`find_cam.py`）；三台的差别是——
   树莓派是 UVC（倒装 → `rotate_ccw` 开着）、Jetson 有 CSI 才需要 `--gst`（没有 CSI 会失败并自动回退）、
   **RDK X5 的 OpenCV 不带 GStreamer（`--gst` 无用）且打包时还没插相机**（用 `--no-cam`/`--image` 调参）。
3. **性能看相机，也看算力，分平台不一样**：`[loop] fps=` 是处理循环速度，端到端帧率 = min(处理, 相机)。
   实测 Jetson 处理 34fps / 相机 14.9fps（瓶颈在相机）；**RDK X5 只有 7.5fps（瓶颈在算力，用 `--perf rdk`）**；
   树莓派相机 ≈17fps。跑 `bash bench_perf.sh` 能一次看清四档差异。

## 四、排障速查

- SSH 连不上：先 ping，再查 `~/.ssh/config` 的 `HostName`（IP 会变；别让 config 带 BOM）。
- UI 打不开：`ps -eo pid,args | grep python3 | grep -v grep` 找残留进程 → `kill <PID>`（别用 `pkill -f`）。
- 改完代码没反应：确认**在用的那台机器部署了**（三台同名文件容易串）、`py_compile` 通过、并重启了工具进程。
- 平台不明先跑：`python3 -u probe_platform.py --seconds 2`（Jetson / RDK X5 / 树莓派 三合一）。
"""


def add_file(zf, src, arc, counter):
    if not os.path.exists(src):
        print("  [缺少] %s" % src)
        return
    zf.write(src, arc)
    counter[0] += 1


def add_tree(zf, src, arc, counter, exts=None):
    if not os.path.isdir(src):
        print("  [缺少目录] %s" % src)
        return
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIRS)
        for fn in sorted(filenames):
            ext = os.path.splitext(fn)[1].lower()
            if ext in EXCLUDE_EXT:
                continue
            if exts is not None and ext not in exts:
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, src)
            zf.write(full, os.path.join(arc, rel).replace("\\", "/"))
            counter[0] += 1


def main():
    desktop = sys.argv[1] if len(sys.argv) > 1 else r"E:\desktop"
    if not os.path.isdir(desktop):
        print("!! 输出目录不存在: %s" % desktop)
        return 2
    out = os.path.join(desktop, "VisualBot.zip")
    if os.path.exists(out):
        os.remove(out)

    cnt = [0]
    t0 = time.time()
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print("[1/1] 打包 -> %s" % out)

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        zf.writestr("%s/00_打包说明.md" % TOP, NOTE.format(stamp=stamp))
        cnt[0] += 1

        add_file(zf, os.path.join(ROOT, "README.md"),
                 "%s/工程总览_README.md" % TOP, cnt)

        # 上位机主包(部署到两台机器的那份)
        add_tree(zf, os.path.join(PROG, "deploy_pi"),
                 "%s/vision_robot" % TOP, cnt)
        # 视觉变体(在 整机程序设计\ 根级, 部署脚本会一起传)
        for f in ("aim_and_place_glare_filter.py", "aim_and_place_pi_lite.py"):
            add_file(zf, os.path.join(PROG, f), "%s/vision_robot/%s" % (TOP, f), cnt)
        # exp_config.py / exp_speed.py 的示例图(ASCII 名, 免得命令行里中文括号出问题)
        add_file(zf, os.path.join(ROOT, "工创赛标靶A4(12).png"),
                 "%s/vision_robot/a4_12.png" % TOP, cnt)

        # 视觉依赖与回归/诊断工具
        add_tree(zf, EXTRACT, "%s/vision_deps" % TOP, cnt,
                 exts={".py", ".sh"})
        # ★校赛（上位机主导架构）：主程序 + 动作组配置 + 开发指南 + 下位机扩展骨架
        add_tree(zf, os.path.join(PROG, "校赛"), "%s/school" % TOP, cnt)
        add_file(zf, os.path.join(ROOT, "校赛开发.md"),
                 "%s/校赛开发.md" % TOP, cnt)
        _clean = os.path.join(PROG, "26090901TEST", "CLEAN")
        for f in ("params.h", "params.c", "motion.h", "motion.c", "README_校赛扩展.md"):
            add_file(zf, os.path.join(_clean, f),
                     "%s/firmware_school_ext/%s" % (TOP, f), cnt)
        for f in ("工创赛标靶A4(12).png", "工创赛标靶A4(13).png",
                  "1d18d5a0e18f129d594dc650f6a9934e.png",
                  "813E3A228441E3D999271434BD64028A.jpg"):
            add_file(zf, os.path.join(ROOT, f),
                     "%s/vision_deps/images/%s" % (TOP, f), cnt)

        # 全流程 v2 纯逻辑层
        add_tree(zf, os.path.join(PROG, "car_code上位机"),
                 "%s/logic_v2" % TOP, cnt)

        # 文档
        add_file(zf, os.path.join(ROOT, "智能搬运赛道要求研读笔记.md"),
                 "%s/docs/智能搬运赛道要求研读笔记.md" % TOP, cnt)
        add_file(zf, os.path.join(ROOT, "任务码二维码生成器.html"),
                 "%s/docs/任务码二维码生成器.html" % TOP, cnt)
        add_file(zf, os.path.join(ROOT, "continue.md"),
                 "%s/docs/history/continue.md" % TOP, cnt)
        add_file(zf, os.path.join(ROOT, "lecture.md"),
                 "%s/docs/history/lecture.md" % TOP, cnt)
        for f in ("全流程上位机v2设计.md", "上位机程序分析.md"):
            add_file(zf, os.path.join(PROG, f), "%s/docs/%s" % (TOP, f), cnt)
        # 赛题原文文本(从 PDF 提取) + VSCode 任务配置生成脚本
        for f in ("附件2-1命题与运行.txt", "附件2-2评分与规则.txt",
                  "创建工创赛VSCode配置文件.ps1"):
            add_file(zf, os.path.join(EXTRACT, f), "%s/docs/%s" % (TOP, f), cnt)

    size = os.path.getsize(out)
    print("      完成: %d 个文件, %.2f MB, 用时 %.1fs"
          % (cnt[0], size / 1048576.0, time.time() - t0))

    # 自检: 重新打开确认可读 + 列出顶层
    with zipfile.ZipFile(out) as zf:
        bad = zf.testzip()
        tops = sorted({n.split("/")[1] for n in zf.namelist() if "/" in n})
        print("      自检: %s" % ("损坏项 " + str(bad) if bad else "压缩包完好"))
        print("      顶层: %s" % ", ".join(tops))
    return 0


if __name__ == "__main__":
    sys.exit(main())
