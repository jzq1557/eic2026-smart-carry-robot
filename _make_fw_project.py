# -*- coding: utf-8 -*-
"""
_make_fw_project.py —— 按"今天日期"创建下位机工程（全量 + 校赛扩展，开箱可编译）
================================================================================
命名沿用工程惯例 `YYMMDD01TEST`（例：2026-09-16 → `26091601TEST`）。

做四件事：
  1. 从现役工程 `26090901TEST` 全量复制（**排除 OBJ 编译产物** —— 免得误烧旧 hex）；
  2. 把校赛扩展 `params.c/h`、`motion.c/h` 放进 `CLEAN\\`（IncludePath 已含 ..\\CLEAN）；
  3. 改 Keil 工程：TargetName/OutputName/工程文件名 → 新名字，并把 params.c / motion.c
     加进 **CLEAN 组**；
  4. 改源码接入校赛：`car_config.h` 加 0x20~0x24 命令字；`protocol_and_main.c` 加
     include / SendFrame10 / 5 个 case / Params_Load()+Motion_Init()。

用法：
    D:\\programs\\anaconda\\python.exe _make_fw_project.py            # 用今天日期
    D:\\programs\\anaconda\\python.exe _make_fw_project.py 26091701TEST  # 指定工程名
"""
import os
import re
import shutil
import sys
import time

ROOT = r"E:\工程文档\27工创赛"
PROG = os.path.join(ROOT, "整机程序设计")
SRC = os.path.join(PROG, "26090901TEST")
OLD_NAME = "26090901TEST"
EXT_DIR = os.path.join(PROG, "校赛", "firmware")

# 复制时排除：编译产物与 Keil 用户状态（OBJ 里若有旧 hex 会让人误烧）
SKIP_DIRS = {"Listings", "DebugConfig", "RTE", "Objects", "__pycache__"}
SKIP_EXT = {".o", ".d", ".crf", ".dep", ".lnp", ".iex", ".sct", ".htm", ".map",
            ".lst", ".bak", ".axf", ".build_log.htm", ".scvd", ".uvoptx", ".uvguix",
            ".hex", ".pyc"}


def read_text(path):
    """工程里 UTF-8 / GBK 混用：先按 UTF-8 读, 失败退回 GBK。

    ★千万不要用 `utf-8-sig`：它在"没有 BOM 的文件"上也能解码成功, 于是写回时
      会凭空加上 UTF-8 BOM —— Keil µVision 打不开带 BOM 的 .uvprojx, 报
      "cannot read project file"（2026-09-16 真踩过, 三个文件被加了 BOM）。
      用 `utf-8` 读反而安全：原文件有 BOM 时会作为 U+FEFF 保留在字符串首,
      写回时自动还原, 有无 BOM 都不会被改。"""
    b = open(path, "rb").read()
    for enc in ("utf-8", "gbk"):
        try:
            return b.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return b.decode("latin-1"), "latin-1"


def write_text(path, text, enc="utf-8"):
    with open(path, "wb") as f:
        f.write(text.encode(enc, errors="replace"))


def copy_project(dst):
    n = 0
    for dirpath, dirnames, filenames in os.walk(SRC):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        rel = os.path.relpath(dirpath, SRC)
        out_dir = dst if rel == "." else os.path.join(dst, rel)
        os.makedirs(out_dir, exist_ok=True)
        for fn in filenames:
            if os.path.splitext(fn)[1].lower() in SKIP_EXT:
                continue
            if fn.startswith("~") or fn.endswith(".uvopt"):
                continue
            shutil.copy2(os.path.join(dirpath, fn), os.path.join(out_dir, fn))
            n += 1
    os.makedirs(os.path.join(dst, "OBJ"), exist_ok=True)   # 留空目录, Keil 会重建
    return n


