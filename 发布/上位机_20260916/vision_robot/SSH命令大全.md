# SSH 命令大全 —— 三台上位机：树莓派 vision_robot + Jetson VisualBot + RDK X5 VisualBot

> 目标1：`ssh pi`（10.48.81.145，用户 justin，密钥 `~/.ssh/pi_deploy`）→ `~/vision_robot/`
> 目标2：`ssh jetson`（10.48.81.108，用户 jetson，密钥 `~/.ssh/jetson_deploy`）→ `~/VisualBot/`
> 目标3：`ssh rdk`（10.48.81.249，用户 root，密钥 `~/.ssh/rdk_deploy`）→ `/root/VisualBot/`
> 旧地址已废：树莓派 10.169.170.145 / 192.168.3.30（**IP 会变，先改 ~/.ssh/config 的 HostName**）
> 适用：瞄准/放料主线(deploy_pi 套件) + 圆环/QR/颜色视觉工具 + 回归 + 诊断 + 性能档
> 更新：2026-09-14（加入 RDK X5 与 `rdk` 性能档；09-13 加入 Jetson 与性能档/推流限帧/GStreamer）
> 参考：根目录 `README.md`（工程总览/坑档案）、`deploy_pi/README.md`、`deploy_pi/上位机指令对照表.md`

---

## 0. 连接与文件同步(在 Windows 本机 PowerShell 敲)

```bash
ssh pi                                      # 树莓派 10.48.81.145  (~/vision_robot)
ssh jetson                                  # Jetson 10.48.81.108  (~/VisualBot)
ssh rdk                                     # RDK X5 10.48.81.249  (/root/VisualBot)
scp 本地文件 pi:/home/justin/vision_robot/
scp 本地文件 jetson:/home/jetson/VisualBot/
scp 本地文件 rdk:/root/VisualBot/

# 一键部署(本机, 会自动 py_compile + 平台/相机探测):
powershell -ExecutionPolicy Bypass -File 整机程序设计\deploy_pi\deploy_to_pi.ps1
powershell -ExecutionPolicy Bypass -File 整机程序设计\deploy_pi\deploy_to_jetson.ps1 [-WithLogic]
powershell -ExecutionPolicy Bypass -File 整机程序设计\deploy_pi\deploy_to_rdk.ps1
# ★-Stamped: 工程文件夹按时间命名(可并存/可回滚), 并更新 <原名>_current 软链
#   deploy_to_rdk.ps1 -Stamped    → /root/VisualBot_20260916_1224  (+ /root/VisualBot_current)
#   deploy_to_jetson.ps1 -Stamped → ~/VisualBot_20260916_1224      (+ ~/VisualBot_current)
#   deploy_to_pi.ps1 -Stamped     → ~/vision_robot_20260916_1224   (+ ~/vision_robot_current)
#   -Stamp 20260916_1224 指定时间戳 / -NoLink 不建软链 / Pi 加 -WithExtras 才覆盖圆环库

# 找不到机器时:
ping raspberrypi.local ; arp -a          # 树莓派
ssh 10.48.81.108                         # Jetson(VSCode Remote-SSH 里连的就是它)
ssh 10.48.81.249                         # RDK X5
```
⚠️ **同一套代码每台机器都要部署**；⚠️ **`.ps1` 必须存成 UTF-8 with BOM**（否则 PowerShell 5.1 按 GBK 解码，中文乱码→语法错）。

## 1. 瞄准/放料套件（当前主线，脚本在 ~/vision_robot）

### aim_and_place.py —— 瞄准 / 归位 / 夹料 / 放料
```bash
cd ~/vision_robot

# 只瞄准(记录偏移到 aim_offset.txt) —— 需要已插 STM32 USB-TTL
python3 aim_and_place.py --port /dev/ttyUSB0

# 带推流看画面(浏览器 http://<PiIP>:8090)
python3 aim_and_place.py --port /dev/ttyUSB0 --stream 8090

# 全流程：瞄准→归位→0x16夹料→回放→0x13放料
python3 aim_and_place.py --port /dev/ttyUSB0 --mission

# 分步(每步人工确认，可叠加，按 home→grab→replay 顺序执行)
python3 aim_and_place.py --port /dev/ttyUSB0 --home      # 回观察位
python3 aim_and_place.py --port /dev/ttyUSB0 --grab      # 去车载物架夹料(0x16)
python3 aim_and_place.py --port /dev/ttyUSB0 --replay    # 回靶心+放料(0x13)
python3 aim_and_place.py --port /dev/ttyUSB0 --home --grab --replay  # 叠加一条

# 不连车、纯视觉(demo)：只算偏差不动电机
python3 aim_and_place.py --demo --stream 8090
python3 aim_and_place.py --demo                          # 不带推流

# 摄像头打不开/设备号漂移时指定索引
python3 aim_and_place.py --port /dev/ttyUSB0 --cam 1 --stream 8090
```
参数：`--port`(默认 /dev/ttyUSB0) `--baud`(115200) `--demo` `--cam N` `--show`(有显示器弹窗)
`--stream 端口` `--save 文件`(默认 aim_offset.txt) `--home` `--grab` `--replay` `--mission`

