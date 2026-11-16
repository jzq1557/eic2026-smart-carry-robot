# VisualBot3 交接文档

> **2026 上海工创赛 · 智能搬运小车** —— 市赛开发起点
> 这份文档集写给"接手这个工程的人"。目标是：**不靠口头交接，照着读能上手跑、能改、能排障。**

---

## 0. 三十秒速览

| 问题 | 答案 |
|---|---|
| 车是怎么组成的？ | RDK X5（视觉+决策）+ STM32F103ZET6（电机+舵机），10 字节定长帧串口通信 |
| 代码在哪？ | 板上 `/root/VisualBot3/`；本地 `整机程序设计\VisualBot2\`（两者镜像） |
| 怎么连板子？ | `ssh rdk`（**IP 会变、别写死**；现查 `ssh rdk "hostname -I"`，连不上跑 `E:\工程文档\27工创赛\find_rdk.ps1`） |
| 怎么跑整场？ | `python3 -u route_main.py --port /dev/stm32`（默认每段等回车；`--auto` 一口气） |
| 怎么单独调取料？ | `python3 -u grab_demo.py` → 浏览器 `http://<IP>:8092/` 看画面 |
| 怎么单独调放置？ | `python3 -u place_demo.py` |
| 怎么调视觉阈值？ | 圆环台 `ring_stream_ui.py --cam 0 --port 8091`；物料颜色台 `--port 8090`（**两者互斥**） |
| 参数改哪个文件？ | 见 [06 调参手册](06_调参手册与常见故障.md) 的"参数四层地图" |
| 固件怎么烧？ | Keil5 打开 `26091601TEST\USER\26091601TEST.uvprojx` → **Rebuild all** → 确认 hex 时间戳 → 烧录 |
| 车突然不动了？ | 先看 [06 的故障速查表](06_调参手册与常见故障.md) |

---

## 1. 文档索引

| # | 文档 | 内容 | 建议阅读时机 |
|---|---|---|---|
| **01** | [系统总览](01_系统总览.md) | 两台机器分工、执行器清单、相机、坐标与方向约定、目录地图、一次取放的完整链路 | **第一天必读** |
| **02** | [通信协议与下位机](02_通信协议与下位机.md) | 帧格式、命令字全集、ACK 语义、TTL 闭环、固件工程谱系、编译烧录 | 动固件前 |
| **03** | [上位机工程](03_上位机工程.md) | 每个 `.py` 的职责、三种运行形态、**参数注入机制**、部署与同步流程 | **第一天必读** |
| **04** | [视觉与标定](04_视觉与标定.md) | 两种检测模式（★ring vs color）、`BlobTracker` stale 坑、K 值、标定流程 | 调视觉/瞄准时 |
| **05** | [比赛流程与动作组](05_比赛流程与动作组.md) | 七段跑图逐段说明、取料/放置工序、动作组数据结构、里程软跟踪 | 改流程时 |
| **06** | [调参手册与常见故障](06_调参手册与常见故障.md) | **参数四层地图**、TTL 估时公式、速度边界、**故障速查表** | **随时查** |
| **07** | [市赛开发待办](07_市赛开发待办.md) | 校赛已完成清单、遗留问题、下一步建议 | 规划市赛时 |
| — | `26091601TEST\README.md` | 下位机固件的当前说明（工程谱系 / 编译烧录 / 参数表） | 动固件时 |
| — | `..\..\..\命名规范.md` | git 仓库/分支/提交/tag 命名体系 | 提交代码前 |

---

## 2. 当前状态（2026-09-22）

### 2.1 已经跑通的能力（校赛版）

| 能力 | 状态 | 代码位置 |
|---|---|---|
| 出启动区 → 扫码 → 进原料区 | ✅ | `route_main.s1_out` / `s2_scan` / `s3_material` |
| 按任务码在原料区三个工位走位 | ✅ | `s3_material`（`PITCH=12cm`） |
| 原料区取料（瞄准 → 归零 → 夹料 → 放物架） | ✅ 现场验证 | `grab_demo.run_one` |
| 出原料区 → 自旋 → 进加工区 | ✅ | `s4_leave_material` / `s5_spin` / `s6_process` |
| 加工区放置（瞄准标靶 → 取料 → 回放 → 放置 → 回零） | ✅ 逻辑闭环 | `place_demo.run_one` |
| 标靶圆环识别（ring 模式） | ✅ 已修 | `aim.py` + `ring_center_detect_v2.py` |
| 二维码扫码 + 任务码解析 | ✅ | `qr_scan.py` / `school_config.parse_code` |
| 圆环 / 颜色浏览器调参台 | ✅ | `ring_stream_ui.py` / `target_stream_ui.py` / `color_blob_stream.py` |
| 双摄推流监控 | ✅ | `monitor_ui.py`（8092） |
| git 版本管理 | ✅ | 私有仓库 `eic2026-smart-carry-robot` |

### 2.2 已知问题（详见 [07](07_市赛开发待办.md)）

- ⚠️ **固件重烧未确认**：`TTLctrl.c` 三处物架函数参数、`car_config.h` 的 `TTL_PLACE_LOWER_CLK` 是否已烧进板子
- ⚠️ **两边料位不一致**：放物架 H+2500 vs 从物架取料 H+1500（差 1000 脉冲）
- ⚠️ **`close_k` 当前生效 5**，但现场经验认为该是 9
- ⚠️ **`ring_calib.json` 里只有 2 项**（`rotate_ccw` / `rotate_mode`），阈值全是性能档默认值
- ⚠️ **底盘命令 ACK 超时会等满 timeout**（最长 10s），偶发"停住不动"
- ⚠️ **板子 IP 会变**，写死的地址要跟着改

