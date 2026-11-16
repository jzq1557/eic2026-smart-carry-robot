# deploy_pi —— 上位机部署包（树莓派 4B + Jetson Orin Nano + RDK X5 同一套代码）

> 更新时间: 2026-09-14(新增 RDK X5 部署与 rdk 性能档; 09-13 新增 Jetson 部署/探测/性能档;
> 09-05 修复 smoke_calib/cam_diag_aim 到 aim_and_place v4 API; 统一瞄准/标定/诊断三处画面的旋转坐标空间)
> 用途: 本地改 → 一键部署到三台上位机 → 浏览器调参/现场跑流程
> 目录对应: 树莓派 `~/vision_robot/`(用户 justin) ; Jetson `~/VisualBot/`(用户 jetson) ; RDK X5 `/root/VisualBot/`(用户 root)

## 一、包内文件与用途

### 1.1 视觉调参台（浏览器，远端零窗口）
| 文件 | 作用 |
|---|---|
| `ring_stream_ui.py` | ★圆环识别调参台(端口 8091)：全部 CALIB 滑条+数字输入框、诊断面板、一键加打印体模板、一键跑三套回归；支持 `--perf/--stream-fps/--stream-width/--image/--no-cam/--gst` |
| `color_blob_stream.py` | ★颜色识别调参台(端口 8090)：MJPEG + 阈值滑条 + 6 色下拉 + 自动标定(ROI) + 存/读档案；支持 `--perf/--stream-fps/--gst` |
| `color_blob_demo.py` | 颜色算法桌面(Qt)版 **兼「相机/性能档」公共库**：`open_camera() / gst_pipeline() / PERF_PRESETS / detect_perf() / resolve_perf()`；⚠️ Qt 窗口在 Pi VNC 里会卡死，仅备用 |
| `digit_templates_ext.py` | 现场打印体数字 → `digit_custom/<1\|2\|3>/*.png` 热重建模板库(不改原版检测源码) |

### 1.2 瞄准/放料主线
| 文件 | 作用 | 部署目标 |
|---|---|---|
| `aim_and_place.py` | 视觉瞄准(复用原版 ring 检测)+分轴PID+X1YX2三段+记录偏移+mission全流程；支持 `--gst/--slot/--mission-all` | `~/vision_robot/` |
| `calibrate_aim.py` | 半自动标定 K_PX2STEP(像素/脉冲) | 同上 |
| `find_cam.py` | 探测摄像头实际索引(video0 会漂移) | 同上 |
| `smoke_calib.py` | import/摄像头冒烟测试 | 同上 |
| `cam_diag_aim.py` | 圆心检测稳定性诊断 | 同上 |
| `test_aim_protocol.py` | **协议层纯逻辑回归(本机可跑)**：帧格式/大端/ACK/命令字对齐(含 0x16 位号) | 本机(见下) |

**本地自检(不需要树莓派/串口/摄像头)**：本机 Anaconda Python 3.13 即可跑
```powershell
python deploy_pi\test_aim_protocol.py     # 期望 ALL TESTS PASSED
```
（本机 python 无 cv2/pyserial，脚本会自动 stub；视觉/实串口仍以 Pi 为准。）
### 1.3 部署与运维脚本（2026-09-13 新增）
| 文件 | 作用 | 在哪跑 |
|---|---|---|
| `deploy_to_pi.ps1` | 一键部署到树莓派 `~/vision_robot`(scp+远端 py_compile+摄像头探测) | 本机 |
| `deploy_to_jetson.ps1` | 一键部署到 Jetson `~/VisualBot`(多带 `ring_center_detect_v2.py`/回归/QR；`-WithLogic` 连 v2 纯逻辑层) | 本机 |
| `deploy_to_rdk.ps1` | 一键部署到 RDK X5 `/root/VisualBot`(同上；可用 `-User sunrise -RemoteDir /home/sunrise/VisualBot` 换账号) | 本机 |
| `probe_platform.py` | ★平台探测(**Jetson / RDK X5 / 树莓派 三合一**)：型号族+OS+核数+内存、OpenCV(版本/GStreamer/CUDA/V4L2)、相机(CSI 或 UVC)、推荐性能档 | 远端 |
| `setup_platform.sh` | 依赖检查(python/opencv/numpy)；缺 pyserial 时 `pip3 install --user pyserial` | 远端 |
| `probe_jetson.py` / `setup_jetson.sh` | 上面两个的**旧名兼容入口**(自动转调；历史文档里写的名字仍能跑) | 远端 |
| `bench_perf.sh` | 合成画面跑 low/mid/high/rdk 四档(纯算力对比) | 远端 |
| `bench_cam.sh` | 真实相机跑 ring/color/aim(端到端对比) | 远端 |
| `prof_ui.py` | ★按 UI 真实顺序分段计时(先回答"慢在哪"; 内置预热, 免得懒加载污染数据) | 远端 |
| `exp_speed.py` | ★单算子微基准：阈值/形态学/旋转/缩放/编码/OpenCL 逐项对比"现状 vs 候选" | 远端 |
| `exp_config.py` | ★参数组合扫描：每档给 ms + 与基线的精度对比(改参数前先看这张表) | 远端 |
| `kill_rsui.sh` / `rsui_test.sh` / `cbs_test.sh` / `rdiag.sh` | 清 UI 进程 / 圆环 UI 冒烟 / 颜色自测 / 圆环诊断 | 远端 |
| `README.md` / `上位机指令对照表.md` / `SSH命令大全.md` | 本包说明 / 指令-字节对照 / 两台上位机的 SSH 命令大全 | 远端备查 |