### calibrate_aim.py —— 半自动标定像素/脉冲 K
```bash
python3 calibrate_aim.py --port /dev/ttyUSB0                     # 交互：回车逐轮
python3 calibrate_aim.py --port /dev/ttyUSB0 --pulses 100 --vel 40  # 小步幅
```
参数：`--port --baud --pulses`(200) `--vel`(60 RPM) `--cam N`
产出：打印 `K_PX2STEP_BASE / K_PX2STEP_ARM` → 填进 aim_and_place.py 顶部。

### aim_and_place_glare_filter.py —— 瞄准 + 防反光预处理版(默认开滤镜)
```bash
# 与 aim_and_place 用法完全一致, 另加 3 个参数:
python3 aim_and_place_glare_filter.py --port /dev/ttyUSB0 --stream 8090   # 默认开防反光滤镜
python3 aim_and_place_glare_filter.py --port /dev/ttyUSB0 --no-filter     # 关滤镜(对照用)
python3 aim_and_place_glare_filter.py --port /dev/ttyUSB0 --filter-view   # 画面叠加品红=高光区
python3 aim_and_place_glare_filter.py --demo --exposure 156               # 覆盖锁曝光值
# 其余 --home/--grab/--replay/--mission/--cam/--save 均同 aim_and_place
```
> 滤镜内容：双边滤波 + CLAHE + 高光(高亮低饱和)软压缩；不影响瞄准/协议逻辑。

### aim_and_place_pi_lite.py —— 树莓派轻量版(轻量反光mask + 残缺圆弧拟合)
```bash
# 与 aim_and_place 同用法; 默认开: 轻量反光mask + 局部圆弧检测(ROI Canny+圆拟合,
# 首次/跟丢才 Hough, 无双边滤波/CLAHE, CPU 更省), 并与原版 RingDetector 融合
python3 aim_and_place_pi_lite.py --port /dev/ttyUSB0 --stream 8090
python3 aim_and_place_pi_lite.py --port /dev/ttyUSB0 --no-filter   # 关反光mask
python3 aim_and_place_pi_lite.py --port /dev/ttyUSB0 --no-arc      # 关圆弧检测(只用原版环)
python3 aim_and_place_pi_lite.py --port /dev/ttyUSB0 --arc-view    # 推流叠加圆弧拟合框/边缘
python3 aim_and_place_pi_lite.py --demo --exposure 156             # demo+覆盖曝光
# 其余 --home/--grab/--replay/--mission/--cam/--save 同 aim_and_place
```

### aim_and_place_pi_vision_kalman.py —— 轻量增强+中心卡尔曼(最新,优先试)
```bash
# = pi_lite 全部能力(反光mask+圆弧检测) + 中心卡尔曼滤波(突跳门限抗噪声) + 二值辅助
# 卡尔曼默认常开(无 --no-kalman; 参数在文件顶部 KALMAN_*)
python3 aim_and_place_pi_vision_kalman.py --port /dev/ttyUSB0 --stream 8090
python3 aim_and_place_pi_vision_kalman.py --port /dev/ttyUSB0 --no-filter / --no-arc
python3 aim_and_place_pi_vision_kalman.py --port /dev/ttyUSB0 --filter-view / --arc-view / --binary-view
python3 aim_and_place_pi_vision_kalman.py --demo --exposure 156
# 其余 --home/--grab/--replay/--mission/--cam/--save 同 aim_and_place
```

