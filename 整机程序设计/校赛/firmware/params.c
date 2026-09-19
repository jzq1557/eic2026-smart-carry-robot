/**
  ******************************************************************************
  * @file    params.c
  * @brief   校赛扩展：参数表实现（上位机 0x20 下发 / 0x22 读回 / 0x23 存 Flash）
  *
  *  ⚠️ 现场调参流程：改上位机 `motion_profile.json` → 重跑 school_flow.py
  *     → 上位机会逐个 0x20 下发并 0x22 读回校验 → 校验不过就拒绝发车。
  *     所以这里"参数名/ID/默认值"必须与 `校赛/lower_api.py` 的 P 表严格一致。
  ******************************************************************************
  */
#include "params.h"

typedef struct {
    uint16_t id;
    uint32_t def;          /* 上电默认（无上位机时的兜底） */
    uint32_t val;          /* 当前值（上位机下发后覆盖） */
} ParamSlot;

/* 默认值 = 2026-09 现场手调值；改了记得同步 motion_profile.json */
static ParamSlot s_p[PARAM_COUNT] = {
    { P_CHASSIS_VEL,          8u,    8u    },
    { P_CHASSIS_ACC,          6u,    6u    },
    { P_CHASSIS_DEC,          3u,    3u    },
    { P_TTL_DEFAULT_VEL,      40u,   40u   },
    { P_GRIP_CLOSE_ANGLE,     82u,   82u   },
    { P_GRIP_OPEN_ANGLE,      50u,   50u   },
    { P_BASE_LEFT90_PULSES,   6400u, 6400u },
    { P_BASE_RIGHT90_PULSES,  6400u, 6400u },
    { P_LIFT_UP_PULSES,       4400u, 4400u },   /* 龙门架(升降) */
    { P_LIFT_DOWN_PULSES,     4400u, 4400u },   /* 龙门架(升降) */
    { P_TELESCOPE_OUT_PULSES, 3000u, 3000u },   /* 伸缩 id1 */
    { P_TELESCOPE_IN_PULSES,  3000u, 3000u },   /* 伸缩 id1 */
    { P_AXES_HOME_SPEED,      40u,   40u   },
    { 0u, 0u, 0u }, { 0u, 0u, 0u }, { 0u, 0u, 0u }     /* 预留空位 */
};

void Params_Reset(void)
{
    uint8_t i;
    for (i = 0; i < PARAM_COUNT; i++) {
        s_p[i].val = s_p[i].def;
    }
}

uint8_t Params_Set(uint16_t id, uint32_t v)
{
    uint8_t i;
    for (i = 0; i < PARAM_COUNT; i++) {
        if (s_p[i].id == id && s_p[i].id != 0u) {
            s_p[i].val = v;
            return 1u;
        }
    }
    return 0u;                      /* 未知 ID：上位机会通过 0x22 读回发现自己设错了 */
}

uint8_t Params_Get(uint16_t id, uint32_t *v)
{
    uint8_t i;
    for (i = 0; i < PARAM_COUNT; i++) {
        if (s_p[i].id == id && s_p[i].id != 0u) {
            if (v != 0) { *v = s_p[i].val; }
            return 1u;
        }
    }
    return 0u;
}

uint32_t Params_U32(uint16_t id)
{
    uint32_t v = 0u;
    (void)Params_Get(id, &v);
    return v;
}

/* --------------------------------------------------------------------------
 * Flash 持久化（0x23）
 *   目的：掉电后仍保留上一次下发值，省掉"上电必须连上位机"的依赖。
 *   实现要点（STM32F103ZET6，512K Flash 的最后一页 0x0807F800，页大小 2K）：
 *     1. FLASH_Unlock() → 擦页 → 按半字写入 → FLASH_Lock()
 *     2. 写入前先写魔数 0x5AA5，读取时校验；校验失败就 Params_Reset()
 *   ⚠️ 本工程当前**没有**做这一步（默认不存，避免误擦坏固件区）；
 *      要用就把下面两处 TODO 换成标准库调用。上位机侧 0x23 已经会发，
 *      下位机不实现也只是"不持久化"，不影响比赛。
 * -------------------------------------------------------------------------- */
void Params_Save(void)
{
    /* TODO(可选): 擦最后一页并写入 {魔数, id/val 对...} */
}

void Params_Load(void)
{
    /* TODO(可选): 读最后一页 → 校验魔数 → 逐个 Params_Set(); 失败则 Params_Reset() */
    Params_Reset();
}
