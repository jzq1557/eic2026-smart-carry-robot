# 重写版说明(car_clean)

## 一、文件地图与新旧对照

| 新文件 | 替代旧代码 | 干什么 |
|---|---|---|
| car_config.h | 散落全工程的魔法数字 | 全车唯一的数字仓库 |
| ramp.h / ramp.c | MSD_Move ×3 + 三个中断 + StepCounter ×3 | 通用加减速引擎,轴当参数 |
| chassis.h / chassis.c | DIR1~4、MOTOR_Direction/Displacement/Angle | 方向查表 + 换算 + 阻塞接口 |
| claw.h / claw.c | contral_claw.c + 爪子相关散函数 | 绝对高度制升降 + 舵机原语 + 编舞 |
| protocol_and_main.c | uart_handle + main.c | 命令分发一表清 + 五幕 |

**继续沿用、零改动的旧模块**:所有 MOTORx_Init、Servo2/3_Init、
uart.c、system_init、ws2812、oled、key、delay —— 硬件配置一根线都没动。

## 二、修掉的 6 个老 bug / 坏味道

1. 三份复制粘贴的 MSD_Move → 一个 Ramp_Start(轴指针传入)
2. 点火时 ARR=100 的"意外小步" → 上电即写真实首步间隔
3. STOP 分支把垃圾 new_step_delay 写回任务单 → 默认保持旧值
4. 起步即超速路径下 last_accel_delay 未初始化 → 补赋值
5. MOTOR_Angle 负角度 ×51 溢出 uint16 → 先取绝对值
6. 0x0C 小端 vs 0x04 大端打架 → 全协议统一大端
   ★ 注意:第 6 条需要树莓派端 0x0C 的打包同步改成高字节在前!

## 三、怎么并入 Keil 工程

1. 把 5 个新文件拷进工程目录(建议新建 CLEAN 文件夹)
2. Keil 左侧 Project 树 → 右键 Add Existing Files,加入三个 .c
3. **移除旧文件的参与编译**:右键 contral.c、motor.c 中的旧
   MSD/中断/uart_handle 部分 —— 最省事的做法:
   - contral.c 整个移出编译(它的功能已全部被新文件覆盖)
   - motor.c 保留(只用它的 Init 函数),但**删掉文件末尾的
     TIM2/TIM3/TIM4_IRQHandler 三个函数**(否则与 ramp.c 重定义冲突)
   - contral_claw.c 移出编译(claw.c 覆盖)
4. Options → C/C++ → Include Paths 加入新文件夹
5. 编译。剩余报错基本都是"某函数在旧头文件声明过" —— 按报错
   把旧 .h 里的对应声明删掉即可。

## 四、上电测试计划(务必架空车轮!)

1. 第一步只测底盘:main 里临时写 Chassis_MoveX_cm(10),看车轮
   转向、圈数(10cm≈0.41圈)是否正确,四轮是否同向
2. 测旋转:Chassis_Rotate_deg(90),量实际角度,误差大就微调
   STEPS_PER_DEGREE
3. 测升降:Lift_GoTo(LIFT_CAMERA) → Lift_GoTo(LIFT_HOME),
   看是否回到原点(绝对高度制的自检)
4. 测舵机:Grip_Open/Close、Wrist_Front/Back
5. 最后串起协议:树莓派逐条发命令(先改好 0x0C 字节序)

## 五、诚实声明

这份代码没有经过编译验证(本环境无 Keil/arm 工具链),
结构与算法忠实等价于原工程 + 上述修复,但**并入时必然会有
少量胶水报错**(旧头文件残留声明、include 顺序等),都是
5 分钟级别的小修。逐条报错解决的过程,恰好是检验这三天
所学的最佳实战 —— 每个报错你现在都有能力读懂。
