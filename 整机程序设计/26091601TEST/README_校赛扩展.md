# 校赛扩展接入说明（下位机侧）

> 本目录（整机程序设计\校赛\firmware\）是**校赛固件扩展**，刻意**不放进原 Keil 工程**（保持原工程干净、随时可编译）。共 4 个源文件，把下位机改造成**"空函数接口 + 参数表 + 动作组解释器"**：
>
> | 文件 | 作用 |
> |---|---|
> | `params.h` / `params.c` | 参数表：速度/脉冲/加速度全部可由上位机 0x20 下发、0x22 读回 |
> | `motion.h` / `motion.c` | 动作组解释器：步骤表由上位机 0x21 下发，0x24 触发执行 |
>
> **不想手动加文件？** 直接跑 `D:\programs\anaconda\python.exe E:\工程文档\27工创赛\_make_fw_project.py` ——
> 它按今天日期建工程(如 `26091601TEST`)，**自动**把这 4 个文件放进 CLEAN、加进 Keil 的 CLEAN 组、
> 并把 0x20~0x24 的 case 与命令字都补好，打开就能 Rebuild。

> 设计依据：根目录 `校赛开发.md` §3.2~§3.5、§5.3；上位机对应实现 `整机程序设计\校赛\lower_api.py`。
> ⚠️ 本机**没有 Keil/ARM 工具链**，这两个 .c **尚未编译验证**，请按下面步骤在 Keil 里 Rebuild。

---

## 一、Keil 里怎么加（3 步）

1. 打开 `USER\26090901TEST.uvprojx` → 左侧工程树右键 **CLEAN 组** → `Add Existing Files to Group 'CLEAN'`
   → 选 本目录（校赛\firmware\）下的 `params.c`、`motion.c`（`.h` 不用加，Keil 自动按 include 路径找）；
2. `Options for Target → C/C++ → Include Paths` 里确认包含 `..\CLEAN`（一般已经在）；
3. **Rebuild**（不是 Build）→ 看 0 Error → 确认 `OBJ\26090901TEST.hex` 时间戳刷新。

## 二、`protocol_and_main.c` 里要加的代码（复制即可）

### 2.1 头部 include + 初始化

```c
#include "params.h"
#include "motion.h"
/* ... 现有 include ... */

/* 初始化顺序：先参数表，再动作组解释器（main() 开头，其他外设初始化之前/之后都行） */
Params_Load();          /* 先尝试 Flash 读回；没有就 Params_Reset() */
Motion_Init();
```

### 2.2 在 `switch ((CarCommand)buf[0])` 里追加 5 个 case

```c
    /* ===== 校赛扩展：上位机下发参数 / 动作组 ===== */
    case CMD_SET_PARAM: {                       /* 0x20: buf[1..2]=id  buf[3..6]=value 大端 */
        uint16_t pid = be16(buf[1], buf[2]);
        uint32_t val = ((uint32_t)buf[3] << 24) | ((uint32_t)buf[4] << 16)
                     | ((uint32_t)buf[5] << 8)  |  (uint32_t)buf[6];
        uint8_t ok = Params_Set(pid, val);
        Ack_Send(CMD_SET_PARAM, ok ? 1u : 0u);
        break;
    }
    case CMD_SET_MOTION_STEP: {                 /* 0x21: buf[1]=子类型 buf[2]=组 buf[3]=步骤号 */
        uint8_t sub = buf[1];
        uint8_t gid = buf[2];
        uint8_t idx = buf[3];
        uint8_t ok  = 1u;
        switch (sub) {
        case 0u:  Motion_Clear(gid); break;                                  /* 清空 */
        case 1u:  ok = Motion_SetHead(gid, idx, buf[4], buf[5], buf[6], buf[7]); break;
        case 2u:  ok = Motion_SetPulses(gid, idx,
                        ((uint32_t)buf[4] << 24) | ((uint32_t)buf[5] << 16)
                      | ((uint32_t)buf[6] << 8)  |  (uint32_t)buf[7]); break;
        case 3u:  ok = Motion_Commit(gid, idx); break;                       /* idx 当步骤数用 */
        default:  ok = 0u; break;
        }
        Ack_Send(CMD_SET_MOTION_STEP, ok);
        break;
    }
    case CMD_GET_PARAM: {                       /* 0x22: buf[1..2]=id；回帧带 value */
        uint16_t pid = be16(buf[1], buf[2]);
        uint32_t val = 0u;
        uint8_t  ok  = Params_Get(pid, &val);
        /* 回：FF 22 idH idL v3 v2 v1 v0 FE   —— 上位机 query() 就按这个布局取值 */
        Uart1_SendFrame(CMD_GET_PARAM, pid >> 8, pid & 0xFF,
                        (val >> 24) & 0xFF, (val >> 16) & 0xFF,
                        (val >> 8) & 0xFF, val & 0xFF, ok);
        break;
    }
    case CMD_SAVE_PARAMS:                       /* 0x23 */
        Params_Save();
        Ack_Send(CMD_SAVE_PARAMS, 1u);
        break;
    case CMD_MOTION_RUN: {                      /* 0x24: buf[1]=组 buf[2]=slot */
        uint8_t ok = Motion_Run(buf[1], buf[2]);   /* 阻塞到整组完成 */
        Ack_Send(CMD_MOTION_RUN, ok);              /* ← 上位机等 ACK 超时 25s */
        break;
    }
```