---

## 3. 最短上手路径（Day 1）

```powershell
# ① 连板子，确认环境
ssh rdk "hostname; ls /root/VisualBot3 | wc -l; ls /dev/video*; ls -l /dev/stm32"

# ② 看当前有没有残留进程占着相机/端口
ssh rdk "ps aux | grep -E 'stream_ui|monitor_ui|route_main|grab_demo|place_demo' | grep -v grep"

# ③ 干跑一遍，确认程序本身没问题（不会动车）
ssh rdk "cd /root/VisualBot3 && timeout 120 python3 -u route_main.py --dry-run --start 2"

# ④ 开圆环调参台，确认能看到标靶
ssh rdk "cd /root/VisualBot3 && nohup setsid python3 -u ring_stream_ui.py --cam 0 --port 8091 > /tmp/ring.log 2>&1 &"
#    浏览器打开 http://<IP>:8091/

# ⑤ 架空车轮，跑分步调试器摸清动作
ssh rdk "cd /root/VisualBot3 && python3 -u grab_demo.py"
#    命令：h 归位 / l 作业位 / a 瞄准 / g 取料 / 1-3 放物架 / A-C 一条龙 / k 改瞄准参数 / m 手动 / p 打印
```

**动手前务必确认**：
1. 车轮架空（或场地清空、别站人 —— base 转 180° 时龙门架会扫过一大片空间）
2. 相机是哪个索引（`ls /dev/video*`）
3. 没有别的进程占着 `/dev/video0`（**8090 与 8091 互斥**）

---

## 4. 速查卡

### 路径

| 项 | 值 |
|---|---|
| 板上工程 | `/root/VisualBot3/`（市赛）· `/root/VisualBot2/`（校赛，参照） |
| 本地镜像 | `E:\工程文档\27工创赛\整机程序设计\VisualBot2\` |
| 共享库源码 | `E:\工程文档\27工创赛\整机程序设计\校赛\` |
| 最新固件 | `E:\工程文档\27工创赛\整机程序设计\26091601TEST\` |
| 串口设备 | `/dev/stm32`（上位机侧别名） |
| 日志 | `/tmp/ring.log`、`/tmp/route.log` 等（启动时自己指定） |

### 端口

| 端口 | 服务 | 说明 |
|---|---|---|
| **8090** | **物料颜色**调参台（`color_blob_stream.py`） | 抢 `/dev/video0` |
| **8091** | **圆环**调参台（`ring_stream_ui.py`）**或** **彩色标靶**台（`target_stream_ui.py`） | ⚠️ **两者默认端口都是 8091，互斥**；也都抢 `/dev/video0` |
| **8092** | 双摄推流（`monitor_ui.py`） | work 路可复用主程序共享帧（不抢相机） |

### 常用命令

```bash
# 跑图（默认每段等回车）
python3 -u route_main.py --port /dev/stm32

# 干跑（不动硬件）
python3 -u route_main.py --dry-run --start 1

# 调试器
python3 -u grab_demo.py            # 原料区取料
python3 -u place_demo.py           # 加工区放置

# 调参台
python3 -u ring_stream_ui.py --cam 0 --port 8091
python3 -u ring_stream_ui.py --no-cam --port 8091      # 无相机，合成靶标
python3 -u target_stream_ui.py --cam 0 --port 8090

# 停掉所有调试服务
pkill -f 'stream_ui|monitor_ui'
```

### git 工作流

```powershell
# 从板子拉回现场改动 → 看清改了什么 → 提交推送
cd E:\工程文档\27工创赛\整机程序设计\deploy_pi
powershell -ExecutionPolicy Bypass -File pull_from_rdk.ps1 -RemoteDir /root/VisualBot3 -LocalSchool ..\VisualBot2 -Apply

cd E:\工程文档\27工创赛
git diff                                  # ★看清板子上改了哪一行
git add -A
git commit -m "tune(route): TO_PROCESS 82→85"
git push
```

> ⚠️ **铁律**：部署前**必须先 pull 对比**，绝不覆盖车主在板上现场调过的参数。

---

## 5. 关于这份文档集

| 项 | 说明 |
|---|---|
| **生成时间** | 2026-09-22（校赛结束后、市赛开工前） |
| **规模** | 8 篇，约 3300 行 |
| **取材原则** | 每条结论都追溯到**具体源文件**；查不到或不确定的一律标 **⚠️ 待确认**，不编造 |
| **覆盖范围** | 上位机 + 下位机 + 协议 + 视觉标定 + 比赛流程 + 调参排障 + 遗留问题 |

**维护约定**（重要，否则文档很快会烂）：

1. **改了代码/参数，顺手改对应文档** —— 过时的文档比没有文档更危险
2. **文档与代码矛盾时，一律以代码为准**，并把矛盾记进 [07 的 §2.10](07_市赛开发待办.md)（那里已经记了 10 处）
3. **新发现的坑 / 待办，记进 [07](07_市赛开发待办.md)** —— 它是这份文档集的"活页"
4. **每篇开头都有"一句话概括"**，急着找东西时先扫这一段

**已知的信息时效性风险**：

- 板上**目录名、IP、相机设备节点**都可能变（见 [01 §2.2](01_系统总览.md)、[01 的路径对照](#路径对照最容易搞混的一点)）
- 下位机固件**重烧后**，`02` 里关于"hex 时间戳 / 程序大小"的记录需要更新
- 标定档案（`ring_calib.json` / `color_profile.json`）是**现场不断变化的**，文档只讲方法不抄数值
