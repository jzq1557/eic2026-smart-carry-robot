# 26091601TEST —— 校赛版下位机工程（2026-09-16 创建）

> 由 `26090901TEST` 全量复制 + 校赛扩展接入而来，**开箱可编译**（`USER\26091601TEST.uvprojx`）。

## 一、这个工程比 26090901TEST 多了什么

| 位置 | 变化 |
|---|---|
| `CLEAN\params.c/h` | **新增**：参数表（速度/脉冲/加速度），上位机 `0x20` 下发、`0x22` 读回 |
| `CLEAN\motion.c/h` | **新增**：动作组解释器，步骤表由上位机 `0x21` 下发、`0x24` 触发执行 |
| `CLEAN\car_config.h` | 命令字加 `0x20~0x24` |
| `CLEAN\protocol_and_main.c` | 加 include、`SendFrame10()`、5 个 case、`Params_Load()`/`Motion_Init()`；**0x22 不再补发统一 ACK** |
| Keil 工程 | `TargetName`/`OutputName`/工程文件名 = `26091601TEST`；**CLEAN 组已加入 params.c、motion.c** |
| `OBJ\` | **刻意留空**（不带旧 hex，避免"烧旧 hex 白查几小时"的老坑） |

## 二、编译与烧录

1. Keil5 打开 `USER\26091601TEST.uvprojx`（**不用再加文件，已经加好了**）；
2. **Project → Rebuild all target files**（不是 Build）→ 看 **0 Error**；
3. 确认 `OBJ\26091601TEST.hex` 时间戳 = 刚刚（这一步别省）；
4. 烧录 → **架空车轮**先试 `l.run_group("HOME")`（上位机 `校赛/lower_api.py`）；
5. 再接视觉，跑 `python3 -u 校赛/school_flow.py --port /dev/ttyUSB0`。

## 三、上位机侧配套（必须先更新）

`整机程序设计\校赛\lower_api.py` 已适配本固件：
- ACK 判定**同时接受** `FF 01 FE`（旧固件统一回执）与 `FF <cmd> ... FE`；
- `0x22` 读参数按 **10 字节定长**读（值里可能含 `0xFE`，只靠包尾会误截）。

`sync_profile()` 会在发车前下发全部参数与 9 个动作组，并对关键参数读回校验；
校验不一致会**拒绝发车** —— 那时先查参数 ID 与帧布局，别硬跑。

## 五、本车机械结构（别按"老师那套"想当然）

| 部位 | 实现 | 协议 | 单位 |
|---|---|---|---|
| 伸缩滑台 | TTL 闭环 id1 | 0x10 | 脉冲/RPM |
| **龙门架升降** | **TTL 闭环 id2 + id3 两台反向传动** | 0x11（自动 id2=dir、id3=反向） | 脉冲/RPM |
| base 轴 | TTL 闭环 id4 | 0x12 | 脉冲/RPM |
| 夹爪 / 腕关节 | **舵机** Servo2 / Servo3（50张·82夹 / 240前·60后） | 0x14 开闭、0x15 角度 | **角度 0~255°** |
| 底盘 4 轮 | 步进 + ramp | 0x01/0x02/0x03 | cm / 度 |

⚠️ 本车**没有**独立步进升降轴、**没有**载物台旋转电机（载物台固定）→
`claw.c` 的 `Lift_GoTo / Tray_Turn120 / Claw_*` 与协议 `0x0B/0x0C/0x07/0x09` 是**老师车遗留死代码，别调用**。
夹爪一律走舵机（`Grip_Close/Open` 现在会优先读参数表 `P_GRIP_*_ANGLE`）。

## 四、参数与动作组的"唯一真源"

- 上位机 `整机程序设计\校赛\motion_profile.json`（现场只改这个，改完重跑主程序即自动下发）；
- 本工程 `params.c` 里的默认值只是"没有上位机时也能动"的兜底，**两边 ID 必须一致**。