> ⚠️ 三个细节：
> 1. `Ack_Send` / `Uart1_SendFrame` 用你工程里现有的发送函数（`protocol_and_main.c` 里已有回执逻辑，
>    照它现在的写法接即可）；如果现有回执函数签名不同，**只改这两处调用**。
> 2. 0x22/0x24 是**要回数据/要等动作**的命令，别走"收到就立刻回 OK"的通用回执。
> 3. `CMD_SET_PARAM` 等 5 个宏加进 `car_config.h` 的 `CarCommand` 枚举：
>    ```c
>    CMD_SET_PARAM       = 0x20,
>    CMD_SET_MOTION_STEP = 0x21,
>    CMD_GET_PARAM       = 0x22,
>    CMD_SAVE_PARAMS     = 0x23,
>    CMD_MOTION_RUN      = 0x24,
>    ```

## 三、帧布局对照表（两边必须一致，**已经踩过一次错位**）

上位机 `校赛/lower_api.py` ↔ 下位机 `protocol_and_main.c`

| 命令 | buf[1] | buf[2] | buf[3] | buf[4] | buf[5] | buf[6] | buf[7] |
|---|---|---|---|---|---|---|---|
| 0x20 SET_PARAM | idH | idL | v3 | v2 | v1 | v0 | 0 |
| 0x21 子类型0 清空 | 0 | gid | 0 | 0 | 0 | 0 | 0 |
| 0x21 子类型1 步头 | 1 | gid | **idx** | kind | axis | dir | vel |
| 0x21 子类型2 脉冲 | 2 | gid | **idx** | p3 | p2 | p1 | p0 |
| 0x21 子类型3 提交 | 3 | gid | **步骤数** | 0 | 0 | 0 | 0 |
| 0x22 GET_PARAM | idH | idL | 0 | 0 | 0 | 0 | 0 |
| 0x23 SAVE | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 0x24 MOTION_RUN | gid | slot | 0 | 0 | 0 | 0 | 0 |

**注意 buf[] 是"命令后的 7 字节"**（本工程现有代码里 `buf[1]` 才对应第一个参数字节，
`buf[0]` 是命令字本身）—— 上面的表已经按这个习惯写好了，**照抄即可，别自己挪位**。

## 四、编译后自检（不用上车）

1. 上位机接 USB-TTL，跑：
   ```bash
   python3 -u 校赛/school_flow.py --port /dev/ttyUSB0 --dry-run   # 只验证上位机逻辑
   python3 -u 校赛/school_flow.py --port /dev/ttyUSB0             # 真跑（先架空车轮！）
   ```
2. 只看"参数同步"这一段：
   ```python
   import lower_api as LA, school_config as SC
   l = LA.SerialLower("/dev/ttyUSB0")
   print(l.sync_profile(SC.load_profile()))     # 期望 (True, [])
   ```
   若返回 `(False, ['verify:chassis_vel(8!=6)'])` 这类 → 说明**读回值 ≠ 下发值**：
   多半是 0x22 回帧布局或参数 ID 对不上（查上面两张表）。
3. 架空车轮，单独试一个组：
   ```python
   l.run_group("HOME")            # 期望机械臂真的回 Home 并回 ACK
   ```