def patch_uvprojx(dst, new_name):
    """工程文件改名 + 全局替换工程名 + 在 CLEAN 组里插入两个 .c。"""
    src_uv = os.path.join(dst, "USER", "%s.uvprojx" % OLD_NAME)
    dst_uv = os.path.join(dst, "USER", "%s.uvprojx" % new_name)
    txt, enc = read_text(src_uv)
    txt = txt.replace(OLD_NAME, new_name)
    nl = "\r\n" if "\r\n" in txt else "\n"
    block = nl.join([
        "            <File>",
        "              <FileName>params.c</FileName>",
        "              <FileType>1</FileType>",
        "              <FilePath>..\\CLEAN\\params.c</FilePath>",
        "            </File>",
        "            <File>",
        "              <FileName>motion.c</FileName>",
        "              <FileType>1</FileType>",
        "              <FilePath>..\\CLEAN\\motion.c</FilePath>",
        "            </File>",
    ]) + nl
    pat = re.compile(r"<FilePath>\.\.\\CLEAN\\direction_check\.c</FilePath>\s*</File>")
    m = pat.search(txt)
    if not m:
        raise RuntimeError("没找到 CLEAN 组的锚点(direction_check.c), 工程结构变了?")
    txt = txt[:m.end()] + nl + block.rstrip(nl) + txt[m.end():]
    write_text(dst_uv, txt, enc if enc != "latin-1" else "utf-8")
    os.remove(src_uv)
    return dst_uv


def patch_car_config(dst):
    p = os.path.join(dst, "CLEAN", "car_config.h")
    txt, enc = read_text(p)
    if "CMD_SET_PARAM" in txt:
        return False
    nl = "\r\n" if "\r\n" in txt else "\n"
    block = nl.join([
        "    /* ===== 校赛扩展(2026-09-16): 参数下发 + 动作组步骤, 上位机主导 =====",
        "     * 见工程根 README_校赛工程.md; 上位机侧实现在 整机程序设计\\校赛\\lower_api.py",
        "     *   0x22 的回帧是 10 字节: FF 22 idH idL v3 v2 v1 v0 FE(不走统一 ACK) */",
        "    CMD_SET_PARAM       = 0x20,  /* buf[1..2]=参数ID 大端  buf[3..6]=值 32位大端 */",
        "    CMD_SET_MOTION_STEP = 0x21,  /* 动作组步骤: buf[1]=子类型(0清空/1步头/2补脉冲/3提交) */",
        "    CMD_GET_PARAM       = 0x22,  /* 读参数: buf[1..2]=ID(回值帧, 见上)               */",
        "    CMD_SAVE_PARAMS     = 0x23,  /* 参数存 Flash(可选实现, 未实现也不影响比赛)      */",
        "    CMD_MOTION_RUN      = 0x24,  /* 执行动作组: buf[1]=组号 buf[2]=slot(兼容物架位)  */",
    ]) + nl
    anchor = "} CarCommand;"
    if anchor not in txt:
        raise RuntimeError("car_config.h 里没找到 '} CarCommand;'")
    txt = txt.replace(anchor, block + anchor, 1)
    write_text(p, txt, enc)
    return True