### color_blob_stream.py —— 转盘物料颜色识别(★浏览器 UI 版, 推荐)
```bash
# Pi 端零窗口(不用 Qt) → 不会卡死图形栈; SSH 直接跑, 电脑浏览器操作
python3 color_blob_stream.py --cam 0 --port 8090      # 浏览器打开 http://<PiIP>:8090/
python3 color_blob_stream.py --no-cam --port 8090     # 无相机:合成场景体验界面
python3 color_blob_stream.py --cam 0 --width 640      # 帧率不够时缩到640宽(更快)
python3 color_blob_stream.py --cam 0 --exposure 220   # 画面偏暗时提高曝光
# 后台跑 + 停止:
nohup python3 -u color_blob_stream.py --cam 0 --port 8090 >/tmp/cbs.log 2>&1 &
kill $(cat /tmp/color_blob_stream.pid)                # 或用 pgrep -f 拿 PID 再 kill
```
网页功能:实时画面/检测框(点画面=选标定 ROI)、全部阈值滑条(即时生效)、
颜色下拉 1..6、**自动标定(ROI)**、保存/读取 `color_profile.json`、抓拍。

### ring_stream_ui.py —— 圆环识别中心调参/诊断台(★浏览器版, 独立脚本)
```bash
# Pi 端零窗口; 浏览器打开 http://<PiIP>:8091/
python3 ring_stream_ui.py --cam 0 --port 8091                 # 实时(相机默认缩到960宽)
python3 ring_stream_ui.py --no-cam --port 8091                # 合成靶标(离线自测, ~5.8fps)
python3 ring_stream_ui.py --image "工创赛标靶A4(12).png" --no-cam --port 8091
python3 ring_stream_ui.py --cam 0 --width 640 --diag-every 5  # 想更快
python3 ring_stream_ui.py --cam 0 --digit-rot-step 15         # 模板构建 6.7s→1.9s(精度略降)
# 停止 / 清残留(端口占用时):
kill $(cat /tmp/ring_stream_ui.pid)      # 或  bash kill_rsui.sh
```
网页:全部 CALIB 滑条即时生效(阈值/形态学/EMA/数字阈值/曝光/rotate_ccw…); 诊断面板显示
原始vs EMA 圆心、φ1 与 fallback、数字各类分数、投票窗口、各阶段耗时; 按钮=自动估参 /
一键加模板(打印体) / 冻结 / 抓拍 / 存·读档案 / 一键跑三套回归 / 重读图片。

**每个滑条右侧都有输入框 + 类型提示**(2026-09-12 增强):
- 提示会写明"整数/小数""是否必须奇数""范围";输入框可直接键入精确值,越界/类型不符会被夹取并回显原因;
- `thr_c` 支持**小数(步进0.1)**,1~5 之间可精调;
- `thr_block` 只接受**奇数且 ≥3**——`cv2.adaptiveThreshold` 的硬性要求,填 2 会被夹到 3;
  想要"更小的有效窗口":把 `proc_scale` 调到 1.0(同 blockSize 覆盖更小物理范围)+ 用 `pre_blur` 压噪;
- `close_k` 允许 **1~31 任意整数(含偶数)**,10~15 可精调(≈环线间距px);
- 新增 `pre_blur`(奇数,默认1=关):阈值前额外高斯,小 `thr_block` 时防噪;
- 叠加图例:**绿圈/黄十字 = EMA(输出)中心**;**红色圆环 = 以原始(红色)中心为圆心、半径与绿环相同**,
  洋红短线连两个中心 → 一眼看出抖动偏移(静态图上两点重合时连线长度为0,属正常)。
模板目录 `digit_custom/<1|2|3>/*.png`(**打印体现场样例**, 由 `digit_templates_ext.py` 热加载)。

### color_blob_demo.py —— 同算法的桌面窗口版(⚠️ 慎用)
```bash
# ⚠️ 走 Qt 窗口: VNC 桌面上曾把整机卡死到断电; 只在必须用本地窗口时用, 优先用上面 stream 版
python3 color_blob_demo.py --cam 0                 # 需要 DISPLAY(VNC 桌面终端里跑)
python3 color_blob_demo.py --synthetic 3           # 无相机:合成场景
python3 color_blob_demo.py --selftest              # 六色自测(无相机/无UI)
python3 color_blob_demo.py --debug --color black   # 打印候选轮廓面积/圆度(调参)
python3 color_blob_demo.py --no-show --cam 0       # 无界面冒烟
python3 cam_probe.py 0                             # 抓一帧打统计(亮度/主色/存图)
```
桌面版按键:鼠标点选 ROI;`a`=自动标定;`1..6`=标定为第 1~6 色;`s`/`l`=存/读档案;
`n`/`p`=切颜色;`b`=灰世界;`v`=高光剔除;`[`/`]`=减/加曝光;`space`=冻结;`c`=抓拍;`q`=退出。