**注意**: `deploy_to_pi.ps1` **不覆盖**树莓派上已在的原版 `ring_center_detect_v2.py`、`qr_stream_demo.py`、
`test_*.py`(它们在 `~/vision_robot/`)；而 `deploy_to_jetson.ps1` 会**从 `_pdf_extract/` 主动带上**这些文件
(Jetson 目录是空的, 不带就 `import ring_center_detect_v2` 直接失败)。
**树莓派端也建议补齐**：`scp _pdf_extract\ring_center_detect_v2.py pi:~/vision_robot/` 之前务必先比对
两边 md5（旧版是 Hough 版, 与现在的 `_build_rot_templates` 版**不是同一个文件**）。

## 二、一键部署（两台机器各一条命令）

在两台上位机都上电联网后, 在本机 PowerShell:

```powershell
cd E:\工程文档\27工创赛\整机程序设计\deploy_pi

# 树莓派 (10.48.81.145 / justin / ~/vision_robot)
powershell -ExecutionPolicy Bypass -File .\deploy_to_pi.ps1            # IP 变了: -Pi 新IP

# Jetson Orin Nano (10.48.81.108 / jetson / ~/VisualBot)
powershell -ExecutionPolicy Bypass -File .\deploy_to_jetson.ps1        # 想连 v2 纯逻辑层: -WithLogic

# RDK X5 (root / /root/VisualBot; **IP 会变 — 一律 ssh rdk**)  —— 三台中算力最弱, 用 --perf rdk
#   找不到板子: powershell -ExecutionPolicy Bypass -File E:\工程文档\27工创赛\find_rdk.ps1
powershell -ExecutionPolicy Bypass -File .\deploy_to_rdk.ps1

# ★工程文件夹按时间命名(可并存/可回滚), 并自动更新 <原名>_current 软链:
#   .\deploy_to_rdk.ps1 -Stamped          → /root/VisualBot_20260916_1224 (+ /root/VisualBot_current)
#   .\deploy_to_jetson.ps1 -Stamped       → ~/VisualBot_20260916_1224      (+ ~/VisualBot_current)
#   .\deploy_to_pi.ps1 -Stamped           → ~/vision_robot_20260916_1224   (+ ~/vision_robot_current)
#   -Stamp 20260916_1224 指定时间戳 / -NoLink 不建软链 / Pi 的 -WithExtras 才覆盖圆环库
```

脚本会自动: ping 探测 → ssh 握手 → scp 全部工具+文档 → 远程 `python3 -m py_compile`
→ 平台/相机探测(`deploy_to_jetson.ps1` 会跑 `probe_jetson.py`)。
⚠️ **`.ps1` 必须存成 UTF-8 with BOM**：PowerShell 5.1 会把无 BOM 的 UTF-8 当 GBK 读，
中文变乱码后**引号被吃掉 → 语法错误**（这两个脚本已修正，别用编辑器另存成 ANSI）。