def patch_protocol(dst):
    p = os.path.join(dst, "CLEAN", "protocol_and_main.c")
    txt, enc = read_text(p)
    if "CMD_SET_PARAM" in txt:
        return False
    nl = "\r\n" if "\r\n" in txt else "\n"

    # 1) include
    txt = txt.replace('#include "TTLctrl.h"',
                      '#include "TTLctrl.h"' + nl +
                      '#include "params.h"          /* 校赛扩展: 参数表(上位机 0x20 下发) */' + nl +
                      '#include "motion.h"          /* 校赛扩展: 动作组解释器(0x21/0x24)  */', 1)

    # 2) SendFrame10
    helper = nl.join([
        "/* 校赛扩展: 发一帧 0xFF + cmd + 7字节 + 0xFE(与上位机 lower_api 的帧格式一致)。",
        " * 现有 UART_SendPacket2UP 只能发 3 字节 ACK, 而 0x22 读参数要回 10 字节值帧。 */",
        "static void SendFrame10(uint8_t cmd, const uint8_t *p7)",
        "{",
        "    uint8_t i;",
        "    USART1_SendBits(0xFF);",
        "    USART1_SendBits(cmd);",
        "    for (i = 0; i < 7; i++) { USART1_SendBits(p7[i]); }",
        "    USART1_SendBits(0xFE);",
        "}",
        "",
    ]) + nl
    anchor1 = "void Protocol_Handle(const uint8_t *buf)"
    txt = txt.replace(anchor1, helper + anchor1, 1)

    # 3) 五个 case（插在 0x16 之后、default 之前）
    cases = nl.join([
        "",
        "    /* ===== 校赛扩展 0x20~0x24(上位机开赛前下发参数与动作组) ===== */",
        "    case CMD_SET_PARAM: {                    /* buf[1..2]=id  buf[3..6]=value 大端 */",
        "        uint16_t pid = be16(buf[1], buf[2]);",
        "        uint32_t val = ((uint32_t)buf[3] << 24) | ((uint32_t)buf[4] << 16)",
        "                     | ((uint32_t)buf[5] << 8)  |  (uint32_t)buf[6];",
        "        (void)Params_Set(pid, val);          /* 未知 id 由上位机 0x22 读回发现 */",
        "        break;",
        "    }",
        "    case CMD_SET_MOTION_STEP: {              /* buf[1]=子类型 buf[2]=组 buf[3]=步骤号 */",
        "        uint8_t sub = buf[1], gid = buf[2], idx = buf[3];",
        "        switch (sub) {",
        "        case 0: Motion_Clear(gid); break;",
        "        case 1: Motion_SetHead(gid, idx, buf[4], buf[5], buf[6], buf[7]); break;",
        "        case 2: Motion_SetPulses(gid, idx, ((uint32_t)buf[4] << 24) | ((uint32_t)buf[5] << 16)",
        "                                            | ((uint32_t)buf[6] << 8) | (uint32_t)buf[7]); break;",
        "        case 3: Motion_Commit(gid, idx); break;",
        "        default: break;",
        "        }",
        "        break;",
        "    }",
        "    case CMD_GET_PARAM: {                    /* 读参数: 回 10 字节值帧(不走统一 ACK) */",
        "        uint8_t  p7[7];",
        "        uint32_t val = 0;",
        "        (void)Params_Get(be16(buf[1], buf[2]), &val);",
        "        p7[0] = buf[1]; p7[1] = buf[2];",
        "        p7[2] = (uint8_t)(val >> 24); p7[3] = (uint8_t)(val >> 16);",
        "        p7[4] = (uint8_t)(val >> 8);  p7[5] = (uint8_t)val;",
        "        p7[6] = 0;",
        "        SendFrame10(CMD_GET_PARAM, p7);",
        "        break;",
        "    }",
        "    case CMD_SAVE_PARAMS:                    /* 参数存 Flash(可选) */",
        "        Params_Save();",
        "        break;",
        "    case CMD_MOTION_RUN:                     /* 执行动作组: 阻塞到整组完成 */",
        "        (void)Motion_Run(buf[1], buf[2]);",
        "        break;",
        "",
    ]) + nl
    anchor2 = "    default: break;"
    txt = txt.replace(anchor2, cases + anchor2, 1)

    # 4) main 里初始化
    txt = txt.replace("    Claw_Init();",
                      "    Claw_Init();" + nl +
                      "    Params_Load();          /* 校赛扩展: 参数表(试 Flash, 失败用默认值) */" + nl +
                      "    Motion_Init();          /* 校赛扩展: 动作组解释器 */", 1)

    # 5) 0x22 已自行回值帧, 不再补发统一 ACK（否则上位机把残留 ACK 当成下一条命令的回执）
    old_loop = nl.join([
        "        if (Serial1_GetRxFlag())",
        "        {",
        "            Protocol_Handle(Serial_RXPacket);",
        "            UART_SendPacket2UP(PKT_ACK_DONE);",
        "        }",
    ])
    new_loop = nl.join([
        "        if (Serial1_GetRxFlag())",
        "        {",
        "            Protocol_Handle(Serial_RXPacket);",
        "            /* 校赛扩展: 0x22 在读参数时已回过 10 字节值帧, 这里不再补 ACK",
        "             * (否则残留的 FF 01 FE 会被上位机当成下一条命令的回执) */",
        "            if (Serial_RXPacket[0] != CMD_GET_PARAM) {",
        "                UART_SendPacket2UP(PKT_ACK_DONE);",
        "            }",
        "        }",
    ])
    if old_loop in txt:
        txt = txt.replace(old_loop, new_loop, 1)
    else:
        print("   [提醒] main 循环没匹配到, 请手动确认 0x22 不再补发 ACK")
    write_text(p, txt, enc)
    return True