> VNC 终端里中文显示成"圆圈问号"= 系统缺中文字体, 与程序无关:
> `sudo apt-get install -y fonts-wqy-zenhei` 后重开终端;
> 想消掉 ssh 的 setlocale 警告: `echo 'export LC_ALL=C.UTF-8' >> ~/.bashrc`。

### find_cam.py —— 摄像头索引探测
```bash
python3 find_cam.py        # 打印 video0~3 哪个能开/分辨率(无参数)
```

### smoke_calib.py —— 部署冒烟(import+摄像头+圆环)
```bash
python3 smoke_calib.py             # SMOKE_OK / SMOKE_WARN_NO_CIRCLE
python3 smoke_calib.py --cam 1     # 指定索引
```

### cam_diag_aim.py —— 圆心抖动诊断(std 应 <2px)
```bash
python3 cam_diag_aim.py --frames 20
python3 cam_diag_aim.py --cam 1 --frames 30
```

## 2. 圆环检测 / 二维码 demo（两个主交付）

```bash
python3 ring_center_detect_v2.py                    # 有显示器弹窗
python3 ring_center_detect_v2.py --stream           # 推流 8080(默认)
python3 ring_center_detect_v2.py --stream 8082      # 指定端口
python3 ring_center_detect_v2.py --no-show          # SSH 纯终端
python3 ring_center_detect_v2.py 图片.jpg           # 离线单图(跑完打印[result]退出)

python3 qr_stream_demo.py                           # 同上一套用法
python3 qr_stream_demo.py --stream
python3 qr_stream_demo.py 图片.jpg
```

## 2.5 性能档 / 推流限帧 / GStreamer（树莓派与 Jetson 通用，2026-09-13 新增）

```bash
# 平台+相机+推荐档位 一键探测(Jetson 上先跑这个)
python3 -u probe_jetson.py --seconds 2

# 性能档 auto: 自动识别 Jetson→high / 否则按 CPU 核数;
# 一次填好 处理宽 / 推流最长边 / JPEG质量 / 诊断频率 / 推流限帧 (显式传参优先)
python3 ring_stream_ui.py    --perf auto --cam 0 --port 8091
python3 color_blob_stream.py --perf auto --cam 0 --port 8090
python3 ring_stream_ui.py    --perf mid  --cam 0 --port 8091   # 想更省电/更凉就降档

# 想单独拧某一项:
--width 640            # 检测(处理)宽度
--stream-width 480     # 推流最长边(只缩小不放大; 编码是 CPU 大头)
--stream-fps 10        # 推流限帧(0=不限; 检测照常每帧跑, 省的是编码)
--quality 70           # JPEG 质量
--diag-every 5         # 每 N 帧做一次完整诊断
--gst [管线]           # 只有 Jetson CSI 排线摄像头需要; UVC 摄像头别加
--sensor-id 0          # CSI 传感器号(--gst auto 时用)

# 性能实测(先确保没有别的进程占着相机):
bash bench_perf.sh 12 8097    # 合成画面: low/mid/high/rdk 四档对比(纯算力)
bash bench_cam.sh  16 0       # 真实相机: 端到端

# 调优三件套(弱算力平台: 先量再改; RDK X5 靠它把 7.5fps 提到 15.8fps)
python3 -u prof_ui.py --perf rdk              # 按 UI 真实顺序分段计时(内置预热)
python3 -u exp_speed.py                       # 单算子微基准(阈值/形态/旋转/缩放/编码/OpenCL)
python3 -u exp_config.py --image a4_12.png    # 参数组合扫描(速度 + 精度对比)

# 依赖检查(视觉开箱可用; 只有要控制下位机才需要 pyserial):
bash setup_platform.sh
```

| 档位 | 处理宽 | 推流边 | JPEG | 诊断 | 推流限帧 | OpenCV 线程 | 默认用于 |
|---|---|---|---|---|---|---|---|
| low | 640 | 480 | 70 | 每 5 帧 | 10 | 2 | 最省 |
| mid | 960 | 720 | 75 | 每 3 帧 | 15 | 2 | 树莓派 4B |
| high | 1280 | 960 | 85 | 每帧 | 25 | 4 | Jetson Orin |
| **rdk** | **512** | **512** | 76 | 每 20 帧 | 12 | 4 | **RDK X5**（弱算力专用档） |

