# 27 工创赛 · 智能搬运小车 —— 工程总入口 README

> **这份文档是当前工程的唯一入口**：整合了 `continue.md`（视觉上位机交接）、`lecture.md`（SSH 开发教学）
> 以及 2026-09-01 ~ 2026-09-12 全部开发过程、事故与教训。
> 下次接手请**先通读本文**，再按需查 `continue.md` / `lecture.md`（它们现在只作历史附录）。
> 最后更新：2026-09-12 ｜ 维护者：上一轮接手的 agent + 队伍开发者

---

## 目录
- [0. 一分钟速览](#0-一分钟速览)
- [1. 目录结构地图](#1-目录结构地图)
- [2. 赛项解读（智能搬运）](#2-赛项解读智能搬运)
- [3. 硬件、接线与器件](#3-硬件接线与器件)
- [4. 通信协议（上位机 ↔ 下位机 / 下位机 ↔ 驱动器）](#4-通信协议上位机--下位机--下位机--驱动器)
- [5. 下位机固件（STM32F103ZET6）](#5-下位机固件stm32f103zet6)
- [6. 上位机（树莓派 4B）](#6-上位机树莓派-4b)
- [7. 全流程搬运 v2（纯逻辑层地基）](#7-全流程搬运-v2纯逻辑层地基)
- [8. 环境、访问与工具链](#8-环境访问与工具链)
- [9. 已知坑与事故档案（★最重要）](#9-已知坑与事故档案最重要)
- [10. 开发循环与协作规范](#10-开发循环与协作规范)
- [11. 现场执行手册（到场地第一天）](#11-现场执行手册到场地第一天)
- [12. 当前状态与未完成清单](#12-当前状态与未完成清单)
- [13. 命令速查](#13-命令速查)
- [14. 变更日志](#14-变更日志)

---

## 0. 一分钟速览

| 项 | 内容 |
|---|---|
| 目标赛项 | 2026 上海工创赛 / 2027 国赛「智能+工程创新赛道 · 智能搬运」 |
| 上位机 | **三台可选，同一套代码**：树莓派 4B（10.48.81.145，`ssh pi`）/ **Jetson Orin Nano Super**（10.48.81.108，`ssh jetson`）/ **RDK X5**（10.48.81.249，`ssh rdk`），只做视觉 + 流程 |
| 下位机 | STM32F103ZET6（Keil MDK，CLEAN 重构固件），**做运动/机械/回执** |
| 主通信 | USB-TTL：上位机 `/dev/ttyUSB0` ↔ STM32 **USART1 = PA9(TX)/PA10(RX)**，115200-8N1 |
| 驱动链 | 4 路 **Emm_V5.0 闭环步进**（张大头）走 STM32 **USART3 部分重映射 PC10(TX)/PC11(RX)**；底盘 4 轮 + 升降 + 载物台走 GPIO 脉冲（定时器 ramp 引擎） |
| 当前主线 | **视觉瞄准 → 回放偏移 → 放料到靶心圆环**（aim_and_place 系列）＋ **转盘物料颜色识别**（color_blob_stream）＋ **圆环调参台**（ring_stream_ui） |
| 上位机地址 | 树莓派 **`10.48.81.145`**/`justin`（别名 `pi`）；Jetson **`10.48.81.108`**/`jetson`（JetPack R36.4.3，OpenCV 4.10+CUDA）；RDK X5 **`10.48.81.249`**/`root`（RDK OS 3.5，OpenCV 4.11 无 GStreamer） |
| 上位机代码位置 | 本机 `整机程序设计\deploy_pi\` ↔ 树莓派 `~/vision_robot/` ↔ Jetson `~/VisualBot/` ↔ RDK X5 `/root/VisualBot/`（**每台都要部署，见 §10.1**） |
| 性能档 | `--perf auto`（Jetson→high / **RDK X5→rdk** / 否则按核数）一次设定 处理宽·推流宽·JPEG·诊断频率·**推流限帧**；实测 Jetson 处理 34fps·相机 14.9fps，RDK 只有 7.5fps |
| 固件工程 | **`整机程序设计\26090901TEST\`**（当日工程，最新）；历史：26090501 / 26090201 / 26090101 / 26072301TEST |
| 一句最重要的提醒 | **改完代码必须 Rebuild + 重新烧录**；烧到旧 hex 会浪费几小时（真实踩过） |

---

## 1. 目录结构地图

```
E:\工程文档\27工创赛\
├── README.md                     ← ★ 本文（唯一入口）
├── 校赛开发.md                    ← ★校赛（魔改规则）开发指南：规则完善/上位机主导架构/六次瞄准/协议扩展/主程序走读
├── 现场标定手册.md                 ← ★到场地后照做：12 步标定顺序（机械→对焦→圆环→数字→颜色→瞄准→扫码→里程→联调）+ 记录表 + 回滚
├── continue.md                   ← 历史：视觉上位机交接（09-05 前）
├── lecture.md                    ← 历史：SSH 开发全流程教学 + 十大坑
├── 智能搬运赛道要求研读笔记.md        ← 赛题要点整理（软件视角）
├── 任务码二维码生成器.html           ← 离线单文件：生成/校验任务码二维码
├── 工创赛标靶A4(12)/(13).png        ← 真实靶标图（digit=2 / digit=3，离线验证用）
├── 附件2-1/2-2 …pdf               ← 官方命题与评分原文
├── .vscode\                      ← tasks.json（13 个一键任务）等
├── _pdf_extract\                 ← 视觉演示的本地工作副本 + PDF 文本 + 各种实验脚本
│   ├── ring_center_detect_v2.py  ← ★圆环+数字识别（被 aim/ring_ui 复用）
│   ├── qr_stream_demo.py         ← 二维码识别推流
│   ├── test_rotation/dashed/stability.py  ← 三套回归
│   ├── run_all_tests.py / verify_rotate.py / focus_assist.py / probe_palette.py
│   └── exp_highlight_ring.py / bench_pipeline.py   ← 高光鲁棒性实验（暂停项）
└── 整机程序设计\
    ├── 校赛\                    ← ★校赛主程序（上位机主导：参数/动作组全在 Python 里）
    │   ├── school_main.py / car.py      ← ★流水账版主程序 + 动作库（参数写在括号里，现场看这两个）
    │   ├── aim.py / vision.py / qr_scan.py / ledger.py / lower_api.py
    │   ├── school_config.py / test_school_main_local.py   ← 规则常量 / 流水账版回归（33 项）
    │   ├── school_flow.py / motion_profile.json / test_school_local.py
    │   │                                ← 状态机版（M0~M10 + JSON 动作组）+ 其回归（53 项），保留备用
    │   └── firmware\            ← 校赛固件扩展（params/motion + 接入说明）；**刻意不放进原 Keil 工程**
    ├── deploy_pi\                ← ★上位机交付包（同一套代码同步到 Pi ~/vision_robot、Jetson ~/VisualBot、RDK X5 /root/VisualBot）
    │   ├── aim_and_place.py      ← 瞄准/放料闭环（当前现场主线；含 --gst/--slot/--mission-all）
    │   ├── aim_and_place_glare_filter.py / _pi_lite.py   ← 视觉增强变体（在 整机程序设计\ 根级）
    │   ├── calibrate_aim.py / find_cam.py / smoke_calib.py / cam_diag_aim.py / cam_probe.py / prof_loop.py / tpl_time.py
    │   ├── color_blob_demo.py / color_blob_stream.py   ← 颜色识别（桌面版·兼相机/性能档公共库 / ★浏览器版）
    │   ├── ring_stream_ui.py / monitor_ui.py / digit_templates_ext.py
    │   │                          ← ★圆环调参台 / ★双摄监控台(作业+扫码两路同时推流) / 打印体数字模板热加载
    │   ├── test_aim_protocol.py  ← 协议层回归（本机可跑）
    │   ├── deploy_to_pi.ps1 / deploy_to_jetson.ps1 / deploy_to_rdk.ps1   ← 一键部署（ping→ssh→scp→远端编译→探测）
    │   ├── probe_platform.py / setup_platform.sh       ← 平台·相机·性能档探测(Jetson/RDK X5/Pi 通用) / 依赖安装
    │   ├── probe_jetson.py / setup_jetson.sh           ← 上面两个的旧名兼容入口（转调）
    │   ├── bench_perf.sh / bench_cam.sh                ← 性能档对比（合成画面 / 真实相机）
    │   ├── README.md / 上位机指令对照表.md / SSH命令大全.md   ← ★三份现场必读
    │   └── kill_rsui.sh / rsui_test.sh / cbs_test.sh / rdiag.sh
    ├── car_code上位机\            ← ★全流程 v2 纯逻辑层（任务码/统计/编排/打包/串口）
    │   ├── task_code_v2.py / task_stats.py / flow_runner.py / cmd_pack.py / serial_exec.py
    │   ├── test_*.py（五套回归）  / demo_fullflow_local.py
    │   └── my_move.py 等          ← 旧上位机（只作参考，勿直接用，见 §2.5）
    ├── 全流程上位机v2设计.md        ← 整场任务的架构蓝图
    ├── 上位机程序分析.md            ← 旧上位机高危问题清单（14 条）
    ├── 26091601TEST\             ← ★**校赛版下位机工程（09-16 创建，开箱可编译）**：全量 + 校赛扩展已接好
    ├── 26090901TEST\             ← 上一版下位机工程（CLEAN 重构 + TTL 闭环；校赛版是它的超集）
    ├── 26090501TEST\ 26090201TEST\ 26090101TEST\ 26072301TEST\   ← 历史固件快照
    ├── STM32F103ZET6_下位机_原硬件底盘命令版\ + RaspberryPi4B_上位机_底盘手动控制\  ← 老师车历史线
    ├── STM32F407_串口通讯__* / STM32_串口通讯__位置模式\  ← 张大头例程原型（参考）
    └── car2_clean_v2_\            ← 更早的 CLEAN 重写（同车上一代）
```

**判读原则**：`2609xx01TEST` 是固件主线；`deploy_pi` 是上位机主线；`car_code上位机` 的 `*_v2` 文件是全流程地基；
其余「老师车 / car2 / F407 / 位置模式」都是**历史或参考**，别误当现役。

---

## 2. 赛项解读（智能搬运）

> 详细版见 `智能搬运赛道要求研读笔记.md`；官方原文 `附件2-1（命题与运行）`、`附件2-2（评分与规则）`。

### 2.1 硬性门槛（不满足＝取消资格）
- 铅垂投影 **≤300×300mm**、总高 **≤400mm**（含线束）；机械臂可折叠但**出发后自行展开**；
- **必须**有任务码显示装置：上部醒目、亮光、不遮挡、**字高≥12mm**，能显示任务码 + 抓取/放置正确次数；
- **单电源内置、全程不可更换**（含调试）；禁液体/腐蚀性电源；
- 允许与笔记本通讯但**运行中不可触碰**，**严禁遥控**；
- **不允许遮挡场地，只允许垂直向下补光**（相机需自带向下补光）；
- **一键启动**、明确标识、仅一次机会。

### 2.2 场地与物料（初赛）
- 场地 2400×2400mm；**灰道宽 400mm**（车宽 300 → 两侧余量仅 50mm）；淡黄区 450×450；蓝色启停区 300×300×2；
- 黑色障碍 φ50×100mm，数量/位置随机；
- 原料区 = **φ300 电动转盘**（高 80–100mm，转速 6–10s/圈，3 个物料 120° 均布，停位有误差）；
- 放置区 = 580×150mm 板，印**同心圆环 φ1~φ6（环心标数字）**，线宽 1.5mm；
- 物料：3D 打印 ABS/PLA，**≤80mm、≤100g**；初赛 **6 色**（红、黄、蓝、绿、黑=ABS；浅蓝=PLA），每色 2 个；
- 环分：**1环15 / 2环10 / 3环7 / 4环5 / 5环3 / 6环1 / 环外或倾倒 0**。

### 2.3 任务码（二维码）语义 —— 全流程开发的最高优先级坑
- 格式：**四组三位数，用 `+` 连接**，共 15 字符，如 `156+123+516+231`；
- 第 1 组 = 第一批 3 个物料的**颜色+搬运顺序**；第 2 组 = 第一批在粗加工区/暂存区的**放置环位**；
- 第 3 组 = 第二批颜色+顺序；第 4 组 = 第二批放置环位；
- 颜色编号官方固定：**红1 黄2 蓝3 绿4 黑5 浅蓝6**（组内不重复）。
- ⚠️ 旧 `car_code上位机/my_move.py` **只支持 `XXX+YYY` 两组、颜色只认红绿蓝、编号还是 1红2绿3蓝** →
  遇官方 15 字符码会 `int('+')` 崩溃或全放错。**已由 `task_code_v2.py` 重写覆盖**（见 §7）。

### 2.4 流程与死线（时间/犯规）
- 流程：一键启动 → 先到二维码板读码并显示 → 原料区按序逐个抓（**放到车上后才能抓下一个**）→
  粗加工区按序/位垂直放置 → 第一批取回暂存区平面放 → 第二批上料 → 暂存区**码垛** → 回启停区核对统计；
- **运输时禁止夹爪夹持物料**（必须放在车体/载物平台）；
- **物料底面一触地即定分，再移动/推行 → 本轮结束**；
- **原地静止 15s**（等转盘 +8s）→ 本轮结束；越出灰道进异色区 → 本轮结束；
- 每队 2 次机会取最好；初赛 3 分钟/轮、调试 3 分钟。

### 2.5 评分与「软件能力清单／缺口」
- 原始分上限 ≈ **154** = 读码显示1 + 抓取12 + 圆环放置最高 9×15 + 回区2 + 统计显示4；按最高队归一化到 100。
- 已具备（本轮开发前）：二维码识别、圆环圆心检测、红绿蓝颜色识别、底盘/机械动作命令层；
- **本轮补齐**：官方任务码解析、统计账本、编排骨架、串口执行层、颜色识别（6 色，浏览器标定）、圆环调参台；
- **仍缺**：AI/视觉的车道循迹与避障（现场调）、颜色/形状的现场阈值标定（有工具待采数据）、
  任务码显示装置驱动、决赛两种形状识别、全流程与真机动作组联调。

---

## 3. 硬件、接线与器件

### 3.1 整车（当前车）
4×全向轮底盘 + **base 旋转轴**（同步带 + 交叉滚子轴承）+ **龙门架 V2.0**（镜像双轴，丝杆/同步带升降）
+ **2040 伸缩臂滑台** + **齿轮平行夹爪**（MG995×2：夹开合 + 腕翻转）+ 垂直下视摄像头 + 车载物架/载物台。

### 3.2 轴与驱动器
| 轴 | 驱动 | 接口 | 说明 |
|---|---|---|---|
| id1 伸缩滑台 | Emm_V5.0 闭环 | USART3（PC10/PC11） | 径向伸收（画面 Y） |
| id2/id3 龙门架 | Emm_V5.0 闭环 ×2 | 同上，镜像互反 | 升降（下放/抬升） |
| id4 base 轴 | Emm_V5.0 闭环 | 同上 | 整机水平旋转（画面 X） |
| 底盘 4 轮 | 步进 + 定时器脉冲 | MOTOR3/4/7/8（DIR: PG7/PG8/PA8/PC1） | ramp 梯形加减速 |
| 升降/载物台（历史轴） | 步进 | MOTOR1（PA4 方向）/ MOTOR5 | 新方案主要用 TTL 龙门架 |
| 夹爪/腕 | 舵机 | SERVO2 PA2 / SERVO3 PA3（TIM5_CH3/CH4） | 0~255° |

### 3.3 引脚速查（来自固件 README.TXT）
- 步进：PB10(step1)/PA4(dir1)、PB11(step2)/PA5(dir2)、PC6(step3)/PG7(dir3)、PC7(step4)/PG8(dir4)、
  PB8(step5)/PG13(dir5)、PB9(step6)/PE6(dir6)、PC8(step7)/PA8(dir7)、PC9(step8)/PC1(dir8)
- 舵机：PA1(SERVO1)/PA2(SERVO2)/PA3(SERVO3)；串口：**USART1 PA9/PA10**（对树莓派）、**USART2 重映射 PD5/PD6**（历史，串口屏）
- 闭环电机：**USART3 部分重映射 PC10(TX)/PC11(RX)**

### 3.4 树莓派侧
- 摄像头：Microdia LRCP G720P（USB 0c45:1915），**定焦、无对焦马达**（要清晰只能物理拧镜头）；
  1280×720 MJPG，帧率天花板 ≈17fps（read 固定 ~60ms）；**设备号会因插拔漂移**（见过 video0↔video1）。
- USB-TTL：`/dev/ttyUSB0`（STM32 的 USB 转串口）；接 **PA9/PA10/GND**，注意 TX/RX 交叉。
- 环形补光 LED 灯圈（用户自备）；规则只允许**垂直向下**补光。

---

## 4. 通信协议

### 4.1 树莓派 ↔ STM32（USART1，115200-8N1）
```
帧: 0xFF + 命令(1B) + 参数(7B) + 0xFE          共 10 字节, 无校验
回执: 0xFF 0x01 0xFE                           (UART_SendPacket2UP(0x01))
握手(旧)：0xFF 0xFD ... 0xFE（历史，新固件不需要）
```
- **字节序**：16/32 位**一律大端**（0x04 二维码、0x0C 升降距离、0x10~0x12 的 vel/clk）；
- **单字节**：int8 补码，范围 **-128~127**（旧代码写 `128` 是 bug）；
- **回执时机**：非阻塞命令（0x10~0x12 电机）即时回；**阻塞动作组 0x13/0x16 整组跑完才回** →
  上位机 `ACK_WAIT_ACT = 25s`（3s 会误判超时并在下位机忙时乱发命令，属真实 bug 已修）。

### 4.2 命令字全集（`car_config.h` 的 `CarCommand`）
| 命令 | 含义 | 参数位置 |
|---|---|---|
| 0x01 / 0x02 / 0x03 | X 位移 cm / Y 位移 cm / 旋转 ° | buf[1] / buf[2] / buf[3]（int8，+前/+左/+逆时针） |
| 0x04 | 显示任务码（历史串口屏） | buf[4..7] 两组大端 16 位 |
| 0x05 / 0x06 | X/Y 位移 mm（慢档） | buf[1] / buf[2] |
| 0x07 / 0x08 / 0x09 / 0x0A | 转盘抓 / 地面抓 / 放一层 / 放二层 | — |
| 0x0B / 0x0C | 升降预设位 / 升降相对步数（大端 16 位） | buf[1] / buf[1..2] |
| 0x0D | 复合（边跑边升） | buf[1] 子模式 / buf[2] 距离 |
| 0x10 / 0x11 / 0x12 | TTL id1 / id2+id3 / id4 | buf[1]=dir, buf[2..3]=vel(RPM) 大端, buf[4..7]=clk 大端（3200 脉冲=1 圈） |
| **0x13** | **放料动作组**（下放→停→开爪→抬升） | 无参（阻塞） |
| 0x14 / 0x15 | 夹爪 0开/1闭 / 夹爪任意角度(0~255°) | buf[1] |
| **0x16** | **去车载物架夹料动作组**，**buf[1] = 物架位 1/2/3** | 1=base 正转1700；2=base 不动；3=base 反转1700（阻塞） |

### 4.3 STM32 ↔ Emm_V5 驱动器（USART3，115200）
- 帧由 `Emm_V5.c` 生成：`[地址][功能码][参数…][0x6B]`；地址 0=广播，1~7 电机；
- 位置模式 0xFD（13 字节，含 dir/vel/acc/clk/raF/snF）；多机同步：各轴先发 `snF=1` 暂存，再对 0 广播触发；
- 到位返回：`[addr] 0xFD 0x9F 0x6B`（**需在驱动器菜单开 Response→Reached**），固件在 USART3 中断里 4 字节滑窗解析；
- 厂商提醒：多台同开 Reached 可能撞包 → 镜像对只等 id2。

---

## 5. 下位机固件（STM32F103ZET6）

### 5.1 工程谱系（**别搞混**）
| 工程 | 时间 | 用途/状态 |
|---|---|---|
| `26072301TEST` | 07-23 | 早期 CLEAN，含出图 Demo（**历史**） |
| `26090101TEST` | 09-01 | 首次合入 Emm_V5 TTL（历史） |
| `26090201TEST` | 09-03 | 编译出 hex（09-03 14:51），TTLctrl 0x16 前身 |
| `26090501TEST` | 09-09 | 打磨版（09-09 19:15 有 hex） |
| **`26090901TEST`** | 09-12→now | **当前工程**：方向校正 + 平滑减速 + 0x16 三位动作组适配 |

> ⚠️ `26090901TEST.hex` 最新是 **09-12 14:13** 编的，而 **09-12 下午还改过**（0x16 的 `buf[1]` 位号适配、
> TTLctrl 三函数+分发、car_config 注释）→ **必须 Rebuild 再烧**，否则没有三位功能。

### 5.2 CLEAN 重构结构（源码组织）
| 文件 | 职责 |
|---|---|
| `car_config.h` | **全车唯一数字仓库**：机械换算、运动档位、舵机角度、升降高度表、协议命令枚举 |
| `ramp.c/.h` | 通用梯形加减速引擎（AVR446）：`Ramp_Start(axis, steps, accel, decel, speed)` + `Ramp_OnTick`；三根轴 chassis(TIM3)/lift(TIM2)/tray(TIM4) |
| `chassis.c/.h` | 底盘方向查表 + 换算 + 阻塞接口（`Chassis_MoveX/Y_cm/mm`、`Chassis_Rotate_deg`、Async + WaitDone） |
| `claw.c/.h` | 升降（绝对高度制）+ 舵机原语（`Grip_Open/Close/Angle`、`Wrist_Front/Back`）+ 编舞 |
| `TTLctrl.c/.h` | 4 路 Emm_V5 闭环控制（USART3）+ 到位交互 + 动作组（放料 / 三位夹料） |
| `protocol_and_main.c` | 协议分发 `Protocol_Handle()` + `main()` |
| `direction_check.c/.h` | 方向/线序校准 demo（宏开关，默认关） |
| 沿用零改动 | `motor.c`(仅 Init)、`uart.c`、`system_init`、`ws2812`、`oled`、`key`、`servo`、`time_count` |

### 5.3 编译与烧录（Keil5）
1. 打开 `26090901TEST\USER\26090901TEST.uvprojx`；
2. 若要用方向校准 demo：先把 `CLEAN\direction_check.c` **Add Existing Files** 进 CLEAN 组；
3. **Rebuild**（确认 0 Error；`Debug_ManualSeq` 未使用会有 1 条 warning，可忽略）→ 生成 `OBJ\26090901TEST.hex`；
4. ST-Link/J-Link 烧录（工程 `CreateHexFile=1`）；
5. 编译集合自检：**main 唯一**（`CLEAN/protocol_and_main.c`；`USER\main.c` 是历史文件**未挂进工程**）、
   中断无重名（TIM2/3/4→ramp.c，TIM6→time_count.c，USART1/2→uart.c，USART3→TTLctrl.c），IncludePath 已含 `..\CLEAN`。

### 5.4 方向表与「程序方向 ↔ 实际车向」（**已实测校正，勿随意改回**）
`chassis.c` 顶部有两层：
- `dir_pins[]`：DIR 线 + `invert` 位（轮2 PG8、轮3 PA8 置 1，机械镜像补偿）；
- 方向表 6 行：`DIR_FORWARD/BACKWARD/LEFT/RIGHT/CCW/CW`。
**2026-09-09 实测结论**：原表左右与自旋整体相反 → 已互换 `LEFT↔RIGHT`、`CCW↔CW`（前进/后退不动）。
最终正确语义（现在生效）：
```
Chassis_MoveX_cm(+)=前进  (-)=后退
Chassis_MoveY_cm(+)=左移  (-)=右移
Chassis_Rotate_deg(+)=逆时针(左旋)  (-)=顺时针(右旋)
```
验证工具：`direction_check.c` 六向 demo（上电自动跑，`DCHK_CM/DCHK_DEG/DCHK_CYCLES` 可调）。

### 5.5 加减速引擎（ramp.c）与平滑减速改造
- 档位在 `car_config.h`：`PROFILE_CHASSIS_FAST = 6, 3, 8`（当前；格式 `accel, decel, speed`）、
  `TURN = 2,2,6`、`FINE = 1,1,2`。**decel 越小 → 刹车越柔但减速段越长**；
- 原版减速把 95% 的减速压到最后 1~2cm → 体感"高速直停"。**2026-09-09 改造**：
  `Ramp_OnTick` 的 DECEL 段改为"**速度随减速步数近似线性下降**"，
  末尾速度 ≈ 最高速 ÷ `RAMP_DECEL_RATIO`（默认 **5**，可 4~8 调），不再拖超长低速尾巴；
- 备份：`ramp.c.bak_pre_smoothdecel` / `ramp.h.bak_pre_smoothdecel`（要回退旧引擎就用它们）；
- 经验：`thr` 之外的"体感抖动/一顿一顿"常来自**低速蠕行**（第一版改造的教训）→ 用 RATIO 而不是把 delay 线性拉长。

### 5.6 动作组清单（0x13 / 0x16）
| 动作组 | 调用 | 内容（当前手调参数） |
|---|---|---|
| 放料 `TTLctrl_Act_Place()` | 0x13 | 龙门下放 6000@50rpm → 停 800ms → 开爪 → 停 600ms → 抬升 6000@80rpm |
| 夹料 1 号位 `_GrabFromVehicle_1()` | 0x16 slot=1 | 开爪 → 龙门抬 4400 → **base 正转 1700** → 伸缩 6400 → 下放 1200 → 夹闭 1s → 抬 1200 → 缩回 6400 → **base 反转 1700 回观察位** → 龙门放 4400 |
| 夹料 2 号位 `_2()` | 0x16 slot=2 | 同上但 **base 不动** |
| 夹料 3 号位 `_3()` | 0x16 slot=3 | 同上但 **base 反转 1700 / 回程正转 1700** |
| 统一入口 | — | `TTLctrl_Act_GrabFromVehicle(uint8_t slot)`（0/其它→1 号位，兼容旧上位机） |
> **约定**：三个夹料组结束都必须回到"观察位"（base/伸缩相对起点偏移=0），这是"回放 aim_offset 到靶心"的前提。

---

## 6. 上位机（树莓派 4B）

### 6.1 代码位置与同步
- 本机 `整机程序设计\deploy_pi\`（唯一编辑源）→ `deploy_to_pi.ps1` 一键部署 → 树莓派 `~/vision_robot/`；
- 另有根级存档副本 `整机程序设计\aim_and_place.py` 等（历史习惯，保持 md5 一致）。

### 6.2 瞄准/放料套件（现场主线）
- `aim_and_place.py`（v4）：检测复用 `ring_center_detect_v2.RingDetector`；**分轴 PID**：
  阶段A 只用 base(id4) 收敛画面 x → 阶段B 只用伸缩(id1) 收敛画面 y → X2 二次复校；
  收敛后把"观察位→靶心"净偏移写成 `aim_offset.txt`（base/arm 脉冲，带符号）。
- 标定值（09-03 实测）：`K_PX2STEP_BASE=0.2593`、`K_PX2STEP_ARM=0.0413`（px/脉冲）；
  `CAM_GRIP_OFFSET_X/Y` 需现场实测（夹爪与相机不共轴补偿）。
- 运行模式：
```bash
python3 aim_and_place.py --port /dev/ttyUSB0 --stream 8090     # 只瞄准 + 推流
python3 aim_and_place.py --port /dev/ttyUSB0 --home            # 回观察位
python3 aim_and_place.py --port /dev/ttyUSB0 --grab --slot 2   # 取 2 号位物料(0x16)
python3 aim_and_place.py --port /dev/ttyUSB0 --replay          # 回靶心 + 放料(0x13)
python3 aim_and_place.py --port /dev/ttyUSB0 --mission         # 单料全流程
python3 aim_and_place.py --port /dev/ttyUSB0 --mission-all     # 三料:依次取 1/2/3 号位各放一次
python3 aim_and_place.py --demo --stream 8090                  # 不连车纯视觉
```
- `calibrate_aim.py`：半自动标定 K（交互回车）→ 打印建议值填回顶部；
- `smoke_calib.py`（部署冒烟）/ `cam_diag_aim.py`（圆心抖动 std）/ `find_cam.py`（相机索引）/ `cam_probe.py`（抓帧统计）。

### 6.3 视觉增强三变体（备用/可选）
| 文件 | 特点 |
|---|---|
| `aim_and_place_glare_filter.py` | 双边滤波 + CLAHE + 高光软压缩（重，慢） |
| `aim_and_place_pi_lite.py` | 轻量反光 mask + ROI Canny 残缺圆弧拟合（推荐轻量） |
| `aim_and_place_pi_vision_kalman.py` | lite + **中心卡尔曼**（门限抗突跳）+ 灰度/二值辅助（最新最稳）—— ⚠️ **本工作区里找不到这个文件**（只有 `_glare_filter` / `_pi_lite` 两份），接手前先确认它在谁手上 |
都保持 1280×720 坐标不变（换算法不用重标 K）。

### 6.4 颜色识别（转盘物料）
- `color_blob_stream.py`（★浏览器版，端口 8090，Pi 端零窗口）：实时 MJPEG + 全部阈值滑条 +
  颜色下拉 1..6 + **自动标定(ROI)** + 存/读 `color_profile.json` + 抓拍；
- 算法：降噪 → 色相增幅/压制 → 圆环式色相阈值 + S/V 门限（黑料走 V 低/S 低分支）→ 形态学 → 圆度/面积筛 → 最小外接圆；
- 实测：6 色合成自测全过；**暗色必须先去噪**（否则 22% 像素随机缺失，5×5 开运算把掩膜削成碎片）；
- `color_blob_demo.py`：同算法的 **Qt 桌面版**（⚠️ 在 VNC 里曾把 Pi 卡死，仅备用）。

### 6.5 圆环调参台
- `ring_stream_ui.py`（★浏览器版，端口 8091，Pi 端零窗口）：
  - 全部 CALIB 参数滑条 + **数字输入框 + 类型提示**（整数/小数/是否奇数/范围）；
  - 诊断面板：原始 vs EMA 圆心、φ1 与 fallback、数字各类分数、投票窗口、各阶段耗时；
  - 按钮：自动估参 / **一键加模板(打印体)** / 冻结 / 抓拍 / 存·读档案 / 一键跑三套回归 / 重读图片；
  - 叠加图例：**绿圈+黄十字 = EMA 输出中心**；**红圆环 = 以原始中心为圆心、半径与绿圈相同**；洋红短线=偏移；
  - 关键约束：`thr_block` 必须**奇数且 ≥3**（OpenCV 限制），想要更小有效窗口用 `proc_scale=1.0` + `pre_blur`；
    `thr_c` 支持**小数(0.1)**；`close_k` 允许 **1~31 含偶数**（10~15 可精调）。
- `digit_templates_ext.py`：现场打印体数字样例 → `digit_custom/<1|2|3>/*.png`，热重建模板库
  （不改 `ring_center_detect_v2.py` 源码；实测把某类分数 0.469 → 0.999）。

### 6.6 性能实测与「性能档」（三台机器一份代码）
上位机现在**一份代码跑三台机器**：`color_blob_demo.py` 里提供 `PERF_PRESETS` 与
`detect_perf()`（读 `/proc/device-tree/model`：Jetson→`high`、**RDK X5→`rdk`**、否则按核数）
＋ `resolve_perf(args, --perf)`；`--perf auto|low|mid|high|rdk` 会自动填
`width / stream_width / quality / diag_every / stream_fps`（显式传参优先，不会被覆盖）。

| 档位 | 处理宽 | 推流最长边 | JPEG | 诊断频率 | 推流限帧 | OpenCV 线程 | 适用 |
|---|---|---|---|---|---|---|---|
| low | 640 | 480 | 70 | 每 5 帧 | 10 fps | 2 | 最省 |
| mid | 960 | 720 | 75 | 每 3 帧 | 15 fps | 2 | 树莓派 4B（默认） |
| high | 1280 | 960 | 85 | 每帧 | 25 fps | 4 | **Jetson Orin（默认）** |
| **rdk** | 640 | **640** | 78 | 每 4 帧 | 12 fps | 4 | **RDK X5（默认；见下）** |

实测（同一套代码，`--no-cam` 合成画面 + 真机相机）：

| 场景 | 树莓派 4B | Jetson Orin Nano Super | RDK X5 |
|---|---|---|---|
| `ring_stream_ui` 合成靶标全管线 | 5.8 fps（loop 126ms） | **20.0 fps**（loop 47~52ms） | 4.3 fps(high) / **15.8 fps(rdk, 已优化)** |
| `ring_stream_ui` **真实相机** | ~7 fps 量级 | **34 fps**（loop 27~31ms） | 未测（RDK 上没插相机） |
| `color_blob_stream` 合成 | 6.9 fps(960 真实相机) | 4.1 fps 合成 / **26.9 fps 真实相机** | 5.6(high) / 6.6(mid) / **10.8(rdk)** |
| `ring_stream_ui` 单帧耗时 | loop 126ms | loop 27~31ms（thr 4.3 / center 5.5 / process 6.5ms） | loop **60ms**（thr 5.8 / center 7.1 / process ~24ms） |
| `--stream-fps 5` 后的处理 fps | — | 32.1 → **35.6**（限帧确实省下编码） | — |
| 相机 read 上限（`probe_platform.py`） | ~60ms/帧 → **≈17 fps** | **14.9 fps**（内置 UVC，首帧 336ms） | 无相机（`/dev/video*` 不存在） |
| 平台 | 4×A72 / 4GB / Python 3.13.5 / OpenCV 4.5ish | 6 核 / 7.4GB / Python 3.10.12 / OpenCV 4.10（CUDA 12.6、GStreamer） | **8×A55 / 6.9GB / Python 3.10.12 / OpenCV 4.11（无 GStreamer）/ RDK OS 3.5** |

- **为什么 RDK X5 要单独一档**：8 个 A55 核看着多，但单核弱 + OpenCV 无 GStreamer/NEON 优化，
  1280 全管线只有 4.3 fps（比 Pi4 还慢），**640 处理 + 640 推流**才回到 7.5~10.7 fps。
  它的 BPU（10 TOPS）本套件**没用上**（纯 OpenCV；要提速得走 hobot/TROS 的模型量化路线）。
- ⚠️ **看数据要分清两种 fps**：`[loop] fps=` 是**处理循环**的速度（后台采集线程会不断复用"最新帧"，
  所以处理 fps 会高于相机实际出帧率）；**端到端有效帧率 = min(处理 fps, 相机出帧率)**。
  Jetson 处理 34fps 但相机只有 14.9fps → **瓶颈在相机**；RDK 反过来 —— **瓶颈在算力**。
- **推流限帧 `--stream-fps`**（按档位 10/15/25/12，`0`=不限）：浏览器看不出 25fps 以上的差别，
  而 JPEG 编码是 CPU 大头；检测照常每帧跑，只有编码被节流（Jetson 上 32.1→35.6 fps 就是它的收益）。
- 优化套路（三台通用）：**先缩放到推流尺寸再画叠加**（否则 2px 细线被降采样抹掉）、
  **只缩小不放大**（相机旋转后 720×1280 再"缩到 960"其实是放大 960×1707，白烧 CPU）、
  单图缓存、**合成场景缓存**（曾让 `color_blob_stream` 的 bench 只测到 1.5fps —— 测的其实是"画图"）、
  `--diag-every 5`、`--stream-width 480`。
- 一键对比：`bash bench_perf.sh`（合成画面，low/mid/high/rdk 四档 + 自动打印推荐档）、
  `bash bench_cam.sh`（真机端到端）。

### 6.7 弱算力平台调优手册（RDK X5 实录：7.5 → 15.8 fps）
**第一步永远是量，不是猜** —— 三件套（已随包部署到三台机器）：
```bash
python3 -u prof_ui.py --perf rdk              # 按 UI 真实顺序分段计时（先回答"慢在哪"）
python3 -u exp_speed.py                       # 单算子微基准（阈值/形态/旋转/缩放/编码/OpenCL）
python3 -u exp_config.py --image a4_12.png    # 参数组合扫描（每档给 ms + 精度对比）
```
⚠️ 用 prof 时**必须预热**：数字模板库是懒加载的（首次在 RDK 上 ~1s），不预热会把建库时间
算进 `diag.digit_scores`（我们第一次就得到 201ms/帧 的假数据）。

**RDK X5 优化前的耗时地图**（`exp_speed.py`，640 宽处理图上单次调用）：

| 环节 | 实测 | 对比 | 结论 |
|---|---|---|---|
| `adaptiveThreshold` GAUSSIAN block=41 | **72 ms** | MEAN_C 同窗口 13ms；GAUSS block=21 只要 36ms | **头号杀手**，窗口越大越贵 |
| `morphologyEx CLOSE` ellipse 9×9 | **22.5 ms** | 5×5 → 7.6ms；3×3 → 3.1ms | 第二杀手 |
| `rotate_ccw`（每帧 90°） | **14.6 ms** | 先缩 0.5 再转 → 2.6ms；不转 → 0 | 相机正向就别转 |
| `resize` INTER_AREA | 10.2 ms | INTER_LINEAR → 5.3ms | 预览图不值得用 AREA |
| JPEG 编码 960/640/480 宽 | 34.9 / 14.5 / 8.0 ms | — | 推流宽度直接决定开销 |
| `UMat`/OpenCL | GAUSS41 反而 **292 ms** | — | 弱平台别指望 OpenCL |
| 识别数字（2592 模板矩阵乘） | ~11 ms | — | 不是瓶颈，但可以少跑几次 |

**落地的 7 项改动**（全部实测 + 三套回归通过）：

| # | 改动 | 省下 |
|---|---|---|
| 1 | `rdk` 档：`width 640→512`、`stream_width 640→512`、`diag_every 4→20` | 诊断重跑从 27→1.6 ms/帧 |
| 2 | 算法参数随档覆盖：`thr_block 41→25`、`close_k 9→5` | 检测 -27%（精度与基线逐项一致） |
| 3 | `proc_scale 0.5→0.34`（检测尺度 = width × proc_scale） | 检测面积 -40% |
| 4 | **数字识别节流** `digit_every=3`（新增 CALIB 项，默认 1 行为不变） | ~5 ms/帧 |
| 5 | φ1 内径**复用** `find_center` 的掩膜（`find_inner_radius(..., bw_full=)`） | ~8 ms/帧 |
| 6 | 去掉每帧多余的 `rotate`（原图副本只在冻结时留存） | ~7 ms/帧 |
| 7 | 先缩放再旋转 + 推流缩放改 `INTER_LINEAR` | ~12 ms/帧 |

**结果**：`ring_stream_ui --perf rdk` 从 **loop 102~109ms / 7.5fps → loop 60ms / 15.8fps（+111%）**，
三套回归 `36/36`、`PASS`、`ALL WINDOW-MODE TESTS PASSED`。

**还没上的更大招（按性价比排序）**：
1. **关掉 `rotate_ccw`**（相机正向时）→ 再省 ~7ms/帧，零风险；
2. 继续压推流：`--stream-fps 6` + `--stream-width 384`（浏览器预览本来就看不出差别）；
3. **多进程流水线**：8 核拆成"采集+旋转 / 检测 / 编码+HTTP"三个进程 + 共享内存，理论再快 1.5~2×（工程量大）；
4. **BPU**：把数字识别换成量化模型（hobot/TROS）或整体走 hobot 图像管线，10 TOPS 是白给的算力（学习成本高）；
5. **换平台**：同一套代码在 Jetson 上同档实测 34fps（那边瓶颈反而是相机 14.9fps）。

**通用经验（换任何平台都适用）**：
- 端到端 fps 只说明"慢"，`prof_ui.py` 才说明"慢在哪"；改完必须**复测 + 回归**双确认；
- **合成画面会掩盖真实性能**：`color_blob_stream` 曾把"每帧重画合成图"算进 bench，测出 1.5fps 的假结果；
- **懒加载会污染 profile**：模板库/首帧缓冲一定要预热；
- `[loop] fps=` 是处理循环速度，**不等于相机出帧率**，端到端 = min(处理, 相机)；
- 算法参数（`thr_block`/`close_k`/`proc_scale`）既影响质量也影响速度 —— 先用 `exp_config.py`
  看"精度是否与基线一致"，再决定要不要落到档位里。

---

## 7. 全流程搬运 v2（纯逻辑层地基）

> 蓝图见 `整机程序设计\全流程上位机v2设计.md`；代码在 `整机程序设计\car_code上位机\`。

| 模块 | 作用 | 回归 |
|---|---|---|
| `task_code_v2.py` | **官方任务码解析**（4 组 15 字符 / 6 色编号 / 位置 / 查重）+ `describe()` 人话翻译 | `test_task_code_v2.py` 9 项 |
| `task_stats.py` | 统计账本：抓取/放置正确次数、环分、`display_lines()`，支持同一物料多场合放置 | `test_task_stats.py` 5 项 |
| `flow_runner.py` | 编排骨架：任务链/依赖跳过/重试/总时长预算/静止看门狗/统计联动 + 结构化日志 | `test_flow_runner.py` 7 项 |
| `cmd_pack.py` | 串口帧打包 0x01~0x16（int8 限幅、16/32 位大端、`pack_grab_vehicle(slot)`） | `test_cmd_pack.py` |
| `serial_exec.py` | 串口执行层：延迟 import pyserial、10B 帧校验、ACK 滑窗、超时重发 | `test_serial_exec.py` 7 项 |
| `demo_fullflow_local.py` | 端到端演示：任务码 → 19 步编排 → 执行 → 统计 → 显示行（假执行器） | 直接运行 |
- **本机就能全绿**：`D:\programs\anaconda\python.exe`（3.13.5，有 numpy，**无 cv2/pyserial**）→
  纯逻辑层不依赖 cv2；视觉层只能在树莓派跑。
- 下一步：把 vision（读码/颜色/圆环）接进 Executor，并在车上定稿动作原语表（旧车 0x01~0x0D / 新车 0x10~0x16 两套配置）。

---

## 8. 环境、访问与工具链

### 8.1 树莓派（唯一真机）
```bash
ssh pi                 # 本机 ~/.ssh/config 已配: HostName 10.48.81.145 / User justin / IdentityFile ~/.ssh/pi_deploy
cd ~/vision_robot      # 上位机代码目录
```
- **IP 会变**（学校网络/换网）：`ssh pi` 不通时先 `ping raspberrypi.local`，再改 `~/.ssh/config` 的 `HostName`；
- 历史地址：10.169.170.145（旧）、192.168.3.30（很旧，已废）；
- 密钥：本机 `~/.ssh/pi_deploy`（ed25519，已加进 Pi 的 `authorized_keys`）。
- ⚠️ 事故：`~/.ssh/config` 曾只剩 Host/HostName/User（**IdentityFile 行丢失**）→ 表现为 `Permission denied (publickey,password)`；
  显式 `ssh -i ~/.ssh/pi_deploy ...` 能过就是这个问题 → 重写 config 即可。

### 8.2 Jetson Orin Nano（第二台上位机，2026-09-13 接入）
```bash
ssh jetson                 # ~/.ssh/config: HostName 10.48.81.108 / User jetson / IdentityFile ~/.ssh/jetson_deploy
cd ~/VisualBot             # 上位机套件（与树莓派 ~/vision_robot 同一套代码）
```
- **平台实测**：`yahboom`（NVIDIA Jetson Orin Nano Engineering Reference Developer Kit Super）、
  JetPack **R36.4.3**、6 核、7.4GB、Python **3.10.12**、numpy 1.23.5、
  OpenCV **4.10.0（GStreamer 1.20.3 / CUDA 12.6 / cuDNN 9.3 / V4L2）**；
- **相机**：**板载/外接 UVC 摄像头**（`Integrated_Webcam_HD`，`/dev/video0`，1280×720，
  read 上限 **14.9fps**）；**CSI 排线摄像头没有**（`nvarguscamerasrc` 报 `No cameras available`）→
  **平时不要加 `--gst`**（`--gst` 只对 CSI 有用，UVC 加了会白等一次再回退）；`/dev/video1` 是它的 metadata 节点，打不开是正常的。
- **部署**：`powershell -ExecutionPolicy Bypass -File 整机程序设计\deploy_pi\deploy_to_jetson.ps1`
  （传全部工具+文档+`ring_center_detect_v2.py` 到 `~/VisualBot`，自动远端 `py_compile` + 平台/相机探测；`-WithLogic` 连 v2 纯逻辑层一起传）；
- **依赖**：`bash setup_jetson.sh` —— 视觉部分开箱可用；**要控制下位机才需要 pyserial**
  （Jetson 出厂没有，脚本会 `pip3 install --user pyserial`，实测 3.5 装上）；
- **性能档**：`--perf auto` 在 Jetson 上自动选 **high**（1280 处理 / 960 推流 / q85 / 25fps 限帧）——
  实测余量很大（真机 34fps 处理，相机只有 14.9fps），**想更省电/更凉可以手动 `--perf mid`**；
- **注意机位方向**：树莓派的 `CALIB["rotate_ccw"]` 是为**倒装**相机设的；Jetson 用的是正向摄像头，
  直接跑会出现画面转 90°（竖着）→ 在圆环调参台把 `rotate_ccw` 关掉后**存档**，或在首启命令里用面板改。
- 首次授权（一次性）：把 `~/.ssh/jetson_deploy.pub` 的内容追加到 Jetson 的 `~/.ssh/authorized_keys`。

### 8.3 RDK X5（第三台上位机，2026-09-14 接入）
```bash
ssh rdk                    # ~/.ssh/config: HostName 10.48.81.249 / User root / IdentityFile ~/.ssh/rdk_deploy
cd /root/VisualBot         # 与 pi:~/vision_robot、jetson:~/VisualBot 同一套代码
```
- **平台实测**：`D-Robotics RDK X5 V1.0`（地平线 Sunrise 5）＋ **RDK OS 3.5.0** / Ubuntu 22.04.5、
  内核 6.1.83、**8×Cortex-A55**、6.9GB、Python 3.10.12、numpy 1.26.4、
  **OpenCV 4.11（V4L2 有、GStreamer 没有）**；`/usr/hobot` 与 `/sys/class/vps` 都在（BPU/TROS 工具链就绪）。
- **默认性能档是 `rdk`（640 处理 / 640 推流 / q78 / 12fps 限帧）** —— 原因见 §6.6：
  它的算力只够这个分辨率（1280 全管线 4.3fps，比 Pi4 还慢）。
- **相机**：本次接入时 **`/dev/video*` 不存在**（USB 摄像头没插、MIPI 驱动也没加载）→
  用 `--no-cam`（合成靶标）或 `--image 图片` 照样能调参；接了相机直接 `--cam 0`。
  MIPI CSI 摄像头由 **hobot VPS** 驱动，出图后同样表现为 `/dev/video*`，当 UVC 用即可。
- **不要加 `--gst`**：本机 OpenCV 不带 GStreamer，加了他会白等一轮再回退 V4L2。
- **部署**：`powershell -ExecutionPolicy Bypass -File 整机程序设计\deploy_pi\deploy_to_rdk.ps1`
  （默认 `root@10.48.81.249:/root/VisualBot`；若换 sunrise 账号：
  `-User sunrise -RemoteDir /home/sunrise/VisualBot`。首启授权同 §8.2，用 `~/.ssh/rdk_deploy.pub`）。
- **依赖**：`bash setup_platform.sh` —— 实测 pyserial 3.5 已在，numpy/opencv 齐全。
- **首启验证**：`python3 -u probe_platform.py --seconds 2` → 打印 `family: RDK` + 推荐档位 `rdk`。
- ⚠️ 三台机器的存档文件（`ring_calib.json` / `color_profile.json` / `aim_offset.txt`）**同名不同内容**，
  换机调参前先确认是哪一份；`rotate_ccw` 也要按各自相机朝向设。

### 8.4 本机（Windows）
- Python：`D:\programs\anaconda\python.exe`（3.13.5 / numpy；**无 cv2、无 pyserial**；pip 装 opencv 无网络）；
- **没有 Keil/ARM 工具链** → 固件只能在有 Keil 的机器上编译；
- VSCode `.vscode\tasks.json`：**13 个一键任务**（部署、回归、探针、演示等）；
- 所有纯逻辑回归都能本机跑（见 §13）。

### 8.5 显示环境（VNC）
- 树莓派上 **不要用 OpenCV 的 Qt 窗口**（`cv2.imshow`）：在 VNC/无 DISPLAY 下会直接崩或**卡死整机**（真实事故，只能断电）；
- 正确姿势：**浏览器版**（`color_blob_stream.py` / `ring_stream_ui.py`），或 SSH 里 `--no-show` 跑无界面；
- 非要在 SSH 里开窗口：`DISPLAY=:0`（不行换 `:1`）或 `QT_QPA_PLATFORM=vnc`（再连 `:5900`）；
- VNC 终端里中文显示成"圆圈问号"= **系统缺中文字体**：`sudo apt-get install -y fonts-wqy-zenhei`；
  想消掉 `setlocale` 警告：`echo 'export LC_ALL=C.UTF-8' >> ~/.bashrc`。

---

## 9. 已知坑与事故档案（★最重要）

### 9.1 工具链 / PowerShell（踩得最多）
1. **PowerShell 引号地狱**：`ssh pi "python3 -c \"...\""` 里的引号会被双层转义吃掉（我们多次看到 `\"` 变成字面反斜杠 → Python `SyntaxError`）。
   → **铁律：远程跑 Python 一律"本地写脚本文件 → scp → 远程执行"**，不要内联。
2. **`$()` 会被本机 PowerShell 抢先展开**：`ssh pi "ts=$(date +%H%M%S) ..."` 在本机就执行了 `date`。
   → 远程脚本写进文件；或远程用 `\$(...)` 且确认转义。
3. **`sed` 替换时引号被吃**：`sed 's/"camera_id": 0/.../'` 曾把文件写坏（变成 `camera_id.: 1`）。
   → 用不含引号的模式（`camera_id.: 0`）或用 Python 脚本改。
4. **`$ErrorActionPreference="Stop"` + ssh 的无害 stderr**：树莓派 locale 警告走 stderr，会把 **部署脚本整段中断**。
   → 部署脚本改 `Continue`（`deploy_to_pi.ps1` 已修）。
5. **路径手误**：`27工创赛` 常被写成 `27创赛`（本会话发生过 5 次以上，且一次真在错误位置建了目录）→ 命令前先确认路径存在。
6. **`pkill -f xxx` 会杀自己**（命令行里含该串的 bash 包装进程）→ **用 PID**：`ps -eo pid,args | grep xxx | grep -v grep` 再 `kill <pid>`；
   同理 `pgrep -af xxx` 在本机 ssh 里会匹配到自己的包装命令，**别据此判断"还有残留"**。

### 9.2 固件 / 烧录
7. **烧旧 hex**（最贵的坑）：源码改了但没 Rebuild，或 Rebuild 后没确认 `OBJ\*.hex` 时间戳 → 行为与代码不符，
   白排查几小时。**每次烧录前先看 hex 时间**。
8. **OBJ 为空 ≠ 编译过**：`26090501TEST` 曾 OBJ 空；`26090901TEST.hex` 是 09-12 14:13，
   而 09-12 下午又改了 0x16 适配 → **必须再 Rebuild**。
9. **`USER\main.c` 是历史文件**，**没挂进工程**；真正的 main 在 `CLEAN\protocol_and_main.c`。改错文件＝白改。
10. **函数声明/定义/调用三处必须同步**：把 `TTLctrl_Act_GrabFromVehicle()` 拆成 `_1/_2/_3` 时，
    `.h` 里的旧声明 + `protocol_and_main.c` 的旧调用会让**链接失败**（我们这次就是这样发现并修好的）。
11. **`int8` 范围**：`Chassis_*_cm/deg` 与协议单字节都是 **-128~127**；旧代码钳位写 `128` 是 bug。
12. **main 里别留测试动作**：曾有一段"右移→前进→后退→左转"的测试序列忘了注释，
    结果**上电就无限循环**且抢占串口命令 → 现在测试都走宏开关（`RUN_DEBUG_SEQ` / `RUN_DIRECTION_DEMO`）。
13. **旋转/平移方向要靠实测校正**：`chassis.c` 的方向表与真实车曾整体镜像相反 →
    修法是**互换 `DIR_LEFT↔DIR_RIGHT`、`DIR_CCW↔DIR_CW`**（不是改调用端符号）。
14. **减速体感**：AVR446 原版减速把减速压到最后 1~2cm（"高速直停"）；把 delay 线性拉长又会**低速蠕行+一顿一顿**；
    正解＝按"速度随步数线性下降 + 末尾保留 1/R 速度"（见 §5.5，`RAMP_DECEL_RATIO`）。

### 9.3 协议 / 上位机
15. **阻塞动作组的 ACK 语义**：0x13/0x16 要等整组跑完才回；上位机若只等 3s 就会"超时→提前发下一条"，
    下位机忙时后者会覆盖前帧 → **丢失命令**。现用 `ACK_WAIT_ACT=25s`。
16. **0x0C 曾是小端、0x04 是大端**（老 bug）→ 现在**全大端**；改任何一端都要两端同步。
17. **旧上位机只认 2 组任务码 + 3 色编号错位**（见 §2.3）→ 用 `task_code_v2.py`，别沿用 `my_move.py`。
18. **RGB2HSV vs BGR2HSV**：旧 `Detection.py` 用的是 `COLOR_RGB2HSV`（通道互换）但阈值是按它调的——
    **不要"顺手"改成 BGR2HSV**（会全线失效）；新颜色模块统一用 BGR2HSV/Lab 重新标定。

### 9.4 视觉 / 树莓派
19. **摄像头独占**：同一时刻只能一个进程开 `/dev/video0`；SSH 断线**不杀后台进程** →
    测试一律 `timeout 20 python3 ...` 包一层，收尾 `ps -eo pid,args | grep python3 | grep -v grep`。
20. **相机设备号漂移**：插拔后 video0↔video1 会换（我们遇到过两次）。工具类脚本（aim/color/ring_ui）**已做 0..3 自动回退**；
    ⚠️ **但 `ring_center_detect_v2.py` 现在写死 `camera_id=1`、`qr_stream_demo.py` 写死 `0`（不一致！）** →
    跑之前先 `python3 find_cam.py`，必要时改这两个文件的 `camera_id`（或给它们加 `--cam`，见 §12 TODO）。
21. **定焦摄像头**：无 focus 控件（`v4l2-ctl --list-ctrls` 可证）→ 只能物理拧镜头；用 `focus_assist.py` 看拉普拉斯方差。
22. **明暗/反光**：颜色与圆环都吃光照；必须**锁曝光 + 锁白平衡**（`AUTO_EXPOSURE=0.25 / EXPOSURE=156 / AUTO_WB=0`），
    并保持环形补光稳定；抓帧统计中央亮度只有 30 那种情况**先调灯再谈算法**。
23. **暗色物料阈值噪声**：暗像素的 S 极不稳定（小噪声→S 爆到 100+），硬阈值 + 5×5 开运算会把掩膜打碎 →
    **先高斯降噪再阈值**（`blur_k`/`pre_blur`），黑色分支放宽 S。
24. **降采样会吃掉细线**：叠加画在大图上再缩到 720 → 2px 线变 0.4px 看不见 →
    **先缩放到推流尺寸再画叠加**（已改）。
25. **旋转后又被"缩"成放大**：1280×720 逆时针转 90° 变 720×1280，若再按"宽度=960"缩放＝**放大到 960×1707**，白烧几百 ms →
    规则改成**只缩小不放大**。
26. **每帧重读大图 / 每帧重造合成图 / 每帧写盘**：都曾把帧率压到 1fps 以下 → 缓存 + 只在需要时写盘。
27. **启动慢≠卡死**：数字模板库构建 **6.7s**（2592 张，step=5°）→ 现在**后台构建 + 先出画面**，
    UI 立即可用；想快用 `--digit-rot-step 15`（1.9s）。

### 9.5 协作 / 环境
28. **同一目录多台机器/多人改**（文件时间戳出现 09-09/09-12，说明队友也在这份工作区里改）→
    动手前先 `Get-ChildItem -Recurse | Sort LastWriteTime` 看"谁刚改了什么"，避免覆盖对方工作。
29. **端口占用**：UI 重复启动会 `Address already in use`（旧实例还占 8091）→ 已加友好提示 + `kill_rsui.sh`。
30. **改文件四同步**（见 §10）：只改一处＝白改。

### 9.6 跨平台 / Jetson / RDK X5（2026-09-13~14 接入时踩到）
31. **PowerShell 5.1 读不了「无 BOM 的 UTF-8 .ps1」**（最阴的一个）：脚本里的中文被按 GBK 解码 →
    变成 `浼犳牳蹇冨伐鍏?` 这种乱码，**字符串里的引号被吃掉 → 整脚本 `Unexpected token ')'` 语法错误**，
    报错行号还指在无关的行上。→ **本项目所有 `.ps1` 必须存成 UTF-8 with BOM**（`deploy_to_pi.ps1`/
    `deploy_to_jetson.ps1` 已加）。反过来，**`~/.ssh/config` 千万不要留 BOM**（首行 `Host` 前有 BOM 会让该 Host 匹配不上）。
32. **`ssh` 内联命令里的 `\[perf\]`、`"..."`、`$(...)` 会被本机 shell 再吃一层**：本会话又踩了一次
    （`grep -E "\[cam\]"` → 远端报 `bash: [cam]: command not found`）。→ 一律**写脚本文件→scp→执行**；
    临时用也要**避开方括号/双引号**（`tail -3` 比 `grep -E '\[x\]'` 稳）。
33. **CSI ≠ UVC**：Jetson 上 `nvarguscamerasrc` 只在**排线摄像头**存在；没有 CSI 时它会打印
    `No cameras available` 并让 `VideoCapture` 打开失败（**不是崩溃，是返回打不开**）。
    `--gst` 因此做成"失败自动回退 V4L2"，但**UVC 摄像头别加**（白等一轮）。判断用 `probe_jetson.py`。
34. **`rotate_ccw` 是与机型绑定的**：树莓派相机倒装所以开了；换到 Jetson 正向摄像头就变成画面竖着 →
    先确认画面方向再调参，否则一切阈值都白调。
35. **别把"处理 fps"当"相机 fps"**：后台采集线程让处理循环一直在跑（34fps），
    而相机只有 14.9fps（read 阻塞在另一个线程）→ **端到端帧率取两者较小值**，
    真要提速先换相机/换分辨率，而不是继续优化算法。
36. **Jetson 出厂没有 pyserial**：`aim_and_place.py` 曾在 Jetson 上直接
    `ModuleNotFoundError: No module named 'serial'` → 现在顶部改 try-import，
    **纯视觉（`--demo`/`--stream`）不装也能跑**；要控制下位机就 `bash setup_jetson.sh`。
37. **`setsid nohup python3 ... &` 起后台 UI 时，ssh 会话可能挂着不返回**（stdin 没关）→
    用 `ssh -n`（禁用 stdin）或加 `< /dev/null`；收尾仍用 `ps -eo pid,args | grep ... | grep -v grep`。
    （实测最稳的写法：`ssh -n host "(setsid python3 ... > /tmp/x.log 2>&1 < /dev/null &) ; sleep 12; tail /tmp/x.log"`。）
38. **RDK X5 的「8 核」是假象**：8×Cortex-A55 单核明显弱于 Pi4 的 A72，加上 OpenCV 无 GStreamer/NEON 优化，
    1280×720 全管线只有 **4.3fps**（Pi4 5.8fps、Jetson 20fps）→ 用 **`--perf rdk`（640 处理）** 才回到 7.5~10.7fps。
    它的 **BPU（10 TOPS）本套件没用上**，纯 CPU 跑 OpenCV；要提速得走 hobot/TROS 的模型量化路线。
39. **RDK X5 的 OpenCV 4.11 不带 GStreamer**（`cv2.getBuildInformation()` → `GStreamer: NO`）→
    `--gst` 在它上面纯属浪费一轮（会先失败再回退 V4L2）；三台机器里只有 Jetson 的 CSI 需要 `--gst`。
40. **RDK X5 上 `/dev/video*` 不存在 ≠ 坏了**：MIPI CSI 要 hobot VPS 驱动加载后才出节点，USB 摄像头没插也一样。
    这种情况用 `--no-cam`（合成靶标）或 `--image 现场照片` 照样能把阈值/参数调好，接了相机再 `--cam 0`。
41. **合成画面会掩盖真实性能**：`color_blob_stream.py` 曾把「每帧重造合成场景」算进 bench，
    在 RDK 上只测到 1.5fps（其实算法只要 ~100ms）→ 已改成**按组合缓存**（现在同机测到 10.7fps）。
    看到"某个平台特别慢"先怀疑测量方式。
42. **懒加载/首次分配会污染 profile**：数字模板库第一次要 ~1s（RDK），不预热就计时会得到
    `diag.digit_scores = 201ms/帧` 这种假数据（我们真踩过）→ **prof 脚本必须先跑一遍预热**
    （`prof_ui.py` 已内置），同理首次 `resize`/`imencode` 也要预热。
43. **`--no-cam` 与真实相机的处理宽度不一致会测错**：合成模式以前强制 `width=0`（不缩放），
    与真机 `--width 512` 走的不是同一条路 → 端到端差近一倍。已改成
    **只有 `--image` 离线单图才默认不缩放**，`--no-cam` 照性能档走。
44. **上游文件 `ring_center_detect_v2.py` 不在 `deploy_pi` 的部署清单里**（树莓派上已有原版）→
    在 `_pdf_extract\` 改了它以后，**必须三台一起 scp**，否则会出现"调参台是新的、瞄准还用旧的"。
    改动它之后务必跑三套回归（`test_rotation/test_dashed/test_stability`）。
45. **别用 Python 的 `utf-8-sig` 去读-改 Keil 工程文件**：它在"没有 BOM 的文件"上**也能解码成功**，
    于是写回时会**凭空加上 UTF-8 BOM** → Keil µVision 直接报 **"cannot read project file"**
    （2026-09-16 真踩：`26091601TEST.uvprojx`、`car_config.h`、`protocol_and_main.c` 三个文件被加 BOM）。
    正解：用 `utf-8` 读（原文有 BOM 时会作为 U+FEFF 保留在首字符，写回自动还原，有无 BOM 都不改），
    或者干脆按**字节**做替换。排查办法：`ReadAllBytes` 看前 3 字节是不是 `239,187,191`，
    是的话写回 `b[3:]` 即可。（同理：`.ps1` 反过来**必须有** BOM —— 两个坑方向相反，别记串。）
46. **夹爪/腕关节是「舵机」，不是 TTL 闭环电机**：`Grip_Close/Grip_Open` 走 **0x14**、任意角度走 **0x15**，
    单位是**角度 0~255°**（`car_config.h`: `SERVO_GRIP_CLOSE=82` / `SERVO_GRIP_OPEN=50`；
    腕关节 `Servo3` 240=朝前/60=朝后）。TTL 四路（0x10/0x11/0x12）只有 1=伸缩滑台 / 2·3=龙门架 / 4=base 轴，
    **里面没有夹爪**。2026-09-16 我把夹爪写成"轴 1 的 1700 脉冲"（那个 1700 其实是 base 轴取料偏移），
    已全部改正：上位机 `car.grip_close/grip_open/grip_angle` 走舵机协议、参数改角度；
    固件 `claw.c` 的 `Grip_Close/Open` 改成**优先读参数表 `P_GRIP_*_ANGLE`**（这样 `car.setup(grip_close_angle=82)` 才真生效）。
    ⚠️ 改舵机角度**必须同步全局 `servo_angle2/3`**，否则 TIM6 每秒把旧角度写回（claw.c 已处理）。
    ⚠️ 另有一处待确认：原 `claw.c` 还有一套**步进升降**（`Lift_GoTo`，协议 0x0B/0x0C）——
    与 TTL 龙门架（0x11）是不是同一根 Z 轴，需上车确认后再定 `car.lift_up/lift_down` 走哪条。
    ✅ **2026-09-16 车主已确认本车结构**（别再按"老师那套"想当然）：
    **没有独立步进升降轴、没有载物台旋转电机（载物台是固定的）**；"升降"就是
    **TTL id2 + id3 两台反向传动**带动的龙门架 → `0x11 TTLctrl_Motor23` 内部正是"id2=dir、id3=反向"，
    所以 `car.lift_up/lift_down` 走 0x11 是**对的**；固件 `claw.c` 里的
    `Lift_GoTo / Tray_Turn120 / Claw_GrabFromTray / Claw_PutLayer1` 在本车上是**死代码，别调用**
    （`MOTOR1_Init/MOTOR5_Init` 这类初始化可保留，不影响运行）。
47. **左右/旋转方向以固件 `chassis.c` 为准，别按直觉写**：
    `Chassis_MoveY_cm`：**cm ≥ 0 = 左移、cm < 0 = 右移**；`Chassis_Rotate_deg`：**deg ≥ 0 = 逆时针(CCW)、
    deg < 0 = 顺时针(CW)**；`Chassis_MoveX_cm`：cm ≥ 0 = 前进。
    （`protocol_and_main.c` 里还留着实测注释"`Chassis_MoveY_cm(-10)` → 右移 20cm（负=右, 已实测校正）"。）
    2026-09-16 就是在这里翻的车：`lower_api.py` 原先写 `_RIGHT_SIGN=+1` / `cw=+1` → **出启动区实际在左移**。
    现已固定为 `_RIGHT_SIGN=-1`、`cw=-1`，并在 `test_school_main_local.py` 加了 **[0] 方向符号**专项断言
    （右移必须发 `0xE2 = -30`、cw 必须发 `0xA6 = -90`）——谁再改回去，回归立刻变红。
    附带一个易错点：帧布局是 `FF cmd b1 b2 b3 …`，所以 **X 在 `f[2]`、Y 在 `f[3]`、旋转在 `f[4]`**（写断言时别数错）。

---

## 10. 开发循环与协作规范

### 10.1 上位机（树莓派 / Jetson）五步循环
1. **本地改**：只改 `整机程序设计\deploy_pi\`（唯一编辑源）；
2. **部署**：`powershell -ExecutionPolicy Bypass -File deploy_to_pi.ps1`（→ `pi:~/vision_robot/`）、
   `... -File deploy_to_jetson.ps1`（→ `jetson:~/VisualBot/`）、
   `... -File deploy_to_rdk.ps1`（→ `rdk:/root/VisualBot/`，可用 `-User/-RemoteDir` 换账号）；单文件用
   `scp xxx.py pi:~/vision_robot/` / `jetson:~/VisualBot/` / `rdk:/root/VisualBot/`；
   ⚠️ **在用的那台机器必须最新**，否则会出现"在某台上修好了、换一台还是老 bug"；三台都跑同一套代码，
3. **远端校验**：`python3 -m py_compile xxx.py`；能纯逻辑测的先跑回归；
4. **实测**：`--no-show`/`--selftest` 无界面验证 → 再浏览器/相机实测；
   **换机器后第一件事是 `python3 -u probe_jetson.py`**（平台/相机/性能档一目了然）；
5. **收尾**：同步根级存档副本 → 更新 `README.md`/`continue.md` → 清残留进程 → 核对 md5。

### 10.2 固件循环
改 `CLEAN\*.c/.h` → Keil **Rebuild（看 0 Error）** → 确认 hex 时间刷新 → 烧录 → **架空轮子**先测单方向/单动作组 → 再上地。

### 10.3 通用铁律
- **远程跑 Python：写脚本文件再 scp**（不要内联引号）；
- **改前备份**（`cp x x.bak_<原因>_<日期>`），尤其动 `ramp.c`/`chassis.c`/`ring_center_detect_v2.py`；
- **回归优先**：改检测/协议后必须跑对应回归（见 §13），"合成靶标全过 ≠ 真实图能过"；
- **一切依赖时间的动作都要有超时**（赛规"静止15s即判负"）。

---

## 11. 现场执行手册（到场地第一天）

**第 0 步 · 硬件**
1. STM32 上电前**架空/清空机构上方**（有"盲升归顶"类动作）；
2. 接线：USB-TTL → **PA9/PA10/GND**（交叉）；闭环电机接 PC10/PC11；摄像头插好、环形补光打开；
3. 树莓派联网：`ping raspberrypi.local` → 改 `~/.ssh/config` → `ssh pi` → `cd ~/vision_robot`。

**第 1 步 · 固件**
4. Keil 打开 `26090901TEST` → **Rebuild（0 Error）** → 烧 `OBJ\26090901TEST.hex`（**确认时间戳是刚编的**）；
5. 上电应只做初始化+归位，然后**静待串口**（若自跑循环＝测试动作没注释）。

**第 2 步 · 上位机**
6. `powershell -File deploy_to_pi.ps1`（IP 变则 `-Pi 新IP`）；
7. `python3 find_cam.py` 确认相机索引（必要时 `--cam N` 或改 ring/qr 的 `camera_id`）；
8. `python3 smoke_calib.py` → `SMOKE_OK`；`python3 -u run_all_tests.py` → 全绿。

**第 3 步 · 标定**
9. 机械/相机位置变过 → `python3 calibrate_aim.py --port /dev/ttyUSB0` 重标 K，填回 `aim_and_place.py`；
10. 测 `CAM_GRIP_OFFSET_X/Y`（物料放夹爪正下方，读圆心 − 画面中心）；
11. 颜色：浏览器开 `color_blob_stream.py`（8090）→ 6 色依次放 ROI → **自动标定** → 保存档案；
12. 圆环：浏览器开 `ring_stream_ui.py`（8091）→ 自动估参 → 打印体不像就**一键加模板** → 保存档案 → **跑三套回归**确认没调坏。

**第 4 步 · 实弹（分步，别一上来全自动）**
13. `--home` 回观察位 → `--grab --slot 1/2/3` 逐位验证 base 转向与回位 → `--replay` 回靶心+放料；
14. 单料顺了再 `--mission`，最后 `--mission-all`（三料）；
15. 记录每次偏移/耗时，调 `deploy_pi/README.md §四` 的现场参数表。

---

## 12. 当前状态与未完成清单

| 模块 | 状态 |
|---|---|
| 官方任务码解析 / 统计 / 编排 / 打包 / 串口执行 | ✅ 纯逻辑层完成，本机回归全绿 |
| 瞄准/放料（含三物架位 0x16、mission-all） | ✅ 代码+协议+回归；**固件需 Rebuild**；K/偏移待现场 |
| 颜色识别（6 色，浏览器标定） | ✅ 工具+合成自测过；**等现场真实物料数据调阈值** |
| 圆环调参台（含一键加模板） | ✅ 已实测（A4 digit=2、加模板 0.469→0.999） |
| 平滑减速改造 | ✅ 已验证；`RAMP_DECEL_RATIO` 可按体感再调 |
| 方向表校正 | ✅ 已实测六向正确 |
| 0x13/0x16 动作组参数 | ⚠️ 仍是手调占位值，**上车实测后定稿** |
| 摄像头帧率 | ⚠️ Pi4 5~7fps；**Jetson 处理 34fps / 相机 14.9fps**；**RDK X5 只有 7.5fps(rdk 档)** |
| Jetson Orin Nano 上位机部署 | ✅ 已部署到 `~/VisualBot`（工具+文档+探测脚本齐全，真机跑通 ring/color/aim 三个工具） |
| **RDK X5 上位机部署** | ✅ 已部署到 `/root/VisualBot`，`--perf auto`→`rdk` 档，网页面板实测可用；**已做完一轮性能调优（7.5→15.8fps，见 §6.7）**；⚠️ RDK 上还没插相机 |
| 全流程搬运与真机联调 | ⏳ 未开始（见 `全流程上位机v2设计.md`） |
| 任务码显示装置 + 抓/放统计上屏 | ⏳ 未实现（规则硬性要求，需选屏+驱动） |
| 决赛两种形状识别、车道循迹/避障 | ⏳ 未实现 |

**明确 TODO（按优先级）**
1. **Rebuild + 烧录 `26090901TEST`**（含 0x16 三位适配），然后架空逐位验 `--grab --slot`；
2. **统一 ring/qr 的相机索引策略**（现在 ring=1 / qr=0 不一致）→ 建议给 `ring_center_detect_v2.py`、`qr_stream_demo.py` 加 `camera_id` 自动扫描；
3. 现场采 6 色真实物料数据 → 跑混淆矩阵 → 固化 `color_profile.json`；
4. 现场标 K 与 CAM_GRIP_OFFSET，固化 `ring_calib.json`；
5. 把 vision（读码/颜色/圆环）接进 `flow_runner` 的 Executor；
6. 0x16 三个动作组、0x13 放料组的脉冲/速度上车定稿；
7. 需要时把"颜色+圆环+二维码+瞄准"合并成**单页多管线**（相机独占 → 只能同进程）；
8. **给 RDK X5 接相机**（USB 或 MIPI：后者要确认 hobot VPS 驱动出 `/dev/video*`），
   然后 `bash bench_cam.sh 16 0` 实测真机帧率、并按现场光照重调 `ring_calib.json`；
9. **决定三台机器的分工**（谁当主控/谁跑视觉）；注意 `ring_calib.json`/`color_profile.json`/`aim_offset.txt`
   在三台上**同名不同内容**，别互相覆盖（要搬就显式 scp）。

---

## 13. 命令速查

```bash
# ===== 连接/部署（本机 PowerShell）=====
ssh pi                                  # 树莓派 10.48.81.145（~/vision_robot）
ssh jetson                              # Jetson 10.48.81.108（~/VisualBot）
ssh rdk                                 # RDK X5  10.48.81.249（/root/VisualBot）
powershell -ExecutionPolicy Bypass -File 整机程序设计\deploy_pi\deploy_to_pi.ps1 [-Pi 新IP]
powershell -ExecutionPolicy Bypass -File 整机程序设计\deploy_pi\deploy_to_jetson.ps1 [-WithLogic]
powershell -ExecutionPolicy Bypass -File 整机程序设计\deploy_pi\deploy_to_rdk.ps1 [-User sunrise -RemoteDir /home/sunrise/VisualBot]
# ★加 -Stamped：工程文件夹按时间命名，并自动更新 <原名>_current 软链（部署可回滚/可并存）
#   deploy_to_rdk.ps1 -Stamped     → rdk:/root/VisualBot_20260916_1224   + /root/VisualBot_current
#   deploy_to_jetson.ps1 -Stamped  → jetson:~/VisualBot_20260916_1224    + ~/VisualBot_current
#   deploy_to_pi.ps1 -Stamped      → pi:~/vision_robot_20260916_1224     + ~/vision_robot_current
#   （-Stamp 20260916_1224 可指定时间戳；-NoLink 不建软链；Pi 加 -WithExtras 才覆盖圆环库/回归）
scp "E:\工程文档\27工创赛\整机程序设计\deploy_pi\xxx.py" pi:/home/justin/vision_robot/
scp "E:\工程文档\27工创赛\整机程序设计\deploy_pi\xxx.py" jetson:/home/jetson/VisualBot/
scp "E:\工程文档\27工创赛\整机程序设计\deploy_pi\xxx.py" rdk:/root/VisualBot/

# ===== 树莓派：瞄准/放料 =====
python3 aim_and_place.py --port /dev/ttyUSB0 --stream 8090
python3 aim_and_place.py --port /dev/ttyUSB0 --grab --slot 2
python3 aim_and_place.py --port /dev/ttyUSB0 --mission-all
python3 calibrate_aim.py --port /dev/ttyUSB0
python3 smoke_calib.py ; python3 cam_diag_aim.py --frames 20 ; python3 find_cam.py ; python3 cam_probe.py 0

# ===== 树莓派：浏览器调参台 =====
python3 color_blob_stream.py --cam 0 --port 8090      # 颜色(浏览器 :8090)
python3 ring_stream_ui.py   --cam 0 --port 8091       # 圆环(浏览器 :8091)
python3 ring_stream_ui.py --no-cam --port 8091        # 合成自测
kill $(cat /tmp/ring_stream_ui.pid)                   # 或 bash kill_rsui.sh

# ===== Jetson：同一套工具（--perf auto 自动选 high；UVC 摄像头别加 --gst）=====
cd ~/VisualBot
python3 -u probe_jetson.py --seconds 2               # 平台/OpenCV/相机/推荐档位
bash setup_jetson.sh                                 # 检查 opencv/numpy + 装 pyserial
python3 ring_stream_ui.py   --perf auto --cam 0 --port 8091
python3 color_blob_stream.py --perf auto --cam 0 --port 8090
python3 aim_and_place.py --demo --stream 8092        # 纯视觉（不需要 pyserial）
python3 aim_and_place.py --port /dev/ttyUSB0 --stream 8092
bash bench_perf.sh 12 8097                           # 合成画面：三档性能对比
bash bench_cam.sh 16 0                               # 真实相机：端到端对比

# ===== 两台机器通用的调参小开关 =====
--perf low|mid|high|rdk|auto   # 处理宽/推流宽/JPEG/诊断频率/推流限帧(+rdk 档带算法参数)
--stream-fps 10              # 推流限帧(0=不限; 检测仍每帧跑, 省的是编码)
--stream-width 480           # 推流最长边(编码是大头)
--gst / --sensor-id 0        # 只有 Jetson CSI 排线摄像头需要

# ===== 性能调优三件套（弱算力平台定位瓶颈；三台机器都有）=====
python3 -u prof_ui.py --perf rdk            # 按 UI 真实顺序分段计时
python3 -u exp_speed.py                     # 单算子微基准(含 OpenCL/UMat 对比)
python3 -u exp_config.py --image a4_12.png  # 参数组合扫描(速度 + 精度对比)
bash bench_perf.sh 12                       # 端到端四档对比

# ===== 树莓派：视觉回归 =====
cd ~/vision_robot && python3 -u run_all_tests.py      # 三套回归 + 真实 A4 图 + 二维码
python3 test_rotation.py ; python3 test_dashed.py ; python3 test_stability.py ; python3 verify_rotate.py

# ===== 本机：纯逻辑回归（anaconda python）=====
D:\programs\anaconda\python.exe 整机程序设计\deploy_pi\test_aim_protocol.py
D:\programs\anaconda\python.exe 整机程序设计\car_code上位机\test_task_code_v2.py
D:\programs\anaconda\python.exe 整机程序设计\car_code上位机\test_task_stats.py
D:\programs\anaconda\python.exe 整机程序设计\car_code上位机\test_flow_runner.py
D:\programs\anaconda\python.exe 整机程序设计\car_code上位机\test_cmd_pack.py
D:\programs\anaconda\python.exe 整机程序设计\car_code上位机\test_serial_exec.py
D:\programs\anaconda\python.exe 整机程序设计\car_code上位机\demo_fullflow_local.py
# 或 VSCode：终端 → 运行任务（13 个一键任务）

# ===== 清理残留（树莓派）=====
ps -eo pid,args | grep python3 | grep -v grep      # 看；再 kill <PID>（别 pkill -f）
```

---

## 14. 变更日志（关键节点）

| 日期 | 事件 |
|---|---|
| 09-01 | 视觉上位机：圆环+数字识别、二维码推流、三套回归、真实 A4 验证；`continue.md`/`lecture.md` 成形 |
| 09-02 | TTL 闭环四路集成进固件（USART3/PC10-11），老师车包研读 |
| 09-03 | **瞄准/放料闭环 v4**：分轴 PID、K 标定（0.2593/0.0413）、`aim_offset.txt`、0x13/0x16 动作组 |
| 09-05 | `deploy_pi` 部署包成形；**我接手**：修 smoke/cam_diag 旧 API、旋转坐标一致、0x13/0x16 长等待、下位机静态审计、文档整理 |
| 09-05→09-11 | 全流程 v2 纯逻辑层（任务码/统计/编排/打包/串口）+ 5 套回归；颜色识别浏览器版；圆环调参台原型 |
| 09-09 | **方向表实测校正**（左/右、顺/逆互换）；`ramp.c` 加减速改造讨论；`26090501TEST` 快照 |
| 09-12 | **0x16 拆成物架 1/2/3 号位并适配两端**（含 `--slot`/`--mission-all`）；平滑减速第 2 版（速度线性下降 + RATIO）；圆环 UI 增强（输入框+类型提示、`thr_c` 小数、`close_k` 含偶数、`pre_blur`、**红环=原始中心**、绘制顺序修复）；整合出本 README |
| 09-13 | **接入 Jetson Orin Nano 第二台上位机**：新增 `deploy_to_jetson.ps1`/`probe_jetson.py`/`setup_jetson.sh`/`bench_perf.sh`/`bench_cam.sh`；视觉工具加 **性能档 `--perf` + 推流限帧 `--stream-fps` + GStreamer `--gst`**（一份代码跑 Pi/Jetson）；`aim_and_place.py` 串口改 try-import；实测 ring 34fps / color 27fps（相机上限 14.9fps）；`deploy_*.ps1` 转 UTF-8 BOM（修中文乱码语法错） |
| 09-14 | **接入 RDK X5 第三台上位机**（10.48.81.249，D-Robotics RDK X5 V1.0 / RDK OS 3.5 / 8×A55 / OpenCV 4.11 无 GStreamer）：新增 `deploy_to_rdk.ps1`，探测脚本通用化为 **`probe_platform.py`**（Jetson/RDK/Pi 三合一，旧名 `probe_jetson.py` 转调）、`setup_platform.sh`；**新增 `rdk` 性能档**（640 处理/640 推流，实测 1280 只有 4.3fps → 7.5fps）；`color_blob_stream` 合成场景改缓存（bench 从"测画图"1.5fps 修正为 10.7fps）；UI 在 RDK 上实测可用 |
| 09-14 (续) | **RDK X5 性能调优一轮（7.5 → 15.8 fps，翻倍）**：新增调优三件套 `prof_ui.py`/`exp_speed.py`/`exp_config.py`；定位出 `adaptiveThreshold(GAUSS,41)` 72ms 与 `morphologyEx(9×9)` 22.5ms 是头号杀手；落地 7 项改动 —— `rdk` 档重构（512/512/diag 20）、算法参数随档覆盖（block 25/close 5/proc_scale 0.34）、**数字识别节流 `digit_every`**、**φ1 复用掩膜 `bw_full=`**、去掉每帧多余 rotate、先缩后转、推流改 INTER_LINEAR；三套回归仍 `36/36` + `PASS` + `ALL PASSED` |
| 09-15 | **校赛（魔改规则）开发**：新增 `校赛开发.md`（规则完善 + [待确认] 清单 + 架构 + 六次瞄准 + 协议 + 走读）与 `整机程序设计\校赛\` 全套上位机主程序（状态机 M0~M10、账本、分轴瞄准、扫码、参数下发 API、`--dry-run` 闭环仿真）；**协议扩展 0x20~0x24**（参数下发/动作步骤/读回/存 Flash/执行组）；下位机加 `params.c/h` + `motion.c/h`（**空接口 + 参数表 + 动作组解释器**，Keil 待编译）；本机与 RDK 各跑 **53 项纯逻辑回归全过**，RDK 上 `--dry-run` 整场 3/3 放置成功 |
| 09-16 | **原工程复原 + 上下位机分别打包**：校赛固件扩展从 `26090901TEST\CLEAN\` **移出**到 `整机程序设计\校赛\firmware\`（原 Keil 工程回到 18 个原文件的干净状态）；新增 `_pack_split.py`，按时间戳分别产出 `上位机_<时间>.zip`（85 文件）与 `下位机_<时间>.zip`（116 文件，含 `OBJ\*.hex`） |
| 09-16 (续) | **部署到 RDK X5 + 三个部署脚本升级**：三脚本新增 **`-Stamped`（工程文件夹按时间命名）**、`-Stamp`、`-NoLink`；**部署内容加入校赛主程序**（`$RemoteDir/校赛`）与 `deploy_to_rdk.ps1` 自身；py_compile 覆盖校赛；部署后自动维护 **`<原名>_current` 软链**；VSCode 任务改指向 `_current`。实测：`rdk:/root/VisualBot_20260916_1224`（43 顶层文件 + 校赛 10 个），远端 53 项校赛回归全过 |
| 09-16 (续2) | **创建今日下位机工程 `26091601TEST`（校赛版，开箱可编译）**：`_make_fw_project.py` 一键从现役工程全量复制 + 接入校赛 —— 4 个校赛源文件自动进 `CLEAN\` 并加进 **Keil 的 CLEAN 组**、`car_config.h` 补 0x20~0x24 命令字、`protocol_and_main.c` 补 `SendFrame10()`/5 个 case/`Params_Load()`+`Motion_Init()`，**且 0x22 不再补发统一 ACK**；`motion.c` 的钩子按实际函数名核对（`TTLctrl_Motor1/23/4` + `TTLctrl_WaitReached` + `delay_ms(u16)`，每步**等到位**再走下一步）；OBJ 留空防烧旧 hex。同步修上位机：`lower_api.py` 的 ACK 兼容 `FF 01 FE`、`0x22` 改**10 字节定长读**（值里可能含 0xFE） |
| 09-16 (续3) | **`motion_profile.json` 全文件加注释 + 支持 JSONC**：每个参数/步骤/里程逐行写清"单位·物理来源·现场怎么调"，动作组三种步骤格式与轴号方向也在文件内说明；`school_config.load_profile()` 新增 `_strip_jsonc()`（只剥字符串外的 `//` 与 `/* */`，路径里的 `//` 不受影响），因此**带注释也能直接跑**；53 项回归复测全过。另修掉 Keil 打不开的 BOM 问题（见坑档案 45 条） |
| 09-16 (续4) | **主程序改成「流水账版」**（不再依赖 JSON）：新增 `校赛/school_main.py`（顶部「现场参数区」+ 工序函数 + `run()` 一串函数调用，参数写在括号里）与 `校赛/car.py`（**车与机械臂动作库**：`fwd/back/left/right/spin`、`arm_out/arm_in/lift/base_left/base_right/grip_close/grip_open`、`pick_here/place_here/pick_at/place_at`，机械动作按"脉冲×60/(RPM×3200)"**自动等到位**）；新增 `test_school_main_local.py`（**33 项全过**）；`motion_profile.json` 降级为「状态机版 `school_flow.py` 专用」；`校赛开发.md` 新增 §3.5 两种形态对照 |
| 09-16 (续5) | **修正夹爪/升降的硬件认知 + 双摄监控台**：① 夹爪是**舵机**（`0x14/0x15`，角度制）不是 TTL 轴脉冲 —— `car.py`/`lower_api.py`/`school_main.py`/固件 `params.h·c` 全改，并让固件 `claw.c` 的 `Grip_Close/Open` **优先读参数表**；② 车主确认"升降 = TTL id2+id3 反向传动的龙门架，本车无步进升降轴、无载物台旋转"，把机械结构写进 `car.py`/`school_main.py`/固件 params 注释/`校赛开发.md` **§3.1b**；③ 新增 **双摄监控台 `monitor_ui.py`**（work+scan 两路同屏、各自按 `ring_stream_ui`/`qr_stream_demo` 样式叠加、独立线程与限帧）；④ `校赛开发.md` 新增 **§3.0 硬件与接口确认清单**（执行器 A1~A7、传感器 B1~B5、5 项待确认）与 §7.1 监控台用法；流水账版回归 **38 项**、状态机版 **53 项**全过 |
| 09-16 (续6) | **现场标定体系化 + 相机曝光策略修正**：① 新增 **`现场标定手册.md`**（12 步标定顺序：硬件检查→机械臂/夹爪→相机对焦→圆环→数字模板→颜色→瞄准K/偏移→扫码→里程→联调，每步含命令/判读/改哪个文件/验收标准，附标定记录表与存档回滚）；② 新增 **`校赛/calibrate_route.py`**（里程/自旋标定助手：发一段→量实测→自动算比例→**直接打印 `school_main.py` 的建议参数值**，支持 `--dry-run` 自测）；③ `open_camera()` 曝光策略按 RDK 相机实测修正（OpenCV 锁不住 → **v4l2-ctl 兜底 + 读回校验**；该相机不支持红蓝增益 → **默认不锁 WB**；默认 156 会过曝 → **只在显式 `--exposure N` 时才锁**） |

---

> **给下一个接手者的一句话**：先把 §9 坑表读一遍、按 §11 走一遍现场流程；
> 任何"改了没反应"的情况，第一反应都是**没 Rebuild / 烧了旧 hex / 相机索引变了 / 残留进程占着资源**。