FW_NOTE = """# {proj} —— 校赛版下位机工程（{date} 创建）

> 由 `26090901TEST` 全量复制 + 校赛扩展接入而来，**开箱可编译**（`USER\\{proj}.uvprojx`）。

## 一、这个工程比 26090901TEST 多了什么

| 位置 | 变化 |
|---|---|
| `CLEAN\\params.c/h` | **新增**：参数表（速度/脉冲/加速度），上位机 `0x20` 下发、`0x22` 读回 |
| `CLEAN\\motion.c/h` | **新增**：动作组解释器，步骤表由上位机 `0x21` 下发、`0x24` 触发执行 |
| `CLEAN\\car_config.h` | 命令字加 `0x20~0x24` |
| `CLEAN\\protocol_and_main.c` | 加 include、`SendFrame10()`、5 个 case、`Params_Load()`/`Motion_Init()`；**0x22 不再补发统一 ACK** |
| Keil 工程 | `TargetName`/`OutputName`/工程文件名 = `{proj}`；**CLEAN 组已加入 params.c、motion.c** |
| `OBJ\\` | **刻意留空**（不带旧 hex，避免"烧旧 hex 白查几小时"的老坑） |

## 二、编译与烧录

1. Keil5 打开 `USER\\{proj}.uvprojx`（**不用再加文件，已经加好了**）；
2. **Project → Rebuild all target files**（不是 Build）→ 看 **0 Error**；
3. 确认 `OBJ\\{proj}.hex` 时间戳 = 刚刚（这一步别省）；
4. 烧录 → **架空车轮**先试 `l.run_group("HOME")`（上位机 `校赛/lower_api.py`）；
5. 再接视觉，跑 `python3 -u 校赛/school_flow.py --port /dev/ttyUSB0`。

## 三、上位机侧配套（必须先更新）

`整机程序设计\\校赛\\lower_api.py` 已适配本固件：
- ACK 判定**同时接受** `FF 01 FE`（旧固件统一回执）与 `FF <cmd> ... FE`；
- `0x22` 读参数按 **10 字节定长**读（值里可能含 `0xFE`，只靠包尾会误截）。

`sync_profile()` 会在发车前下发全部参数与 9 个动作组，并对关键参数读回校验；
校验不一致会**拒绝发车** —— 那时先查参数 ID 与帧布局，别硬跑。

## 四、参数与动作组的"唯一真源"

- 上位机 `整机程序设计\\校赛\\motion_profile.json`（现场只改这个，改完重跑主程序即自动下发）；
- 本工程 `params.c` 里的默认值只是"没有上位机时也能动"的兜底，**两边 ID 必须一致**。
"""


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else time.strftime("%y%m%d") + "01TEST"
    dst = os.path.join(PROG, name)
    if os.path.exists(dst):
        print("!! 目标已存在: %s（换个名字或先删掉）" % dst)
        return 2
    print("创建 %s  ←  %s" % (dst, SRC))
    n = copy_project(dst)

    # 校赛扩展 4 个源文件 → CLEAN（firmware 是权威副本）
    for f in ("params.h", "params.c", "motion.h", "motion.c"):
        src = os.path.join(EXT_DIR, f)
        if not os.path.exists(src):
            src = os.path.join(SRC, "CLEAN", f)
        shutil.copy2(src, os.path.join(dst, "CLEAN", f))
        n += 1

    uv = patch_uvprojx(dst, name)
    c1 = patch_car_config(dst)
    c2 = patch_protocol(dst)
    write_text(os.path.join(dst, "README_校赛工程.md"),
               FW_NOTE.format(proj=name, date=time.strftime("%Y-%m-%d")))
    n += 1

    print("复制 %d 个文件" % n)
    print("  Keil 工程: %s" % uv)
    print("  car_config.h 加命令字: %s" % ("已加" if c1 else "已存在,跳过"))
    print("  protocol_and_main.c 接入: %s" % ("已改" if c2 else "已存在,跳过"))
    print("  CLEAN 里现在有: %s" % ", ".join(sorted(
        f for f in os.listdir(os.path.join(dst, "CLEAN")) if f.endswith(".c"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