**实测**：树莓派合成靶标 5.8fps → Jetson 20fps(high)/30fps(low)；真实相机 Jetson
ring 34fps、color 27fps、`--stream-fps 5` 后处理 fps 32→35.6。
**RDK X5**：1280 全管线只有 4.3fps(high)、960 → 6.3fps(mid)。**rdk 档优化前 7.5fps**
（单帧 thr 28.7 / center 31.7 / process 57.9ms），做完调优（§6.7 的 7 项改动：
512 宽 + block25/close5 + proc_scale 0.34 + 数字节流 + φ1 复用掩膜 + 去多余 rotate +
推流 INTER_LINEAR）后 → **15.8fps（loop 60ms，thr 5.8 / center 7.1 / process 24ms）**。
⚠️ `[loop] fps=` 是**处理循环**速度（后台线程复用最新帧），**不等于相机出帧率**：
树莓派相机 read ≈60ms(≈17fps 上限)、Jetson 内置 UVC 14.9fps → **端到端帧率 = min(处理, 相机)**。
**Jetson 坑**：没有 CSI 摄像头时 `nvarguscamerasrc` 报 `No cameras available`（是"打不开"不是崩溃），
`--gst` 已做失败回退；`rotate_ccw` 是给树莓派倒装相机的，Jetson 正向相机要关掉（否则画面竖着）。
**RDK X5 坑**：`--gst` 无用（OpenCV 4.11 不带 GStreamer）；`/dev/video*` 不存在 ≠ 坏了
（MIPI 要 hobot VPS 驱动、USB 摄像头没插也一样）→ 先用 `--no-cam`/`--image` 调参；
它的 8×A55 算力只够 640 档，别手动上 high。

## 3. 回归与验证（每次改检测/协议后必跑）

```bash
python3 -u run_all_tests.py     # 一键：语法+三套回归+真实标靶图+二维码
python3 test_rotation.py        # 合成靶标 1/2/3×12角度 → 36/36
python3 test_dashed.py          # 虚线环场景 → PASS
python3 test_stability.py       # 窗口众数 → ALL PASSED
python3 verify_rotate.py        # 旋转坐标映射断言
python3 gen_test_qr.py          # 重新生成 test_qr.png(内容 156+123+516+231)
```

## 4. 相机/对焦诊断工具

```bash
python3 focus_assist.py --stream 8090    # 物理拧镜头时看清晰度分数(浏览器 :8090)
python3 focus_scan.py                    # 对焦扫描(此摄像头无 focus 控件,基本无效)
python3 focus_diag.py                    # 对焦能力终诊
python3 cam_diag.py                      # 早期相机冒烟(旧版,一般用 cam_diag_aim)
ps -eo pid,args | grep python3 | grep -v grep   # 查占用摄像头的进程
kill <PID>                               # 用 PID 杀(勿 pkill -f,会自匹配)
v4l2-ctl -d /dev/video0 --list-ctrls     # 看相机控件(定焦无 focus)
```

## 5. 当前未部署到 Pi 的本机命令(供开发期用,Windows 侧)

```bash
# Anaconda python + 纯逻辑回归(协议/任务码/统计/编排/打包/串口):
python deploy_pi\test_aim_protocol.py
python car_code上位机\test_task_code_v2.py / test_task_stats.py / test_flow_runner.py / test_cmd_pack.py / test_serial_exec.py
python car_code上位机\demo_fullflow_local.py     # 全流程端到端演示
python _pdf_extract\probe_palette.py 图片        # 图片主色调探针(PIL)
python _pdf_extract\exp_highlight_ring.py [图]   # 高光鲁棒性实验(可 scp 到 Pi 跑)
```

## 6. 提醒

- 摄像头一次只能被一个进程占用；SSH 断线不杀后台进程 → 测试用 `timeout 20 python3 ...` 包一层。
- 无 `/dev/ttyUSB*` = STM32 USB-TTL 没插 → 标定/闭环动作需先接线(见 README §0)。
- 每个脚本都可 `python3 xxx.py --help`？否(手写 argparse 无 help 文本请以本文为准)。