部署完在远端先跑:

```bash
python3 -u probe_jetson.py --seconds 2    # 平台/OpenCV/相机/推荐性能档(Jetson)
bash setup_jetson.sh                      # 依赖检查(+ 需要串口时装 pyserial)
bash bench_perf.sh 12 8097                # 三档性能对比
python3 ring_stream_ui.py --perf auto --cam 0 --port 8091
```

⚠️ **两台机器的存档文件同名**(`ring_calib.json` / `color_profile.json` / `aim_offset.txt`)
→ 各自机器上有一份, **别互相信任**：换机器调参前先确认是哪一份, 需要时互相 scp 覆盖。

## 三、到场地后的启动流程

### 0. 硬件检查
- ⚠️ **下位机固件必须先 Rebuild 再烧录**:
  当前最新工程是 `整机程序设计\26090501TEST`(09-05 建,Keil5 打开
  `USER\26090501TEST.uvprojx` → Rebuild → 烧 `OBJ\26090501TEST.hex`)。
  **它的 OBJ 目录还是空的,尚未编译**;
  `26090201TEST\OBJ\26090201TEST.hex`(09-03 14:51)虽然能编译,但早于
  `TTLctrl.c` 最后一次改动(09-03 16:11,即 0x16 去物架夹料动作组的手调参数),
  **不要直接烧那个旧 hex**。固件含协议 0x10~0x16 / Grip_Angle / 放料组 / 夹料组。
- USB-TTL 接树莓派 `/dev/ttyUSB0` ↔ STM32 PA9(树莓派RX)/PA10(树莓派TX)/GND
  (对应下位机 USART1;TTL 四路闭环电机在 USART3 PC10/PC11,不接树莓派)
- 摄像头插好(若 video0 漂移, 脚本会自动探测并打印实际索引)

### 1. 标定(若机械/相机位置与上次不同则必须重标)
```bash
cd ~/vision_robot
python3 calibrate_aim.py --port /dev/ttyUSB0
# 按提示回车, 得到 K_PX2STEP_BASE / K_PX2STEP_ARM → 填入 aim_and_place.py 顶部
# (数值记录到下方表格, 现场把值给我/或自己改)
```

### 2. 瞄准(带推流看画面)
```bash
python3 aim_and_place.py --port /dev/ttyUSB0 --stream 8090
# 浏览器 http://<Pi-IP>:8090/
# 跑完自动存 ~/vision_robot/aim_offset.txt
```

### 3. 全流程 mission(瞄准→归位→夹料→回放放料)
```bash
python3 aim_and_place.py --port /dev/ttyUSB0 --mission
```
或分步: `--home`(归位) / `--grab`(0x16夹料) / `--replay`(回放+放料)

## 四、现场参数记录表(填这里, 回传给我调参)

| 参数 | 值 | 位置 |
|---|---|---|
| K_PX2STEP_BASE | `0.2593`(2026-09-03 旧值) | aim_and_place.py 顶部 |
| K_PX2STEP_ARM | `0.0413` | 同上 |
| CAM_GRIP_OFFSET_X | `0.0`(待你实测: 圆心在画面中心偏右多少px) | 同上 |
| CAM_GRIP_OFFSET_Y | `0.0` | 同上 |
| 摄像头索引 | video?(探测结果) | 启动时 --cam |
| 下位机实际串口 | /dev/ttyUSB0? | --port |

### 标定 CAM_GRIP_OFFSET 的方法
把物料放夹爪**正下方**(夹爪对准物料不夹), 推流里读圆心像素:
```
OFFSET_X = 圆心x − 640
OFFSET_Y = 圆心y − 360
```
例: 圆心在 (700, 400) → OFFSET_X=+60, OFFSET_Y=+40

## 五、瞄准画面判读

| 画面元素 | 含义 |
|---|---|
| 绿圈 | 检测到的圆环 |
| 黄色 × | **目标点**(夹爪应对准处 = 画面中心+偏移) |
| 红点 | 画面中心(参考) |
| 左上黄字 | stage=X1/Y/X2、dx/dy、累计偏移、digit |

若圆心停在黄色×处 → 夹爪正对靶心。若夹爪实际没对准, 调 CAM_GRIP_OFFSET_*。
